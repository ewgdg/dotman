import pytest

from dotman.pull_session import PullSession
from dotman.sync_base_store import FilePresent, SyncBaseStore, SyncBaseStoreError
from dotman.sync_session import SetApproval
from tests.engine.test_sync_convergence import command
from tests.engine.test_sync_session import make_engine, open_session


def test_repository_only_checkpoint_uses_final_candidate(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [('unit', 'pull-only', b'repo', b'live', '')])
    with open_session(engine, preview=False) as session:
        command(session, SetApproval, 'main:app.unit', True)
        result = session.execute().result
        assert result.units[0].status == 'converged'
        assert result.units[0].acknowledged
    with open_session(engine) as session:
        assert session.view.observations[0].base.record.payload == FilePresent(b'live')


@pytest.mark.parametrize('render', ['printf other', 'exit 7'])
def test_checkpoint_only_render_does_not_block_repository_work(tmp_path, monkeypatch, render):
    engine = make_engine(tmp_path, monkeypatch, [('unit', 'pull-only', b'repo', b'live',
        f'render = "{render}"\ncompare = {{ repo = "raw", live = "raw" }}')])
    with open_session(engine, preview=False) as session:
        command(session, SetApproval, 'main:app.unit', True)
        result = session.execute().result
        assert result.status == 'completed'
        assert result.units[0].status == 'converged'
        assert not result.units[0].acknowledged
        assert bool(result.units[0].diagnostics) == (render == 'exit 7')
    assert (tmp_path / 'repo/packages/app/unit').read_bytes() == b'live'


def test_checkpoint_persistence_warning_does_not_stop_other_units(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [(name, 'pull-only', b'repo', b'live', '') for name in ('one', 'two')])
    with open_session(engine, preview=False) as session:
        for name in ('one', 'two'):
            command(session, SetApproval, f'main:app.{name}', True)
        original = SyncBaseStore.replace
        def fail_first(self, record):
            if record.identity == b'main:app.one':
                raise SyncBaseStoreError('unavailable')
            return original(self, record)
        monkeypatch.setattr(SyncBaseStore, 'replace', fail_first)
        result = session.execute().result
        assert result.status == 'completed'
        assert [unit.status for unit in result.units] == ['converged', 'converged']
        assert [unit.acknowledged for unit in result.units] == [False, True]
        assert result.units[0].diagnostics[0].severity == 'warning'


def test_permanent_pull_freezes_forward_check_before_apply(tmp_path, monkeypatch):
    marker = tmp_path / 'renders'
    engine = make_engine(tmp_path, monkeypatch, [('unit', 'pull-only', b'repo', b'live',
        f'render = "echo render >> {marker}; cat $DOTMAN_SOURCE"\ncompare = {{ repo = "raw", live = "raw" }}')])
    session = engine.open_pull_session(engine.resolve_sync_scope(), preview=False)
    assert isinstance(session, PullSession), session
    with session:
        assert marker.read_text().splitlines() == ['render']
        (tmp_path / 'live/unit').write_bytes(b'external')
        result = session.execute().result
        assert result.units[0].acknowledged
        assert marker.read_text().splitlines() == ['render']
    with open_session(engine) as session:
        assert session.view.observations[0].base.record.payload == FilePresent(b'live')


def test_patch_capture_reuses_required_forward_validation(tmp_path, monkeypatch):
    marker = tmp_path / 'renders'
    engine = make_engine(tmp_path, monkeypatch, [('unit', 'pull-only', b'template\nold\n', b'rendered\nnew\n',
        f'render = "echo render >> {marker}; sed s/template/rendered/ $DOTMAN_SOURCE"\ncapture = "patch"\ncompare = {{repo = "render", live = "raw"}}')])
    with engine.open_pull_session(engine.resolve_sync_scope()) as session:
        assert session.view.rows[0].proposal.checkpoint_qualified
        # One Observation Render plus one required patch validation; qualification
        # and execution must consume that proof rather than run another provider.
        assert marker.read_text().splitlines() == ['render', 'render']
        assert session.execute().result.units[0].acknowledged
        assert marker.read_text().splitlines() == ['render', 'render']


def test_later_hook_failure_preserves_only_completed_checkpoint(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [
        ('one', 'pull-only', b'repo', b'live', '[targets.one.hooks]\npost_pull = "exit 9"'),
        ('two', 'pull-only', b'repo', b'live', ''),
    ])
    with engine.open_pull_session(engine.resolve_sync_scope()) as session:
        result = session.execute().result
        assert result.status == 'failed'
        assert [unit.status for unit in result.units] == ['applied', 'skipped']
        assert [unit.acknowledged for unit in result.units] == [True, False]


def test_repository_editor_forward_qualification_uses_edited_bytes(tmp_path, monkeypatch):
    from dotman.sync_session import EditProposal
    engine = make_engine(tmp_path, monkeypatch, [('unit', 'pull-only', b'repo', b'live',
        'capture = "printf lossy"\ncompare = {repo = "raw", live = "raw"}\n'
        '''editor = {run = 'printf live > "$DOTMAN_SOURCE"', io = "pipe"}''')])
    with open_session(engine, preview=False) as session:
        command(session, SetApproval, 'main:app.unit', True)
        assert not session.view.rows[0].proposal.checkpoint_qualified
        command(session, EditProposal, 'main:app.unit')
        assert session.view.rows[0].proposal.checkpoint_qualified
        assert session.execute().result.units[0].acknowledged


def test_repository_only_qualification_tracks_approved_dependency_inputs(tmp_path, monkeypatch):
    import json
    from dotman.sync_session import EditProposal
    editor = 'printf live > "$DOTMAN_EDITOR_ADDITIONAL_SOURCE_PATHS"'
    config = ('render = "cat $DOTMAN_PACKAGE_ROOT/shared"\n'
              'compare = {repo = "raw", live = "raw"}\n'
              f'editor = {{run = {json.dumps(editor)}, io = "pipe", additional_sources = ["shared"]}}')
    engine = make_engine(tmp_path, monkeypatch, [('unit', 'pull-only', b'repo', b'live', config)])
    shared = tmp_path / 'repo/packages/app/shared'
    shared.write_bytes(b'other')
    with open_session(engine, preview=False) as session:
        command(session, SetApproval, 'main:app.unit', True)
        assert not session.view.rows[0].proposal.checkpoint_qualified
        command(session, EditProposal, 'main:app.unit')
        additional = session.view.rows[-1]
        assert not session.view.rows[0].proposal.checkpoint_qualified
        command(session, SetApproval, additional.row_id, True)
        assert session.view.rows[0].proposal.checkpoint_qualified
        shared.write_bytes(b'external')
        command(session, SetApproval, additional.row_id, False)
        assert not session.view.rows[0].proposal.checkpoint_qualified
        command(session, SetApproval, additional.row_id, True)
        assert session.view.rows[0].proposal.checkpoint_qualified
        assert session.execute().result.units[0].acknowledged
    assert shared.read_bytes() == b'live'
