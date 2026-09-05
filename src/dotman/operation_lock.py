"""Manager-wide non-blocking ownership of real operation planning through execution."""

from __future__ import annotations

import errno
import fcntl
import os
import stat
from pathlib import Path


LOCK_FILE_NAME = "operation.lock"


class OperationLockError(ValueError):
    """The manager operation lock cannot be safely acquired."""


class OperationBusy(OperationLockError):
    """A real operation already owns this manager."""


class OperationLock:
    def __init__(self, descriptor: int):
        self._descriptor: int | None = descriptor

    @classmethod
    def acquire(cls, state_root: Path) -> OperationLock:
        descriptor = None
        directory = None
        try:
            state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            directory = os.open(
                state_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            )
            parent = os.fstat(directory)
            if parent.st_uid != os.geteuid() or parent.st_mode & 0o022:
                raise OperationLockError(
                    "manager state directory must be owned by the current user and not writable by others"
                )
            descriptor = os.open(
                LOCK_FILE_NAME,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                0o600,
                dir_fd=directory,
            )
            status = os.fstat(descriptor)
            if (
                not stat.S_ISREG(status.st_mode)
                or status.st_uid != os.geteuid()
                or stat.S_IMODE(status.st_mode) != 0o600
                or status.st_nlink != 1
            ):
                raise OperationLockError(
                    "manager operation lock must be an owner-only regular file"
                )
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EACCES):
                    raise OperationBusy(
                        "another real operation owns the manager operation lock"
                    ) from exc
                raise
            lock = cls(descriptor)
            descriptor = None
            return lock
        except OSError as exc:
            raise OperationLockError(
                f"cannot acquire manager operation lock: {exc}"
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if directory is not None:
                os.close(directory)

    def close(self) -> None:
        if self._descriptor is not None:
            os.close(self._descriptor)
            self._descriptor = None

    def __enter__(self) -> OperationLock:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
