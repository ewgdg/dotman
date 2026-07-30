from __future__ import annotations

import stat
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
from dotman.models import DirectoryPlanItem, HookCommandSpec, HookPlan, OperationPlan, TargetPlan
from tests.helpers import (
    make_package_plan,
    single_package_plan,
    write_named_manager_config,
    write_shared_stack_repo,
    write_single_repo_config,
)


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


def test_build_execution_session_does_not_mark_custom_reconcile_steps_privileged(monkeypatch) -> None:
    monkeypatch.setattr("dotman.execution.needs_sudo_for_read", lambda path: True)

    plan = make_package_plan(
        operation="pull",
        repo_name="fixture",
        package_id="app",
        requested_profile="default",
        variables={},
        hooks={
            "guard_pull": [HookPlan(package_id="app", hook_name="guard_pull", command="echo guard", cwd=Path("/repo"))],
            "post_pull": [HookPlan(package_id="app", hook_name="post_pull", command="echo post", cwd=Path("/repo"))],
        },
        target_plans=[
            TargetPlan(
                package_id="app",
                target_name="config",
                repo_path=Path("/repo/app.conf"),
                live_path=Path("/etc/sddm.conf"),
                action="update",
                target_kind="file",
                projection_kind="raw",
                reconcile=HookCommandSpec(run="sh hooks/reconcile.sh"),
            )
        ],
    )

    session = build_execution_session([plan], operation="pull")

    assert session.requires_privilege is False
    assert [step.privileged for step in session.packages[0].steps] == [False, False, False]


def test_build_execution_session_marks_explicit_privileged_reconcile(monkeypatch) -> None:
    monkeypatch.setattr("dotman.execution.needs_sudo_for_read", lambda path: False)

    plan = make_package_plan(
        operation="pull",
        repo_name="fixture",
        package_id="app",
        requested_profile="default",
        variables={},
        hooks={},
        target_plans=[
            TargetPlan(
                package_id="app",
                target_name="config",
                repo_path=Path("/repo/app.conf"),
                live_path=Path("/etc/sddm.conf"),
                action="update",
                target_kind="file",
                projection_kind="raw",
                reconcile=HookCommandSpec(run="sh hooks/reconcile.sh", elevation="root"),
            )
        ],
    )

    session = build_execution_session([plan], operation="pull")

    assert session.requires_privilege is True
    assert [step.privileged for step in session.packages[0].steps] == [True]


def test_build_execution_session_marks_privileged_reconcile_fallback(monkeypatch) -> None:
    monkeypatch.setattr("dotman.execution.needs_sudo_for_read", lambda path: False)

    plan = make_package_plan(
        operation="pull",
        repo_name="fixture",
        package_id="app",
        requested_profile="default",
        variables={},
        hooks={},
        target_plans=[
            TargetPlan(
                package_id="app",
                target_name="config",
                repo_path=Path("/repo/app.conf"),
                live_path=Path("/etc/sddm.conf"),
                action="update",
                target_kind="file",
                projection_kind="raw",
                capture_command="capture-command",
                reconcile=HookCommandSpec(run="sh hooks/reconcile.sh", elevation="root"),
            )
        ],
    )

    session = build_execution_session([plan], operation="pull")

    assert session.requires_privilege is True
    assert [step.privileged for step in session.packages[0].steps] == [True]
    assert execution._execution_session_sudo_reason(session) == "execute privileged reconcile for fixture:app.config"



def test_build_execution_session_prefers_capture_step_when_capture_and_reconcile_both_defined() -> None:
    plan = make_package_plan(
        operation="pull",
        repo_name="fixture",
        package_id="app",
        requested_profile="default",
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
                capture_command="printf 'captured\\n'",
                reconcile=HookCommandSpec(run="printf 'reconcile\\n'"),
            )
        ],
    )

    session = build_execution_session([plan], operation="pull")

    assert [step.action for step in session.packages[0].steps] == ["update_repo"]


def test_build_execution_session_does_not_add_pull_chmod_steps() -> None:
    plan = make_package_plan(
        operation="pull",
        repo_name="fixture",
        package_id="app",
        requested_profile="default",
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


def test_build_execution_session_keeps_hook_only_packages_when_hooks_are_finalized() -> None:
    for operation, hook_name_prefix in (("push", "push"), ("pull", "pull")):
        plan = make_package_plan(
            operation=operation,
            repo_name="fixture",
            package_id="app",
            requested_profile="default",
            variables={},
            hooks={
                f"guard_{hook_name_prefix}": [
                    HookPlan(package_id="app", hook_name=f"guard_{hook_name_prefix}", command=f"echo guard {hook_name_prefix}", cwd=Path("/repo")),
                ],
                f"pre_{hook_name_prefix}": [
                    HookPlan(package_id="app", hook_name=f"pre_{hook_name_prefix}", command=f"echo pre {hook_name_prefix}", cwd=Path("/repo")),
                ],
                f"post_{hook_name_prefix}": [
                    HookPlan(package_id="app", hook_name=f"post_{hook_name_prefix}", command=f"echo post {hook_name_prefix}", cwd=Path("/repo")),
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

        session = build_execution_session([plan], operation=operation)

        assert [unit.package_id for unit in session.packages] == ["app"]
        assert [step.action for step in session.packages[0].steps] == [
            f"guard_{hook_name_prefix}",
            f"pre_{hook_name_prefix}",
            f"post_{hook_name_prefix}",
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


def test_execute_session_soft_skips_pull_package_on_guard_exit_100_and_continues_next_package(
    tmp_path: Path,
    monkeypatch,
) -> None:
    alpha_repo_path = tmp_path / "alpha.repo"
    beta_repo_path = tmp_path / "beta.repo"
    alpha_live_path = tmp_path / "alpha.live"
    beta_live_path = tmp_path / "beta.live"
    alpha_repo_path.write_text("alpha repo\n", encoding="utf-8")
    beta_repo_path.write_text("beta repo\n", encoding="utf-8")
    alpha_live_path.write_text("alpha live\n", encoding="utf-8")
    beta_live_path.write_text("beta live\n", encoding="utf-8")

    alpha_plan = make_package_plan(
        operation="pull",
        repo_name="fixture",
        package_id="alpha",
        requested_profile="default",
        source_selector="stack",
        variables={},
        hooks={
            "guard_pull": [
                HookPlan(package_id="alpha", hook_name="guard_pull", command="echo alpha guard 1", cwd=Path("/repo")),
                HookPlan(package_id="alpha", hook_name="guard_pull", command="echo alpha guard 2", cwd=Path("/repo")),
            ],
            "pre_pull": [
                HookPlan(package_id="alpha", hook_name="pre_pull", command="echo alpha pre", cwd=Path("/repo")),
            ],
            "post_pull": [
                HookPlan(package_id="alpha", hook_name="post_pull", command="echo alpha post", cwd=Path("/repo")),
            ],
        },
        target_plans=[
            TargetPlan(
                package_id="alpha",
                target_name="config",
                repo_path=alpha_repo_path,
                live_path=alpha_live_path,
                action="update",
                target_kind="file",
                projection_kind="raw",
            ),
        ],
    )
    beta_plan = make_package_plan(
        operation="pull",
        repo_name="fixture",
        package_id="beta",
        requested_profile="default",
        source_selector="stack",
        variables={},
        hooks={
            "guard_pull": [
                HookPlan(package_id="beta", hook_name="guard_pull", command="echo beta guard", cwd=Path("/repo")),
            ],
            "pre_pull": [
                HookPlan(package_id="beta", hook_name="pre_pull", command="echo beta pre", cwd=Path("/repo")),
            ],
            "post_pull": [
                HookPlan(package_id="beta", hook_name="post_pull", command="echo beta post", cwd=Path("/repo")),
            ],
        },
        target_plans=[
            TargetPlan(
                package_id="beta",
                target_name="config",
                repo_path=beta_repo_path,
                live_path=beta_live_path,
                action="update",
                target_kind="file",
                projection_kind="raw",
            ),
        ],
    )
    session = build_execution_session([alpha_plan, beta_plan], operation="pull")

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
    assert alpha_repo_path.read_text(encoding="utf-8") == "alpha repo\n"
    assert beta_repo_path.read_text(encoding="utf-8") == "beta live\n"


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


def test_execute_session_runs_tty_reconcile_steps_with_terminal_passthrough(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo_path = tmp_path / "repo-file"
    live_path = tmp_path / "live-file"
    repo_path.write_text("repo\n", encoding="utf-8")
    live_path.write_text("live\n", encoding="utf-8")

    plan = make_package_plan(
        operation="pull",
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
                reconcile=HookCommandSpec(
                    run="dotman reconcile editor --repo-path \"$DOTMAN_REPO_PATH\" --live-path \"$DOTMAN_LIVE_PATH\"",
                    io="tty",
                ),
                command_env={
                    "DOTMAN_REPO_PATH": str(repo_path),
                    "DOTMAN_LIVE_PATH": str(live_path),
                },
            )
        ],
    )
    session = build_execution_session([plan], operation="pull")

    runtime = MemoryCommandRuntime([CommandResult(exit_code=0)])
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("sys.stderr.isatty", lambda: True)

    result = execute_session(session, stream_output=True, command_runtime=runtime)

    assert result.status == "ok"
    assert result.packages[0].steps[0].step.action == "reconcile"
    request = runtime.requests[0]
    assert request.command == ShellCommand(
        'dotman reconcile editor --repo-path "$DOTMAN_REPO_PATH" --live-path "$DOTMAN_LIVE_PATH"'
    )
    assert request.cwd is None
    assert request.io == "tty"
    assert request.env["DOTMAN_REPO_PATH"] == str(repo_path)
    assert request.env["DOTMAN_LIVE_PATH"] == str(live_path)


def test_execute_session_runs_builtin_jinja_reconcile_helper(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo_path = tmp_path / "repo-file"
    live_path = tmp_path / "live-file"
    repo_path.write_text("repo\n", encoding="utf-8")
    live_path.write_text("live\n", encoding="utf-8")

    plan = make_package_plan(
        operation="pull",
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
                reconcile=HookCommandSpec(run="jinja", io="tty"),
                review_before_bytes=b"repo planning view\n",
                review_after_bytes=b"live planning view\n",
            )
        ],
    )
    session = build_execution_session([plan], operation="pull")

    recorded: dict[str, object] = {}

    def fake_run_jinja_reconcile(
        *,
        repo_path: str,
        live_path: str,
        review_repo_path: str | None = None,
        review_live_path: str | None = None,
        editor: str | None = None,
        assume_yes: bool = False,
    ) -> int:
        recorded["repo_path"] = repo_path
        recorded["live_path"] = live_path
        recorded["review_repo_path"] = review_repo_path
        recorded["review_live_path"] = review_live_path
        recorded["editor"] = editor
        recorded["assume_yes"] = assume_yes
        assert review_repo_path is not None
        assert review_live_path is not None
        assert Path(review_repo_path).read_text(encoding="utf-8") == "repo planning view\n"
        assert Path(review_live_path).read_text(encoding="utf-8") == "live planning view\n"
        return 0

    monkeypatch.setattr("dotman.execution.run_jinja_reconcile", fake_run_jinja_reconcile)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("sys.stderr.isatty", lambda: True)

    result = execute_session(session, stream_output=True, assume_yes=True)

    assert result.status == "ok"
    assert result.packages[0].steps[0].step.action == "reconcile"
    assert recorded["repo_path"] == str(repo_path)
    assert recorded["live_path"] == str(live_path)
    assert recorded["editor"] is None
    assert recorded["assume_yes"] is True



def test_execute_session_fails_tty_reconcile_without_terminal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo_path = tmp_path / "repo-file"
    live_path = tmp_path / "live-file"
    repo_path.write_text("repo\n", encoding="utf-8")
    live_path.write_text("live\n", encoding="utf-8")

    plan = make_package_plan(
        operation="pull",
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
                reconcile=HookCommandSpec(
                    run="dotman reconcile editor --repo-path \"$DOTMAN_REPO_PATH\" --live-path \"$DOTMAN_LIVE_PATH\"",
                    io="tty",
                ),
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
    assert result.packages[0].steps[0].error == "reconcile io 'tty' requires an interactive terminal"


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



def test_execute_session_restores_repo_path_access_for_pull_updates_run_via_sudo(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo_root = tmp_path / "repo"
    repo_path = repo_root / "packages" / "app" / "config.txt"
    repo_path.parent.mkdir(parents=True)
    live_path = tmp_path / "live.txt"
    live_path.write_text("live\n", encoding="utf-8")

    plan = make_package_plan(
        operation="pull",
        repo_name="fixture",
        package_id="app",
        requested_profile="default",
        variables={},
        hooks={},
        repo_root=repo_root,
        target_plans=[
            TargetPlan(
                package_id="app",
                target_name="config",
                repo_path=repo_path,
                live_path=live_path,
                action="update",
                target_kind="file",
                projection_kind="raw",
            )
        ],
    )
    session = build_execution_session([plan], operation="pull")

    recorded_chown_calls: list[tuple[Path, int, int]] = []
    monkeypatch.setattr("dotman.execution.os.geteuid", lambda: 0)
    monkeypatch.setenv("SUDO_UID", "1234")
    monkeypatch.setenv("SUDO_GID", "5678")
    monkeypatch.setattr(
        "dotman.execution.os.chown",
        lambda path, uid, gid: recorded_chown_calls.append((Path(path), uid, gid)),
    )

    result = execute_session(session, stream_output=False)

    assert result.status == "ok"
    assert repo_path.read_text(encoding="utf-8") == "live\n"
    assert recorded_chown_calls == [
        (repo_path, 1234, 5678),
        (repo_path.parent, 1234, 5678),
        (repo_path.parent.parent, 1234, 5678),
        (repo_root, 1234, 5678),
    ]



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


def test_execute_session_passes_directory_pull_executable_bit_to_privileged_repo_write(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo_root = tmp_path / "repo"
    repo_path = repo_root / "packages" / "app" / "files" / "config" / "script.sh"
    live_path = tmp_path / "home" / ".config" / "app" / "script.sh"
    repo_path.parent.mkdir(parents=True)
    live_path.parent.mkdir(parents=True)
    repo_path.write_text("old\n", encoding="utf-8")
    repo_path.chmod(0o644)
    live_path.write_text("live\n", encoding="utf-8")
    live_path.chmod(0o755)

    plan = make_package_plan(
        operation="pull",
        repo_name="fixture",
        package_id="app",
        requested_profile="default",
        variables={},
        hooks={},
        repo_root=repo_root,
        target_plans=[
            TargetPlan(
                package_id="app",
                target_name="config",
                repo_path=repo_path.parent,
                live_path=live_path.parent,
                action="update",
                target_kind="directory",
                projection_kind="raw",
                directory_items=(
                    DirectoryPlanItem(
                        relative_path="script.sh",
                        action="update",
                        repo_path=repo_path,
                        live_path=live_path,
                    ),
                ),
            )
        ],
    )
    session = build_execution_session([plan], operation="pull")

    recorded_calls: list[tuple[Path, bytes, Path | None, int | None]] = []
    monkeypatch.setattr("dotman.execution.needs_sudo_for_write", lambda path: path == repo_path)
    monkeypatch.setattr(
        "dotman.execution.sudo_write_bytes_atomic",
        lambda path, content, restore_root=None, mode=None: recorded_calls.append((Path(path), content, restore_root, mode)),
    )

    result = execute_session(session, stream_output=False)

    assert result.status == "ok"
    assert recorded_calls == [(repo_path, b"live\n", repo_root, 0o755)]



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


def test_restore_repo_path_access_adds_owner_write_bits_for_repo_files_and_dirs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo_root = tmp_path / "repo"
    repo_path = repo_root / "packages" / "app" / "config.txt"
    repo_path.parent.mkdir(parents=True)
    repo_path.write_text("repo\n", encoding="utf-8")
    repo_path.chmod(0o400)
    repo_path.parent.chmod(0o500)

    recorded_chown_calls: list[tuple[Path, int, int]] = []
    monkeypatch.setattr("dotman.execution.os.geteuid", lambda: 0)
    monkeypatch.setenv("SUDO_UID", "1234")
    monkeypatch.setenv("SUDO_GID", "5678")
    monkeypatch.setattr(
        "dotman.execution.os.chown",
        lambda path, uid, gid: recorded_chown_calls.append((Path(path), uid, gid)),
    )

    execution._restore_repo_path_access_for_invoking_user(repo_path, repo_root=repo_root)

    assert stat.S_IMODE(repo_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(repo_path.parent.stat().st_mode) == 0o700
    assert recorded_chown_calls == [
        (repo_path, 1234, 5678),
        (repo_path.parent, 1234, 5678),
        (repo_path.parent.parent, 1234, 5678),
        (repo_root, 1234, 5678),
    ]



def test_execute_session_keeps_batch_reconcile_on_piped_command_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo_path = tmp_path / "repo-file"
    live_path = tmp_path / "live-file"
    repo_path.write_text("repo\n", encoding="utf-8")
    live_path.write_text("live\n", encoding="utf-8")

    plan = make_package_plan(
        operation="pull",
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
                reconcile=HookCommandSpec(run="printf 'batch reconcile\\n'"),
                command_env={
                    "DOTMAN_REPO_PATH": str(repo_path),
                    "DOTMAN_LIVE_PATH": str(live_path),
                },
            )
        ],
    )
    session = build_execution_session([plan], operation="pull")

    runtime = MemoryCommandRuntime(
        [CommandResult(exit_code=0, stdout=b"batch reconcile\n")]
    )

    result = execute_session(session, stream_output=True, command_runtime=runtime)

    assert result.status == "ok"
    assert result.packages[0].steps[0].stdout == "batch reconcile\n"
    assert runtime.requests[0].command == ShellCommand("printf 'batch reconcile\\n'")
    assert runtime.requests[0].io == "pipe"


def test_execute_session_runs_custom_reconcile_without_auto_sudo(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo_path = tmp_path / "repo-file"
    live_path = tmp_path / "live-file"
    repo_path.write_text("repo\n", encoding="utf-8")
    live_path.write_text("live\n", encoding="utf-8")

    plan = make_package_plan(
        operation="pull",
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
                reconcile=HookCommandSpec(run="printf 'batch reconcile\\n'"),
                command_env={
                    "DOTMAN_REPO_PATH": str(repo_path),
                    "DOTMAN_LIVE_PATH": str(live_path),
                },
            )
        ],
    )
    monkeypatch.setattr("dotman.execution.needs_sudo_for_read", lambda path: True)
    session = build_execution_session([plan], operation="pull")

    monkeypatch.setattr(
        "dotman.execution.request_sudo",
        lambda reason=None: (_ for _ in ()).throw(AssertionError(f"unexpected sudo request: {reason}")),
    )
    runtime = MemoryCommandRuntime(
        [CommandResult(exit_code=0, stdout=b"batch reconcile\n")]
    )

    result = execute_session(session, stream_output=False, command_runtime=runtime)

    assert result.status == "ok"
    assert runtime.requests[0].command == ShellCommand("printf 'batch reconcile\\n'")
    assert runtime.requests[0].elevation == "none"


def test_execute_session_uses_explicit_privileged_reconcile_reason_and_runner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo_path = tmp_path / "repo-file"
    live_path = tmp_path / "live-file"
    repo_path.write_text("repo\n", encoding="utf-8")
    live_path.write_text("live\n", encoding="utf-8")

    plan = make_package_plan(
        operation="pull",
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
                reconcile=HookCommandSpec(run="printf 'batch reconcile\n'", elevation="root"),
                command_env={
                    "DOTMAN_REPO_PATH": str(repo_path),
                    "DOTMAN_LIVE_PATH": str(live_path),
                },
            )
        ],
    )
    session = build_execution_session([plan], operation="pull")

    sudo_reasons: list[str | None] = []

    monkeypatch.setattr(
        "dotman.execution.request_sudo",
        lambda reason=None: sudo_reasons.append(reason),
    )
    runtime = MemoryCommandRuntime(
        [CommandResult(exit_code=0, stdout=b"batch reconcile\n")]
    )

    result = execute_session(session, stream_output=False, command_runtime=runtime)

    assert result.status == "ok"
    assert sudo_reasons == ["execute privileged reconcile for fixture:app.config"]
    assert runtime.requests[0].command == ShellCommand("printf 'batch reconcile\n'")
    assert runtime.requests[0].elevation == "root"



@pytest.mark.parametrize("capture_exit_code", [1, 100])
def test_execute_session_falls_back_to_reconcile_when_capture_fails(
    tmp_path: Path,
    monkeypatch,
    capture_exit_code: int,
) -> None:
    repo_path = tmp_path / "repo-file"
    live_path = tmp_path / "live-file"
    repo_path.write_text("repo\n", encoding="utf-8")
    live_path.write_text("live\n", encoding="utf-8")

    plan = make_package_plan(
        operation="pull",
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
                capture_command="capture-command",
                reconcile=HookCommandSpec(run="reconcile-command", io="pipe"),
                review_before_bytes=b"repo planning view\n",
                review_after_bytes=b"capture live planning view\n",
                command_env={
                    "DOTMAN_REPO_PATH": str(repo_path),
                    "DOTMAN_LIVE_PATH": str(live_path),
                },
            )
        ],
    )
    session = build_execution_session([plan], operation="pull")

    recorded: dict[str, object] = {}

    def fake_run(request):
        command = request.command.source
        if command == "capture-command":
            return CommandResult(exit_code=capture_exit_code, stderr=b"capture exploded")
        if command == "reconcile-command":
            recorded["review_repo_text"] = Path(request.env["DOTMAN_REVIEW_REPO_PATH"]).read_text(encoding="utf-8")
            recorded["review_live_text"] = Path(request.env["DOTMAN_REVIEW_LIVE_PATH"]).read_text(encoding="utf-8")
            recorded["reconcile_elevation"] = request.elevation
            repo_path.write_text(live_path.read_text(encoding="utf-8"), encoding="utf-8")
            return CommandResult(exit_code=0, stdout=b"reconciled\n")
        raise AssertionError(f"unexpected command: {command}")

    runtime = MemoryCommandRuntime([fake_run] * 2)

    result = execute_session(session, stream_output=False, command_runtime=runtime)

    assert result.status == "ok"
    assert result.packages[0].steps[0].step.action == "update_repo"
    assert result.packages[0].steps[0].stdout == "reconciled\n"
    assert "capture failed; falling back to reconcile: capture exploded" in result.packages[0].steps[0].stderr
    assert repo_path.read_text(encoding="utf-8") == "live\n"
    assert recorded["review_repo_text"] == "repo planning view\n"
    assert recorded["review_live_text"] == "capture live planning view\n"
    assert recorded["reconcile_elevation"] == "none"


def _write_patch_capture_execution_repo(repo_root: Path) -> None:
    package_root = repo_root / "packages" / "shell"
    (package_root / "files").mkdir(parents=True)
    (repo_root / "profiles").mkdir(parents=True)

    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    (package_root / "files" / "profile").write_text("greeting = {{ vars.greeting }}\n", encoding="utf-8")
    (package_root / "package.toml").write_text(
        "\n".join(
            [
                'id = "shell"',
                "",
                '[vars]',
                'greeting = "hello"',
                "",
                '[targets.profile]',
                'source = "files/profile"',
                'path = "~/.profile"',
                'render = "jinja"',
                'capture = "patch"',
                'pull_view_repo = "render"',
                'pull_view_live = "raw"',
                "",
            ]
        ),
        encoding="utf-8",
    )



def _write_command_patch_capture_execution_repo(repo_root: Path) -> None:
    package_root = repo_root / "packages" / "shell"
    (package_root / "files").mkdir(parents=True)
    (repo_root / "profiles").mkdir(parents=True)

    render_command = 'sed "s/@@greeting@@/$DOTMAN_VAR_greeting/g" "$DOTMAN_SOURCE"'
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    (package_root / "files" / "profile").write_text("greeting = @@greeting@@\n", encoding="utf-8")
    (package_root / "package.toml").write_text(
        "\n".join(
            [
                'id = "shell"',
                "",
                '[vars]',
                'greeting = "hello"',
                "",
                '[targets.profile]',
                'source = "files/profile"',
                'path = "~/.profile"',
                f"render = '{render_command}'",
                'capture = "patch"',
                'pull_view_repo = "render"',
                'pull_view_live = "raw"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_execute_session_uses_review_env_for_patch_capture_and_writes_patched_repo_bytes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _write_patch_capture_execution_repo(repo_root)
    live_path = home / ".profile"
    live_path.write_text("greeting = world\n", encoding="utf-8")

    engine = DotmanEngine.from_config_path(write_named_manager_config(tmp_path, {"fixture": repo_root}))
    plan = single_package_plan(engine, "fixture:shell@default", operation="pull")
    session = build_execution_session([plan], operation="pull")

    recorded: dict[str, object] = {}

    def fake_capture_patch(*, repo_path, project_repo_bytes, review_repo_path=None, review_live_path=None):
        recorded["repo_path"] = repo_path
        recorded["review_repo_path"] = review_repo_path
        recorded["review_live_path"] = review_live_path
        assert review_repo_path is None
        assert review_live_path is None
        assert execution.os.environ["DOTMAN_REVIEW_REPO_PATH"]
        assert execution.os.environ["DOTMAN_REVIEW_LIVE_PATH"]
        assert Path(execution.os.environ["DOTMAN_REVIEW_REPO_PATH"]).read_text(encoding="utf-8") == "greeting = hello\n"
        assert Path(execution.os.environ["DOTMAN_REVIEW_LIVE_PATH"]).read_text(encoding="utf-8") == "greeting = world\n"
        assert project_repo_bytes(b"greeting = world\n") == b"greeting = world\n"
        return b"greeting = world\n"

    monkeypatch.setattr("dotman.execution.capture_patch", fake_capture_patch)

    result = execute_session(session, stream_output=False)

    assert result.status == "ok"
    assert recorded["repo_path"] == str(repo_root / "packages" / "shell" / "files" / "profile")
    assert live_path.read_text(encoding="utf-8") == "greeting = world\n"
    assert (repo_root / "packages" / "shell" / "files" / "profile").read_text(encoding="utf-8") == "greeting = world\n"



def test_execute_session_projects_patch_capture_through_command_renderers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _write_command_patch_capture_execution_repo(repo_root)
    live_path = home / ".profile"
    live_path.write_text("greeting = world\n", encoding="utf-8")

    engine = DotmanEngine.from_config_path(write_named_manager_config(tmp_path, {"fixture": repo_root}))
    plan = single_package_plan(engine, "fixture:shell@default", operation="pull")
    session = build_execution_session([plan], operation="pull")

    result = execute_session(session, stream_output=False)

    assert result.status == "ok"
    assert live_path.read_text(encoding="utf-8") == "greeting = world\n"
    assert (repo_root / "packages" / "shell" / "files" / "profile").read_text(encoding="utf-8") == "greeting = world\n"


def test_execute_session_aborts_when_patch_capture_verification_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _write_patch_capture_execution_repo(repo_root)
    live_path = home / ".profile"
    live_path.write_text("greeting = world\n", encoding="utf-8")

    engine = DotmanEngine.from_config_path(write_named_manager_config(tmp_path, {"fixture": repo_root}))
    plan = single_package_plan(engine, "fixture:shell@default", operation="pull")
    session = build_execution_session([plan], operation="pull")

    monkeypatch.setattr(
        "dotman.execution.capture_patch",
        lambda **kwargs: (_ for _ in ()).throw(
            ValueError("capture verification mismatch: captured bytes do not match the review live bytes")
        ),
    )

    result = execute_session(session, stream_output=False)

    assert result.status == "failed"
    assert result.packages[0].steps[0].status == "failed"
    assert "verification mismatch" in result.packages[0].steps[0].error
    assert (repo_root / "packages" / "shell" / "files" / "profile").read_text(encoding="utf-8") == "greeting = {{ vars.greeting }}\n"
