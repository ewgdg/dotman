"""Persistent initial Command Deck over public immutable SyncSession views."""

from __future__ import annotations

import asyncio
from contextvars import copy_context
from contextlib import ExitStack
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from difflib import unified_diff
from itertools import groupby
import signal
import time

from rich.console import Group
from rich.padding import Padding
from rich.rule import Rule
from rich.spinner import Spinner
from rich.table import Table

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult, SuspendNotSupported
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.errors import NoWidget
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import DataTable, OptionList, Static

from dotman.diff_review import display_review_path
from dotman.ui_context import current_ui_config
from dotman.cli_style import MENU_HEADER_MARKER, MENU_HEADER_MARKER_STYLE, render_annotation_parentheses, render_conflict_lines, render_diff_line, render_info_section_header, render_key_hints, render_payload_action, render_payload_section_label, render_sync_term, render_package_label, style_text
from dotman.sync_base_store import DirectoryChildPresent, FilePresent, Missing
from dotman.sync_deck_command import selection_uses_inclusion, auxiliary_resolution, additional_label, guard_skip_explanation, guard_skip_label, set_all_selected, set_selected, row_diagnostics, auxiliary_label, review, edit_proposal, set_resolution_intent, retry_materialization, effect_summary, primary_change_summary, resolution_label, summary_stats
from dotman.sync_session import AuthorizeSymlinkReplacement, AdditionalRow, AuxiliaryRow, CommandRejected, SessionRow, SyncSession, conflict_diagnostic


@dataclass(frozen=True)
class ReviewFact:
    label: str
    value: str


@dataclass(frozen=True)
class ReviewNote:
    text: str


@dataclass(frozen=True)
class ReviewDiff:
    lines: tuple[str, ...]


@dataclass(frozen=True)
class ReviewConflict:
    lines: tuple[str, ...]


@dataclass(frozen=True)
class ReviewSection:
    title: str
    items: tuple[ReviewFact | ReviewNote | ReviewDiff | ReviewConflict, ...]


REVIEW_ITEM_INDENT = 4


@dataclass(frozen=True)
class ReviewDocument:
    """One review, laid out as styled wrapping renderables or as copyable text.

    On screen the Deck title already names the review, so the body opens with
    the subject alone; copied text keeps the full heading for context.
    """

    heading: str
    subject: str
    sections: tuple[ReviewSection, ...]
    use_color: bool

    def text(self) -> str:
        indent = " " * REVIEW_ITEM_INDENT
        lines = [f"{self.heading} — {self.subject}"]
        for section in self.sections:
            lines += ["", render_info_section_header(section.title, use_color=self.use_color)]
            for item in section.items:
                if isinstance(item, ReviewFact):
                    lines.append(f"{indent}{render_payload_section_label(item.label + ':', use_color=self.use_color)} {item.value}")
                elif isinstance(item, ReviewNote):
                    lines.append(f"{indent}{item.text}")
                elif isinstance(item, ReviewConflict):
                    lines.extend(indent + line for line in render_conflict_lines(item.lines, use_color=self.use_color))
                else:
                    lines.extend(indent + "".join(render_diff_line(line, use_color=self.use_color)) for line in item.lines)
        return "\n".join(lines)

    def renderable(self) -> Group:
        parts: list = [Text.from_ansi(self.subject, overflow="fold")]
        for section in self.sections:
            parts += [Text(), self._section_rule(section.title)]
            # Consecutive facts share one grid so their values align.
            for kind, items in groupby(section.items, type):
                items = list(items)
                if kind is ReviewFact:
                    body = fact_grid([(render_payload_section_label(item.label + ":", use_color=self.use_color), item.value)
                                      for item in items])
                elif kind is ReviewNote:
                    body = Group(*(Text.from_ansi(item.text, overflow="fold") for item in items))
                elif kind is ReviewConflict:
                    body = Group(*(Text.from_ansi(line, overflow="fold") for item in items
                                   for line in render_conflict_lines(item.lines, use_color=self.use_color)))
                else:
                    body = Group(*(self._diff_grid(item.lines) for item in items))
                parts.append(Padding(body, (0, 0, 0, REVIEW_ITEM_INDENT)))
        return Group(*parts)

    def _section_rule(self, title: str) -> Rule:
        # On screen a full-width rule separates sections without costing an extra row.
        lead = style_text("──", *MENU_HEADER_MARKER_STYLE) if self.use_color else "──"
        heading = style_text(title, "1") if self.use_color else title
        return Rule(Text.from_ansi(f"{lead} {heading}"), align="left", style="dim" if self.use_color else "")

    def _diff_grid(self, lines: tuple[str, ...]) -> Table:
        # A marker column keeps wrapped content hanging right of the +/- sign.
        grid = Table.grid()
        grid.add_column(width=1, no_wrap=True)
        grid.add_column(ratio=1, overflow="fold")
        for line in lines:
            marker, content = render_diff_line(line, use_color=self.use_color)
            grid.add_row(Text.from_ansi(marker), Text.from_ansi(content))
        return grid


CONFLICT_CONTEXT_LINES = 3
CONFLICT_ELISION = "⋯"


def conflict_excerpt(content: bytes, *, description: str) -> ReviewConflict | ReviewNote:
    """zdiff3 conflict blocks with surrounding context, like unified-diff hunks."""
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return ReviewNote(f"Binary {description}: {len(content)} bytes")
    in_block, block_lines = False, []
    for index, line in enumerate(lines):
        if line.startswith("<<<<<<<"):
            in_block = True
        if in_block:
            block_lines.append(index)
        if line.startswith(">>>>>>>"):
            in_block = False
    kept = sorted({index for block_index in block_lines
                   for index in range(block_index - CONFLICT_CONTEXT_LINES, block_index + CONFLICT_CONTEXT_LINES + 1)
                   if 0 <= index < len(lines)})
    excerpt = []
    for previous, index in zip([-1, *kept], kept):
        if index - previous > 1:
            excerpt.append(CONFLICT_ELISION)
        excerpt.append(lines[index])
    if kept and kept[-1] < len(lines) - 1:
        excerpt.append(CONFLICT_ELISION)
    return ReviewConflict(tuple(excerpt))


def _review_difference(
    before: FilePresent | DirectoryChildPresent | Missing | None,
    after: FilePresent | DirectoryChildPresent | Missing | None,
    *,
    before_label: str,
    after_label: str,
    description: str,
    use_color: bool,
) -> list[ReviewNote | ReviewDiff]:
    if before is None or after is None:
        return [ReviewNote("Comparison evidence unavailable")]
    present_types = (FilePresent, DirectoryChildPresent)
    before_bytes = before.content if isinstance(before, present_types) else b""
    after_bytes = after.content if isinstance(after, present_types) else b""
    mode_changed = (
        isinstance(before, DirectoryChildPresent)
        and isinstance(after, DirectoryChildPresent)
        and before.executable != after.executable
    )
    # Missing compares as empty bytes, so existence changes need their own Git-style
    # header; otherwise creating or deleting an empty file reads as no difference.
    existence_change = (
        ["new file"] if isinstance(before, Missing) and not isinstance(after, Missing)
        else ["deleted file"] if isinstance(after, Missing) and not isinstance(before, Missing)
        else []
    )
    if before_bytes == after_bytes and not mode_changed and not existence_change:
        return [ReviewNote(render_payload_section_label("No content difference", use_color=use_color))]

    lines: list[str] = existence_change
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
            binary = ReviewNote(f"Binary {description}: {len(before_bytes)} → {len(after_bytes)} bytes")
            return [ReviewDiff(tuple(lines)), binary] if lines else [binary]
    return [ReviewDiff(tuple(lines))]




NOOP_NOTICE = "Nothing to do: Approval would neither write nor record a Sync Base"


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
        # Selecting may materialize the Proposal and only then reveal it as a no-op.
        focused = self.focused_row
        if isinstance(focused, SessionRow) and focused.proposal is not None and focused.proposal.noop:
            self.notice = NOOP_NOTICE
        else:
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
        return f":: {verb}? — {stats}"

    def review_text(self) -> str:
        document = self.review_document()
        return document.text() if document else ""

    def review_heading(self, title: str) -> str:
        if not self.use_color:
            return f"{MENU_HEADER_MARKER} {title}"
        return f"{style_text(MENU_HEADER_MARKER, *MENU_HEADER_MARKER_STYLE)} {style_text(title, '1')}"

    def review_document(self) -> ReviewDocument | None:
        row = self.focused_row
        if row is None or isinstance(row, AuxiliaryRow):
            return None
        color = self.use_color
        term = lambda value: render_sync_term(value, use_color=color)
        difference = lambda *args, **kwargs: _review_difference(*args, **kwargs, use_color=color)
        approval = ReviewFact("Approval", term(
            "not needed" if isinstance(row, SessionRow) and row.proposal is not None and row.proposal.noop
            else "approved" if row.approved else "unapproved"))
        if isinstance(row, AdditionalRow):
            return ReviewDocument(
                heading=self.review_heading("Additional Source Review"),
                subject=additional_label(row, use_color=color),
                sections=(
                    ReviewSection("Decision", (approval, ReviewFact("References", ", ".join(row.references)))),
                    ReviewSection("Source Change", tuple(difference(
                        FilePresent(row.change.before), FilePresent(row.change.candidate),
                        before_label="Additional Source",
                        after_label="candidate Additional Source",
                        description="Additional Source",
                    ))),
                ),
                use_color=color,
            )
        proposal = row.proposal
        intent = row.intent
        observation = row.observation
        base = observation.base
        pull = self.session.view.operation == "pull" or intent in ("use-live", "merge")
        capture_required = pull and not (proposal and proposal.intent == "editor")
        ui = current_ui_config()
        display_path = lambda path: display_review_path(path, compact=not (ui and ui.full_paths))
        primary = primary_change_summary(proposal, observation.repository_path)
        presence = lambda state: "missing" if isinstance(state, Missing) else "present"

        decision = [
            approval,
            ReviewFact("Resolution", term(row_resolution(row)) if intent or self.session.view.operation == "pull" else term("blocked")),
            ReviewFact("Policy", observation.effective_policy),
        ]
        if observation.configured_policy != observation.effective_policy:
            decision.append(ReviewFact("Configured policy", observation.configured_policy))
        if "authorize-symlink-replacement" in row.allowed_commands:
            link = "Link replacement authorized" if row.symlink_authorized else "Link replacement requires authorization"
            decision.append(ReviewNote(f"{term(link)} (L)"))
        if row.fallback_reason:
            decision.append(ReviewNote(f"{term('Fallback')}: {row.fallback_reason}"))
        decision.extend(ReviewNote(f"{term(item.severity)}: {item.message}") for item in row_diagnostics(row))
        sections = [
            ReviewSection("Decision", tuple(decision)),
            ReviewSection("Paths", (
                ReviewFact("Repository path", display_path(observation.repository_path)),
                ReviewFact("Live path", display_path(observation.live_path)),
            )),
        ]

        primary_value = "none"
        if primary:
            annotation = render_annotation_parentheses("authorized by Proposal Approval", use_color=color)
            primary_value = f"{primary['kind']} · {display_path(primary['path'])}{annotation}"
        conflicted = conflict_diagnostic(row)
        captured = proposal.capture if proposal else conflicted.capture if conflicted else None
        capture = (presence(captured) if captured is not None
                   else term("pending") if capture_required else "not required")
        state = [
            ReviewFact("Observation", term(observation.state)),
            ReviewFact("Sync Base", term(base.status)),
        ]
        if base.reason:
            state.append(ReviewFact("Base reason", base.reason))
        if base.record:
            state += [ReviewFact("Base fingerprint", base.record.envelope.fingerprint),
                      ReviewFact("Base payload", presence(base.record.payload))]
        state += [
            ReviewFact("Primary Source Change", primary_value),
            ReviewFact("Capture", capture),
            ReviewFact("Reconciliation", proposal.reconciliation if proposal
                       else term("conflict") if conflicted else term("pending")),
        ]
        if proposal and observation.configured_policy in ("pull-only", "both"):
            state.append(ReviewFact("Checkpoint qualified", "yes" if proposal.checkpoint_qualified else "no"))
        sections.append(ReviewSection("State", tuple(state)))
        conflict = conflicted.conflict if conflicted else None
        if isinstance(conflict, (FilePresent, DirectoryChildPresent)):
            sections.append(ReviewSection("Merge conflicts", (
                ReviewNote(render_payload_section_label("Resolve in the Editor (E); it opens with this merge output",
                                                        use_color=color)),
                conflict_excerpt(conflict.content, description="merge output"),
            )))

        if proposal is not None:
            effects = []
            for effect in proposal.publication_effects:
                summary = effect_summary(effect)
                detail = f"{render_payload_action(effect.kind, use_color=color)} {display_path(summary['path'])}"
                if "bytes" in summary:
                    detail += render_annotation_parentheses(f"{summary['bytes']} bytes", use_color=color)
                if "mode" in summary:
                    detail += f" → {summary['mode']}"
                effects.append(ReviewNote(detail))
            if not effects:
                effects.append(ReviewNote(render_payload_section_label("none", use_color=color)))
            sections.append(ReviewSection("Publication Effects", tuple(effects)))

            repository_effect = pull or observation.effective_policy == "both" or proposal.intent == "editor"
            effect_preview = lambda side, before, after, notes=(): ReviewSection(
                f"{side.capitalize()} effect preview", (*notes, *difference(
                    before, after, before_label=side,
                    after_label=f"{side} outcome", description=f"{side} outcome",
                )))
            pull_only = observation.effective_policy == "pull-only"
            notes = (ReviewNote("Live remains unchanged"),) if pull_only else ()
            if repository_effect:
                sections.append(effect_preview("repository", observation.repository, proposal.repository, notes))
            if not pull_only:
                sections.append(effect_preview("live", observation.live, proposal.live))
        # Drift explains a drifted row only when no outcome preview shows a change,
        # e.g. Capture reproduces the repository while the compared copies differ.
        writes_nothing = proposal is None or (
            proposal.primary_source_change is None and not proposal.publication_effects)
        if (observation.effective_policy in ("both", "pull-only")
                and observation.state == "drifted" and writes_nothing):
            drift = [
                ReviewNote(NOOP_NOTICE if proposal.noop else "Nothing will be written; Approval records the Sync Base"),
                # No write means Capture already reproduces the repository, so only the
                # compare projection sees drift; that is a configuration mismatch.
                ReviewNote("compare does not match Capture, so this unit keeps appearing; align them to stop it"),
            ] if proposal else []
            drift += [ReviewFact("Repository comparison", observation.compare_repo),
                      ReviewFact("Live comparison", observation.compare_live)]
            drift.extend(difference(
                observation.comparison_repository, observation.comparison_live,
                before_label="compared repository",
                after_label="compared live", description="compared copies",
            ))
            sections.append(ReviewSection("Drift", tuple(drift)))
        additional = [item for item in self.session.view.rows
                      if isinstance(item, AdditionalRow) and row.row_id in item.references]
        if additional:
            sections.append(ReviewSection(
                "Additional Source Changes (independent Approval and Review)",
                tuple(ReviewNote(additional_label(item, use_color=color)) for item in additional),
            ))
        return ReviewDocument(
            heading=self.review_heading("Proposal Review"),
            subject=unit_label(row, use_color=color),
            sections=tuple(sections), use_color=color,
        )


def auxiliary_row_label(row: AuxiliaryRow, *, use_color: bool) -> str:
    if row.guard_skip is not None:
        return guard_skip_label(row.scope, row.directions[0], row.guard_skip.path_rule_pattern, use_color=use_color)
    return auxiliary_label(row.scope, row.kind, row.directions, use_color=use_color)


def unit_label(row, *, use_color: bool) -> str:
    identity = row.observation.identity
    label = render_package_label(
        repo_name=identity.repo, package_id=identity.package_id,
        target_name=identity.target_name, bound_profile=identity.bound_profile,
        use_color=use_color,
    )
    return label if identity.child_path is None else f"{label}/{identity.child_path}"


def unit_detail_facts(observation) -> list[tuple[str, str]]:
    """Surface what the workset columns cannot fit; labels match Proposal Review."""
    state = f"{observation.state} · Sync Base: {observation.base.status}"
    if observation.configured_policy != observation.effective_policy:
        state += f" · Configured policy: {observation.configured_policy}"
    # Full paths: the ring wraps them, so compaction would only hide identity.
    paths = [(label, display_review_path(path, compact=False))
             for label, path in (("Live path", observation.live_path), ("Repository path", observation.repository_path))
             if path is not None]
    return [*paths, ("Observation", state)]


def fact_grid(facts: list[tuple[str, str]]) -> Table:
    """Label/value grid: wrapped values keep a hanging indent under their column."""
    grid = Table.grid(padding=(0, 1))
    grid.add_column(no_wrap=True)
    # Paths have no spaces to break at; fold them at the column edge.
    grid.add_column(ratio=1, overflow="fold")
    for label, value in facts:
        grid.add_row(Text.from_ansi(label), Text.from_ansi(value))
    return grid


def detail_renderable(identity: str, facts: list[tuple[str, str]]) -> Group:
    return Group(Text.from_ansi(identity, overflow="fold"),
                 Padding(fact_grid([(f"{label}:", value) for label, value in facts]), (0, 0, 0, 2)))


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
    if row.proposal is not None and row.proposal.noop:
        return "No-op"
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


class ReviewBody(Widget):
    """Review text laid out at the current width, then shown as plain styled lines.

    Textual selects individual characters only in text content; a Rich table
    (which provides the hanging indents) would select only as a whole block.
    """

    DEFAULT_CSS = "ReviewBody { height: auto; }"
    document: reactive[ReviewDocument | None] = reactive(None, layout=True)

    def render(self) -> Text:
        if self.document is None:
            return Text()
        console = self.app.console
        options = console.options.update_width(max(self.content_size.width, 1))
        lines = []
        for segments in console.render_lines(self.document.renderable(), options, pad=False):
            line = Text.assemble(*((segment.text, segment.style) for segment in segments))
            # Strip grid padding so copied text has no trailing blanks.
            line.rstrip()
            lines.append(line)
        return Text("\n").join(lines)


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
# Copy confirmation is transient; other notices report state and stay until the next command.
COPY_NOTICE_SECONDS = 2.0


class SyncDeckApp(App[bool]):
    """Terminal adapter: the public session remains the sole mutation authority."""

    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen { background: $surface; }
    /* Textual's 2-cell default steals width from the dense deck; match the 1-cell horizontal bar. */
    * { scrollbar-size-vertical: 1; }
    #title { height: auto; padding: 0 1; text-style: bold; color: $accent; }
    #workset { height: 1fr; }
    /* No side margin: a margined sibling narrows the workset table in Textual's vertical layout. */
    #detail { height: auto; max-height: 35%; border: round $foreground 30%; padding: 0 1; overflow-y: auto; }
    #detail:focus { border: round $accent; }
    #resolution { height: auto; max-height: 5; border: round $accent; margin: 0 1; }
    #review { height: 1fr; overflow-x: hidden; }
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
        Binding("tab,shift+tab", "toggle_detail_focus", "Detail", priority=True),
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
        self._detail_row_id: str | None = None
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
        yield WorksetTable(id="workset", cursor_type="row", zebra_stripes=True)
        # A scroll container: a bare Static cannot scroll, so overflowing details would be unreachable.
        with VerticalScroll(id="detail"):
            yield Static(id="detail-body", markup=False)
        yield OptionList(id="resolution")
        # Review wraps instead of scrolling sideways; a Static re-wraps on resize, unlike a RichLog.
        with VerticalScroll(id="review"):
            yield ReviewBody(id="review-body")
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
            selectable = row.approvable if isinstance(row, SessionRow) else bool(
                {"set-included", "set-approval"}.intersection(row.allowed_commands))
            marker = "[x]" if selected else "[ ]" if selectable else "[-]"
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
        self.update_hints()

    @property
    def detail_focused(self) -> bool:
        return self.focused is self.query_one("#detail")

    def update_hints(self) -> None:
        review_scroll = ("↑/↓/j/k/PgUp/PgDn", "scroll")
        bulk_selection = ("A/U", "all/none")
        if self.query_one(OptionList).display:
            hints = [("↑/↓/j/k", "choose Resolution"), ("Enter", "select"), ("Esc", "dismiss")]
        elif self.deck.confirming:
            hints = [("Enter", "confirm"), ("Esc", "return"), ("Ctrl+C", "abort")]
        elif self.deck.reviewing and isinstance(self.deck.focused_row, AdditionalRow):
            hints = [("Esc", "return"), ("Space", "Approval"), ("Y", "copy"), review_scroll, ("Ctrl+C", "abort")]
        elif self.deck.reviewing:
            hints = [("Esc", "return"), ("Space", "Approval"), ("E", "edit"), ("T", "retry"), ("Y", "copy"),
                     review_scroll, ("Ctrl+C", "abort")]
        elif self.detail_focused:
            hints = [("Tab/Esc", "return"), review_scroll, ("Space", "mark"), bulk_selection,
                     ("Enter", "view"), ("E", "edit"), ("T", "retry"), ("Y", "copy")]
        else:
            hints = [("Esc", "abort"), ("X", "confirm"), ("Space", "mark"), bulk_selection, ("Enter", "view"),
                     ("E", "edit"), ("T", "retry"), ("Y", "copy"), ("Tab", "detail")]
        row = self.deck.focused_row
        if row and "authorize-symlink-replacement" in row.allowed_commands and not self.deck.confirming:
            hints.append(("Shift+L", "authorize link replacement"))
        if self.deck.session.view.operation == "sync" and not self.deck.reviewing and not self.query_one(OptionList).display and not self.deck.confirming:
            hints.append(("R", "intent"))
        self.query_one("#help", Static).update(Text.from_ansi(render_key_hints(hints, use_color=self.deck.use_color)))

    def update_detail(self) -> None:
        row = self.deck.focused_row
        use_color = self.deck.use_color
        detail, body = self.query_one("#detail"), self.query_one("#detail-body", Static)
        if row is None:
            body.update(Text.from_ansi(render_payload_section_label("No drifted work.", use_color=use_color)))
            return
        facts = [(render_sync_term(item.severity, use_color=use_color), item.message) for item in row_diagnostics(row)]
        if isinstance(row, AdditionalRow):
            identity = additional_label(row, use_color=use_color)
        elif isinstance(row, AuxiliaryRow):
            identity = auxiliary_row_label(row, use_color=use_color)
            if row.guard_skip is not None:
                facts.append((render_sync_term('Guard skipped', use_color=use_color), guard_skip_explanation(row)))
        else:
            identity = unit_label(row, use_color=use_color)
            if row.fallback_reason:
                facts.append((render_sync_term('Fallback', use_color=use_color), row.fallback_reason))
            facts += unit_detail_facts(row.observation)
        if row.row_id != self._detail_row_id:
            # A newly focused row starts from its identity, not the old scroll offset.
            self._detail_row_id = row.row_id
            detail.scroll_home(animate=False)
        body.update(detail_renderable(identity, facts))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
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
            await self.query_one("#review").run_action(review_action)
        elif self.detail_focused:
            await self.query_one("#detail").run_action(review_action)
        else:
            table = self.query_one(WorksetTable)
            if table.row_count:
                await table.run_action(table_action)
            self.sync_focus()

    def sync_focus(self) -> None:
        # A following key may arrive before RowHighlighted is delivered (paste/PTY).
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
        log = self.query_one("#review", VerticalScroll)
        position = self.review_positions.get(self.deck.focused_row.row_id, (0, 0))
        if log.display:
            position = (log.scroll_x, log.scroll_y)
        self.query_one("#workset").display = False
        self.query_one("#detail").display = False
        log.display = True
        self.query_one(ReviewBody).document = self.deck.review_document()
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
        if self.detail_focused:
            self.query_one(WorksetTable).focus()
            return
        if self.query_one(OptionList).display:
            self.close_resolution()
            self.update_workset()
            return
        if self.deck.reviewing:
            log = self.query_one("#review", VerticalScroll)
            self.review_positions[self.deck.focused_row.row_id] = (log.scroll_x, log.scroll_y)
        if not self.deck.back():
            self.exit(False)
        else:
            self.show_workset()

    def action_toggle_detail_focus(self) -> None:
        if self.busy or self.deck.reviewing or self.deck.confirming or self.query_one(OptionList).display:
            return
        (self.query_one(WorksetTable) if self.detail_focused else self.query_one("#detail")).focus()

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        # Keyboard toggles and mouse clicks both move focus; hints follow either.
        self.update_hints()

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
        """Copy the full Target identity, or the review selection or whole review, via the terminal clipboard (OSC 52)."""
        if self.busy or self.deck.confirming or self.query_one(OptionList).display:
            return
        self.sync_focus()
        row = self.deck.focused_row
        if row is None:
            return
        if self.deck.reviewing and (selected := self.screen.get_selected_text()):
            # Ctrl+C is Abort, so Y also copies a mouse selection.
            text, subject = selected, "selection"
            self.screen.clear_selection()
        elif self.deck.reviewing:
            text, subject = Text.from_ansi(self.deck.review_text()).plain, "review"
        else:
            # The Target cell may be elided; copy the untruncated identity.
            text, subject = self.query_one(WorksetTable).full_targets[row.row_id].plain, "Target"
        self.copy_to_clipboard(text)
        notice = self.deck.notice = f"Copied {subject} to clipboard."
        self.update_workset()
        self.set_timer(COPY_NOTICE_SECONDS, lambda: self.dismiss_notice(notice))

    def dismiss_notice(self, notice: str) -> None:
        # A later command may have replaced the notice; leave that one alone.
        if self.deck.notice == notice:
            self.deck.notice = ""
            self.query_one("#notice", Static).update("")

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
