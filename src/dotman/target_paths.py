from __future__ import annotations

from pathlib import Path


def ensure_declared_live_path_is_not_symlink(
    *,
    live_path: Path,
    target_label: str,
    allow_symlink_replacement: bool = False,
) -> None:
    if allow_symlink_replacement or not live_path.is_symlink():
        return
    raise ValueError(
        f"live target path is a symlink for target '{target_label}': "
        f"{live_path} -> {live_path.resolve(strict=False)}"
    )
