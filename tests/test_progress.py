from __future__ import annotations

import sys
from threading import Event
from types import SimpleNamespace

import pytest

from dotman import planning
from dotman.progress import _TqdmSink, make_planning_sink
from tests.helpers import make_package_plan, make_resolved_package_selection


class FakeSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, int | None]] = []

    def start(self, total: int) -> None:
        self.events.append(("start", total))

    def update(self, n: int = 1) -> None:
        self.events.append(("update", n))

    def close(self) -> None:
        self.events.append(("close", None))


def test_make_planning_sink_skips_json_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stderr.isatty", lambda: True)

    assert make_planning_sink(json_output=True) is None


def test_make_planning_sink_skips_non_tty_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stderr.isatty", lambda: False)

    assert make_planning_sink(json_output=False) is None


def test_tqdm_sink_closes_after_update() -> None:
    sink = _TqdmSink()

    sink.start(1)
    sink.update(1)
    sink.close()


def test_tqdm_sink_redraws_elapsed_without_progress_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeTqdm:
        instances: list["FakeTqdm"] = []

        def __init__(self, **_kwargs) -> None:
            self.refreshed = Event()
            self.refresh_count = 0
            self.closed = False
            FakeTqdm.instances.append(self)

        def update(self, _n: int = 1) -> None:
            raise AssertionError("redraw timer must not advance progress")

        def refresh(self) -> None:
            self.refresh_count += 1
            self.refreshed.set()

        def close(self) -> None:
            self.closed = True

    monkeypatch.setitem(sys.modules, "tqdm", SimpleNamespace(tqdm=FakeTqdm))
    sink = _TqdmSink(refresh_interval=0.01)

    sink.start(1)
    fake_pbar = FakeTqdm.instances[0]
    try:
        assert fake_pbar.refreshed.wait(timeout=1)
        assert fake_pbar.refresh_count >= 1
    finally:
        sink.close()

    assert fake_pbar.closed
    assert sink._refresh_thread is None


def test_collect_tracked_candidates_reports_progress_after_package_build(monkeypatch: pytest.MonkeyPatch) -> None:
    selection = make_resolved_package_selection(repo_name="example", package_id="git", requested_profile="basic")
    events: list[str] = []

    monkeypatch.setattr(planning, "resolve_tracked_package_selections", lambda _engine, entries_by_repo=None: [selection])

    def build_package_plan(_engine, _repo, built_selection, *, operation: str):
        assert built_selection is selection
        events.append("built")
        return make_package_plan(
            operation=operation,
            repo_name="example",
            package_id="git",
            requested_profile="basic",
        )

    class OrderingSink(FakeSink):
        def start(self, total: int) -> None:
            events.append(f"start:{total}")
            super().start(total)

        def update(self, n: int = 1) -> None:
            events.append("update")
            super().update(n)

        def close(self) -> None:
            events.append("close")
            super().close()

    monkeypatch.setattr(planning, "build_package_plan", build_package_plan)
    sink = OrderingSink()

    plans, candidates_by_path = planning.collect_tracked_candidates(
        SimpleNamespace(get_repo=lambda repo_name: SimpleNamespace(name=repo_name)),
        operation="push",
        sink=sink,
    )

    assert len(plans) == 1
    assert candidates_by_path == {}
    assert events == ["start:1", "built", "update", "close"]
    assert sink.events == [("start", 1), ("update", 1), ("close", None)]


def test_collect_tracked_candidates_closes_progress_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    selection = make_resolved_package_selection(repo_name="example", package_id="git", requested_profile="basic")
    sink = FakeSink()

    monkeypatch.setattr(planning, "resolve_tracked_package_selections", lambda _engine, entries_by_repo=None: [selection])

    def fail_build_package_plan(*_args, **_kwargs):
        raise RuntimeError("planning failed")

    monkeypatch.setattr(planning, "build_package_plan", fail_build_package_plan)

    with pytest.raises(RuntimeError, match="planning failed"):
        planning.collect_tracked_candidates(
            SimpleNamespace(get_repo=lambda repo_name: SimpleNamespace(name=repo_name)),
            operation="push",
            sink=sink,
        )

    assert sink.events == [("start", 1), ("close", None)]
