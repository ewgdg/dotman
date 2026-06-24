from __future__ import annotations

import sys
from typing import Any, Protocol


class ProgressSink(Protocol):
    """Protocol for progress reporting during planning."""

    def start(self, total: int) -> None: ...
    def update(self, n: int = 1) -> None: ...
    def close(self) -> None: ...


def make_planning_sink(*, json_output: bool) -> ProgressSink | None:
    """Return a ProgressSink for the planning phase, or None when skipped.

    Returns None in JSON mode or when stderr is not a TTY.
    """
    if json_output or not sys.stderr.isatty():
        return None
    return _TqdmSink()


class _TqdmSink:
    """Progress sink backed by a tqdm progress bar writing to stderr."""

    def __init__(self) -> None:
        self._pbar: Any | None = None  # deferred until start()

    def start(self, total: int) -> None:
        from tqdm import tqdm

        self._pbar = tqdm(
            total=total,
            file=sys.stderr,
            desc="Planning",
            unit="pkg",
            leave=False,
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}]",
        )

    def update(self, n: int = 1) -> None:
        if self._pbar is None:
            return
        self._pbar.update(n)

    def close(self) -> None:
        if self._pbar is not None:
            self._pbar.close()
            self._pbar = None
