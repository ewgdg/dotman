from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import Iterator, Sequence

from prompt_toolkit import prompt as prompt_toolkit_prompt
from prompt_toolkit.formatted_text import ANSI

try:
    import termios
except ImportError:  # pragma: no cover - non-POSIX platforms do not expose termios.
    termios = None


type TerminalStateSnapshot = tuple[int, list[int | bytes]]


@contextmanager
def preserve_terminal_state(*, streams: Sequence[object] | None = None) -> Iterator[None]:
    snapshots = _capture_terminal_state(streams=streams)
    try:
        yield
    finally:
        _restore_terminal_state(snapshots)


def read_prompt_line(
    message: str,
    *,
    input_stream: object | None = None,
    output_stream: object | None = None,
) -> str:
    input_stream = sys.stdin if input_stream is None else input_stream
    output_stream = sys.stdout if output_stream is None else output_stream

    if _prompt_toolkit_supported(input_stream=input_stream, output_stream=output_stream):
        try:
            return _prompt_with_toolkit(message).strip()
        except EOFError:
            return ""

    output_stream.write(message)
    output_stream.flush()
    answer = input_stream.readline()
    return answer.strip()


def _capture_terminal_state(*, streams: Sequence[object] | None = None) -> list[TerminalStateSnapshot]:
    if termios is None:
        return []

    snapshots: list[TerminalStateSnapshot] = []
    seen_fds: set[int] = set()
    for stream in streams or (sys.stdin, sys.stdout, sys.stderr):
        file_descriptor = _tty_file_descriptor(stream)
        if file_descriptor is None or file_descriptor in seen_fds:
            continue
        seen_fds.add(file_descriptor)
        try:
            snapshots.append((file_descriptor, termios.tcgetattr(file_descriptor)))
        except (OSError, termios.error, ValueError):
            continue
    return snapshots


def _restore_terminal_state(snapshots: Sequence[TerminalStateSnapshot]) -> None:
    if termios is None:
        return

    for file_descriptor, attributes in snapshots:
        try:
            # Interactive helpers sometimes exit on SIGINT before restoring the
            # tty they switched into raw mode. Put the caller's terminal back so
            # the next prompt does not echo Enter as `^M`.
            termios.tcsetattr(file_descriptor, termios.TCSADRAIN, attributes)
        except (OSError, termios.error, ValueError):
            continue


def _tty_file_descriptor(stream: object) -> int | None:
    try:
        is_tty = bool(stream.isatty())
    except (AttributeError, OSError, ValueError):
        return None
    if not is_tty:
        return None
    try:
        return int(stream.fileno())
    except (AttributeError, OSError, ValueError, TypeError):
        return None


def _prompt_toolkit_supported(*, input_stream: object, output_stream: object) -> bool:
    return input_stream is sys.stdin and output_stream is sys.stdout and _tty_file_descriptor(input_stream) is not None and _tty_file_descriptor(output_stream) is not None


def _prompt_with_toolkit(message: str) -> str:
    prompt_message: str | ANSI
    if "\x1b[" in message:
        prompt_message = ANSI(message)
    else:
        prompt_message = message
    return prompt_toolkit_prompt(prompt_message)
