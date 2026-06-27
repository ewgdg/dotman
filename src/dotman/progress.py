from __future__ import annotations

import sys
from threading import Event, Lock, Thread
from typing import Any, Protocol


DEFAULT_REDRAW_INTERVAL_SECONDS = 1.0


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

    def __init__(
        self,
        *,
        refresh_interval: float = DEFAULT_REDRAW_INTERVAL_SECONDS,
    ) -> None:
        self._pbar: Any | None = None  # deferred until start()
        self._refresh_interval = refresh_interval
        self._pbar_lock = Lock()
        self._refresh_stop: Event | None = None
        self._refresh_thread: Thread | None = None

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
        self._start_redraw_timer()

    def update(self, n: int = 1) -> None:
        with self._pbar_lock:
            if self._pbar is None:
                return
            self._pbar.update(n)

    def close(self) -> None:
        if self._pbar is None:
            return
        self._stop_redraw_timer()
        with self._pbar_lock:
            if self._pbar is not None:
                self._pbar.close()
                self._pbar = None

    def _start_redraw_timer(self) -> None:
        self._refresh_stop = Event()
        self._refresh_thread = Thread(target=self._redraw_until_closed, daemon=True)
        self._refresh_thread.start()

    def _stop_redraw_timer(self) -> None:
        if self._refresh_stop is not None:
            self._refresh_stop.set()
        if self._refresh_thread is not None:
            self._refresh_thread.join()
        self._refresh_stop = None
        self._refresh_thread = None

    def _redraw_until_closed(self) -> None:
        if self._refresh_stop is None:
            return
        while not self._refresh_stop.wait(self._refresh_interval):
            with self._pbar_lock:
                if self._pbar is None:
                    return
                self._pbar.refresh()
