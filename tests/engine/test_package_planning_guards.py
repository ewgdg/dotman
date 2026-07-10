from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

import dotman.cli as cli
from dotman import cli_commands
from dotman.engine import DotmanEngine
from dotman.execution import build_execution_session
from tests.helpers import write_single_repo_config, write_tracked_packages_state


def _write_profile(repo_root: Path) -> None:
    (repo_root / "profiles").mkdir(parents=True, exist_ok=True)
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")


def _write_guarded_target_package(
    repo_root: Path,
    *,
    operation: str,
    guard_command: str,
    projection_marker: Path | None = None,
) -> None:
    package_root = repo_root / "packages" / "app"
    (package_root / "files").mkdir(parents=True, exist_ok=True)
    (package_root / "files" / "config.txt").write_text("repo value\n", encoding="utf-8")
    projection_line = ""
    if projection_marker is not None:
        marker = shlex.quote(str(projection_marker))
        if operation == "push":
            projection_line = f'render = "printf projected > {marker}; cat \\"$DOTMAN_REPO_PATH\\""'
        else:
            projection_line = f'capture = "printf projected > {marker}; cat \\"$DOTMAN_LIVE_PATH\\""'
    lines = [
        'id = "app"',
        "",
        "[targets.config]",
        'source = "files/config.txt"',
        'path = "~/.config/app/config.txt"',
        *( [projection_line] if projection_line else [] ),
        "",
        "[hooks]",
        f"guard_{operation} = {json.dumps(guard_command)}",
        f"pre_{operation} = \"printf pre\"",
        f"post_{operation} = \"printf post\"",
        "",
    ]
    (package_root / "package.toml").write_text("\n".join(lines), encoding="utf-8")


def _engine(tmp_path: Path, repo_root: Path) -> DotmanEngine:
    return DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )


@pytest.mark.parametrize("operation", ["push", "pull"])
def test_package_guard_exit_100_omits_package_before_host_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("DOTMAN_ASSUME_YES", "1")
    live_path = home / ".config" / "app" / "config.txt"
    live_path.parent.mkdir(parents=True)
    live_path.write_text("live value\n", encoding="utf-8")

    repo_root = tmp_path / "repo"
    _write_profile(repo_root)
    projection_marker = tmp_path / "projected"
    _write_guarded_target_package(
        repo_root,
        operation=operation,
        guard_command="[ -z \"${DOTMAN_ASSUME_YES+x}\" ] || exit 9; printf 'not for this host\\n' >&2; exit 100",
        projection_marker=projection_marker,
    )
    engine = _engine(tmp_path, repo_root)

    operation_plan = (
        engine.plan_push_query("fixture:app@default")
        if operation == "push"
        else engine.plan_pull_query("fixture:app@default")
    )

    assert operation_plan.package_plans == ()
    assert [skip.to_dict() for skip in operation_plan.guard_skips] == [
        {
            "scope_kind": "package",
            "repo": "fixture",
            "package_id": "app",
            "bound_profile": None,
            "scope": "fixture:app",
            "reason": "not for this host",
        }
    ]
    assert not projection_marker.exists()


def test_package_guard_hard_failure_aborts_planning_with_captured_detail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repo_root = tmp_path / "repo"
    _write_profile(repo_root)
    _write_guarded_target_package(
        repo_root,
        operation="push",
        guard_command="printf 'guard exploded\\n' >&2; exit 7",
    )
    engine = _engine(tmp_path, repo_root)

    with pytest.raises(ValueError, match=r"guard_push failed with exit 7: guard exploded"):
        engine.plan_push_query("fixture:app@default")


def test_package_guard_commands_preserve_order_stop_on_skip_and_support_elevation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repo_root = tmp_path / "repo"
    _write_profile(repo_root)
    package_root = repo_root / "packages" / "app"
    (package_root / "files").mkdir(parents=True)
    (package_root / "files" / "config.txt").write_text("repo value\n", encoding="utf-8")
    marker = tmp_path / "guard-order"
    package_root.joinpath("package.toml").write_text(
        "\n".join(
            [
                'id = "app"',
                "",
                "[targets.config]",
                'source = "files/config.txt"',
                'path = "~/.config/app/config.txt"',
                "",
                "[hooks]",
                "guard_push = [",
                f"  {{ run = {json.dumps(f'printf first >> {marker}')} }},",
                "  { run = \"printf 'elevated skip\\n' >&2; exit 100\", elevation = \"lease\" },",
                f"  {{ run = {json.dumps(f'printf third >> {marker}')} }},",
                "]",
                "",
            ]
        ),
        encoding="utf-8",
    )

    operation_plan = _engine(tmp_path, repo_root).plan_push_query("fixture:app@default")

    assert marker.read_text(encoding="utf-8") == "first"
    assert operation_plan.guard_skips[0].reason == "elevated skip"


def test_package_guard_runs_once_per_instance_per_plan_build_and_never_enters_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repo_root = tmp_path / "repo"
    _write_profile(repo_root)
    (repo_root / "groups").mkdir()
    (repo_root / "groups" / "all.toml").write_text('members = ["meta-a", "meta-b"]\n', encoding="utf-8")
    marker = tmp_path / "guard-runs"
    _write_guarded_target_package(
        repo_root,
        operation="push",
        guard_command=f"printf run >> {shlex.quote(str(marker))}",
    )
    for package_id in ("meta-a", "meta-b"):
        package_root = repo_root / "packages" / package_id
        package_root.mkdir(parents=True)
        (package_root / "package.toml").write_text(
            f'id = "{package_id}"\ndepends = ["app"]\n',
            encoding="utf-8",
        )
    engine = _engine(tmp_path, repo_root)

    first_plan = engine.plan_push_query("fixture:all@default")
    second_plan = engine.plan_push_query("fixture:all@default")

    assert marker.read_text(encoding="utf-8") == "runrun"
    app_plan = next(plan for plan in first_plan.package_plans if plan.package_id == "app")
    assert "guard_push" not in app_plan.hooks
    session = build_execution_session(first_plan, operation="push")
    assert all(step.action != "guard_push" for repo in session.repos for step in repo.steps)
    assert second_plan.guard_skips == ()


def test_run_noop_is_planning_input_and_only_retains_pre_post_hooks(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    _write_profile(repo_root)
    package_root = repo_root / "packages" / "app"
    package_root.mkdir(parents=True)
    marker = tmp_path / "guard-runs"
    package_root.joinpath("package.toml").write_text(
        "\n".join(
            [
                'id = "app"',
                "",
                "[hooks]",
                f'guard_push = "printf guard >> {marker}"',
                'pre_push = "printf pre"',
                'post_push = "printf post"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    engine = _engine(tmp_path, repo_root)

    default_plan = engine.plan_push_query("fixture:app@default")
    run_noop_plan = engine.plan_push_query("fixture:app@default", run_noop=True)

    assert marker.read_text(encoding="utf-8") == "guard"
    assert default_plan.has_effective_work is False
    assert set(run_noop_plan.package_plans[0].hooks) == {"pre_push", "post_push"}
    assert "guard_push" not in run_noop_plan.package_plans[0].hooks


@pytest.mark.parametrize(
    "guard_manifest, error_match",
    [
        ('guard_push = { run = "printf guard", io = "tty" }', "guard_push.*io.*pipe"),
        ('guard_push = { run = "printf guard", run_noop = true }', "guard_push.*run_noop"),
        ("[hooks.guard_push]\ncommands = [\"printf guard\"]\nrun_noop = true", "guard_push.*run_noop"),
    ],
)
def test_package_guard_manifest_rejects_interactive_io_and_run_noop(
    tmp_path: Path,
    guard_manifest: str,
    error_match: str,
) -> None:
    repo_root = tmp_path / "repo"
    _write_profile(repo_root)
    package_root = repo_root / "packages" / "app"
    package_root.mkdir(parents=True)
    package_root.joinpath("package.toml").write_text(
        f'id = "app"\n\n[hooks]\n{guard_manifest}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=error_match):
        _engine(tmp_path, repo_root).get_repo("fixture").resolve_package("app")


def test_all_guard_skipped_cli_reports_before_ui_and_returns_without_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repo_root = tmp_path / "repo"
    _write_profile(repo_root)
    _write_guarded_target_package(
        repo_root,
        operation="push",
        guard_command="printf 'host mismatch\\n'; exit 100",
    )
    config_path = write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    write_tracked_packages_state(tmp_path / "state", repo_name="fixture", entries=[("app", "default")])

    class RecordingSink:
        closed = False

        def start(self, _total: int) -> None:
            pass

        def update(self, _count: int = 1) -> None:
            pass

        def close(self) -> None:
            self.closed = True

    sink = RecordingSink()
    original_emit_guard_skips = cli.emit_planning_guard_skips

    def emit_guard_skips_after_progress(**kwargs) -> None:
        assert sink.closed is True
        original_emit_guard_skips(**kwargs)

    monkeypatch.setattr(cli_commands, "make_planning_sink", lambda *, json_output: sink)
    monkeypatch.setattr(cli, "emit_planning_guard_skips", emit_guard_skips_after_progress)

    monkeypatch.setattr(cli, "review_plans_for_interactive_diffs", lambda **_kwargs: pytest.fail("review must not run"))
    monkeypatch.setattr(cli, "filter_plans_for_interactive_selection", lambda **_kwargs: pytest.fail("selection must not run"))
    monkeypatch.setattr(cli, "execute_plans", lambda **_kwargs: pytest.fail("execution must not run"))

    exit_code = cli.main(["--config", str(config_path), "push"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "skipped (guard) fixture:app (host mismatch)" in output
    assert "executing push" not in output


def test_guard_skip_json_is_structured_and_omits_command_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repo_root = tmp_path / "repo"
    _write_profile(repo_root)
    secret_command = "printf 'json reason\\n' >&2; exit 100"
    _write_guarded_target_package(
        repo_root,
        operation="push",
        guard_command=secret_command,
    )
    config_path = write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    write_tracked_packages_state(tmp_path / "state", repo_name="fixture", entries=[("app", "default")])

    exit_code = cli.main(["--config", str(config_path), "--json", "push", "--dry-run"])

    assert exit_code == 0
    raw_output = capsys.readouterr().out
    payload = json.loads(raw_output)
    assert payload["guard_skips"] == [
        {
            "bound_profile": None,
            "package_id": "app",
            "reason": "json reason",
            "repo": "fixture",
            "scope": "fixture:app",
            "scope_kind": "package",
        }
    ]
    assert payload["package_entries"] == []
    assert secret_command not in raw_output
