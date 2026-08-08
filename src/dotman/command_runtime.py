from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from threading import Thread
from types import MappingProxyType
from typing import Callable, Iterable, Iterator, Literal, Mapping, Protocol, TextIO, TypeAlias

from dotman.models import ElevationMode
from dotman.terminal import preserve_terminal_state


INTERRUPTED_EXIT_CODE = 130
_INTERRUPT_GRACE_SECONDS = 0.5


@dataclass(frozen=True)
class ShellCommand:
    source: str

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("shell command must not be empty")


@dataclass(frozen=True)
class ArgvCommand:
    arguments: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.arguments:
            raise ValueError("argument command must not be empty")


Command: TypeAlias = ShellCommand | ArgvCommand
CommandIO: TypeAlias = Literal["pipe", "tty"]


@dataclass(frozen=True)
class CommandRequest:
    command: Command
    cwd: Path | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    excluded_env_keys: frozenset[str] = frozenset()
    io: CommandIO = "pipe"
    input: bytes | None = None
    stream_output: bool = False
    stdout_sink: TextIO | None = field(default=None, compare=False, repr=False)
    stderr_sink: TextIO | None = field(default=None, compare=False, repr=False)
    elevation: ElevationMode = "none"
    elevation_reason: str | None = None
    isolate_process_group: bool = True

    def __post_init__(self) -> None:
        if self.io not in {"pipe", "tty"}:
            raise ValueError(f"unsupported command I/O mode '{self.io}'")
        if self.io == "tty" and self.input is not None:
            raise ValueError("tty command input must come from the terminal")
        if self.io == "tty" and self.stream_output:
            raise ValueError("tty command output belongs to the terminal")
        object.__setattr__(self, "env", MappingProxyType(dict(self.env)))


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    stdout: bytes = b""
    stderr: bytes = b""

    @property
    def stdout_text(self) -> str:
        return self.stdout.decode("utf-8")

    @property
    def stderr_text(self) -> str:
        return self.stderr.decode("utf-8")


def raise_for_command_interruption(result: CommandResult) -> None:
    if result.exit_code == INTERRUPTED_EXIT_CODE:
        raise KeyboardInterrupt


class CommandRuntime(Protocol):
    def run(self, request: CommandRequest) -> CommandResult: ...


class CommandElevation(Protocol):
    def prepare(
        self,
        command: Command,
        env: Mapping[str, str],
        mode: ElevationMode,
        reason: str | None,
    ) -> tuple[Command, dict[str, str]]: ...


@dataclass(frozen=True)
class SystemCommandElevation:
    def prepare(
        self,
        command: Command,
        env: Mapping[str, str],
        mode: ElevationMode,
        reason: str | None,
    ) -> tuple[Command, dict[str, str]]:
        prepared_env = dict(env)
        if mode == "none":
            return command, prepared_env

        # Imports stay local because the privileged file helpers also execute
        # argv commands through this module. Elevation itself remains owned here.
        from dotman.file_access import request_sudo, sudo_prefix_command

        if mode == "root":
            if os.geteuid() != 0:
                request_sudo(reason or "run privileged command")
                if isinstance(command, ShellCommand):
                    command = ShellCommand(sudo_prefix_command(command.source))
                else:
                    command = ArgvCommand(("sudo", "-n", "-E", *command.arguments))
            return command, prepared_env
        if mode == "lease":
            if os.geteuid() != 0:
                request_sudo(reason or "run privileged command")
            return command, prepared_env
        if mode in {"broker", "intercept"}:
            from dotman.elevation import current_elevation_broker

            broker_env = current_elevation_broker().env(
                reason=reason or "run privileged command",
                intercept=mode == "intercept",
            )
            if mode == "intercept" and "PATH" in prepared_env and "PATH" in broker_env:
                # Command-specific PATH remains authoritative after the injected shim.
                shim_dir = broker_env["PATH"].split(os.pathsep, 1)[0]
                broker_env["PATH"] = f"{shim_dir}{os.pathsep}{prepared_env['PATH']}"
            return command, {**prepared_env, **broker_env}
        raise ValueError(f"unsupported elevation mode '{mode}'")


@dataclass
class ProductionCommandRuntime:
    elevation: CommandElevation = field(default_factory=SystemCommandElevation)

    def run(self, request: CommandRequest) -> CommandResult:
        command, request_env = self.elevation.prepare(
            request.command,
            request.env,
            request.elevation,
            request.elevation_reason,
        )
        environment = {
            **{
                key: value
                for key, value in os.environ.items()
                if key not in request.excluded_env_keys
            },
            **request_env,
        }
        if request.io == "tty":
            return self._run_with_terminal(request=request, command=command, environment=environment)
        return self._run_with_pipes(request=request, command=command, environment=environment)

    def _run_with_pipes(
        self,
        *,
        request: CommandRequest,
        command: Command,
        environment: dict[str, str],
    ) -> CommandResult:
        stdout_buffer: list[bytes] = []
        stderr_buffer: list[bytes] = []
        stdout_sink = request.stdout_sink or sys.stdout
        stderr_sink = request.stderr_sink or sys.stderr

        def pump(stream, buffer: list[bytes], sink: TextIO) -> None:
            try:
                for chunk in iter(stream.readline, b""):
                    buffer.append(chunk)
                    if request.stream_output:
                        _write_streamed_chunk(chunk, sink)
            finally:
                stream.close()

        def write_input(stream, content: bytes) -> None:
            try:
                stream.write(content)
            except BrokenPipeError:
                pass
            finally:
                stream.close()

        with preserve_terminal_state():
            # Elevated commands stay in the invoking terminal session so sudo's
            # tty-scoped lease remains usable. Ordinary pipe commands own a new
            # process group so interruption can stop the complete shell tree.
            owns_process_group = request.elevation == "none" and request.isolate_process_group
            process = subprocess.Popen(
                _process_arguments(command),
                **_process_command_options(command),
                cwd=str(request.cwd) if request.cwd is not None else None,
                env=environment,
                stdin=subprocess.PIPE if request.input is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=owns_process_group,
            )
            stdout_thread = Thread(
                target=pump,
                args=(process.stdout, stdout_buffer, stdout_sink),
                daemon=True,
            )
            stderr_thread = Thread(
                target=pump,
                args=(process.stderr, stderr_buffer, stderr_sink),
                daemon=True,
            )
            stdout_thread.start()
            stderr_thread.start()
            input_thread: Thread | None = None
            if request.input is not None:
                if process.stdin is None:
                    raise AssertionError("pipe command stdin was not created")
                input_thread = Thread(
                    target=write_input,
                    args=(process.stdin, request.input),
                    daemon=True,
                )
                input_thread.start()
            try:
                return_code = process.wait()
            except KeyboardInterrupt:
                _interrupt_pipe_process(process, owns_process_group=owns_process_group)
                _wait_for_process_exit(process)
                raise
            finally:
                if input_thread is not None:
                    input_thread.join()
                stdout_thread.join()
                stderr_thread.join()
        return CommandResult(
            exit_code=_normalize_return_code(return_code),
            stdout=b"".join(stdout_buffer),
            stderr=b"".join(stderr_buffer),
        )

    def _run_with_terminal(
        self,
        *,
        request: CommandRequest,
        command: Command,
        environment: dict[str, str],
    ) -> CommandResult:
        with preserve_terminal_state():
            process = subprocess.Popen(
                _process_arguments(command),
                **_process_command_options(command),
                cwd=str(request.cwd) if request.cwd is not None else None,
                env=environment,
            )
            previous_sigint_handler = signal.getsignal(signal.SIGINT)
            main_thread = threading.current_thread() is threading.main_thread()
            # The foreground child owns Ctrl-C; ignoring it in Dotman prevents a
            # second interruption path and duplicate UI after the child exits.
            # Signal handlers are process-global, so this is only safe from the
            # main thread; off-main-thread runs (e.g. the elevation broker)
            # must skip it.
            if main_thread:
                signal.signal(signal.SIGINT, signal.SIG_IGN)
            try:
                return_code = process.wait()
            finally:
                if main_thread:
                    signal.signal(signal.SIGINT, previous_sigint_handler)
        return CommandResult(exit_code=_normalize_return_code(return_code))


MemoryCommandOutcome: TypeAlias = (
    CommandResult | BaseException | Callable[[CommandRequest], CommandResult]
)


class MemoryCommandRuntime:
    def __init__(self, results: Iterable[MemoryCommandOutcome] = ()) -> None:
        self._results = deque(results)
        self.requests: list[CommandRequest] = []

    def queue(self, result: MemoryCommandOutcome) -> None:
        self._results.append(result)

    def run(self, request: CommandRequest) -> CommandResult:
        self.requests.append(request)
        if not self._results:
            raise AssertionError("no queued command result")
        result = self._results.popleft()
        if isinstance(result, BaseException):
            raise result
        if callable(result):
            return result(request)
        return result


DEFAULT_COMMAND_RUNTIME = ProductionCommandRuntime()
_ACTIVE_COMMAND_RUNTIME: ContextVar[CommandRuntime] = ContextVar(
    "dotman_command_runtime",
    default=DEFAULT_COMMAND_RUNTIME,
)


def current_command_runtime() -> CommandRuntime:
    return _ACTIVE_COMMAND_RUNTIME.get()


@contextmanager
def command_runtime_session(runtime: CommandRuntime) -> Iterator[None]:
    token = _ACTIVE_COMMAND_RUNTIME.set(runtime)
    try:
        yield
    finally:
        _ACTIVE_COMMAND_RUNTIME.reset(token)


def _process_arguments(command: Command) -> str | tuple[str, ...]:
    if isinstance(command, ShellCommand):
        return command.source
    return command.arguments


def _process_command_options(command: Command) -> dict[str, object]:
    if isinstance(command, ShellCommand):
        return {"shell": True, "executable": "/bin/sh"}
    return {"shell": False}


def _write_streamed_chunk(chunk: bytes, sink: TextIO) -> None:
    text = chunk.decode("utf-8", errors="replace")
    for line in text.splitlines(keepends=True):
        sink.write(f"      {line}")
        sink.flush()


def _interrupt_pipe_process(process: subprocess.Popen[bytes], *, owns_process_group: bool) -> None:
    if not owns_process_group:
        try:
            process.terminate()
        except OSError:
            pass
        return
    try:
        os.killpg(process.pid, signal.SIGINT)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            process.terminate()
        except OSError:
            pass


def _wait_for_process_exit(process: subprocess.Popen[bytes]) -> None:
    try:
        process.wait(timeout=_INTERRUPT_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                process.kill()
            except OSError:
                pass
        process.wait()


def _normalize_return_code(return_code: int) -> int:
    if return_code in {INTERRUPTED_EXIT_CODE, -signal.SIGINT}:
        return INTERRUPTED_EXIT_CODE
    return return_code
