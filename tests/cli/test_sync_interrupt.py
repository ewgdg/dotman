"""Real process interruption across the Sync CLI and terminal lifecycle."""

from __future__ import annotations

import json
import os
import pty
import select
import signal
import subprocess
import sys
import termios
import time

import pytest

from dotman.operation_lock import OperationLock
from tests.engine.test_sync_both_convergence import established


def wait_until(predicate, process, read_output=lambda: None):
    deadline = time.monotonic() + 5
    while not predicate():
        assert process.poll() is None or predicate(), "Sync exited before reaching the interruption boundary"
        assert time.monotonic() < deadline, "Sync did not reach the interruption boundary"
        read_output()
        time.sleep(0.01)


@pytest.mark.parametrize("keys,title", [
    (b"", b"Merge"),
    (b"\r", b"Proposal Review"),
    (b" x", b"Confirmation"),
], ids=["workset", "review", "confirmation"])
def test_ctrl_c_with_sync_base_exits_cleanly(tmp_path, monkeypatch, keys, title):
    established(tmp_path, monkeypatch)
    source = tmp_path / "repo/packages/app/unit"
    live = tmp_path / "live/unit"
    before = source.read_bytes(), live.read_bytes()
    master, slave = pty.openpty()
    terminal_before = termios.tcgetattr(slave)
    process = subprocess.Popen(
        [sys.executable, "-m", "dotman.cli", "--config", str(tmp_path / "config.toml"), "sync"],
        stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
        env={**os.environ, "TERM": "xterm-256color"},
    )
    output = bytearray()

    def read_output():
        if select.select([master], [], [], 0.01)[0]:
            output.extend(os.read(master, 65536))

    try:
        wait_until(lambda: b"Merge" in output, process, read_output)
        if keys:
            os.write(master, keys)
            wait_until(lambda: title in output, process, read_output)
        # Textual's raw terminal receives the real Ctrl-C byte as a key event.
        os.write(master, b"\x03")
        wait_until(lambda: process.poll() is not None, process, read_output)
        while select.select([master], [], [], 0.05)[0]:
            read_output()
        assert b"Traceback" not in output, output[-2000:].decode(errors="replace")
        assert b"Task exception" not in output
        assert process.returncode == 130
        assert termios.tcgetattr(slave) == terminal_before
        assert b"\x1b[?1049l" in output  # Leave the alternate screen.
        assert b"\x1b[?25h" in output  # Restore cursor visibility.
        assert (source.read_bytes(), live.read_bytes()) == before
        with OperationLock.acquire(tmp_path / "state/dotman"):
            pass
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        os.close(master)
        os.close(slave)


def test_sigint_with_sync_base_emits_clean_json_and_stops_execution(tmp_path, monkeypatch):
    ready, later = tmp_path / "ready", tmp_path / "later"
    established(tmp_path, monkeypatch, (
        '[targets.unit.hooks]\n'
        f'pre_pull = "touch {ready}; sleep 2; touch {later}"'
    ))
    source = tmp_path / "repo/packages/app/unit"
    live = tmp_path / "live/unit"
    before = source.read_bytes(), live.read_bytes()
    process = subprocess.Popen(
        [sys.executable, "-m", "dotman.cli", "--config", str(tmp_path / "config.toml"),
         "--json", "--unattended", "sync"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        wait_until(ready.exists, process)
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=5)
        assert b"Traceback" not in stderr, stderr.decode()
        assert stderr == b""
        assert process.returncode == 130
        payload = json.loads(stdout)
        assert payload["sync_units"][0]["base"]["provenance"] == "exact"
        assert (source.read_bytes(), live.read_bytes()) == before
        assert not later.exists()
        with OperationLock.acquire(tmp_path / "state/dotman"):
            pass
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
