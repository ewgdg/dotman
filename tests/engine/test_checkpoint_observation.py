"""Checkpoint Observation is filesystem evidence, independent of Git history."""

import pytest

from dotman.sync_base_store import FilePresent, Missing
from tests.engine.test_sync_session import make_engine, open_session


def test_direct_agreement_acknowledges_dirty_repository_without_extra_render(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [('file', 'both', b'committed', b'dirty', 'render = "exit 91"')])
    (tmp_path / 'repo/packages/app/file').write_bytes(b'dirty')
    with open_session(engine, preview=False) as session:
        observation, = session.view.observations
        assert observation.state == 'directly-in-sync'
        assert observation.base.acknowledged
        assert not observation.diagnostics
    with open_session(engine) as session:
        observation, = session.view.observations
        assert observation.base.record.payload == FilePresent(b'dirty')


def test_direct_missing_is_a_checkpoint_without_git_repository(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [('file', 'both', None, None, '')])
    (tmp_path / 'repo/.git').rename(tmp_path / 'removed-git')
    with open_session(engine, preview=False) as session:
        observation, = session.view.observations
        assert observation.base.acknowledged
    with open_session(engine) as session:
        observation, = session.view.observations
        assert observation.base.record.payload == Missing()


@pytest.mark.parametrize('preview', [True, False])
def test_store_open_failure_warns_without_changing_observation(tmp_path, monkeypatch, preview):
    engine = make_engine(tmp_path, monkeypatch, [('file', 'both', b'repo', b'live', '')])
    # Establish storage first so preview must validate it too.
    with open_session(engine, preview=False):
        pass
    from dotman.sync_base_store import SyncBaseStore

    def denied(*args, **kwargs):
        raise PermissionError('checkpoint denied')

    monkeypatch.setattr(SyncBaseStore, 'open', denied)
    with open_session(engine, preview=preview) as session:
        observation, = session.view.observations
        assert observation.state == 'drifted'
        assert observation.base.status == 'unavailable'
        assert observation.diagnostics
        assert all(diagnostic.severity == 'warning' for diagnostic in observation.diagnostics)
        assert session.view.rows[0].allowed_commands


def test_preview_absent_checkpoint_store_does_not_create_files(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [('file', 'both', b'same', b'same', '')])
    state = tmp_path / 'state'
    before = {path.relative_to(state): path.read_bytes() for path in state.rglob('*') if path.is_file()}
    with open_session(engine) as session:
        observation, = session.view.observations
        assert not observation.base.acknowledged
    after = {path.relative_to(state): path.read_bytes() for path in state.rglob('*') if path.is_file()}
    assert after == before
