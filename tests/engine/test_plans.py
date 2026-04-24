from __future__ import annotations

import json
from pathlib import Path

import pytest

from dotman.engine import DotmanEngine
from dotman.models import HookCommandSpec
from tests.helpers import (
    EXAMPLE_REPO,
    REFERENCE_REPO,
    single_package_plan,
    write_manager_config,
    write_multi_instance_repo,
    write_package_override_preview_repo,
    write_single_repo_config,
    write_untrack_conflict_repo,
)


def write_sync_policy_repo(
    tmp_path: Path,
    *,
    package_manifest: list[str],
    target_manifest: list[str] | None = None,
    hook_manifest: list[str] | None = None,
    package_id: str = "app",
) -> Path:
    repo_root = tmp_path / "repo"
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "packages" / package_id / "files").mkdir(parents=True)
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    (repo_root / "packages" / package_id / "files" / "config.txt").write_text("config\n", encoding="utf-8")
    (repo_root / "packages" / package_id / "package.toml").write_text(
        "\n".join(
            [
                f'id = "{package_id}"',
                *package_manifest,
                *(hook_manifest or []),
                "",
                "[targets.config]",
                'source = "files/config.txt"',
                'path = "~/.config/app/config.txt"',
                *(target_manifest or []),
                "",
            ]
        ),
        encoding="utf-8",
    )
    return repo_root


def write_sync_policy_repo_with_extends(
    tmp_path: Path,
    *,
    base_manifest: list[str],
    child_manifest: list[str],
) -> Path:
    repo_root = tmp_path / "repo"
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "packages" / "base" / "files").mkdir(parents=True)
    (repo_root / "packages" / "child" / "files").mkdir(parents=True)
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    (repo_root / "packages" / "base" / "files" / "config.txt").write_text("base\n", encoding="utf-8")
    (repo_root / "packages" / "child" / "files" / "config.txt").write_text("child\n", encoding="utf-8")
    (repo_root / "packages" / "base" / "package.toml").write_text(
        "\n".join(
            [
                'id = "base"',
                *base_manifest,
                "",
                "[targets.config]",
                'source = "files/config.txt"',
                'path = "~/.config/base/config.txt"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "child" / "package.toml").write_text(
        "\n".join(
            [
                'id = "child"',
                'extends = ["base"]',
                *child_manifest,
                "",
            ]
        ),
        encoding="utf-8",
    )
    return repo_root


def write_hook_metadata_repo(
    tmp_path: Path,
    *,
    package_manifest: list[str],
    package_id: str = "app",
) -> Path:
    repo_root = tmp_path / "repo"
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "packages" / package_id).mkdir(parents=True)
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    (repo_root / "packages" / package_id / "package.toml").write_text(
        "\n".join(
            [
                f'id = "{package_id}"',
                *package_manifest,
                "",
            ]
        ),
        encoding="utf-8",
    )
    return repo_root


def write_repo_and_target_hook_repo(
    tmp_path: Path,
    *,
    repo_manifest: list[str] | None = None,
    package_manifest: list[str] | None = None,
    child_manifest: list[str] | None = None,
) -> Path:
    repo_root = tmp_path / "repo"
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "packages" / "app" / "files").mkdir(parents=True)
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    (repo_root / "packages" / "app" / "files" / "config.txt").write_text("config\n", encoding="utf-8")
    if repo_manifest is not None:
        (repo_root / "repo.toml").write_text("\n".join([*repo_manifest, ""]), encoding="utf-8")
    (repo_root / "packages" / "app" / "package.toml").write_text(
        "\n".join(
            [
                'id = "app"',
                *(package_manifest or []),
                "",
                "[targets.config]",
                'source = "files/config.txt"',
                'path = "~/.config/app/config.txt"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    if child_manifest is not None:
        (repo_root / "packages" / "child" / "files").mkdir(parents=True)
        (repo_root / "packages" / "child" / "files" / "config.txt").write_text("child\n", encoding="utf-8")
        (repo_root / "packages" / "child" / "package.toml").write_text(
            "\n".join(
                [
                    'id = "child"',
                    'extends = ["app"]',
                    *child_manifest,
                    "",
                ]
            ),
            encoding="utf-8",
        )
    return repo_root


def write_target_ref_repo(
    tmp_path: Path,
    *,
    alpha_manifest: list[str] | None = None,
    beta_manifest: list[str] | None = None,
    gamma_manifest: list[str] | None = None,
    include_alpha_shared_target: bool = True,
) -> Path:
    repo_root = tmp_path / "repo"
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")

    (repo_root / "packages" / "alpha" / "files").mkdir(parents=True)
    (repo_root / "packages" / "alpha" / "files" / "shared.conf").write_text("shared\n", encoding="utf-8")
    alpha_target_lines = [
        "",
        "[targets.shared]",
        'source = "files/shared.conf"',
        'path = "~/.config/shared.conf"',
    ] if include_alpha_shared_target else []
    (repo_root / "packages" / "alpha" / "package.toml").write_text(
        "\n".join(
            [
                'id = "alpha"',
                *(alpha_manifest or []),
                *alpha_target_lines,
                "",
            ]
        ),
        encoding="utf-8",
    )

    if beta_manifest is not None:
        (repo_root / "packages" / "beta").mkdir(parents=True)
        (repo_root / "packages" / "beta" / "package.toml").write_text(
            "\n".join(
                [
                    'id = "beta"',
                    *beta_manifest,
                    "",
                ]
            ),
            encoding="utf-8",
        )

    if gamma_manifest is not None:
        (repo_root / "packages" / "gamma").mkdir(parents=True)
        (repo_root / "packages" / "gamma" / "package.toml").write_text(
            "\n".join(
                [
                    'id = "gamma"',
                    *gamma_manifest,
                    "",
                ]
            ),
            encoding="utf-8",
        )

    return repo_root


def test_example_push_plan_renders_package_defaults_profile_and_local_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    engine = DotmanEngine.from_config_path(write_manager_config(tmp_path))

    plan = single_package_plan(engine, "example:git@basic", operation="push")

    assert plan.repo_name == "example"
    assert plan.package_id == "git"
    assert plan.requested_profile == "basic"
    assert [hook.command for hook in plan.hooks["pre_push"]] == [
        "printf 'install %s\\n' git",
        'sh "$DOTMAN_REPO_ROOT/scripts/log-package-event.sh" "install-packages" "$DOTMAN_PACKAGE_ID"',
    ]

    target = plan.target_plans[0]
    assert target.package_id == "git"
    assert target.target_name == "gitconfig"
    assert target.action == "create"
    assert target.live_path == home / ".gitconfig"
    assert "name = Example User" in target.desired_text
    assert "email = local@example.test" in target.desired_text
    assert "editor = nvim" in target.desired_text
    assert "[include]" not in target.desired_text


def test_default_sync_policy_keeps_targets_in_both_push_and_pull_plans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = write_sync_policy_repo(tmp_path, package_manifest=[])
    engine = DotmanEngine.from_config_path(write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root))

    push_plan = single_package_plan(engine, "fixture:app@default", operation="push")
    pull_plan = single_package_plan(engine, "fixture:app@default", operation="pull")

    assert [target.target_name for target in push_plan.target_plans] == ["config"]
    assert [target.target_name for target in pull_plan.target_plans] == ["config"]


def test_target_sync_policy_overrides_package_sync_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = write_sync_policy_repo(
        tmp_path,
        package_manifest=['sync_policy = "push-only"'],
        target_manifest=['sync_policy = "pull-only"'],
    )
    engine = DotmanEngine.from_config_path(write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root))

    push_plan = single_package_plan(engine, "fixture:app@default", operation="push")
    pull_plan = single_package_plan(engine, "fixture:app@default", operation="pull")

    assert engine.get_repo("fixture").resolve_package("app").sync_policy == "push-only"
    assert push_plan.target_plans == []
    assert push_plan.hooks == {}
    assert [target.target_name for target in pull_plan.target_plans] == ["config"]


def test_hook_filtering_stays_quiet_when_no_targets_are_eligible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = write_sync_policy_repo(
        tmp_path,
        package_manifest=['sync_policy = "pull-only"'],
        hook_manifest=['[hooks]', 'pre_push = ["echo push"]'],
    )
    engine = DotmanEngine.from_config_path(write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root))

    push_plan = single_package_plan(engine, "fixture:app@default", operation="push")

    assert push_plan.target_plans == []
    assert push_plan.hooks == {}


def test_package_hook_table_form_parses_run_noop_metadata(
    tmp_path: Path,
) -> None:
    repo_root = write_hook_metadata_repo(
        tmp_path,
        package_manifest=[
            "[hooks.pre_push]",
            'commands = ["echo pre", { run = "echo tty", io = "tty" }]',
            "run_noop = true",
        ],
    )

    engine = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )

    hook = engine.get_repo("fixture").resolve_package("app").hooks["pre_push"]
    assert hook.commands == (
        HookCommandSpec(run="echo pre"),
        HookCommandSpec(run="echo tty", io="tty"),
    )
    assert hook.run_noop is True


def test_package_hook_shorthand_defaults_run_noop_false(
    tmp_path: Path,
) -> None:
    repo_root = write_hook_metadata_repo(
        tmp_path,
        package_manifest=[
            "[hooks]",
            'pre_push = ["echo pre"]',
        ],
    )

    engine = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )

    hook = engine.get_repo("fixture").resolve_package("app").hooks["pre_push"]
    assert hook.commands == (HookCommandSpec(run="echo pre"),)
    assert hook.run_noop is False


def test_package_hook_planning_preserves_per_command_io_and_json_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = write_sync_policy_repo(
        tmp_path,
        package_manifest=[],
        hook_manifest=[
            "[hooks.pre_push]",
            'commands = ["echo prep", { run = "echo tty", io = "tty" }]',
        ],
    )
    engine = DotmanEngine.from_config_path(write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root))

    push_plan = single_package_plan(engine, "fixture:app@default", operation="push")

    assert [hook.command for hook in push_plan.hooks["pre_push"]] == ["echo prep", "echo tty"]
    assert [hook.io for hook in push_plan.hooks["pre_push"]] == ["pipe", "tty"]
    assert push_plan.to_dict()["hooks"]["pre_push"][1]["io"] == "tty"


@pytest.mark.parametrize(
    ("package_manifest", "error_match"),
    [
        (
            [
                "[hooks.pre_push]",
                'commands = [{ run = "echo pre", nope = true }]',
            ],
            r"command object has unsupported keys: nope",
        ),
        (
            [
                "[hooks.pre_push]",
                'commands = [{ run = "   " }]',
            ],
            r"command object 'run' must not be empty",
        ),
        (
            [
                "[hooks.pre_push]",
                'commands = [{ run = "echo pre", io = "bad" }]',
            ],
            r"unsupported io 'bad'; expected one of: pipe, tty",
        ),
        (
            [
                "[hooks]",
                'pre_push = ["echo pre", { run = "echo tty", io = "tty" }]',
            ],
            r"commands must contain only strings",
        ),
    ],
)
def test_package_hook_command_objects_fail_fast_on_invalid_shapes(
    tmp_path: Path,
    package_manifest: list[str],
    error_match: str,
) -> None:
    repo_root = write_hook_metadata_repo(tmp_path, package_manifest=package_manifest)

    with pytest.raises(ValueError, match=error_match):
        DotmanEngine.from_config_path(write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root))


def test_package_hook_table_form_allows_empty_commands_and_skips_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = write_sync_policy_repo(
        tmp_path,
        package_manifest=[],
        hook_manifest=[
            "[hooks.post_push]",
            "commands = []",
            "run_noop = true",
        ],
    )
    engine = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )

    hook = engine.get_repo("fixture").resolve_package("app").hooks["post_push"]
    assert hook.commands == ()
    assert hook.run_noop is True

    push_plan = single_package_plan(engine, "fixture:app@default", operation="push")
    assert push_plan.hooks == {}


def test_package_hook_empty_override_disables_inherited_hook(
    tmp_path: Path,
) -> None:
    repo_root = write_sync_policy_repo_with_extends(
        tmp_path,
        base_manifest=[
            "[hooks.pre_push]",
            'commands = ["echo base"]',
            "run_noop = true",
        ],
        child_manifest=[
            "[hooks.pre_push]",
            "commands = []",
            "run_noop = false",
        ],
    )

    engine = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )

    hook = engine.get_repo("fixture").resolve_package("child").hooks["pre_push"]
    assert hook.commands == ()
    assert hook.run_noop is False


def test_package_hook_override_replaces_metadata_when_merging_extends(
    tmp_path: Path,
) -> None:
    repo_root = write_sync_policy_repo_with_extends(
        tmp_path,
        base_manifest=[
            "[hooks.pre_push]",
            'commands = ["echo base"]',
            "run_noop = false",
        ],
        child_manifest=[
            "[hooks.pre_push]",
            'commands = ["echo child"]',
            "run_noop = true",
        ],
    )

    engine = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )

    hook = engine.get_repo("fixture").resolve_package("child").hooks["pre_push"]
    assert hook.commands == (HookCommandSpec(run="echo child"),)
    assert hook.run_noop is True


def test_repo_hook_table_form_parses_run_noop_metadata(
    tmp_path: Path,
) -> None:
    repo_root = write_repo_and_target_hook_repo(
        tmp_path,
        repo_manifest=[
            "[hooks.pre_push]",
            'commands = [{ run = "echo repo", io = "tty" }]',
            "run_noop = true",
        ],
    )

    engine = DotmanEngine.from_config_path(write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root))

    hook = engine.get_repo("fixture").hooks["pre_push"]
    assert hook.commands == (HookCommandSpec(run="echo repo", io="tty"),)
    assert hook.run_noop is True

    operation_plan = engine.plan_push_query("fixture:app@default")
    assert operation_plan.repo_hooks["fixture"]["pre_push"][0].io == "tty"


def test_target_hook_table_form_parses_run_noop_metadata(
    tmp_path: Path,
) -> None:
    repo_root = write_repo_and_target_hook_repo(
        tmp_path,
        package_manifest=[
            "[targets.config.hooks.pre_push]",
            'commands = [{ run = "echo target", io = "tty" }]',
            "run_noop = true",
        ],
    )

    engine = DotmanEngine.from_config_path(write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root))

    hook = engine.get_repo("fixture").resolve_package("app").targets["config"].hooks["pre_push"]
    assert hook.commands == (HookCommandSpec(run="echo target", io="tty"),)
    assert hook.run_noop is True

    push_plan = single_package_plan(engine, "fixture:app@default", operation="push")
    assert push_plan.hooks["pre_push"][0].io == "tty"


def test_target_hook_override_replaces_metadata_when_merging_extends(
    tmp_path: Path,
) -> None:
    repo_root = write_repo_and_target_hook_repo(
        tmp_path,
        package_manifest=[
            "[targets.config.hooks.pre_push]",
            'commands = ["echo base target"]',
            "run_noop = false",
        ],
        child_manifest=[
            "[targets.config]",
            'source = "files/config.txt"',
            'path = "~/.config/child/config.txt"',
            "",
            "[targets.config.hooks.pre_push]",
            'commands = ["echo child target"]',
            "run_noop = true",
        ],
    )

    engine = DotmanEngine.from_config_path(write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root))

    hook = engine.get_repo("fixture").resolve_package("child").targets["config"].hooks["pre_push"]
    assert hook.commands == (HookCommandSpec(run="echo child target"),)
    assert hook.run_noop is True


def test_target_refs_manifest_is_rejected_like_other_unknown_keys(
    tmp_path: Path,
) -> None:
    repo_root = write_target_ref_repo(
        tmp_path,
        beta_manifest=[
            "[target_refs]",
            'shared = "alpha.shared"',
        ],
    )

    with pytest.raises(
        ValueError,
        match=r"package manifest .+ has unknown top-level keys: target_refs",
    ):
        DotmanEngine.from_config_path(write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root))


def test_package_sync_policy_is_inherited_through_extends(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = write_sync_policy_repo_with_extends(
        tmp_path,
        base_manifest=['sync_policy = "pull-only"'],
        child_manifest=[],
    )
    engine = DotmanEngine.from_config_path(write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root))

    child_package = engine.get_repo("fixture").resolve_package("child")
    push_plan = single_package_plan(engine, "fixture:child@default", operation="push")
    pull_plan = single_package_plan(engine, "fixture:child@default", operation="pull")

    assert child_package.sync_policy == "pull-only"
    assert push_plan.target_plans == []
    assert [target.target_name for target in pull_plan.target_plans] == ["config"]


@pytest.mark.parametrize(
    ("package_manifest", "target_manifest"),
    [
        (['sync_policy = "sideways"'], None),
        ([], ['sync_policy = "sideways"']),
    ],
)
def test_sync_policy_rejects_invalid_values(
    tmp_path: Path,
    package_manifest: list[str],
    target_manifest: list[str] | None,
) -> None:
    repo_root = write_sync_policy_repo(
        tmp_path,
        package_manifest=package_manifest,
        target_manifest=target_manifest,
    )

    with pytest.raises(ValueError, match="sync_policy"):
        DotmanEngine.from_config_path(write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root))

def test_example_group_push_plan_expands_depends_and_render_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    engine = DotmanEngine.from_config_path(write_manager_config(tmp_path))

    operation_plan = engine.plan_push_query("example:os/arch@basic")

    assert [plan.package_id for plan in operation_plan.package_plans] == ["core-cli-meta", "git", "nvim"]
    assert {plan.selection_label for plan in operation_plan.package_plans} == {
        "example:os/arch@basic",
        "example:git@basic",
        "example:nvim@basic",
    }
    assert {target.package_id for plan in operation_plan.package_plans for target in plan.target_plans} == {"git", "nvim"}

    nvim_target = next(
        target
        for plan in operation_plan.package_plans
        for target in plan.target_plans
        if target.package_id == "nvim"
    )
    assert nvim_target.projection_kind == "command"
    assert nvim_target.desired_text == 'vim.g.mapleader = " "\nvim.cmd.colorscheme("industry")\n'


def test_meta_package_depends_on_group_expands_group_members(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "fixture-repo"
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "groups").mkdir(parents=True)
    (repo_root / "packages" / "tooling-meta").mkdir(parents=True)
    (repo_root / "packages" / "git" / "files").mkdir(parents=True)
    (repo_root / "packages" / "nvim" / "files").mkdir(parents=True)

    (repo_root / "profiles" / "basic.toml").write_text("", encoding="utf-8")
    (repo_root / "groups" / "tooling.toml").write_text('members = ["git", "nvim"]\n', encoding="utf-8")
    (repo_root / "packages" / "tooling-meta" / "package.toml").write_text(
        '\n'.join([
            'id = "tooling-meta"',
            'depends = ["tooling"]',
            '',
        ]),
        encoding="utf-8",
    )
    (repo_root / "packages" / "git" / "files" / "git.conf").write_text("git\n", encoding="utf-8")
    (repo_root / "packages" / "git" / "package.toml").write_text(
        '\n'.join([
            'id = "git"',
            '',
            '[targets.git]',
            'source = "files/git.conf"',
            'path = "~/.gitconfig"',
            '',
        ]),
        encoding="utf-8",
    )
    (repo_root / "packages" / "nvim" / "files" / "init.lua").write_text("nvim\n", encoding="utf-8")
    (repo_root / "packages" / "nvim" / "package.toml").write_text(
        '\n'.join([
            'id = "nvim"',
            '',
            '[targets.nvim]',
            'source = "files/init.lua"',
            'path = "~/.config/nvim/init.lua"',
            '',
        ]),
        encoding="utf-8",
    )

    engine = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )

    operation_plan = engine.plan_push_query("fixture:tooling-meta@basic")

    assert [plan.package_id for plan in operation_plan.package_plans] == ["tooling-meta", "git", "nvim"]
    assert {plan.selection_label for plan in operation_plan.package_plans} == {
        "fixture:tooling-meta@basic",
        "fixture:git@basic",
        "fixture:nvim@basic",
    }
    assert {target.package_id for plan in operation_plan.package_plans for target in plan.target_plans} == {"git", "nvim"}


def test_dependency_resolution_allows_mixed_package_and_group_cycles_without_revisiting_packages(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "fixture-repo"
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "groups").mkdir(parents=True)
    (repo_root / "packages" / "alpha" / "files").mkdir(parents=True)
    (repo_root / "packages" / "beta" / "files").mkdir(parents=True)

    (repo_root / "profiles" / "basic.toml").write_text("", encoding="utf-8")
    (repo_root / "groups" / "bundle.toml").write_text('members = ["beta"]\n', encoding="utf-8")
    (repo_root / "packages" / "alpha" / "files" / "alpha.txt").write_text("alpha\n", encoding="utf-8")
    (repo_root / "packages" / "alpha" / "package.toml").write_text(
        '\n'.join([
            'id = "alpha"',
            'depends = ["bundle"]',
            '',
            '[targets.alpha]',
            'source = "files/alpha.txt"',
            'path = "~/.config/alpha.txt"',
            '',
        ]),
        encoding="utf-8",
    )
    (repo_root / "packages" / "beta" / "files" / "beta.txt").write_text("beta\n", encoding="utf-8")
    (repo_root / "packages" / "beta" / "package.toml").write_text(
        '\n'.join([
            'id = "beta"',
            'depends = ["alpha"]',
            '',
            '[targets.beta]',
            'source = "files/beta.txt"',
            'path = "~/.config/beta.txt"',
            '',
        ]),
        encoding="utf-8",
    )

    engine = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )

    operation_plan = engine.plan_push_query("fixture:alpha@basic")

    assert [plan.package_id for plan in operation_plan.package_plans] == ["alpha", "beta"]
    assert {plan.selection_label for plan in operation_plan.package_plans} == {
        "fixture:alpha@basic",
        "fixture:beta@basic",
    }
    assert {target.package_id for plan in operation_plan.package_plans for target in plan.target_plans} == {"alpha", "beta"}


def test_example_extends_preserves_child_values_after_local_merge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    engine = DotmanEngine.from_config_path(write_manager_config(tmp_path))

    plan = single_package_plan(engine, "example:work/git@work", operation="push")

    target = plan.target_plans[0]
    assert "name = Work User" in target.desired_text
    assert "email = local@example.test" in target.desired_text
    assert "path = ~/.config/git/includes/work.inc" in target.desired_text

def test_pull_plan_uses_declared_repo_and_live_views_for_rendered_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    (home / ".config" / "nvim").mkdir(parents=True)
    (home / ".config" / "nvim" / "init.lua").write_text(
        'vim.g.mapleader = " "\nvim.cmd.colorscheme("industry")\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))

    engine = DotmanEngine.from_config_path(write_manager_config(tmp_path))

    plan = single_package_plan(engine, "example:nvim@basic", operation="pull")

    target = plan.target_plans[0]
    assert target.pull_view_repo == "render"
    assert target.pull_view_live == "raw"
    assert target.action == "noop"
    assert target.reconcile_command == "sh hooks/reconcile.sh"
    assert target.reconcile == HookCommandSpec(run="sh hooks/reconcile.sh", io="tty")

def test_target_preset_jinja_editor_expands_default_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    (repo_root / "packages" / "shell" / "files").mkdir(parents=True)
    (repo_root / "profiles").mkdir()
    (repo_root / "packages" / "shell" / "package.toml").write_text(
        "\n".join(
            [
                'id = "shell"',
                "",
                "[targets.profile]",
                'source = "files/profile"',
                'path = "~/.profile"',
                'preset = "jinja-editor"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "shell" / "files" / "profile").write_text(
        "{% include 'env.core.sh' %}\n",
        encoding="utf-8",
    )
    (repo_root / "packages" / "shell" / "files" / "env.core.sh").write_text(
        "export XDG_CONFIG_HOME=\"${XDG_CONFIG_HOME:-$HOME/.config}\"\n",
        encoding="utf-8",
    )
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    (home / ".profile").write_text("export XDG_CONFIG_HOME=\"${XDG_CONFIG_HOME:-$HOME/.config}\"\n", encoding="utf-8")

    config_path = write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)

    engine = DotmanEngine.from_config_path(config_path)

    push_plan = single_package_plan(engine, "fixture:shell@default", operation="push")
    pull_plan = single_package_plan(engine, "fixture:shell@default", operation="pull")

    push_target = push_plan.target_plans[0]
    pull_target = pull_plan.target_plans[0]
    assert push_target.render_command == "jinja"
    assert pull_target.pull_view_repo == "render"
    assert pull_target.pull_view_live == "raw"
    assert pull_target.reconcile_command == "jinja"
    assert pull_target.reconcile == HookCommandSpec(run="jinja", io="tty")


def test_target_preset_jinja_patch_expands_default_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    (repo_root / "packages" / "shell" / "files").mkdir(parents=True)
    (repo_root / "profiles").mkdir()
    (repo_root / "packages" / "shell" / "package.toml").write_text(
        "\n".join(
            [
                'id = "shell"',
                "",
                "[targets.profile]",
                'source = "files/profile"',
                'path = "~/.profile"',
                'preset = "jinja-patch"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "shell" / "files" / "profile").write_text(
        "{% include 'env.core.sh' %}\n",
        encoding="utf-8",
    )
    (repo_root / "packages" / "shell" / "files" / "env.core.sh").write_text(
        "export XDG_CONFIG_HOME=\"${XDG_CONFIG_HOME:-$HOME/.config}\"\n",
        encoding="utf-8",
    )
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    (home / ".profile").write_text("export XDG_CONFIG_HOME=\"${XDG_CONFIG_HOME:-$HOME/.config}\"\n", encoding="utf-8")

    config_path = write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)

    engine = DotmanEngine.from_config_path(config_path)

    push_plan = single_package_plan(engine, "fixture:shell@default", operation="push")
    pull_plan = single_package_plan(engine, "fixture:shell@default", operation="pull")

    push_target = push_plan.target_plans[0]
    pull_target = pull_plan.target_plans[0]
    assert push_target.render_command == "jinja"
    assert push_target.capture_command == "patch"
    assert pull_target.pull_view_repo == "render"
    assert pull_target.pull_view_live == "raw"
    assert pull_target.capture_command == "patch"


def test_target_preset_jinja_patch_editor_expands_default_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    (repo_root / "packages" / "shell" / "files").mkdir(parents=True)
    (repo_root / "profiles").mkdir()
    (repo_root / "packages" / "shell" / "package.toml").write_text(
        "\n".join(
            [
                'id = "shell"',
                "",
                "[targets.profile]",
                'source = "files/profile"',
                'path = "~/.profile"',
                'preset = "jinja-patch-editor"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "shell" / "files" / "profile").write_text(
        "{% include 'env.core.sh' %}\n",
        encoding="utf-8",
    )
    (repo_root / "packages" / "shell" / "files" / "env.core.sh").write_text(
        "export XDG_CONFIG_HOME=\"${XDG_CONFIG_HOME:-$HOME/.config}\"\n",
        encoding="utf-8",
    )
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    (home / ".profile").write_text("export XDG_CONFIG_HOME=\"${XDG_CONFIG_HOME:-$HOME/.config}\"\n", encoding="utf-8")

    config_path = write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)

    engine = DotmanEngine.from_config_path(config_path)

    push_plan = single_package_plan(engine, "fixture:shell@default", operation="push")
    pull_plan = single_package_plan(engine, "fixture:shell@default", operation="pull")

    push_target = push_plan.target_plans[0]
    pull_target = pull_plan.target_plans[0]
    assert push_target.render_command == "jinja"
    assert push_target.capture_command == "patch"
    assert push_target.reconcile_command == "jinja"
    assert push_target.reconcile == HookCommandSpec(run="jinja", io="tty")
    assert pull_target.pull_view_repo == "render"
    assert pull_target.pull_view_live == "raw"
    assert pull_target.capture_command == "patch"
    assert pull_target.reconcile_command == "jinja"
    assert pull_target.reconcile == HookCommandSpec(run="jinja", io="tty")


def test_capture_patch_rejects_directory_targets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    (repo_root / "packages" / "shell" / "files" / "profile").mkdir(parents=True)
    (repo_root / "profiles").mkdir()
    (repo_root / "packages" / "shell" / "package.toml").write_text(
        "\n".join(
            [
                'id = "shell"',
                "",
                "[targets.profile]",
                'source = "files/profile"',
                'path = "~/.profile"',
                'preset = "jinja-patch"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")

    config_path = write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)

    engine = DotmanEngine.from_config_path(config_path)

    with pytest.raises(ValueError, match="capture = \"patch\" requires a file target"):
        engine.plan_pull_query("fixture:shell@default")


def test_capture_patch_rejects_raw_review_views(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    (repo_root / "packages" / "shell" / "files").mkdir(parents=True)
    (repo_root / "profiles").mkdir()
    (repo_root / "packages" / "shell" / "package.toml").write_text(
        "\n".join(
            [
                'id = "shell"',
                "",
                "[targets.profile]",
                'source = "files/profile"',
                'path = "~/.profile"',
                'render = "jinja"',
                'capture = "patch"',
                'pull_view_repo = "raw"',
                'pull_view_live = "raw"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "shell" / "files" / "profile").write_text(
        "{% include 'env.core.sh' %}\n",
        encoding="utf-8",
    )
    (repo_root / "packages" / "shell" / "files" / "env.core.sh").write_text(
        "export XDG_CONFIG_HOME=\"${XDG_CONFIG_HOME:-$HOME/.config}\"\n",
        encoding="utf-8",
    )
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    (home / ".profile").write_text("export XDG_CONFIG_HOME=\"${XDG_CONFIG_HOME:-$HOME/.config}\"\n", encoding="utf-8")

    config_path = write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)

    engine = DotmanEngine.from_config_path(config_path)

    with pytest.raises(ValueError, match="pull_view_repo = \"render\" and pull_view_live = \"raw\""):
        engine.plan_pull_query("fixture:shell@default")



def test_capture_patch_requires_render_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    (repo_root / "packages" / "shell" / "files").mkdir(parents=True)
    (repo_root / "profiles").mkdir()
    (repo_root / "packages" / "shell" / "package.toml").write_text(
        "\n".join(
            [
                'id = "shell"',
                "",
                "[targets.profile]",
                'source = "files/profile"',
                'path = "~/.profile"',
                'capture = "patch"',
                'pull_view_repo = "render"',
                'pull_view_live = "raw"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "shell" / "files" / "profile").write_text("greeting = hello\n", encoding="utf-8")
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    (home / ".profile").write_text("greeting = hello\n", encoding="utf-8")

    config_path = write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)

    engine = DotmanEngine.from_config_path(config_path)

    with pytest.raises(ValueError, match='capture = "patch" requires render'):
        engine.plan_pull_query("fixture:shell@default")



def test_capture_patch_accepts_command_renderers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    (repo_root / "packages" / "shell" / "files").mkdir(parents=True)
    (repo_root / "profiles").mkdir()
    render_command = 'sed "s/@@greeting@@/$DOTMAN_VAR_greeting/g" "$DOTMAN_SOURCE"'
    (repo_root / "packages" / "shell" / "package.toml").write_text(
        "\n".join(
            [
                'id = "shell"',
                "",
                '[vars]',
                'greeting = "hello"',
                "",
                "[targets.profile]",
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
    (repo_root / "packages" / "shell" / "files" / "profile").write_text("greeting = @@greeting@@\n", encoding="utf-8")
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    (home / ".profile").write_text("greeting = world\n", encoding="utf-8")

    config_path = write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)

    engine = DotmanEngine.from_config_path(config_path)
    pull_plan = single_package_plan(engine, "fixture:shell@default", operation="pull")

    target = pull_plan.target_plans[0]
    assert target.action == "update"
    assert target.render_command == render_command
    assert target.capture_command == "patch"
    assert target.review_before_bytes == b"greeting = hello\n"
    assert target.review_after_bytes == b"greeting = world\n"



def test_target_preset_values_can_be_overridden(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    (repo_root / "packages" / "shell" / "files").mkdir(parents=True)
    (repo_root / "profiles").mkdir()
    (repo_root / "packages" / "shell" / "package.toml").write_text(
        "\n".join(
            [
                'id = "shell"',
                "",
                "[targets.profile]",
                'source = "files/profile"',
                'path = "~/.profile"',
                'preset = "jinja-editor"',
                'import_view_repo = "raw"',
                'reconcile = { run = "jinja", io = "pipe" }',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "shell" / "files" / "profile").write_text("hello\n", encoding="utf-8")
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    (home / ".profile").write_text("hello\n", encoding="utf-8")

    config_path = write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)

    engine = DotmanEngine.from_config_path(config_path)

    target = single_package_plan(engine, "fixture:shell@default", operation="pull").target_plans[0]

    assert target.render_command == "jinja"
    assert target.pull_view_repo == "raw"
    assert target.pull_view_live == "raw"
    assert target.reconcile_command == "jinja"
    assert target.reconcile == HookCommandSpec(run="jinja", io="pipe")


def test_unknown_target_preset_fails_engine_load(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "packages" / "shell").mkdir(parents=True)
    (repo_root / "profiles").mkdir()
    (repo_root / "packages" / "shell" / "package.toml").write_text(
        "\n".join(
            [
                'id = "shell"',
                "",
                "[targets.profile]",
                'source = "files/profile"',
                'path = "~/.profile"',
                'preset = "missing"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")

    config_path = write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)

    with pytest.raises(ValueError, match="unknown preset 'missing'"):
        DotmanEngine.from_config_path(config_path)



def test_pull_plan_preserves_builtin_jinja_reconcile_shortcut(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    (repo_root / "packages" / "shell" / "files").mkdir(parents=True)
    (repo_root / "profiles").mkdir()
    (repo_root / "packages" / "shell" / "package.toml").write_text(
        "\n".join(
            [
                'id = "shell"',
                "",
                "[targets.profile]",
                'source = "files/profile"',
                'path = "~/.profile"',
                'render = "jinja"',
                'pull_view_repo = "render"',
                'pull_view_live = "raw"',
                'reconcile = { run = "jinja", io = "tty" }',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "shell" / "files" / "profile").write_text(
        "{% include 'env.core.sh' %}\n",
        encoding="utf-8",
    )
    (repo_root / "packages" / "shell" / "files" / "env.core.sh").write_text(
        "export XDG_CONFIG_HOME=\"${XDG_CONFIG_HOME:-$HOME/.config}\"\n",
        encoding="utf-8",
    )
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    (home / ".profile").write_text("export XDG_CONFIG_HOME=\"${XDG_CONFIG_HOME:-$HOME/.config}\"\n", encoding="utf-8")

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[repos.fixture]",
                f'path = "{repo_root}"',
                "order = 10",
                "",
            ]
        ),
        encoding="utf-8",
    )

    engine = DotmanEngine.from_config_path(config_path)

    plan = single_package_plan(engine, "fixture:shell@default", operation="pull")

    target = plan.target_plans[0]
    assert target.reconcile_command == "jinja"
    assert target.reconcile == HookCommandSpec(run="jinja", io="tty")



def test_plain_file_with_jinja_markers_requires_explicit_render(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    (repo_root / "packages" / "shell" / "files").mkdir(parents=True)
    (repo_root / "profiles").mkdir()
    (repo_root / "packages" / "shell" / "package.toml").write_text(
        "\n".join(
            [
                'id = "shell"',
                "",
                "[targets.profile]",
                'source = "files/profile"',
                'path = "~/.profile"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "shell" / "files" / "profile").write_text(
        "profile={{ profile }}\n",
        encoding="utf-8",
    )
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[repos.fixture]",
                f'path = "{repo_root}"',
                "order = 10",
                "",
            ]
        ),
        encoding="utf-8",
    )

    engine = DotmanEngine.from_config_path(config_path)

    plan = single_package_plan(engine, "fixture:shell@default", operation="push")

    assert plan.target_plans[0].projection_kind == "raw"
    assert plan.target_plans[0].desired_text == "profile={{ profile }}\n"


def test_template_file_render_supports_relative_include(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    (repo_root / "packages" / "shell" / "files").mkdir(parents=True)
    (repo_root / "profiles").mkdir()
    (repo_root / "packages" / "shell" / "package.toml").write_text(
        "\n".join(
            [
                'id = "shell"',
                "",
                "[targets.profile]",
                'source = "files/profile"',
                'path = "~/.profile"',
                'render = "jinja"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "shell" / "files" / "profile").write_text(
        "\n".join(
            [
                "export SHELL_PROFILE=1",
                "{% include 'env.core.sh' %}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "shell" / "files" / "env.core.sh").write_text(
        "export CORE_ENV=1\n",
        encoding="utf-8",
    )
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[repos.fixture]",
                f'path = "{repo_root}"',
                "order = 10",
                "",
            ]
        ),
        encoding="utf-8",
    )

    engine = DotmanEngine.from_config_path(config_path)

    plan = single_package_plan(engine, "fixture:shell@default", operation="push")

    assert plan.target_plans[0].projection_kind == "template"
    assert plan.target_plans[0].desired_text.strip().splitlines() == [
        "export SHELL_PROFILE=1",
        "export CORE_ENV=1",
    ]

def test_sandbox_host_plan_composes_profile_vars_and_namespaced_packages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    engine = DotmanEngine.from_config_path(write_manager_config(tmp_path))

    operation_plan = engine.plan_push_query("host/linux-meta@host/linux")
    plans_by_package_id = {plan.package_id: plan for plan in operation_plan.package_plans}

    assert {plan.repo_name for plan in operation_plan.package_plans} == {"sandbox"}
    assert "linux/1password" in plans_by_package_id
    assert plans_by_package_id["host/linux-meta"].variables["desktop"] == "niri"
    assert plans_by_package_id["host/linux-meta"].variables["UV_RUN"] == 'uv run --project "$DOTMAN_REPO_ROOT"'

    sunshine_target = next(
        target
        for plan in operation_plan.package_plans
        for target in plan.target_plans
        if target.package_id == "sunshine" and target.target_name == "selected_config"
    )
    assert sunshine_target.repo_path.name == "sunshine-niri.conf"

def test_sandbox_nested_directory_and_file_targets_plan_without_collision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    engine = DotmanEngine.from_config_path(write_manager_config(tmp_path))

    plan = single_package_plan(engine, "gsettings@host/linux", operation="push")

    assert {target.target_name for target in plan.target_plans} == {
        "desktop",
        "nautilus",
        "gtk3_dir",
        "gtk3_settings",
        "gtk4_dir",
        "gtk4_settings",
    }

    gtk3_dir = next(target for target in plan.target_plans if target.target_name == "gtk3_dir")
    assert "settings.ini" in gtk3_dir.push_ignore
    assert "settings.ini" in gtk3_dir.pull_ignore

def test_repo_toml_pull_ignore_applies_to_directory_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    source_root = repo_root / "packages" / "sample" / "files" / "config"
    source_root.mkdir(parents=True)
    (repo_root / "profiles").mkdir()
    (repo_root / "repo.toml").write_text(
        "\n".join(
            [
                "[ignore]",
                'pull = ["*.bak"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "sample" / "package.toml").write_text(
        "\n".join(
            [
                'id = "sample"',
                "",
                "[targets.config]",
                'source = "files/config"',
                'path = "~/.config/sample"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (source_root / "tool.conf").write_text("value = 1\n", encoding="utf-8")
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")

    live_root = home / ".config" / "sample"
    live_root.mkdir(parents=True)
    (live_root / "tool.conf").write_text("value = 1\n", encoding="utf-8")
    (live_root / "tool.conf.bak").write_text("old value = 0\n", encoding="utf-8")

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[repos.fixture]",
                f'path = "{repo_root}"',
                "order = 10",
                "",
            ]
        ),
        encoding="utf-8",
    )

    engine = DotmanEngine.from_config_path(config_path)

    plan = single_package_plan(engine, "fixture:sample@default", operation="push")

    assert plan.target_plans[0].action == "noop"

def test_pull_plan_infers_directory_target_from_live_path_when_repo_source_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "packages" / "sample").mkdir(parents=True)
    (repo_root / "packages" / "sample" / "package.toml").write_text(
        "\n".join(
            [
                'id = "sample"',
                "",
                "[targets.config]",
                'source = "files/config"',
                'path = "~/.config/sample"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")

    live_root = home / ".config" / "sample"
    live_root.mkdir(parents=True)
    (live_root / "alpha.toml").write_text('value = "live alpha"\n', encoding="utf-8")
    (live_root / "gamma.toml").write_text('value = "live gamma"\n', encoding="utf-8")

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[repos.fixture]",
                f'path = "{repo_root}"',
                "order = 10",
                "",
            ]
        ),
        encoding="utf-8",
    )

    engine = DotmanEngine.from_config_path(config_path)

    plan = single_package_plan(engine, "fixture:sample@default", operation="pull")

    target = plan.target_plans[0]
    assert target.target_kind == "directory"
    assert target.action == "update"
    assert [(item.action, item.relative_path) for item in target.directory_items] == [
        ("create", "alpha.toml"),
        ("create", "gamma.toml"),
    ]


def test_push_plan_infers_directory_target_from_live_path_when_repo_source_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "packages" / "sample").mkdir(parents=True)
    (repo_root / "packages" / "sample" / "package.toml").write_text(
        "\n".join(
            [
                'id = "sample"',
                "",
                "[targets.config]",
                'source = "files/config"',
                'path = "~/.config/sample"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")

    live_root = home / ".config" / "sample"
    live_root.mkdir(parents=True)
    (live_root / "alpha.toml").write_text('value = "live alpha"\n', encoding="utf-8")

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[repos.fixture]",
                f'path = "{repo_root}"',
                "order = 10",
                "",
            ]
        ),
        encoding="utf-8",
    )

    engine = DotmanEngine.from_config_path(config_path)

    plan = single_package_plan(engine, "fixture:sample@default", operation="push")

    target = plan.target_plans[0]
    assert target.target_kind == "directory"
    assert target.action == "delete"
    assert [(item.action, item.relative_path) for item in target.directory_items] == [
        ("delete", "alpha.toml"),
    ]


def test_push_plan_marks_directory_target_unknown_when_repo_and_live_paths_are_both_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "packages" / "sample").mkdir(parents=True)
    (repo_root / "packages" / "sample" / "package.toml").write_text(
        "\n".join(
            [
                'id = "sample"',
                "",
                "[targets.config]",
                'source = "files/config"',
                'path = "~/.config/sample"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[repos.fixture]",
                f'path = "{repo_root}"',
                "order = 10",
                "",
            ]
        ),
        encoding="utf-8",
    )

    engine = DotmanEngine.from_config_path(config_path)

    plan = single_package_plan(engine, "fixture:sample@default", operation="push")

    target = plan.target_plans[0]
    assert target.target_kind == "unknown"
    assert target.action == "noop"
    assert target.directory_items == ()


def test_pull_plan_exposes_file_level_items_for_directory_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    source_root = repo_root / "packages" / "sample" / "files" / "config"
    source_root.mkdir(parents=True)
    (repo_root / "profiles").mkdir()
    (repo_root / "packages" / "sample" / "package.toml").write_text(
        "\n".join(
            [
                'id = "sample"',
                "",
                "[targets.config]",
                'source = "files/config"',
                'path = "~/.config/sample"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (source_root / "alpha.toml").write_text('value = "repo alpha"\n', encoding="utf-8")
    (source_root / "beta.toml").write_text('value = "repo beta"\n', encoding="utf-8")
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")

    live_root = home / ".config" / "sample"
    live_root.mkdir(parents=True)
    (live_root / "alpha.toml").write_text('value = "live alpha"\n', encoding="utf-8")
    (live_root / "gamma.toml").write_text('value = "live gamma"\n', encoding="utf-8")

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[repos.fixture]",
                f'path = "{repo_root}"',
                "order = 10",
                "",
            ]
        ),
        encoding="utf-8",
    )

    engine = DotmanEngine.from_config_path(config_path)

    plan = single_package_plan(engine, "fixture:sample@default", operation="pull")

    target = plan.target_plans[0]
    assert target.action == "update"
    assert [(item.action, item.relative_path) for item in target.directory_items] == [
        ("update", "alpha.toml"),
        ("delete", "beta.toml"),
        ("create", "gamma.toml"),
    ]

def test_push_plan_exposes_file_level_items_for_directory_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    source_root = repo_root / "packages" / "sample" / "files" / "config"
    source_root.mkdir(parents=True)
    (repo_root / "profiles").mkdir()
    (repo_root / "packages" / "sample" / "package.toml").write_text(
        "\n".join(
            [
                'id = "sample"',
                "",
                "[targets.config]",
                'source = "files/config"',
                'path = "~/.config/sample"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (source_root / "alpha.toml").write_text('value = "repo alpha"\n', encoding="utf-8")
    (source_root / "beta.toml").write_text('value = "repo beta"\n', encoding="utf-8")
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")

    live_root = home / ".config" / "sample"
    live_root.mkdir(parents=True)
    (live_root / "alpha.toml").write_text('value = "live alpha"\n', encoding="utf-8")
    (live_root / "gamma.toml").write_text('value = "live gamma"\n', encoding="utf-8")

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[repos.fixture]",
                f'path = "{repo_root}"',
                "order = 10",
                "",
            ]
        ),
        encoding="utf-8",
    )

    engine = DotmanEngine.from_config_path(config_path)

    plan = single_package_plan(engine, "fixture:sample@default", operation="push")

    target = plan.target_plans[0]
    assert target.action == "update"
    assert [(item.action, item.relative_path) for item in target.directory_items] == [
        ("update", "alpha.toml"),
        ("create", "beta.toml"),
        ("delete", "gamma.toml"),
    ]

def test_repo_toml_ignore_defaults_merge_with_target_ignore_for_directory_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    source_root = repo_root / "packages" / "sample" / "files" / "config"
    source_root.mkdir(parents=True)
    (repo_root / "profiles").mkdir()
    (repo_root / "repo.toml").write_text(
        "\n".join(
            [
                "[ignore]",
                'push = ["*.archived"]',
                'pull = ["*.bak"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "sample" / "package.toml").write_text(
        "\n".join(
            [
                'id = "sample"',
                "",
                "[targets.config]",
                'source = "files/config"',
                'path = "~/.config/sample"',
                'pull_ignore = ["keep.local"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (source_root / "tool.conf").write_text("value = 1\n", encoding="utf-8")
    (source_root / "old.archived").write_text("ignored\n", encoding="utf-8")
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")

    live_root = home / ".config" / "sample"
    live_root.mkdir(parents=True)
    (live_root / "tool.conf").write_text("value = 1\n", encoding="utf-8")
    (live_root / "tool.conf.bak").write_text("old value = 0\n", encoding="utf-8")
    (live_root / "keep.local").write_text("keep me\n", encoding="utf-8")

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[repos.fixture]",
                f'path = "{repo_root}"',
                "order = 10",
                "",
            ]
        ),
        encoding="utf-8",
    )

    engine = DotmanEngine.from_config_path(config_path)

    plan = single_package_plan(engine, "fixture:sample@default", operation="push")

    assert plan.target_plans[0].action == "noop"
    assert plan.target_plans[0].push_ignore == ("*.archived",)
    assert plan.target_plans[0].pull_ignore == ("*.bak", "keep.local")
