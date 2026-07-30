from __future__ import annotations

import json
from pathlib import Path

from dotman.cli_emit import HumanExecutionRenderer, JsonExecutionRenderer
from dotman.execution import ExecutionResult, ExecutionSession
from dotman.operation_runner import (
    RestoreActionFinished,
    RestoreActionStarted,
    RestoreOperationFinished,
    RestoreOperationStarted,
    SyncOperationFinished,
    SyncOperationStarted,
)
from dotman.snapshot import RestoreAction, RestoreActionResult, RestoreResult, SnapshotRecord


def test_human_renderer_consumes_sync_events_without_running_execution(capsys) -> None:
    session = ExecutionSession(operation="push")
    result = ExecutionResult(session=session, status="ok", repos=())
    renderer = HumanExecutionRenderer(full_paths=False, use_color=False)

    renderer.render_sync_event(SyncOperationStarted(session))
    renderer.render_sync_event(SyncOperationFinished(result))

    output = capsys.readouterr().out
    assert ":: executing push" in output
    assert "repos: 0 · packages: 0 · steps: 0" in output
    assert "no pending target actions" in output


def test_json_renderer_ignores_progress_and_emits_one_final_document(capsys) -> None:
    session = ExecutionSession(operation="pull")
    result = ExecutionResult(session=session, status="ok", repos=())
    renderer = JsonExecutionRenderer()

    renderer.render_sync_event(SyncOperationStarted(session))
    assert capsys.readouterr().out == ""

    exit_code = renderer.render_sync_result(result)
    output = capsys.readouterr().out

    assert exit_code == 0
    assert json.loads(output) == result.to_dict()
    assert output.count("\n{") == 0


def test_human_renderer_consumes_restore_events_without_mutating_filesystem(tmp_path: Path, capsys) -> None:
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
    action_result = RestoreActionResult(action=action, status="ok")
    result = RestoreResult(snapshot=snapshot, actions=(action_result,), status="ok")
    renderer = HumanExecutionRenderer(full_paths=True, use_color=False)

    renderer.render_restore_event(RestoreOperationStarted(snapshot=snapshot, action_count=1))
    renderer.render_restore_event(RestoreActionStarted(action=action, index=1, total=1))
    renderer.render_restore_event(RestoreActionFinished(result=action_result, index=1, total=1))
    renderer.render_restore_event(RestoreOperationFinished(result))

    output = capsys.readouterr().out
    assert ":: executing restore" in output
    assert f"[1/1] create      {live_path}" in output
    assert "ok" in output
    assert not live_path.exists()
