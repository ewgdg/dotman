"""Persistent initial Command Deck over public immutable SyncSession views."""

from __future__ import annotations

import asyncio
from contextvars import copy_context
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
from difflib import unified_diff
import signal
import time

from rich.spinner import Spinner

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult, SuspendNotSupported
from textual.binding import Binding
from textual.errors import NoWidget
from textual.widgets import DataTable, OptionList, RichLog, Static

from dotman.diff_review import display_review_path
from dotman.ui_context import current_ui_config
from dotman.cli_style import render_key_hints, render_payload_section_label, render_sync_term, render_package_label
from dotman.sync_base_store import DirectoryChildPresent, FilePresent, Missing
from dotman.sync_deck_command import selection_uses_inclusion, auxiliary_resolution, additional_label, guard_skip_explanation, set_all_selected, set_selected, row_diagnostics, auxiliary_label, review, edit_proposal, set_resolution_intent, retry_materialization, effect_summary, primary_change_summary, resolution_label, summary_stats
from dotman.sync_session import AuthorizeSymlinkReplacement, AdditionalRow, AuxiliaryRow, CommandRejected, SyncSession


def _frozen_difference(
    before: FilePresent | DirectoryChildPresent | Missing | None,
    after: FilePresent | DirectoryChildPresent | Missing | None,
    *,
    before_label: str,
    after_label: str,
    description: str,
) -> list[str]:
    if before is None or after is None:
        return ["    Comparison evidence unavailable"]
    present_types = (FilePresent, DirectoryChildPresent)
    before_bytes = before.content if isinstance(before, present_types) else b""
    after_bytes = after.content if isinstance(after, present_types) else b""
    mode_changed = (
        isinstance(before, DirectoryChildPresent)
        and isinstance(after, DirectoryChildPresent)
        and before.executable != after.executable
    )
    if before_bytes == after_bytes and not mode_changed:
        return ["    No content difference"]

    lines: list[str] = []
    if mode_changed:
        # Directory children carry Git's executable bit, so use the same mode
        # evidence as Git diffs instead of presenting host-specific permissions.
        lines.extend([
            f"old mode {'100755' if before.executable else '100644'}",
            f"new mode {'100755' if after.executable else '100644'}",
        ])
    if before_bytes != after_bytes:
        try:
            diff = unified_diff(
                before_bytes.decode("utf-8").splitlines(keepends=True),
                after_bytes.decode("utf-8").splitlines(keepends=True),
                fromfile="/dev/null" if isinstance(before, Missing) else before_label,
                tofile="/dev/null" if isinstance(after, Missing) else after_label,
                lineterm="\n",
            )
            for line in diff:
                lines.append(line.rstrip("\n"))
                # Retain newline-only drift in both evidence and effect previews.
                if not line.endswith("\n"):
                    lines.append("\\ No newline at end of file")
        except UnicodeDecodeError:
            lines.append(f"  Binary {description}: {len(before_bytes)} → {len(after_bytes)} bytes")
    return lines


class CommandDeck:
    def __init__(self, session: SyncSession, *, use_color: bool) -> None:
        self.session = session
        self.use_color = use_color
        self.focus = 0
        self.reviewing = False
        self.confirming = False
        self.notice = ""

    @property
    def focused_row(self):
        rows = self.session.view.rows
        return rows[self.focus] if rows else None

    def move(self, offset: int) -> None:
        if not self.confirming and not self.reviewing:
            self.focus = max(0, min(self.focus + offset, len(self.session.view.rows) - 1))

    def back(self) -> bool:
        """Return False only when Escape requests abort from the workset."""
        if self.confirming:
            self.confirming = False
        elif self.reviewing:
            self.reviewing = False
        else:
            return False
        return True

    def confirm(self) -> None:
        if self.reviewing or self.confirming:
            return
        view = self.session.view
        command = "preview" if view.preview else "execute"
        # Command availability is not proof that the reviewed Approval set is ready.
        invalid_approved = any(
            row.included and row.approved
            and (row.proposal is None or any(item.severity == "error" for item in row_diagnostics(row)))
            for row in view.rows
            if not isinstance(row, (AuxiliaryRow, AdditionalRow))
        )
        if view.topology_diagnostics:
            self.notice = view.topology_diagnostics[0].message
            return
        if command not in view.allowed_commands or invalid_approved:
            self.notice = "Selected Proposals must be ready before confirmation."
            return
        self.confirming = True

    def click(self, index: int, *, selection: bool) -> None:
        if self.reviewing or self.confirming:
            return
        self.focus = index
        if selection:
            self.select()

    def select(self, approved: bool | None = None) -> None:
        row = self.focused_row
        if row is None or self.confirming:
            return
        selected = row.included if selection_uses_inclusion(row) else row.approved
        result = set_selected(self.session, row, not selected if approved is None else approved)
        self.notice = result.reason if isinstance(result, CommandRejected) else ""

    def select_all(self, approved: bool) -> None:
        if self.reviewing or self.confirming:
            return
        result = set_all_selected(self.session, approved)
        self.notice = result.reason if isinstance(result, CommandRejected) else ""

    def open_review(self) -> None:
        row = self.focused_row
        if row is None or self.confirming or self.reviewing:
            return
        if isinstance(row, AuxiliaryRow):
            self.notice = "Auxiliary work has no Proposal to review."
            return
        result = review(self.session, row.row_id)
        if isinstance(result, CommandRejected):
            self.notice = result.reason
        else:
            self.reviewing = True

    def edit(self) -> None:
        row = self.focused_row
        if row is None or self.confirming or "edit-proposal" not in row.allowed_commands:
            return
        result = edit_proposal(self.session, row.row_id)
        if isinstance(result, CommandRejected):
            self.notice = result.reason
        elif result.result.status == "cancelled":
            self.notice = "Editor cancelled; previous Proposal preserved."
        else:
            self.notice = "\n".join(item.message for item in result.result.diagnostics)

    def confirmation_text(self) -> str:
        selected = [row for row in self.session.view.rows if not isinstance(row, (AuxiliaryRow, AdditionalRow)) and row.approved]
        auxiliary_count = sum(row.included for row in self.session.view.rows if isinstance(row, AuxiliaryRow))
        effects = [effect for row in selected if row.proposal
                   for effect in row.proposal.publication_effects]
        writes = sum(effect.kind == "write" for effect in effects)
        deletions = sum(effect.kind == "delete" for effect in effects)
        modes = sum(effect.kind == "chmod" for effect in effects)
        repository_changes = sum(row.proposal.primary_source_change is not None for row in selected if row.proposal)
        additional_count = sum(row.approved for row in self.session.view.rows if isinstance(row, AdditionalRow))
        repository_changes += additional_count
        verb = "Preview" if self.session.view.preview else "Execute"
        stats = summary_stats(
            (("approved", len(selected)), ("additional", additional_count),
             ("auxiliary", auxiliary_count), ("repos", repository_changes)),
            writes=writes, deletions=deletions, trailing=(("modes", modes),), use_color=self.use_color,
        )
        hints = render_key_hints((("Enter", "confirm"), ("Esc", "return")), use_color=self.use_color)
        return f":: {verb}? — {stats}\n\n  {hints}"

    def review_text(self) -> str:
        row = self.focused_row
        if row is None or isinstance(row, AuxiliaryRow):
            return ""
        if isinstance(row, AdditionalRow):
            lines = [f":: Additional Source Review — {additional_label(row, use_color=self.use_color)}",
                     f"  Approval: {'approved' if row.approved else 'unapproved'}",
                     "  References: " + ", ".join(row.references)]
            lines.extend(_frozen_difference(
                FilePresent(row.change.before), FilePresent(row.change.candidate),
                before_label="frozen Additional Source",
                after_label="candidate Additional Source",
                description="Additional Source",
            ))
            hints = (("↑/↓", "scroll"), ("Space", "Approval"), ("Esc", "return to workset"))
            lines += ["", f"  {render_key_hints(hints, use_color=self.use_color)}"]
            return "\n".join(lines)
        proposal = row.proposal
        intent = row.intent
        pull = self.session.view.operation == "pull" or intent in ("use-live", "merge")
        capture_required = pull and not (proposal and proposal.intent == "editor")
        ui = current_ui_config()
        display_path = lambda path: display_review_path(path, compact=not (ui and ui.full_paths))
        primary = primary_change_summary(proposal, row.observation.repository_path)
        lines = [f":: Proposal Review — {row.row_id}",
                 f"  Approval: {'approved' if row.approved else 'unapproved'}",
                 f"  Observation: {row.observation.state}",
                 f"  Policy: {row.observation.effective_policy}",
                 f"  Configured policy: {row.observation.configured_policy}",
                 f"  Repository path: {display_path(row.observation.repository_path)}",
                 f"  Live path: {display_path(row.observation.live_path)}",
                 f"  Resolution: {render_sync_term(row_resolution(row), use_color=self.use_color) if intent or self.session.view.operation == 'pull' else 'blocked'}",
                 f"  Sync Base: {row.observation.base.status}",
                 f"  Primary Source Change: {primary['kind'] if primary else 'none'}",
                 f"  Capture: {('missing' if isinstance(proposal.capture, Missing) else 'present') if proposal and proposal.capture is not None else 'pending' if capture_required else 'not required'}",
                 f"  Reconciliation: {proposal.reconciliation if proposal else 'pending'}"]
        if "authorize-symlink-replacement" in row.allowed_commands:
            term = "Link replacement authorized" if row.symlink_authorized else "Link replacement requires authorization"
            lines.append(f"  {render_sync_term(term, use_color=self.use_color)} (L)")
        base = row.observation.base
        if base.reason:
            lines.append(f"  Base reason: {base.reason}")
        if base.record:
            lines += [
                f"  Base fingerprint: {base.record.envelope.fingerprint}",
                f"  Base payload: {'missing' if isinstance(base.record.payload, Missing) else 'present'}",
                "  Base vs frozen repository:",
            ]
            lines.extend(_frozen_difference(
                base.record.payload, row.observation.repository,
                before_label="Sync Base", after_label="frozen repository",
                description="Base",
            ))
        if row.fallback_reason:
            lines.append(f"  {render_sync_term('Fallback', use_color=self.use_color)}: {row.fallback_reason}")
        if proposal and proposal.capture is not None:
            lines.append("  Capture result vs frozen repository:")
            lines.extend(_frozen_difference(
                row.observation.repository, proposal.capture,
                before_label="frozen repository", after_label="Capture result",
                description="Capture",
            ))
        if primary:
            lines.append(f"    {display_path(primary['path'])} (authorized by Proposal Approval)")
        if proposal and row.observation.configured_policy in ("pull-only", "both"):
            lines.append(f"  Checkpoint qualified: {'yes' if proposal.checkpoint_qualified else 'no'}")
        lines.extend(f"  {item.severity}: {item.message}" for item in row_diagnostics(row))
        # Pull Views are frozen Observation evidence, independent of the chosen intent.
        if row.observation.effective_policy in ("both", "pull-only"):
            observation = row.observation
            lines += ["", "  Frozen Pull Views:",
                      f"    Repository comparison: {observation.compare_repo}",
                      f"    Live comparison: {observation.compare_live}"]
            for label, state in (("Repository", observation.comparison_repository),
                                 ("Live", observation.comparison_live)):
                kind = "unavailable" if state is None else "missing" if isinstance(state, Missing) else "present"
                lines.append(f"    {label} Pull View: {kind}")
            lines.extend(_frozen_difference(
                observation.comparison_repository, observation.comparison_live,
                before_label="frozen repository Pull View",
                after_label="frozen live Pull View", description="Pull Views",
            ))
        if proposal is not None:
            lines += ["", "  Frozen Publication Effects:"]
            for effect in proposal.publication_effects:
                summary = effect_summary(effect)
                detail = f"    {effect.kind} {display_path(summary['path'])}"
                if "bytes" in summary:
                    detail += f" ({summary['bytes']} bytes)"
                if "mode" in summary:
                    detail += f" → {summary['mode']}"
                lines.append(detail)
            if not proposal.publication_effects:
                lines.append("    none (Approval still required)")
            repository_effect = pull or row.observation.effective_policy == "both" or proposal.intent == "editor"
            side = "repository" if repository_effect else "live"
            before = row.observation.repository if repository_effect else row.observation.live
            after = proposal.repository if repository_effect else proposal.live
            if row.observation.effective_policy == "pull-only":
                lines.append("  Live remains unchanged")
            lines.append(f"  {side.capitalize()} effect preview:")
            lines.extend(_frozen_difference(
                before, after, before_label=f"frozen {side}",
                after_label=f"approved {side} outcome", description=f"{side} outcome",
            ))
            if repository_effect and row.observation.effective_policy != "pull-only":
                lines.append("  Live effect preview:")
                lines.extend(_frozen_difference(
                    row.observation.live, proposal.live,
                    before_label="frozen live", after_label="approved live outcome",
                    description="live outcome",
                ))
            lines.append(f"  Frozen {side}: {'missing' if isinstance(before, Missing) else 'present'}")
            lines.append(f"  {side.capitalize()} outcome: {'missing' if isinstance(after, Missing) else 'present'}")
        additional = [item for item in self.session.view.rows
                      if isinstance(item, AdditionalRow) and row.row_id in item.references]
        if additional:
            lines += ["", "  Additional Source Changes (independent Approval and Review):"]
            lines.extend(f"    {additional_label(item, use_color=self.use_color)}" for item in additional)
        hints = (("↑/↓", "scroll"), ("Space", "Approval"), ("E", "edit"), ("T", "retry"), ("Esc", "return to workset"))
        lines += ["", f"  {render_key_hints(hints, use_color=self.use_color)}"]
        return "\n".join(lines)


def auxiliary_row_label(row: AuxiliaryRow, *, use_color: bool) -> str:
    pattern = row.guard_skip.path_rule_pattern if row.guard_skip is not None else None
    return auxiliary_label(row.scope, row.kind, row.directions, path_rule_pattern=pattern, use_color=use_color)


def unit_label(row, *, use_color: bool) -> str:
    identity = row.observation.identity
    label = render_package_label(
        repo_name=identity.repo, package_id=identity.package_id,
        target_name=identity.target_name, bound_profile=identity.bound_profile,
        use_color=use_color,
    )
    return label if identity.child_path is None else f"{label}/{identity.child_path}"


def row_resolution(row) -> str:
    """Capability absence is not a failed filesystem observation."""
    if isinstance(row, AuxiliaryRow):
        return auxiliary_resolution(row.kind)
    if isinstance(row, AdditionalRow):
        return "Additional Source Change"
    if any(item.severity == "error" for item in row.observation.diagnostics) or row.observation.state == "observation-failed":
        return "Observation failed"
    if row.diagnostics:
        return "Proposal failed"
    if "prepare-proposal-review" not in row.allowed_commands:
        # In-sync units appear only to surface their warnings.
        return "In sync" if row.observation.state == "directly-in-sync" else "Unsupported"
    # Pull leaves its fixed direction implicit; Push records use-repository.
    return resolution_label((row.proposal and row.proposal.intent) or row.intent or "use-live")


def elide_middle(label: Text, width: int) -> Text:
    """Keep both the repo prefix and the target-name tail of long identities."""
    if label.cell_len <= width:
        return label
    # Identities are canonical ASCII in practice; character slicing may leave
    # wide glyphs slightly over width, which DataTable then crops.
    kept = max(width - 1, 0)
    head = kept // 2
    return Text.assemble(label[:head], "…", label[len(label) - (kept - head):])


class WorksetTable(DataTable):
    """Render native cells; the app input boundary owns row actions."""

    # Narrower targets hide too much identity; horizontal scrolling takes over.
    MIN_TARGET_WIDTH = 16
    TARGET_COLUMN = 1

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.full_targets: dict[str, Text] = {}
        self._fitted_target_width: int | None = None

    def add_workset_row(self, row_id: str, target: Text, policy: str) -> None:
        self.full_targets[row_id] = target
        self._fitted_target_width = None
        self.add_row("", target, policy, "", key=row_id)

    def clear(self, columns: bool = False):
        self.full_targets.clear()
        self._fitted_target_width = None
        return super().clear(columns)

    def fit_targets(self) -> None:
        """Shrink the Target column so Selection, Policy and Resolution stay on screen."""
        if not self.full_targets or not self.size.width:
            return
        target_key = self.ordered_columns[self.TARGET_COLUMN].key
        other_columns = sum(
            column.get_render_width(self) for column in self.ordered_columns if column.key != target_key
        )
        available = self.scrollable_content_region.width - other_columns - 2 * self.cell_padding
        width = max(self.MIN_TARGET_WIDTH, available)
        if width == self._fitted_target_width:
            return
        self._fitted_target_width = width
        for row_id, target in self.full_targets.items():
            self.update_cell(row_id, target_key, elide_middle(target, width), update_width=True)

    def on_resize(self, event: events.Resize) -> None:
        self.fit_targets()

    def on_click(self, event: events.Click) -> None:
        if event.style.meta.get("row", -1) >= 0:
            # Row clicks already ran in input order at the App boundary. Letting
            # DataTable replay them here could undo newer keyboard navigation.
            event.prevent_default()
            event.stop()


PROGRESS_REVEAL_DELAY_SECONDS = 0.3
PROGRESS_FRAME_SECONDS = 0.1


class SyncDeckApp(App[bool]):
    """Terminal adapter: the public session remains the sole mutation authority."""

    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen { background: $surface; }
    #title { height: auto; padding: 0 1; text-style: bold; color: $accent; }
    #workset { height: 1fr; }
    #detail { height: auto; max-height: 5; padding: 0 1; overflow-y: auto; }
    #resolution { height: auto; max-height: 5; border: round $accent; margin: 0 1; }
    #review { height: 1fr; }
    #confirmation { height: 1fr; padding: 1 2; overflow-y: auto; }
    #notice { height: auto; padding: 0 1; color: $warning; }
    #help { dock: bottom; height: auto; max-height: 2; padding: 0 1; color: $text-muted; }
    """
    BINDINGS = [
        *[
            Binding(key, f"navigate('{table_action}', '{review_action}')",
                    show=False, priority=True)
            for key, table_action, review_action in (
                ("up,k", "cursor_up", "scroll_up"),
                ("down,j", "cursor_down", "scroll_down"),
                ("left,h", "cursor_left", "scroll_left"),
                ("right,l", "cursor_right", "scroll_right"),
                ("pageup", "page_up", "page_up"),
                ("pagedown", "page_down", "page_down"),
                ("home", "scroll_home", "scroll_home"),
                ("end", "scroll_end", "scroll_end"),
                ("ctrl+home", "scroll_top", "scroll_home"),
                ("ctrl+end", "scroll_bottom", "scroll_end"),
            )
        ],
        Binding("r,R", "resolution", "Resolution", priority=True),
        Binding("t,T", "retry", "Retry", priority=True),
        # Lowercase l is vim-style right; authorization needs the deliberate Shift+L.
        Binding("L", "authorize_link", "Authorize link replacement", priority=True),
        Binding("e,E", "editor", "Editor", priority=True),
        Binding("space", "approve", "Select", priority=True),
        Binding("a,A", "approve_all", "Select all", priority=True),
        Binding("u,U", "clear_all", "Clear all", priority=True),
        Binding("enter", "review_or_confirm", "Review / Confirm", priority=True),
        Binding("x,X", "confirm", "Preview / Execute", priority=True),
        # Ctrl+C stays Abort, so copying needs its own key (vim-style yank).
        Binding("y,Y", "copy", "Copy", priority=True),
        Binding("escape", "back", "Back / Abort", priority=True),
        Binding("ctrl+c", "abort", "Abort", priority=True),
    ]

    def __init__(self, deck: CommandDeck) -> None:
        super().__init__(ansi_color=True)
        self.theme = "ansi-dark"
        self.deck = deck
        self.review_positions: dict[str, tuple[float, float]] = {}
        self._workset_mouse_down = False
        self._lane = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sync-materialization")
        self._materialization: asyncio.Task | None = None
        self._aborting = False
        self._editing = False

    @property
    def busy(self) -> bool:
        # Completed dispatch has drained even before its done callback clears the handle.
        return self._aborting or (
            self._materialization is not None and not self._materialization.done()
        )

    def materialize(self, action, *, editor_io: str | None = None, row_ids: tuple[str, ...] | None = None) -> None:
        """Admit one mutation; all further input is rejected until actual work drains."""
        if self.busy:
            return
        self._editing = editor_io is not None
        self._progress_label = (
            "Editing Proposal · Ctrl+C cancel Editor" if self._editing
            else "Materializing Proposal · Ctrl+C abort"
        )
        focused = self.deck.focused_row
        self._progress_rows = row_ids if row_ids is not None else ((focused.row_id,) if focused else ())
        self._progress_spinner = Spinner("dots")
        self._progress_started = time.monotonic()
        # A terminal Editor owns the screen, so deck progress would never be seen.
        self._progress = None if editor_io == "tty" else self.set_interval(PROGRESS_FRAME_SECONDS, self._render_progress)
        self._materialization = asyncio.create_task(self._materialize(action, editor_io=editor_io))
        self._materialization.add_done_callback(self._materialization_finished)

    def _render_progress(self) -> None:
        """Spin in the affected rows' Selection cells, so progress takes no extra line."""
        now = time.monotonic()
        # Most dispatches finish within a frame or two; showing progress
        # immediately makes every Enter/Space flash it.
        if not self.busy or now - self._progress_started < PROGRESS_REVEAL_DELAY_SECONDS:
            return
        frame = self._progress_spinner.render(now).plain
        table = self.query_one(WorksetTable)
        help_text = self._progress_label
        if table.display:
            for row_id in self._progress_rows:
                # Pad to the "[ ]" marker width so the column does not jitter.
                table.update_cell(row_id, table.ordered_columns[0].key, Text(f" {frame} ", style="bold"))
        else:
            # Review has no Selection cells on screen; spin in the help line instead.
            help_text = f"{frame} {help_text}"
        self.query_one("#help", Static).update(help_text)

    def _materialization_finished(self, task: asyncio.Task) -> None:
        if self._materialization is task:
            self._materialization = None
        if not task.cancelled() and (error := task.exception()) is not None:
            self._handle_exception(error)

    async def _materialize(self, action, *, editor_io: str | None = None) -> None:
        def dispatch():
            # KeyboardInterrupt must not escape a Future into the asyncio runner.
            try:
                self.deck.session.check_cancelled()
                action()
                self.deck.session.check_cancelled()
            except KeyboardInterrupt as exc:
                raise InterruptedError("Materialization interrupted") from exc

        try:
            with ExitStack() as terminal:
                if editor_io == "tty":
                    # The event loop keeps running while the Editor owns the
                    # terminal. Suspension stops Textual's bounded writer
                    # thread, so repaints (the busy spinner) would fill its
                    # queue and block the loop forever. Resume runs first on
                    # exit (LIFO), then the batch ends and repaints the deck.
                    terminal.enter_context(self.batch_update())
                    suspension = self.suspend()
                    suspension.__enter__()
                    # Textual resumes after its yield only on normal context exit.
                    # Always restore the terminal, including unexpected dispatch errors.
                    terminal.callback(suspension.__exit__, None, None, None)
                work = asyncio.get_running_loop().run_in_executor(
                    self._lane, copy_context().run, dispatch,
                )
                try:
                    await asyncio.shield(work)
                except asyncio.CancelledError:
                    self._aborting = True
                    self.deck.session.request_cancel()
                    try:
                        await asyncio.shield(work)
                    except InterruptedError:
                        pass
                except InterruptedError:
                    self._aborting = True
                except Exception:
                    self._aborting = True
                    raise
        except SuspendNotSupported:
            self.deck.notice = "Terminal Editor requires a suspend-capable terminal."
        finally:
            self._materialization = None
            self._editing = False
            if self._progress is not None:
                self._progress.stop()
        if self._aborting or any(d.code == "interrupted" for row in self.deck.session.view.rows for d in row.diagnostics):
            self.exit(False)
            return
        self.update_workset()
        if self.deck.reviewing:
            self.show_review()

    async def on_unmount(self) -> None:
        if self._materialization is not None:
            self._aborting = True
            self.deck.session.request_cancel()
            await asyncio.shield(self._materialization)
        # Textual cancellation is not a thread join. Release the lane only after
        # its dispatch and provider temporary-resource cleanup have really ended.
        self._lane.shutdown(wait=True)

    async def on_event(self, event: events.Event) -> None:
        if self.busy and isinstance(event, events.InputEvent):
            if isinstance(event, events.Key) and event.key == "ctrl+c":
                self.action_abort()
            event.stop()
            event.prevent_default()
            return
        # App receives terminal input in order, before forwarding mouse events to
        # widget queues. Resolve row clicks here alongside priority key actions;
        # neither input modality may overtake the other when bytes arrive together.
        if isinstance(event, (events.MouseDown, events.MouseUp)) and not event.is_forwarded:
            try:
                widget, _ = self.get_widget_at(event.screen_x, event.screen_y)
            except NoWidget:
                widget = None
            workset = isinstance(widget, WorksetTable) and not (
                self.deck.reviewing or self.deck.confirming
            )
            if isinstance(event, events.MouseDown):
                self._workset_mouse_down = workset
            else:
                if workset and self._workset_mouse_down:
                    metadata = self.screen.get_style_at(event.screen_x, event.screen_y).meta
                    row, column = metadata.get("row", -1), metadata.get("column", -1)
                    if row >= 0 and column >= 0 and not metadata.get("out_of_bounds"):
                        widget.move_cursor(row=row, column=column)
                        self.deck.focus = row
                        if column == 0:
                            self.materialize(self.deck.select)
                        self.close_resolution()
                        self.update_workset()
                        if column == 3:
                            self.action_resolution()
                self._workset_mouse_down = False
        # Preserve native focus, mouse capture, selection cleanup and scrolling.
        await super().on_event(event)

    def compose(self) -> ComposeResult:
        yield Static(f":: {self.deck.session.view.operation.title()} Command Deck", id="title", markup=False)
        yield WorksetTable(id="workset", cursor_type="cell", zebra_stripes=True)
        yield Static(id="detail", markup=False)
        yield OptionList(id="resolution")
        yield RichLog(id="review", wrap=False, auto_scroll=False, min_width=1)
        yield Static(id="confirmation", markup=False)
        yield Static(id="notice", markup=False)
        yield Static(id="help", markup=False)

    def on_mount(self) -> None:
        self.query_one(OptionList).display = False
        table = self.query_one(WorksetTable)
        # The Selection header matches its narrow "[ ]" markers, leaving width for targets.
        table.add_columns("✓", "Target", "Policy", "Resolution")
        self.show_workset()

    def rebuild_workset(self) -> None:
        table = self.query_one(WorksetTable)
        table.clear()
        self.deck.focus = max(0, min(self.deck.focus, len(self.deck.session.view.rows) - 1))
        for row in self.deck.session.view.rows:
            if isinstance(row, AdditionalRow):
                table.add_workset_row(row.row_id, Text.from_ansi(additional_label(row, use_color=self.deck.use_color)), "")
                continue
            if isinstance(row, AuxiliaryRow):
                table.add_workset_row(row.row_id, Text.from_ansi(auxiliary_row_label(row, use_color=self.deck.use_color)), "")
                continue
            table.add_workset_row(row.row_id, Text.from_ansi(unit_label(row, use_color=self.deck.use_color)),
                                  row.observation.effective_policy)
        table.move_cursor(row=self.deck.focus)

    def update_workset(self) -> None:
        table = self.query_one(WorksetTable)
        # Editor saves can add or remove canonical source rows.
        if tuple(key.value for key in table.rows) != tuple(row.row_id for row in self.deck.session.view.rows):
            self.rebuild_workset()
        for row in self.deck.session.view.rows:
            auxiliary = selection_uses_inclusion(row)
            selected = row.included if auxiliary else row.approved
            marker = "[x]" if selected else "[ ]" if {"set-included", "set-approval"}.intersection(row.allowed_commands) else "[-]"
            term = ("selected" if selected else "unselected") if auxiliary else ("approved" if selected else "unapproved")
            table.update_cell(row.row_id, table.ordered_columns[0].key,
                              Text.from_ansi(render_sync_term(term, use_color=self.deck.use_color).replace(term, marker)),
                              update_width=True)
            table.update_cell(row.row_id, table.ordered_columns[3].key,
                              Text.from_ansi(render_sync_term(row_resolution(row), use_color=self.deck.use_color)),
                              update_width=True)
        # Resolution width varies with intent, so refit after every cell update.
        table.fit_targets()
        self.update_detail()
        self.query_one("#notice", Static).update(self.deck.notice)
        review_scroll = ("↑/↓/j/k/PgUp/PgDn", "scroll")
        if self.query_one(OptionList).display:
            hints = [("↑/↓/j/k", "choose Resolution"), ("Enter", "select"), ("Esc", "dismiss")]
        elif self.deck.confirming:
            hints = [("Enter", "confirm"), ("Esc", "return"), ("Ctrl+C", "abort")]
        elif self.deck.reviewing and isinstance(self.deck.focused_row, AdditionalRow):
            hints = [("Esc", "return"), ("Space", "Approval"), ("Y", "copy"), review_scroll, ("Ctrl+C", "abort")]
        elif self.deck.reviewing:
            hints = [("Esc", "return"), ("Space", "Approval"), ("E", "edit"), ("T", "retry"), ("Y", "copy"),
                     review_scroll, ("Ctrl+C", "abort")]
        else:
            hints = [("Esc", "abort"), ("X", "confirm"), ("Space", "mark"), ("Enter", "view"), ("E", "edit"), ("T", "retry")]
        row = self.deck.focused_row
        if row and "authorize-symlink-replacement" in row.allowed_commands and not self.deck.confirming:
            hints.append(("Shift+L", "authorize link replacement"))
        if self.deck.session.view.operation == "sync" and not self.deck.reviewing and not self.query_one(OptionList).display and not self.deck.confirming:
            hints.append(("R", "intent"))
        self.query_one("#help", Static).update(Text.from_ansi(render_key_hints(hints, use_color=self.deck.use_color)))

    def update_detail(self) -> None:
        row = self.deck.focused_row
        use_color = self.deck.use_color
        if row is None:
            lines = [render_payload_section_label("No drifted work.", use_color=use_color)]
        elif isinstance(row, AdditionalRow):
            lines = [additional_label(row, use_color=use_color)]
        elif isinstance(row, AuxiliaryRow):
            lines = [auxiliary_row_label(row, use_color=use_color)]
            if row.guard_skip is not None:
                lines.append(f"  {render_sync_term('Guard skipped', use_color=use_color)}: {guard_skip_explanation(row)}")
        else:
            lines = [unit_label(row, use_color=use_color)]
            if row.fallback_reason:
                lines.append(f"  {render_sync_term('Fallback', use_color=use_color)}: {row.fallback_reason}")
        if row is not None:
            lines[1:1] = [f"  {render_sync_term(item.severity, use_color=use_color)}: {item.message}"
                          for item in row_diagnostics(row)]
        self.query_one("#detail", Static).update(Text.from_ansi("\n".join(lines)))

    def on_data_table_cell_highlighted(self, event: DataTable.CellHighlighted) -> None:
        if not self.busy and not self.deck.reviewing and not self.deck.confirming:
            self.sync_focus()
            self.update_detail()

    def show_workset(self) -> None:
        self.query_one("#workset").display = True
        self.query_one("#detail").display = True
        self.query_one("#review").display = False
        self.query_one("#confirmation").display = False
        self.query_one("#title", Static).update(f":: {self.deck.session.view.operation.title()} Command Deck")
        self.update_workset()
        self.query_one(WorksetTable).focus()

    async def action_navigate(self, table_action: str, review_action: str) -> None:
        if self.busy:
            return
        # Every native cursor action shares the Approval/Review queue. Otherwise
        # batched terminal keys can approve the old row before navigation runs.
        if self.deck.confirming:
            return
        if self.query_one(OptionList).display:
            menu_action = {"cursor_up": "cursor_up", "cursor_down": "cursor_down", "scroll_home": "first", "scroll_end": "last"}.get(table_action)
            if menu_action:
                await self.query_one(OptionList).run_action(menu_action)
            return
        if self.deck.reviewing:
            await self.query_one(RichLog).run_action(review_action)
        else:
            table = self.query_one(WorksetTable)
            if table.row_count:
                await table.run_action(table_action)
            self.sync_focus()

    def sync_focus(self) -> None:
        # A following key may arrive before CellHighlighted is delivered (paste/PTY).
        # Read the widget cursor at the action boundary, not the queued notification.
        if not self.deck.reviewing and not self.deck.confirming:
            self.deck.focus = self.query_one(WorksetTable).cursor_row

    def action_approve(self) -> None:
        if self.busy:
            return
        if self.query_one(OptionList).display:
            return
        self.sync_focus()
        self.materialize(self.deck.select)

    def action_approve_all(self) -> None:
        if self.busy:
            return
        if self.query_one(OptionList).display:
            return
        self.materialize(lambda: self.deck.select_all(True), row_ids=self.all_row_ids())

    def action_clear_all(self) -> None:
        if self.busy:
            return
        if self.query_one(OptionList).display:
            return
        self.materialize(lambda: self.deck.select_all(False), row_ids=self.all_row_ids())

    def all_row_ids(self) -> tuple[str, ...]:
        return tuple(row.row_id for row in self.deck.session.view.rows)

    def show_review(self) -> None:
        log = self.query_one("#review", RichLog)
        position = self.review_positions.get(self.deck.focused_row.row_id, (0, 0))
        if log.display:
            position = (log.scroll_x, log.scroll_y)
        self.query_one("#workset").display = False
        self.query_one("#detail").display = False
        log.display = True
        log.clear()
        log.write(Text.from_ansi(self.deck.review_text()), scroll_end=False)
        self.query_one("#title", Static).update(":: Additional Source Review" if isinstance(self.deck.focused_row, AdditionalRow) else ":: Proposal Review")
        log.focus()
        self.call_after_refresh(log.scroll_to, *position, animate=False)

    def action_review_or_confirm(self) -> None:
        if self.busy:
            return
        menu = self.query_one(OptionList)
        if menu.display:
            if menu.highlighted is not None:
                self.choose_resolution(menu.highlighted)
            return
        self.sync_focus()
        if self.deck.confirming:
            self.exit(True)
        elif not self.deck.reviewing:
            self.materialize(self.deck.open_review)

    def action_confirm(self) -> None:
        if self.busy:
            return
        self.close_resolution()
        self.deck.confirm()
        if self.deck.confirming:
            self.query_one("#workset").display = False
            self.query_one("#detail").display = False
            self.query_one("#confirmation").display = True
            self.query_one("#confirmation", Static).update(Text.from_ansi(self.deck.confirmation_text()))
            self.query_one("#title", Static).update(":: Confirmation")
            self.set_focus(None)
        self.update_workset()

    def action_back(self) -> None:
        if self.busy:
            return
        if self.query_one(OptionList).display:
            self.close_resolution()
            self.update_workset()
            return
        if self.deck.reviewing:
            log = self.query_one("#review", RichLog)
            self.review_positions[self.deck.focused_row.row_id] = (log.scroll_x, log.scroll_y)
        if not self.deck.back():
            self.exit(False)
        else:
            self.show_workset()

    def close_resolution(self) -> None:
        self.query_one(OptionList).display = False
        self.query_one(WorksetTable).focus()

    def action_resolution(self) -> None:
        if self.busy:
            return
        if self.deck.reviewing or self.deck.confirming:
            return
        self.sync_focus()
        row = self.deck.focused_row
        if row is None or "set-resolution-intent" not in row.allowed_commands or len(row.allowed_intents) < 2:
            return
        menu = self.query_one(OptionList)
        menu.clear_options()
        menu.add_options([Text.from_ansi(render_sync_term(resolution_label(intent), use_color=self.deck.use_color))
                          for intent in row.allowed_intents])
        menu.highlighted = row.allowed_intents.index(row.intent)
        menu.display = True
        menu.focus()
        self.update_workset()

    def choose_resolution(self, index: int) -> None:
        if self.busy:
            return
        row = self.deck.focused_row
        intent = row.allowed_intents[index]
        self.close_resolution()
        def change():
            result = set_resolution_intent(self.deck.session, row.row_id, intent)
            self.deck.notice = result.reason if isinstance(result, CommandRejected) else ""
        self.materialize(change)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if self.busy:
            return
        if self.query_one(OptionList).display:
            self.choose_resolution(event.option_index)

    def action_editor(self) -> None:
        if self.busy or self.deck.confirming or self.query_one(OptionList).display:
            return
        self.sync_focus()
        row = self.deck.focused_row
        if row is None or "edit-proposal" not in row.allowed_commands:
            return
        self.materialize(self.deck.edit, editor_io=row.editor_io)

    def action_authorize_link(self) -> None:
        if self.busy or self.deck.confirming or self.query_one(OptionList).display:
            return
        self.sync_focus()
        row = self.deck.focused_row
        if row is None or "authorize-symlink-replacement" not in row.allowed_commands:
            return
        def authorize():
            view = self.deck.session.view
            self.deck.session.dispatch(AuthorizeSymlinkReplacement(view.session_id, view.revision, row.row_id))
            self.deck.notice = "Link replacement authorized; select the Proposal to approve."
        self.materialize(authorize)

    def action_retry(self) -> None:
        if self.busy:
            return
        if self.deck.confirming:
            return
        self.sync_focus()
        row = self.deck.focused_row
        if row is None or "retry-materialization" not in row.allowed_commands:
            return
        def retry():
            result = retry_materialization(self.deck.session, row.row_id)
            self.deck.notice = result.reason if isinstance(result, CommandRejected) else ""
        self.materialize(retry)

    def action_copy(self) -> None:
        """Copy the full Target identity, or the whole review, via the terminal clipboard (OSC 52)."""
        if self.busy or self.deck.confirming or self.query_one(OptionList).display:
            return
        self.sync_focus()
        row = self.deck.focused_row
        if row is None:
            return
        if self.deck.reviewing:
            # The RichLog shows only a viewport and cannot be selected in-app.
            text, subject = Text.from_ansi(self.deck.review_text()).plain, "review"
        else:
            # The Target cell may be elided; copy the untruncated identity.
            # Not listed in workset help: it would wrap at 80 columns and cost a row.
            text, subject = self.query_one(WorksetTable).full_targets[row.row_id].plain, "Target"
        self.copy_to_clipboard(text)
        self.deck.notice = f"Copied {subject} to clipboard."
        self.update_workset()

    def action_abort(self) -> None:
        if self._editing:
            self.deck.session.request_editor_cancel()
            return
        self._aborting = True
        self.deck.session.request_cancel()
        if self._materialization is None or self._materialization.done():
            self.exit(False)


def run_command_deck(session: SyncSession, *, use_color: bool) -> bool:
    app = SyncDeckApp(CommandDeck(session, use_color=use_color))
    previous = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, lambda signum, frame: app.action_abort())
    try:
        return bool(app.run())
    finally:
        try:
            if app._materialization is not None:
                session.request_cancel()
            app._lane.shutdown(wait=True)
        finally:
            signal.signal(signal.SIGINT, previous)
