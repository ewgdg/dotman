from __future__ import annotations

from pathlib import Path

import pytest

from dotman.collisions import paths_conflict


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("/home/u/.config", "/home/u/.config", True),
        ("/home/u/.config", "/home/u/.config/app/settings.json", True),
        ("/home/u/.config/app/settings.json", "/home/u/.config", True),
        # A shared text prefix is not ancestry.
        ("/home/u/.config", "/home/u/.configs/app", False),
        ("/home/u/.config/app", "/home/u/.config/apple", False),
        ("/", "/etc/hosts", True),
    ],
)
def test_paths_conflict_matches_path_ancestry(left: str, right: str, expected: bool) -> None:
    assert paths_conflict(Path(left), Path(right)) is expected
