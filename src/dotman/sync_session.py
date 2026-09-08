"""One-shot Sync boundary: immutable views, semantic commands and typed outcomes."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass, replace
from typing import Callable, Literal
from pathlib import Path
from uuid import uuid4

from dotman.execution import ExecutionStep
from dotman.capture import CaptureError
from dotman.sync_capture import capture_observation
from dotman.sync_reconciliation import reconcile, ReconciliationConflict, ReconciliationFailed
from dotman.projection import project_frozen_file
from dotman.models import ResolvedSyncScope, package_ref_text, repo_qualified_target_text
from dotman.planning import PlanningContext
from dotman.sync_base_store import SyncBaseStore, SyncBaseStoreError, FilePresent, Missing
from dotman.sync_base_lifecycle import (
    FrozenBaseUnit, ProposalCompletion, SyncBaseGit, SyncBaseGitError, SyncBaseLifecycle,
)
from dotman.operation_lock import OperationBusy, OperationLock, OperationLockError
from dotman.sync_observation import Diagnostic, Observation, observe_scope, _resolve_inputs, _base_unit
from dotman.sync_publication import PublicationResult, PublicationUnit, execute_publication, prepare_publication
from dotman.sync_repository_apply import (
    RepositoryApplyUnit, execute_repository_apply, prepare_repository_apply,
)


ResolutionIntent = Literal["use-repository", "use-live", "merge"]


CommandName = Literal["set-resolution-intent", "retry-materialization", "set-included", "set-approval", "prepare-proposal-review", "preview", "execute", "abort"]


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
    primary_source_change: FilePresent | Missing | None
    publication_effects: tuple[PublicationEffect, ...]
    intent: ResolutionIntent = "use-repository"
    capture: FilePresent | Missing | None = None
    reconciliation: str | None = None


def materialize(
    observation: Observation,
    *,
    capture: Callable[[Observation], FilePresent | Missing] | None = None,
    intent: ResolutionIntent | None = None,
    render: Callable[[Observation, FilePresent | Missing], FilePresent | Missing] | None = None,
    merge: Callable[[Observation, FilePresent | Missing], FilePresent | Missing] | None = None,
) -> Proposal:
    intent = intent or ("use-live" if observation.effective_policy == "pull-only" else "use-repository")
    repository = observation.repository
    captured = None
    if repository is None or observation.live is None:
        raise ValueError("Proposal requires successfully frozen file endpoints")
    if intent in ("use-live", "merge"):
        if capture is None:
            raise ValueError("Resolution requires a frozen Capture provider")
        captured = capture(observation)
        if intent == "merge":
            if merge is None:
                raise ValueError("Merge requires usable frozen ancestry")
            repository = merge(observation, captured)
        else:
            repository = captured
    primary = repository if repository != observation.repository else None
    if observation.effective_policy == "pull-only":
        return Proposal(repository, observation.live, primary, (), intent, captured,
                        "captured repository outcome")
    if observation.effective_policy == "both":
        if render is None:
            raise ValueError("Both-policy publication requires frozen outcome projection")
        live = render(observation, repository)
    else:
        live = observation.comparison_repository
    if live is None:
        raise ValueError("Proposal requires successfully frozen publication outcome")
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
    return Proposal(repository, live, primary, tuple(effects), intent, captured,
                    "three-way merged repository outcome" if intent == "merge" else
                    "captured repository outcome" if intent == "use-live" else "frozen repository outcome")


def supports_proposal(unit: Observation) -> bool:
    return (
        unit.state == "drifted" and not unit.diagnostics
        and unit.configured_policy in ("push-only", "push-only-delete", "pull-only", "both")
        and unit.effective_policy in ("push-only", "push-only-delete", "pull-only", "both")
    )


def allowed_intents(unit: Observation) -> tuple[ResolutionIntent, ...]:
    if not supports_proposal(unit):
        return ()
    if unit.effective_policy == "both":
        return ("use-repository", "use-live", "merge") if unit.base.status == "usable" else ("use-repository", "use-live")
    return ("use-live",) if unit.effective_policy == "pull-only" else ("use-repository",)


def default_intent(unit: Observation) -> ResolutionIntent | None:
    if not supports_proposal(unit):
        return None
    if unit.effective_policy == "both":
        return "merge" if unit.base.status == "usable" else "use-live"
    return allowed_intents(unit)[0]


@dataclass(frozen=True)
class SessionRow:
    row_id: str
    kind: Literal["drift", "diagnostic"]
    included: bool
    observation: Observation
    allowed_commands: tuple[CommandName, ...]
    approved: bool = False
    proposal: Proposal | None = None
    allowed_intents: tuple[ResolutionIntent, ...] = ()
    intent: ResolutionIntent | None = None
    fallback_reason: str | None = None
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
class SetResolutionIntent:
    session_id: str
    revision: int
    row_id: str
    intent: ResolutionIntent


@dataclass(frozen=True)
class RetryMaterialization:
    session_id: str
    revision: int
    row_id: str


@dataclass(frozen=True)
class Preview:
    session_id: str
    revision: int


SessionCommand = SetResolutionIntent | RetryMaterialization | SetIncluded | SetApproval | PrepareProposalReview | Preview | Execute | Abort


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
class SyncStepOutcome:
    """Semantic execution evidence without private plans or captured payloads."""

    stage: Literal["repository-apply", "live-publication"]
    kind: str
    action: str
    scope: str
    scope_identity: str | None
    repo: str
    package_id: str | None
    status: str
    skip_reason: str | None = None
    exit_code: int | None = None
    error: str | None = None


def _step_scope_identity(step: ExecutionStep) -> str | None:
    if not step.repo_name:
        return None
    if step.scope_kind == "repo":
        return step.repo_name
    package = step.package_plan
    if step.package_id is None or package is None:
        return None
    if step.scope_kind == "target" and step.target_plan is not None:
        return repo_qualified_target_text(
            repo_name=step.repo_name,
            package_id=step.package_id,
            bound_profile=package.bound_profile,
            target_name=step.target_plan.target_name,
        )
    return f"{step.repo_name}:{package_ref_text(package_id=step.package_id, bound_profile=package.bound_profile)}"


@dataclass(frozen=True)
class SyncResult:
    status: Literal["completed", "incomplete", "failed", "aborted"]
    units: tuple[SyncUnitResult, ...]
    diagnostics: tuple[Diagnostic, ...] = ()
    steps: tuple[SyncStepOutcome, ...] = ()

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
        self._command_runtime = None
        self._captures = {}
        # A comparison Render is already a valid projection of these frozen
        # repository inputs. Reusing it also avoids volatile provider reruns.
        self._renders = {
            (unit.identity, unit.repository): unit.comparison_repository
            for unit in observations
            if unit.compare_repo == "render" and unit.comparison_repository is not None
        }
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
                    ("set-included", "set-approval", "prepare-proposal-review", "set-resolution-intent", "retry-materialization")
                    if supports_proposal(unit)
                    else ("set-included",)
                    if unit.state == "drifted" and not unit.diagnostics
                    else (),
                    allowed_intents=allowed_intents(unit),
                    intent=default_intent(unit),
                    fallback_reason=(unit.base.reason or "absent")
                    if unit.effective_policy == "both" and unit.base.status != "usable" else None,
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
                repository_metadata = prepare_repository_apply(tuple(selected_inputs.values()))
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
            session._command_runtime = context.projection.command_runtime
            session._resolved_inputs = resolved_inputs[0]
            session._frozen_bases = {
                observation.identity: FrozenBaseUnit(
                    _base_unit(context, observation.identity, *resolved_inputs[0][observation.identity]),
                    observation.git.head, observation.git.committed,
                    observation.git.primary_clean,
                )
                for observation in observations
                if observation.configured_policy in ("pull-only", "both") and not observation.diagnostics
            }
            session._publication_metadata = publication_metadata
            session._repository_metadata = repository_metadata
            # Adapter exceptions are programming failures, not planning results.
            session._emit(SessionOpened(session.view))
            resources.pop_all()
            return session

    def request_cancel(self) -> None:
        """Cancel owned commands without racing a session view mutation."""
        if self._command_runtime is not None:
            self._command_runtime.request_cancel()

    def check_cancelled(self) -> None:
        if self._command_runtime is not None:
            self._command_runtime.check_cancelled()

    @property
    def view(self) -> SessionView:
        return self._view

    def _emit(self, event: SessionEvent) -> None:
        if self._event_sink is not None:
            self._event_sink(event)

    def dispatch(self, command: SessionCommand) -> CommandAccepted | CommandRejected:
        view = self.view
        if type(command) not in (SetResolutionIntent, RetryMaterialization, SetIncluded, SetApproval, PrepareProposalReview, Preview, Execute, Abort):
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
        if isinstance(command, (SetApproval, PrepareProposalReview, SetResolutionIntent, RetryMaterialization)):
            if type(command.row_id) is not str or (
                isinstance(command, SetApproval) and type(command.approved) is not bool
            ):
                return CommandRejected(view, "invalid")
            row = next((row for row in view.rows if row.row_id == command.row_id), None)
            if row is None:
                return CommandRejected(view, "unknown-row")
            name = {
                SetApproval: "set-approval", PrepareProposalReview: "prepare-proposal-review",
                SetResolutionIntent: "set-resolution-intent", RetryMaterialization: "retry-materialization",
            }[type(command)]
            if name not in row.allowed_commands:
                return CommandRejected(view, "disallowed")
            if isinstance(command, SetResolutionIntent):
                if command.intent not in ("use-repository", "use-live", "merge"):
                    return CommandRejected(view, "invalid")
                if command.intent not in row.allowed_intents:
                    return CommandRejected(view, "disallowed")
                row = replace(row, intent=command.intent, proposal=None, diagnostics=())
            if isinstance(command, RetryMaterialization):
                row = replace(row, proposal=None)
            proposal, diagnostics = row.proposal, row.diagnostics
            approved = command.approved if isinstance(command, SetApproval) else row.approved
            if proposal is None and (approved or isinstance(command, (PrepareProposalReview, RetryMaterialization))):
                try:
                    proposal = materialize(
                        row.observation, intent=row.intent, capture=self._capture,
                        render=self._render, merge=self._merge,
                    )
                    self.check_cancelled()
                    diagnostics = ()
                except (KeyboardInterrupt, InterruptedError):
                    self.request_cancel()
                    diagnostics = (Diagnostic("interrupted", "Materialization interrupted"),)
                    approved = False
                except CaptureError as exc:
                    diagnostics = (Diagnostic("capture-failed", str(exc)),)
                    approved = False
                except (ReconciliationConflict, ReconciliationFailed) as exc:
                    diagnostics = (Diagnostic(
                        "reconciliation-conflict" if isinstance(exc, ReconciliationConflict)
                        else "reconciliation-failed", str(exc)),)
                    approved = False
                except (ValueError, OSError) as exc:
                    diagnostics = (Diagnostic("materialization-failed", str(exc)),)
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

    def _capture(self, observation: Observation) -> FilePresent | Missing:
        self.check_cancelled()
        item, metadata = self._resolved_inputs[observation.identity]
        if observation.identity not in self._captures:
            try:
                self._captures[observation.identity] = capture_observation(
                    observation, metadata=metadata, context=item.package_context.context,
                    command_runtime=self._context.projection.command_runtime,
                )
            except (KeyboardInterrupt, InterruptedError, CaptureError):
                raise
            except (ValueError, OSError) as exc:
                raise CaptureError(observation.repository_path, str(exc)) from exc
        return self._captures[observation.identity]

    def _render(self, observation: Observation, repository: FilePresent | Missing) -> FilePresent | Missing:
        self.check_cancelled()
        key = (observation.identity, repository)
        if key not in self._renders:
            item, metadata = self._resolved_inputs[observation.identity]
            outcome = project_frozen_file(
                self._context.projection.command_runtime, metadata=metadata,
                context=item.package_context.context,
                repository=repository.content if isinstance(repository, FilePresent) else None,
                live=observation.live.content if isinstance(observation.live, FilePresent) else None,
                view="render", repo_side=True,
            )
            self._renders[key] = Missing() if outcome is None else FilePresent(outcome)
        return self._renders[key]

    def _merge(self, observation: Observation, captured: FilePresent | Missing) -> FilePresent | Missing:
        self.check_cancelled()
        if observation.base.status != "usable" or observation.base.record is None:
            raise ValueError("Merge requires a usable Sync Base")
        return reconcile(
            observation.base.record.payload, observation.repository, captured,
            command_runtime=self._context.projection.command_runtime,
        )

    def _acknowledge(self, row: SessionRow) -> None:
        observation = row.observation
        item, _metadata = self._resolved_inputs[observation.identity]
        with SyncBaseStore.open(
            self._context.tracked_state.state_root, item.repo.config.state_key,
        ) as store:
            lifecycle = SyncBaseLifecycle(
                store, SyncBaseGit(item.repo.root, self._context.projection.command_runtime),
                operation="sync", preview=False,
            )
            completion = lifecycle.complete(
                self._frozen_bases[observation.identity],
                ProposalCompletion(
                    intent=row.proposal.intent, approved=True,
                    publication_effects="succeeded" if row.proposal.publication_effects else "not-required",
                    primary_effect="succeeded" if row.proposal.primary_source_change is not None else "not-required",
                ),
            )
            if completion.failure is not None:
                raise completion.failure
            if not completion.converged:
                raise AssertionError("Approved repository outcome did not reach Base completion")

    def _publish(self) -> tuple[
        dict[str, tuple[str, tuple[Diagnostic, ...]]],
        tuple[Diagnostic, ...],
        tuple[SyncStepOutcome, ...],
    ]:
        selected = tuple(
            row for row in self.view.rows
            if row.included and row.approved and row.proposal is not None
        )
        if not selected:
            return {}, (), ()
        units, diagnostics, steps = {}, (), ()
        if any(row.observation.configured_policy in ("pull-only", "both") or row.proposal.primary_source_change is not None for row in selected):
            by_id = {row.row_id: row for row in selected}
            acknowledgment_failures = {}

            def complete(unit: RepositoryApplyUnit) -> None:
                row = by_id[unit.row_id]
                if row.observation.configured_policy in ("pull-only", "both") and not row.proposal.publication_effects:
                    try:
                        self._acknowledge(row)
                    except InterruptedError:
                        raise
                    except (SyncBaseStoreError, SyncBaseGitError, OSError) as exc:
                        acknowledgment_failures[row.row_id] = Diagnostic("base-acknowledgment-failed", str(exc))
                        raise

            result = execute_repository_apply(
                self._repository_metadata,
                tuple(RepositoryApplyUnit(
                    row.row_id, row.observation.identity, row.proposal.primary_source_change,
                ) for row in selected),
                command_runtime=self._context.projection.command_runtime,
                complete=complete,
            )
            units, diagnostics, steps = self._execution_outcome(result, "repository-apply")
            for row in selected:
                if row.row_id in acknowledgment_failures:
                    units[row.row_id] = ("execution-failed", (acknowledgment_failures[row.row_id],))
                elif row.proposal.publication_effects and units[row.row_id][0] == "converged":
                    # Repository Apply success does not complete pending live work.
                    units[row.row_id] = ("skipped", ())
            if result.error is not None:
                return units, diagnostics, steps
        else:
            # Ineligible no-write completion needs neither a Base nor a receipt.
            units = {row.row_id: ("converged", ()) for row in selected if not row.proposal.publication_effects}
        publication = tuple(row for row in selected if row.proposal.publication_effects)
        if publication:
            published, diagnostics, publication_steps = self._publish_effects(publication)
            units.update(published)
            steps += publication_steps
        return units, diagnostics, steps

    def _publish_effects(self, selected: tuple[SessionRow, ...]) -> tuple[
        dict[str, tuple[str, tuple[Diagnostic, ...]]],
        tuple[Diagnostic, ...],
        tuple[SyncStepOutcome, ...],
    ]:
        by_id = {row.row_id: row for row in selected}
        acknowledgment_failures = {}

        def complete(unit: PublicationUnit) -> None:
            row = by_id[unit.row_id]
            if row.observation.configured_policy in ("pull-only", "both"):
                try:
                    self._acknowledge(row)
                except InterruptedError:
                    raise
                except (SyncBaseStoreError, SyncBaseGitError, OSError) as exc:
                    acknowledgment_failures[row.row_id] = Diagnostic("base-acknowledgment-failed", str(exc))
                    raise

        result = execute_publication(
            self._publication_metadata,
            tuple(PublicationUnit(
                row.row_id, row.observation.identity, row.proposal.publication_effects
            ) for row in selected),
            complete=complete,
            snapshot_config=self._context.config.snapshots,
            command_runtime=self._context.projection.command_runtime,
        )
        units, diagnostics, steps = self._execution_outcome(result, "live-publication")
        for row_id, failure in acknowledgment_failures.items():
            units[row_id] = ("execution-failed", (failure,))
        return units, diagnostics, steps

    @staticmethod
    def _execution_outcome(
        result: PublicationResult,
        stage: Literal["repository-apply", "live-publication"],
    ) -> tuple[
        dict[str, tuple[str, tuple[Diagnostic, ...]]],
        tuple[Diagnostic, ...],
        tuple[SyncStepOutcome, ...],
    ]:
        code = "interrupted" if result.interrupted else "execution-failed"
        diagnostics = () if result.error is None else (Diagnostic(code, result.error),)
        units = {
            unit.row_id: (
                "converged" if unit.status == "ok" else "skipped" if unit.status == "skipped" else code,
                () if unit.error is None else (Diagnostic(code, unit.error),),
            )
            for unit in result.units
        }
        steps = tuple(
            SyncStepOutcome(
                stage=stage,
                kind=item.step.kind,
                action=item.step.action,
                scope=item.step.scope_kind,
                scope_identity=_step_scope_identity(item.step),
                repo=item.step.repo_name,
                package_id=item.step.package_id,
                status=item.status,
                skip_reason=item.skip_reason,
                exit_code=item.exit_code,
                error=item.error,
            )
            for item in result.steps
        )
        return units, diagnostics, steps

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
