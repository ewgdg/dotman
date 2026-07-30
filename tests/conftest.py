from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
from typing import Iterator

import pytest

from dotman.command_runtime import (
    ArgvCommand,
    CommandRequest,
    CommandResult,
    ProductionCommandRuntime,
    command_runtime_session,
)


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
def mock_sudo_for_tests() -> Iterator[None]:
    production = ProductionCommandRuntime()

    class TestCommandRuntime:
        def run(self, request: CommandRequest) -> CommandResult:
            command = request.command
            if not isinstance(command, ArgvCommand) or not command.arguments or command.arguments[0] != "sudo":
                return production.run(request)
            arguments = command.arguments
            if arguments[1:] == ("-v",) or arguments[1:3] == ("-n", "true"):
                return CommandResult(exit_code=0)
            if arguments[1:3] == ("-n", "/bin/cat") and len(arguments) >= 4:
                return CommandResult(exit_code=0, stdout=Path(arguments[3]).read_bytes())
            if len(arguments) >= 4 and arguments[1] == "-n" and arguments[2] == sys.executable:
                return production.run(replace(request, command=ArgvCommand(arguments[2:])))
            prefix_length = 2 if len(arguments) > 1 and arguments[1] == "-n" else 1
            return production.run(replace(request, command=ArgvCommand(arguments[prefix_length:])))

    with command_runtime_session(TestCommandRuntime()):
        yield
