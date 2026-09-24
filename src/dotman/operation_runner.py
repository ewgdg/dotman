from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence, TypeAlias

from dotman.file_access import sudo_session
from dotman.snapshot import (
    RestoreAction,
    RestoreActionResult,
    RestoreResult,
    SnapshotRecord,
    execute_restore_action,
    record_snapshot_restore,
)


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
