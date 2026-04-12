from __future__ import annotations

from pathlib import Path

import pytest

from dotman.engine import DotmanEngine, list_directory_files, matches_ignore_pattern
from tests.helpers import write_single_repo_config


def test_gitignore_style_recursive_directory_patterns_ignore_nested_pycache_files(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    source_root = repo_root / "packages" / "sample" / "files" / "config"
    source_root.mkdir(parents=True)
    (repo_root / "profiles").mkdir()
    (repo_root / "packages" / "sample" / "package.toml").write_text(
        "\n".join(
            [
                'id = "sample"',
                '',
                '[targets.config]',
                'source = "files/config"',
                'path = "~/.config/sample"',
                'push_ignore = ["**/__pycache__/"]',
                '',
            ]
        ),
        encoding="utf-8",
    )
    (source_root / "visible.conf").write_text("visible = true\n", encoding="utf-8")
    (source_root / "nested" / "__pycache__").mkdir(parents=True)
    (source_root / "nested" / "__pycache__" / "cached.pyc").write_text(
        "compiled\n",
        encoding="utf-8",
    )
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")

    files = list_directory_files(source_root, ("**/__pycache__/",))

    assert "visible.conf" in files
    assert "nested/__pycache__/cached.pyc" not in files


def test_gitignore_style_root_anchored_patterns_only_match_from_target_root() -> None:
    assert matches_ignore_pattern("foo", "/foo")
    assert not matches_ignore_pattern("nested/foo", "/foo")


def test_basename_only_ignore_patterns_still_match_nested_files() -> None:
    assert matches_ignore_pattern("foo/bookmarks", "bookmarks")
    assert matches_ignore_pattern("gtk-3.0/settings.ini", "settings.ini")


def test_negated_ignore_patterns_can_reinclude_specific_files(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "keep.pyc").write_text("keep\n", encoding="utf-8")
    (root / "drop.pyc").write_text("drop\n", encoding="utf-8")

    files = list_directory_files(root, ("*.pyc", "!keep.pyc"))

    assert "keep.pyc" in files
    assert "drop.pyc" not in files


def test_negated_directory_patterns_do_not_reinclude_still_ignored_descendant_files(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "plugins" / "pinned-window").mkdir(parents=True)
    (root / "plugins" / "pinned-window" / "BarWidget.qml.dotdropbak").write_text(
        "backup\n",
        encoding="utf-8",
    )
    (root / "plugins" / "pinned-window" / "BarWidget.qml").write_text(
        "live\n",
        encoding="utf-8",
    )

    files = list_directory_files(root, ("**/*.dotdropbak", "!plugins/pinned-window/"))

    assert "plugins/pinned-window/BarWidget.qml" in files
    assert "plugins/pinned-window/BarWidget.qml.dotdropbak" not in files


def test_directory_target_push_ignore_uses_gitignore_semantics_for_nested_pycache_files(
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
                '',
                '[targets.config]',
                'source = "files/config"',
                'path = "~/.config/sample"',
                'push_ignore = ["**/__pycache__/"]',
                '',
            ]
        ),
        encoding="utf-8",
    )
    (source_root / "visible.conf").write_text("visible = true\n", encoding="utf-8")
    (source_root / "nested" / "__pycache__").mkdir(parents=True)
    (source_root / "nested" / "__pycache__" / "cached.pyc").write_text(
        "compiled\n",
        encoding="utf-8",
    )
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")

    engine = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )

    plan = engine.plan_push_binding("fixture:sample@default")

    target = plan.target_plans[0]
    assert target.action == "create"
    assert [item.relative_path for item in target.directory_items] == ["visible.conf"]


def test_directory_target_pull_ignore_preserves_gitignore_style_nested_pycache_files_during_push_cleanup(
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
                '',
                '[targets.config]',
                'source = "files/config"',
                'path = "~/.config/sample"',
                'pull_ignore = ["**/__pycache__/"]',
                '',
            ]
        ),
        encoding="utf-8",
    )
    (source_root / "visible.conf").write_text("visible = true\n", encoding="utf-8")
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")

    live_root = home / ".config" / "sample"
    live_root.mkdir(parents=True)
    (live_root / "visible.conf").write_text("visible = true\n", encoding="utf-8")
    (live_root / "nested" / "__pycache__").mkdir(parents=True)
    (live_root / "nested" / "__pycache__" / "cached.pyc").write_text(
        "compiled\n",
        encoding="utf-8",
    )

    engine = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )

    plan = engine.plan_push_binding("fixture:sample@default")

    target = plan.target_plans[0]
    assert target.action == "noop"
    assert [item.relative_path for item in target.directory_items] == []
