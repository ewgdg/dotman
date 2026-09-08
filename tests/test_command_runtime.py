from __future__ import annotations

import io
import signal
import sys
from pathlib import Path
from threading import Thread

import pytest

from dotman.command_runtime import (
    ArgvCommand,
    CommandRequest,
    CommandResult,
    MemoryCommandRuntime,
    ProductionCommandRuntime,
    ShellCommand,
    SystemCommandElevation,
    raise_for_command_interruption,
)


def test_memory_runtime_records_requests_and_returns_results() -> None:
    expected = CommandResult(exit_code=17, stdout=b"out", stderr=b"err")
    runtime = MemoryCommandRuntime([expected])
    request = CommandRequest(command=ShellCommand("exit 17"), env={"MODE": "test"})

    assert runtime.run(request) is expected
    assert runtime.requests == [request]

    with pytest.raises(AssertionError, match="no queued command result"):
        runtime.run(request)


def test_memory_runtime_can_raise_a_queued_interruption() -> None:
    runtime = MemoryCommandRuntime([KeyboardInterrupt()])

    with pytest.raises(KeyboardInterrupt):
        runtime.run(CommandRequest(command=ArgvCommand(("unused",))))


def test_memory_runtime_can_compute_a_result_from_the_recorded_request() -> None:
    runtime = MemoryCommandRuntime(
        [lambda request: CommandResult(exit_code=len(request.command.arguments))]
    )

    result = runtime.run(CommandRequest(command=ArgvCommand(("one", "two"))))

    assert result.exit_code == 2


def test_raise_for_command_interruption_preserves_normalized_sigint() -> None:
    raise_for_command_interruption(CommandResult(exit_code=0))

    with pytest.raises(KeyboardInterrupt):
        raise_for_command_interruption(CommandResult(exit_code=130))


def test_production_runtime_pipe_argv_merges_environment_and_captures_bytes(tmp_path: Path) -> None:
    request = CommandRequest(
        command=ArgvCommand(
            (
                sys.executable,
                "-c",
                (
                    "import os, sys; "
                    "sys.stdout.buffer.write(sys.stdin.buffer.read() + os.environ['DOTMAN_TEST'].encode()); "
                    "sys.stderr.buffer.write(b'err')"
                ),
            )
        ),
        cwd=tmp_path,
        env={"DOTMAN_TEST": "-env"},
        input=b"payload",
    )
    result = ProductionCommandRuntime().run(request)

    assert result == CommandResult(exit_code=0, stdout=b"payload-env", stderr=b"err")


def test_production_runtime_shell_excludes_ambient_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOTMAN_REMOVED", "ambient")

    result = ProductionCommandRuntime().run(
        CommandRequest(
            command=ShellCommand("printf '%s' \"${DOTMAN_REMOVED-unset}:$DOTMAN_KEPT\""),
            env={"DOTMAN_KEPT": "overlay"},
            excluded_env_keys=frozenset({"DOTMAN_REMOVED"}),
        )
    )

    assert result.stdout == b"unset:overlay"


def test_production_runtime_streams_prefixed_output_while_capturing_it() -> None:
    stdout_sink = io.StringIO()
    stderr_sink = io.StringIO()

    result = ProductionCommandRuntime().run(
        CommandRequest(
            command=ShellCommand("printf 'one\\ntwo\\n'; printf 'bad\\n' >&2"),
            stream_output=True,
            stdout_sink=stdout_sink,
            stderr_sink=stderr_sink,
        )
    )

    assert result.stdout == b"one\ntwo\n"
    assert result.stderr == b"bad\n"
    assert stdout_sink.getvalue() == "      one\n      two\n"
    assert stderr_sink.getvalue() == "      bad\n"


def test_production_runtime_tty_mode_inherits_process_streams(capfd: pytest.CaptureFixture[str]) -> None:
    result = ProductionCommandRuntime().run(
        CommandRequest(
            command=ArgvCommand((sys.executable, "-c", "import sys; print('tty-out'); print('tty-err', file=sys.stderr)")),
            io="tty",
        )
    )

    captured = capfd.readouterr()
    assert result == CommandResult(exit_code=0)
    assert "tty-out" in captured.out
    assert "tty-err" in captured.err


def test_production_runtime_tty_mode_from_non_main_thread() -> None:
    # signal.signal() raises ValueError outside the main thread; tty-mode runs
    # happen off the main thread when the elevation broker serves a sudo shim
    # request, so they must not touch process-global signal handlers there.
    results: list[CommandResult] = []
    failures: list[BaseException] = []

    def run_tty_command() -> None:
        try:
            results.append(
                ProductionCommandRuntime().run(
                    CommandRequest(
                        command=ArgvCommand((sys.executable, "-c", "print('thread-tty')")),
                        io="tty",
                    )
                )
            )
        except BaseException as exc:  # noqa: BLE001 - surfaced to the test body.
            failures.append(exc)

    thread = Thread(target=run_tty_command)
    thread.start()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert failures == []
    assert results == [CommandResult(exit_code=0)]


def test_production_runtime_delegates_elevation_preparation() -> None:
    class RecordingElevation:
        def __init__(self) -> None:
            self.modes: list[str] = []

        def prepare(self, command, env, mode, reason):
            self.modes.append(mode)
            return command, {**env, "ELEVATED": "yes"}

    elevation = RecordingElevation()
    runtime = ProductionCommandRuntime(elevation=elevation)

    result = runtime.run(
        CommandRequest(
            command=ShellCommand("printf %s \"$ELEVATED\""),
            elevation="broker",
        )
    )

    assert elevation.modes == ["broker"]
    assert result.stdout == b"yes"


def test_system_elevation_root_requests_sudo_and_wraps_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    reasons: list[str | None] = []
    monkeypatch.setattr("dotman.command_runtime.os.geteuid", lambda: 1000)
    monkeypatch.setattr("dotman.file_access.request_sudo", reasons.append)
    monkeypatch.setattr("dotman.file_access.sudo_prefix_command", lambda command: f"ROOT({command})")

    command, env = SystemCommandElevation().prepare(
        ShellCommand("systemctl restart service"),
        {"X": "1"},
        "root",
        "restart protected service",
    )

    assert reasons == ["restart protected service"]
    assert command == ShellCommand("ROOT(systemctl restart service)")
    assert env == {"X": "1"}


def test_system_elevation_lease_requests_sudo_without_wrapping(monkeypatch: pytest.MonkeyPatch) -> None:
    reasons: list[str | None] = []
    monkeypatch.setattr("dotman.command_runtime.os.geteuid", lambda: 1000)
    monkeypatch.setattr("dotman.file_access.request_sudo", reasons.append)
    command = ArgvCommand(("installer",))

    prepared_command, _env = SystemCommandElevation().prepare(
        command,
        {},
        "lease",
        "prepare installer",
    )

    assert reasons == ["prepare installer"]
    assert prepared_command is command


def test_system_elevation_intercept_preserves_command_path_after_shim(monkeypatch: pytest.MonkeyPatch) -> None:
    class Broker:
        def env(self, *, reason, intercept):
            assert reason == "install dependencies"
            assert intercept is True
            return {"DOTMAN_ELEVATION_BROKER": "/runtime/broker", "PATH": "/shim:/usr/bin"}

    monkeypatch.setattr("dotman.elevation.current_elevation_broker", lambda: Broker())

    _command, env = SystemCommandElevation().prepare(
        ShellCommand("installer"),
        {"PATH": "/custom/bin", "X": "1"},
        "intercept",
        "install dependencies",
    )

    assert env == {
        "PATH": "/shim:/custom/bin",
        "X": "1",
        "DOTMAN_ELEVATION_BROKER": "/runtime/broker",
    }


def test_production_runtime_normalizes_sigint_exit() -> None:
    result = ProductionCommandRuntime().run(
        CommandRequest(
            command=ArgvCommand(
                (
                    sys.executable,
                    "-c",
                    f"import os, signal; os.kill(os.getpid(), {signal.SIGINT})",
                )
            )
        )
    )

    assert result.exit_code == 130


def test_command_result_text_requires_valid_utf8() -> None:
    result = CommandResult(exit_code=0, stdout="text".encode(), stderr=b"\xff")

    assert result.stdout_text == "text"
    with pytest.raises(UnicodeDecodeError):
        _ = result.stderr_text

@pytest.mark.parametrize("runtime_type", [ProductionCommandRuntime, MemoryCommandRuntime])
def test_cancel_latch_prevents_launch(runtime_type) -> None:
    runtime = runtime_type()
    runtime.request_cancel()
    runtime.request_cancel()
    with pytest.raises(InterruptedError):
        runtime.check_cancelled()
    with pytest.raises(InterruptedError):
        runtime.run(CommandRequest(command=ArgvCommand(("must-not-launch",))))


def test_copied_context_cancels_ignored_sigint_and_reaps_group(tmp_path: Path) -> None:
    from contextvars import copy_context
    import os
    import time
    from dotman.command_runtime import command_runtime_session, current_command_runtime

    ready = tmp_path / "ready"
    runtime = ProductionCommandRuntime()
    errors = []
    code = (
        "import os, signal, time; from pathlib import Path; "
        "signal.signal(signal.SIGINT, signal.SIG_IGN); "
        "pid = os.fork(); "
        f"Path({str(ready)!r}).write_text(str(os.getpid())) if pid else None; "
        "time.sleep(30)"
    )
    def run():
        try:
            current_command_runtime().run(CommandRequest(command=ArgvCommand((sys.executable, "-c", code))))
        except BaseException as exc:
            errors.append(exc)
    with command_runtime_session(runtime):
        thread = Thread(target=copy_context().run, args=(run,), daemon=True)
        thread.start()
        deadline = time.monotonic() + 3
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        try:
            assert ready.exists()
        finally:
            runtime.request_cancel()
            runtime.request_cancel()
            thread.join(3)
    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], InterruptedError)
    with pytest.raises(ProcessLookupError):
        os.kill(int(ready.read_text()), 0)


def test_cancel_during_spawn_is_cleaned_up(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess
    runtime = ProductionCommandRuntime()
    original = subprocess.Popen
    spawned = []
    def spawn(*args, **kwargs):
        process = original(*args, **kwargs)
        spawned.append(process)
        runtime.request_cancel()
        return process
    monkeypatch.setattr("dotman.command_runtime.subprocess.Popen", spawn)
    with pytest.raises(InterruptedError):
        runtime.run(CommandRequest(command=ArgvCommand((sys.executable, "-c", "import time; time.sleep(30)"))))
    assert spawned[0].poll() is not None

def test_cancellation_stops_descendants_after_group_leader_exits(tmp_path: Path) -> None:
    import time
    ready = tmp_path / "child-ready"
    runtime = ProductionCommandRuntime()
    errors = []
    code = (
        "import os, signal, time; from pathlib import Path; "
        "pid = os.fork(); "
        "os._exit(0) if pid else None; "
        "signal.signal(signal.SIGINT, signal.SIG_IGN); "
        f"Path({str(ready)!r}).touch(); "
        "time.sleep(30)"
    )
    def run():
        try:
            runtime.run(CommandRequest(command=ArgvCommand((sys.executable, "-c", code))))
        except BaseException as exc:
            errors.append(exc)
    thread = Thread(target=run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 3
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    try:
        assert ready.exists()
    finally:
        runtime.request_cancel()
        thread.join(3)
    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], InterruptedError)
