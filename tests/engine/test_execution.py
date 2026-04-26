from __future__ import annotations

import io
import contextlib
import signal
import stat
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import dotman.execution as execution
from dotman import file_access
from dotman.engine import DotmanEngine
from dotman.execution import build_execution_session, execute_session
from dotman.models import DirectoryPlanItem, HookCommandSpec, HookPlan, OperationPlan, TargetPlan
from tests.helpers import make_package_plan, single_package_plan, write_named_manager_config


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

    recorded_envs: dict[str, dict[str, str]] = {}

    def fake_run_command(*, command, cwd, env, stream_output, interactive, elevation="none"):
        assert env is not None
        recorded_envs[command] = dict(env)
        return 0, "", ""

    monkeypatch.setattr("dotman.execution._run_command", fake_run_command)

    result = execute_session(session, stream_output=False, assume_yes=assume_yes)

    assert result.status == "ok"
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
    recorded: list[str] = []

    def fake_run_command(**kwargs):
        recorded.append(kwargs["command"])
        if kwargs["command"] == "exit 100":
            return 100, "", ""
        return 0, "", ""

    monkeypatch.setattr(execution, "_run_command", fake_run_command)
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

    result = execute_session(build_execution_session([plan], operation="push"), stream_output=False)

    assert result.status == "ok"
    assert recorded == ["exit 100", "echo beta guard", "echo beta pre"]


def test_execute_session_marks_only_tty_hook_commands_interactive(monkeypatch) -> None:
    recorded: list[tuple[str, bool]] = []

    def fake_run_command(*, command, cwd, env, stream_output, interactive, elevation="none"):
        recorded.append((command, interactive))
        return 0, "", ""

    monkeypatch.setattr(execution, "_run_command", fake_run_command)
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

    result = execute_session(build_execution_session([plan], operation="push"), stream_output=False)

    assert result.status == "ok"
    assert recorded == [("echo pipe", False), ("echo tty", True)]



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

    recorded_commands: list[str] = []

    def fake_run_command(*, command, cwd, env, stream_output, interactive, elevation="none"):
        recorded_commands.append(command)
        stdout_by_command = {
            "echo alpha guard 1": (100, "alpha guard 1\n", ""),
            "echo alpha guard 2": (0, "alpha guard 2\n", ""),
            "echo beta guard": (0, "beta guard\n", ""),
            "echo beta pre": (0, "beta pre\n", ""),
            "echo beta post": (0, "beta post\n", ""),
        }
        if command not in stdout_by_command:
            raise AssertionError(f"unexpected command: {command}")
        return stdout_by_command[command]

    monkeypatch.setattr("dotman.execution._run_command", fake_run_command)

    result = execute_session(session, stream_output=False)

    assert result.status == "ok"
    alpha_result, beta_result = result.packages
    assert alpha_result.status == "skipped"
    assert alpha_result.skip_reason == "guard"
    assert [step.status for step in alpha_result.steps] == ["skipped", "skipped", "skipped", "skipped", "skipped"]
    assert alpha_result.steps[0].skip_reason == "guard"
    assert alpha_result.steps[1].skip_reason == "guard"
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

    recorded_commands: list[str] = []

    def fake_run_command(*, command, cwd, env, stream_output, interactive, elevation="none"):
        recorded_commands.append(command)
        stdout_by_command = {
            "echo alpha guard 1": (100, "alpha guard 1\n", ""),
            "echo alpha guard 2": (0, "alpha guard 2\n", ""),
            "echo beta guard": (0, "beta guard\n", ""),
            "echo beta pre": (0, "beta pre\n", ""),
            "echo beta post": (0, "beta post\n", ""),
        }
        if command not in stdout_by_command:
            raise AssertionError(f"unexpected command: {command}")
        return stdout_by_command[command]

    monkeypatch.setattr("dotman.execution._run_command", fake_run_command)

    result = execute_session(session, stream_output=False)

    assert result.status == "ok"
    alpha_result, beta_result = result.packages
    assert alpha_result.status == "skipped"
    assert alpha_result.skip_reason == "guard"
    assert [step.status for step in alpha_result.steps] == ["skipped", "skipped", "skipped", "skipped", "skipped"]
    assert alpha_result.steps[0].skip_reason == "guard"
    assert alpha_result.steps[1].skip_reason == "guard"
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

    recorded: dict[str, object] = {}

    class FakeProcess:
        pid = 12345

        def wait(self):
            return 0

    def fake_popen(command: str, **kwargs):
        recorded["command"] = command
        recorded["kwargs"] = kwargs
        return FakeProcess()

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
    assert "stdin" not in recorded["kwargs"]
    assert "stdout" not in recorded["kwargs"]
    assert "stderr" not in recorded["kwargs"]
    assert recorded["kwargs"]["env"]["DOTMAN_REPO_PATH"] == str(repo_path)
    assert recorded["kwargs"]["env"]["DOTMAN_LIVE_PATH"] == str(live_path)


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
    monkeypatch.setattr(
        "dotman.execution._run_command",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("tty hook should fail before spawning command")),
    )

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

    result = execute_session(session, stream_output=False)

    assert result.status == "failed"
    assert result.packages[0].steps[0].error == "hook command io 'tty' requires an interactive terminal"



def test_run_command_interactive_uses_terminal_runner_without_pipes(monkeypatch) -> None:
    recorded: dict[str, object] = {}

    class FakeProcess:
        pid = 12345

        def wait(self):
            return 0

    def fake_popen(command: str, **kwargs):
        recorded["command"] = command
        recorded["kwargs"] = kwargs
        return FakeProcess()

    events: list[str] = []

    class FakePreserveTerminalState:
        def __enter__(self):
            events.append("enter")

        def __exit__(self, exc_type, exc, traceback):
            events.append("exit")

    monkeypatch.setattr("dotman.execution.subprocess.Popen", fake_popen)
    monkeypatch.setattr("dotman.execution.preserve_terminal_state", lambda: FakePreserveTerminalState())

    exit_code, stdout, stderr = execution._run_command(
        command="printf 'tty\\n'",
        cwd=None,
        env={"X": "1"},
        stream_output=False,
        interactive=True,
    )

    assert (exit_code, stdout, stderr) == (0, "", "")
    assert recorded["command"] == "printf 'tty\\n'"
    assert recorded["kwargs"]["cwd"] is None
    assert recorded["kwargs"]["shell"] is True
    assert recorded["kwargs"]["executable"] == "/bin/sh"
    assert recorded["kwargs"]["env"]["X"] == "1"
    assert "stdin" not in recorded["kwargs"]
    assert "stdout" not in recorded["kwargs"]
    assert "stderr" not in recorded["kwargs"]
    assert events == ["enter", "exit"]


def test_run_command_pipe_uses_devnull_stdin_and_preserves_terminal(monkeypatch) -> None:
    recorded: dict[str, object] = {}
    events: list[str] = []

    class FakePreserveTerminalState:
        def __enter__(self):
            events.append("enter")

        def __exit__(self, exc_type, exc, traceback):
            events.append("exit")

    class FakeProcess:
        pid = 12345
        stdout = io.StringIO("out\n")
        stderr = io.StringIO("err\n")

        def wait(self):
            return 0

    def fake_popen(command: str, **kwargs):
        recorded["command"] = command
        recorded["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr("dotman.execution.subprocess.Popen", fake_popen)
    monkeypatch.setattr("dotman.execution.preserve_terminal_state", lambda: FakePreserveTerminalState())

    exit_code, stdout, stderr = execution._run_command(
        command="printf 'pipe\n'",
        cwd=None,
        env={},
        stream_output=False,
        interactive=False,
    )

    assert (exit_code, stdout, stderr) == (0, "out\n", "err\n")
    kwargs = recorded["kwargs"]
    assert kwargs["stdin"] is execution.subprocess.DEVNULL
    assert kwargs["stdout"] is execution.subprocess.PIPE
    assert kwargs["stderr"] is execution.subprocess.PIPE
    assert kwargs["start_new_session"] is True
    assert events == ["enter", "exit"]


def test_run_command_root_elevation_requests_sudo_and_prefixes_command(monkeypatch) -> None:
    recorded: dict[str, object] = {"sudo_reasons": []}

    class FakeProcess:
        pid = 12345
        stdout = io.StringIO("")
        stderr = io.StringIO("")

        def wait(self):
            return 0

    monkeypatch.setattr("dotman.execution.os.geteuid", lambda: 1000)
    monkeypatch.setattr("dotman.execution.request_sudo", lambda reason=None: recorded["sudo_reasons"].append(reason))
    monkeypatch.setattr("dotman.execution.sudo_prefix_command", lambda command: f"SUDO({command})")
    monkeypatch.setattr("dotman.execution.subprocess.Popen", lambda command, **kwargs: recorded.update({"command": command, "kwargs": kwargs}) or FakeProcess())
    monkeypatch.setattr("dotman.execution.preserve_terminal_state", lambda: contextlib.nullcontext())

    exit_code, stdout, stderr = execution._run_command(
        command="systemctl restart sddm",
        cwd=None,
        env={},
        stream_output=False,
        interactive=False,
        elevation="root",
    )

    assert (exit_code, stdout, stderr) == (0, "", "")
    assert recorded["sudo_reasons"] == ["run privileged command"]
    assert recorded["command"] == "SUDO(systemctl restart sddm)"
    assert recorded["kwargs"]["stdin"] is execution.subprocess.DEVNULL
    assert recorded["kwargs"]["stdout"] is execution.subprocess.PIPE
    assert recorded["kwargs"]["stderr"] is execution.subprocess.PIPE
    assert "start_new_session" not in recorded["kwargs"]


def test_run_command_lease_elevation_requests_sudo_without_prefix(monkeypatch) -> None:
    recorded: dict[str, object] = {"sudo_reasons": []}

    class FakeProcess:
        pid = 12345
        stdout = io.StringIO("")
        stderr = io.StringIO("")

        def wait(self):
            return 0

    monkeypatch.setattr("dotman.execution.os.geteuid", lambda: 1000)
    monkeypatch.setattr("dotman.execution.request_sudo", lambda reason=None: recorded["sudo_reasons"].append(reason))
    monkeypatch.setattr("dotman.execution.sudo_prefix_command", lambda command: f"SUDO({command})")
    monkeypatch.setattr("dotman.execution.subprocess.Popen", lambda command, **kwargs: recorded.update({"command": command, "kwargs": kwargs}) or FakeProcess())
    monkeypatch.setattr("dotman.execution.preserve_terminal_state", lambda: contextlib.nullcontext())

    execution._run_command(
        command="sh maybe-sudo-later.sh",
        cwd=None,
        env={},
        stream_output=False,
        interactive=False,
        elevation="lease",
    )

    assert recorded["sudo_reasons"] == ["run privileged command"]
    assert recorded["command"] == "sh maybe-sudo-later.sh"
    assert "start_new_session" not in recorded["kwargs"]


@pytest.mark.parametrize("elevation", ["broker", "intercept"])
def test_run_command_broker_elevations_inject_broker_env_without_sudo(monkeypatch, elevation: str) -> None:
    recorded: dict[str, object] = {}

    class FakeBroker:
        def env(self, *, reason=None, intercept=False):
            recorded["broker_reason"] = reason
            recorded["broker_intercept"] = intercept
            return {"DOTMAN_ELEVATION_BROKER": "/tmp/broker.sock", "PATH": "/shim:/usr/bin"} if intercept else {"DOTMAN_ELEVATION_BROKER": "/tmp/broker.sock"}

    class FakeProcess:
        pid = 12345
        stdout = io.StringIO("")
        stderr = io.StringIO("")

        def wait(self):
            return 0

    monkeypatch.setattr("dotman.execution.request_sudo", lambda reason=None: pytest.fail(f"unexpected sudo request: {reason}"))
    monkeypatch.setattr("dotman.execution.current_elevation_broker", lambda: FakeBroker())
    monkeypatch.setattr("dotman.execution.subprocess.Popen", lambda command, **kwargs: recorded.update({"env": kwargs["env"], "kwargs": kwargs}) or FakeProcess())
    monkeypatch.setattr("dotman.execution.preserve_terminal_state", lambda: contextlib.nullcontext())

    execution._run_command(
        command="sh installer.sh",
        cwd=None,
        env={"EXISTING": "1"},
        stream_output=False,
        interactive=False,
        elevation=elevation,
    )

    assert recorded["broker_reason"] == "run privileged command"
    assert recorded["broker_intercept"] is (elevation == "intercept")
    assert recorded["env"]["EXISTING"] == "1"
    assert recorded["env"]["DOTMAN_ELEVATION_BROKER"] == "/tmp/broker.sock"
    assert recorded["kwargs"]["stdin"] is execution.subprocess.DEVNULL
    assert recorded["kwargs"]["stdout"] is execution.subprocess.PIPE
    assert recorded["kwargs"]["stderr"] is execution.subprocess.PIPE
    assert "start_new_session" not in recorded["kwargs"]
    if elevation == "intercept":
        assert recorded["env"]["PATH"] == "/shim:/usr/bin"


def test_run_command_pipe_interrupts_process_group_before_reraising(monkeypatch) -> None:
    killed: list[tuple[int, int]] = []

    class FakeProcess:
        pid = 12345
        stdout = io.StringIO("")
        stderr = io.StringIO("")

        def __init__(self):
            self.wait_calls = 0

        def wait(self, timeout=None):
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise KeyboardInterrupt
            return -signal.SIGINT

    fake_process = FakeProcess()

    monkeypatch.setattr("dotman.execution.subprocess.Popen", lambda *args, **kwargs: fake_process)
    monkeypatch.setattr("dotman.execution.os.killpg", lambda pid, sig: killed.append((pid, sig)))

    with pytest.raises(KeyboardInterrupt):
        execution._run_command(
            command="sleep 30",
            cwd=None,
            env={},
            stream_output=False,
            interactive=False,
        )

    assert killed == [(12345, signal.SIGINT)]


def test_run_command_elevated_pipe_interrupt_terminates_child_without_process_group(monkeypatch) -> None:
    killed: list[tuple[int, int]] = []
    terminated: list[str] = []

    class FakeProcess:
        pid = 12345
        stdout = io.StringIO("")
        stderr = io.StringIO("")

        def __init__(self):
            self.wait_calls = 0

        def wait(self, timeout=None):
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise KeyboardInterrupt
            return -signal.SIGINT

        def terminate(self):
            terminated.append("terminate")

    fake_process = FakeProcess()

    monkeypatch.setattr("dotman.execution.os.geteuid", lambda: 0)
    monkeypatch.setattr("dotman.execution.subprocess.Popen", lambda *args, **kwargs: fake_process)
    monkeypatch.setattr("dotman.execution.os.killpg", lambda pid, sig: killed.append((pid, sig)))

    with pytest.raises(KeyboardInterrupt):
        execution._run_command(
            command="sudo true",
            cwd=None,
            env={},
            stream_output=False,
            interactive=False,
            elevation="root",
        )

    assert terminated == ["terminate"]
    assert killed == []


def test_execute_session_marks_command_exit_130_as_interrupted(monkeypatch) -> None:
    monkeypatch.setattr("dotman.execution._run_command", lambda **kwargs: (130, "", ""))

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

    result = execute_session(build_execution_session([plan], operation="push"), stream_output=False)

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
    monkeypatch.setattr(
        "dotman.execution._run_command",
        lambda *, command, cwd, env, stream_output, interactive, elevation="none": (
            recorded_events.append((command, elevation))
            or (0, "", "")
        ),
    )
    monkeypatch.setattr(
        "dotman.execution._execute_target_step",
        lambda step: recorded_events.append((step.action, step.privileged)),
    )

    result = execute_session(session, stream_output=False)

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
    stale_temp_file = tmp_path / ".dotman-999999-deadbeef.tmp"
    stale_temp_file.write_text("stale\n", encoding="utf-8")

    target_path = tmp_path / "config.txt"
    execution.write_bytes_atomic(target_path, b"payload\n")

    assert target_path.read_text(encoding="utf-8") == "payload\n"
    assert not stale_temp_file.exists()


def test_read_bytes_uses_sudo_when_direct_read_is_denied(tmp_path: Path, monkeypatch) -> None:
    target_path = tmp_path / "protected.txt"
    target_path.write_text("payload\n", encoding="utf-8")
    target_path.chmod(0o000)

    def fake_run(command, *args, **kwargs):
        if command[:2] == ["sudo", "-v"]:
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        if command[:3] == ["sudo", "-n", "/bin/cat"]:
            assert command[3] == str(target_path)
            return SimpleNamespace(returncode=0, stdout=b"payload\n", stderr=b"")
        if command[:3] == ["sudo", "-n", "true"]:
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        raise AssertionError(f"unexpected sudo command: {command}")

    monkeypatch.setattr(file_access.subprocess, "run", fake_run)

    with file_access.sudo_session():
        assert file_access.read_bytes(target_path) == b"payload\n"


def test_request_sudo_emits_user_facing_reason_only_when_password_prompt_is_needed(monkeypatch, capsys) -> None:
    def fake_run(command, *args, **kwargs):
        if command[:2] == ["sudo", "-v"]:
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        if command[:3] == ["sudo", "-n", "true"]:
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        raise AssertionError(f"unexpected sudo command: {command}")

    monkeypatch.setattr(file_access.subprocess, "run", fake_run)

    with file_access.sudo_session():
        file_access.request_sudo("list protected directory: /etc/sddm.conf.d")
        file_access.request_sudo("write protected path: /etc/sddm.conf")

    captured = capsys.readouterr()
    assert captured.err == "[sudo] password required to list protected directory: /etc/sddm.conf.d\n"



def test_request_sudo_emits_user_facing_reason_again_when_cached_lease_expires(monkeypatch, capsys) -> None:
    keepalive_checks = iter((1,))

    def fake_run(command, *args, **kwargs):
        if command[:2] == ["sudo", "-v"]:
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        if command[:3] == ["sudo", "-n", "true"]:
            return SimpleNamespace(returncode=next(keepalive_checks), stdout=b"", stderr=b"")
        raise AssertionError(f"unexpected sudo command: {command}")

    monkeypatch.setattr(file_access.subprocess, "run", fake_run)

    with file_access.sudo_session():
        file_access.request_sudo("list protected directory: /etc/sddm.conf.d")
        file_access.request_sudo("write protected path: /etc/sddm.conf")

    captured = capsys.readouterr()
    assert captured.err == (
        "[sudo] password required to list protected directory: /etc/sddm.conf.d\n"
        "[sudo] password required to write protected path: /etc/sddm.conf\n"
    )


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

    recorded: dict[str, object] = {}

    monkeypatch.setattr(
        "dotman.execution.request_sudo",
        lambda reason=None: (_ for _ in ()).throw(AssertionError(f"unexpected sudo request: {reason}")),
    )
    monkeypatch.setattr(
        "dotman.execution._run_command",
        lambda *, command, cwd, env, stream_output, interactive, elevation="none": (
            recorded.update(
                {
                    "command": command,
                    "cwd": cwd,
                    "env": env,
                    "stream_output": stream_output,
                    "interactive": interactive,
                    "elevation": elevation,
                }
            )
            or (0, "batch reconcile\n", "")
        ),
    )

    result = execute_session(session, stream_output=False)

    assert result.status == "ok"
    assert recorded["command"] == "printf 'batch reconcile\\n'"
    assert recorded["elevation"] == "none"


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

    recorded: dict[str, object] = {"sudo_reasons": []}

    monkeypatch.setattr(
        "dotman.execution.request_sudo",
        lambda reason=None: recorded["sudo_reasons"].append(reason),
    )
    monkeypatch.setattr(
        "dotman.execution._run_command",
        lambda *, command, cwd, env, stream_output, interactive, elevation="none": (
            recorded.update({"command": command, "elevation": elevation}) or (0, "batch reconcile\n", "")
        ),
    )

    result = execute_session(session, stream_output=False)

    assert result.status == "ok"
    assert recorded["sudo_reasons"] == ["execute privileged reconcile for fixture:app.config"]
    assert recorded["command"] == "printf 'batch reconcile\n'"
    assert recorded["elevation"] == "root"



def test_execute_session_falls_back_to_reconcile_when_capture_fails(
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

    def fake_run_command(*, command, cwd, env, stream_output, interactive, elevation="none"):
        if command == "capture-command":
            return 1, "", "capture exploded"
        if command == "reconcile-command":
            assert env is not None
            recorded["review_repo_text"] = Path(env["DOTMAN_REVIEW_REPO_PATH"]).read_text(encoding="utf-8")
            recorded["review_live_text"] = Path(env["DOTMAN_REVIEW_LIVE_PATH"]).read_text(encoding="utf-8")
            recorded["reconcile_elevation"] = elevation
            repo_path.write_text(live_path.read_text(encoding="utf-8"), encoding="utf-8")
            return 0, "reconciled\n", ""
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("dotman.execution._run_command", fake_run_command)

    result = execute_session(session, stream_output=False)

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
