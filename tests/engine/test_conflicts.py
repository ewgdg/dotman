from __future__ import annotations

import json
from pathlib import Path

import pytest

from dotman.engine import DotmanEngine
from dotman.models import FullSpecSelector, TrackedPackageEntry
from tests.helpers import (
    EXAMPLE_REPO,
    REFERENCE_REPO,
    write_manager_config,
    write_multi_instance_repo,
    write_package_override_preview_repo,
    write_profile_ambiguous_dependency_repo,
    write_single_repo_config,
    write_untrack_conflict_repo,
)


def write_tracked_packages_state(state_root: Path, *, repo_name: str, entries: list[tuple[str, str]]) -> None:
    state_dir = state_root / "dotman" / "repos" / repo_name
    state_dir.mkdir(parents=True, exist_ok=True)
    lines = ["schema_version = 1", ""]
    for package_id, profile in entries:
        lines.extend(
            [
                "[[packages]]",
                f'repo = "{repo_name}"',
                f'package_id = "{package_id}"',
                f'profile = "{profile}"',
                "",
            ]
        )
    (state_dir / "tracked-packages.toml").write_text("\n".join(lines), encoding="utf-8")


def write_same_live_path_repo(repo_root: Path, *, same_source_bytes: bool = True) -> None:
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "packages" / "alpha" / "files").mkdir(parents=True)
    (repo_root / "packages" / "beta" / "files").mkdir(parents=True)
    (repo_root / "packages" / "alpha-meta").mkdir(parents=True)
    (repo_root / "packages" / "beta-meta").mkdir(parents=True)
    (repo_root / "profiles" / "basic.toml").write_text("", encoding="utf-8")
    (repo_root / "packages" / "alpha" / "files" / "shared.conf").write_text("same\n", encoding="utf-8")
    (repo_root / "packages" / "beta" / "files" / "shared.conf").write_text(
        "same\n" if same_source_bytes else "different\n",
        encoding="utf-8",
    )
    for package_id in ("alpha", "beta"):
        (repo_root / "packages" / package_id / "package.toml").write_text(
            "\n".join(
                [
                    f'id = "{package_id}"',
                    "",
                    "[targets.shared]",
                    'source = "files/shared.conf"',
                    'path = "~/.config/shared.conf"',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (repo_root / "packages" / f"{package_id}-meta" / "package.toml").write_text(
            "\n".join(
                [
                    f'id = "{package_id}-meta"',
                    f'depends = ["{package_id}"]',
                    "",
                ]
            ),
            encoding="utf-8",
        )


def write_static_multi_instance_repo(repo_root: Path) -> None:
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "packages" / "profiled" / "files").mkdir(parents=True)
    (repo_root / "profiles" / "basic.toml").write_text("", encoding="utf-8")
    (repo_root / "profiles" / "work.toml").write_text("", encoding="utf-8")
    (repo_root / "packages" / "profiled" / "files" / "managed.conf").write_text("same\n", encoding="utf-8")
    (repo_root / "packages" / "profiled" / "package.toml").write_text(
        "\n".join(
            [
                'id = "profiled"',
                'binding_mode = "multi_instance"',
                "",
                "[targets.managed]",
                'source = "files/managed.conf"',
                'path = "~/.config/profiled.conf"',
                "",
            ]
        ),
        encoding="utf-8",
    )


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
        engine.plan_push_query("fixture:all@default")

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
        engine.plan_push_query("fixture:all@default")

def test_record_binding_rejects_conflicting_explicit_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    engine = DotmanEngine.from_config_path(write_manager_config(tmp_path))

    _repo, selector = engine.resolve_full_spec_selector_text("example:git@basic")
    engine.record_tracked_package_entry(selector)

    with pytest.raises(
        ValueError,
        match=r"conflicting explicit tracked targets for .+\.gitconfig: example:git@basic -> example:git\.gitconfig, example:work/git@work -> example:work/git\.gitconfig",
    ):
        _repo, selector = engine.resolve_full_spec_selector_text("example:work/git@work")
        engine.record_tracked_package_entry(selector)


def test_record_binding_rejects_same_signature_explicit_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "fixture-repo"
    write_same_live_path_repo(repo_root, same_source_bytes=True)
    engine = DotmanEngine.from_config_path(write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root))

    _repo, selector = engine.resolve_full_spec_selector_text("fixture:alpha@basic")
    engine.record_tracked_package_entry(selector)

    with pytest.raises(
        ValueError,
        match=r"conflicting explicit tracked targets for .+shared\.conf: fixture:alpha@basic -> fixture:alpha\.shared, fixture:beta@basic -> fixture:beta\.shared",
    ):
        _repo, selector = engine.resolve_full_spec_selector_text("fixture:beta@basic")
        engine.record_tracked_package_entry(selector)


def test_record_binding_rejects_same_signature_implicit_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "fixture-repo"
    write_same_live_path_repo(repo_root, same_source_bytes=True)
    engine = DotmanEngine.from_config_path(write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root))

    _repo, selector = engine.resolve_full_spec_selector_text("fixture:alpha-meta@basic")
    engine.record_tracked_package_entry(selector)

    with pytest.raises(
        ValueError,
        match=r"conflicting implicit tracked targets for .+shared\.conf: fixture:alpha@basic -> fixture:alpha\.shared, fixture:beta@basic -> fixture:beta\.shared",
    ):
        _repo, selector = engine.resolve_full_spec_selector_text("fixture:beta-meta@basic")
        engine.record_tracked_package_entry(selector)


def test_record_binding_rejects_multi_instance_same_live_path_profiles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "fixture-repo"
    write_static_multi_instance_repo(repo_root)
    engine = DotmanEngine.from_config_path(write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root))

    _repo, selector = engine.resolve_full_spec_selector_text("fixture:profiled@basic")
    engine.record_tracked_package_entry(selector)

    with pytest.raises(
        ValueError,
        match=r"conflicting explicit tracked targets for .+profiled\.conf: fixture:profiled@basic -> fixture:profiled<basic>\.managed, fixture:profiled@work -> fixture:profiled<work>\.managed",
    ):
        _repo, selector = engine.resolve_full_spec_selector_text("fixture:profiled@work")
        engine.record_tracked_package_entry(selector)


def test_track_rejects_singleton_implicit_dependency_profile_ambiguity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "fixture-repo"
    write_profile_ambiguous_dependency_repo(repo_root)
    engine = DotmanEngine.from_config_path(write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root))

    _repo, selector = engine.resolve_full_spec_selector_text("fixture:meta-a@basic")
    engine.record_tracked_package_entry(selector)

    with pytest.raises(
        ValueError,
        match=(
            r"ambiguous implicit profile contexts for fixture:shared:\n"
            r"  fixture:shared@basic required by fixture:meta-a@basic\n"
            r"  fixture:shared@work required by fixture:meta-b@work"
        ),
    ):
        _repo, selector = engine.resolve_full_spec_selector_text("fixture:meta-b@work")
        engine.record_tracked_package_entry(selector)


def test_plan_push_fails_for_invalid_singleton_implicit_dependency_profile_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "fixture-repo"
    write_profile_ambiguous_dependency_repo(repo_root)
    config_path = write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    write_tracked_packages_state(
        tmp_path / "state",
        repo_name="fixture",
        entries=[("meta-a", "basic"), ("meta-b", "work")],
    )
    engine = DotmanEngine.from_config_path(config_path)

    with pytest.raises(ValueError, match=r"ambiguous implicit profile contexts for fixture:shared"):
        engine.plan_push()


def test_plan_pull_fails_for_invalid_singleton_implicit_dependency_profile_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "fixture-repo"
    write_profile_ambiguous_dependency_repo(repo_root)
    config_path = write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    write_tracked_packages_state(
        tmp_path / "state",
        repo_name="fixture",
        entries=[("meta-a", "basic"), ("meta-b", "work")],
    )
    engine = DotmanEngine.from_config_path(config_path)

    with pytest.raises(ValueError, match=r"ambiguous implicit profile contexts for fixture:shared"):
        engine.plan_pull()


def test_same_profile_singleton_implicit_dependency_dedupes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "fixture-repo"
    write_profile_ambiguous_dependency_repo(repo_root)
    engine = DotmanEngine.from_config_path(write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root))

    for selector_text in ("fixture:meta-a@basic", "fixture:meta-b@basic"):
        _repo, selector = engine.resolve_full_spec_selector_text(selector_text)
        engine.record_tracked_package_entry(selector)

    plan = engine.plan_push()

    assert [(item.package_id, item.requested_profile) for item in plan.package_plans] == [
        ("meta-a", "basic"),
        ("shared", "basic"),
        ("meta-b", "basic"),
    ]


def test_multi_instance_implicit_dependency_allows_different_profiles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "fixture-repo"
    write_profile_ambiguous_dependency_repo(repo_root, shared_binding_mode="multi_instance")
    engine = DotmanEngine.from_config_path(write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root))

    for selector_text in ("fixture:meta-a@basic", "fixture:meta-b@work"):
        _repo, selector = engine.resolve_full_spec_selector_text(selector_text)
        engine.record_tracked_package_entry(selector)

    plan = engine.plan_push()

    assert [(item.package_id, item.bound_profile, item.requested_profile) for item in plan.package_plans] == [
        ("meta-a", None, "basic"),
        ("shared", "basic", "basic"),
        ("meta-b", None, "work"),
        ("shared", "work", "work"),
    ]


def test_explicit_singleton_dependency_profile_suppresses_conflicting_implicit_profile_before_planning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "fixture-repo"
    write_profile_ambiguous_dependency_repo(repo_root)
    config_path = write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    write_tracked_packages_state(
        tmp_path / "state",
        repo_name="fixture",
        entries=[("shared", "work"), ("meta-a", "basic"), ("meta-b", "work")],
    )
    engine = DotmanEngine.from_config_path(config_path)

    plan = engine.plan_push()

    shared_plans = [item for item in plan.package_plans if item.package_id == "shared"]

    assert [(item.requested_profile, item.selection.explicit) for item in shared_plans] == [("work", True)]
    assert shared_plans[0].target_plans[0].desired_text == "profile=work\n"


def test_shared_resolver_rejects_conflicting_explicit_singleton_profiles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "fixture-repo"
    write_profile_ambiguous_dependency_repo(repo_root)
    engine = DotmanEngine.from_config_path(write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root))

    with pytest.raises(ValueError, match=r"conflicting explicit profile contexts for fixture:shared"):
        engine._planning_helpers().validate_tracked_package_ownership(
            engine,
            entries_by_repo={
                "fixture": [
                    TrackedPackageEntry(repo="fixture", package_id="shared", profile="basic"),
                    TrackedPackageEntry(repo="fixture", package_id="shared", profile="work"),
                ],
            },
        )


def test_remove_binding_rejects_resulting_singleton_dependency_profile_ambiguity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "fixture-repo"
    write_untrack_conflict_repo(repo_root)
    config_path = write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    state_dir = tmp_path / "state" / "dotman" / "repos" / "fixture"
    state_dir.mkdir(parents=True, exist_ok=True)
    original_state = "\n".join(
        [
            "schema_version = 1",
            "",
            "[[packages]]",
            'repo = "fixture"',
            'package_id = "shared"',
            'profile = "direct"',
            "",
            "[[packages]]",
            'repo = "fixture"',
            'package_id = "stack-a"',
            'profile = "work"',
            "",
            "[[packages]]",
            'repo = "fixture"',
            'package_id = "stack-b"',
            'profile = "personal"',
            "",
        ]
    )
    (state_dir / "tracked-packages.toml").write_text(original_state, encoding="utf-8")

    engine = DotmanEngine.from_config_path(config_path)

    with pytest.raises(ValueError, match=r"ambiguous implicit profile contexts for fixture:shared"):
        engine.remove_tracked_package_entry("fixture:shared@direct")

    assert (state_dir / "tracked-packages.toml").read_text(encoding="utf-8") == original_state
