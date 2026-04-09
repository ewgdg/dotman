from __future__ import annotations

import json
from pathlib import Path

import pytest

from dotman.engine import DotmanEngine
from dotman.snapshot import default_snapshot_root
from test_support import (
    EXAMPLE_REPO,
    REFERENCE_REPO,
    write_manager_config,
    write_multi_instance_repo,
    write_package_override_preview_repo,
    write_single_repo_config,
    write_untrack_conflict_repo,
)


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


def test_config_defaults_snapshot_settings(tmp_path: Path) -> None:
    config_path = write_single_repo_config(tmp_path, repo_name="example", repo_path=EXAMPLE_REPO)

    engine = DotmanEngine.from_config_path(config_path)

    assert engine.config.snapshots.enabled is True
    assert engine.config.snapshots.path == default_snapshot_root()
    assert engine.config.snapshots.max_generations == 10


def test_config_reads_snapshot_overrides(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    snapshot_path = tmp_path / "custom-snapshots"
    config_path.write_text(
        "\n".join(
            [
                "[repos.example]",
                f'path = "{EXAMPLE_REPO}"',
                "order = 10",
                "",
                "[snapshots]",
                "enabled = false",
                f'path = "{snapshot_path}"',
                "max_generations = 2",
                "",
            ]
        ),
        encoding="utf-8",
    )

    engine = DotmanEngine.from_config_path(config_path)

    assert engine.config.snapshots.enabled is False
    assert engine.config.snapshots.path == snapshot_path.resolve()
    assert engine.config.snapshots.max_generations == 2


def test_config_rejects_non_positive_snapshot_retention(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[repos.example]",
                f'path = "{EXAMPLE_REPO}"',
                "order = 10",
                "",
                "[snapshots]",
                "max_generations = 0",
                "",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="max_generations"):
        DotmanEngine.from_config_path(config_path)
