from __future__ import annotations

import json
from pathlib import Path

import pytest

from dotman.engine import DotmanEngine
from test_support import (
    EXAMPLE_REPO,
    REFERENCE_REPO,
    write_manager_config,
    write_multi_instance_repo,
    write_package_override_preview_repo,
    write_single_repo_config,
    write_untrack_conflict_repo,
)


def test_tracked_push_plan_drops_hooks_for_packages_without_winning_targets(
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
                "[[bindings]]",
                'repo = "example"',
                'selector = "work/git"',
                'profile = "work"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    engine = DotmanEngine.from_config_path(config_path)

    plans_by_selector = {plan.binding.selector: plan for plan in engine.plan_push()}

    os_arch_plan = plans_by_selector["os/arch"]
    assert {target.package_id for target in os_arch_plan.target_plans} == {"nvim"}
    assert os_arch_plan.hooks == {}

    work_git_plan = plans_by_selector["work/git"]
    assert {target.package_id for target in work_git_plan.target_plans} == {"work/git"}
    assert set(work_git_plan.hooks) == {"guard_push", "pre_push", "post_push"}
    assert {hook.package_id for hook in work_git_plan.hooks["pre_push"]} == {"work/git"}

def test_group_selected_package_is_marked_explicit_in_tracked_detail(
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

    package_detail = engine.describe_installed_package("example:core-cli-meta")

    assert [binding.tracked_reason for binding in package_detail.bindings] == ["explicit"]

def test_info_tracked_drops_hooks_for_non_effective_provenance_binding(
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

    package_detail = engine.describe_installed_package("example:git")

    assert [binding.binding.selector for binding in package_detail.bindings] == ["core-cli-meta", "git"]
    assert package_detail.bindings[0].hooks == {}
    assert set(package_detail.bindings[1].hooks) == {"guard_push", "pre_push", "post_push"}

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

def test_plan_push_prefers_explicit_targets_over_implicit_targets(
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
                "[[bindings]]",
                'repo = "example"',
                'selector = "work/git"',
                'profile = "work"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    engine = DotmanEngine.from_config_path(config_path)

    plans = engine.plan_push()
    plans_by_selector = {plan.binding.selector: plan for plan in plans}

    assert {target.package_id for target in plans_by_selector["os/arch"].target_plans} == {"nvim"}
    assert {target.package_id for target in plans_by_selector["work/git"].target_plans} == {"work/git"}
    assert "Work User" in plans_by_selector["work/git"].target_plans[0].desired_text

def test_preview_binding_implicit_overrides_returns_unique_packages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "override-preview-repo"
    write_package_override_preview_repo(repo_root)
    config_path = write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    engine = DotmanEngine.from_config_path(config_path)

    engine.record_binding(engine.resolve_binding("fixture:beta-stack@basic")[1])

    overrides = engine.preview_binding_implicit_overrides(engine.resolve_binding("fixture:alpha@basic")[1])

    assert len(overrides) == 1
    override = overrides[0]
    assert override.winner.binding_label == "fixture:alpha@basic"
    assert override.winner.package_id == "alpha"
    assert [contender.binding_label for contender in override.overridden] == ["fixture:beta-stack@basic"]
    assert [contender.package_id for contender in override.overridden] == ["beta"]

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

def test_record_binding_keeps_distinct_profiles_for_multi_instance_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    write_multi_instance_repo(repo_root)
    config_path = write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    engine = DotmanEngine.from_config_path(config_path)

    engine.record_binding(engine.plan_push_binding("fixture:profiled@basic").binding)
    engine.record_binding(engine.plan_push_binding("fixture:profiled@work").binding)

    bindings = engine.read_bindings(engine.get_repo("fixture"))
    assert [(binding.selector, binding.profile) for binding in bindings] == [
        ("profiled", "basic"),
        ("profiled", "work"),
    ]

    packages = engine.list_installed_packages()
    assert [(package.package_ref, package.bound_profile) for package in packages] == [
        ("profiled<basic>", "basic"),
        ("profiled<work>", "work"),
    ]

    package_detail = engine.describe_installed_package("fixture:profiled<work>")
    assert package_detail.package_ref == "profiled<work>"
    assert package_detail.bound_profile == "work"
    assert [binding.binding.profile for binding in package_detail.bindings] == ["work"]

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
