from __future__ import annotations

import json
from pathlib import Path

import pytest

from dotman.engine import DotmanEngine


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_REPO = PROJECT_ROOT / "examples" / "repo"
REFERENCE_REPO = PROJECT_ROOT / "tests" / "fixtures" / "reference_repo"


def write_manager_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[repos.example]",
                f'path = "{EXAMPLE_REPO}"',
                "order = 10",
                f'state_path = "{tmp_path / "state" / "example"}"',
                "",
                "[repos.sandbox]",
                f'path = "{REFERENCE_REPO}"',
                "order = 20",
                f'state_path = "{tmp_path / "state" / "sandbox"}"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def test_config_validation_rejects_duplicate_repo_order(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[repos.one]",
                f'path = "{EXAMPLE_REPO}"',
                "order = 10",
                "",
                "[repos.two]",
                f'path = "{REFERENCE_REPO}"',
                "order = 10",
                "",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unique"):
        DotmanEngine.from_config_path(config_path)


def test_example_push_plan_renders_package_defaults_profile_and_local_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    engine = DotmanEngine.from_config_path(write_manager_config(tmp_path))

    plan = engine.plan_push_binding("example:git@basic")

    assert plan.binding.repo == "example"
    assert plan.binding.selector == "git"
    assert plan.binding.profile == "basic"
    assert plan.package_ids == ["git"]
    assert [hook.command for hook in plan.hooks["pre_push"]] == [
        "brew install git",
        '"$DOTMAN_REPO_ROOT/scripts/log-package-event.sh" "install-packages" "$DOTMAN_PACKAGE_ID"',
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


def test_example_group_push_plan_expands_depends_and_render_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    engine = DotmanEngine.from_config_path(write_manager_config(tmp_path))

    plan = engine.plan_push_binding("example:os/arch@basic")

    assert plan.binding.selector == "os/arch"
    assert plan.selector_kind == "group"
    assert plan.package_ids == ["core-cli-meta", "git", "nvim"]
    assert {target.package_id for target in plan.target_plans} == {"git", "nvim"}

    nvim_target = next(target for target in plan.target_plans if target.package_id == "nvim")
    assert nvim_target.projection_kind == "command"
    assert nvim_target.desired_text == 'vim.g.mapleader = " "\nvim.cmd.colorscheme("industry")\n'


def test_example_extends_preserves_child_values_after_local_merge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    engine = DotmanEngine.from_config_path(write_manager_config(tmp_path))

    plan = engine.plan_push_binding("example:work/git@work")

    assert plan.package_ids == ["work/git"]
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

    plan = engine.plan_pull_binding("example:nvim@basic")

    target = plan.target_plans[0]
    assert target.pull_view_repo == "render"
    assert target.pull_view_live == "raw"
    assert target.action == "noop"
    assert target.reconcile_command == "sh hooks/reconcile.sh"


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
                f'state_path = "{tmp_path / "state" / "fixture"}"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    engine = DotmanEngine.from_config_path(config_path)

    plan = engine.plan_push_binding("fixture:shell@default")

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

    plan = engine.plan_push_binding("host/linux-meta@host/linux")

    assert plan.binding.repo == "sandbox"
    assert "linux/1password" in plan.package_ids
    assert plan.variables["desktop"] == "niri"
    assert plan.variables["UV_RUN"] == 'uv run --project "$DOTMAN_REPO_ROOT"'

    sunshine_target = next(
        target
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

    plan = engine.plan_push_binding("gsettings@host/linux")

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
                f'state_path = "{tmp_path / "state" / "fixture"}"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    engine = DotmanEngine.from_config_path(config_path)

    plan = engine.plan_push_binding("fixture:sample@default")

    assert plan.target_plans[0].action == "noop"


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
                f'state_path = "{tmp_path / "state" / "fixture"}"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    engine = DotmanEngine.from_config_path(config_path)

    plan = engine.plan_pull_binding("fixture:sample@default")

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
                f'state_path = "{tmp_path / "state" / "fixture"}"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    engine = DotmanEngine.from_config_path(config_path)

    plan = engine.plan_push_binding("fixture:sample@default")

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
                f'state_path = "{tmp_path / "state" / "fixture"}"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    engine = DotmanEngine.from_config_path(config_path)

    plan = engine.plan_push_binding("fixture:sample@default")

    assert plan.target_plans[0].action == "noop"
    assert plan.target_plans[0].push_ignore == ("*.archived",)
    assert plan.target_plans[0].pull_ignore == ("*.bak", "keep.local")


def test_package_reserved_paths_conflict_with_other_package_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "groups").mkdir()
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    (repo_root / "groups" / "all.toml").write_text('members = ["alpha", "beta"]\n', encoding="utf-8")
    (repo_root / "packages" / "alpha" / "files").mkdir(parents=True)
    (repo_root / "packages" / "beta" / "files").mkdir(parents=True)
    (repo_root / "packages" / "alpha" / "files" / "alpha.conf").write_text("alpha = 1\n", encoding="utf-8")
    (repo_root / "packages" / "beta" / "files" / "beta.conf").write_text("beta = 1\n", encoding="utf-8")
    (repo_root / "packages" / "alpha" / "package.toml").write_text(
        "\n".join(
            [
                'id = "alpha"',
                'reserved_paths = ["~/.config/shared"]',
                "",
                "[targets.alpha]",
                'source = "files/alpha.conf"',
                'path = "~/.config/alpha/alpha.conf"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "beta" / "package.toml").write_text(
        "\n".join(
            [
                'id = "beta"',
                "",
                "[targets.beta]",
                'source = "files/beta.conf"',
                'path = "~/.config/shared/beta.conf"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[repos.fixture]",
                f'path = "{repo_root}"',
                "order = 10",
                f'state_path = "{tmp_path / "state" / "fixture"}"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    engine = DotmanEngine.from_config_path(config_path)

    with pytest.raises(
        ValueError,
        match=r"reserved path conflict: alpha reserves .+shared and beta:beta maps to .+shared/beta\.conf",
    ):
        engine.plan_push_binding("fixture:all@default")


def test_package_reserved_paths_conflict_with_other_package_reserved_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "groups").mkdir()
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    (repo_root / "groups" / "all.toml").write_text('members = ["alpha", "beta"]\n', encoding="utf-8")
    (repo_root / "packages" / "alpha").mkdir(parents=True)
    (repo_root / "packages" / "beta").mkdir(parents=True)
    (repo_root / "packages" / "alpha" / "package.toml").write_text(
        "\n".join(
            [
                'id = "alpha"',
                'reserved_paths = ["~/.cache/shared"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "beta" / "package.toml").write_text(
        "\n".join(
            [
                'id = "beta"',
                'reserved_paths = ["~/.cache/shared/session"]',
                "",
            ]
        ),
        encoding="utf-8",
    )

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[repos.fixture]",
                f'path = "{repo_root}"',
                "order = 10",
                f'state_path = "{tmp_path / "state" / "fixture"}"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    engine = DotmanEngine.from_config_path(config_path)

    with pytest.raises(
        ValueError,
        match=r"reserved path conflict: alpha reserves .+shared and beta reserves .+shared/session",
    ):
        engine.plan_push_binding("fixture:all@default")


def test_upgrade_uses_persisted_bindings_without_writing_new_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    config_path = write_manager_config(tmp_path)
    state_dir = tmp_path / "state" / "example"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "bindings.toml").write_text(
        "\n".join(
            [
                "version = 1",
                "",
                "[[bindings]]",
                'repo = "example"',
                'selector = "os/arch"',
                'profile = "basic"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    engine = DotmanEngine.from_config_path(config_path)

    plans = engine.plan_upgrade()

    assert len(plans) == 1
    assert plans[0].binding.selector == "os/arch"
    assert plans[0].package_ids == ["core-cli-meta", "git", "nvim"]
    assert not (state_dir / "bindings.toml").with_suffix(".tmp").exists()


def test_record_binding_writes_resolved_binding_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    config_path = write_manager_config(tmp_path)
    engine = DotmanEngine.from_config_path(config_path)
    plan = engine.plan_push_binding("example:git@basic")

    engine.record_binding(plan.binding)

    state_path = tmp_path / "state" / "example" / "bindings.toml"
    assert state_path.exists()
    assert state_path.read_text(encoding="utf-8") == "\n".join(
        [
            "version = 1",
            "",
            "[[bindings]]",
            'repo = "example"',
            'selector = "git"',
            'profile = "basic"',
            "",
        ]
    )


def test_record_binding_replaces_existing_selector_binding_with_new_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    config_path = write_manager_config(tmp_path)
    engine = DotmanEngine.from_config_path(config_path)
    state_dir = tmp_path / "state" / "example"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "bindings.toml").write_text(
        "\n".join(
            [
                "version = 1",
                "",
                "[[bindings]]",
                'repo = "example"',
                'selector = "git"',
                'profile = "basic"',
                "",
                "[[bindings]]",
                'repo = "example"',
                'selector = "nvim"',
                'profile = "basic"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    plan = engine.plan_push_binding("example:git@work")

    engine.record_binding(plan.binding)

    bindings = engine.read_bindings(engine.get_repo("example"))
    assert [(binding.selector, binding.profile) for binding in bindings] == [
        ("git", "work"),
        ("nvim", "basic"),
    ]
    assert not (state_dir / "bindings.toml").with_suffix(".tmp").exists()


def test_remove_binding_deletes_only_the_selected_tracked_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    config_path = write_manager_config(tmp_path)
    state_dir = tmp_path / "state" / "example"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "bindings.toml").write_text(
        "\n".join(
            [
                "version = 1",
                "",
                "[[bindings]]",
                'repo = "example"',
                'selector = "git"',
                'profile = "basic"',
                "",
                "[[bindings]]",
                'repo = "example"',
                'selector = "core-cli-meta"',
                'profile = "basic"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    engine = DotmanEngine.from_config_path(config_path)

    removed = engine.remove_binding("example:git@basic")

    assert removed.repo == "example"
    assert removed.selector == "git"
    assert removed.profile == "basic"
    assert engine.read_bindings(engine.get_repo("example")) == [
        removed.__class__(repo="example", selector="core-cli-meta", profile="basic")
    ]


def test_remove_binding_allows_selector_only_when_tracked_binding_is_unique(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    config_path = write_manager_config(tmp_path)
    state_dir = tmp_path / "state" / "example"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "bindings.toml").write_text(
        "\n".join(
            [
                "version = 1",
                "",
                "[[bindings]]",
                'repo = "example"',
                'selector = "git"',
                'profile = "basic"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    engine = DotmanEngine.from_config_path(config_path)

    removed = engine.remove_binding("example:git")

    assert removed == removed.__class__(repo="example", selector="git", profile="basic")
    assert engine.read_bindings(engine.get_repo("example")) == []


def test_remove_binding_reports_tracked_owner_when_selector_is_only_a_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    config_path = write_manager_config(tmp_path)
    state_dir = tmp_path / "state" / "example"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "bindings.toml").write_text(
        "\n".join(
            [
                "version = 1",
                "",
                "[[bindings]]",
                'repo = "example"',
                'selector = "core-cli-meta"',
                'profile = "basic"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    engine = DotmanEngine.from_config_path(config_path)

    with pytest.raises(
        ValueError,
        match=r"cannot untrack 'example:nvim': required by tracked bindings: example:core-cli-meta@basic",
    ):
        engine.remove_binding("nvim@basic")
