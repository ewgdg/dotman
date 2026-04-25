from __future__ import annotations

import atexit
import json
import os
import shutil
import socket
import stat
import struct
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from dotman.file_access import request_sudo


BROKER_ENV = "DOTMAN_ELEVATION_BROKER"
REASON_ENV = "DOTMAN_ELEVATION_REASON"
REAL_SUDO_ENV = "DOTMAN_REAL_SUDO"


class ElevationBroker:
    def __init__(self) -> None:
        self._root = Path(tempfile.mkdtemp(prefix="dotman-elevation-"))
        self.socket_path = self._root / "broker.sock"
        self._shim_dir = self._root / "bin"
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._request_lock = threading.Lock()
        self._expected_uid = os.getuid()
        self._real_sudo_path: str | None = None

    def start(self) -> None:
        if self._server is not None:
            return
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(str(self.socket_path))
            server.listen(8)
            server.settimeout(0.1)
        except Exception:
            server.close()
            raise
        self._server = server
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def close(self) -> None:
        if not self._root.exists():
            return
        self._stop_event.set()
        if self._server is not None:
            self._server.close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=1)
            self._thread = None
        self.socket_path.unlink(missing_ok=True)
        for path in sorted(self._root.rglob("*"), reverse=True):
            if path.is_dir():
                path.rmdir()
            else:
                path.unlink(missing_ok=True)
        self._root.rmdir()

    def env(self, *, reason: str | None = None, intercept: bool = False) -> dict[str, str]:
        self.start()
        env = {
            BROKER_ENV: str(self.socket_path),
            REASON_ENV: reason or "perform privileged operation",
        }
        if intercept:
            env.update(self._intercept_env())
        return env

    def _intercept_env(self) -> dict[str, str]:
        real_sudo = self._real_sudo_path or shutil.which("sudo")
        if real_sudo is None:
            raise ValueError("sudo is required for elevation intercept mode but was not found in PATH")
        self._real_sudo_path = real_sudo
        self._shim_dir.mkdir(exist_ok=True)
        shim_path = self._shim_dir / "sudo"
        if not shim_path.exists():
            shim_path.write_text(_SUDO_SHIM_SOURCE, encoding="utf-8")
            shim_path.chmod(shim_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        current_path = os.environ.get("PATH", "")
        return {
            REAL_SUDO_ENV: real_sudo,
            "PATH": f"{self._shim_dir}{os.pathsep}{current_path}",
        }

    def _serve(self) -> None:
        assert self._server is not None
        while not self._stop_event.is_set():
            try:
                connection, _ = self._server.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle_connection, args=(connection,), daemon=True).start()

    def _handle_connection(self, connection: socket.socket) -> None:
        with connection:
            try:
                self._validate_peer(connection)
                payload = _read_json_payload(connection)
                reason = payload.get("reason") if isinstance(payload, dict) else None
                with self._request_lock:
                    request_sudo(reason if isinstance(reason, str) and reason else None)
                _write_json_payload(connection, {"ok": True})
            except Exception as exc:  # noqa: BLE001 - broker protocol must convert errors to structured replies.
                _write_json_payload(connection, {"ok": False, "error": str(exc)})

    def _validate_peer(self, connection: socket.socket) -> None:
        if not hasattr(socket, "SO_PEERCRED"):
            return
        try:
            credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            _pid, uid, _gid = struct.unpack("3i", credentials)
        except OSError:
            return
        if uid != self._expected_uid:
            raise PermissionError("elevation broker rejected request from unexpected uid")


_SUDO_SHIM_SOURCE = """#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import socket
import sys

broker = os.environ.get("DOTMAN_ELEVATION_BROKER")
real_sudo = os.environ.get("DOTMAN_REAL_SUDO")
reason = os.environ.get("DOTMAN_ELEVATION_REASON") or "run sudo command"

if not broker:
    print("dotman sudo shim: DOTMAN_ELEVATION_BROKER is not set", file=sys.stderr)
    raise SystemExit(1)
if not real_sudo:
    print("dotman sudo shim: DOTMAN_REAL_SUDO is not set", file=sys.stderr)
    raise SystemExit(1)

try:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(broker)
        client.sendall((json.dumps({"reason": reason}) + "\\n").encode("utf-8"))
        response = client.makefile("r", encoding="utf-8").readline()
except OSError as exc:
    print(f"dotman sudo shim: elevation broker request failed: {exc}", file=sys.stderr)
    raise SystemExit(1)

try:
    payload = json.loads(response)
except json.JSONDecodeError:
    print("dotman sudo shim: elevation broker returned invalid response", file=sys.stderr)
    raise SystemExit(1)

if not payload.get("ok"):
    print(f"dotman sudo shim: elevation broker denied request: {payload.get('error', 'unknown error')}", file=sys.stderr)
    raise SystemExit(1)

os.execv(real_sudo, [real_sudo, "-n", *sys.argv[1:]])
"""


_broker_lock = threading.Lock()
_active_broker: ElevationBroker | None = None
_broker_session_depth = 0
_broker_atexit_registered = False


def current_elevation_broker() -> ElevationBroker:
    global _active_broker, _broker_atexit_registered
    with _broker_lock:
        if _active_broker is None:
            _active_broker = ElevationBroker()
            if not _broker_atexit_registered:
                atexit.register(close_active_elevation_broker)
                _broker_atexit_registered = True
        return _active_broker


@contextmanager
def elevation_broker_session() -> Iterator[None]:
    global _broker_session_depth
    _broker_session_depth += 1
    try:
        yield
    finally:
        _broker_session_depth -= 1
        if _broker_session_depth == 0:
            close_active_elevation_broker()


def close_active_elevation_broker() -> None:
    global _active_broker
    with _broker_lock:
        broker = _active_broker
        _active_broker = None
    if broker is not None:
        broker.close()


def _read_json_payload(connection: socket.socket) -> dict[str, object]:
    line = connection.makefile("r", encoding="utf-8").readline()
    if not line:
        raise ValueError("empty broker request")
    payload = json.loads(line)
    if not isinstance(payload, dict):
        raise ValueError("broker request must be a JSON object")
    return payload


def _write_json_payload(connection: socket.socket, payload: dict[str, object]) -> None:
    connection.sendall((json.dumps(payload) + "\n").encode("utf-8"))


def request_elevation_from_env(reason: str | None = None) -> int:
    broker = os.environ.get(BROKER_ENV)
    if not broker:
        print(
            'dotman elevation request requires DOTMAN_ELEVATION_BROKER; run from a command with elevation = "broker" or "intercept"',
            file=sys.stderr,
        )
        return 1
    request_reason = reason or os.environ.get(REASON_ENV) or "perform privileged operation"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(broker)
            _write_json_payload(client, {"reason": request_reason})
            response = client.makefile("r", encoding="utf-8").readline()
    except OSError as exc:
        print(f"dotman elevation request failed: broker unavailable: {exc}", file=sys.stderr)
        return 1
    try:
        payload = json.loads(response)
    except json.JSONDecodeError:
        print("dotman elevation request failed: broker returned invalid response", file=sys.stderr)
        return 1
    if not payload.get("ok"):
        print(f"dotman elevation request failed: {payload.get('error', 'unknown broker error')}", file=sys.stderr)
        return 1
    return 0
