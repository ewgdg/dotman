"""File-target chmod convergence at the SyncSession boundary."""

import pytest

from dotman import file_access
from dotman.sync_base_store import FilePresent
from dotman.sync_session import CommandAccepted, SetApproval
from tests.engine.test_sync_session import make_engine, open_session


def approve(session):
    row, = session.view.rows
    accepted = session.dispatch(SetApproval(session.view.session_id, session.view.revision, row.row_id, True))
    assert isinstance(accepted, CommandAccepted)


def test_both_policy_file_mode_drift_requires_approval_before_checkpoint(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [
        ("unit", "both", b"same", b"same", 'chmod = "0600"'),
    ])
    live = tmp_path / "live/unit"
    live.chmod(0o644)

    with open_session(engine, preview=False) as session:
        observation, = session.view.observations
        assert observation.state == "drifted"
        assert observation.base.record is None
        assert [row.row_id for row in session.view.rows] == ["main:app.unit"]
        approve(session)
        assert [effect.kind for effect in session.view.rows[0].proposal.publication_effects] == ["chmod"]
        assert session.execute().result.units[0].status == "converged"

    assert live.stat().st_mode & 0o777 == 0o600
    with open_session(engine) as later:
        observation, = later.view.observations
        assert observation.state == "directly-in-sync"
        assert observation.base.record.payload == FilePresent(b"same")


def test_failed_both_policy_file_chmod_cannot_checkpoint(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [
        ("unit", "both", b"same", b"same", 'chmod = "0600"'),
    ])
    live = tmp_path / "live/unit"
    live.chmod(0o644)

    with open_session(engine, preview=False) as session:
        approve(session)
        chmod = file_access.chmod
        with monkeypatch.context() as patch:
            def fail_chmod(path, mode):
                if path == live:
                    raise OSError("mode unavailable")
                return chmod(path, mode)
            patch.setattr(file_access, "chmod", fail_chmod)
            assert session.execute().result.units[0].status == "execution-failed"

    assert live.stat().st_mode & 0o777 == 0o644
    with open_session(engine) as later:
        observation, = later.view.observations
        assert observation.state == "drifted"
        assert observation.base.record is None


@pytest.mark.parametrize("policy,guard", [
    ("pull-only", ""),
    ("both", '[targets.unit.hooks]\nguard_push = "exit 100"'),
])
def test_no_push_capability_does_not_enforce_file_chmod(tmp_path, monkeypatch, policy, guard):
    engine = make_engine(tmp_path, monkeypatch, [
        ("unit", policy, b"same", b"same", f'chmod = "0600"\n{guard}'),
    ])
    live = tmp_path / "live/unit"
    live.chmod(0o644)

    with open_session(engine, preview=False) as session:
        observation, = session.view.observations
        assert observation.effective_policy == "pull-only"
        assert observation.state == "directly-in-sync"
        # Only the Guard's own row may explain the narrowed policy.
        assert [row.kind for row in session.view.rows] == (["guard-skip"] if guard else [])

    assert live.stat().st_mode & 0o777 == 0o644
