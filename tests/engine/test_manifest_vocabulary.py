from __future__ import annotations

from pathlib import Path

import pytest

from dotman.engine import DotmanEngine
from tests.helpers import write_single_repo_config


def write_manifest_repo(
    tmp_path: Path,
    *,
    repo_manifest: list[str] | None = None,
    target_manifest: list[str] | None = None,
    target_is_directory: bool = False,
) -> Path:
    repo_root = tmp_path / "repo"
    (repo_root / "packages" / "app" / "files").mkdir(parents=True)
    (repo_root / "profiles").mkdir()
    (repo_root / "repo.toml").write_text(
        "\n".join([*(repo_manifest or []), ""]),
        encoding="utf-8",
    )
    target_source = repo_root / "packages" / "app" / "files" / "config"
    if target_is_directory:
        target_source.mkdir()
        (target_source / "example.conf").write_text("config\n", encoding="utf-8")
    else:
        target_source.write_text("config\n", encoding="utf-8")
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
            "[targets.config.path_rules.rule]",
            'pattern = "*.conf"',
            'unexpected = "value"',
        ],
    )

    with pytest.raises(
        ValueError,
        match=r"target 'config' path_rules.rule has unsupported keys: unexpected",
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


def test_canonical_manifest_vocabulary_loads_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    live_target = home / ".config" / "app" / "config"
    live_target.mkdir(parents=True)
    (live_target / "example.conf").write_text("old config\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    repo_root = write_manifest_repo(
        tmp_path,
        target_is_directory=True,
        repo_manifest=[
            '[hooks.pre_push]',
            'commands = [{ run = "echo ready", elevation = "root" }]',
            "",
            "[ignore]",
            'patterns = ["repo.one", "repo.two"]',
            'gitignore = ["push"]',
        ],
        target_manifest=[
            'type = "directory"',
            'render = "cat \\"$DOTMAN_REPO_PATH\\""',
            'compare = { repo = "render", live = "raw" }',
            "",
            "[targets.config.path_rules.rule]",
            'pattern = "*.conf"',
            'compare = { repo = "render", live = "raw" }',
            "",
            "[targets.config.ignore]",
            'patterns = ["target.one", "target.two"]',
        ],
    )

    engine = load_manifest_repo(tmp_path, repo_root)
    push_plan = engine.plan_push_query("fixture:app@default")
    pull_plan = engine.plan_pull_query("fixture:app@default")
    push_target = push_plan.package_plans[0].target_plans[0]
    pull_target = pull_plan.package_plans[0].target_plans[0]

    assert push_plan.repo_hooks["fixture"]["pre_push"][0].elevation == "root"
    assert "push_ignore" not in push_target.to_dict()
    assert "pull_ignore" not in pull_target.to_dict()
    assert pull_target.compare_repo == "render"
    assert pull_target.compare_live == "raw"
    assert pull_target.path_rules[0].compare_repo == "render"
    assert pull_target.path_rules[0].compare_live == "raw"
