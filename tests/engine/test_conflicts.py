from __future__ import annotations

import json
from pathlib import Path

import pytest

from dotman.engine import DotmanEngine
from dotman.models import FullSpecSelector
from tests.helpers import (
    EXAMPLE_REPO,
    REFERENCE_REPO,
    write_manager_config,
    write_multi_instance_repo,
    write_package_override_preview_repo,
    write_single_repo_config,
    write_untrack_conflict_repo,
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

def test_remove_binding_rejects_removal_that_exposes_implicit_conflict(
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

    with pytest.raises(
        ValueError,
        match=(
            r"cannot untrack 'fixture:shared@direct': removing this binding would expose "
            r"conflicting implicit tracked targets for .+shared\.conf: "
            r"fixture:shared@personal -> fixture:shared\.shared, fixture:shared@work -> fixture:shared\.shared"
        ),
    ):
        engine.remove_tracked_package_entry("fixture:shared@direct")

    assert (state_dir / "tracked-packages.toml").read_text(encoding="utf-8") == original_state
