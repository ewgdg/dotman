from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

import dotman.cli as cli
from dotman.engine import DotmanEngine
from dotman.execution import build_execution_session, execute_session
from tests.helpers import write_named_manager_config, write_single_repo_config, write_tracked_packages_state


def _write_profile(repo_root: Path) -> None:
    (repo_root / "profiles").mkdir(parents=True, exist_ok=True)
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")


def _write_repo_guard(repo_root: Path, *, operation: str, command: str) -> None:
    (repo_root / "repo.toml").write_text(
        f"[hooks]\nguard_{operation} = {json.dumps(command)}\n",
        encoding="utf-8",
    )


def _write_file_package(
    repo_root: Path,
    *,
    package_id: str,
    targets: list[tuple[str, str, str | None, str | None]],
    operation: str = "push",
    package_guard: str | None = None,
    depends: tuple[str, ...] = (),
) -> None:
    package_root = repo_root / "packages" / package_id
    (package_root / "files").mkdir(parents=True, exist_ok=True)
    lines = [f'id = "{package_id}"']
    if depends:
        lines.append(f"depends = {json.dumps(list(depends))}")
    for target_name, live_path, target_guard, projection_command in targets:
        source_name = f"{target_name}.txt"
        (package_root / "files" / source_name).write_text(f"{package_id}:{target_name}\n", encoding="utf-8")
        lines.extend(
            [
                "",
                f"[targets.{target_name}]",
                f'source = "files/{source_name}"',
                f"path = {json.dumps(live_path)}",
            ]
        )
        if projection_command is not None:
            projection_key = "render" if operation == "push" else "capture"
            lines.append(f"{projection_key} = {json.dumps(projection_command)}")
        if target_guard is not None:
            lines.extend(
                [
                    "",
                    f"[targets.{target_name}.hooks]",
                    f"guard_{operation} = {json.dumps(target_guard)}",
                ]
            )
    if package_guard is not None:
        lines.extend(["", "[hooks]", f"guard_{operation} = {json.dumps(package_guard)}"])
    package_root.joinpath("package.toml").write_text("\n".join([*lines, ""]), encoding="utf-8")


def _engine(tmp_path: Path, repo_root: Path) -> DotmanEngine:
    return DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )


def test_repo_guard_skip_short_circuits_lower_planning_and_sibling_repo_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    marker = tmp_path / "order"
    quoted_marker = shlex.quote(str(marker))

    skipped_repo = tmp_path / "skipped-repo"
    _write_profile(skipped_repo)
    _write_repo_guard(
        skipped_repo,
        operation="push",
        command=f"printf 'repo-skipped\\n' >> {quoted_marker}; printf 'wrong host\\n'; exit 100",
    )
    _write_file_package(
        skipped_repo,
        package_id="app",
        package_guard=f"printf 'package-skipped\\n' >> {quoted_marker}",
        targets=[
            (
                "config",
                "~/.config/skipped/config.txt",
                f"printf 'target-skipped\\n' >> {quoted_marker}",
                f"printf 'projection-skipped\\n' >> {quoted_marker}; cat \"$DOTMAN_REPO_PATH\"",
            )
        ],
    )

    admitted_repo = tmp_path / "admitted-repo"
    _write_profile(admitted_repo)
    _write_repo_guard(admitted_repo, operation="push", command=f"printf 'repo-admitted\\n' >> {quoted_marker}")
    _write_file_package(
        admitted_repo,
        package_id="app",
        package_guard=f"printf 'package-admitted\\n' >> {quoted_marker}",
        targets=[
            (
                "config",
                "~/.config/admitted/config.txt",
                f"printf 'target-admitted\\n' >> {quoted_marker}",
                f"printf 'projection-admitted\\n' >> {quoted_marker}; cat \"$DOTMAN_REPO_PATH\"",
            )
        ],
    )

    config_path = write_named_manager_config(
        tmp_path,
        {"skipped": skipped_repo, "admitted": admitted_repo},
    )
    write_tracked_packages_state(tmp_path / "state", repo_name="skipped", entries=[("app", "default")])
    write_tracked_packages_state(tmp_path / "state", repo_name="admitted", entries=[("app", "default")])

    operation_plan = DotmanEngine.from_config_path(config_path).plan_push()

    assert marker.read_text(encoding="utf-8").splitlines() == [
        "repo-skipped",
        "repo-admitted",
        "package-admitted",
        "target-admitted",
        "projection-admitted",
    ]
    assert [plan.repo_name for plan in operation_plan.package_plans] == ["admitted"]
    assert [skip.to_dict() for skip in operation_plan.guard_skips] == [
        {
            "scope_kind": "repo",
            "repo": "skipped",
            "package_id": None,
            "bound_profile": None,
            "scope": "skipped",
            "reason": "wrong host",
        }
    ]


def test_package_dependency_guard_skip_is_local_to_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repo_root = tmp_path / "repo"
    _write_profile(repo_root)
    _write_file_package(
        repo_root,
        package_id="dependency",
        package_guard="printf 'dependency unavailable\\n'; exit 100",
        targets=[("dependency", "~/.config/dependency.txt", None, None)],
    )
    _write_file_package(
        repo_root,
        package_id="app",
        depends=("dependency",),
        targets=[("app", "~/.config/app.txt", None, None)],
    )

    operation_plan = _engine(tmp_path, repo_root).plan_push_query("fixture:app@default")

    assert [plan.package_id for plan in operation_plan.package_plans] == ["app"]
    assert [target.target_name for target in operation_plan.package_plans[0].target_plans] == ["app"]
    assert operation_plan.guard_skips[0].scope_label == "fixture:dependency"


def test_target_guard_skip_prevents_projection_and_directory_scan_but_keeps_sibling_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repo_root = tmp_path / "repo"
    _write_profile(repo_root)
    marker = tmp_path / "projection"
    _write_file_package(
        repo_root,
        package_id="app",
        targets=[
            (
                "skipped",
                "~/.config/skipped.txt",
                "printf 'target unavailable\\n'; exit 100",
                f"printf projected > {shlex.quote(str(marker))}; cat \"$DOTMAN_REPO_PATH\"",
            ),
            ("kept", "~/.config/kept.txt", None, None),
        ],
    )

    operation_plan = _engine(tmp_path, repo_root).plan_push_query("fixture:app@default")

    assert not marker.exists()
    assert [target.target_name for target in operation_plan.package_plans[0].target_plans] == ["kept"]
    assert operation_plan.guard_skips[0].scope_label == "fixture:app.skipped"

    directory_root = tmp_path / "directory-repo"
    _write_profile(directory_root)
    package_root = directory_root / "packages" / "app"
    (package_root / "files" / "tree").mkdir(parents=True)
    (package_root / "files" / "tree" / "item.txt").write_text("value\n", encoding="utf-8")
    package_root.joinpath("package.toml").write_text(
        "\n".join(
            [
                'id = "app"',
                "",
                "[targets.tree]",
                'source = "files/tree"',
                'path = "~/.config/tree"',
                'type = "directory"',
                "",
                "[targets.tree.hooks]",
                'guard_push = "exit 100"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "dotman.projection.list_directory_files",
        lambda *_args, **_kwargs: pytest.fail("directory scan must not run"),
    )
    directory_config_root = tmp_path / "directory-config"
    directory_config_root.mkdir()

    directory_plan = DotmanEngine.from_config_path(
        write_single_repo_config(directory_config_root, repo_name="fixture", repo_path=directory_root)
    ).plan_push_query("fixture:app@default")

    assert directory_plan.package_plans[0].target_plans == []


def test_probe_target_guard_runs_before_probe_and_distinguishes_guard_skip_from_probe_noop(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    _write_profile(repo_root)
    package_root = repo_root / "packages" / "app"
    package_root.mkdir(parents=True)
    guard_marker = tmp_path / "guard"
    skipped_probe_marker = tmp_path / "skipped-probe"
    noop_probe_marker = tmp_path / "noop-probe"
    package_root.joinpath("package.toml").write_text(
        "\n".join(
            [
                'id = "app"',
                "",
                "[targets.skipped]",
                f'probe = "printf probe > {skipped_probe_marker}; exit 0"',
                "",
                "[targets.skipped.hooks]",
                f'guard_push = "printf guard > {guard_marker}; exit 100"',
                "",
                "[targets.noop]",
                f'probe = "printf probe > {noop_probe_marker}; exit 100"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    operation_plan = _engine(tmp_path, repo_root).plan_push_query("fixture:app@default")

    assert guard_marker.read_text(encoding="utf-8") == "guard"
    assert not skipped_probe_marker.exists()
    assert noop_probe_marker.read_text(encoding="utf-8") == "probe"
    assert [(target.target_name, target.action) for target in operation_plan.package_plans[0].target_plans] == [
        ("noop", "noop")
    ]
    assert operation_plan.guard_skips[0].scope_label == "fixture:app.skipped"


def test_repo_and_target_pull_guards_use_same_hierarchy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    live_path = home / ".config" / "app.txt"
    live_path.parent.mkdir(parents=True)
    live_path.write_text("live\n", encoding="utf-8")
    repo_root = tmp_path / "repo"
    _write_profile(repo_root)
    marker = tmp_path / "pull-order"
    quoted_marker = shlex.quote(str(marker))
    _write_repo_guard(repo_root, operation="pull", command=f"printf 'repo\\n' >> {quoted_marker}")
    _write_file_package(
        repo_root,
        package_id="app",
        operation="pull",
        targets=[
            (
                "config",
                "~/.config/app.txt",
                f"printf 'target\\n' >> {quoted_marker}; exit 100",
                None,
            )
        ],
    )

    operation_plan = _engine(tmp_path, repo_root).plan_pull_query("fixture:app@default")

    assert marker.read_text(encoding="utf-8").splitlines() == ["repo", "target"]
    assert operation_plan.package_plans[0].target_plans == []
    assert operation_plan.guard_skips[0].scope_label == "fixture:app.config"


def test_repo_package_and_target_guards_are_deduplicated_per_plan_build(
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
    marker = tmp_path / "runs"
    quoted_marker = shlex.quote(str(marker))
    _write_repo_guard(repo_root, operation="push", command=f"printf 'repo\\n' >> {quoted_marker}")
    _write_file_package(
        repo_root,
        package_id="app",
        package_guard=f"printf 'package\\n' >> {quoted_marker}",
        targets=[
            (
                "config",
                "~/.config/app.txt",
                f"printf 'target\\n' >> {quoted_marker}",
                None,
            )
        ],
    )
    for package_id in ("meta-a", "meta-b"):
        package_root = repo_root / "packages" / package_id
        package_root.mkdir(parents=True)
        package_root.joinpath("package.toml").write_text(
            f'id = "{package_id}"\ndepends = ["app"]\n',
            encoding="utf-8",
        )
    engine = _engine(tmp_path, repo_root)

    engine.plan_push_query("fixture:all@default")
    engine.plan_push_query("fixture:all@default")

    assert marker.read_text(encoding="utf-8").splitlines() == [
        "repo",
        "package",
        "target",
        "repo",
        "package",
        "target",
    ]


def test_static_ownership_conflict_is_reported_before_repo_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repo_root = tmp_path / "repo"
    _write_profile(repo_root)
    (repo_root / "groups").mkdir()
    (repo_root / "groups" / "all.toml").write_text('members = ["alpha", "beta"]\n', encoding="utf-8")
    marker = tmp_path / "repo-guard"
    _write_repo_guard(repo_root, operation="push", command=f"printf guard > {marker}; exit 100")
    for package_id in ("alpha", "beta"):
        _write_file_package(
            repo_root,
            package_id=package_id,
            targets=[("config", "~/.config/shared.txt", None, None)],
        )

    with pytest.raises(ValueError, match="conflicting explicit tracked targets"):
        _engine(tmp_path, repo_root).plan_push_query("fixture:all@default")

    assert not marker.exists()


@pytest.mark.parametrize("scope", ["repo", "target"])
def test_repo_and_target_guard_hard_failures_abort_planning_with_captured_detail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scope: str,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repo_root = tmp_path / "repo"
    _write_profile(repo_root)
    if scope == "repo":
        _write_repo_guard(
            repo_root,
            operation="push",
            command="printf 'repo guard exploded\\n' >&2; exit 7",
        )
        target_guard = None
    else:
        target_guard = "printf 'target guard exploded\\n' >&2; exit 8"
    _write_file_package(
        repo_root,
        package_id="app",
        targets=[("config", "~/.config/app.txt", target_guard, None)],
    )

    expected_status = 7 if scope == "repo" else 8
    with pytest.raises(ValueError, match=rf"guard_push failed with exit {expected_status}: {scope} guard exploded"):
        _engine(tmp_path, repo_root).plan_push_query("fixture:app@default")


def test_target_guard_diagnostic_uses_package_instance_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repo_root = tmp_path / "repo"
    _write_profile(repo_root)
    package_root = repo_root / "packages" / "profiled"
    (package_root / "files").mkdir(parents=True)
    (package_root / "files" / "config.txt").write_text("value\n", encoding="utf-8")
    package_root.joinpath("package.toml").write_text(
        "\n".join(
            [
                'id = "profiled"',
                'binding_mode = "multi_instance"',
                "",
                "[targets.config]",
                'source = "files/config.txt"',
                'path = "~/.config/{{ profile }}/config.txt"',
                "",
                "[targets.config.hooks]",
                'guard_push = "exit 100"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "profiles" / "work.toml").write_text("", encoding="utf-8")

    operation_plan = _engine(tmp_path, repo_root).plan_push_query("fixture:profiled@work")

    assert operation_plan.guard_skips[0].scope_label == "fixture:profiled<work>.config"


def test_target_guard_hard_failure_renders_package_instance_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repo_root = tmp_path / "repo"
    _write_profile(repo_root)
    (repo_root / "profiles" / "work.toml").write_text("", encoding="utf-8")
    package_root = repo_root / "packages" / "profiled"
    (package_root / "files").mkdir(parents=True)
    (package_root / "files" / "config.txt").write_text("value\n", encoding="utf-8")
    package_root.joinpath("package.toml").write_text(
        "\n".join(
            [
                'id = "profiled"',
                'binding_mode = "multi_instance"',
                "",
                "[targets.config]",
                'source = "files/config.txt"',
                'path = "~/.config/{{ profile }}/config.txt"',
                "",
                "[targets.config.hooks]",
                'guard_push = "printf failed >&2; exit 8"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    config_path = write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    write_tracked_packages_state(tmp_path / "state", repo_name="fixture", entries=[("profiled", "work")])

    assert cli.main(["--config", str(config_path), "push"]) == 2

    assert "target: fixture:profiled<work>.config" in capsys.readouterr().err


def test_target_skip_can_leave_noop_eligible_package_and_repo_hooks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repo_root = tmp_path / "repo"
    _write_profile(repo_root)
    repo_root.joinpath("repo.toml").write_text(
        "[hooks.pre_push]\ncommands = [\"printf repo pre\"]\nrun_noop = true\n",
        encoding="utf-8",
    )
    _write_file_package(
        repo_root,
        package_id="app",
        targets=[("config", "~/.config/app.txt", "exit 100", None)],
    )
    package_path = repo_root / "packages" / "app" / "package.toml"
    package_path.write_text(
        package_path.read_text(encoding="utf-8")
        + "\n[hooks.pre_push]\ncommands = [\"printf package pre\"]\nrun_noop = true\n",
        encoding="utf-8",
    )

    operation_plan = _engine(tmp_path, repo_root).plan_push_query("fixture:app@default")

    assert set(operation_plan.package_plans[0].hooks) == {"pre_push"}
    assert set(operation_plan.repo_hooks["fixture"]) == {"pre_push"}
    assert operation_plan.has_effective_work is True


@pytest.mark.parametrize("scope", ["repo", "target"])
@pytest.mark.parametrize(
    ("guard_payload", "error_match"),
    [
        ('{ run = "printf guard", io = "tty" }', "guard_push.*io.*pipe"),
        ('{ run = "printf guard", run_noop = true }', "guard_push.*run_noop"),
        ('{ commands = ["printf guard"], run_noop = true }', "guard_push.*run_noop"),
    ],
)
def test_repo_and_target_guard_manifests_reject_interactive_io_and_run_noop(
    tmp_path: Path,
    scope: str,
    guard_payload: str,
    error_match: str,
) -> None:
    repo_root = tmp_path / "repo"
    _write_profile(repo_root)
    if scope == "repo":
        repo_root.joinpath("repo.toml").write_text(
            f"[hooks]\nguard_push = {guard_payload}\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match=error_match):
            _engine(tmp_path, repo_root)
        return

    package_root = repo_root / "packages" / "app"
    (package_root / "files").mkdir(parents=True)
    (package_root / "files" / "config.txt").write_text("value\n", encoding="utf-8")
    package_root.joinpath("package.toml").write_text(
        "\n".join(
            [
                'id = "app"',
                "",
                "[targets.config]",
                'source = "files/config.txt"',
                'path = "~/.config/app.txt"',
                "",
                "[targets.config.hooks]",
                f"guard_push = {guard_payload}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=error_match):
        _engine(tmp_path, repo_root).get_repo("fixture").resolve_package("app")


def test_generated_execution_session_omits_all_guards_and_late_state_change_fails_hard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repo_root = tmp_path / "repo"
    _write_profile(repo_root)
    guard_runs = tmp_path / "guard-runs"
    blocked = tmp_path / "blocked"
    _write_repo_guard(repo_root, operation="push", command=f"printf repo >> {guard_runs}")
    _write_file_package(
        repo_root,
        package_id="app",
        package_guard=f"printf package >> {guard_runs}",
        targets=[
            (
                "config",
                "~/.config/app/config.txt",
                f"printf target >> {guard_runs}; test ! -e {blocked} || exit 100",
                None,
            )
        ],
    )
    engine = _engine(tmp_path, repo_root)

    operation_plan = engine.plan_push_query("fixture:app@default")
    session = build_execution_session(operation_plan, operation="push")

    assert all(not step.action.startswith("guard_") for repo in session.repos for step in repo.steps)
    blocked.write_text("changed\n", encoding="utf-8")
    live_parent = home / ".config" / "app"
    live_parent.parent.mkdir(parents=True)
    live_parent.write_text("not a directory\n", encoding="utf-8")

    result = execute_session(session, stream_output=False)

    assert result.status == "failed"
    assert guard_runs.read_text(encoding="utf-8") == "repopackagetarget"


def test_capture_exit_100_remains_a_strict_planning_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    live_path = home / ".config" / "app.txt"
    live_path.parent.mkdir(parents=True)
    live_path.write_text("live\n", encoding="utf-8")
    repo_root = tmp_path / "repo"
    _write_profile(repo_root)
    _write_file_package(
        repo_root,
        package_id="app",
        operation="pull",
        targets=[("config", "~/.config/app.txt", None, "printf unavailable >&2; exit 100")],
    )
    package_path = repo_root / "packages" / "app" / "package.toml"
    package_text = package_path.read_text(encoding="utf-8").replace(
        'capture = "printf unavailable >&2; exit 100"',
        'capture = "printf unavailable >&2; exit 100"\npull_view_live = "capture"',
    )
    package_path.write_text(package_text, encoding="utf-8")

    with pytest.raises(ValueError, match="command projection failed.*unavailable"):
        _engine(tmp_path, repo_root).plan_pull_query("fixture:app@default")


def test_cli_renders_repo_and_target_guard_diagnostics_in_human_and_json_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_skipped_root = tmp_path / "repo-skipped"
    _write_profile(repo_skipped_root)
    _write_repo_guard(
        repo_skipped_root,
        operation="push",
        command="printf 'repo mismatch\\n'; exit 100",
    )
    _write_file_package(
        repo_skipped_root,
        package_id="app",
        targets=[("config", "~/.config/repo-skipped.txt", None, None)],
    )

    target_skipped_root = tmp_path / "target-skipped"
    _write_profile(target_skipped_root)
    _write_file_package(
        target_skipped_root,
        package_id="app",
        targets=[
            (
                "config",
                "~/.config/target-skipped.txt",
                "printf 'target mismatch\\n'; exit 100",
                None,
            )
        ],
    )

    config_path = write_named_manager_config(
        tmp_path,
        {"repo-skip": repo_skipped_root, "target-skip": target_skipped_root},
    )
    for repo_name in ("repo-skip", "target-skip"):
        write_tracked_packages_state(tmp_path / "state", repo_name=repo_name, entries=[("app", "default")])

    assert cli.main(["--config", str(config_path), "push", "--dry-run"]) == 0
    human_output = capsys.readouterr().out
    assert "skipped (guard) repo-skip (repo mismatch)" in human_output
    assert "skipped (guard) target-skip:app.config (target mismatch)" in human_output

    assert cli.main(["--config", str(config_path), "--json", "push", "--dry-run"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["guard_skips"] == [
        {
            "bound_profile": None,
            "package_id": None,
            "reason": "repo mismatch",
            "repo": "repo-skip",
            "scope": "repo-skip",
            "scope_kind": "repo",
        },
        {
            "bound_profile": None,
            "package_id": "app",
            "reason": "target mismatch",
            "repo": "target-skip",
            "scope": "target-skip:app.config",
            "scope_kind": "target",
            "target_name": "config",
        },
    ]
    assert payload["package_entries"] == []


def test_all_target_guard_skipped_cli_returns_before_review_selection_and_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repo_root = tmp_path / "repo"
    _write_profile(repo_root)
    _write_file_package(
        repo_root,
        package_id="app",
        targets=[("config", "~/.config/app.txt", "exit 100", None)],
    )
    config_path = write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    write_tracked_packages_state(tmp_path / "state", repo_name="fixture", entries=[("app", "default")])
    monkeypatch.setattr(cli, "review_plans_for_interactive_diffs", lambda **_kwargs: pytest.fail("review must not run"))
    monkeypatch.setattr(cli, "filter_plans_for_interactive_selection", lambda **_kwargs: pytest.fail("selection must not run"))
    monkeypatch.setattr(cli, "execute_plans", lambda **_kwargs: pytest.fail("execution must not run"))

    assert cli.main(["--config", str(config_path), "push"]) == 0
