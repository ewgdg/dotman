from __future__ import annotations

import os
import subprocess

from dotman import elevation
from dotman.cli import main


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
