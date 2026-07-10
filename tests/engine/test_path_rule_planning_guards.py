from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

import dotman.cli as cli
from dotman.engine import DotmanEngine
from dotman.models import HookCommandSpec
from dotman.planning_guards import GuardPlanningError
from tests.helpers import write_single_repo_config, write_tracked_packages_state


def _write_directory_package(
    repo_root: Path,
    *,
    path_rule_blocks: list[list[str]],
    target_lines: list[str] | None = None,
) -> Path:
    package_root = repo_root / "packages" / "app"
    source_root = package_root / "files" / "config"
    source_root.mkdir(parents=True)
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")

    lines = [
        'id = "app"',
        "",
        "[targets.config]",
        'source = "files/config"',
        'path = "~/.config/app"',
        *(target_lines or []),
    ]
    for block in path_rule_blocks:
        lines.extend(["", "[[targets.config.path_rules]]", *block])
    package_root.joinpath("package.toml").write_text("\n".join([*lines, ""]), encoding="utf-8")
    return source_root


def _engine(tmp_path: Path, repo_root: Path) -> DotmanEngine:
    return DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )


def _guard_rule(*, pattern: str, operation: str, command: str, extra: list[str] | None = None) -> list[str]:
    return [
        f"pattern = {json.dumps(pattern)}",
        *(extra or []),
        "",
        "[targets.config.path_rules.hooks]",
        f"guard_{operation} = {json.dumps(command)}",
    ]


def test_path_rule_guards_normalize_supported_command_forms_and_elevation(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    _write_directory_package(
        repo_root,
        path_rule_blocks=[
            [
                'pattern = "*.txt"',
                "hooks = { guard_push = \"printf string\", guard_pull = [",
                '  { run = "printf object", io = "pipe", elevation = "broker" },',
                '  "printf ordered",',
                "] }",
            ]
        ],
    )

    rule = _engine(tmp_path, repo_root).get_repo("fixture").resolve_package("app").targets["config"].path_rules[0]

    assert rule.hooks is not None
    assert rule.hooks["guard_push"].commands == (HookCommandSpec(run="printf string"),)
    assert rule.hooks["guard_pull"].commands == (
        HookCommandSpec(run="printf object", elevation="broker"),
        HookCommandSpec(run="printf ordered"),
    )


@pytest.mark.parametrize(
    ("hooks_payload", "error_match"),
    [
        ('{ pre_push = "printf invalid" }', "unsupported hook names: pre_push"),
        ('{ guard_push = { run = "printf invalid", io = "tty" } }', "guard_push.*io.*pipe"),
        ('{ guard_push = { run = "printf invalid", run_noop = true } }', "guard_push.*run_noop"),
        ('{ guard_push = { commands = ["printf invalid"], run_noop = true } }', "guard_push.*run_noop"),
    ],
)
def test_path_rule_hooks_reject_pre_post_interactive_io_and_run_noop(
    tmp_path: Path,
    hooks_payload: str,
    error_match: str,
) -> None:
    repo_root = tmp_path / "repo"
    _write_directory_package(
        repo_root,
        path_rule_blocks=[
            [
                'pattern = "*.txt"',
                f"hooks = {hooks_payload}",
            ]
        ],
    )

    with pytest.raises(ValueError, match=error_match):
        _engine(tmp_path, repo_root).get_repo("fixture").resolve_package("app")


@pytest.mark.parametrize("operation", ["push", "pull"])
def test_path_rule_guards_activate_once_for_repo_live_shared_and_noop_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    marker = tmp_path / "guard-order"
    command = f'printf "%s\\n" "$DOTMAN_PATH_RULE_PATTERN" >> {shlex.quote(str(marker))}'
    repo_root = tmp_path / "repo"
    patterns = ["*.txt", "repo-only.txt", "live-only.txt", "shared-changed.txt", "shared-noop.txt", "missing.txt"]
    source_root = _write_directory_package(
        repo_root,
        path_rule_blocks=[
            _guard_rule(pattern=pattern, operation=operation, command=command)
            for pattern in patterns
        ],
    )
    (source_root / "repo-only.txt").write_text("repo only\n", encoding="utf-8")
    (source_root / "shared-changed.txt").write_text("repo changed\n", encoding="utf-8")
    (source_root / "shared-noop.txt").write_text("same\n", encoding="utf-8")
    live_root = home / ".config" / "app"
    live_root.mkdir(parents=True)
    (live_root / "live-only.txt").write_text("live only\n", encoding="utf-8")
    (live_root / "shared-changed.txt").write_text("live changed\n", encoding="utf-8")
    (live_root / "shared-noop.txt").write_text("same\n", encoding="utf-8")

    engine = _engine(tmp_path, repo_root)
    operation_plan = (
        engine.plan_push_query("fixture:app@default")
        if operation == "push"
        else engine.plan_pull_query("fixture:app@default")
    )

    assert marker.read_text(encoding="utf-8").splitlines() == patterns[:-1]
    assert operation_plan.guard_skips == ()



def test_path_rule_activation_excludes_ignored_control_and_skip_marker_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    marker = tmp_path / "guard-order"
    command = f'printf "%s\\n" "$DOTMAN_PATH_RULE_PATTERN" >> {shlex.quote(str(marker))}'
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    repo_root.joinpath("repo.toml").write_text(
        '[ignore]\nskip_markers = [".dotman-skip"]\n',
        encoding="utf-8",
    )
    source_root = _write_directory_package(
        repo_root,
        target_lines=['push_ignore = ["ignored.txt"]', "", "[targets.config.ignore]", 'gitignore = ["push"]'],
        path_rule_blocks=[
            _guard_rule(pattern=pattern, operation="push", command=command)
            for pattern in ("keep.txt", "ignored.txt", ".gitignore", "skipped/hidden.txt")
        ],
    )
    (source_root / "keep.txt").write_text("keep\n", encoding="utf-8")
    (source_root / "ignored.txt").write_text("ignored\n", encoding="utf-8")
    (source_root / ".gitignore").write_text("", encoding="utf-8")
    (source_root / "skipped").mkdir()
    (source_root / "skipped" / ".dotman-skip").write_text("", encoding="utf-8")
    (source_root / "skipped" / "hidden.txt").write_text("hidden\n", encoding="utf-8")

    _engine(tmp_path, repo_root).plan_push_query("fixture:app@default")

    assert marker.read_text(encoding="utf-8").splitlines() == ["keep.txt"]



def test_overlapping_path_rule_guards_run_in_order_prune_work_and_keep_scalar_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    marker = tmp_path / "guard-order"
    quoted_marker = shlex.quote(str(marker))
    repo_root = tmp_path / "repo"
    source_root = _write_directory_package(
        repo_root,
        path_rule_blocks=[
            _guard_rule(
                pattern="a.txt",
                operation="push",
                command=f"printf 'skip-a\\n' >> {quoted_marker}; printf 'a disabled\\n'; exit 100",
            ),
            _guard_rule(
                pattern="*.txt",
                operation="push",
                command=f"printf 'broad\\n' >> {quoted_marker}",
                extra=['chmod = "600"'],
            ),
            _guard_rule(
                pattern="b.txt",
                operation="push",
                command=f"printf 'specific-b\\n' >> {quoted_marker}",
                extra=['chmod = "640"'],
            ),
            _guard_rule(
                pattern="a.txt",
                operation="push",
                command=f"printf 'dead-a\\n' >> {quoted_marker}",
            ),
        ],
    )
    for name in ("a.txt", "b.txt"):
        (source_root / name).write_text(f"repo {name}\n", encoding="utf-8")
    live_root = home / ".config" / "app"
    live_root.mkdir(parents=True)
    for name in ("a.txt", "b.txt"):
        (live_root / name).write_text(f"live {name}\n", encoding="utf-8")

    operation_plan = _engine(tmp_path, repo_root).plan_push_query("fixture:app@default")

    assert marker.read_text(encoding="utf-8").splitlines() == ["skip-a", "broad", "specific-b"]
    target = operation_plan.package_plans[0].target_plans[0]
    assert [(item.relative_path, item.action, item.chmod) for item in target.directory_items] == [
        ("b.txt", "update", "640")
    ]
    assert [skip.to_dict() for skip in operation_plan.guard_skips] == [
        {
            "scope_kind": "path_rule",
            "repo": "fixture",
            "package_id": "app",
            "bound_profile": None,
            "scope": "fixture:app.config",
            "reason": "a disabled",
            "target_name": "config",
            "path_rule_pattern": "a.txt",
        }
    ]



def test_path_rule_guard_environment_uses_target_roots_and_pattern_without_child_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    marker = tmp_path / "guard-env"
    quoted_marker = shlex.quote(str(marker))
    repo_root = tmp_path / "repo"
    source_root = _write_directory_package(
        repo_root,
        path_rule_blocks=[
            _guard_rule(
                pattern="*.txt",
                operation="push",
                command=(
                    f'printf "%s|%s|%s|%s\\n" "$DOTMAN_PATH_RULE_PATTERN" "$DOTMAN_REPO_PATH" '
                    f'"$DOTMAN_LIVE_PATH" "${{DOTMAN_CHILD_PATH-unset}}" > {quoted_marker}; '
                    "printf 'host mismatch\\n'; exit 100"
                ),
            )
        ],
    )
    (source_root / "one.txt").write_text("one\n", encoding="utf-8")

    operation_plan = _engine(tmp_path, repo_root).plan_push_query("fixture:app@default")

    assert marker.read_text(encoding="utf-8").strip() == "|".join(
        ["*.txt", str(source_root), str(home / ".config" / "app"), "unset"]
    )
    assert operation_plan.guard_skips[0].reason == "host mismatch"



def test_path_rule_guard_hard_failure_exposes_target_and_pattern_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repo_root = tmp_path / "repo"
    source_root = _write_directory_package(
        repo_root,
        path_rule_blocks=[
            _guard_rule(
                pattern="*.txt",
                operation="push",
                command="printf 'rule exploded\\n' >&2; exit 7",
            )
        ],
    )
    (source_root / "one.txt").write_text("one\n", encoding="utf-8")

    with pytest.raises(GuardPlanningError, match="guard_push failed with exit 7: rule exploded") as caught:
        _engine(tmp_path, repo_root).plan_push_query("fixture:app@default")

    assert caught.value.scope_kind == "path_rule"
    assert caught.value.target_name == "config"
    assert caught.value.path_rule_pattern == "*.txt"



def test_all_path_rule_work_skipped_cli_reports_pattern_and_bypasses_interaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repo_root = tmp_path / "repo"
    source_root = _write_directory_package(
        repo_root,
        path_rule_blocks=[
            _guard_rule(
                pattern="*.txt",
                operation="push",
                command="printf 'directory disabled\\n'; exit 100",
            )
        ],
    )
    (source_root / "one.txt").write_text("one\n", encoding="utf-8")
    config_path = write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    write_tracked_packages_state(tmp_path / "state", repo_name="fixture", entries=[("app", "default")])
    monkeypatch.setattr(cli, "review_plans_for_interactive_diffs", lambda **_kwargs: pytest.fail("review must not run"))
    monkeypatch.setattr(cli, "filter_plans_for_interactive_selection", lambda **_kwargs: pytest.fail("selection must not run"))
    monkeypatch.setattr(cli, "execute_plans", lambda **_kwargs: pytest.fail("execution must not run"))

    assert cli.main(["--config", str(config_path), "push"]) == 0
    human_output = capsys.readouterr().out
    assert "skipped (guard) fixture:app.config (path rule: *.txt) (directory disabled)" in human_output

    assert cli.main(["--config", str(config_path), "--json", "push", "--dry-run"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["package_entries"] == []
    assert payload["guard_skips"] == [
        {
            "bound_profile": None,
            "package_id": "app",
            "path_rule_pattern": "*.txt",
            "reason": "directory disabled",
            "repo": "fixture",
            "scope": "fixture:app.config",
            "scope_kind": "path_rule",
            "target_name": "config",
        }
    ]
