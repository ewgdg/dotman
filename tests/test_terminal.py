from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

import dotman.terminal as terminal
from dotman.diff_review import DEFAULT_REVIEW_PAGER, ReviewItem, run_review_item_diff


class DummyStream:
    def __init__(self, fd: int, *, tty: bool = True, line: str = "") -> None:
        self._fd = fd
        self._tty = tty
        self._line = line

    def isatty(self) -> bool:
        return self._tty

    def fileno(self) -> int:
        return self._fd

    def readline(self) -> str:
        return self._line


class DummyOutputStream(DummyStream):
    def __init__(self, fd: int, *, tty: bool = True) -> None:
        super().__init__(fd, tty=tty)
        self.writes: list[str] = []

    def write(self, text: str) -> int:
        self.writes.append(text)
        return len(text)

    def flush(self) -> None:
        return None


def test_preserve_terminal_state_restores_unique_tty_streams(monkeypatch) -> None:
    restored: list[tuple[int, int, list[object]]] = []

    fake_termios = SimpleNamespace(
        TCSADRAIN=7,
        error=OSError,
        tcgetattr=lambda fd: [f"attrs-{fd}"],
        tcsetattr=lambda fd, when, attrs: restored.append((fd, when, attrs)),
    )
    monkeypatch.setattr(terminal, "termios", fake_termios)

    with terminal.preserve_terminal_state(
        streams=[DummyStream(10), DummyStream(11), DummyStream(10), DummyStream(12, tty=False)]
    ):
        pass

    assert restored == [
        (10, 7, ["attrs-10"]),
        (11, 7, ["attrs-11"]),
    ]


def test_preserve_terminal_state_restores_terminal_after_keyboard_interrupt(monkeypatch) -> None:
    restored: list[tuple[int, int, list[object]]] = []

    fake_termios = SimpleNamespace(
        TCSADRAIN=9,
        error=OSError,
        tcgetattr=lambda fd: [f"attrs-{fd}"],
        tcsetattr=lambda fd, when, attrs: restored.append((fd, when, attrs)),
    )
    monkeypatch.setattr(terminal, "termios", fake_termios)

    with pytest.raises(KeyboardInterrupt):
        with terminal.preserve_terminal_state(streams=[DummyStream(20)]):
            raise KeyboardInterrupt()

    assert restored == [(20, 9, ["attrs-20"])]


def test_run_review_item_diff_preserves_terminal_state_when_interrupted(monkeypatch) -> None:
    repo_path = Path.home() / ".config" / "repo-file"
    live_path = Path.home() / ".local" / "share" / "live-file"
    review_item = ReviewItem(
        binding_label="example:git@basic",
        package_id="git",
        target_name="gitconfig",
        action="update",
        operation="push",
        repo_path=repo_path,
        live_path=live_path,
        source_path="/repo-file",
        destination_path="/live-file",
        before_bytes=b"before\n",
        after_bytes=b"after\n",
    )
    events: list[str] = []

    @contextmanager
    def fake_preserve_terminal_state():
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("dotman.diff_review._select_review_pager_command", lambda: DEFAULT_REVIEW_PAGER)
    monkeypatch.setattr("dotman.diff_review.preserve_terminal_state", fake_preserve_terminal_state)
    monkeypatch.setattr(
        "dotman.diff_review.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    with pytest.raises(KeyboardInterrupt):
        run_review_item_diff(review_item)

    assert events == ["enter", "exit"]


def test_read_prompt_line_uses_prompt_toolkit_for_interactive_stdio(monkeypatch) -> None:
    prompt_messages: list[object] = []

    monkeypatch.setattr(terminal.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(terminal.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(terminal.sys.stdin, "fileno", lambda: 0)
    monkeypatch.setattr(terminal.sys.stdout, "fileno", lambda: 1)
    monkeypatch.setattr(terminal, "prompt_toolkit_prompt", lambda message: prompt_messages.append(message) or "  n  ")

    assert terminal.read_prompt_line("Review command: ") == "n"
    assert len(prompt_messages) == 1


def test_read_prompt_line_falls_back_to_plain_readline_for_non_tty_streams() -> None:
    input_stream = DummyStream(30, tty=False, line="  2  \n")
    output_stream = DummyOutputStream(31, tty=False)

    assert terminal.read_prompt_line("Select: ", input_stream=input_stream, output_stream=output_stream) == "2"
    assert "".join(output_stream.writes) == "Select: "
