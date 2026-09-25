"""One-sided operations omit Guard-skipped work but must still say why."""
from __future__ import annotations

import pytest

from dotman.engine import DotmanEngine
from dotman.sync_session import AuxiliaryRow, BatchSetApproval, SetIncluded
from tests.engine.test_sync_session import make_engine


def guard_skip_rows(session):
    return [row for row in session.view.rows if isinstance(row, AuxiliaryRow) and row.kind == "guard-skip"]


@pytest.mark.parametrize("direction,opener", [("push", "open_push_session"), ("pull", "open_pull_session")])
def test_one_sided_guard_skip_is_visible_and_not_selectable(tmp_path, monkeypatch, direction, opener):
    engine = make_engine(tmp_path, monkeypatch, [
        ("unit", "both", b"repo", b"live", f'[targets.unit.hooks]\nguard_{direction} = "echo offline >&2; exit 100"'),
    ])
    with getattr(engine, opener)(engine.resolve_sync_scope()) as session:
        assert session.view.observations == ()
        row, = guard_skip_rows(session)
        assert row.scope == "main:app.unit"
        assert row.directions == (direction,)
        assert row.guard_skip.reason == "offline"
        assert not row.included and row.allowed_commands == ()
        view = session.view
        session.dispatch(BatchSetApproval(view.session_id, view.revision, True))
        view = session.view
        session.dispatch(SetIncluded(view.session_id, view.revision, row.row_id, True))
        assert not guard_skip_rows(session)[0].included
        assert session.execute().result.status == "completed"
    assert (tmp_path / "live/unit").read_bytes() == b"live"
    assert (tmp_path / "repo/packages/app/unit").read_bytes() == b"repo"


def test_package_guard_skip_reports_one_row_for_the_scope(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [
        ("a", "push-only", b"repo", b"live", ""),
        ("b", "push-only", b"repo", b"live", ""),
    ])
    manifest = tmp_path / "repo/packages/app/package.toml"
    manifest.write_text(manifest.read_text() + '\n[hooks]\nguard_push = "exit 100"\n')
    engine = DotmanEngine(engine.config)
    with engine.open_push_session(engine.resolve_sync_scope()) as session:
        row, = guard_skip_rows(session)
        assert row.scope == "main:app"
        assert row.guard_skip.scope_kind == "package"


@pytest.mark.parametrize("direction,remaining", [("push", "pull-only"), ("pull", "push-only")])
def test_sync_guard_narrowing_reports_the_removed_direction(tmp_path, monkeypatch, direction, remaining):
    engine = make_engine(tmp_path, monkeypatch, [
        ("unit", "both", b"repo", b"live", f'[targets.unit.hooks]\nguard_{direction} = "echo offline >&2; exit 100"'),
    ])
    with engine.open_sync_session(engine.resolve_sync_scope(), preview=True) as session:
        # Sync keeps the unit on its remaining route; the Guard row says why.
        unit, = session.view.observations
        assert unit.effective_policy == remaining
        row, = guard_skip_rows(session)
        assert (row.scope, row.directions, row.guard_skip.reason) == ("main:app.unit", (direction,), "offline")
        assert not row.included and row.allowed_commands == ()
