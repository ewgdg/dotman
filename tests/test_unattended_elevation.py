from __future__ import annotations

import pytest

from dotman import cli_interaction
from dotman.command_runtime import ArgvCommand, CommandResult, MemoryCommandRuntime
from dotman.file_access import _SudoLease


@pytest.mark.parametrize("exit_code", [0, 1])
def test_unattended_elevation_never_requests_password(monkeypatch, exit_code) -> None:
    monkeypatch.setattr("os.geteuid", lambda: 1000)
    runtime = MemoryCommandRuntime([CommandResult(exit_code=exit_code)])
    lease = _SudoLease(runtime)
    try:
        with cli_interaction.interaction_scope(unattended=True):
            if exit_code:
                with pytest.raises(ValueError, match="unattended"):
                    lease.request("publish files")
            else:
                lease.request("publish files")
    finally:
        lease.close()
    assert runtime.requests[0].command == ArgvCommand(("sudo", "-n", "-v"))
    assert runtime.requests[0].io == "pipe"


def test_unattended_runtime_rejects_tty_before_elevation() -> None:
    from dotman.command_runtime import CommandRequest, ProductionCommandRuntime

    class UnexpectedElevation:
        def prepare(self, *args):
            raise AssertionError("TTY execution must fail before elevation")

    runtime = ProductionCommandRuntime(elevation=UnexpectedElevation())
    with cli_interaction.interaction_scope(unattended=True):
        with pytest.raises(ValueError, match="unattended"):
            runtime.run(CommandRequest(command=ArgvCommand(("unused",)), io="tty"))
