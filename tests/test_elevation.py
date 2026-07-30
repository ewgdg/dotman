from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from dotman import elevation, file_access
from dotman.cli import main
from dotman.command_runtime import CommandResult, MemoryCommandRuntime, command_runtime_session


def test_elevation_request_without_broker_env_fails_cleanly(monkeypatch, capsys) -> None:
    monkeypatch.delenv(elevation.BROKER_ENV, raising=False)

    exit_code = elevation.request_elevation_from_env("install packages")

    assert exit_code == 1
    assert "requires DOTMAN_ELEVATION_BROKER" in capsys.readouterr().err


def test_elevation_request_cli_is_parseable_and_hidden_helper(monkeypatch, capsys) -> None:
    monkeypatch.delenv(elevation.BROKER_ENV, raising=False)

    exit_code = main(["elevation", "request", "install packages"])

    assert exit_code == 1
    assert "requires DOTMAN_ELEVATION_BROKER" in capsys.readouterr().err


def test_elevation_request_contacts_broker_and_requests_sudo(monkeypatch) -> None:
    broker = elevation.ElevationBroker()
    recorded_reasons: list[str | None] = []
    monkeypatch.setattr(elevation, "request_sudo", lambda reason=None: recorded_reasons.append(reason))

    try:
        broker.start()
        monkeypatch.setenv(elevation.BROKER_ENV, str(broker.socket_path))

        exit_code = elevation.request_elevation_from_env("install missing Arch packages")

        assert exit_code == 0
        assert recorded_reasons == ["install missing Arch packages"]
    finally:
        broker.close()


def test_elevation_broker_preserves_runtime_in_request_thread(monkeypatch) -> None:
    broker = elevation.ElevationBroker()
    runtime = MemoryCommandRuntime([CommandResult(exit_code=0)])
    monkeypatch.setattr(file_access.os, "geteuid", lambda: 1000)

    try:
        with file_access.sudo_session(), command_runtime_session(runtime):
            broker.start()
            monkeypatch.setenv(elevation.BROKER_ENV, str(broker.socket_path))

            assert elevation.request_elevation_from_env("install packages") == 0

        assert [request.command.arguments for request in runtime.requests] == [
            ("sudo", "-v")
        ]
    finally:
        broker.close()


def test_intercept_sudo_shim_fails_nonzero_when_broker_is_unreachable(monkeypatch) -> None:
    broker = elevation.ElevationBroker()
    monkeypatch.setattr(elevation.shutil, "which", lambda command: "/bin/true" if command == "sudo" else None)
    try:
        shim_env = broker.env(reason="legacy sudo command", intercept=True)
        shim_path = shim_env["PATH"].split(os.pathsep, 1)[0] + "/sudo"
        shim_env[elevation.BROKER_ENV] = f"{broker.socket_path}.missing"

        completed = subprocess.run(
            [shim_path, "true"],
            env={**os.environ, **shim_env},
            capture_output=True,
            text=True,
            check=False,
        )

        assert completed.returncode != 0
        assert "elevation broker request failed" in completed.stderr
    finally:
        broker.close()


def test_intercept_sudo_shim_normalizes_real_sudo_interruption(
    monkeypatch,
    tmp_path,
) -> None:
    interrupted_sudo = tmp_path / "interrupted-sudo"
    interrupted_sudo.write_text(
        f"#!{sys.executable}\nimport os\nimport signal\nos.kill(os.getpid(), signal.SIGINT)\n",
        encoding="utf-8",
    )
    interrupted_sudo.chmod(0o755)
    broker = elevation.ElevationBroker()
    monkeypatch.setattr(elevation, "request_sudo", lambda reason=None: None)
    monkeypatch.setattr(
        elevation.shutil,
        "which",
        lambda command: str(interrupted_sudo) if command == "sudo" else None,
    )
    try:
        shim_env = broker.env(reason="interrupted sudo command", intercept=True)
        shim_path = shim_env["PATH"].split(os.pathsep, 1)[0] + "/sudo"
        shim_env["PATH"] = f"{Path(shim_path).parent}{os.pathsep}/usr/bin{os.pathsep}/bin"

        completed = subprocess.run(
            [shim_path, "true"],
            env={**os.environ, **shim_env},
            capture_output=True,
            text=True,
            check=False,
        )

        assert completed.returncode == 130
    finally:
        broker.close()
