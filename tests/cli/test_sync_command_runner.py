from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from dotman.models import OperationPlan, SnapshotConfig, UiConfig
from dotman.sync_commands import SyncCommandRunner
from dotman.ui_context import current_ui_config


def test_sync_command_runner_declares_owned_commands_and_rejects_other_commands() -> (
    None
):
    runner = SyncCommandRunner(
        engine_factory=lambda _config_path: pytest.fail(
            "unsupported commands must not construct an engine"
        ),
        use_color=False,
    )

    assert runner.command_names == frozenset({"push", "pull", "restore"})
    with pytest.raises(ValueError, match="unsupported sync command 'list'"):
        runner.run(SimpleNamespace(command="list"))


def test_sync_command_runner_uses_typed_plan_and_resets_ui_scope(capsys) -> None:
    ui = UiConfig(full_paths=True)
    engine = SimpleNamespace(
        config=SimpleNamespace(
            ui=ui,
            snapshots=SnapshotConfig(
                enabled=False, path=Path("/unused"), max_generations=0
            ),
        ),
        plan_push=lambda *, sink, run_noop: OperationPlan(
            operation="push",
            package_plans=(),
        ),
    )
    runner = SyncCommandRunner(
        engine_factory=lambda config_path: (
            engine
            if config_path == "manager.toml"
            else pytest.fail("runner must forward the selected config path")
        ),
        use_color=False,
    )

    exit_code = runner.run(
        SimpleNamespace(
            command="push",
            config="manager.toml",
            full_path=None,
            binding=None,
            json_output=True,
            dry_run=True,
            run_noop=False,
            assume_yes=False,
        )
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {
        "mode": "dry-run",
        "operation": "push",
        "package_entries": [],
    }
    assert current_ui_config() is None


@pytest.mark.parametrize("operation", ["push", "pull"])
def test_real_directional_command_conflicts_with_live_sync_session(
    operation, tmp_path, monkeypatch
):
    from dotman.operation_lock import OperationBusy
    from tests.engine.test_sync_session import make_engine, open_session

    engine = make_engine(
        tmp_path, monkeypatch, [("unit", "both", b"same", b"same", "")]
    )
    runner = SyncCommandRunner(engine_factory=lambda _: engine, use_color=False)
    args = SimpleNamespace(
        command=operation,
        config=None,
        full_path=None,
        binding=None,
        json_output=True,
        dry_run=False,
        run_noop=False,
        assume_yes=False,
    )
    with open_session(engine, preview=False):
        with pytest.raises(OperationBusy):
            runner.run(args)
        # A read-only directional preview remains possible while the lock is held.
        args.dry_run = True
        assert runner.run(args) == 0


@pytest.mark.parametrize("operation", ["push", "pull"])
def test_directional_runner_holds_lock_during_planning_and_releases_after_failure(
    operation, tmp_path
):
    from dotman.config import default_state_root
    from dotman.operation_lock import OperationBusy, OperationLock

    def planning_failure(**_kwargs):
        with pytest.raises(OperationBusy):
            OperationLock.acquire(default_state_root())
        raise ValueError("planned failure")

    engine = SimpleNamespace(
        config=SimpleNamespace(ui=UiConfig()),
        **{f"plan_{operation}": planning_failure},
    )
    runner = SyncCommandRunner(engine_factory=lambda _: engine, use_color=False)
    with pytest.raises(ValueError, match="planned failure"):
        runner.run(
            SimpleNamespace(
                command=operation,
                config=None,
                full_path=None,
                binding=None,
                json_output=True,
                dry_run=False,
                run_noop=False,
                assume_yes=False,
            )
        )
    with OperationLock.acquire(default_state_root()):
        pass
