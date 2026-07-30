from __future__ import annotations

from pathlib import Path

import pytest

from dotman.engine import DotmanEngine
from dotman.models import HookCommandSpec
from tests.helpers import write_single_repo_config


def write_manifest_repo(
    tmp_path: Path,
    *,
    repo_manifest: list[str] | None = None,
    target_manifest: list[str] | None = None,
) -> Path:
    repo_root = tmp_path / "repo"
    (repo_root / "packages" / "app" / "files").mkdir(parents=True)
    (repo_root / "profiles").mkdir()
    (repo_root / "repo.toml").write_text(
        "\n".join([*(repo_manifest or []), ""]),
        encoding="utf-8",
    )
    (repo_root / "packages" / "app" / "files" / "config").write_text(
        "config\n",
        encoding="utf-8",
    )
    (repo_root / "packages" / "app" / "package.toml").write_text(
        "\n".join(
            [
                'id = "app"',
                "",
                "[targets.config]",
                'source = "files/config"',
                'path = "~/.config/app/config"',
                *(target_manifest or []),
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    return repo_root


def load_manifest_repo(tmp_path: Path, repo_root: Path) -> DotmanEngine:
    config_path = write_single_repo_config(
        tmp_path,
        repo_name="fixture",
        repo_path=repo_root,
    )
    return DotmanEngine.from_config_path(config_path)


def test_target_rejects_unsupported_schema_fields(tmp_path: Path) -> None:
    repo_root = write_manifest_repo(
        tmp_path,
        target_manifest=['unexpected = "value"'],
    )

    with pytest.raises(
        ValueError,
        match=r"target 'config' has unsupported keys: unexpected",
    ):
        load_manifest_repo(tmp_path, repo_root)


def test_target_path_rule_rejects_unsupported_schema_fields(tmp_path: Path) -> None:
    repo_root = write_manifest_repo(
        tmp_path,
        target_manifest=[
            "",
            "[[targets.config.path_rules]]",
            'pattern = "*.conf"',
            'unexpected = "value"',
        ],
    )

    with pytest.raises(
        ValueError,
        match=r"target 'config' path_rules\[1\] has unsupported keys: unexpected",
    ):
        load_manifest_repo(tmp_path, repo_root)


def test_target_ignore_table_rejects_unsupported_schema_fields(tmp_path: Path) -> None:
    repo_root = write_manifest_repo(
        tmp_path,
        target_manifest=[
            "",
            "[targets.config.ignore]",
            'unexpected = ["*.tmp"]',
        ],
    )

    with pytest.raises(
        ValueError,
        match=r"target 'config' ignore has unsupported keys: unexpected",
    ):
        load_manifest_repo(tmp_path, repo_root)


def test_repo_ignore_table_rejects_unsupported_schema_fields(tmp_path: Path) -> None:
    repo_root = write_manifest_repo(
        tmp_path,
        repo_manifest=[
            "[ignore]",
            'unexpected = ["*.tmp"]',
        ],
    )

    with pytest.raises(
        ValueError,
        match=r"repo config .+ \[ignore\] has unsupported keys: unexpected",
    ):
        load_manifest_repo(tmp_path, repo_root)


def test_repo_config_rejects_unsupported_schema_fields(tmp_path: Path) -> None:
    repo_root = write_manifest_repo(
        tmp_path,
        repo_manifest=['unexpected = "value"'],
    )

    with pytest.raises(
        ValueError,
        match=r"repo config .+ has unsupported keys: unexpected",
    ):
        load_manifest_repo(tmp_path, repo_root)


def test_canonical_manifest_vocabulary_loads_unchanged(tmp_path: Path) -> None:
    repo_root = write_manifest_repo(
        tmp_path,
        repo_manifest=[
            '[hooks.pre_push]',
            'commands = [{ run = "echo ready", elevation = "root" }]',
            "",
            "[ignore]",
            'push = ["repo.push"]',
            'pull = ["repo.pull"]',
            'shared = ["repo.shared"]',
            'gitignore = ["push"]',
        ],
        target_manifest=[
            'pull_view_repo = "render"',
            'pull_view_live = "raw"',
            "",
            "[[targets.config.path_rules]]",
            'pattern = "*.conf"',
            'pull_view_repo = "render"',
            'pull_view_live = "raw"',
            "",
            "[targets.config.ignore]",
            'push = ["target.push"]',
            'pull = ["target.pull"]',
            'shared = ["target.shared"]',
            'gitignore = ["pull"]',
        ],
    )

    engine = load_manifest_repo(tmp_path, repo_root)
    repo = engine.get_repo("fixture")
    package = repo.resolve_package("app")
    assert package.targets is not None
    assert repo.hooks is not None
    target = package.targets["config"]

    assert repo.hooks["pre_push"].commands == (
        HookCommandSpec(run="echo ready", elevation="root"),
    )
    assert repo.ignore_defaults.push == ("repo.push", "repo.shared")
    assert repo.ignore_defaults.pull == ("repo.pull", "repo.shared")
    assert repo.ignore_defaults.gitignore == ("push",)
    assert target.pull_view_repo == "render"
    assert target.pull_view_live == "raw"
    assert target.push_ignore == ("target.push", "target.shared")
    assert target.pull_ignore == ("target.pull", "target.shared")
    assert target.gitignore == ("pull",)
    assert target.path_rules[0].pull_view_repo == "render"
    assert target.path_rules[0].pull_view_live == "raw"
