"""Persistent initial Command Deck over public immutable SyncSession views."""

from __future__ import annotations

from difflib import unified_diff

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.errors import NoWidget
from textual.widgets import DataTable, Footer, RichLog, Static

from dotman.cli_style import render_sync_term, render_package_label
from dotman.sync_base_store import FilePresent, Missing
from dotman.sync_deck_command import approve, review, effect_summary, primary_change_summary, resolution_label
from dotman.sync_session import CommandRejected, SyncSession


def _frozen_difference(
    before: FilePresent | Missing | None,
    after: FilePresent | Missing | None,
    *,
    before_label: str,
    after_label: str,
    description: str,
) -> list[str]:
    if before is None or after is None:
        return ["    Comparison evidence unavailable"]
    before_bytes = before.content if isinstance(before, FilePresent) else b""
    after_bytes = after.content if isinstance(after, FilePresent) else b""
    if before_bytes == after_bytes:
        return ["    No content difference"]
    try:
        diff = unified_diff(
            before_bytes.decode("utf-8").splitlines(keepends=True),
            after_bytes.decode("utf-8").splitlines(keepends=True),
            fromfile="/dev/null" if isinstance(before, Missing) else before_label,
            tofile="/dev/null" if isinstance(after, Missing) else after_label,
            lineterm="\n",
        )
        lines = []
        for line in diff:
            lines.append(line.rstrip("\n"))
            # Retain newline-only drift in both evidence and effect previews.
            if not line.endswith("\n"):
                lines.append("\\ No newline at end of file")
        return lines
    except UnicodeDecodeError:
        return [f"  Binary {description}: {len(before_bytes)} → {len(after_bytes)} bytes"]


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
        command = "preview" if self.session.view.preview else "execute"
        if command not in self.session.view.allowed_commands:
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
        result = approve(self.session, row.row_id, not row.approved if approved is None else approved)
        self.notice = result.reason if isinstance(result, CommandRejected) else ""

    def select_all(self, approved: bool) -> None:
        if self.reviewing or self.confirming:
            return
        for row in self.session.view.rows:
            if "set-approval" in row.allowed_commands:
                approve(self.session, row.row_id, approved)

    def open_review(self) -> None:
        row = self.focused_row
        if row is None or self.confirming or self.reviewing:
            return
        result = review(self.session, row.row_id)
        if isinstance(result, CommandRejected):
            self.notice = result.reason
        else:
            self.reviewing = True

    def confirmation_text(self) -> str:
        selected = [row for row in self.session.view.rows if row.approved]
        effects = [effect for row in selected if row.proposal
                   for effect in row.proposal.publication_effects]
        writes = sum(effect.kind == "write" for effect in effects)
        deletions = sum(effect.kind == "delete" for effect in effects)
        modes = sum(effect.kind == "chmod" for effect in effects)
        repository_changes = sum(row.proposal.primary_source_change is not None for row in selected if row.proposal)
        verb = "Preview" if self.session.view.preview else "Execute"
        return f":: {verb} {len(selected)} approved units / {repository_changes} repository changes / {writes} live writes / {deletions} live deletions / {modes} mode changes?\n\n  Enter confirm  Esc return"

    def review_text(self) -> str:
        row = self.focused_row
        if row is None:
            return ""
        proposal = row.proposal
        intent = proposal.intent if proposal else row.allowed_intents[0] if row.allowed_intents else None
        pull = intent == "use-live"
        primary = primary_change_summary(proposal, row.observation.repository_path)
        lines = [f":: Proposal Review — {row.row_id}",
                 f"  Approval: {'approved' if row.approved else 'unapproved'}",
                 f"  Observation: {row.observation.state}",
                 f"  Policy: {row.observation.effective_policy}",
                 f"  Repository path: {row.observation.repository_path}",
                 f"  Live path: {row.observation.live_path}",
                 f"  Resolution: {render_sync_term(resolution_label(intent), use_color=self.use_color) if intent else 'blocked'}",
                 f"  Sync Base: {row.observation.base.status}",
                 f"  Primary Source Change: {primary['kind'] if primary else 'none'}",
                 f"  Capture: {'frozen live' if proposal else 'pending'}" if pull else "  Capture: not required",
                 "  Reconciliation: captured repository outcome" if pull else "  Reconciliation: frozen repository outcome"]
        if primary:
            lines.append(f"    {primary['path']} (authorized by Proposal Approval)")
        lines.extend(f"  {item.message}" for item in (*row.observation.diagnostics, *row.diagnostics))
        if pull:
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
            side = "repository" if pull else "live"
            before = row.observation.repository if pull else row.observation.live
            after = proposal.repository if pull else proposal.live
            if pull:
                lines.append("  Live remains unchanged")
            lines.append(f"  {side.capitalize()} effect preview:")
            lines.extend(_frozen_difference(
                before, after, before_label=f"frozen {side}",
                after_label=f"approved {side} outcome", description=f"{side} outcome",
            ))
            lines.append(f"  Frozen {side}: {'missing' if isinstance(before, Missing) else 'present'}")
            lines.append(f"  {side.capitalize()} outcome: {'missing' if isinstance(after, Missing) else 'present'}")
        lines += ["", "  ↑/↓ scroll  Space Approval  Esc return to workset"]
        return "\n".join(lines)


def row_resolution(row) -> str:
    """Capability absence is not a failed filesystem observation."""
    if row.observation.diagnostics or row.observation.state == "observation-failed":
        return "Observation failed"
    if row.diagnostics:
        return "Proposal failed"
    if not row.allowed_intents:
        return "Unsupported"
    return resolution_label(row.proposal.intent if row.proposal else row.allowed_intents[0])


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
    #review { height: 1fr; }
    #confirmation { height: 1fr; padding: 1 2; overflow-y: auto; }
    #notice { height: auto; padding: 0 1; color: $warning; }
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
        Binding("space", "approve", "Approval", priority=True),
        Binding("a,A", "approve_all", "Approve all", priority=True),
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

    async def on_event(self, event: events.Event) -> None:
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
                        self.deck.click(row, selection=column == 0)
                        self.update_workset()
                self._workset_mouse_down = False
        # Preserve native focus, mouse capture, selection cleanup and scrolling.
        await super().on_event(event)

    def compose(self) -> ComposeResult:
        yield Static(":: Sync Command Deck", id="title", markup=False)
        yield WorksetTable(id="workset", cursor_type="cell", zebra_stripes=True)
        yield Static(id="detail", markup=False)
        yield RichLog(id="review", wrap=False, auto_scroll=False, min_width=1)
        yield Static(id="confirmation", markup=False)
        yield Static(id="notice", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(WorksetTable)
        table.add_columns("Approval", "Target", "Policy", "Resolution")
        for row in self.deck.session.view.rows:
            identity = row.observation.identity
            label = render_package_label(
                repo_name=identity.repo, package_id=identity.package_id,
                target_name=identity.target_name, bound_profile=identity.bound_profile,
                use_color=self.deck.use_color,
            )
            table.add_row("", Text.from_ansi(label), row.observation.effective_policy, "", key=row.row_id)
        self.show_workset()

    def update_workset(self) -> None:
        table = self.query_one(WorksetTable)
        for row in self.deck.session.view.rows:
            marker = "[x]" if row.approved else "[ ]" if "set-approval" in row.allowed_commands else "[-]"
            term = "approved" if row.approved else "unapproved"
            table.update_cell(row.row_id, table.ordered_columns[0].key,
                              Text.from_ansi(render_sync_term(term, use_color=self.deck.use_color).replace(term, marker)),
                              update_width=True)
            table.update_cell(row.row_id, table.ordered_columns[3].key,
                              Text.from_ansi(render_sync_term(row_resolution(row), use_color=self.deck.use_color)),
                              update_width=True)
        self.update_detail()
        self.query_one("#notice", Static).update(self.deck.notice)

    def update_detail(self) -> None:
        row = self.deck.focused_row
        if row is None:
            detail = "No drifted work."
        else:
            diagnostics = (*row.observation.diagnostics, *row.diagnostics)
            detail = "\n".join(item.message for item in diagnostics)
            if not diagnostics and not row.allowed_intents:
                detail = "Unsupported: this session has no resolution for this target. Sync currently supports one-sided file targets, not both-policy or directory convergence."
            detail = f"{row.row_id}\n{detail}" if detail else row.row_id
        self.query_one("#detail", Static).update(detail)

    def on_data_table_cell_highlighted(self, event: DataTable.CellHighlighted) -> None:
        if not self.deck.reviewing and not self.deck.confirming:
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
        # Every native cursor action shares the Approval/Review queue. Otherwise
        # batched terminal keys can approve the old row before navigation runs.
        if self.deck.confirming:
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
        self.sync_focus()
        self.deck.select()
        self.update_workset()
        if self.deck.reviewing:
            self.show_review()

    def action_approve_all(self) -> None:
        self.deck.select_all(True)
        self.update_workset()

    def action_clear_all(self) -> None:
        self.deck.select_all(False)
        self.update_workset()

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
        self.query_one("#title", Static).update(":: Proposal Review")
        log.focus()
        self.call_after_refresh(log.scroll_to, *position, animate=False)

    def action_review_or_confirm(self) -> None:
        self.sync_focus()
        if self.deck.confirming:
            self.exit(True)
        elif not self.deck.reviewing:
            self.deck.open_review()
            if self.deck.reviewing:
                self.show_review()
            self.update_workset()

    def action_confirm(self) -> None:
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
        if self.deck.reviewing:
            log = self.query_one("#review", RichLog)
            self.review_positions[self.deck.focused_row.row_id] = (log.scroll_x, log.scroll_y)
        if not self.deck.back():
            self.exit(False)
        else:
            self.show_workset()

    def action_abort(self) -> None:
        self.exit(False)


def run_command_deck(session: SyncSession, *, use_color: bool) -> bool:
    return bool(SyncDeckApp(CommandDeck(session, use_color=use_color)).run())
