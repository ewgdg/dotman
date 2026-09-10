import json

import pytest

from dotman.sync_deck_command import PullDeckCommandRunner
from tests.cli.test_sync_deck_command import arguments
from tests.engine.test_sync_session import make_engine


@pytest.mark.parametrize("dry_run", [False, True])
def test_unattended_pull_failure_never_applies_healthy_subset(tmp_path, monkeypatch, capsys, dry_run):
    engine = make_engine(tmp_path, monkeypatch, [
        ("bad", "both", b"repo", b"live", 'capture = "exit 9"\ncompare = {repo = "raw", live = "raw"}'),
        ("good", "both", b"repo", b"live", ""),
    ])
    runner = PullDeckCommandRunner(engine_factory=lambda _: engine, use_color=False)
    assert runner.run(arguments(command="pull", dry_run=dry_run)) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "pull"
    assert payload["sync_units"][0]["approved"] is False
    assert payload["sync_units"][1]["approved"] is True
    assert payload["stages"] == []
    assert (tmp_path / "repo/packages/app/good").read_bytes() == b"repo"


def test_unattended_pull_reports_applied_not_converged(tmp_path, monkeypatch, capsys):
    engine = make_engine(tmp_path, monkeypatch, [("unit", "both", b"repo", b"live", "")])
    runner = PullDeckCommandRunner(engine_factory=lambda _: engine, use_color=False)
    assert runner.run(arguments(command="pull", dry_run=False)) == 0
    payload = json.loads(capsys.readouterr().out)
    unit = payload["sync_units"][0]
    assert unit["result"] == "applied"
    assert unit["resolution_intent"] is None
    assert unit["allowed_intents"] == []
    assert unit["base"]["acknowledged"] is False
    assert all(step["stage"] == "repository-apply" for step in payload["stages"])


def test_pull_parser_accepts_exact_multiple_scopes():
    from dotman.cli_parser import build_parser
    args = build_parser().parse_args(["--unattended", "pull", "main:app.a", "main:app.b"])
    assert args.scopes == ["main:app.a", "main:app.b"]


def test_pull_conflicts_with_live_sync_session_and_releases_lock(tmp_path, monkeypatch, capsys):
    from tests.engine.test_sync_session import open_session

    engine = make_engine(tmp_path, monkeypatch, [("unit", "both", b"repo", b"live", "")])
    runner = PullDeckCommandRunner(engine_factory=lambda _: engine, use_color=False)
    with open_session(engine, preview=False):
        assert runner.run(arguments(command="pull", dry_run=False)) == 1
        assert json.loads(capsys.readouterr().out)["summary"]["diagnostics"][0]["code"] == "operation-busy"
    assert runner.run(arguments(command="pull", dry_run=False)) == 0


def test_pull_releases_lock_after_observation_failure(tmp_path, monkeypatch, capsys):
    from dotman.config import default_state_root
    from dotman.operation_lock import OperationBusy, OperationLock
    from dotman.pull_session import PullSession

    engine = make_engine(tmp_path, monkeypatch, [("unit", "both", b"repo", b"live", "")])

    def fail_observation(*args, **kwargs):
        with pytest.raises(OperationBusy):
            OperationLock.acquire(default_state_root())
        raise ValueError("observation failed")

    monkeypatch.setattr(PullSession, "_observe", staticmethod(fail_observation))
    runner = PullDeckCommandRunner(engine_factory=lambda _: engine, use_color=False)
    assert runner.run(arguments(command="pull", dry_run=False)) == 1
    assert json.loads(capsys.readouterr().out)["summary"]["diagnostics"][0]["message"] == "observation failed"
    with OperationLock.acquire(default_state_root()):
        pass
