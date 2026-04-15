from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TEMP_FILE_PREFIX = ".dotman-"
_TEMP_FILE_SUFFIX = ".tmp"


def write_bytes_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cleanup_stale_atomic_temp_files(path.parent)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=_TEMP_FILE_PREFIX,
            suffix=_TEMP_FILE_SUFFIX,
            delete=False,
        ) as temp_file:
            temp_file.write(content)
            temp_path = Path(temp_file.name)
        temp_path.replace(path)
    finally:
        cleanup_atomic_temp_file(temp_path)



def write_symlink_atomic(path: Path, target: str | Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cleanup_stale_atomic_temp_files(path.parent)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=_TEMP_FILE_PREFIX,
            suffix=_TEMP_FILE_SUFFIX,
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
        temp_path.unlink()
        temp_path.symlink_to(target)
        temp_path.replace(path)
    except Exception:
        cleanup_atomic_temp_file(temp_path)
        raise



def cleanup_atomic_temp_file(temp_path: Path | None) -> None:
    if temp_path is None:
        return
    try:
        if temp_path.exists() or temp_path.is_symlink():
            temp_path.unlink()
    except Exception:
        pass



def cleanup_stale_atomic_temp_files(directory: Path) -> None:
    for temp_path in directory.glob(f"{_TEMP_FILE_PREFIX}*{_TEMP_FILE_SUFFIX}"):
        if is_live_atomic_temp_file(temp_path):
            continue
        cleanup_atomic_temp_file(temp_path)



def is_live_atomic_temp_file(temp_path: Path) -> bool:
    temp_name = temp_path.name
    if not temp_name.startswith(_TEMP_FILE_PREFIX) or not temp_name.endswith(_TEMP_FILE_SUFFIX):
        return False
    pid_text = temp_name[len(_TEMP_FILE_PREFIX) : -len(_TEMP_FILE_SUFFIX)].split("-", 1)[0]
    if not pid_text.isdigit():
        return False
    pid = int(pid_text)
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


__all__ = [
    "cleanup_atomic_temp_file",
    "cleanup_stale_atomic_temp_files",
    "is_live_atomic_temp_file",
    "write_bytes_atomic",
    "write_symlink_atomic",
]
