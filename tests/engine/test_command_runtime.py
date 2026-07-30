from __future__ import annotations

from pathlib import Path

from dotman.command_runtime import CommandResult, MemoryCommandRuntime, ShellCommand
from dotman.engine import DotmanEngine
from tests.helpers import write_single_repo_config


def test_planning_uses_injected_runtime_for_guard_probe_and_projection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    package_root = repo_root / "packages" / "app"
    (package_root / "files").mkdir(parents=True)
    (package_root / "files" / "config.txt").write_text("source\n", encoding="utf-8")
    (package_root / "package.toml").write_text(
        "\n".join(
            [
                'id = "app"',
                "",
                "[hooks]",
                'guard_push = "guard-command"',
                "",
                "[targets.available]",
                'probe = "probe-command"',
                "",
                "[targets.config]",
                'source = "files/config.txt"',
                'path = "~/.config/app/config.txt"',
                'render = "render-command"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    runtime = MemoryCommandRuntime(
        [
            CommandResult(exit_code=0),
            CommandResult(exit_code=0),
            CommandResult(exit_code=0, stdout=b"projected\n"),
        ]
    )
    config_path = write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)

    plan = DotmanEngine.from_config_path(
        config_path,
        command_runtime=runtime,
    ).plan_push_query("fixture:app@default")

    assert [request.command for request in runtime.requests] == [
        ShellCommand("guard-command"),
        ShellCommand("probe-command"),
        ShellCommand("render-command"),
    ]
    assert runtime.requests[0].excluded_env_keys == frozenset({"DOTMAN_ASSUME_YES"})
    assert [(target.target_name, target.action) for target in plan.package_plans[0].target_plans] == [
        ("available", "probe"),
        ("config", "create"),
    ]
    assert plan.package_plans[0].target_plans[1].desired_bytes == b"projected\n"
