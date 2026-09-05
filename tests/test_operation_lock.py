from __future__ import annotations

import os
import sys

import pytest

from dotman.command_runtime import ArgvCommand, CommandRequest, current_command_runtime
from dotman.operation_lock import LOCK_FILE_NAME, OperationLock, OperationLockError


def test_manager_lock_conflicts_across_processes_and_is_owner_only(tmp_path):
    root = tmp_path / "manager"
    with OperationLock.acquire(root):
        assert (root / LOCK_FILE_NAME).stat().st_mode & 0o777 == 0o600
        result = current_command_runtime().run(
            CommandRequest(
                ArgvCommand(
                    (
                        sys.executable,
                        "-c",
                        """
from pathlib import Path
from dotman.operation_lock import OperationLock, OperationBusy
import sys
try:
    with OperationLock.acquire(Path(sys.argv[1])):
        sys.exit(9)
except OperationBusy:
    sys.exit(0)
""",
                        str(root),
                    )
                ),
            )
        )
        assert result.exit_code == 0, result.stderr
    with OperationLock.acquire(root):
        pass


@pytest.mark.parametrize(
    "shape", ["symlink", "fifo", "directory", "public", "hardlink"]
)
def test_manager_lock_rejects_unsafe_state_without_repair(tmp_path, shape):
    root = tmp_path / "manager"
    root.mkdir(mode=0o700)
    lock = root / LOCK_FILE_NAME
    if shape == "symlink":
        other = tmp_path / "other"
        other.write_text("untouched")
        lock.symlink_to(other)
    elif shape == "fifo":
        os.mkfifo(lock, 0o600)
    elif shape == "directory":
        lock.mkdir(mode=0o700)
    else:
        lock.write_text("untouched")
        lock.chmod(0o644 if shape == "public" else 0o600)
        if shape == "hardlink":
            os.link(lock, tmp_path / "other")
    before = lock.lstat()
    with pytest.raises(OperationLockError):
        OperationLock.acquire(root)
    after = lock.lstat()
    assert (after.st_ino, after.st_mode, after.st_size) == (
        before.st_ino,
        before.st_mode,
        before.st_size,
    )


def test_manager_lock_rejects_symlinked_or_writable_manager_root(tmp_path):
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(OperationLockError):
        OperationLock.acquire(link)
    real.chmod(0o777)
    with pytest.raises(OperationLockError):
        OperationLock.acquire(real)
    assert not (real / LOCK_FILE_NAME).exists()
