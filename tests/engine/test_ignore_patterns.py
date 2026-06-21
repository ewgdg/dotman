from __future__ import annotations

from pathlib import Path

import pytest

from dotman.engine import DotmanEngine, list_directory_files, matches_ignore_pattern
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

    plan = single_package_plan(engine, "fixture:sample@default", operation="push")

    target = plan.target_plans[0]
    assert target.action == "create"
    assert [item.relative_path for item in target.directory_items] == ["visible.conf"]


def test_directory_target_push_ignore_preserves_gitignore_style_nested_pycache_files_during_push_cleanup(
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
                'push_ignore = ["linked/"]',
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



def test_directory_target_pull_ignore_hides_both_repo_and_live_children_during_pull(
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
                'pull_ignore = ["*.local"]',
                '',
            ]
        ),
        encoding="utf-8",
    )
    (source_root / "visible.conf").write_text("visible = true\n", encoding="utf-8")
    (source_root / "repo-only.local").write_text("repo\n", encoding="utf-8")
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")

    live_root = home / ".config" / "sample"
    live_root.mkdir(parents=True)
    (live_root / "visible.conf").write_text("visible = true\n", encoding="utf-8")
    (live_root / "live-only.local").write_text("live\n", encoding="utf-8")

    engine = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )

    plan = single_package_plan(engine, "fixture:sample@default", operation="pull")

    target = plan.target_plans[0]
    assert target.action == "noop"
    assert [item.relative_path for item in target.directory_items] == []


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


def test_directory_target_pull_skip_marker_preserves_repo_subtree_during_cleanup(
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
    (source_root / "cache").mkdir()
    (source_root / "cache" / ".dotman-skip").write_text("", encoding="utf-8")
    (source_root / "cache" / "local-state").write_text("keep\n", encoding="utf-8")
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")

    live_root = home / ".config" / "sample"
    live_root.mkdir(parents=True)
    (live_root / "tool.conf").write_text("value = 1\n", encoding="utf-8")

    engine = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )

    plan = single_package_plan(engine, "fixture:sample@default", operation="pull")

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


def test_collect_gitignore_patterns_from_root(tmp_path: Path) -> None:
    from dotman.ignore import collect_gitignore_patterns

    root = tmp_path / "src"
    root.mkdir()
    (root / "visible.conf").write_text("keep\n", encoding="utf-8")
    (root / ".gitignore").write_text("*.log\nsecret/\n", encoding="utf-8")
    (root / "app.log").write_text("log\n", encoding="utf-8")
    (root / "secret").mkdir()
    (root / "secret" / "key.txt").write_text("key\n", encoding="utf-8")

    patterns = collect_gitignore_patterns(root)

    assert "*.log" in patterns
    assert "secret/" in patterns
    assert len(patterns) == 2


def test_collect_gitignore_patterns_includes_nested_files(tmp_path: Path) -> None:
    from dotman.ignore import collect_gitignore_patterns

    root = tmp_path / "src"
    root.mkdir()
    (root / ".gitignore").write_text("*.log\n", encoding="utf-8")
    sub = root / "sub"
    sub.mkdir()
    (sub / ".gitignore").write_text("*.tmp\n!important.tmp\n/deep\n", encoding="utf-8")
    (sub / "data.tmp").write_text("tmp\n", encoding="utf-8")
    (sub / "important.tmp").write_text("keep\n", encoding="utf-8")
    (sub / "nested").mkdir()
    (sub / "nested" / "data.tmp").write_text("nested tmp\n", encoding="utf-8")
    deep = sub / "deep"
    deep.mkdir()
    (deep / ".gitignore").write_text("*.cache\n", encoding="utf-8")
    (deep / "output.cache").write_text("cache\n", encoding="utf-8")

    patterns = collect_gitignore_patterns(root)

    assert "*.log" in patterns
    assert "sub/**/*.tmp" in patterns
    assert "!sub/**/important.tmp" in patterns
    assert "/sub/deep" in patterns
    assert "sub/deep/**/*.cache" in patterns

    files = list_directory_files(root, patterns)
    assert "sub/important.tmp" in files
    assert "sub/nested/data.tmp" not in files
    assert "sub/deep/output.cache" not in files


def test_collect_gitignore_patterns_non_directory_returns_empty(tmp_path: Path) -> None:
    from dotman.ignore import collect_gitignore_patterns

    f = tmp_path / "file.txt"
    f.write_text("data\n", encoding="utf-8")

    assert collect_gitignore_patterns(f) == ()


def test_repo_toml_loads_gitignore_from_ignore_table(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "packages").mkdir()
    (repo_root / "repo.toml").write_text(
        "\n".join(["[ignore]", 'gitignore = ["push", "pull"]', ""]),
        encoding="utf-8",
    )

    engine = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )

    assert engine.get_repo("fixture").ignore_defaults.gitignore == ("push", "pull")


def test_repo_toml_gitignore_defaults_to_empty(tmp_path: Path) -> None:
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

    assert engine.get_repo("fixture").ignore_defaults.gitignore == ()


def test_repo_toml_rejects_invalid_gitignore_values(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "repo.toml").write_text(
        "\n".join(["[ignore]", 'gitignore = ["push", "sync"]', ""]),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="gitignore only supports 'push' and 'pull'"):
        DotmanEngine.from_config_path(
            write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
        )


def test_target_gitignore_overrides_repo_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "packages" / "sample" / "files" / "config").mkdir(parents=True)
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    (repo_root / "repo.toml").write_text(
        "\n".join(["[ignore]", 'gitignore = ["push"]', ""]),
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
                "[targets.config.ignore]",
                'gitignore = ["pull"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "sample" / "files" / "config" / "visible.conf").write_text(
        "visible = true\n", encoding="utf-8"
    )

    engine = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )

    package = engine.get_repo("fixture").resolve_package("sample")
    target_spec = package.targets["config"]
    assert target_spec.gitignore == ("pull",)


def test_target_gitignore_empty_explicitly_disables_ignore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "packages" / "sample" / "files" / "config").mkdir(parents=True)
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    (repo_root / "repo.toml").write_text(
        "\n".join(["[ignore]", 'gitignore = ["push"]', ""]),
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
                "[targets.config.ignore]",
                'gitignore = []',
                "",
            ]
        ),
        encoding="utf-8",
    )
    target_root = repo_root / "packages" / "sample" / "files" / "config"
    (target_root / "visible.conf").write_text("visible = true\n", encoding="utf-8")
    (target_root / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (target_root / "ignored.log").write_text("still managed\n", encoding="utf-8")

    engine = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )

    package = engine.get_repo("fixture").resolve_package("sample")
    target_spec = package.targets["config"]
    assert target_spec.gitignore == ()

    plan = single_package_plan(engine, "fixture:sample@default", operation="push")
    assert "ignored.log" in {item.relative_path for item in plan.target_plans[0].directory_items}


def test_target_gitignore_absent_inherits_repo_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "packages" / "sample" / "files" / "config").mkdir(parents=True)
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    (repo_root / "repo.toml").write_text(
        "\n".join(["[ignore]", 'gitignore = ["push"]', ""]),
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
    (repo_root / "packages" / "sample" / "files" / "config" / "visible.conf").write_text(
        "visible = true\n", encoding="utf-8"
    )

    engine = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )

    package = engine.get_repo("fixture").resolve_package("sample")
    target_spec = package.targets["config"]
    assert target_spec.gitignore is None


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
                'gitignore = ["push"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (source_root / "visible.conf").write_text("visible = true\n", encoding="utf-8")
    (source_root / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (source_root / "app.log").write_text("log\n", encoding="utf-8")
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")

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
                'gitignore = ["push"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (source_root / "visible.conf").write_text("visible = true\n", encoding="utf-8")
    (source_root / ".gitignore").write_text("*.local\n", encoding="utf-8")
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")

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


def test_directory_target_gitignore_not_applied_for_non_selected_operation(
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
                'gitignore = ["push"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (source_root / "visible.conf").write_text("visible = true\n", encoding="utf-8")
    (source_root / ".gitignore").write_text("*.conf\n", encoding="utf-8")
    (source_root / "local.ini").write_text("local\n", encoding="utf-8")
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")

    live_root = home / ".config" / "sample"
    live_root.mkdir(parents=True)
    (live_root / "local.ini").write_text("local\n", encoding="utf-8")

    engine = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )

    plan = single_package_plan(engine, "fixture:sample@default", operation="pull")

    target = plan.target_plans[0]
    assert target.action == "update"
    assert sorted((item.relative_path, item.action) for item in target.directory_items) == [
        (".gitignore", "delete"),
        ("visible.conf", "delete"),
    ]


def test_directory_target_gitignore_preserves_ignored_pull_children(
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
                'gitignore = ["pull"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (source_root / "visible.conf").write_text("visible = true\n", encoding="utf-8")
    (source_root / ".gitignore").write_text("*.local\n", encoding="utf-8")
    (source_root / "repo-only.local").write_text("repo local\n", encoding="utf-8")
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")

    live_root = home / ".config" / "sample"
    live_root.mkdir(parents=True)
    (live_root / "visible.conf").write_text("visible = true\n", encoding="utf-8")
    (live_root / "machine.local").write_text("live local\n", encoding="utf-8")

    engine = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root)
    )

    plan = single_package_plan(engine, "fixture:sample@default", operation="pull")

    target = plan.target_plans[0]
    assert target.action == "noop"
    assert target.directory_items == ()


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
                'gitignore = ["push"]',
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
                'push_ignore = ["!important.log"]',
                "",
                "[targets.config.ignore]",
                'gitignore = ["push"]',
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
