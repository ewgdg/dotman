from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from uuid import uuid4

_TEMP_FILE_PREFIX = ".dotman-"
_TEMP_FILE_SUFFIX = ".tmp"
_TEMP_FILE_TOKEN_HEX_LENGTH = 32
_TEMP_FILE_NAME_PATTERN = re.compile(
    rf"{re.escape(_TEMP_FILE_PREFIX)}"
    rf"(?P<pid>[1-9][0-9]*)-"
    rf"(?P<token>[0-9a-f]{{{_TEMP_FILE_TOKEN_HEX_LENGTH}}})"
    rf"{re.escape(_TEMP_FILE_SUFFIX)}"
)
_DEFAULT_FILE_CREATION_MODE = 0o666


def _atomic_temp_file_prefix() -> str:
    return f"{_TEMP_FILE_PREFIX}{os.getpid()}-"


def _new_atomic_temp_path(directory: Path) -> Path:
    return directory / f"{_atomic_temp_file_prefix()}{uuid4().hex}{_TEMP_FILE_SUFFIX}"


def write_bytes_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cleanup_stale_atomic_temp_files(path.parent)
    replacement_mode = target_replacement_mode(path)
    candidate_temp_path = _new_atomic_temp_path(path.parent)
    temp_path: Path | None = None
    try:
        with candidate_temp_path.open("xb") as temp_file:
            temp_path = candidate_temp_path
            temp_file.write(content)
        if replacement_mode is not None:
            os.chmod(temp_path, replacement_mode)
        temp_path.replace(path)
    finally:
        cleanup_atomic_temp_file(temp_path)



def write_text_atomic(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    write_bytes_atomic(path, content.encode(encoding))



def write_symlink_atomic(path: Path, target: str | Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cleanup_stale_atomic_temp_files(path.parent)
    candidate_temp_path = _new_atomic_temp_path(path.parent)
    temp_path: Path | None = None
    try:
        candidate_temp_path.symlink_to(target)
        temp_path = candidate_temp_path
        temp_path.replace(path)
    finally:
        cleanup_atomic_temp_file(temp_path)



def target_replacement_mode(path: Path) -> int | None:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        # Exclusive creation already applies the process umask. Leaving that
        # mode intact avoids mutating the process-global umask during writes.
        return None



def default_created_file_mode() -> int:
    current_umask = os.umask(0)
    os.umask(current_umask)
    return _DEFAULT_FILE_CREATION_MODE & ~current_umask



def cleanup_atomic_temp_file(temp_path: Path | None) -> None:
    if temp_path is None:
        return
    try:
        temp_path.unlink()
    except FileNotFoundError:
        pass



def cleanup_stale_atomic_temp_files(directory: Path) -> None:
    for temp_path in directory.glob(f"{_TEMP_FILE_PREFIX}*{_TEMP_FILE_SUFFIX}"):
        if atomic_temp_file_owner_is_definitely_absent(temp_path):
            cleanup_atomic_temp_file(temp_path)



def atomic_temp_file_owner_is_definitely_absent(temp_path: Path) -> bool:
    name_match = _TEMP_FILE_NAME_PATTERN.fullmatch(temp_path.name)
    if name_match is None:
        # Without a verified creator PID, the sweeper cannot prove that removing
        # this path is safe. Only the writer that created it may clean it up.
        return False
    pid = int(name_match.group("pid"))
    if pid == os.getpid():
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except (OSError, OverflowError):
        return False
    return False


__all__ = [
    "atomic_temp_file_owner_is_definitely_absent",
    "cleanup_atomic_temp_file",
    "cleanup_stale_atomic_temp_files",
    "default_created_file_mode",
    "target_replacement_mode",
    "write_bytes_atomic",
    "write_symlink_atomic",
    "write_text_atomic",
]
