"""One-shot Sync boundary: immutable views, semantic commands and typed outcomes."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass, replace
from typing import Callable, Literal
from pathlib import Path
from uuid import uuid4
import stat

from dotman.command_runtime import CommandOperation, command_operation, command_runtime_session
from dotman.elevation import elevation_broker_session
from dotman.execution import ExecutionStep, directory_synced_file_mode
from dotman.atomic_files import default_created_file_mode
from dotman.capture import CaptureError
from dotman.sync_capture import capture_observation
from dotman.sync_observation import _identity
from dotman.sync_auxiliary import AuxiliaryRow, plan_auxiliary, retain_directional_hooks
from dotman.sync_reconciliation import reconcile, ReconciliationConflict, ReconciliationFailed
from dotman.projection import project_frozen_file
from dotman.models import ResolvedSyncScope, package_ref_text, repo_qualified_target_text
from dotman.planning import PlanningContext
from dotman.planning_guards import GuardPlanningError
from dotman.sync_base_store import SyncBaseStore, SyncBaseStoreError, FilePresent, Missing, DirectoryChildPresent, SyncBasePayload
from dotman.sync_base_lifecycle import (
    FrozenBaseUnit, ProposalCompletion, SyncBaseGit, SyncBaseGitError, SyncBaseLifecycle,
)
from dotman.operation_lock import OperationBusy, OperationLock, OperationLockError
from dotman.sync_observation import Diagnostic, Observation, observe_scope, _resolve_inputs, _base_unit
from dotman.sync_publication import HookActivation, PublicationResult, PublicationUnit, execute_publication, prepare_publication, freeze_child_metadata
from dotman.sync_repository_apply import (
    RepositoryApplyUnit, apply_repository_source, execute_repository_apply, prepare_repository_apply,
)


from dotman.sync_editor import AdditionalEdit, EditorCommandFailed, edit_sources, freeze_additional_sources, repository_workspace

from dotman.sync_path_policy import SyncPathError

ResolutionIntent = Literal["use-repository", "use-live", "merge"]


CommandName = Literal["authorize-symlink-replacement", "batch-set-approval", "prepare-source-review", "edit-proposal", "set-resolution-intent", "retry-materialization", "set-included", "set-approval", "prepare-proposal-review", "preview", "execute", "abort"]


@dataclass(frozen=True)
class PublicationEffect:
    kind: Literal["write", "delete", "chmod"]
    path: Path
    content: bytes | None = None
    mode: int | None = None


@dataclass(frozen=True)
class Proposal:
    repository: SyncBasePayload
    live: SyncBasePayload
    primary_source_change: SyncBasePayload | None
    publication_effects: tuple[PublicationEffect, ...]
    intent: ResolutionIntent | Literal["editor"] | None = "use-repository"
    capture: SyncBasePayload | None = None
    reconciliation: str | None = None
    generation: int = 0
    additional_changes: tuple[AdditionalEdit, ...] = ()


def materialize(
    observation: Observation,
    *,
    capture: Callable[[Observation], SyncBasePayload] | None = None,
    intent: ResolutionIntent | None = None,
    render: Callable[[Observation, SyncBasePayload], SyncBasePayload] | None = None,
    merge: Callable[[Observation, SyncBasePayload], SyncBasePayload] | None = None,
    symlink_authorized: bool = False,
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
    if isinstance(live, DirectoryChildPresent) and observation.chmod is not None:
        live = replace(live, executable=bool(int(observation.chmod, 8) & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)))
    # A Git mode-only change is a chmod effect, not a redundant payload write.
    content_changed = (
        isinstance(live, Missing) != isinstance(observation.live, Missing)
        or not isinstance(live, Missing) and live.content != observation.live.content
    )
    if content_changed:
        if path is None:
            raise ValueError("Publication requires a live endpoint path")
        effects.append(
            PublicationEffect("write", path, content=live.content)
            if isinstance(live, (FilePresent, DirectoryChildPresent))
            else PublicationEffect("delete", path)
        )
    if isinstance(live, (FilePresent, DirectoryChildPresent)):
        mode = int(observation.chmod, 8) if observation.chmod is not None else None
        if isinstance(live, DirectoryChildPresent) and mode is None:
            mode = observation.live_mode
            if not isinstance(observation.live, DirectoryChildPresent) or live.executable != observation.live.executable:
                mode = directory_synced_file_mode(
                    destination_mode=observation.live_mode if observation.live_mode is not None else default_created_file_mode(),
                    source_mode=stat.S_IXUSR if live.executable else 0,
                )
        if mode is not None and (observation.live_mode != mode or isinstance(observation.live, Missing)):
            if path is None:
                raise ValueError("Publication requires a live endpoint path")
            effects.append(PublicationEffect("chmod", path, mode=mode))
    if (
        effects and observation.live_is_symlink
        and observation.inputs.file_symlink_mode == "prompt"
        and any(effect.kind != "delete" for effect in effects)
    ):
        if not symlink_authorized:
            raise SyncPathError("symlink-authorization-required", "Live symlink replacement requires explicit authorization")
        # A mode-only publication must replace the link too, using frozen bytes.
        if not any(effect.kind == "write" for effect in effects):
            effects.insert(0, PublicationEffect("write", path, content=live.content))
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
    symlink_authorized: bool = False
    editor_io: Literal["tty", "pipe"] = "tty"
    additional_changes: tuple[AdditionalEdit, ...] = ()
    proposal: Proposal | None = None
    allowed_intents: tuple[ResolutionIntent, ...] = ()
    intent: ResolutionIntent | None = None
    fallback_reason: str | None = None
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class AdditionalRow:
    row_id: str
    repo: str
    path: Path
    change: AdditionalEdit
    references: tuple[str, ...]
    approved: bool = False
    kind: Literal["additional"] = "additional"
    allowed_commands: tuple[CommandName, ...] = ("set-approval", "prepare-source-review")


@dataclass(frozen=True)
class BatchSetApproval:
    session_id: str
    revision: int
    approved: bool


@dataclass(frozen=True)
class PrepareSourceReview:
    session_id: str
    revision: int
    row_id: str


@dataclass(frozen=True)
class SourceReview:
    row_id: str
    change: AdditionalEdit
    references: tuple[str, ...]


@dataclass(frozen=True)
class BatchApprovalChanged:
    approved: bool


@dataclass(frozen=True)
class AdditionalSourceResult:
    row_id: str
    repo: str
    path: Path
    status: Literal["pending", "would-apply", "applied", "execution-failed", "skipped", "interrupted"]
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class SessionView:
    session_id: str
    revision: int
    preview: bool
    terminal: bool
    observations: tuple[Observation, ...]
    rows: tuple[SessionRow | AuxiliaryRow | AdditionalRow, ...]
    allowed_commands: tuple[CommandName, ...]
    topology_diagnostics: tuple[Diagnostic, ...] = ()
    operation: str = "sync"


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
class AuthorizeSymlinkReplacement:
    session_id: str
    revision: int
    row_id: str


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


@dataclass(frozen=True)
class EditProposal:
    session_id: str
    revision: int
    row_id: str


@dataclass(frozen=True)
class ProposalEdit:
    row_id: str
    status: Literal["saved", "cancelled", "command-failed", "materialization-failed"]
    diagnostics: tuple[Diagnostic, ...] = ()


SessionCommand = AuthorizeSymlinkReplacement | BatchSetApproval | PrepareSourceReview | EditProposal | SetResolutionIntent | RetryMaterialization | SetIncluded | SetApproval | PrepareProposalReview | Preview | Execute | Abort


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
    status: Literal["directly-in-sync", "pending", "excluded", "observation-failed", "converged", "would-converge", "applied", "would-apply", "execution-failed", "interrupted", "skipped", "not-converged"]
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
        ) + (f"/{step.target_plan.child_path}" if step.target_plan.child_path is not None else "")
    return f"{step.repo_name}:{package_ref_text(package_id=step.package_id, bound_profile=package.bound_profile)}"


@dataclass(frozen=True)
class SyncResult:
    status: Literal["completed", "incomplete", "failed", "aborted"]
    units: tuple[SyncUnitResult, ...]
    diagnostics: tuple[Diagnostic, ...] = ()
    steps: tuple[SyncStepOutcome, ...] = ()
    additional_changes: tuple[AdditionalSourceResult, ...] = ()

    @property
    def exit_code(self) -> int:
        return {"completed": 0, "incomplete": 1, "failed": 1, "aborted": 130}[
            self.status
        ]


@dataclass(frozen=True)
class CommandAccepted:
    view: SessionView
    result: InclusionChanged | ApprovalChanged | BatchApprovalChanged | SourceReview | ProposalReview | ProposalEdit | SyncResult


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
    diagnostics: tuple[Diagnostic, ...] = ()


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


class ProposalSession:
    """Shared frozen workset, editing, approval and command lifecycle."""

    operation = "sync"
    additional_default_approval = False

    @staticmethod
    def _observe(context, scope, **kwargs):
        return observe_scope(context, scope, **kwargs)

    def _prepare_workset(self) -> None:
        pass

    def __init__(
        self,
        observations: tuple[Observation, ...],
        *,
        preview: bool,
        auxiliary: tuple[AuxiliaryRow, ...] = (),
        event_sink: SessionEventSink | None = None,
    ) -> None:
        self._command_operation = CommandOperation()
        self._editor_operation = CommandOperation()
        self._additional_candidates = {}
        self._additional_approvals = {}
        self._additional_results = ()
        self._edited_outcomes = {}
        self._proposal_generations = {}
        self._editor_preimages = {}
        self._editor_input_errors = {}
        self._captures = {}
        self._obsolete_bases = ()
        self._root_inputs = {}
        # A comparison Render is already a valid projection of these frozen
        # repository inputs. Reusing it also avoids volatile provider reruns.
        self._renders = {
            (unit.identity, unit.repository): unit.comparison_repository
            for unit in observations
            if (unit.compare_repo == "render" or unit.effective_policy == "push-only") and unit.comparison_repository is not None
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
                    (("authorize-symlink-replacement",) if unit.live_is_symlink and unit.inputs.file_symlink_mode == "prompt" and unit.effective_policy in ("push-only", "both") else ()) + ("set-included", "set-approval", "prepare-proposal-review", "set-resolution-intent", "retry-materialization", "edit-proposal")
                    if supports_proposal(unit)
                    else ("set-included",)
                    if unit.state == "drifted" and not unit.diagnostics
                    else (),
                    allowed_intents=allowed_intents(unit),
                    intent=default_intent(unit),
                    fallback_reason=(unit.base.reason or "absent")
                    if supports_proposal(unit) and unit.effective_policy == "both" and unit.base.status != "usable" else None,
                )
                for unit in observations
                if unit.state != "directly-in-sync" or unit.diagnostics
            ) + auxiliary,
            ("batch-set-approval", "preview", "abort") if preview else ("batch-set-approval", "preview", "execute", "abort"),
            operation=self.operation,
        )

    @classmethod
    def open(
        cls,
        context: PlanningContext,
        scope: ResolvedSyncScope,
        *,
        preview: bool = False,
        run_noop: bool = False,
        event_sink: SessionEventSink | None = None,
    ) -> SyncSession | SessionOpenFailed:
        with command_operation() as operation, ExitStack() as resources:
            lock = None
            try:
                if not preview:
                    lock = resources.enter_context(
                        OperationLock.acquire(context.tracked_state.state_root)
                    )
                resolved_inputs = _resolve_inputs(context, scope, operation=cls.operation)
                observed = cls._observe(
                    context, scope, preview=preview, run_noop=run_noop, resolved_inputs=resolved_inputs,
                    operation=cls.operation,
                )
                observations = observed.observations

                # Keep no-write completion metadata even when a direction is
                # removed; only Guard-admitted hooks may activate at execution.
                selected_inputs = {}
                for candidates in resolved_inputs[1].values():
                    for item in candidates:
                        key = item.selection.identity
                        if key not in selected_inputs:
                            selected_inputs[key] = replace(item, target_metadata=[])
                # Directional candidates retain empty hook scopes, but their
                # policy filtering must not reorder the frozen target workset.
                for item, target in resolved_inputs[0].values():
                    selected_inputs[item.selection.identity].target_metadata.append(target)
                publication_metadata = retain_directional_hooks(prepare_publication(
                    tuple(selected_inputs.values()), file_symlink_mode=context.config.file_symlink_mode,
                    dir_symlink_mode=context.config.dir_symlink_mode,
                ), observed.hook_scopes["push"])
                # Guards gate automatic flow, not deliberate Editor repository
                # writes. Freeze all destination-stage hooks; execution activates
                # them only for actual Primary writes or admitted auxiliary work.
                repository_metadata = prepare_repository_apply(tuple(selected_inputs.values()))
                auxiliary = plan_auxiliary(
                    resolved_inputs[0], observed.directional,
                    {"pull": retain_directional_hooks(repository_metadata, observed.hook_scopes["pull"]),
                     "push": publication_metadata},
                    dir_symlink_mode=context.config.dir_symlink_mode,
                    command_runtime=context.projection.command_runtime, run_noop=run_noop,
                )
                obsolete_bases = cls._freeze_obsolete_bases(context, observed) if not preview else ()
            except OperationBusy as exc:
                return SessionOpenFailed(Diagnostic("operation-busy", str(exc)))
            except OperationLockError as exc:
                return SessionOpenFailed(Diagnostic("operation-lock-failed", str(exc)))
            except (SyncBaseStoreError, SyncBaseGitError) as exc:
                return SessionOpenFailed(Diagnostic("base-failed", str(exc)))
            except GuardPlanningError as exc:
                # Guard output may contain managed content; report typed command evidence only.
                return SessionOpenFailed(Diagnostic(
                    "planning-failed", f"{exc.hook_name} failed with exit {exc.exit_code}",
                ))
            except ValueError as exc:
                return SessionOpenFailed(Diagnostic("planning-failed", str(exc)))
            except (KeyboardInterrupt, InterruptedError):
                return SessionOpenFailed(
                    Diagnostic("interrupted", "Sync opening interrupted")
                )
            except OSError as exc:
                return SessionOpenFailed(Diagnostic("observation-failed", str(exc)))
            resolved_inputs = (observed.inputs, resolved_inputs[1])
            session = cls(observations, preview=preview, auxiliary=auxiliary, event_sink=event_sink)
            session._obsolete_bases = obsolete_bases
            session._root_inputs = {_identity(target): (item, target) for item in selected_inputs.values() for target in item.target_metadata}
            session._run_noop = run_noop
            session._operation_lock = lock
            session._context = context
            session._command_operation = operation
            session._resolved_inputs = resolved_inputs[0]
            session._view = replace(session.view, rows=tuple(
                replace(row, editor_io=resolved_inputs[0][row.observation.identity][1].editor.io)
                if isinstance(row, SessionRow) and supports_proposal(row.observation) else row
                for row in session.view.rows
            ))
            for observation in observations:
                if not supports_proposal(observation):
                    continue
                item, metadata = resolved_inputs[0][observation.identity]
                try:
                    session._editor_preimages[observation.identity] = freeze_additional_sources(
                        metadata=metadata, repo_root=item.repo.root,
                        primary_paths=tuple(unit.repository_path for unit in observations),
                    )
                except (ValueError, OSError) as exc:
                    session._editor_input_errors[observation.identity] = str(exc)
            session._frozen_bases = {
                observation.identity: FrozenBaseUnit(
                    _base_unit(context, observation.identity, *resolved_inputs[0][observation.identity]),
                    observation.git.head, observation.git.committed,
                    observation.git.primary_clean,
                )
                for observation in observations
                if observation.configured_policy in ("pull-only", "both") and not observation.diagnostics
            }
            session._publication_metadata = freeze_child_metadata(publication_metadata, observations)
            session._repository_metadata = freeze_child_metadata(repository_metadata, observations)
            session._prepare_workset()
            # Adapter exceptions are programming failures, not planning results.
            session._emit(SessionOpened(session.view))
            resources.pop_all()
            return session

    @staticmethod
    def _freeze_obsolete_bases(context, observed) -> tuple:
        candidates = []
        for identity, item, census, complete in observed.directory_censuses:
            if not complete:
                continue
            prefix = (identity.canonical + "/").encode()
            present = {prefix + path.encode() for path, _ in census.entries}
            with SyncBaseStore.open(context.tracked_state.state_root, item.repo.config.state_key) as store:
                candidates.extend(
                    (identity, item.repo.config.state_key, key)
                    for key in store.identities() if key.startswith(prefix) and key not in present
                )
        return tuple(candidates)

    def request_editor_cancel(self) -> None:
        self._editor_operation.request_cancel()

    def request_cancel(self) -> None:
        """Cancel owned commands without racing a session view mutation."""
        self._command_operation.request_cancel()
        self._editor_operation.request_cancel()

    def check_cancelled(self) -> None:
        self._command_operation.check_cancelled()

    @property
    def view(self) -> SessionView:
        diagnostics = self._structural_conflicts()
        if diagnostics != self._view.topology_diagnostics:
            self._view = replace(self._view, topology_diagnostics=diagnostics)
        return self._view

    def _emit(self, event: SessionEvent) -> None:
        if self._event_sink is not None:
            self._event_sink(event)

    def dispatch(self, command: SessionCommand) -> CommandAccepted | CommandRejected:
        with command_operation(self._command_operation):
            return self._dispatch(command)

    def _dispatch(self, command: SessionCommand) -> CommandAccepted | CommandRejected:
        view = self.view
        if type(command) not in (AuthorizeSymlinkReplacement, BatchSetApproval, PrepareSourceReview, EditProposal, SetResolutionIntent, RetryMaterialization, SetIncluded, SetApproval, PrepareProposalReview, Preview, Execute, Abort):
            return CommandRejected(view, "invalid")
        if view.terminal:
            return CommandRejected(view, "terminal")
        if type(command.session_id) is not str or type(command.revision) is not int:
            return CommandRejected(view, "invalid")
        if command.session_id != view.session_id:
            return CommandRejected(view, "foreign-session")
        if command.revision != view.revision:
            return CommandRejected(view, "stale")
        if isinstance(command, BatchSetApproval):
            if type(command.approved) is not bool:
                return CommandRejected(view, "invalid")
            # Establish every final row state before any provider sees inputs.
            changed_references = set()
            for row in view.rows:
                if isinstance(row, AdditionalRow):
                    if row.approved != command.approved:
                        changed_references.update(row.references)
                    self._additional_approvals[row.change.path] = command.approved
            for row in view.rows:
                if isinstance(row, SessionRow) and row.row_id in changed_references:
                    self._clear_input_cache(row)
            rows = tuple(
                replace(row, approved=command.approved, proposal=None)
                if isinstance(row, SessionRow) and "set-approval" in row.allowed_commands
                else replace(row, approved=command.approved)
                if isinstance(row, AdditionalRow)
                else replace(row, included=command.approved)
                if isinstance(row, AuxiliaryRow) and "set-included" in row.allowed_commands
                else row
                for row in view.rows
            )
            self._view = replace(view, rows=rows)
            self._invalidate_inputs({row.row_id for row in rows if isinstance(row, SessionRow)}, clear_cache=False)
            self._view = replace(self.view, revision=view.revision + 1)
            self._emit(SessionChanged(self.view))
            return CommandAccepted(self.view, BatchApprovalChanged(command.approved))
        if isinstance(command, (SetApproval, PrepareSourceReview)):
            if type(command.row_id) is not str or (
                isinstance(command, SetApproval) and type(command.approved) is not bool
            ):
                return CommandRejected(view, "invalid")
            source = next((row for row in view.rows if row.row_id == command.row_id), None)
            if source is None:
                return CommandRejected(view, "unknown-row")
            if isinstance(source, AdditionalRow):
                if isinstance(command, PrepareSourceReview):
                    return CommandAccepted(view, SourceReview(source.row_id, source.change, source.references))
                changed = source.approved != command.approved
                self._additional_approvals[source.change.path] = command.approved
                self._refresh_additional_rows()
                if changed:
                    self._invalidate_inputs(set(source.references))
                self._view = replace(self.view, revision=view.revision + 1)
                self._emit(SessionChanged(self.view))
                return CommandAccepted(self.view, ApprovalChanged(source.row_id, command.approved))
            if isinstance(command, PrepareSourceReview):
                return CommandRejected(view, "disallowed")
        if isinstance(command, EditProposal):
            if type(command.row_id) is not str:
                return CommandRejected(view, "invalid")
            row = next((row for row in view.rows if row.row_id == command.row_id), None)
            if row is None:
                return CommandRejected(view, "unknown-row")
            if "edit-proposal" not in row.allowed_commands:
                return CommandRejected(view, "disallowed")
            return self._edit_proposal(row)
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
        if isinstance(command, (AuthorizeSymlinkReplacement, SetApproval, PrepareProposalReview, SetResolutionIntent, RetryMaterialization)):
            if type(command.row_id) is not str or (
                isinstance(command, SetApproval) and type(command.approved) is not bool
            ):
                return CommandRejected(view, "invalid")
            row = next((row for row in view.rows if row.row_id == command.row_id), None)
            if row is None:
                return CommandRejected(view, "unknown-row")
            name = {
                AuthorizeSymlinkReplacement: "authorize-symlink-replacement",
                SetApproval: "set-approval", PrepareProposalReview: "prepare-proposal-review",
                SetResolutionIntent: "set-resolution-intent", RetryMaterialization: "retry-materialization",
            }[type(command)]
            if name not in row.allowed_commands:
                return CommandRejected(view, "disallowed")
            if isinstance(command, AuthorizeSymlinkReplacement):
                row = replace(row, symlink_authorized=True, proposal=None, diagnostics=())
            if isinstance(command, SetResolutionIntent):
                if command.intent not in ("use-repository", "use-live", "merge"):
                    return CommandRejected(view, "invalid")
                if command.intent not in row.allowed_intents:
                    return CommandRejected(view, "disallowed")
                self._edited_outcomes.pop(row.row_id, None)
                row = replace(row, intent=command.intent, proposal=None, diagnostics=())
            if isinstance(command, RetryMaterialization):
                row = replace(row, proposal=None)
            proposal, diagnostics = row.proposal, row.diagnostics
            approved = command.approved if isinstance(command, SetApproval) else row.approved
            if proposal is None and (approved or isinstance(command, (PrepareProposalReview, RetryMaterialization))):
                try:
                    proposal = self._materialize_row(row)
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
                    diagnostics = (Diagnostic(exc.code if isinstance(exc, SyncPathError) else "materialization-failed", str(exc)),)
                    approved = False
            updated = replace(row, approved=approved, proposal=proposal, diagnostics=diagnostics)
            self._view = replace(view, revision=view.revision + 1, rows=tuple(
                updated if item.row_id == row.row_id else item for item in view.rows
            ))
            self._emit(SessionChanged(self.view))
            return CommandAccepted(self.view,
                ApprovalChanged(row.row_id, approved) if isinstance(command, SetApproval)
                else ProposalReview(row.row_id, proposal, diagnostics))
        if isinstance(command, Execute) and view.preview:
            return CommandRejected(view, "preview")
        if isinstance(command, (Preview, Execute)) and any(
            isinstance(row, SessionRow) and row.approved and row.included and row.proposal is None
            for row in view.rows
        ):
            return CommandRejected(view, "disallowed")
        if isinstance(command, (Preview, Execute)) and self._structural_conflicts():
            return CommandRejected(view, "disallowed", self._structural_conflicts())
        if isinstance(command, Preview):
            return CommandAccepted(view, self._result(preview=True))
        result = self._finish(aborted=isinstance(command, Abort))
        return CommandAccepted(self.view, result)


    def _structural_conflicts(self) -> tuple[Diagnostic, ...]:
        rows = {row.observation.identity: row for row in self._view.rows if isinstance(row, SessionRow)}
        failures = []
        for identity, row in rows.items():
            if not (row.included and row.approved and row.proposal is not None):
                continue
            for repository, blockers in ((True, row.observation.repository_blockers), (False, row.observation.live_blockers)):
                writer = (isinstance(row.proposal.primary_source_change, DirectoryChildPresent) if repository
                          else any(effect.kind == "write" for effect in row.proposal.publication_effects))
                if not writer:
                    continue
                for path in blockers:
                    blocker = rows.get(replace(identity, child_path=path))
                    deletion = blocker is not None and blocker.included and blocker.approved and blocker.proposal is not None and (
                        isinstance(blocker.proposal.primary_source_change, Missing) if repository
                        else any(effect.kind == "delete" for effect in blocker.proposal.publication_effects))
                    if not deletion:
                        failures.append(Diagnostic("structural-approval-required" if path in row.observation.managed_children else "structural-conflict",
                                                   f"{identity.canonical}: requires deletion of {path}"))
        return tuple(failures)

    def _row_additional(self, row: SessionRow) -> tuple[AdditionalEdit, ...]:
        return tuple(
            self._additional_candidates[path]
            for path in self._editor_preimages.get(row.observation.identity, {})
            if path in self._additional_candidates
        )

    def _references(self, paths: set[Path]) -> set[str]:
        return {
            row.row_id for row in self.view.rows
            if isinstance(row, SessionRow)
            and paths.intersection(self._editor_preimages.get(row.observation.identity, {}))
        }

    def _refresh_additional_rows(self) -> None:
        proposals = []
        for row in self.view.rows:
            if isinstance(row, AdditionalRow):
                continue
            if isinstance(row, SessionRow):
                changes = self._row_additional(row)
                row = replace(
                    row, additional_changes=changes,
                    proposal=replace(row.proposal, additional_changes=changes) if row.proposal else None,
                )
            proposals.append(row)
        proposals = tuple(proposals)
        sources = []
        for path, change in self._additional_candidates.items():
            references = tuple(row.row_id for row in proposals
                               if isinstance(row, SessionRow) and change in self._row_additional(row))
            owner = next(row for row in proposals if row.row_id in references)
            item, _ = self._resolved_inputs[owner.observation.identity]
            relative = path.relative_to(item.repo.root)
            repo = owner.observation.identity.repo
            sources.append(AdditionalRow(
                f"{repo}:additional/{relative.as_posix()}", repo, relative, change,
                references, self._additional_approvals.get(path, self.additional_default_approval),
            ))
        repo_order = list(dict.fromkeys(unit.identity.repo for unit in self.view.observations))
        sources.sort(key=lambda row: (repo_order.index(row.repo), row.path.as_posix()))
        self._view = replace(self.view, rows=proposals + tuple(sources))

    def _input_bytes(self, observation: Observation) -> dict[Path, bytes]:
        return {
            path: self._additional_candidates[path].candidate
            if path in self._additional_candidates and self._additional_approvals.get(path, self.additional_default_approval)
            else before
            for path, before in self._editor_preimages.get(observation.identity, {}).items()
        }

    def _clear_input_cache(self, row: SessionRow) -> None:
        identity = row.observation.identity
        self._captures.pop(identity, None)
        self._renders = {key: value for key, value in self._renders.items() if key[0] != identity}

    def _invalidate_inputs(self, references: set[str], *, clear_cache: bool = True) -> None:
        rows = []
        for row in self.view.rows:
            if not isinstance(row, SessionRow) or row.row_id not in references:
                rows.append(row)
                continue
            if clear_cache:
                self._clear_input_cache(row)
            # Unapproval is not a successful retry: retain unresolved diagnostics.
            row = replace(row, proposal=None)
            if row.approved:
                try:
                    self.check_cancelled()
                    row = replace(row, proposal=self._materialize_row(row), diagnostics=())
                    self.check_cancelled()
                except (KeyboardInterrupt, InterruptedError):
                    row = replace(row, approved=False, proposal=None,
                                  diagnostics=(Diagnostic("interrupted", "Materialization interrupted"),))
                except (CaptureError, ReconciliationConflict, ReconciliationFailed, ValueError, OSError) as exc:
                    code = ("capture-failed" if isinstance(exc, CaptureError) else
                            "reconciliation-conflict" if isinstance(exc, ReconciliationConflict) else
                            "reconciliation-failed" if isinstance(exc, ReconciliationFailed) else
                            exc.code if isinstance(exc, SyncPathError) else "materialization-failed")
                    row = replace(row, approved=False, proposal=None, diagnostics=(Diagnostic(code, str(exc)),))
            rows.append(row)
        self._view = replace(self.view, rows=tuple(rows))

    def _next_generation(self, row_id: str) -> int:
        generation = self._proposal_generations.get(row_id, 0) + 1
        self._proposal_generations[row_id] = generation
        return generation

    def _materialize_row(self, row: SessionRow) -> Proposal:
        if row.row_id not in self._edited_outcomes:
            observation = row.observation
            if observation.effective_policy == "push-only":
                observation = replace(observation, comparison_repository=self._render(observation, observation.repository))
            proposal = materialize(
                observation, intent=row.intent, capture=self._capture,
                render=self._render, merge=self._merge, symlink_authorized=row.symlink_authorized,
            )
            return replace(proposal, generation=self._next_generation(row.row_id), additional_changes=self._row_additional(row))
        repository, generation = self._edited_outcomes[row.row_id]
        observation = row.observation
        if observation.effective_policy in ("push-only", "both"):
            live = self._render(observation, repository)
        elif observation.effective_policy == "push-only-delete":
            live = Missing()
        else:
            live = observation.live
        proposal = materialize(
            replace(observation, repository=repository, comparison_repository=live),
            intent="use-repository", render=lambda *_: live, symlink_authorized=row.symlink_authorized,
        )
        return replace(
            proposal, primary_source_change=repository if repository != observation.repository else None,
            intent="editor", generation=generation, reconciliation="edited repository outcome",
            additional_changes=self._row_additional(row),
        )

    def _edit_proposal(self, row: SessionRow) -> CommandAccepted:
        # The idle operation also holds cancellation admitted before dispatch.
        operation = self._editor_operation
        status, diagnostics = "saved", ()
        updated = row
        prior_view = self.view
        prior_captures, prior_renders = dict(self._captures), dict(self._renders)
        prior_generations = dict(self._proposal_generations)
        prior_edited = self._edited_outcomes.get(row.row_id)
        prior_candidates = dict(self._additional_candidates)
        prior_additional = self._row_additional(row)
        try:
            with command_operation(operation):
                operation.check_cancelled()
                self.check_cancelled()
                item, metadata = self._resolved_inputs[row.observation.identity]
                if row.observation.identity in self._editor_input_errors:
                    raise ValueError(self._editor_input_errors[row.observation.identity])
                output = edit_sources(
                    observation=row.observation, proposal=row.proposal,
                    metadata=metadata, repo_root=item.repo.root,
                    additional=prior_additional,
                    preimages=self._editor_preimages[row.observation.identity],
                )
                operation.check_cancelled()
                if output.exit_code in (130, 143):
                    raise InterruptedError("Editor cancelled")
                if output.exit_code:
                    status = "command-failed"
                    diagnostics = (Diagnostic("editor-command-failed", f"Editor exited with status {output.exit_code}"),)
                else:
                    previous = row.proposal
                    generation = self._next_generation(row.row_id)
                    # Commit saved sources before projection: a failed Render must
                    # remain retryable from the user's edits, not automatic intent.
                    self._edited_outcomes[row.row_id] = (output.repository, generation)
                    for path in self._editor_preimages[row.observation.identity]:
                        self._additional_candidates.pop(path, None)
                    self._additional_candidates.update({change.path: change for change in output.additional})
                    # Staged unapproved bytes are review metadata, not provider inputs.
                    changed_input_paths = {
                        path for path in prior_candidates.keys() | self._additional_candidates.keys()
                        if self._additional_approvals.get(path, self.additional_default_approval)
                        and prior_candidates.get(path) != self._additional_candidates.get(path)
                    }
                    self._refresh_additional_rows()
                    affected = self._references(changed_input_paths)
                    self._invalidate_inputs(affected - {row.row_id})
                    if row.row_id in affected:
                        self._clear_input_cache(row)
                    # Primary bytes already key Render reuse; unchanged Additional
                    # inputs must not evict successful Render or Capture results.
                    if previous is not None and output.repository == previous.repository and row.row_id not in affected:
                        proposal = replace(
                            previous, intent="editor", generation=generation,
                            reconciliation="edited repository outcome",
                            additional_changes=output.additional,
                        )
                    else:
                        proposal = self._materialize_row(row)
                    operation.check_cancelled()
                    updated = replace(row, proposal=proposal, diagnostics=(),
                                      additional_changes=output.additional)
        except (KeyboardInterrupt, InterruptedError):
            status = "cancelled"
            diagnostics = (Diagnostic("editor-cancelled", "Editor cancelled"),)
            if prior_edited is None:
                self._edited_outcomes.pop(row.row_id, None)
            else:
                self._edited_outcomes[row.row_id] = prior_edited
            self._additional_candidates = prior_candidates
            self._view = prior_view
            self._captures, self._renders = prior_captures, prior_renders
            self._proposal_generations = prior_generations
        except EditorCommandFailed as exc:
            status = "command-failed"
            diagnostics = (Diagnostic("editor-command-failed", str(exc)),)
        except (ValueError, OSError) as exc:
            status = "materialization-failed"
            diagnostics = (Diagnostic("editor-materialization-failed", str(exc)),)
        finally:
            self._editor_operation = CommandOperation()
        if status not in ("saved", "cancelled"):
            updated = replace(row, approved=False, proposal=None, diagnostics=diagnostics,
                              additional_changes=self._row_additional(row))
        self._view = replace(self.view, revision=self.view.revision + 1, rows=tuple(
            updated if candidate.row_id == row.row_id else candidate for candidate in self.view.rows
        ))
        self._refresh_additional_rows()
        self._emit(SessionChanged(self.view))
        return CommandAccepted(self.view, ProposalEdit(row.row_id, status, diagnostics))

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
        additional = tuple(
            AdditionalSourceResult(row.row_id, row.repo, row.path,
                                   "would-apply" if preview and row.approved and not aborted else "pending")
            for row in self.view.rows if isinstance(row, AdditionalRow)
        ) if preview or aborted else self._additional_results
        if any(change.status == "execution-failed" for change in additional):
            status = "failed"
        return SyncResult(status, tuple(units), operation_diagnostics, steps, additional)

    def _capture(self, observation: Observation) -> SyncBasePayload:
        self.check_cancelled()
        item, metadata = self._resolved_inputs[observation.identity]
        if observation.identity not in self._captures:
            try:
                with ExitStack() as resources:
                    preimages = self._input_bytes(observation)
                    if preimages:
                        metadata, _, _ = resources.enter_context(repository_workspace(
                            metadata=metadata, repo_root=item.repo.root, preimages=preimages,
                        ))
                    self._captures[observation.identity] = capture_observation(
                        observation, metadata=metadata, context=item.package_context.context,
                        command_runtime=self._context.projection.command_runtime,
                        reuse_comparison=preimages == self._editor_preimages.get(observation.identity, {}),
                    )
            except (KeyboardInterrupt, InterruptedError, CaptureError):
                raise
            except (ValueError, OSError) as exc:
                raise CaptureError(observation.repository_path, str(exc)) from exc
        return self._captures[observation.identity]

    def _render(self, observation: Observation, repository: SyncBasePayload) -> SyncBasePayload:
        self.check_cancelled()
        key = (observation.identity, repository)
        if key not in self._renders:
            item, metadata = self._resolved_inputs[observation.identity]
            with ExitStack() as resources:
                preimages = self._input_bytes(observation)
                if preimages:
                    metadata, _, _ = resources.enter_context(repository_workspace(
                        metadata=metadata, repo_root=item.repo.root, preimages=preimages,
                    ))
                outcome = project_frozen_file(
                    self._context.projection.command_runtime, metadata=metadata,
                    context=item.package_context.context,
                    repository=repository.content if isinstance(repository, (FilePresent, DirectoryChildPresent)) else None,
                    live=observation.live.content if isinstance(observation.live, (FilePresent, DirectoryChildPresent)) else None,
                    view="render", repo_side=True,
                )
            self._renders[key] = Missing() if outcome is None else (
                DirectoryChildPresent(outcome, repository.executable)
                if isinstance(repository, DirectoryChildPresent) else FilePresent(outcome)
            )
        return self._renders[key]

    def _merge(self, observation: Observation, captured: SyncBasePayload) -> SyncBasePayload:
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
            if isinstance(row, SessionRow) and row.included and row.approved and row.proposal is not None
        )
        auxiliary = {
            direction: tuple(
                HookActivation(row.scope, self._resolved_inputs_identity(row.scope) if row.kind in ("probe", "directory-root") else None)
                for row in self.view.rows
                if isinstance(row, AuxiliaryRow) and row.included and direction in row.directions
            )
            for direction in ("pull", "push")
        }
        additional_rows = tuple(row for row in self.view.rows if isinstance(row, AdditionalRow))
        self._additional_results, additional_diagnostics, additional_steps = self._apply_additional(additional_rows)
        if not selected and not any(auxiliary.values()):
            if not additional_diagnostics:
                additional_diagnostics = self._reclaim_obsolete_bases()
            return {}, additional_diagnostics, additional_steps
        by_id = {row.row_id: row for row in selected}
        acknowledgment_failures = {}

        def complete(unit: RepositoryApplyUnit) -> None:
            row = by_id[unit.row_id]
            if row.observation.configured_policy in ("pull-only", "both"):
                try:
                    self._acknowledge(row)
                except InterruptedError:
                    raise
                except (SyncBaseStoreError, SyncBaseGitError, OSError) as exc:
                    acknowledgment_failures[row.row_id] = Diagnostic("base-acknowledgment-failed", str(exc))
                    raise

        # Even an ineligible no-write Proposal has an ordered completion boundary.
        result = execute_repository_apply(
            self._repository_metadata,
            tuple(RepositoryApplyUnit(
                row.row_id, row.observation.identity, row.proposal.primary_source_change,
                requires_publication=bool(row.proposal.publication_effects),
            ) for row in selected),
            command_runtime=self._context.projection.command_runtime,
            complete=complete, auxiliary=auxiliary["pull"], run_noop=self._run_noop,
            check_cancelled=self.check_cancelled, blocked=bool(additional_diagnostics),
        )
        units, diagnostics, repository_steps = self._execution_outcome(result, "repository-apply")
        steps = additional_steps + repository_steps
        diagnostics = additional_diagnostics + diagnostics
        for row in selected:
            if row.row_id in acknowledgment_failures:
                units[row.row_id] = ("execution-failed", (acknowledgment_failures[row.row_id],))
            elif row.proposal.publication_effects and units[row.row_id][0] == "converged":
                units[row.row_id] = ("not-converged", ())
        publication = tuple(row for row in selected if row.proposal.publication_effects)
        if publication or auxiliary["push"]:
            blocked = bool(diagnostics)
            published, publication_diagnostics, publication_steps = self._publish_effects(
                publication, auxiliary["push"], blocked=blocked,
            )
            for identity, outcome in published.items():
                # A later stage cannot erase repository failure or successful
                # repository work that has not reached its publication boundary.
                if not blocked and not (outcome[0] == "skipped" and units[identity][0] == "not-converged"):
                    units[identity] = outcome
            diagnostics += publication_diagnostics
            steps += publication_steps
        if not diagnostics:
            diagnostics = self._reclaim_obsolete_bases()
        return units, diagnostics, steps

    def _reclaim_obsolete_bases(self) -> tuple[Diagnostic, ...]:
        try:
            for identity, state_key, key in self._obsolete_bases:
                target_rows = [
                    row for row in self.view.rows if isinstance(row, SessionRow)
                    and replace(row.observation.identity, child_path=None) == identity
                ]
                if any(not row.included or row.observation.diagnostics or row.diagnostics for row in target_rows):
                    continue
                self.check_cancelled()
                with SyncBaseStore.open(self._context.tracked_state.state_root, state_key) as store:
                    store.delete(key)
        except (KeyboardInterrupt, InterruptedError):
            return (Diagnostic("interrupted", "Directory Base maintenance interrupted"),)
        except (SyncBaseStoreError, OSError) as exc:
            return (Diagnostic("base-maintenance-failed", str(exc)),)
        return ()

    def _apply_additional(self, rows: tuple[AdditionalRow, ...]):
        if not rows:
            return (), (), ()
        repo_order = {name: index for index, (name, _) in enumerate(self._repository_metadata.repo_hooks)}
        rows = tuple(sorted(rows, key=lambda row: (repo_order[row.repo], row.path.as_posix())))
        results, steps, diagnostics = [], [], ()
        with elevation_broker_session(), command_runtime_session(self._context.projection.command_runtime):
            for row in rows:
                status, failures = "pending", ()
                if row.approved:
                    status = "skipped"
                    if not diagnostics:
                        try:
                            self.check_cancelled()
                            apply_repository_source(row.change.path, FilePresent(row.change.candidate),
                                                    repo_root=row.change.path.parents[len(row.path.parts) - 1])
                            status = "applied"
                        except (KeyboardInterrupt, InterruptedError):
                            status = "interrupted"
                            failures = (Diagnostic("interrupted", "Additional Source apply interrupted"),)
                        except (OSError, ValueError, RuntimeError) as exc:
                            status = "execution-failed"
                            failures = (Diagnostic("additional-source-failed", str(exc)),)
                        diagnostics += failures
                    steps.append(SyncStepOutcome(
                        "repository-apply", "additional-source", "update", "repo", row.row_id,
                        row.repo, None, "ok" if status == "applied" else
                        "failed" if status == "execution-failed" else "unattempted" if status == "skipped" else status,
                        skip_reason="earlier-failure" if status == "skipped" else None,
                        error=failures[0].message if failures else None,
                    ))
                results.append(AdditionalSourceResult(row.row_id, row.repo, row.path, status, failures))
        return tuple(results), diagnostics, tuple(steps)

    def _resolved_inputs_identity(self, scope: str):
        return next(identity for identity in (*self._resolved_inputs, *self._root_inputs) if identity.canonical == scope)

    def _selected_root_work(self) -> tuple[PublicationUnit, ...]:
        work = []
        for row in self.view.rows:
            if not (isinstance(row, AuxiliaryRow) and row.kind == "directory-root" and row.included):
                continue
            identity = self._resolved_inputs_identity(row.scope)
            _, metadata = self._root_inputs[identity]
            work.append(PublicationUnit(
                row.row_id, identity,
                (PublicationEffect("chmod", metadata.live_path, mode=int(metadata.chmod, 8)),),
                auxiliary=True,
            ))
        return tuple(work)

    def _publish_effects(self, selected: tuple[SessionRow, ...], auxiliary: tuple[HookActivation, ...] = (), *, blocked: bool = False) -> tuple[
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
                row.row_id, row.observation.identity, row.proposal.publication_effects,
                symlink_authorized=row.symlink_authorized,
            ) for row in selected) + self._selected_root_work(),
            complete=complete,
            snapshot_config=self._context.config.snapshots,
            auxiliary=auxiliary, run_noop=self._run_noop,
            check_cancelled=self.check_cancelled, blocked=blocked,
            command_runtime=self._context.projection.command_runtime,
        )
        units, diagnostics, steps = self._execution_outcome(result, "live-publication")
        units = {row_id: outcome for row_id, outcome in units.items() if row_id in by_id}
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
                () if unit.error is None else (Diagnostic(unit.diagnostic_code or code, unit.error),),
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


class SyncSession(ProposalSession):
    """Two-sided convergence orchestration over the shared Proposal workset."""
