from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from dotman.models import Binding, BindingPlan, HookPlan, TargetPlan
from dotman.execution import build_execution_session, execute_session


def test_build_execution_session_orders_push_steps_per_package() -> None:
    plan = BindingPlan(
        operation="push",
        binding=Binding(repo="fixture", selector="stack", profile="default"),
        selector_kind="group",
        package_ids=["alpha", "beta"],
        variables={},
        hooks={
            "guard_push": [
                HookPlan(package_id="alpha", hook_name="guard_push", command="echo alpha guard", cwd=Path("/repo")),
                HookPlan(package_id="beta", hook_name="guard_push", command="echo beta guard", cwd=Path("/repo")),
            ],
            "pre_push": [
                HookPlan(package_id="alpha", hook_name="pre_push", command="echo alpha pre", cwd=Path("/repo")),
            ],
            "post_push": [
                HookPlan(package_id="alpha", hook_name="post_push", command="echo alpha post", cwd=Path("/repo")),
            ],
        },
        target_plans=[
            TargetPlan(
                package_id="alpha",
                target_name="config",
                repo_path=Path("/repo/alpha.conf"),
                live_path=Path("/live/alpha.conf"),
                action="create",
                target_kind="file",
                projection_kind="raw",
                desired_text="alpha\n",
                desired_bytes=b"alpha\n",
            ),
            TargetPlan(
                package_id="beta",
                target_name="config",
                repo_path=Path("/repo/beta.conf"),
                live_path=Path("/live/beta.conf"),
                action="update",
                target_kind="file",
                projection_kind="raw",
                desired_text="beta\n",
                desired_bytes=b"beta\n",
            ),
        ],
    )

    session = build_execution_session([plan], operation="push")

    assert [unit.package_id for unit in session.packages] == ["alpha", "beta"]
    assert [step.action for step in session.packages[0].steps] == [
        "guard_push",
        "pre_push",
        "create",
        "post_push",
    ]
    assert [step.action for step in session.packages[1].steps] == [
        "guard_push",
        "update",
    ]


def test_build_execution_session_does_not_add_pull_chmod_steps() -> None:
    plan = BindingPlan(
        operation="pull",
        binding=Binding(repo="fixture", selector="app", profile="default"),
        selector_kind="package",
        package_ids=["app"],
        variables={},
        hooks={},
        target_plans=[
            TargetPlan(
                package_id="app",
                target_name="config",
                repo_path=Path("/repo/app.conf"),
                live_path=Path("/live/app.conf"),
                action="update",
                target_kind="file",
                projection_kind="raw",
                chmod="600",
            )
        ],
    )

    session = build_execution_session([plan], operation="pull")

    assert [step.action for step in session.packages[0].steps] == ["update_repo"]


def test_execute_session_runs_tty_reconcile_steps_with_terminal_passthrough(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo_path = tmp_path / "repo-file"
    live_path = tmp_path / "live-file"
    repo_path.write_text("repo\n", encoding="utf-8")
    live_path.write_text("live\n", encoding="utf-8")

    plan = BindingPlan(
        operation="pull",
        binding=Binding(repo="fixture", selector="app", profile="default"),
        selector_kind="package",
        package_ids=["app"],
        variables={},
        hooks={},
        target_plans=[
            TargetPlan(
                package_id="app",
                target_name="config",
                repo_path=repo_path,
                live_path=live_path,
                action="update",
                target_kind="file",
                projection_kind="raw",
                reconcile_command="dotman reconcile editor --repo-path \"$DOTMAN_REPO_PATH\" --live-path \"$DOTMAN_LIVE_PATH\"",
                reconcile_io="tty",
                command_env={
                    "DOTMAN_REPO_PATH": str(repo_path),
                    "DOTMAN_LIVE_PATH": str(live_path),
                },
            )
        ],
    )
    session = build_execution_session([plan], operation="pull")

    recorded: dict[str, object] = {}

    def fake_run(command: str, **kwargs):
        recorded["command"] = command
        recorded["kwargs"] = kwargs
        return SimpleNamespace(returncode=0)

    def fake_popen(*args, **kwargs):  # pragma: no cover - the assertion is the test.
        raise AssertionError("interactive reconcile should not use piped Popen execution")

    monkeypatch.setattr("dotman.execution.subprocess.run", fake_run)
    monkeypatch.setattr("dotman.execution.subprocess.Popen", fake_popen)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("sys.stderr.isatty", lambda: True)

    result = execute_session(session, stream_output=True)

    assert result.status == "ok"
    assert result.packages[0].steps[0].step.action == "reconcile"
    assert recorded["command"] == (
        'dotman reconcile editor --repo-path "$DOTMAN_REPO_PATH" --live-path "$DOTMAN_LIVE_PATH"'
    )
    assert recorded["kwargs"]["cwd"] is None
    assert recorded["kwargs"]["shell"] is True
    assert recorded["kwargs"]["executable"] == "/bin/sh"
    assert recorded["kwargs"]["check"] is False
    assert recorded["kwargs"]["env"]["DOTMAN_REPO_PATH"] == str(repo_path)
    assert recorded["kwargs"]["env"]["DOTMAN_LIVE_PATH"] == str(live_path)


def test_execute_session_fails_tty_reconcile_without_terminal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo_path = tmp_path / "repo-file"
    live_path = tmp_path / "live-file"
    repo_path.write_text("repo\n", encoding="utf-8")
    live_path.write_text("live\n", encoding="utf-8")

    plan = BindingPlan(
        operation="pull",
        binding=Binding(repo="fixture", selector="app", profile="default"),
        selector_kind="package",
        package_ids=["app"],
        variables={},
        hooks={},
        target_plans=[
            TargetPlan(
                package_id="app",
                target_name="config",
                repo_path=repo_path,
                live_path=live_path,
                action="update",
                target_kind="file",
                projection_kind="raw",
                reconcile_command="dotman reconcile editor --repo-path \"$DOTMAN_REPO_PATH\" --live-path \"$DOTMAN_LIVE_PATH\"",
                reconcile_io="tty",
                command_env={
                    "DOTMAN_REPO_PATH": str(repo_path),
                    "DOTMAN_LIVE_PATH": str(live_path),
                },
            )
        ],
    )
    session = build_execution_session([plan], operation="pull")

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    monkeypatch.setattr("sys.stderr.isatty", lambda: False)

    result = execute_session(session, stream_output=True)

    assert result.status == "failed"
    assert result.packages[0].steps[0].status == "failed"
    assert result.packages[0].steps[0].error == "reconcile_io 'tty' requires an interactive terminal"


def test_execute_session_keeps_batch_reconcile_on_piped_command_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo_path = tmp_path / "repo-file"
    live_path = tmp_path / "live-file"
    repo_path.write_text("repo\n", encoding="utf-8")
    live_path.write_text("live\n", encoding="utf-8")

    plan = BindingPlan(
        operation="pull",
        binding=Binding(repo="fixture", selector="app", profile="default"),
        selector_kind="package",
        package_ids=["app"],
        variables={},
        hooks={},
        target_plans=[
            TargetPlan(
                package_id="app",
                target_name="config",
                repo_path=repo_path,
                live_path=live_path,
                action="update",
                target_kind="file",
                projection_kind="raw",
                reconcile_command="printf 'batch reconcile\\n'",
                command_env={
                    "DOTMAN_REPO_PATH": str(repo_path),
                    "DOTMAN_LIVE_PATH": str(live_path),
                },
            )
        ],
    )
    session = build_execution_session([plan], operation="pull")

    recorded: dict[str, object] = {}

    class FakeStream:
        def __init__(self, lines: list[str]) -> None:
            self._lines = iter(lines)

        def readline(self) -> str:
            return next(self._lines, "")

        def close(self) -> None:
            return None

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = FakeStream(["batch reconcile\n"])
            self.stderr = FakeStream([])

        def wait(self) -> int:
            return 0

    def fake_popen(command: str, **kwargs):
        recorded["command"] = command
        recorded["kwargs"] = kwargs
        return FakeProcess()

    def fake_run(*args, **kwargs):  # pragma: no cover - the assertion is the test.
        raise AssertionError("batch reconcile should not use terminal passthrough")

    monkeypatch.setattr("dotman.execution.subprocess.Popen", fake_popen)
    monkeypatch.setattr("dotman.execution.subprocess.run", fake_run)

    result = execute_session(session, stream_output=True)

    assert result.status == "ok"
    assert result.packages[0].steps[0].stdout == "batch reconcile\n"
    assert recorded["command"] == "printf 'batch reconcile\\n'"
