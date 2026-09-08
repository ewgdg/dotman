from __future__ import annotations

import os
import stat
import sys

import pytest

from dotman.command_runtime import ArgvCommand, CommandRequest, current_command_runtime
from dotman.operation_lock import (
    LOCK_FILE_NAME,
    OperationBusy,
    OperationLock,
    OperationLockError,
)


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
    "shape", ["symlink", "fifo", "directory", "hardlink"]
)
def test_manager_lock_rejects_unsafe_state_without_repair(tmp_path, monkeypatch, shape):
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
        lock.chmod(0o700)
        if shape == "hardlink":
            os.link(lock, tmp_path / "other")
    before = lock.lstat()

    def unexpected_chmod(descriptor, mode):
        pytest.fail("untrusted inode must not be chmodded")

    monkeypatch.setattr(os, "fchmod", unexpected_chmod)
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


@pytest.mark.parametrize("mode", [0o700, 0o644, 0o666])
def test_manager_lock_repairs_owned_file_permissions_without_replacing_it(tmp_path, mode):
    root = tmp_path / "manager"
    root.mkdir(mode=0o700)
    path = root / LOCK_FILE_NAME
    path.write_bytes(b"preserved")
    path.chmod(mode)
    inode = path.stat().st_ino

    with OperationLock.acquire(root):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert path.stat().st_ino == inode
        assert path.read_bytes() == b"preserved"
        with pytest.raises(OperationBusy):
            OperationLock.acquire(root)
    with OperationLock.acquire(root):
        pass


@pytest.mark.parametrize("failure", ["denied", "ineffective"])
def test_manager_lock_permission_repair_fails_closed(tmp_path, monkeypatch, failure):
    root = tmp_path / "manager"
    root.mkdir(mode=0o700)
    path = root / LOCK_FILE_NAME
    path.touch(mode=0o700)
    calls = []

    def fail_chmod(descriptor, mode):
        calls.append(descriptor)
        if failure == "denied":
            raise PermissionError("injected chmod denial")

    monkeypatch.setattr(os, "fchmod", fail_chmod)
    with pytest.raises(OperationLockError, match="permissions|mode"):
        OperationLock.acquire(root)
    assert len(calls) == 1
    with pytest.raises(OSError):
        os.fstat(calls[0])
    assert stat.S_IMODE(path.stat().st_mode) == 0o700


def test_manager_lock_does_not_chmod_wrong_owner(tmp_path, monkeypatch):
    root = tmp_path / "manager"
    root.mkdir(mode=0o700)
    path = root / LOCK_FILE_NAME
    path.touch(mode=0o700)
    inode = path.stat().st_ino
    original_fstat = os.fstat

    def wrong_owner(descriptor):
        status = original_fstat(descriptor)
        if status.st_ino == inode:
            values = list(status)
            values[4] = os.geteuid() + 1
            return os.stat_result(values)
        return status

    def unexpected_chmod(descriptor, mode):
        pytest.fail("untrusted inode must not be chmodded")

    monkeypatch.setattr(os, "fstat", wrong_owner)
    monkeypatch.setattr(os, "fchmod", unexpected_chmod)
    with pytest.raises(OperationLockError):
        OperationLock.acquire(root)
    assert stat.S_IMODE(path.stat().st_mode) == 0o700


def test_manager_lock_permission_repair_uses_verified_descriptor(tmp_path, monkeypatch):
    root = tmp_path / "manager"
    root.mkdir(mode=0o700)
    path = root / LOCK_FILE_NAME
    path.touch(mode=0o700)
    saved = root / "saved"
    target = tmp_path / "target"
    target.touch(mode=0o644)
    original_fchmod = os.fchmod

    def substitute_before_chmod(descriptor, mode):
        path.rename(saved)
        path.symlink_to(target)
        original_fchmod(descriptor, mode)
        raise PermissionError("injected failure after descriptor repair")

    monkeypatch.setattr(os, "fchmod", substitute_before_chmod)
    with pytest.raises(OperationLockError, match="permissions"):
        OperationLock.acquire(root)
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.stat().st_mode) == 0o644


@pytest.mark.parametrize("changed_field", ["owner", "links"])
def test_manager_lock_revalidates_file_after_permission_repair(
    tmp_path, monkeypatch, changed_field
):
    root = tmp_path / "manager"
    root.mkdir(mode=0o700)
    path = root / LOCK_FILE_NAME
    path.touch(mode=0o700)
    original_fstat = os.fstat
    original_fchmod = os.fchmod
    repaired_descriptor = None

    def chmod(descriptor, mode):
        nonlocal repaired_descriptor
        original_fchmod(descriptor, mode)
        repaired_descriptor = descriptor

    def changed_status(descriptor):
        status = original_fstat(descriptor)
        if descriptor == repaired_descriptor:
            values = list(status)
            if changed_field == "owner":
                values[4] = os.geteuid() + 1
            else:
                values[3] = 2
            return os.stat_result(values)
        return status

    monkeypatch.setattr(os, "fchmod", chmod)
    monkeypatch.setattr(os, "fstat", changed_status)
    with pytest.raises(OperationLockError):
        OperationLock.acquire(root)
    assert repaired_descriptor is not None
    with pytest.raises(OSError):
        original_fstat(repaired_descriptor)
