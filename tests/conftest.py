from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys

import pytest


@pytest.fixture(autouse=True)
def isolate_xdg_config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    xdg_config_home = tmp_path / "xdg-config"
    xdg_config_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config_home))


@pytest.fixture(autouse=True)
def isolate_xdg_state_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    xdg_state_home = tmp_path / "state"
    xdg_state_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg_state_home))


@pytest.fixture(autouse=True)
def mock_sudo_for_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    import dotman.file_access as file_access

    original_run = subprocess.run

    def fake_run(command, *args, **kwargs):
        if isinstance(command, (list, tuple)) and command and command[0] == "sudo":
            if command[1:] == ["-v"]:
                return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
            if command[1:3] == ["-n", "true"]:
                return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
            if command[1:3] == ["-n", "/bin/cat"] and len(command) >= 4:
                return SimpleNamespace(returncode=0, stdout=Path(command[3]).read_bytes(), stderr=b"")
            if len(command) >= 4 and command[1] == "-n" and command[2] == sys.executable:
                return original_run(command[2:], *args, **kwargs)
            return original_run(command[1:], *args, **kwargs)
        return original_run(command, *args, **kwargs)

    monkeypatch.setattr(file_access.subprocess, "run", fake_run)
