from __future__ import annotations

import subprocess
import sys

import pytest


# Every command pays for what the CLI imports before it starts work, and sync
# shows nothing until its progress bar starts. These are needed only for an
# interactive prompt or `--version`.
@pytest.mark.parametrize("module", ["prompt_toolkit", "importlib.metadata"])
def test_cli_import_defers_modules_only_some_commands_need(module: str) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", f"import sys, dotman.cli; print({module!r} in sys.modules)"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )

    assert completed.stdout.strip() == "False"
