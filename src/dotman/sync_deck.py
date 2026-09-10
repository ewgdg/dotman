"""Persistent initial Command Deck over public immutable SyncSession views."""

from __future__ import annotations

import asyncio
from contextvars import copy_context
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
from difflib import unified_diff
import signal

from rich.spinner import Spinner

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult, SuspendNotSupported
from textual.binding import Binding
from textual.errors import NoWidget
from textual.widgets import DataTable, OptionList, RichLog, Static

from dotman.cli_style import render_sync_term, render_package_label
from dotman.sync_base_store import DirectoryChildPresent, FilePresent, Missing
from dotman.sync_deck_command import selection_uses_inclusion, auxiliary_resolution, additional_label, set_all_selected, set_selected, row_diagnostics, auxiliary_label, review, edit_proposal, set_resolution_intent, retry_materialization, effect_summary, primary_change_summary, resolution_label
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
            and (row.proposal is None or row_diagnostics(row))
            for row in view.rows
            if not isinstance(row, (AuxiliaryRow, AdditionalRow))
        )
        if view.topology_diagnostics:
            self.notice = view.topology_diagnostics[0].message
            return
        if command not in view.allowed_commands or (not view.preview and invalid_approved):
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
        return f":: {verb} {len(selected)} approved units / {additional_count} approved additional sources / {auxiliary_count} selected auxiliary / {repository_changes} repository changes / {writes} live writes / {deletions} live deletions / {modes} mode changes?\n\n  Enter confirm  Esc return"

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
            lines += ["", "  ↑/↓ scroll  Space Approval  Esc return to workset"]
            return "\n".join(lines)
        proposal = row.proposal
        intent = row.intent
        pull = self.session.view.operation == "pull" or intent in ("use-live", "merge")
        capture_required = pull and not (proposal and proposal.intent == "editor")
        primary = primary_change_summary(proposal, row.observation.repository_path)
        lines = [f":: Proposal Review — {row.row_id}",
                 f"  Approval: {'approved' if row.approved else 'unapproved'}",
                 f"  Observation: {row.observation.state}",
                 f"  Policy: {row.observation.effective_policy}",
                 f"  Configured policy: {row.observation.configured_policy}",
                 f"  Repository path: {row.observation.repository_path}",
                 f"  Live path: {row.observation.live_path}",
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
                f"  Base provenance: {base.record.envelope.provenance}",
                f"  Base commit: {base.record.envelope.commit_oid}",
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
            lines.append(f"    {primary['path']} (authorized by Proposal Approval)")
        lines.extend(f"  {item.message}" for item in (*row.observation.diagnostics, *row.diagnostics))
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
                detail = f"    {effect.kind} {summary['path']}"
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
        lines += ["", "  ↑/↓ scroll  Space Approval  E edit  T retry  Esc return to workset"]
        return "\n".join(lines)


def row_resolution(row) -> str:
    """Capability absence is not a failed filesystem observation."""
    if isinstance(row, AuxiliaryRow):
        return auxiliary_resolution(row.kind)
    if isinstance(row, AdditionalRow):
        return "Additional Source Change"
    if row.observation.diagnostics or row.observation.state == "observation-failed":
        return "Observation failed"
    if row.diagnostics:
        return "Proposal failed"
    if not row.allowed_intents:
        return ("Edited" if row.proposal and row.proposal.intent == "editor" else "Use live") if "prepare-proposal-review" in row.allowed_commands else "Unsupported"
    return resolution_label(row.proposal.intent if row.proposal else row.intent)


class WorksetTable(DataTable):
    """Render native cells; the app input boundary owns row actions."""

    def on_click(self, event: events.Click) -> None:
        if event.style.meta.get("row", -1) >= 0:
            # Row clicks already ran in input order at the App boundary. Letting
            # DataTable replay them here could undo newer keyboard navigation.
            event.prevent_default()
            event.stop()


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
    #busy { height: auto; padding: 0 1; color: $accent; text-style: bold; }
    #help { dock: bottom; height: auto; max-height: 2; padding: 0 1; color: $text-muted; }
    """
    BINDINGS = [
        *[
            Binding(key, f"navigate('{table_action}', '{review_action}')",
                    show=False, priority=True)
            for key, table_action, review_action in (
                ("up", "cursor_up", "scroll_up"),
                ("down", "cursor_down", "scroll_down"),
                ("left", "cursor_left", "scroll_left"),
                ("right", "cursor_right", "scroll_right"),
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
        Binding("l,L", "authorize_link", "Authorize link replacement", priority=True),
        Binding("e,E", "editor", "Editor", priority=True),
        Binding("space", "approve", "Select", priority=True),
        Binding("a,A", "approve_all", "Select all", priority=True),
        Binding("u,U", "clear_all", "Clear all", priority=True),
        Binding("enter", "review_or_confirm", "Review / Confirm", priority=True),
        Binding("x,X", "confirm", "Preview / Execute", priority=True),
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

    def materialize(self, action, *, editor_io: str | None = None) -> None:
        """Admit one mutation; all further input is rejected until actual work drains."""
        if self.busy:
            return
        self._editing = editor_io is not None
        self.query_one("#busy", Static).update(Spinner(
            "dots", text="Editing Proposal · Ctrl+C cancel Editor" if self._editing
            else "Materializing Proposal · Ctrl+C abort",
        ))
        self.query_one("#busy").display = True
        self._materialization = asyncio.create_task(self._materialize(action, editor_io=editor_io))
        self._materialization.add_done_callback(self._materialization_finished)

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
            self.query_one("#busy").display = False
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
        yield Static(Spinner("dots", text="Materializing Proposal · Ctrl+C abort"), id="busy")
        yield Static(id="help", markup=False)

    def on_mount(self) -> None:
        self.query_one("#busy").display = False
        self.query_one("#busy").auto_refresh = 1 / 10
        self.query_one(OptionList).display = False
        table = self.query_one(WorksetTable)
        table.add_columns("Selection", "Target", "Policy", "Resolution")
        self.show_workset()

    def rebuild_workset(self) -> None:
        table = self.query_one(WorksetTable)
        table.clear()
        self.deck.focus = max(0, min(self.deck.focus, len(self.deck.session.view.rows) - 1))
        for row in self.deck.session.view.rows:
            if isinstance(row, AdditionalRow):
                table.add_row("", Text.from_ansi(additional_label(row, use_color=self.deck.use_color)), "", "", key=row.row_id)
                continue
            if isinstance(row, AuxiliaryRow):
                table.add_row("", Text.from_ansi(auxiliary_label(row.scope, row.kind, row.directions, use_color=self.deck.use_color)), "", "", key=row.row_id)
                continue
            identity = row.observation.identity
            label = render_package_label(
                repo_name=identity.repo, package_id=identity.package_id,
                target_name=identity.target_name, bound_profile=identity.bound_profile,
                use_color=self.deck.use_color,
            )
            if identity.child_path is not None:
                label += "/" + identity.child_path
            table.add_row("", Text.from_ansi(label), row.observation.effective_policy, "", key=row.row_id)
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
        self.update_detail()
        self.query_one("#notice", Static).update(self.deck.notice)
        if self.query_one(OptionList).display:
            help_text = "↑/↓ choose Resolution · Enter select · Esc dismiss"
        elif self.deck.confirming:
            help_text = "Enter confirm · Esc return · Ctrl+C abort"
        elif self.deck.reviewing and isinstance(self.deck.focused_row, AdditionalRow):
            help_text = "Esc return · Space Approval · ↑/↓/PgUp/PgDn scroll · Ctrl+C abort"
        elif self.deck.reviewing:
            help_text = "Esc return · Space Approval · E edit · T retry · ↑/↓/PgUp/PgDn scroll · Ctrl+C abort"
        else:
            help_text = "Esc abort · X confirm · Space mark · Enter view · E edit · T retry"
        row = self.deck.focused_row
        if row and "authorize-symlink-replacement" in row.allowed_commands and not self.deck.confirming:
            help_text += " · L authorize link replacement"
        if self.deck.session.view.operation == "sync" and not self.deck.reviewing and not self.query_one(OptionList).display and not self.deck.confirming:
            help_text += " · R intent"
        self.query_one("#help", Static).update(help_text)

    def update_detail(self) -> None:
        row = self.deck.focused_row
        if row is None:
            detail = "No drifted work."
        elif isinstance(row, AdditionalRow):
            detail = additional_label(row, use_color=self.deck.use_color)
        elif isinstance(row, AuxiliaryRow):
            detail = auxiliary_label(row.scope, row.kind, row.directions)
            detail += "\n" + "\n".join(item.message for item in row_diagnostics(row))
        else:
            diagnostics = row_diagnostics(row)
            detail = "\n".join(item.message for item in diagnostics)
            if row.fallback_reason:
                detail += f"\nFallback: {row.fallback_reason}"
            detail = f"{row.row_id}\n{detail}" if detail else row.row_id
        self.query_one("#detail", Static).update(detail)

    def on_data_table_cell_highlighted(self, event: DataTable.CellHighlighted) -> None:
        if not self.busy and not self.deck.reviewing and not self.deck.confirming:
            self.sync_focus()
            self.update_detail()

    def show_workset(self) -> None:
        self.query_one("#workset").display = True
        self.query_one("#detail").display = True
        self.query_one("#review").display = False
        self.query_one("#confirmation").display = False
        self.query_one("#title", Static).update(":: Sync Command Deck")
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
        self.materialize(lambda: self.deck.select_all(True))

    def action_clear_all(self) -> None:
        if self.busy:
            return
        if self.query_one(OptionList).display:
            return
        self.materialize(lambda: self.deck.select_all(False))

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
            self.query_one("#confirmation", Static).update(self.deck.confirmation_text())
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
