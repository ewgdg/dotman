from __future__ import annotations

import atexit
import os
import shlex
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from dotman.atomic_files import write_bytes_atomic as atomic_write_bytes_atomic
from dotman.atomic_files import write_symlink_atomic as atomic_write_symlink_atomic

_SUDO_KEEPALIVE_INTERVAL_SECONDS = 30
_PRIVILEGED_HELPER_MODULE = "dotman.privileged_ops"


class _SudoLease:
    def __init__(self) -> None:
        self._stop_event = threading.Event()
        self._keepalive_thread: threading.Thread | None = None
        self._acquired = False

    def request(self, reason: str | None = None) -> None:
        if os.geteuid() == 0:
            return
        if self._acquired:
            completed = subprocess.run(
                ["sudo", "-n", "true"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if completed.returncode == 0:
                if self._keepalive_thread is None or not self._keepalive_thread.is_alive():
                    self._keepalive_thread = threading.Thread(target=self._keepalive_loop, daemon=True)
                    self._keepalive_thread.start()
                return
            self.close()
        _emit_sudo_notice(reason)
        try:
            subprocess.run(["sudo", "-v"], check=True)
        except subprocess.CalledProcessError as exc:
            raise PermissionError("sudo authentication failed") from exc
        self._acquired = True
        self._keepalive_thread = threading.Thread(target=self._keepalive_loop, daemon=True)
        self._keepalive_thread.start()

    def _keepalive_loop(self) -> None:
        while not self._stop_event.wait(_SUDO_KEEPALIVE_INTERVAL_SECONDS):
            subprocess.run(["sudo", "-n", "true"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

    def close(self) -> None:
        if not self._acquired:
            return
        self._stop_event.set()
        if self._keepalive_thread is not None:
            self._keepalive_thread.join(timeout=1)
        self._acquired = False
        self._keepalive_thread = None
        self._stop_event = threading.Event()


_active_sudo_lease: _SudoLease | None = None
_sudo_lease_depth = 0
_sudo_atexit_registered = False



def _current_sudo_lease() -> _SudoLease:
    global _active_sudo_lease, _sudo_atexit_registered
    if _active_sudo_lease is None:
        _active_sudo_lease = _SudoLease()
        if not _sudo_atexit_registered:
            atexit.register(_cleanup_active_sudo_lease)
            _sudo_atexit_registered = True
    return _active_sudo_lease



def _cleanup_active_sudo_lease() -> None:
    global _active_sudo_lease
    if _active_sudo_lease is None:
        return
    _active_sudo_lease.close()
    _active_sudo_lease = None


@contextmanager
def sudo_session() -> Iterator[None]:
    global _sudo_lease_depth
    _sudo_lease_depth += 1
    try:
        yield
    finally:
        _sudo_lease_depth -= 1
        if _sudo_lease_depth == 0:
            _cleanup_active_sudo_lease()



def _emit_sudo_notice(reason: str | None) -> None:
    from dotman import cli_style

    detail = reason or "perform privileged operation"
    use_color = sys.stderr.isatty() and os.environ.get("NO_COLOR") is None
    badge = cli_style.render_sudo_badge(use_color=use_color)
    print(f"{badge} password required to {detail}", file=sys.stderr)



def request_sudo(reason: str | None = None) -> None:
    _current_sudo_lease().request(reason)



def needs_sudo_for_read(path: Path) -> bool:
    try:
        path.stat()
    except FileNotFoundError:
        return False
    except PermissionError:
        return True
    return not os.access(path, os.R_OK)



def needs_sudo_for_write(path: Path) -> bool:
    parent = path.parent
    while True:
        try:
            parent.stat()
            break
        except FileNotFoundError:
            if parent == parent.parent:
                return False
            parent = parent.parent
        except PermissionError:
            return True
    return not os.access(parent, os.W_OK | os.X_OK)



def needs_sudo_for_chmod(path: Path) -> bool:
    try:
        owner = path.stat().st_uid
    except FileNotFoundError:
        return False
    except PermissionError:
        return True
    return os.geteuid() != 0 and owner != os.geteuid()



def sudo_prefix_command(command: str) -> str:
    return f"sudo -n -E /bin/sh -c {shlex.quote(command)}"



def _run_privileged_operation(*args: str, input: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["sudo", "-n", sys.executable, "-m", _PRIVILEGED_HELPER_MODULE, *args],
        input=input,
        capture_output=True,
        check=False,
    )



def read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except PermissionError:
        request_sudo(f"read protected path: {path}")
        completed = subprocess.run(["sudo", "-n", "/bin/cat", str(path)], capture_output=True, check=False)
        if completed.returncode == 0:
            return completed.stdout
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise PermissionError(stderr or f"permission denied for {path}")



def write_bytes_atomic(path: Path, content: bytes, *, restore_root: Path | None = None) -> None:
    if not needs_sudo_for_write(path):
        try:
            atomic_write_bytes_atomic(path, content)
            return
        except PermissionError:
            # Fall back to sudo if direct path check was too optimistic.
            pass

    request_sudo(f"write protected path: {path}")
    completed = _run_privileged_operation(
        "write-bytes-atomic",
        str(path),
        *( [str(restore_root)] if restore_root is not None else [] ),
        input=content,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise PermissionError(stderr or f"permission denied for {path}")



def write_symlink_atomic(path: Path, target: str | Path) -> None:
    if not needs_sudo_for_write(path):
        try:
            atomic_write_symlink_atomic(path, target)
            return
        except PermissionError:
            # Fall back to sudo if direct path check was too optimistic.
            pass

    request_sudo(f"write protected path: {path}")
    completed = _run_privileged_operation("write-symlink-atomic", str(path), str(target))
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise PermissionError(stderr or f"permission denied for {path}")



def delete_path_and_prune_empty_parents(path: Path, *, root: Path) -> None:
    try:
        if path.exists() or path.is_symlink():
            path.unlink()
    except PermissionError:
        request_sudo(f"delete protected path: {path}")
        completed = _run_privileged_operation("delete-path-and-prune-empty-parents", str(path), str(root))
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            raise PermissionError(stderr or f"permission denied for {path}")
        return

    prune_root = root if root.is_dir() else root.parent
    current = path.parent
    while current.exists() and current != prune_root and current != current.parent:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent



def chmod(path: Path, mode: int) -> None:
    if not path.exists():
        return
    try:
        os.chmod(path, mode)
    except PermissionError:
        request_sudo(f"change mode on protected path: {path}")
        completed = _run_privileged_operation("chmod", str(path), str(mode))
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            raise PermissionError(stderr or f"permission denied for {path}")


__all__ = [
    "chmod",
    "delete_path_and_prune_empty_parents",
    "needs_sudo_for_chmod",
    "needs_sudo_for_read",
    "needs_sudo_for_write",
    "read_bytes",
    "request_sudo",
    "sudo_prefix_command",
    "sudo_session",
    "write_bytes_atomic",
    "write_symlink_atomic",
]
