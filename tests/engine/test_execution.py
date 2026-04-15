from __future__ import annotations

import stat
from pathlib import Path
from types import SimpleNamespace

import dotman.execution as execution
from dotman.engine import DotmanEngine
from dotman.execution import build_execution_session, execute_session
from dotman.models import Binding, BindingPlan, HookPlan, TargetPlan
from tests.helpers import write_named_manager_config


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


def test_build_execution_session_keeps_hook_only_packages_when_run_noop_is_enabled() -> None:
    for operation, hook_name_prefix in (("push", "push"), ("pull", "pull")):
        plan = BindingPlan(
            operation=operation,
            binding=Binding(repo="fixture", selector="app", profile="default"),
            selector_kind="package",
            package_ids=["app"],
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

        session = build_execution_session([plan], operation=operation, run_noop=True)

        assert [unit.package_id for unit in session.packages] == ["app"]
        assert [step.action for step in session.packages[0].steps] == [
            f"guard_{hook_name_prefix}",
            f"pre_{hook_name_prefix}",
            f"post_{hook_name_prefix}",
        ]


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

    plan = BindingPlan(
        operation="push",
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

    plan = BindingPlan(
        operation="push",
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

    plan = BindingPlan(
        operation="push",
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


def test_execute_session_runs_builtin_jinja_reconcile_helper(
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
                reconcile_command="jinja",
                reconcile_io="tty",
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


def test_execute_session_restores_repo_path_access_for_pull_updates_run_via_sudo(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo_root = tmp_path / "repo"
    repo_path = repo_root / "packages" / "app" / "config.txt"
    repo_path.parent.mkdir(parents=True)
    live_path = tmp_path / "live.txt"
    live_path.write_text("live\n", encoding="utf-8")

    plan = BindingPlan(
        operation="pull",
        binding=Binding(repo="fixture", selector="app", profile="default"),
        selector_kind="package",
        package_ids=["app"],
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
    plan = engine.plan_pull_binding("fixture:shell@default")
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
    plan = engine.plan_pull_binding("fixture:shell@default")
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
