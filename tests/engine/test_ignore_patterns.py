from __future__ import annotations

from pathlib import Path

import pytest

from dotman.engine import DotmanEngine
from dotman.execution import build_execution_session, execute_session
from dotman.ignore import list_directory_files, matches_ignore_pattern
from tests.helpers import single_package_plan, write_single_repo_config


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
                '',
                '[targets.config.ignore]',
                'patterns = ["**/__pycache__/"]',
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


def test_skip_marker_skips_nested_directory_subtree(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "keep.txt").write_text("keep\n", encoding="utf-8")
    (root / "cache").mkdir()
    (root / "cache" / ".dotman-skip").write_text("", encoding="utf-8")
    (root / "cache" / "state.db").write_text("state\n", encoding="utf-8")

    files = list_directory_files(root, (), skip_markers=(".dotman-skip",))

    assert sorted(files) == ["keep.txt"]


def test_skip_marker_file_is_absent_from_results(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / ".dotman-skip").write_text("", encoding="utf-8")
    (root / "keep.txt").write_text("keep\n", encoding="utf-8")

    files = list_directory_files(root, (), skip_markers=(".dotman-skip",))

    assert files == {}


def test_no_prune_marker_config_treats_marker_as_normal_file(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / ".dotman-skip").write_text("", encoding="utf-8")
    (root / "keep.txt").write_text("keep\n", encoding="utf-8")

    files = list_directory_files(root, ())

    assert sorted(files) == [".dotman-skip", "keep.txt"]


def test_followed_directory_symlink_with_skip_marker_is_skipped_only_when_following(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    target = tmp_path / "target"
    root.mkdir()
    target.mkdir()
    (target / ".dotman-skip").write_text("", encoding="utf-8")
    (target / "state.db").write_text("state\n", encoding="utf-8")
    (root / "linked").symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="directory symlink encountered"):
        list_directory_files(root, (), skip_markers=(".dotman-skip",))

    files = list_directory_files(
        root,
        (),
        skip_markers=(".dotman-skip",),
        follow_dir_symlinks=True,
    )

    assert files == {}

def test_directory_target_ignore_push_uses_gitignore_semantics_for_nested_pycache_files(
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
                '',
                '[targets.config.ignore]',
                'patterns = ["**/__pycache__/"]',
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

    plan = single_package_plan(engine, "fixture:sample@default", operation="push")

    target = plan.target_plans[0]
    assert target.action == "create"
    assert [item.relative_path for item in target.directory_items] == ["visible.conf"]


def test_directory_target_ignore_push_preserves_gitignore_style_nested_pycache_files_during_push_cleanup(
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
                '',
                '[targets.config.ignore]',
                'patterns = ["**/__pycache__/"]',
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

    plan = single_package_plan(engine, "fixture:sample@default", operation="push")

    target = plan.target_plans[0]
    assert target.action == "noop"
    assert [item.relative_path for item in target.directory_items] == []


def test_directory_target_scan_rejects_nested_live_directory_symlink_by_default(
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
                '',
            ]
        ),
        encoding="utf-8",
    )
    (source_root / "visible.conf").write_text("visible = true\n", encoding="utf-8")
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")

    live_root = home / ".config" / "sample"
    linked_target = home / ".config" / "linked-real"
    live_root.mkdir(parents=True)
    linked_target.mkdir(parents=True)
    (live_root / "visible.conf").write_text("visible = true\n", encoding="utf-8")
    (live_root / "linked").symlink_to(linked_target, target_is_directory=True)

    engine = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )

    with pytest.raises(ValueError, match="directory symlink encountered while scanning directory: linked"):
        single_package_plan(engine, "fixture:sample@default", operation="push")


def test_directory_target_scan_allows_ignored_nested_live_directory_symlink(
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
                '',
                '[targets.config.ignore]',
                'patterns = ["linked/"]',
                '',
            ]
        ),
        encoding="utf-8",
    )
    (source_root / "visible.conf").write_text("visible = true\n", encoding="utf-8")
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")

    live_root = home / ".config" / "sample"
    linked_target = home / ".config" / "linked-real"
    live_root.mkdir(parents=True)
    linked_target.mkdir(parents=True)
    (live_root / "visible.conf").write_text("visible = true\n", encoding="utf-8")
    (linked_target / "hidden.conf").write_text("hidden = true\n", encoding="utf-8")
    (live_root / "linked").symlink_to(linked_target, target_is_directory=True)

    engine = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )

    plan = single_package_plan(engine, "fixture:sample@default", operation="push")

    target = plan.target_plans[0]
    assert target.action == "noop"
    assert [item.relative_path for item in target.directory_items] == []


def test_directory_target_scan_follows_nested_live_directory_symlink_when_enabled(
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
                '',
            ]
        ),
        encoding="utf-8",
    )
    (source_root / "visible.conf").write_text("visible = true\n", encoding="utf-8")
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")

    live_root = home / ".config" / "sample"
    linked_target = home / ".config" / "linked-real"
    live_root.mkdir(parents=True)
    linked_target.mkdir(parents=True)
    (live_root / "visible.conf").write_text("visible = true\n", encoding="utf-8")
    (linked_target / "extra.conf").write_text("extra = true\n", encoding="utf-8")
    (live_root / "linked").symlink_to(linked_target, target_is_directory=True)

    engine = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root),
        dir_symlink_mode="follow",
    )

    plan = single_package_plan(engine, "fixture:sample@default", operation="push")

    target = plan.target_plans[0]
    assert target.action == "update"
    assert [(item.action, item.relative_path) for item in target.directory_items] == [("delete", "linked/extra.conf")]


def test_directory_target_scan_rejects_nested_directory_symlink_loop_when_following(
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
    (live_root / "loop").symlink_to(live_root, target_is_directory=True)

    engine = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root),
        dir_symlink_mode="follow",
    )

    with pytest.raises(ValueError, match="directory symlink loop encountered while scanning directory: loop"):
        single_package_plan(engine, "fixture:sample@default", operation="push")


def test_directory_target_push_skip_marker_preserves_live_subtree_during_cleanup(
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
        "\n".join(["[ignore]", 'skip_markers = [".dotman-skip"]', ""]),
        encoding="utf-8",
    )
    (repo_root / "packages" / "sample" / "package.toml").write_text(
        "\n".join(
            [
                'id = "sample"',
                '',
                '[targets.config]',
                'source = "files/config"',
                'path = "~/.config/sample"',
                '',
            ]
        ),
        encoding="utf-8",
    )
    (source_root / "tool.conf").write_text("value = 1\n", encoding="utf-8")
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")

    live_root = home / ".config" / "sample"
    live_root.mkdir(parents=True)
    (live_root / "tool.conf").write_text("value = 1\n", encoding="utf-8")
    (live_root / "cache").mkdir()
    (live_root / "cache" / ".dotman-skip").write_text("", encoding="utf-8")
    (live_root / "cache" / "local-state").write_text("keep\n", encoding="utf-8")

    engine = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )

    plan = single_package_plan(engine, "fixture:sample@default", operation="push")

    target = plan.target_plans[0]
    assert target.action == "noop"
    assert target.directory_items == ()


def test_repo_toml_loads_skip_markers_from_ignore_table(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "packages").mkdir()
    (repo_root / "repo.toml").write_text(
        "\n".join(["[ignore]", 'skip_markers = [".dotman-skip"]', ""]),
        encoding="utf-8",
    )

    engine = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )

    assert engine.get_repo("fixture").ignore_defaults.skip_markers == (".dotman-skip",)


def test_repo_toml_rejects_prune_marker_path_names(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "repo.toml").write_text(
        "\n".join(["[ignore]", 'skip_markers = ["nested/.dotman-skip"]', ""]),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="skip_markers entries must be basenames"):
        DotmanEngine.from_config_path(
            write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
        )


def test_repo_toml_rejects_empty_prune_marker_names(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "repo.toml").write_text(
        "\n".join(["[ignore]", 'skip_markers = [""]', ""]),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="skip_markers entries must not be empty"):
        DotmanEngine.from_config_path(
            write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
        )


def test_repo_toml_loads_gitignore_from_ignore_table(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "packages").mkdir()
    (repo_root / "repo.toml").write_text(
        "\n".join(["[ignore]", 'gitignore = true', ""]),
        encoding="utf-8",
    )

    engine = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )

    assert engine.get_repo("fixture").ignore_defaults.gitignore is True


def test_repo_toml_gitignore_defaults_to_disabled(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "packages").mkdir()
    (repo_root / "repo.toml").write_text(
        "\n".join(["[ignore]", 'skip_markers = [".dotman-skip"]', ""]),
        encoding="utf-8",
    )

    engine = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )

    assert engine.get_repo("fixture").ignore_defaults.gitignore is False


def test_repo_toml_rejects_invalid_gitignore_values(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "repo.toml").write_text(
        "\n".join(["[ignore]", 'gitignore = 1', ""]),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="gitignore must be a boolean"):
        DotmanEngine.from_config_path(
            write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
        )


def test_directory_target_applies_gitignore_patterns_during_push(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
                "[targets.config.ignore]",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (source_root / "visible.conf").write_text("visible = true\n", encoding="utf-8")
    (source_root / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (source_root / "app.log").write_text("log\n", encoding="utf-8")
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    (repo_root / "repo.toml").write_text("[ignore]\ngitignore = true\n", encoding="utf-8")

    engine = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )

    plan = single_package_plan(engine, "fixture:sample@default", operation="push")

    target = plan.target_plans[0]
    assert target.action == "create"
    rel_paths = [item.relative_path for item in target.directory_items]
    assert "visible.conf" in rel_paths
    assert "app.log" not in rel_paths


def test_directory_target_gitignore_applies_to_both_repo_and_live_scans_during_push(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
                "[targets.config.ignore]",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (source_root / "visible.conf").write_text("visible = true\n", encoding="utf-8")
    (source_root / ".gitignore").write_text("*.local\n", encoding="utf-8")
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    (repo_root / "repo.toml").write_text("[ignore]\ngitignore = true\n", encoding="utf-8")

    live_root = home / ".config" / "sample"
    live_root.mkdir(parents=True)
    (live_root / "visible.conf").write_text("visible = true\n", encoding="utf-8")
    (live_root / "machine.local").write_text("local\n", encoding="utf-8")

    engine = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )

    plan = single_package_plan(engine, "fixture:sample@default", operation="push")

    target = plan.target_plans[0]
    assert target.action == "noop"
    assert [item.relative_path for item in target.directory_items] == []


def test_gitignore_control_files_are_not_reincluded_by_negation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
                "[targets.config.ignore]",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (source_root / ".gitignore").write_text("!.gitignore\n!nested/.gitignore\n", encoding="utf-8")
    (source_root / "nested").mkdir()
    (source_root / "nested" / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
    (source_root / "visible.conf").write_text("visible = true\n", encoding="utf-8")
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    (repo_root / "repo.toml").write_text("[ignore]\ngitignore = true\n", encoding="utf-8")

    engine = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )

    plan = single_package_plan(engine, "fixture:sample@default", operation="push")

    rel_paths = {item.relative_path for item in plan.target_plans[0].directory_items}
    assert "visible.conf" in rel_paths
    assert ".gitignore" not in rel_paths
    assert "nested/.gitignore" not in rel_paths


def test_explicit_ignore_can_override_gitignore_with_negation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
                "[targets.config.ignore]",
                'patterns = ["!important.log"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (source_root / "visible.conf").write_text("visible = true\n", encoding="utf-8")
    (source_root / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (source_root / "important.log").write_text("important\n", encoding="utf-8")
    (source_root / "trash.log").write_text("trash\n", encoding="utf-8")
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    (repo_root / "repo.toml").write_text("[ignore]\ngitignore = true\n", encoding="utf-8")

    engine = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )

    plan = single_package_plan(engine, "fixture:sample@default", operation="push")

    target = plan.target_plans[0]
    assert target.action == "create"
    items = [(item.relative_path, item.action) for item in target.directory_items]
    assert ("visible.conf", "create") in items
    assert ("important.log", "create") in items
    assert not any("trash.log" in ref for ref, _ in items)


def test_package_ignore_patterns_are_resolved_and_applied_during_push(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
                "[ignore]",
                'patterns = ["*.secret"]',
                "",
                "[targets.config]",
                'source = "files/config"',
                'path = "~/.config/sample"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (source_root / "visible.conf").write_text("visible = true\n", encoding="utf-8")
    (source_root / "machine.secret").write_text("secret\n", encoding="utf-8")
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")

    engine = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )

    package = engine.get_repo("fixture").resolve_package("sample")
    assert package.ignore_patterns == ("*.secret",)

    plan = single_package_plan(engine, "fixture:sample@default", operation="push")
    target = plan.target_plans[0]
    assert [item.relative_path for item in target.directory_items] == ["visible.conf"]

    result = execute_session(
        build_execution_session([plan], operation="push"),
        stream_output=False,
    )
    assert result.status == "ok"
    live_root = home / ".config" / "sample"
    assert (live_root / "visible.conf").read_text(encoding="utf-8") == "visible = true\n"
    assert not (live_root / "machine.secret").exists()


def test_package_gitignore_enablement_is_resolved_and_applied_during_push(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
                "[ignore]",
                'gitignore = true',
                "",
                "[targets.config]",
                'source = "files/config"',
                'path = "~/.config/sample"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (source_root / "visible.conf").write_text("visible = true\n", encoding="utf-8")
    (source_root / ".gitignore").write_text("*.local\n", encoding="utf-8")
    (source_root / "machine.local").write_text("local\n", encoding="utf-8")
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")

    engine = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )

    package = engine.get_repo("fixture").resolve_package("sample")
    assert package.ignore_patterns is None
    assert package.gitignore_enabled is True

    plan = single_package_plan(engine, "fixture:sample@default", operation="push")
    target = plan.target_plans[0]
    assert [item.relative_path for item in target.directory_items] == ["visible.conf"]

    result = execute_session(
        build_execution_session([plan], operation="push"),
        stream_output=False,
    )
    assert result.status == "ok"
    live_root = home / ".config" / "sample"
    assert (live_root / "visible.conf").read_text(encoding="utf-8") == "visible = true\n"
    assert not (live_root / "machine.local").exists()


def test_repository_ignore_patterns_are_direction_independent(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "packages").mkdir()
    (repo_root / "repo.toml").write_text(
        "[ignore]\npatterns = [\"*.cache\"]\n",
        encoding="utf-8",
    )
    engine = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )
    defaults = engine.get_repo("fixture").ignore_defaults
    assert defaults.patterns == ("*.cache",)
    assert not hasattr(defaults, "push")
    assert not hasattr(defaults, "pull")


def test_ignore_patterns_compose_repo_package_target_in_order_and_keep_empty_package_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repo_root = tmp_path / "repo"
    source_root = repo_root / "packages" / "sample" / "files" / "config"
    source_root.mkdir(parents=True)
    (repo_root / "profiles").mkdir()
    (repo_root / "repo.toml").write_text(
        '[ignore]\npatterns = ["*.tmp"]\ngitignore = true\n',
        encoding="utf-8",
    )
    (repo_root / "packages" / "sample" / "package.toml").write_text(
        '\n'.join(
            [
                'id = "sample"',
                "",
                "[ignore]",
                "patterns = []",
                "",
                "[targets.config]",
                'source = "files/config"',
                'path = "~/.config/sample"',
                "",
                "[targets.config.ignore]",
                'patterns = ["!keep.log"]',
            ]
        ),
        encoding="utf-8",
    )
    (source_root / ".gitignore").write_text("*.log\n", encoding="utf-8")
    for name in ("keep.tmp", "drop.tmp", "keep.log", "drop.log"):
        (source_root / name).write_text(name + "\n", encoding="utf-8")
    (source_root / "visible.conf").write_text("visible\n", encoding="utf-8")
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")

    engine = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )
    target = single_package_plan(engine, "fixture:sample@default", operation="push").target_plans[0]
    paths = {item.relative_path for item in target.directory_items}

    assert "visible.conf" in paths
    assert "keep.tmp" not in paths
    assert "drop.tmp" not in paths
    assert "keep.log" in paths
    assert "drop.log" not in paths


def test_ignore_pattern_order_preserves_later_repeated_exclusion_after_negation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repo_root = tmp_path / "repo"
    source_root = repo_root / "packages" / "sample" / "files" / "config"
    source_root.mkdir(parents=True)
    (repo_root / "profiles").mkdir()
    (repo_root / "repo.toml").write_text('[ignore]\npatterns = ["*.tmp"]\n', encoding="utf-8")
    (repo_root / "packages" / "sample" / "package.toml").write_text(
        '\n'.join(
            [
                'id = "sample"',
                "",
                "[ignore]",
                'patterns = ["!keep.tmp"]',
                "",
                "[targets.config]",
                'source = "files/config"',
                'path = "~/.config/sample"',
                "",
                "[targets.config.ignore]",
                'patterns = ["*.tmp"]',
            ]
        ),
        encoding="utf-8",
    )
    for name in ("keep.tmp", "drop.tmp"):
        (source_root / name).write_text(name + "\n", encoding="utf-8")
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")

    engine = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )
    target = single_package_plan(engine, "fixture:sample@default", operation="push").target_plans[0]
    paths = {item.relative_path for item in target.directory_items}

    assert "keep.tmp" not in paths
    assert "drop.tmp" not in paths


def test_pull_unified_controls_preserve_excluded_repository_children(tmp_path, monkeypatch):
    from tests.engine.test_sync_directory_observation import directory_engine, put

    engine = directory_engine(
        tmp_path, monkeypatch,
        extra='[targets.tree.ignore]\npatterns = ["excluded/", "*.secret"]',
    )
    repo, live = tmp_path / "repo/packages/app/tree", tmp_path / "live/tree"
    put(repo, ".gitignore", b"*.ignored\n")
    for name in ("excluded/a", "private.secret", "a.ignored", "repo-marked/a", "live-marked/a"):
        put(repo, name, b"preserve")
    put(repo, "repo-marked/.dotman-skip")
    put(live, "live-marked/.dotman-skip")
    put(live, "a.ignored", b"must not capture")
    put(repo, "managed", b"old")
    put(live, "managed", b"new")
    with engine.open_pull_session(engine.resolve_sync_scope()) as session:
        assert [unit.identity.child_path for unit in session.view.observations] == ["managed"]
        assert session.execute().result.status == "completed"
    assert (repo / "managed").read_bytes() == b"new"
    for name in ("excluded/a", "private.secret", "a.ignored", "repo-marked/a", "live-marked/a"):
        assert (repo / name).read_bytes() == b"preserve"
    assert (repo / ".gitignore").read_bytes() == b"*.ignored\n"

@pytest.mark.parametrize("operation", ["push", "pull", "sync"])
@pytest.mark.parametrize("enabled", [True, False])
def test_repository_gitignore_chain_is_symmetric(tmp_path, monkeypatch, operation, enabled):
    home = tmp_path / "home"
    live = home / "config"
    live.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    root = tmp_path / "repo"
    source = root / "packages/sample/files/config"
    source.mkdir(parents=True)
    (root / "profiles").mkdir()
    (root / "profiles/default.toml").write_text("")
    (root / "repo.toml").write_text("[ignore]\ngitignore = true\n")
    (root / "packages/sample/package.toml").write_text(
        'id = "sample"\n[ignore]\ngitignore = ' + str(enabled).lower() +
        '\n[targets.config]\nsource = "files/config"\npath = "~/config"\n')
    (root / ".gitignore").write_text('/packages/*/files/config/root-*\n*.log\n')
    (root / "packages/.gitignore").write_text('sample/files/config/ancestor-*\n')
    (source / ".gitignore").write_text('!keep.log\n/target-*\nblocked/\n')
    for base in (source, live):
        (base / "nested").mkdir()
        (base / "blocked").mkdir()
        for name in ("visible", "root-only", "ancestor-only", "target-only", "drop.log", "keep.log", "nested/drop.tmp", "nested/keep.tmp", "blocked/keep"):
            (base / name).write_text(operation + str(base))
    (source / "nested/.gitignore").write_text('*.tmp\n!keep.tmp\n')
    (source / "blocked/.gitignore").write_text('!keep\n')
    (live / "live-only.log").write_text("live")
    (live / ".gitignore").write_text("visible\n")
    engine = DotmanEngine.from_config_path(write_single_repo_config(tmp_path, repo_name="fixture", repo_path=root))
    if operation == "push":
        plan = single_package_plan(engine, "fixture:sample@default")
        paths = {item.relative_path for item in plan.target_plans[0].directory_items}
    else:
        from tests.helpers import initialize_git_repository, write_tracked_packages_state
        initialize_git_repository(root)
        write_tracked_packages_state(tmp_path / "state", repo_name="fixture", entries=[("sample", "default")])
        opener = engine.open_pull_session if operation == "pull" else engine.open_sync_session
        with opener(engine.resolve_sync_scope(), preview=True) as session:
            paths = {row.observation.identity.child_path for row in session.view.rows}
    if enabled:
        assert paths == {"visible", "keep.log", "nested/keep.tmp"}
    else:
        assert {"root-only", "ancestor-only", "target-only", "drop.log", "live-only.log", "nested/drop.tmp", "blocked/keep"} <= paths

def test_scoped_gitignore_preserves_git_pattern_syntax_and_parent_exclusion(tmp_path):
    from dotman.ignore import IgnoreMatcher, collect_gitignore_chain

    source = tmp_path / "packages/app/config"
    source.mkdir(parents=True)
    (tmp_path / ".gitignore").write_text(
        "/packages/[ab]pp/config/root-*\n"
        "packages/**/config/double-*\n"
        "/packages/app/config/blocked/\n"
        "*.log\n")
    (source / ".gitignore").write_text(
        "!keep.log\n/local-*\n\\#literal\n\\!literal\nspace\\ \n")
    (source / "blocked").mkdir()
    (source / "blocked/.gitignore").write_text("!keep\n")
    (source / "nested").mkdir()
    (source / "nested/.gitignore").write_text("/only-here\n")
    matcher = IgnoreMatcher.from_patterns((), gitignore=collect_gitignore_chain(source, tmp_path))
    for path in ("root-file", "double-file", "drop.log", "#literal", "!literal", "space ", "blocked/keep", "nested/only-here"):
        assert matcher.matches(path), path
    for path in ("keep.log", "nested/local-file", "nested/deeper/only-here", "only-here", "space"):
        assert not matcher.matches(path), path

@pytest.mark.parametrize("operation", ["push", "pull", "sync"])
@pytest.mark.parametrize("control_scope", ["repository", "target"])
def test_gitignore_exclusion_allows_separately_owned_nested_target(tmp_path, monkeypatch, operation, control_scope):
    from tests.helpers import initialize_git_repository, write_tracked_packages_state
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    root = tmp_path / "repo"
    package = root / "packages/app"
    (package / "tree").mkdir(parents=True)
    (package / "tree/keep").write_text("keep")
    (package / "owned").write_text("owned")
    if control_scope == "repository":
        (root / ".gitignore").write_text("/packages/*/tree/owned\n")
    else:
        (package / "tree/.gitignore").write_text("/owned\n")
    (root / "profiles").mkdir()
    (root / "profiles/default.toml").write_text("")
    (root / "repo.toml").write_text("[ignore]\ngitignore = true\n")
    (package / "package.toml").write_text(
        'id = "app"\n[targets.tree]\nsource = "tree"\npath = "~/tree"\n'
        '[targets.owned]\nsource = "owned"\npath = "~/tree/owned"\n')
    initialize_git_repository(root)
    write_tracked_packages_state(tmp_path / "state", repo_name="fixture", entries=[("app", "default")])
    engine = DotmanEngine.from_config_path(write_single_repo_config(tmp_path, repo_name="fixture", repo_path=root))
    if operation == "push":
        assert len(single_package_plan(engine, "fixture:app@default").target_plans) == 2
    else:
        opener = engine.open_pull_session if operation == "pull" else engine.open_sync_session
        with opener(engine.resolve_sync_scope(), preview=True) as session:
            assert session.view.observations
