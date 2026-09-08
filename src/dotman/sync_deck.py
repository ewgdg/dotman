"""Persistent initial Command Deck over public immutable SyncSession views."""

from __future__ import annotations

from difflib import unified_diff

from prompt_toolkit.application import Application
from prompt_toolkit.data_structures import Point
from prompt_toolkit.formatted_text import ANSI, to_formatted_text
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, ScrollOffsets, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.mouse_events import MouseEventType

from dotman.cli_style import render_sync_term, render_package_label
from dotman.sync_base_store import FilePresent, Missing
from dotman.sync_deck_command import approve, review, effect_summary
from dotman.sync_session import CommandRejected, SyncSession


class CommandDeck:
    def __init__(self, session: SyncSession, *, use_color: bool) -> None:
        self.session = session
        self.use_color = use_color
        self.focus = 0
        self.reviewing = False
        self.confirming = False
        self.notice = ""
        self._review_scroll: dict[str, int] = {}

    @property
    def focused_row(self):
        rows = self.session.view.rows
        return rows[self.focus] if rows else None

    @property
    def review_scroll(self) -> int:
        row = self.focused_row
        return self._review_scroll.get(row.row_id, 0) if row else 0

    def move(self, offset: int) -> None:
        if self.confirming:
            return
        if self.reviewing:
            row = self.focused_row
            if row:
                self._review_scroll[row.row_id] = max(
                    0, min(self.review_scroll + offset, len(self.review_text().splitlines()) - 1)
                )
            return
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

    def text(self) -> str:
        if self.confirming:
            selected = [row for row in self.session.view.rows if row.approved]
            effects = [effect for row in selected if row.proposal
                       for effect in row.proposal.publication_effects]
            writes = sum(effect.kind == "write" for effect in effects)
            deletions = sum(effect.kind == "delete" for effect in effects)
            modes = sum(effect.kind == "chmod" for effect in effects)
            repository_changes = sum(row.proposal.primary_source_change is not None for row in selected if row.proposal)
            verb = "Preview" if self.session.view.preview else "Execute"
            return f":: {verb} {len(selected)} approved units / {repository_changes} repository changes / {writes} live writes / {deletions} live deletions / {modes} mode changes?\n\n  Enter confirm  Esc return"
        if self.reviewing:
            return self.review_text()
        lines = [":: Sync Command Deck", "", "  Selection    Target    Policy    Resolution"]
        for index, row in enumerate(self.session.view.rows):
            identity = row.observation.identity
            label = render_package_label(
                repo_name=identity.repo, package_id=identity.package_id,
                target_name=identity.target_name, bound_profile=identity.bound_profile,
                use_color=self.use_color,
            )
            marker = "[x]" if row.approved else "[ ]" if "set-approval" in row.allowed_commands else "[-]"
            resolution = render_sync_term("Use repository", use_color=self.use_color) if "set-approval" in row.allowed_commands else render_sync_term("blocked", use_color=self.use_color)
            lines.append(f"{'>' if index == self.focus else ' '} {marker}  {label}  {row.observation.effective_policy}  {resolution}")
            lines.extend(f"       {item.message}" for item in (*row.observation.diagnostics, *row.diagnostics))
        if not self.session.view.rows:
            lines.append("  No drifted work.")
        lines += ["", "  ↑/↓ focus  Space Selection  A select all  U unselect all",
                  "  Enter review  X preview/execute  Esc abort", self.notice]
        return "\n".join(lines)

    def review_text(self) -> str:
        row = self.focused_row
        if row is None:
            return ""
        proposal = row.proposal
        lines = [f":: Proposal Review — {row.row_id}",
                 f"  Selection: {'approved' if row.approved else 'unapproved'}",
                 f"  Observation: {row.observation.state}",
                 f"  Policy: {row.observation.effective_policy}",
                 f"  Repository path: {row.observation.repository_path}",
                 f"  Live path: {row.observation.live_path}",
                 "  Resolution: Use repository",
                 f"  Sync Base: {row.observation.base.status}",
                 "  Primary Source Change: none",
                 "  Capture: not required",
                 "  Reconciliation: frozen repository outcome"]
        lines.extend(f"  {item.message}" for item in (*row.observation.diagnostics, *row.diagnostics))
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
            before = row.observation.live
            after = proposal.live
            before_bytes = before.content if isinstance(before, FilePresent) else b""
            after_bytes = after.content if isinstance(after, FilePresent) else b""
            if before_bytes != after_bytes:
                try:
                    diff = unified_diff(
                        before_bytes.decode("utf-8").splitlines(keepends=True),
                        after_bytes.decode("utf-8").splitlines(keepends=True),
                        fromfile="/dev/null" if isinstance(before, Missing) else "frozen live",
                        tofile="/dev/null" if isinstance(after, Missing) else "approved live outcome",
                        lineterm="\n",
                    )
                    for line in diff:
                        lines.append(line.rstrip("\n"))
                        # splitlines without endings hides newline-only changes.
                        if not line.endswith("\n"):
                            lines.append("\\ No newline at end of file")
                except UnicodeDecodeError:
                    lines.append(f"  Binary live outcome: {len(before_bytes)} → {len(after_bytes)} bytes")
            lines.append(f"  Frozen live: {'missing' if isinstance(before, Missing) else 'present'}")
            lines.append(f"  Live outcome: {'missing' if isinstance(after, Missing) else 'present'}")
        lines += ["", "  ↑/↓ scroll  Space Selection  Esc return to workset"]
        return "\n".join(lines)


def command_deck_application(deck: CommandDeck) -> Application[bool]:
    keys = KeyBindings()

    @keys.add("up")
    def up(event):
        deck.move(-1)

    @keys.add("down")
    def down(event):
        deck.move(1)

    @keys.add(" ")
    def toggle(event):
        deck.select()

    @keys.add("a")
    @keys.add("A")
    def select_all(event):
        deck.select_all(True)

    @keys.add("u")
    @keys.add("U")
    def unselect_all(event):
        deck.select_all(False)

    @keys.add("enter")
    def enter(event):
        if deck.confirming:
            event.app.exit(result=True)
        elif not deck.reviewing:
            deck.open_review()

    @keys.add("x")
    @keys.add("X")
    def execute(event):
        deck.confirm()

    @keys.add("escape")
    def back(event):
        if not deck.back():
            event.app.exit(result=False)

    @keys.add("c-c")
    def abort(event):
        event.app.exit(result=False)

    def fragments():
        if deck.reviewing or deck.confirming:
            return to_formatted_text(ANSI(deck.text()))
        # Each row carries its own mouse action; path cells remain identities.
        result = []
        row_index = 0
        for line in deck.text().splitlines(keepends=True):
            is_row = line.startswith(("> [", "  ["))
            if is_row:
                index = row_index
                row_index += 1

                def click(mouse_event, index=index):
                    if mouse_event.event_type == MouseEventType.MOUSE_UP:
                        deck.click(index, selection=mouse_event.position.x <= 5)
                result.extend((style, text, click) for style, text in to_formatted_text(ANSI(line)))
            else:
                result.extend(to_formatted_text(ANSI(line)))
        return result

    control = FormattedTextControl(
        fragments, focusable=True,
        get_cursor_position=lambda: Point(0, deck.review_scroll if deck.reviewing else 0 if deck.confirming else deck.focus + 3),
    )
    return Application(
        layout=Layout(Window(
            control, wrap_lines=True, scroll_offsets=ScrollOffsets(),
            get_vertical_scroll=lambda window: deck.review_scroll if deck.reviewing else window.vertical_scroll,
        )),
        key_bindings=keys, full_screen=True, mouse_support=True,
    )


def run_command_deck(session: SyncSession, *, use_color: bool) -> bool:
    return command_deck_application(CommandDeck(session, use_color=use_color)).run()
