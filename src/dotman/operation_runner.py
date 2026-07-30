from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Sequence, TypeAlias

from dotman.command_runtime import CommandRuntime
from dotman.execution import (
    ExecutionResult,
    ExecutionSession,
    ExecutionStep,
    ExecutionStepResult,
    PackageExecutionResult,
    PackageExecutionUnit,
    RepoExecutionUnit,
    build_execution_session,
    execute_session,
)
from dotman.file_access import sudo_session
from dotman.models import OperationPlan, PackagePlan, SnapshotConfig
from dotman.snapshot import (
    RestoreAction,
    RestoreActionResult,
    RestoreResult,
    SnapshotRecord,
    create_push_snapshot,
    execute_restore_action,
    mark_snapshot_status,
    prune_snapshots,
    record_snapshot_restore,
)


ExecutionStepOwner: TypeAlias = PackageExecutionUnit | RepoExecutionUnit


@dataclass(frozen=True)
class SyncOperationStarted:
    session: ExecutionSession


@dataclass(frozen=True)
class SyncPackageStarted:
    package: PackageExecutionUnit


@dataclass(frozen=True)
class SyncStepStarted:
    owner: ExecutionStepOwner
    step: ExecutionStep
    index: int
    total: int


@dataclass(frozen=True)
class SyncStepFinished:
    owner: ExecutionStepOwner
    result: ExecutionStepResult
    index: int
    total: int


@dataclass(frozen=True)
class SyncPackageFinished:
    result: PackageExecutionResult


@dataclass(frozen=True)
class SyncOperationFinished:
    result: ExecutionResult


SyncExecutionEvent: TypeAlias = (
    SyncOperationStarted
    | SyncPackageStarted
    | SyncStepStarted
    | SyncStepFinished
    | SyncPackageFinished
    | SyncOperationFinished
)
SyncEventSink: TypeAlias = Callable[[SyncExecutionEvent], None]


@dataclass(frozen=True)
class RestoreOperationStarted:
    snapshot: SnapshotRecord
    action_count: int


@dataclass(frozen=True)
class RestoreActionStarted:
    action: RestoreAction
    index: int
    total: int


@dataclass(frozen=True)
class RestoreActionFinished:
    result: RestoreActionResult
    index: int
    total: int


@dataclass(frozen=True)
class RestoreOperationFinished:
    result: RestoreResult


RestoreExecutionEvent: TypeAlias = (
    RestoreOperationStarted | RestoreActionStarted | RestoreActionFinished | RestoreOperationFinished
)
RestoreEventSink: TypeAlias = Callable[[RestoreExecutionEvent], None]


def run_sync_operation(
    *,
    operation: str,
    plans: Sequence[PackagePlan] | OperationPlan,
    stream_output: bool,
    run_noop: bool = False,
    assume_yes: bool = False,
    command_runtime: CommandRuntime | None = None,
    event_sink: SyncEventSink | None = None,
    snapshot_config: SnapshotConfig | None = None,
) -> ExecutionResult:
    emit = event_sink or _ignore_sync_event
    session = build_execution_session(plans, operation=operation, run_noop=run_noop)
    snapshot: SnapshotRecord | None = None
    snapshot_attempted = False

    def emit_step_started(owner: ExecutionStepOwner, step: ExecutionStep, index: int, total: int) -> None:
        nonlocal snapshot, snapshot_attempted
        if operation == "push" and snapshot_config is not None and not snapshot_attempted and step.kind != "hook":
            snapshot_attempted = True
            snapshot = create_push_snapshot(plans, snapshot_config)
        emit(SyncStepStarted(owner, step, index, total))

    def finalize_snapshot(status: str) -> None:
        nonlocal snapshot
        if snapshot is None or snapshot_config is None:
            return
        mark_snapshot_status(snapshot, status)
        prune_snapshots(snapshot_config.path, max_generations=snapshot_config.max_generations)
        snapshot = None

    try:
        with sudo_session():
            emit(SyncOperationStarted(session))
            result = execute_session(
                session,
                stream_output=stream_output,
                assume_yes=assume_yes,
                command_runtime=command_runtime,
                on_package_start=lambda package: emit(SyncPackageStarted(package)),
                on_step_start=emit_step_started,
                on_step_finish=lambda owner, step_result, index, total: emit(
                    SyncStepFinished(owner, step_result, index, total)
                ),
                on_package_finish=lambda package_result: emit(SyncPackageFinished(package_result)),
            )
    except BaseException:
        finalize_snapshot("failed")
        raise

    if isinstance(plans, OperationPlan):
        result = replace(result, guard_skips=plans.guard_skips)
    finalize_snapshot("applied" if result.exit_code == 0 else "failed")
    emit(SyncOperationFinished(result))
    return result


def _ignore_sync_event(_event: SyncExecutionEvent) -> None:
    return None


def run_restore_operation(
    *,
    snapshot: SnapshotRecord,
    actions: Sequence[RestoreAction],
    event_sink: RestoreEventSink | None = None,
) -> RestoreResult:
    emit = event_sink or _ignore_restore_event
    visible_actions = tuple(action for action in actions if action.action != "noop")
    action_results: list[RestoreActionResult] = []
    status = "ok"

    with sudo_session():
        emit(RestoreOperationStarted(snapshot=snapshot, action_count=len(visible_actions)))
        for index, action in enumerate(visible_actions, start=1):
            emit(RestoreActionStarted(action=action, index=index, total=len(visible_actions)))
            action_result = execute_restore_action(action)
            action_results.append(action_result)
            emit(RestoreActionFinished(result=action_result, index=index, total=len(visible_actions)))
            if action_result.status != "ok":
                status = "failed"
                break
        if status == "ok":
            record_snapshot_restore(snapshot)

    result = RestoreResult(snapshot=snapshot, actions=tuple(action_results), status=status)
    emit(RestoreOperationFinished(result))
    return result


def _ignore_restore_event(_event: RestoreExecutionEvent) -> None:
    return None
