from __future__ import annotations

from pathlib import Path

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
