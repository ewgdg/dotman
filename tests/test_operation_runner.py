from __future__ import annotations

from pathlib import Path

from dotman.operation_runner import (
    RestoreActionFinished,
    RestoreActionStarted,
    RestoreOperationFinished,
    RestoreOperationStarted,
    run_restore_operation,
)
from dotman.snapshot import RestoreAction, SnapshotRecord, load_snapshot


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
