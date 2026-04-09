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
