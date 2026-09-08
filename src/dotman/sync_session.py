"""One-shot Sync boundary: immutable views, semantic commands and typed outcomes."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass, replace
from typing import Callable, Literal
from pathlib import Path
from uuid import uuid4

from dotman.execution import ExecutionStepResult
from dotman.models import ResolvedSyncScope
from dotman.planning import PlanningContext
from dotman.sync_base_store import SyncBaseStoreError, FilePresent, Missing
from dotman.sync_base_lifecycle import SyncBaseGitError
from dotman.operation_lock import OperationBusy, OperationLock, OperationLockError
from dotman.sync_observation import Diagnostic, Observation, observe_scope, _resolve_inputs
from dotman.sync_publication import PublicationUnit, execute_publication, prepare_publication


CommandName = Literal["set-included", "set-approval", "prepare-proposal-review", "preview", "execute", "abort"]


@dataclass(frozen=True)
class PublicationEffect:
    kind: Literal["write", "delete", "chmod"]
    path: Path
    content: bytes | None = None
    mode: int | None = None


@dataclass(frozen=True)
class Proposal:
    repository: FilePresent | Missing
    live: FilePresent | Missing
    primary_source_change: None
    publication_effects: tuple[PublicationEffect, ...]
    intent: Literal["use-repository"] = "use-repository"


def materialize(observation: Observation) -> Proposal:
    repository = observation.repository
    live = observation.comparison_repository
    if repository is None or live is None:
        raise ValueError("Proposal requires successfully frozen file endpoints")
    effects = []
    path = observation.live_path
    if live != observation.live:
        if path is None:
            raise ValueError("Publication requires a live endpoint path")
        effects.append(
            PublicationEffect("write", path, content=live.content)
            if isinstance(live, FilePresent)
            else PublicationEffect("delete", path)
        )
    if isinstance(live, FilePresent) and observation.chmod is not None:
        mode = int(observation.chmod, 8)
        if observation.live_mode != mode:
            if path is None:
                raise ValueError("Publication requires a live endpoint path")
            effects.append(PublicationEffect("chmod", path, mode=mode))
    if (
        effects and observation.live_is_symlink
        and observation.inputs.file_symlink_mode == "prompt"
        and any(effect.kind != "delete" for effect in effects)
    ):
        raise ValueError("Live symlink replacement requires explicit authorization")
    return Proposal(repository, live, None, tuple(effects))


def supports_proposal(unit: Observation) -> bool:
    return (
        unit.state == "drifted" and not unit.diagnostics
        and unit.configured_policy in ("push-only", "push-only-delete")
        and unit.effective_policy in ("push-only", "push-only-delete")
    )


@dataclass(frozen=True)
class SessionRow:
    row_id: str
    kind: Literal["drift", "diagnostic"]
    included: bool
    observation: Observation
    allowed_commands: tuple[CommandName, ...]
    approved: bool = False
    proposal: Proposal | None = None
    allowed_intents: tuple[Literal["use-repository"], ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class SessionView:
    session_id: str
    revision: int
    preview: bool
    terminal: bool
    observations: tuple[Observation, ...]
    rows: tuple[SessionRow, ...]
    allowed_commands: tuple[CommandName, ...]


@dataclass(frozen=True)
class SetIncluded:
    session_id: str
    revision: int
    row_id: str
    included: bool


@dataclass(frozen=True)
class Execute:
    session_id: str
    revision: int


@dataclass(frozen=True)
class Abort:
    session_id: str
    revision: int


@dataclass(frozen=True)
class SetApproval:
    session_id: str
    revision: int
    row_id: str
    approved: bool


@dataclass(frozen=True)
class PrepareProposalReview:
    session_id: str
    revision: int
    row_id: str


@dataclass(frozen=True)
class Preview:
    session_id: str
    revision: int


SessionCommand = SetIncluded | SetApproval | PrepareProposalReview | Preview | Execute | Abort


@dataclass(frozen=True)
class ApprovalChanged:
    row_id: str
    approved: bool


@dataclass(frozen=True)
class ProposalReview:
    row_id: str
    proposal: Proposal | None
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class InclusionChanged:
    row_id: str
    included: bool


@dataclass(frozen=True)
class SyncUnitResult:
    identity: str
    status: Literal["directly-in-sync", "pending", "excluded", "observation-failed", "converged", "would-converge", "execution-failed", "interrupted", "skipped"]
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class SyncResult:
    status: Literal["completed", "incomplete", "failed", "aborted"]
    units: tuple[SyncUnitResult, ...]
    diagnostics: tuple[Diagnostic, ...] = ()
    steps: tuple[ExecutionStepResult, ...] = ()

    @property
    def exit_code(self) -> int:
        return {"completed": 0, "incomplete": 1, "failed": 1, "aborted": 130}[
            self.status
        ]


@dataclass(frozen=True)
class CommandAccepted:
    view: SessionView
    result: InclusionChanged | ApprovalChanged | ProposalReview | SyncResult


@dataclass(frozen=True)
class CommandRejected:
    view: SessionView
    reason: Literal[
        "invalid",
        "foreign-session",
        "stale",
        "unknown-row",
        "disallowed",
        "preview",
        "terminal",
    ]


@dataclass(frozen=True)
class SessionOpenFailed:
    diagnostic: Diagnostic


@dataclass(frozen=True)
class SessionOpened:
    view: SessionView


@dataclass(frozen=True)
class SessionChanged:
    view: SessionView


@dataclass(frozen=True)
class SessionFinished:
    view: SessionView
    result: SyncResult


SessionEvent = SessionOpened | SessionChanged | SessionFinished
SessionEventSink = Callable[[SessionEvent], None]


class SyncSession:
    def __init__(
        self,
        observations: tuple[Observation, ...],
        *,
        preview: bool,
        event_sink: SessionEventSink | None = None,
    ) -> None:
        self._event_sink = event_sink
        self._operation_lock: OperationLock | None = None
        self._view = SessionView(
            uuid4().hex,
            0,
            preview,
            False,
            observations,
            tuple(
                SessionRow(
                    unit.identity.canonical,
                    "drift"
                    if unit.state == "drifted" and not unit.diagnostics
                    else "diagnostic",
                    unit.state == "drifted" and not unit.diagnostics,
                    unit,
                    ("set-included", "set-approval", "prepare-proposal-review")
                    if supports_proposal(unit)
                    else ("set-included",)
                    if unit.state == "drifted" and not unit.diagnostics
                    else (),
                    allowed_intents=("use-repository",) if supports_proposal(unit) else (),
                )
                for unit in observations
                if unit.state != "directly-in-sync" or unit.diagnostics
            ),
            ("preview", "abort") if preview else ("preview", "execute", "abort"),
        )

    @classmethod
    def open(
        cls,
        context: PlanningContext,
        scope: ResolvedSyncScope,
        *,
        preview: bool = False,
        event_sink: SessionEventSink | None = None,
    ) -> SyncSession | SessionOpenFailed:
        with ExitStack() as resources:
            lock = None
            try:
                if not preview:
                    lock = resources.enter_context(
                        OperationLock.acquire(context.tracked_state.state_root)
                    )
                resolved_inputs = _resolve_inputs(context, scope)
                observations = observe_scope(context, scope, preview=preview, resolved_inputs=resolved_inputs)

                # Retain only selected static metadata; preparation must never
                # project source content a second time.
                selected_inputs = {}
                for item, metadata in resolved_inputs[0].values():
                    key = id(item)
                    if key not in selected_inputs:
                        selected_inputs[key] = replace(item, target_metadata=[])
                    selected_inputs[key].target_metadata.append(metadata)
                publication_metadata = prepare_publication(
                    tuple(selected_inputs.values()),
                    file_symlink_mode=context.config.file_symlink_mode,
                )
            except OperationBusy as exc:
                return SessionOpenFailed(Diagnostic("operation-busy", str(exc)))
            except OperationLockError as exc:
                return SessionOpenFailed(Diagnostic("operation-lock-failed", str(exc)))
            except (SyncBaseStoreError, SyncBaseGitError) as exc:
                return SessionOpenFailed(Diagnostic("base-failed", str(exc)))
            except ValueError as exc:
                return SessionOpenFailed(Diagnostic("planning-failed", str(exc)))
            except (KeyboardInterrupt, InterruptedError):
                return SessionOpenFailed(
                    Diagnostic("interrupted", "Sync opening interrupted")
                )
            except OSError as exc:
                return SessionOpenFailed(Diagnostic("observation-failed", str(exc)))
            session = cls(observations, preview=preview, event_sink=event_sink)
            session._operation_lock = lock
            session._context = context
            session._publication_metadata = publication_metadata
            # Adapter exceptions are programming failures, not planning results.
            session._emit(SessionOpened(session.view))
            resources.pop_all()
            return session

    @property
    def view(self) -> SessionView:
        return self._view

    def _emit(self, event: SessionEvent) -> None:
        if self._event_sink is not None:
            self._event_sink(event)

    def dispatch(self, command: SessionCommand) -> CommandAccepted | CommandRejected:
        view = self.view
        if type(command) not in (SetIncluded, SetApproval, PrepareProposalReview, Preview, Execute, Abort):
            return CommandRejected(view, "invalid")
        if view.terminal:
            return CommandRejected(view, "terminal")
        if type(command.session_id) is not str or type(command.revision) is not int:
            return CommandRejected(view, "invalid")
        if command.session_id != view.session_id:
            return CommandRejected(view, "foreign-session")
        if command.revision != view.revision:
            return CommandRejected(view, "stale")
        if isinstance(command, SetIncluded):
            if type(command.row_id) is not str or type(command.included) is not bool:
                return CommandRejected(view, "invalid")
            row = next((row for row in view.rows if row.row_id == command.row_id), None)
            if row is None:
                return CommandRejected(view, "unknown-row")
            if "set-included" not in row.allowed_commands:
                return CommandRejected(view, "disallowed")
            self._view = replace(
                view,
                revision=view.revision + 1,
                rows=tuple(
                    replace(item, included=command.included)
                    if item.row_id == row.row_id
                    else item
                    for item in view.rows
                ),
            )
            result = CommandAccepted(
                self.view, InclusionChanged(row.row_id, command.included)
            )
            self._emit(SessionChanged(self.view))
            return result
        if isinstance(command, (SetApproval, PrepareProposalReview)):
            if type(command.row_id) is not str or (
                isinstance(command, SetApproval) and type(command.approved) is not bool
            ):
                return CommandRejected(view, "invalid")
            row = next((row for row in view.rows if row.row_id == command.row_id), None)
            if row is None:
                return CommandRejected(view, "unknown-row")
            name = "set-approval" if isinstance(command, SetApproval) else "prepare-proposal-review"
            if name not in row.allowed_commands:
                return CommandRejected(view, "disallowed")
            proposal, diagnostics = row.proposal, row.diagnostics
            approved = command.approved if isinstance(command, SetApproval) else row.approved
            if proposal is None and (approved or isinstance(command, PrepareProposalReview)):
                try:
                    proposal = materialize(row.observation)
                    diagnostics = ()
                except (ValueError, OSError) as exc:
                    diagnostics = (Diagnostic("materialization-failed", str(exc)),)
                    approved = False
                except (KeyboardInterrupt, InterruptedError):
                    diagnostics = (Diagnostic("interrupted", "Materialization interrupted"),)
                    approved = False
            updated = replace(row, approved=approved, proposal=proposal, diagnostics=diagnostics)
            self._view = replace(view, revision=view.revision + 1, rows=tuple(
                updated if item.row_id == row.row_id else item for item in view.rows
            ))
            self._emit(SessionChanged(self.view))
            return CommandAccepted(self.view,
                ApprovalChanged(row.row_id, approved) if isinstance(command, SetApproval)
                else ProposalReview(row.row_id, proposal, diagnostics))
        if isinstance(command, Preview):
            return CommandAccepted(view, self._result(preview=True))
        if isinstance(command, Execute) and view.preview:
            return CommandRejected(view, "preview")
        result = self._finish(aborted=isinstance(command, Abort))
        return CommandAccepted(self.view, result)

    def execute(self) -> CommandAccepted | CommandRejected:
        """Execute the current view; adapters with cached views use Execute tokens."""
        return self.dispatch(Execute(self.view.session_id, self.view.revision))

    def abort(self) -> CommandAccepted | CommandRejected:
        return self.dispatch(Abort(self.view.session_id, self.view.revision))

    def _result(self, *, preview: bool = False, aborted: bool = False) -> SyncResult:
        rows = {row.row_id: row for row in self.view.rows}
        published, operation_diagnostics, steps = (
            ({}, (), ()) if preview or aborted else self._publish()
        )
        units = []
        for observation in self.view.observations:
            identity = observation.identity.canonical
            status = observation.state
            diagnostics = observation.diagnostics
            if status == "drifted":
                row = rows[identity]
                diagnostics += row.diagnostics
                status = "excluded" if not row.included else "pending"
                if row.included and row.approved and row.proposal is not None and not aborted:
                    if preview:
                        status = "would-converge"
                    else:
                        status, failures = published[identity]
                        diagnostics += failures
            units.append(SyncUnitResult(identity, status, diagnostics))
        status = (
            "aborted" if aborted or any(d.code == "interrupted" for d in operation_diagnostics) or any(
                unit.status == "interrupted" or any(d.code == "interrupted" for d in unit.diagnostics)
                for unit in units
            )
            else "failed" if operation_diagnostics or any(unit.diagnostics or unit.status in ("observation-failed", "execution-failed") for unit in units)
            else "incomplete" if any(
                unit.status == "pending" and not supports_proposal(rows[unit.identity].observation)
                for unit in units
            )
            else "completed"
        )
        return SyncResult(status, tuple(units), operation_diagnostics, steps)

    def _publish(self) -> tuple[
        dict[str, tuple[str, tuple[Diagnostic, ...]]],
        tuple[Diagnostic, ...],
        tuple[ExecutionStepResult, ...],
    ]:
        selected = tuple(
            row for row in self.view.rows
            if row.included and row.approved and row.proposal is not None
        )
        if not selected:
            return {}, (), ()
        # No-write convergence is a real approved completion, not direct agreement.
        if all(not row.proposal.publication_effects for row in selected):
            return {row.row_id: ("converged", ()) for row in selected}, (), ()
        return self._publish_effects(selected)

    def _publish_effects(self, selected: tuple[SessionRow, ...]) -> tuple[
        dict[str, tuple[str, tuple[Diagnostic, ...]]],
        tuple[Diagnostic, ...],
        tuple[ExecutionStepResult, ...],
    ]:
        result = execute_publication(
            self._publication_metadata,
            tuple(PublicationUnit(
                row.row_id, row.observation.identity, row.proposal.publication_effects
            ) for row in selected),
            snapshot_config=self._context.config.snapshots,
            command_runtime=self._context.projection.command_runtime,
        )
        code = "interrupted" if result.interrupted else "execution-failed"
        diagnostics = () if result.error is None else (Diagnostic(code, result.error),)
        units = {
            unit.row_id: (
                "converged" if unit.status == "ok" else "skipped" if unit.status == "skipped" else code,
                () if unit.error is None else (Diagnostic(code, unit.error),),
            )
            for unit in result.units
        }
        return units, diagnostics, result.steps

    def _finish(self, *, aborted: bool) -> SyncResult:
        try:
            result = self._result(aborted=aborted)
        finally:
            if self._operation_lock is not None:
                self._operation_lock.close()
                self._operation_lock = None
        self._view = replace(
            self.view,
            revision=self.view.revision + 1,
            terminal=True,
            allowed_commands=(),
            rows=tuple(replace(row, allowed_commands=()) for row in self.view.rows),
        )
        self._emit(SessionFinished(self.view, result))
        return result

    def __enter__(self) -> SyncSession:
        return self

    def __exit__(self, *_exc) -> None:
        if not self.view.terminal:
            self.abort()
