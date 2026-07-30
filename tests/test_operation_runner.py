from __future__ import annotations

from pathlib import Path

import pytest

import dotman.operation_runner as operation_runner
from dotman.command_runtime import CommandResult, MemoryCommandRuntime
from dotman.models import HookPlan, SnapshotConfig, TargetPlan
from dotman.operation_runner import (
    RestoreActionFinished,
    RestoreActionStarted,
    RestoreOperationFinished,
    RestoreOperationStarted,
    SyncOperationFinished,
    SyncOperationStarted,
    SyncPackageFinished,
    SyncPackageStarted,
    SyncStepFinished,
    SyncStepStarted,
    run_restore_operation,
    run_sync_operation,
)
from dotman.snapshot import RestoreAction, SnapshotRecord, load_snapshot
from tests.helpers import make_package_plan


def _push_plan_with_hook_and_target(*, tmp_path: Path, live_path: Path, chmod: str | None = None):
    return make_package_plan(
        operation="push",
        repo_name="fixture",
        package_id="app",
        requested_profile="default",
        hooks={
            "pre_push": [
                HookPlan(
                    package_id="app",
                    hook_name="pre_push",
                    command="printf 'pre push\\n'",
                    cwd=tmp_path,
                )
            ]
        },
        target_plans=[
            TargetPlan(
                package_id="app",
                target_name="config",
                repo_path=tmp_path / "repo" / "config.txt",
                live_path=live_path,
                action="create",
                target_kind="file",
                projection_kind="raw",
                desired_text="managed\n",
                desired_bytes=b"managed\n",
                chmod=chmod,
            )
        ],
    )


def test_run_sync_operation_emits_typed_events_in_execution_order(tmp_path: Path) -> None:
    live_path = tmp_path / "live" / "config.txt"
    plan = _push_plan_with_hook_and_target(tmp_path=tmp_path, live_path=live_path)
    events = []

    result = run_sync_operation(
        operation="push",
        plans=[plan],
        stream_output=False,
        command_runtime=MemoryCommandRuntime([CommandResult(exit_code=0, stdout=b"pre push\n")]),
        event_sink=events.append,
    )

    assert result.status == "ok"
    assert live_path.read_bytes() == b"managed\n"
    assert [type(event) for event in events] == [
        SyncOperationStarted,
        SyncPackageStarted,
        SyncStepStarted,
        SyncStepFinished,
        SyncStepStarted,
        SyncStepFinished,
        SyncPackageFinished,
        SyncOperationFinished,
    ]
    assert [event.step.action for event in events if isinstance(event, SyncStepStarted)] == ["pre_push", "create"]


def test_run_sync_operation_creates_and_finalizes_snapshot_at_first_live_mutation(tmp_path: Path) -> None:
    from dotman.snapshot import list_snapshots

    live_path = tmp_path / "live" / "config.txt"
    plan = _push_plan_with_hook_and_target(tmp_path=tmp_path, live_path=live_path)
    snapshot_config = SnapshotConfig(enabled=True, path=tmp_path / "snapshots", max_generations=5)
    snapshot_statuses_at_step_start: list[tuple[str, list[str]]] = []

    def record_snapshot_state(event) -> None:
        if isinstance(event, SyncStepStarted):
            snapshot_statuses_at_step_start.append(
                (event.step.action, [snapshot.status for snapshot in list_snapshots(snapshot_config.path)])
            )

    result = run_sync_operation(
        operation="push",
        plans=[plan],
        stream_output=False,
        snapshot_config=snapshot_config,
        command_runtime=MemoryCommandRuntime([CommandResult(exit_code=0)]),
        event_sink=record_snapshot_state,
    )

    assert result.status == "ok"
    assert snapshot_statuses_at_step_start == [("pre_push", []), ("create", ["prepared"])]
    assert [snapshot.status for snapshot in list_snapshots(snapshot_config.path)] == ["applied"]


def test_run_sync_operation_attempts_lazy_snapshot_only_once_when_none_is_created(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_path = tmp_path / "live" / "config.txt"
    plan = _push_plan_with_hook_and_target(tmp_path=tmp_path, live_path=live_path, chmod="600")
    attempts = 0

    def skip_snapshot(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        return None

    monkeypatch.setattr(operation_runner, "create_push_snapshot", skip_snapshot)

    result = run_sync_operation(
        operation="push",
        plans=[plan],
        stream_output=False,
        snapshot_config=SnapshotConfig(enabled=False, path=tmp_path / "snapshots", max_generations=5),
        command_runtime=MemoryCommandRuntime([CommandResult(exit_code=0)]),
    )

    assert result.status == "ok"
    assert attempts == 1


def test_run_sync_operation_cleans_incomplete_snapshot_when_capture_fails(tmp_path: Path) -> None:
    live_path = tmp_path / "live" / "config.txt"
    live_path.mkdir(parents=True)
    snapshot_config = SnapshotConfig(enabled=True, path=tmp_path / "snapshots", max_generations=5)
    plan = make_package_plan(
        operation="push",
        repo_name="fixture",
        package_id="app",
        requested_profile="default",
        target_plans=[
            TargetPlan(
                package_id="app",
                target_name="config",
                repo_path=tmp_path / "repo" / "config.txt",
                live_path=live_path,
                action="update",
                target_kind="file",
                projection_kind="raw",
                desired_text="managed\n",
                desired_bytes=b"managed\n",
            )
        ],
    )

    with pytest.raises(ValueError, match="snapshot capture expects file path"):
        run_sync_operation(
            operation="push",
            plans=[plan],
            stream_output=False,
            snapshot_config=snapshot_config,
        )

    assert not snapshot_config.path.exists() or list(snapshot_config.path.iterdir()) == []


def test_run_sync_operation_finalizes_snapshot_when_interrupted_after_capture(tmp_path: Path) -> None:
    from dotman.snapshot import list_snapshots

    live_path = tmp_path / "live" / "config.txt"
    plan = _push_plan_with_hook_and_target(tmp_path=tmp_path, live_path=live_path)
    snapshot_config = SnapshotConfig(enabled=True, path=tmp_path / "snapshots", max_generations=5)

    def interrupt_first_mutation(event) -> None:
        if isinstance(event, SyncStepStarted) and event.step.kind != "hook":
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_sync_operation(
            operation="push",
            plans=[plan],
            stream_output=False,
            snapshot_config=snapshot_config,
            command_runtime=MemoryCommandRuntime([CommandResult(exit_code=0)]),
            event_sink=interrupt_first_mutation,
        )

    assert [snapshot.status for snapshot in list_snapshots(snapshot_config.path)] == ["failed"]


def test_run_restore_operation_mutates_and_records_typed_lifecycle_without_output_capture(tmp_path: Path) -> None:
    live_path = tmp_path / "live" / "config.txt"
    snapshot = SnapshotRecord(
        snapshot_id="snapshot-1",
        created_at="2026-07-29T00:00:00Z",
        status="applied",
        root=tmp_path / "snapshots" / "snapshot-1",
        entries=(),
    )
    action = RestoreAction(
        live_path=live_path,
        snapshot_path=snapshot.root / "restore" / "config.txt",
        action="create",
        before_bytes=b"",
        after_bytes=b"restored\n",
        desired_mode=0o600,
    )
    events = []

    result = run_restore_operation(snapshot=snapshot, actions=[action], event_sink=events.append)

    assert result.status == "ok"
    assert live_path.read_bytes() == b"restored\n"
    assert [type(event) for event in events] == [
        RestoreOperationStarted,
        RestoreActionStarted,
        RestoreActionFinished,
        RestoreOperationFinished,
    ]
    assert load_snapshot(snapshot.root).restore_count == 1


def test_run_restore_operation_stops_after_first_failed_action(tmp_path: Path) -> None:
    snapshot = SnapshotRecord(
        snapshot_id="snapshot-1",
        created_at="2026-07-29T00:00:00Z",
        status="applied",
        root=tmp_path / "snapshots" / "snapshot-1",
        entries=(),
    )
    invalid_action = RestoreAction(
        live_path=tmp_path / "invalid.txt",
        snapshot_path=snapshot.root / "restore" / "invalid.txt",
        action="unsupported",
        before_bytes=b"",
        after_bytes=b"",
        desired_mode=None,
    )
    skipped_live_path = tmp_path / "skipped.txt"
    later_action = RestoreAction(
        live_path=skipped_live_path,
        snapshot_path=snapshot.root / "restore" / "skipped.txt",
        action="create",
        before_bytes=b"",
        after_bytes=b"should not be written\n",
        desired_mode=None,
    )
    events = []

    result = run_restore_operation(snapshot=snapshot, actions=[invalid_action, later_action], event_sink=events.append)

    assert result.status == "failed"
    assert result.exit_code == 1
    assert [event.action for event in events if isinstance(event, RestoreActionStarted)] == [invalid_action]
    assert not skipped_live_path.exists()
    assert not snapshot.root.exists()
