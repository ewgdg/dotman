from __future__ import annotations

import os
import stat
from pathlib import Path


def restore_repo_path_access_for_invoking_user(path: Path, *, repo_root: Path | None) -> None:
    invoking_user = invoking_user_ids()
    if invoking_user is None or not path.exists():
        return

    target_uid, target_gid = invoking_user
    for repo_path in repo_access_paths(path, repo_root=repo_root):
        os.chown(repo_path, target_uid, target_gid)
        ensure_owner_write_access(repo_path)



def invoking_user_ids() -> tuple[int, int] | None:
    geteuid = getattr(os, "geteuid", None)
    if geteuid is None or geteuid() != 0:
        return None
    sudo_uid = os.environ.get("SUDO_UID")
    sudo_gid = os.environ.get("SUDO_GID")
    if sudo_uid is None or sudo_gid is None:
        return None
    try:
        return int(sudo_uid), int(sudo_gid)
    except ValueError:
        return None



def repo_access_paths(path: Path, *, repo_root: Path | None) -> tuple[Path, ...]:
    if repo_root is None or (path != repo_root and repo_root not in path.parents):
        return (path,)

    access_paths: list[Path] = []
    current = path
    while True:
        access_paths.append(current)
        if current == repo_root:
            return tuple(access_paths)
        current = current.parent



def ensure_owner_write_access(path: Path) -> None:
    mode = path.stat().st_mode
    required_bits = stat.S_IWUSR
    if path.is_dir():
        # Pull may run under sudo to read protected live paths. When that happens,
        # repo-side writes must still leave repo tree editable by invoking user
        # instead of stranding files or newly created directories as root-only.
        required_bits |= stat.S_IRUSR | stat.S_IXUSR
    if mode & required_bits == required_bits:
        return
    os.chmod(path, mode | required_bits)


__all__ = [
    "ensure_owner_write_access",
    "invoking_user_ids",
    "repo_access_paths",
    "restore_repo_path_access_for_invoking_user",
]
