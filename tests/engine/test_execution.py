from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest

import dotman.execution as execution
from dotman import command_runtime as command_runtime_module, file_access
from dotman.command_runtime import CommandResult, MemoryCommandRuntime, ShellCommand, command_runtime_session
from dotman.engine import DotmanEngine
from dotman.execution import build_execution_session, execute_session
from dotman.models import HookPlan, OperationPlan, TargetPlan
from tests.helpers import make_package_plan, write_shared_stack_repo, write_single_repo_config


def test_build_execution_session_orders_push_steps_per_package() -> None:
    alpha_plan = make_package_plan(
        operation="push",
        repo_name="fixture",
        package_id="alpha",
        requested_profile="default",
        source_selector="stack",
        variables={},
        hooks={
            "guard_push": [
                HookPlan(package_id="alpha", hook_name="guard_push", command="echo alpha guard", cwd=Path("/repo")),
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
        ],
    )
    beta_plan = make_package_plan(
        operation="push",
        repo_name="fixture",
        package_id="beta",
        requested_profile="default",
        source_selector="stack",
        variables={},
        hooks={
            "guard_push": [
                HookPlan(package_id="beta", hook_name="guard_push", command="echo beta guard", cwd=Path("/repo")),
            ],
        },
        target_plans=[
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

    session = build_execution_session([alpha_plan, beta_plan], operation="push")

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


def test_build_execution_session_uses_probe_target_as_hook_premise_without_target_step() -> None:
    plan = make_package_plan(
        operation="push",
        repo_name="fixture",
        package_id="app",
        requested_profile="default",
        variables={},
        hooks={
            "pre_push": [
                HookPlan(
                    package_id="app",
                    target_name="version",
                    scope_kind="target",
                    hook_name="pre_push",
                    command="echo target pre",
                    cwd=Path("/repo/app"),
                )
            ],
            "post_push": [
                HookPlan(
                    package_id="app",
                    target_name="version",
                    scope_kind="target",
                    hook_name="post_push",
                    command="echo target post",
                    cwd=Path("/repo/app"),
                )
            ],
        },
        target_plans=[
            TargetPlan(
                package_id="app",
                target_name="version",
                repo_path=Path("/repo/app"),
                live_path=Path("/repo/app"),
                action="probe",
                target_kind="probe",
                projection_kind="probe",
                probe_command="exit 0",
            )
        ],
    )

    session = build_execution_session([plan], operation="push")

    assert [(step.kind, step.action) for step in session.packages[0].steps] == [
        ("hook", "pre_push"),
        ("hook", "post_push"),
    ]


def test_build_execution_session_orders_dependency_package_before_dependent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    write_shared_stack_repo(repo_root)
    stack_root = repo_root / "packages" / "shared-stack"
    (stack_root / "files").mkdir()
    (stack_root / "files" / "stack.conf").write_text("stack\n", encoding="utf-8")
    (stack_root / "package.toml").write_text(
        "\n".join(
            [
                'id = "shared-stack"',
                'depends = ["shared"]',
                "",
                "[targets.stack]",
                'source = "files/stack.conf"',
                'path = "~/.config/stack.conf"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    engine = DotmanEngine.from_config_path(write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root))

    session = build_execution_session(engine.plan_push_query("fixture:shared-stack@basic"), operation="push")

    assert [unit.package_id for unit in session.packages] == ["shared", "shared-stack"]


def test_execution_session_accepts_repo_units_without_touching_package_property() -> None:
    repo_unit = execution.RepoExecutionUnit(
        repo_name="fixture",
        pre_steps=(),
        packages=(),
        post_steps=(),
    )

    session = execution.ExecutionSession(
        operation="push",
        repos=(repo_unit,),
        requires_privilege=False,
    )

    assert session.repos == (repo_unit,)
    assert session.packages == ()


def test_execution_session_groups_package_units_into_repo_units() -> None:
    alpha = execution.PackageExecutionUnit(
        repo_name="fixture",
        selection_label="fixture:alpha@default",
        requested_profile="default",
        package_id="alpha",
        steps=(),
    )
    beta = execution.PackageExecutionUnit(
        repo_name="fixture",
        selection_label="fixture:beta@default",
        requested_profile="default",
        package_id="beta",
        steps=(),
    )

    session = execution.ExecutionSession(
        operation="push",
        package_units=(alpha, beta),
    )

    assert [repo.repo_name for repo in session.repos] == ["fixture"]
    assert session.repos[0].packages == (alpha, beta)
    assert session.packages == (alpha, beta)


def test_build_execution_session_orders_repo_package_and_target_scopes() -> None:
    plan = make_package_plan(
        operation="push",
        repo_name="fixture",
        package_id="app",
        requested_profile="default",
        variables={},
        hooks={
            "guard_push": [HookPlan(package_id="app", hook_name="guard_push", command="echo package guard", cwd=Path("/repo/app"))],
            "pre_push": [HookPlan(package_id="app", hook_name="pre_push", command="echo package pre", cwd=Path("/repo/app"))],
            "post_push": [HookPlan(package_id="app", hook_name="post_push", command="echo package post", cwd=Path("/repo/app"))],
        },
        target_plans=[
            TargetPlan(
                package_id="app",
                target_name="config",
                repo_path=Path("/repo/app.conf"),
                live_path=Path("/live/app.conf"),
                action="create",
                target_kind="file",
                projection_kind="raw",
                desired_bytes=b"repo\n",
            )
        ],
    )
    operation_plan = OperationPlan(
        operation="push",
        package_plans=(replace(plan, hooks={
            **plan.hooks,
            "guard_push": [
                *plan.hooks["guard_push"],
                HookPlan(package_id="app", target_name="config", scope_kind="target", hook_name="guard_push", command="echo target guard", cwd=Path("/repo/app")),
            ],
            "pre_push": [
                *plan.hooks["pre_push"],
                HookPlan(package_id="app", target_name="config", scope_kind="target", hook_name="pre_push", command="echo target pre", cwd=Path("/repo/app")),
            ],
            "post_push": [
                *plan.hooks["post_push"],
                HookPlan(package_id="app", target_name="config", scope_kind="target", hook_name="post_push", command="echo target post", cwd=Path("/repo/app")),
            ],
        }),),
        repo_hooks={
            "fixture": {
                "guard_push": [HookPlan(repo_name="fixture", scope_kind="repo", hook_name="guard_push", command="echo repo guard", cwd=Path("/repo"))],
                "pre_push": [HookPlan(repo_name="fixture", scope_kind="repo", hook_name="pre_push", command="echo repo pre", cwd=Path("/repo"))],
                "post_push": [HookPlan(repo_name="fixture", scope_kind="repo", hook_name="post_push", command="echo repo post", cwd=Path("/repo"))],
            }
        },
        repo_order=("fixture",),
    )

    session = build_execution_session(operation_plan, operation="push")

    assert [step.action for step in session.repos[0].pre_steps] == ["guard_push", "pre_push"]
    assert [step.action for step in session.repos[0].packages[0].steps] == [
        "guard_push",
        "pre_push",
        "guard_push",
        "pre_push",
        "create",
        "post_push",
        "post_push",
    ]
    assert [step.action for step in session.repos[0].post_steps] == ["post_push"]


def test_build_execution_session_keeps_hooks_unprivileged_even_when_target_needs_sudo(
    monkeypatch,
) -> None:
    monkeypatch.setattr("dotman.execution.needs_sudo_for_write", lambda path: path == Path("/etc/sddm.conf"))

    plan = make_package_plan(
        operation="push",
        repo_name="fixture",
        package_id="app",
        requested_profile="default",
        variables={},
        hooks={
            "guard_push": [HookPlan(package_id="app", hook_name="guard_push", command="echo package guard", cwd=Path("/repo/app"))],
            "pre_push": [HookPlan(package_id="app", hook_name="pre_push", command="echo package pre", cwd=Path("/repo/app"))],
            "post_push": [HookPlan(package_id="app", hook_name="post_push", command="echo package post", cwd=Path("/repo/app"))],
        },
        target_plans=[
            TargetPlan(
                package_id="app",
                target_name="config",
                repo_path=Path("/repo/app.conf"),
                live_path=Path("/etc/sddm.conf"),
                action="create",
                target_kind="file",
                projection_kind="raw",
                desired_bytes=b"repo\n",
            )
        ],
    )
    operation_plan = OperationPlan(
        operation="push",
        package_plans=(replace(plan, hooks={
            **plan.hooks,
            "guard_push": [
                *plan.hooks["guard_push"],
                HookPlan(package_id="app", target_name="config", scope_kind="target", hook_name="guard_push", command="echo target guard", cwd=Path("/repo/app")),
            ],
            "pre_push": [
                *plan.hooks["pre_push"],
                HookPlan(package_id="app", target_name="config", scope_kind="target", hook_name="pre_push", command="echo target pre", cwd=Path("/repo/app")),
            ],
            "post_push": [
                *plan.hooks["post_push"],
                HookPlan(package_id="app", target_name="config", scope_kind="target", hook_name="post_push", command="echo target post", cwd=Path("/repo/app")),
            ],
        }),),
        repo_hooks={
            "fixture": {
                "guard_push": [HookPlan(repo_name="fixture", scope_kind="repo", hook_name="guard_push", command="echo repo guard", cwd=Path("/repo"))],
                "pre_push": [HookPlan(repo_name="fixture", scope_kind="repo", hook_name="pre_push", command="echo repo pre", cwd=Path("/repo"))],
                "post_push": [HookPlan(repo_name="fixture", scope_kind="repo", hook_name="post_push", command="echo repo post", cwd=Path("/repo"))],
            }
        },
        repo_order=("fixture",),
    )

    session = build_execution_session(operation_plan, operation="push")

    assert session.requires_privilege is True
    assert all(not step.privileged for step in session.repos[0].pre_steps)
    assert [step.privileged for step in session.repos[0].packages[0].steps] == [
        False,
        False,
        False,
        False,
        True,
        False,
        False,
    ]
    assert all(not step.privileged for step in session.repos[0].post_steps)


@pytest.mark.parametrize(("assume_yes", "expected_value"), [(False, "0"), (True, "1")])
def test_execute_session_passes_dotman_assume_yes_to_hook_envs(
    monkeypatch,
    tmp_path: Path,
    assume_yes: bool,
    expected_value: str,
) -> None:
    plan = make_package_plan(
        operation="push",
        repo_name="fixture",
        package_id="app",
        requested_profile="default",
        variables={"feature": {"flag": "on"}},
        hooks={
            "guard_push": [
                HookPlan(package_id="app", hook_name="guard_push", command="echo package guard", cwd=Path("/repo/app")),
                HookPlan(
                    package_id="app",
                    target_name="config",
                    scope_kind="target",
                    hook_name="guard_push",
                    command="echo target guard",
                    cwd=Path("/repo/app"),
                    env={
                        "DOTMAN_TARGET_NAME": "config",
                        "EXISTING_TARGET_ENV": "target",
                    },
                ),
            ]
        },
        target_plans=[
            TargetPlan(
                package_id="app",
                target_name="config",
                repo_path=tmp_path / "repo" / "config",
                live_path=tmp_path / "live" / "config",
                action="noop",
                target_kind="file",
                projection_kind="raw",
            )
        ],
        repo_root=tmp_path / "repo",
        state_path=tmp_path / "state",
        inferred_os="linux",
    )
    operation_plan = OperationPlan(
        operation="push",
        package_plans=(plan,),
        repo_hooks={
            "fixture": {
                "guard_push": [
                    HookPlan(
                        repo_name="fixture",
                        scope_kind="repo",
                        hook_name="guard_push",
                        command="echo repo guard",
                        cwd=Path("/repo"),
                        env={
                            "DOTMAN_REPO_NAME": "fixture",
                            "EXISTING_REPO_ENV": "repo",
                        },
                    )
                ]
            }
        },
        repo_order=("fixture",),
    )
    session = build_execution_session(operation_plan, operation="push")

    runtime = MemoryCommandRuntime([CommandResult(exit_code=0)] * 3)

    result = execute_session(
        session,
        stream_output=False,
        assume_yes=assume_yes,
        command_runtime=runtime,
    )

    assert result.status == "ok"
    recorded_envs = {
        request.command.source: dict(request.env)
        for request in runtime.requests
        if isinstance(request.command, ShellCommand)
    }
    assert recorded_envs["echo repo guard"]["DOTMAN_ASSUME_YES"] == expected_value
    assert recorded_envs["echo repo guard"]["EXISTING_REPO_ENV"] == "repo"
    assert recorded_envs["echo package guard"]["DOTMAN_ASSUME_YES"] == expected_value
    assert recorded_envs["echo package guard"]["DOTMAN_REPO_NAME"] == "fixture"
    assert recorded_envs["echo package guard"]["DOTMAN_PACKAGE_ID"] == "app"
    assert recorded_envs["echo package guard"]["DOTMAN_PROFILE"] == "default"
    assert recorded_envs["echo package guard"]["DOTMAN_OPERATION"] == "push"
    assert recorded_envs["echo package guard"]["DOTMAN_REPO_ROOT"] == str(tmp_path / "repo")
    assert recorded_envs["echo package guard"]["DOTMAN_STATE_PATH"] == str(tmp_path / "state")
    assert recorded_envs["echo package guard"]["DOTMAN_OS"] == "linux"
    assert recorded_envs["echo package guard"]["DOTMAN_VAR_feature__flag"] == "on"
    assert recorded_envs["echo target guard"]["DOTMAN_ASSUME_YES"] == expected_value
    assert recorded_envs["echo target guard"]["EXISTING_TARGET_ENV"] == "target"
    assert recorded_envs["echo target guard"]["DOTMAN_TARGET_NAME"] == "config"


def test_execute_session_target_guard_skip_continues_next_target(monkeypatch, tmp_path: Path) -> None:
    def fake_run(request):
        if request.command == ShellCommand("exit 100"):
            return CommandResult(exit_code=100)
        return CommandResult(exit_code=0)

    runtime = MemoryCommandRuntime([fake_run] * 3)
    monkeypatch.setattr(execution, "_execute_target_step", lambda step: None)

    plan = make_package_plan(
        operation="push",
        repo_name="fixture",
        package_id="app",
        requested_profile="default",
        variables={},
        hooks={
            "guard_push": [
                HookPlan(package_id="app", target_name="alpha", scope_kind="target", hook_name="guard_push", command="exit 100", cwd=Path("/repo/app")),
                HookPlan(package_id="app", target_name="beta", scope_kind="target", hook_name="guard_push", command="echo beta guard", cwd=Path("/repo/app")),
            ],
            "pre_push": [
                HookPlan(package_id="app", target_name="alpha", scope_kind="target", hook_name="pre_push", command="echo alpha pre", cwd=Path("/repo/app")),
                HookPlan(package_id="app", target_name="beta", scope_kind="target", hook_name="pre_push", command="echo beta pre", cwd=Path("/repo/app")),
            ],
        },
        target_plans=[
            TargetPlan(
                package_id="app",
                target_name="alpha",
                repo_path=tmp_path / "repo" / "alpha.conf",
                live_path=tmp_path / "live" / "alpha.conf",
                action="create",
                target_kind="file",
                projection_kind="raw",
                desired_bytes=b"alpha\n",
            ),
            TargetPlan(
                package_id="app",
                target_name="beta",
                repo_path=tmp_path / "repo" / "beta.conf",
                live_path=tmp_path / "live" / "beta.conf",
                action="create",
                target_kind="file",
                projection_kind="raw",
                desired_bytes=b"beta\n",
            ),
        ],
    )

    result = execute_session(
        build_execution_session([plan], operation="push"),
        stream_output=False,
        command_runtime=runtime,
    )

    assert result.status == "ok"
    assert [request.command.source for request in runtime.requests] == [
        "exit 100",
        "echo beta guard",
        "echo beta pre",
    ]


def test_execute_session_marks_only_tty_hook_commands_interactive(monkeypatch) -> None:
    runtime = MemoryCommandRuntime([CommandResult(exit_code=0)] * 2)
    monkeypatch.setattr(execution, "_execute_target_step", lambda step: None)
    monkeypatch.setattr(execution, "_require_interactive_terminal_for_hook", lambda: None)

    plan = make_package_plan(
        operation="push",
        repo_name="fixture",
        package_id="app",
        requested_profile="default",
        variables={},
        hooks={
            "pre_push": [
                HookPlan(package_id="app", hook_name="pre_push", command="echo pipe", cwd=Path("/repo/app"), io="pipe"),
                HookPlan(package_id="app", hook_name="pre_push", command="echo tty", cwd=Path("/repo/app"), io="tty"),
            ],
        },
        target_plans=[],
    )

    result = execute_session(
        build_execution_session([plan], operation="push"),
        stream_output=False,
        command_runtime=runtime,
    )

    assert result.status == "ok"
    assert [(request.command.source, request.io) for request in runtime.requests] == [
        ("echo pipe", "pipe"),
        ("echo tty", "tty"),
    ]


def test_build_execution_session_keeps_package_hooks_unprivileged_when_package_needs_sudo(monkeypatch) -> None:
    monkeypatch.setattr("dotman.execution.needs_sudo_for_write", lambda path: path == Path("/etc/sddm.conf"))

    plan = make_package_plan(
        operation="push",
        repo_name="fixture",
        package_id="app",
        requested_profile="default",
        variables={},
        hooks={
            "guard_push": [HookPlan(package_id="app", hook_name="guard_push", command="echo guard", cwd=Path("/repo"))],
            "pre_push": [HookPlan(package_id="app", hook_name="pre_push", command="echo pre", cwd=Path("/repo"))],
            "post_push": [HookPlan(package_id="app", hook_name="post_push", command="echo post", cwd=Path("/repo"))],
        },
        target_plans=[
            TargetPlan(
                package_id="app",
                target_name="config",
                repo_path=Path("/repo/app.conf"),
                live_path=Path("/etc/sddm.conf"),
                action="create",
                target_kind="file",
                projection_kind="raw",
                desired_bytes=b"repo\n",
            )
        ],
    )

    session = build_execution_session([plan], operation="push")

    assert session.requires_privilege is True
    assert [step.privileged for step in session.packages[0].steps] == [False, False, True, False]


def test_build_execution_session_marks_privileged_hook_commands() -> None:
    plan = make_package_plan(
        operation="push",
        repo_name="fixture",
        package_id="app",
        requested_profile="default",
        variables={},
        hooks={
            "pre_push": [
                HookPlan(
                    package_id="app",
                    hook_name="pre_push",
                    command="systemctl restart sddm",
                    cwd=Path("/repo/app"),
                    elevation="root",
                )
            ],
        },
        target_plans=[],
    )

    session = build_execution_session([plan], operation="push", run_noop=True)

    assert session.requires_privilege is True
    assert [step.privileged for step in session.packages[0].steps] == [True]


def test_build_execution_session_keeps_hook_only_packages_when_hooks_are_finalized() -> None:
    plan = make_package_plan(
        operation="push",
        repo_name="fixture",
        package_id="app",
        requested_profile="default",
        variables={},
        hooks={
            "guard_push": [
                HookPlan(package_id="app", hook_name="guard_push", command="echo guard push", cwd=Path("/repo")),
            ],
            "pre_push": [
                HookPlan(package_id="app", hook_name="pre_push", command="echo pre push", cwd=Path("/repo")),
            ],
            "post_push": [
                HookPlan(package_id="app", hook_name="post_push", command="echo post push", cwd=Path("/repo")),
            ],
        },
        target_plans=[
            TargetPlan(
                package_id="app",
                target_name="config",
                repo_path=Path("/repo/config"),
                live_path=Path("/live/config"),
                action="noop",
                target_kind="file",
                projection_kind="raw",
            )
        ],
    )

    session = build_execution_session([plan], operation="push")

    assert [unit.package_id for unit in session.packages] == ["app"]
    assert [step.action for step in session.packages[0].steps] == [
        "guard_push",
        "pre_push",
        "post_push",
    ]


def test_execute_session_soft_skips_push_package_on_guard_exit_100_and_continues_next_package(
    tmp_path: Path,
    monkeypatch,
) -> None:
    alpha_repo_path = tmp_path / "alpha.repo"
    beta_repo_path = tmp_path / "beta.repo"
    alpha_live_path = tmp_path / "alpha.live"
    beta_live_path = tmp_path / "beta.live"
    alpha_repo_path.write_text("alpha repo\n", encoding="utf-8")
    beta_repo_path.write_text("beta repo\n", encoding="utf-8")

    alpha_plan = make_package_plan(
        operation="push",
        repo_name="fixture",
        package_id="alpha",
        requested_profile="default",
        source_selector="stack",
        variables={},
        hooks={
            "guard_push": [
                HookPlan(package_id="alpha", hook_name="guard_push", command="echo alpha guard 1", cwd=Path("/repo")),
                HookPlan(package_id="alpha", hook_name="guard_push", command="echo alpha guard 2", cwd=Path("/repo")),
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
                repo_path=alpha_repo_path,
                live_path=alpha_live_path,
                action="create",
                target_kind="file",
                projection_kind="raw",
                desired_bytes=b"alpha live\n",
            ),
        ],
    )
    beta_plan = make_package_plan(
        operation="push",
        repo_name="fixture",
        package_id="beta",
        requested_profile="default",
        source_selector="stack",
        variables={},
        hooks={
            "guard_push": [
                HookPlan(package_id="beta", hook_name="guard_push", command="echo beta guard", cwd=Path("/repo")),
            ],
            "pre_push": [
                HookPlan(package_id="beta", hook_name="pre_push", command="echo beta pre", cwd=Path("/repo")),
            ],
            "post_push": [
                HookPlan(package_id="beta", hook_name="post_push", command="echo beta post", cwd=Path("/repo")),
            ],
        },
        target_plans=[
            TargetPlan(
                package_id="beta",
                target_name="config",
                repo_path=beta_repo_path,
                live_path=beta_live_path,
                action="create",
                target_kind="file",
                projection_kind="raw",
                desired_bytes=b"beta live\n",
            ),
        ],
    )
    session = build_execution_session([alpha_plan, beta_plan], operation="push")

    def fake_run(request):
        command = request.command.source
        stdout_by_command = {
            "echo alpha guard 1": CommandResult(exit_code=100, stdout=b"alpha guard 1\n"),
            "echo alpha guard 2": CommandResult(exit_code=0, stdout=b"alpha guard 2\n"),
            "echo beta guard": CommandResult(exit_code=0, stdout=b"beta guard\n"),
            "echo beta pre": CommandResult(exit_code=0, stdout=b"beta pre\n"),
            "echo beta post": CommandResult(exit_code=0, stdout=b"beta post\n"),
        }
        if command not in stdout_by_command:
            raise AssertionError(f"unexpected command: {command}")
        return stdout_by_command[command]

    runtime = MemoryCommandRuntime([fake_run] * 4)

    result = execute_session(session, stream_output=False, command_runtime=runtime)

    assert result.status == "ok"
    alpha_result, beta_result = result.packages
    assert alpha_result.status == "skipped"
    assert alpha_result.skip_reason == "guard"
    assert [step.status for step in alpha_result.steps] == ["skipped", "skipped", "skipped", "skipped", "skipped"]
    assert alpha_result.steps[0].skip_reason == "guard"
    assert alpha_result.steps[1].skip_reason == "guard"
    recorded_commands = [request.command.source for request in runtime.requests]
    assert "echo alpha guard 2" not in recorded_commands
    assert "echo alpha pre" not in recorded_commands
    assert "echo alpha post" not in recorded_commands
    assert beta_result.status == "ok"
    assert [step.status for step in beta_result.steps] == ["ok", "ok", "ok", "ok"]
    assert beta_live_path.read_text(encoding="utf-8") == "beta live\n"
    assert not alpha_live_path.exists()


def test_execute_session_fails_when_live_target_becomes_symlink_before_execution(
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "repo-file"
    repo_path.write_text("repo\n", encoding="utf-8")

    live_root = tmp_path / "live"
    live_root.mkdir()
    real_live_path = live_root / "config-real.txt"
    real_live_path.write_text("live\n", encoding="utf-8")
    live_path = live_root / "config.txt"

    plan = make_package_plan(
        operation="push",
        repo_name="fixture",
        package_id="app",
        requested_profile="default",
        variables={},
        hooks={},
        target_plans=[
            TargetPlan(
                package_id="app",
                target_name="config",
                repo_path=repo_path,
                live_path=live_path,
                action="create",
                target_kind="file",
                projection_kind="raw",
                desired_bytes=b"repo\n",
            )
        ],
    )
    session = build_execution_session([plan], operation="push")

    live_path.symlink_to(real_live_path)

    result = execute_session(session, stream_output=False)

    assert result.status == "failed"
    assert result.packages[0].steps[0].status == "failed"
    assert result.packages[0].steps[0].error is not None
    assert "live target path is a symlink" in result.packages[0].steps[0].error
    assert real_live_path.read_text(encoding="utf-8") == "live\n"


def test_execute_session_allows_live_target_symlink_replacement_when_explicitly_approved(
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "repo-file"
    repo_path.write_text("repo\n", encoding="utf-8")

    live_root = tmp_path / "live"
    live_root.mkdir()
    real_live_path = live_root / "config-real.txt"
    real_live_path.write_text("live\n", encoding="utf-8")
    live_path = live_root / "config.txt"
    live_path.symlink_to(real_live_path)

    plan = make_package_plan(
        operation="push",
        repo_name="fixture",
        package_id="app",
        requested_profile="default",
        variables={},
        hooks={},
        target_plans=[
            TargetPlan(
                package_id="app",
                target_name="config",
                repo_path=repo_path,
                live_path=live_path,
                action="create",
                target_kind="file",
                projection_kind="raw",
                desired_bytes=b"repo\n",
                live_path_is_symlink=True,
                live_path_symlink_target=str(real_live_path),
                allow_live_path_symlink_replace=True,
            )
        ],
    )
    session = build_execution_session([plan], operation="push")

    result = execute_session(session, stream_output=False)

    assert result.status == "ok"
    assert live_path.is_file()
    assert not live_path.is_symlink()
    assert live_path.read_text(encoding="utf-8") == "repo\n"
    assert real_live_path.read_text(encoding="utf-8") == "live\n"


def test_execute_session_follows_live_target_symlink_when_configured(
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "repo-file"
    repo_path.write_text("repo\n", encoding="utf-8")

    live_root = tmp_path / "live"
    live_root.mkdir()
    real_live_path = live_root / "config-real.txt"
    real_live_path.write_text("live\n", encoding="utf-8")
    live_path = live_root / "config.txt"
    live_path.symlink_to(real_live_path)

    plan = make_package_plan(
        operation="push",
        repo_name="fixture",
        package_id="app",
        requested_profile="default",
        variables={},
        hooks={},
        target_plans=[
            TargetPlan(
                package_id="app",
                target_name="config",
                repo_path=repo_path,
                live_path=live_path,
                action="create",
                target_kind="file",
                projection_kind="raw",
                desired_bytes=b"repo\n",
                live_path_is_symlink=True,
                live_path_symlink_target=str(real_live_path),
                file_symlink_mode="follow",
            )
        ],
    )
    session = build_execution_session([plan], operation="push")

    result = execute_session(session, stream_output=False)

    assert result.status == "ok"
    assert live_path.is_symlink()
    assert live_path.read_text(encoding="utf-8") == "repo\n"
    assert real_live_path.read_text(encoding="utf-8") == "repo\n"


def test_execute_session_fails_tty_hook_without_terminal(
    monkeypatch,
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    monkeypatch.setattr("sys.stderr.isatty", lambda: False)
    runtime = MemoryCommandRuntime()

    plan = make_package_plan(
        operation="push",
        repo_name="fixture",
        package_id="app",
        requested_profile="default",
        variables={},
        hooks={
            "pre_push": [
                HookPlan(package_id="app", hook_name="pre_push", command="echo tty", cwd=Path("/repo/app"), io="tty"),
            ],
        },
        target_plans=[],
    )
    session = build_execution_session([plan], operation="push")

    result = execute_session(session, stream_output=False, command_runtime=runtime)

    assert result.status == "failed"
    assert result.packages[0].steps[0].error == "hook command io 'tty' requires an interactive terminal"
    assert runtime.requests == []


@pytest.mark.parametrize("interactive", [False, True])
def test_execute_session_translates_hook_options_to_runtime_request(
    interactive: bool,
    monkeypatch,
) -> None:
    runtime = MemoryCommandRuntime([CommandResult(exit_code=7, stdout=b"out", stderr=b"err")])
    monkeypatch.setattr(execution, "request_sudo", lambda reason=None: None)
    monkeypatch.setattr(execution, "_require_interactive_terminal_for_hook", lambda: None)
    plan = make_package_plan(
        operation="push",
        repo_name="fixture",
        package_id="app",
        requested_profile="default",
        variables={},
        hooks={
            "pre_push": [
                HookPlan(
                    package_id="app",
                    hook_name="pre_push",
                    command="printf result",
                    cwd=Path("/command-cwd"),
                    env={"X": "1"},
                    io="tty" if interactive else "pipe",
                    elevation="lease",
                )
            ]
        },
        target_plans=[],
    )

    result = execute_session(
        build_execution_session([plan], operation="push"),
        stream_output=not interactive,
        command_runtime=runtime,
    )

    assert result.status == "failed"
    assert result.packages[0].steps[0].stdout == "out"
    assert result.packages[0].steps[0].stderr == "err"
    request = runtime.requests[0]
    assert request.command == ShellCommand("printf result")
    assert request.cwd == Path("/command-cwd")
    assert request.env["X"] == "1"
    assert request.io == ("tty" if interactive else "pipe")
    assert request.stream_output is (not interactive)
    assert request.elevation == "lease"


def test_execute_session_marks_command_exit_130_as_interrupted() -> None:
    runtime = MemoryCommandRuntime([CommandResult(exit_code=130)])
    plan = make_package_plan(
        operation="push",
        repo_name="fixture",
        package_id="app",
        requested_profile="default",
        variables={},
        hooks={
            "pre_push": [
                HookPlan(package_id="app", hook_name="pre_push", command="python hook.py", cwd=Path("/repo/app")),
            ],
        },
        target_plans=[],
    )

    result = execute_session(
        build_execution_session([plan], operation="push"),
        stream_output=False,
        command_runtime=runtime,
    )

    assert result.status == "interrupted"
    assert result.exit_code == 130
    step_result = result.packages[0].steps[0]
    assert step_result.status == "interrupted"
    assert step_result.exit_code == 130
    assert step_result.error is None


def test_execute_session_uses_sudo_writer_for_system_live_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo_root = tmp_path / "repo"
    repo_path = repo_root / "packages" / "app" / "config.txt"
    repo_path.parent.mkdir(parents=True)
    repo_path.write_text("repo\n", encoding="utf-8")
    live_path = Path("/etc/sddm.conf")

    plan = make_package_plan(
        operation="push",
        repo_name="fixture",
        package_id="app",
        requested_profile="default",
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
                desired_bytes=b"repo\n",
            )
        ],
    )
    session = build_execution_session([plan], operation="push")

    recorded_calls: list[tuple[Path, bytes, Path | None, int | None]] = []
    monkeypatch.setattr("dotman.execution.request_sudo", lambda reason=None: None)
    monkeypatch.setattr("dotman.execution.needs_sudo_for_write", lambda path: path == live_path)
    monkeypatch.setattr(
        "dotman.execution.sudo_write_bytes_atomic",
        lambda path, content, restore_root=None, mode=None: recorded_calls.append((Path(path), content, restore_root, mode)),
    )

    result = execute_session(session, stream_output=False)

    assert result.status == "ok"
    assert recorded_calls == [(live_path, b"repo\n", None, None)]


def test_execute_session_requests_sudo_before_privileged_execution_steps(
    monkeypatch,
) -> None:
    recorded_events: list[str] = []
    target_plan = TargetPlan(
        package_id="app",
        target_name="config",
        repo_path=Path("/repo/app.conf"),
        live_path=Path("/etc/sddm.conf"),
        action="update",
        target_kind="file",
        projection_kind="raw",
        desired_bytes=b"repo\n",
    )

    plan = execution.ExecutionSession(
        operation="push",
        package_units=(
            execution.PackageExecutionUnit(
                repo_name="fixture",
                selection_label="fixture:app@default",
                requested_profile="default",
                package_id="app",
                steps=(
                    execution.ExecutionStep(
                        package_id="app",
                        package_plan=make_package_plan(
                            operation="push",
                            repo_name="fixture",
                            package_id="app",
                            requested_profile="default",
                            variables={},
                            hooks={},
                            target_plans=[target_plan],
                        ),
                        kind="target",
                        action="update",
                        target_plan=target_plan,
                        privileged=True,
                    ),
                ),
            ),
        ),
        requires_privilege=True,
    )

    monkeypatch.setattr(
        "dotman.execution.request_sudo",
        lambda reason=None: recorded_events.append(f"sudo:{reason}"),
    )
    monkeypatch.setattr(
        "dotman.execution._execute_step",
        lambda step, *, stream_output, assume_yes: (
            recorded_events.append("step")
            or execution.ExecutionStepResult(step=step, status="ok")
        ),
    )

    result = execute_session(
        plan,
        stream_output=False,
        on_package_start=lambda _package: recorded_events.append("package"),
    )

    assert result.status == "ok"
    assert recorded_events == ["sudo:write protected path: /etc/sddm.conf", "package", "step"]


def test_execute_session_keeps_hooks_unprivileged_when_target_step_needs_sudo(
    monkeypatch,
) -> None:
    plan = make_package_plan(
        operation="push",
        repo_name="fixture",
        package_id="app",
        requested_profile="default",
        variables={},
        hooks={
            "guard_push": [HookPlan(package_id="app", hook_name="guard_push", command="echo guard", cwd=Path("/repo"))],
            "pre_push": [HookPlan(package_id="app", hook_name="pre_push", command="echo pre", cwd=Path("/repo"))],
            "post_push": [HookPlan(package_id="app", hook_name="post_push", command="echo post", cwd=Path("/repo"))],
        },
        target_plans=[
            TargetPlan(
                package_id="app",
                target_name="config",
                repo_path=Path("/repo/app.conf"),
                live_path=Path("/etc/sddm.conf"),
                action="create",
                target_kind="file",
                projection_kind="raw",
                desired_bytes=b"repo\n",
            )
        ],
    )
    monkeypatch.setattr("dotman.execution.needs_sudo_for_write", lambda path: path == Path("/etc/sddm.conf"))
    session = build_execution_session([plan], operation="push")

    recorded_events: list[tuple[str, object]] = []
    monkeypatch.setattr(
        "dotman.execution.request_sudo",
        lambda reason=None: recorded_events.append((f"sudo:{reason}", True)),
    )

    def record_command(request):
        recorded_events.append((request.command.source, request.elevation))
        return CommandResult(exit_code=0)

    runtime = MemoryCommandRuntime([record_command] * 3)
    monkeypatch.setattr(
        "dotman.execution._execute_target_step",
        lambda step: recorded_events.append((step.action, step.privileged)),
    )

    result = execute_session(session, stream_output=False, command_runtime=runtime)

    assert result.status == "ok"
    assert ("sudo:write protected path: /etc/sddm.conf", True) in recorded_events
    assert ("create", True) in recorded_events
    assert ("echo guard", "none") in recorded_events
    assert ("echo pre", "none") in recorded_events
    assert ("echo post", "none") in recorded_events


def test_write_bytes_atomic_cleans_up_temp_file_after_failed_replace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target_path = tmp_path / "config.txt"
    temp_name_prefix = ".dotman-"
    temp_name_suffix = ".tmp"

    def failing_replace(self: Path, target: Path) -> Path:
        raise RuntimeError("boom")

    monkeypatch.setattr(Path, "replace", failing_replace)

    with pytest.raises(RuntimeError, match="boom"):
        execution.write_bytes_atomic(target_path, b"payload\n")

    leftover_temp_files = list(tmp_path.glob(f"{temp_name_prefix}*{temp_name_suffix}"))
    assert leftover_temp_files == []


def test_write_bytes_atomic_removes_stale_dotman_temp_files_before_write(tmp_path: Path) -> None:
    stale_temp_file = tmp_path / f".dotman-999999-{'deadbeef' * 4}.tmp"
    stale_temp_file.write_text("stale\n", encoding="utf-8")

    target_path = tmp_path / "config.txt"
    execution.write_bytes_atomic(target_path, b"payload\n")

    assert target_path.read_text(encoding="utf-8") == "payload\n"
    assert not stale_temp_file.exists()


def test_read_bytes_uses_sudo_when_direct_read_is_denied(tmp_path: Path, monkeypatch) -> None:
    target_path = tmp_path / "protected.txt"
    target_path.write_text("payload\n", encoding="utf-8")
    target_path.chmod(0o000)

    runtime = MemoryCommandRuntime(
        [CommandResult(exit_code=0), CommandResult(exit_code=0, stdout=b"payload\n")]
    )
    monkeypatch.setattr(file_access, "current_command_runtime", lambda: runtime)

    with file_access.sudo_session():
        assert file_access.read_bytes(target_path) == b"payload\n"


def test_request_sudo_emits_user_facing_reason_only_when_password_prompt_is_needed(monkeypatch, capsys) -> None:
    runtime = MemoryCommandRuntime([CommandResult(exit_code=0), CommandResult(exit_code=0)])
    monkeypatch.setattr(file_access, "current_command_runtime", lambda: runtime)

    with file_access.sudo_session():
        file_access.request_sudo("list protected directory: /etc/sddm.conf.d")
        file_access.request_sudo("write protected path: /etc/sddm.conf")

    captured = capsys.readouterr()
    assert captured.err == "[sudo] password required to list protected directory: /etc/sddm.conf.d\n"


def test_request_sudo_emits_user_facing_reason_again_when_cached_lease_expires(monkeypatch, capsys) -> None:
    runtime = MemoryCommandRuntime(
        [CommandResult(exit_code=0), CommandResult(exit_code=1), CommandResult(exit_code=0)]
    )
    monkeypatch.setattr(file_access, "current_command_runtime", lambda: runtime)

    with file_access.sudo_session():
        file_access.request_sudo("list protected directory: /etc/sddm.conf.d")
        file_access.request_sudo("write protected path: /etc/sddm.conf")

    captured = capsys.readouterr()
    assert captured.err == (
        "[sudo] password required to list protected directory: /etc/sddm.conf.d\n"
        "[sudo] password required to write protected path: /etc/sddm.conf\n"
    )


def test_request_sudo_preserves_authentication_interruption(monkeypatch) -> None:
    runtime = MemoryCommandRuntime([CommandResult(exit_code=130)])
    monkeypatch.setattr(file_access, "current_command_runtime", lambda: runtime)

    with file_access.sudo_session(), pytest.raises(KeyboardInterrupt):
        file_access.request_sudo("write protected path")


def test_request_sudo_without_operation_scope_does_not_reuse_a_stale_runtime(monkeypatch) -> None:
    first_runtime = MemoryCommandRuntime([CommandResult(exit_code=0)])
    second_runtime = MemoryCommandRuntime([CommandResult(exit_code=0)])
    active_runtime = [first_runtime]
    monkeypatch.setattr(file_access.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(file_access, "current_command_runtime", lambda: active_runtime[0])

    try:
        file_access.request_sudo("first planning command")
        active_runtime[0] = second_runtime
        file_access.request_sudo("second planning command")
    finally:
        file_access._cleanup_active_sudo_lease()

    assert [request.command.arguments for request in first_runtime.requests] == [("sudo", "-v")]
    assert [request.command.arguments for request in second_runtime.requests] == [("sudo", "-v")]


def test_sudo_lease_keepalive_uses_runtime_captured_on_creation(monkeypatch) -> None:
    runtime = MemoryCommandRuntime([CommandResult(exit_code=0)])
    default_runtime_requests = []
    monkeypatch.setattr(
        command_runtime_module.DEFAULT_COMMAND_RUNTIME,
        "run",
        lambda request: default_runtime_requests.append(request) or CommandResult(exit_code=0),
    )

    with command_runtime_session(runtime):
        lease = file_access._SudoLease()

    wait_results = iter((False, True))
    lease._stop_event = SimpleNamespace(wait=lambda timeout: next(wait_results))
    keepalive_thread = Thread(target=lease._keepalive_loop)
    keepalive_thread.start()
    keepalive_thread.join()

    assert [request.command.arguments for request in runtime.requests] == [
        ("sudo", "-n", "true")
    ]
    assert default_runtime_requests == []
