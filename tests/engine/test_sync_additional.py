import json

from dotman.sync_base_store import FilePresent
from dotman.sync_session import (
    AdditionalRow, BatchSetApproval, EditProposal, PrepareProposalReview,
    PrepareSourceReview, SetApproval,
)
from tests.engine.test_sync_session import make_engine, open_session


def command(session, kind, row=None, **kwargs):
    args = (session.view.session_id, session.view.revision)
    return session.dispatch(kind(*args, row.row_id, **kwargs) if row else kind(*args, **kwargs))


def setup(tmp_path, monkeypatch, *, preview=True):
    editor = 'value=$(cat "$DOTMAN_EDITOR_ADDITIONAL_SOURCE_PATHS"); if test "$value" = original; then printf candidate; else printf original; fi > "$DOTMAN_EDITOR_ADDITIONAL_SOURCE_PATHS"'
    config = ('render = "cat $DOTMAN_PACKAGE_ROOT/shared"\n'
              f'editor = {{ run = {json.dumps(editor)}, io = "pipe", additional_sources = ["shared"] }}')
    engine = make_engine(tmp_path, monkeypatch, [
        ('a', 'push-only', b'repo', b'live', config),
        ('b', 'push-only', b'repo', b'live', config),
    ])
    shared = tmp_path / 'repo/packages/app/shared'
    shared.write_bytes(b'original')
    session = open_session(engine, preview=preview)
    command(session, EditProposal, session.view.rows[0])
    return session, shared


def test_canonical_approval_aligns_eager_and_lazy_references(tmp_path, monkeypatch):
    session, shared = setup(tmp_path, monkeypatch)
    a, b, additional = session.view.rows
    assert isinstance(additional, AdditionalRow)
    assert additional.path.as_posix() == 'packages/app/shared'
    assert additional.references == (a.row_id, b.row_id)
    command(session, SetApproval, a, approved=True)
    command(session, PrepareProposalReview, b)
    command(session, SetApproval, additional, approved=True)
    a, b, additional = session.view.rows
    assert a.approved and a.proposal.live == FilePresent(b'candidate')
    assert b.proposal is None and not b.approved
    reviewed = command(session, PrepareProposalReview, b)
    assert reviewed.result.proposal.live == FilePresent(b'candidate')
    command(session, SetApproval, additional, approved=False)
    a, b, additional = session.view.rows
    assert a.proposal.live == FilePresent(b'original')
    assert b.proposal is None
    assert command(session, PrepareSourceReview, additional).result.change.candidate == b'candidate'
    assert shared.read_bytes() == b'original'


def test_batch_uses_final_additional_set_and_unapproval_is_lazy(tmp_path, monkeypatch):
    session, _ = setup(tmp_path, monkeypatch)
    result = command(session, BatchSetApproval, approved=True)
    assert all(row.approved for row in result.view.rows)
    assert all(row.proposal.live == FilePresent(b'candidate') for row in result.view.rows[:2])
    result = command(session, BatchSetApproval, approved=False)
    assert all(not row.approved for row in result.view.rows)
    assert all(row.proposal is None for row in result.view.rows[:2])


def test_disappearance_retains_approval_and_shared_editor_candidate(tmp_path, monkeypatch):
    session, _ = setup(tmp_path, monkeypatch)
    command(session, SetApproval, session.view.rows[-1], approved=True)
    command(session, EditProposal, session.view.rows[1])
    assert len(session.view.rows) == 2
    command(session, EditProposal, session.view.rows[0])
    assert len(session.view.rows) == 3
    assert session.view.rows[-1].approved
    assert session.view.rows[-1].references == tuple(row.row_id for row in session.view.rows[:2])


def test_additional_executes_without_approved_proposals_once(tmp_path, monkeypatch):
    session, shared = setup(tmp_path, monkeypatch, preview=False)
    command(session, SetApproval, session.view.rows[-1], approved=True)
    result = session.execute().result
    assert shared.read_bytes() == b'candidate'
    assert [unit.status for unit in result.units] == ['pending', 'pending']
    assert [change.status for change in result.additional_changes] == ['applied']
    assert [step.kind for step in result.steps] == ['additional-source']


def test_approved_additional_failure_stops_later_effects_with_own_result(tmp_path, monkeypatch):
    session, shared = setup(tmp_path, monkeypatch, preview=False)
    command(session, BatchSetApproval, approved=True)
    shared.rename(shared.with_name('saved-shared'))
    shared.mkdir()
    result = session.execute().result
    assert result.status == 'failed'
    assert result.additional_changes[0].status == 'execution-failed'
    assert result.additional_changes[0].diagnostics[0].code == 'additional-source-failed'
    assert [unit.status for unit in result.units] == ['skipped', 'skipped']
    assert all((tmp_path / 'live' / name).read_bytes() == b'live' for name in ('a', 'b'))


def test_batch_materializes_once_and_reference_failure_is_independent(tmp_path, monkeypatch):
    log = tmp_path / 'renders'
    marker = tmp_path / 'fail-a'
    configs = []
    for name in ('a', 'b'):
        render = f'echo {name} >> {log}; '
        if name == 'a':
            render += f'test ! -e {marker} || exit 7; '
        render += 'cat "$DOTMAN_PACKAGE_ROOT/shared"'
        configs.append((name, 'push-only', b'repo', b'live',
            f'render = {json.dumps(render)}\n'
            'editor = { run = "printf candidate > \\"$DOTMAN_EDITOR_ADDITIONAL_SOURCE_PATHS\\"", io = "pipe", additional_sources = ["shared"] }'))
    engine = make_engine(tmp_path, monkeypatch, configs)
    (tmp_path / 'repo/packages/app/shared').write_bytes(b'original')
    session = open_session(engine)
    command(session, EditProposal, session.view.rows[0])
    before = len(log.read_text().splitlines())
    marker.touch()
    result = command(session, BatchSetApproval, approved=True)
    assert log.read_text().splitlines()[before:] == ['a', 'b']
    a, b, source = result.view.rows
    assert not a.approved and a.proposal is None and a.diagnostics
    assert b.approved and b.proposal.live == FilePresent(b'candidate')
    assert source.approved
    before = log.read_bytes()
    command(session, BatchSetApproval, approved=False)
    assert log.read_bytes() == before


def test_capture_uses_only_authorized_additional_inputs(tmp_path, monkeypatch):
    config = ('capture = "cat $DOTMAN_PACKAGE_ROOT/shared"\n'
              'compare = { repo = "raw", live = "raw" }\n'
              'editor = { run = "printf candidate > \\"$DOTMAN_EDITOR_ADDITIONAL_SOURCE_PATHS\\"", io = "pipe", additional_sources = ["shared"] }')
    engine = make_engine(tmp_path, monkeypatch, [
        ('a', 'pull-only', b'repo', b'live', config),
        ('b', 'pull-only', b'repo', b'live', config),
    ])
    shared = tmp_path / 'repo/packages/app/shared'
    shared.write_bytes(b'original')
    session = open_session(engine)
    command(session, EditProposal, session.view.rows[0])
    command(session, SetApproval, session.view.rows[1], approved=True)
    assert session.view.rows[1].proposal.repository == FilePresent(b'original')
    shared.write_bytes(b'external')
    command(session, SetApproval, session.view.rows[-1], approved=True)
    assert session.view.rows[1].proposal.repository == FilePresent(b'candidate')
    command(session, SetApproval, session.view.rows[-1], approved=False)
    assert session.view.rows[1].proposal.repository == FilePresent(b'original')


def test_frozen_publication_does_not_rerender_after_source_apply(tmp_path, monkeypatch):
    session, shared = setup(tmp_path, monkeypatch, preview=False)
    command(session, BatchSetApproval, approved=True)
    shared.write_bytes(b'external')
    result = session.execute().result
    assert result.status == 'completed'
    assert [unit.status for unit in result.units] == ['converged', 'converged']
    assert shared.read_bytes() == b'candidate'
    assert all((tmp_path / 'live' / name).read_bytes() == b'candidate' for name in ('a', 'b'))
    assert sum(step.kind == 'additional-source' for step in result.steps) == 1


def test_additional_only_does_not_activate_hooks_or_snapshot(tmp_path, monkeypatch):
    hook_log = tmp_path / 'hooks'
    engine = make_engine(tmp_path, monkeypatch, [
        ('a', 'push-only', b'repo', b'live',
         'editor = { run = "printf candidate > \\"$DOTMAN_EDITOR_ADDITIONAL_SOURCE_PATHS\\"", io = "pipe", additional_sources = ["shared"] }\n'
         '[targets.a.hooks]\n'
         f'pre_pull = {json.dumps(f"echo pull >> {hook_log}")}\n'
         f'pre_push = {json.dumps(f"echo push >> {hook_log}")}'),
    ])
    shared = tmp_path / 'repo/packages/app/shared'
    shared.write_bytes(b'original')
    session = open_session(engine, preview=False)
    command(session, EditProposal, session.view.rows[0])
    command(session, SetApproval, session.view.rows[-1], approved=True)
    result = session.execute().result
    assert result.status == 'completed'
    assert shared.read_bytes() == b'candidate'
    assert not hook_log.exists()
    assert [step.kind for step in result.steps] == ['additional-source']


def test_unapproved_additional_does_not_gate_eligible_convergence(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [
        ('a', 'pull-only', b'repo', b'live',
         'editor = { run = "printf candidate > \\"$DOTMAN_EDITOR_ADDITIONAL_SOURCE_PATHS\\"", io = "pipe", additional_sources = ["shared"] }'),
    ])
    shared = tmp_path / 'repo/packages/app/shared'
    shared.write_bytes(b'original')
    session = open_session(engine, preview=False)
    command(session, EditProposal, session.view.rows[0])
    command(session, SetApproval, session.view.rows[0], approved=True)
    result = session.execute().result
    assert result.units[0].status == 'converged'
    assert result.additional_changes[0].status == 'pending'
    assert shared.read_bytes() == b'original'


def test_additional_apply_is_sorted_atomic_and_fail_fast(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [
        ('unit', 'push-only', b'repo', b'live',
         'editor = { run = "IFS=:; for source in $DOTMAN_EDITOR_ADDITIONAL_SOURCE_PATHS; do printf candidate > \\"$source\\"; done", io = "pipe", additional_sources = ["z", "a", "a"] }'),
    ])
    package = tmp_path / 'repo/packages/app'
    for name in ('a', 'z'):
        (package / name).write_bytes(b'original')
    session = open_session(engine, preview=False)
    command(session, EditProposal, session.view.rows[0])
    assert [row.path.name for row in session.view.rows[1:]] == ['a', 'z']
    for row in session.view.rows[1:]:
        command(session, SetApproval, row, approved=True)
    (package / 'z').rename(package / 'z-before')
    (package / 'z').mkdir()
    result = session.execute().result
    assert [change.status for change in result.additional_changes] == ['applied', 'execution-failed']
    assert (package / 'a').read_bytes() == b'candidate'
    assert (package / 'z-before').read_bytes() == b'original'
    assert result.units[0].status == 'pending'


def test_additional_approval_executes_when_editor_has_no_valid_proposal(tmp_path, monkeypatch):
    marker = tmp_path / 'render-fails'
    render = f'test ! -e {marker} && cat "$DOTMAN_SOURCE"'
    engine = make_engine(tmp_path, monkeypatch, [
        ('a', 'push-only', b'repo', b'live',
         f'render = {json.dumps(render)}\n'
         'editor = { run = "printf edited > \\"$DOTMAN_SOURCE\\"; printf candidate > \\"$DOTMAN_EDITOR_ADDITIONAL_SOURCE_PATHS\\"", io = "pipe", additional_sources = ["shared"] }'),
    ])
    shared = tmp_path / 'repo/packages/app/shared'
    shared.write_bytes(b'original')
    session = open_session(engine, preview=False)
    marker.touch()
    result = command(session, EditProposal, session.view.rows[0])
    assert result.result.status == 'materialization-failed'
    assert result.view.rows[0].proposal is None
    source = result.view.rows[-1]
    command(session, SetApproval, source, approved=True)
    result = session.execute().result
    assert result.additional_changes[0].status == 'applied'
    assert shared.read_bytes() == b'candidate'
    assert (tmp_path / 'repo/packages/app/a').read_bytes() == b'repo'
    assert (tmp_path / 'live/a').read_bytes() == b'live'


def test_unapproved_candidate_edit_preserves_approved_capture_and_refreshes_references(tmp_path, monkeypatch):
    marker = tmp_path / 'capture-ran'
    capture = f'test ! -e {marker} || exit 7; touch {marker}; cat "$DOTMAN_PACKAGE_ROOT/shared"'
    config = (f'capture = {json.dumps(capture)}\n'
              'compare = { repo = "raw", live = "raw" }\n'
              'editor = { run = "printf candidate > \\"$DOTMAN_EDITOR_ADDITIONAL_SOURCE_PATHS\\"", io = "pipe", additional_sources = ["shared"] }')
    engine = make_engine(tmp_path, monkeypatch, [
        ('a', 'pull-only', b'repo', b'live', config),
        ('b', 'pull-only', b'repo', b'live', config),
    ])
    (tmp_path / 'repo/packages/app/shared').write_bytes(b'original')
    session = open_session(engine)
    command(session, SetApproval, session.view.rows[1], approved=True)
    before = session.view.rows[1].proposal
    command(session, EditProposal, session.view.rows[0])
    b, source = session.view.rows[1:]
    assert b.approved and not b.diagnostics
    assert b.proposal.repository == before.repository == FilePresent(b'original')
    assert b.proposal.generation == before.generation
    assert b.additional_changes == b.proposal.additional_changes == (source.change,)
    assert not source.approved


def test_identical_editor_save_retains_render_for_later_intent_materialization(tmp_path, monkeypatch):
    from dotman.sync_session import SetResolutionIntent
    marker = tmp_path / 'render-ran'
    render = f'test ! -e {marker} || exit 7; touch {marker}; cat "$DOTMAN_SOURCE"'
    engine = make_engine(tmp_path, monkeypatch, [
        ('a', 'push-only', b'repo', b'live',
         f'render = {json.dumps(render)}\neditor = {{ run = "true", io = "pipe" }}'),
    ])
    session = open_session(engine)
    command(session, SetApproval, session.view.rows[0], approved=True)
    command(session, EditProposal, session.view.rows[0])
    assert session.view.rows[0].approved
    result = command(session, SetResolutionIntent, session.view.rows[0], intent='use-repository')
    assert result.view.rows[0].approved
    assert not result.view.rows[0].diagnostics
    assert result.view.rows[0].proposal.live == FilePresent(b'repo')


def test_batch_unapproval_preserves_unresolved_capture_diagnostic(tmp_path, monkeypatch):
    from dotman.sync_session import Preview
    engine = make_engine(tmp_path, monkeypatch, [
        ('a', 'pull-only', b'repo', b'live',
         'capture = "exit 7"\ncompare = { repo = "raw", live = "raw" }'),
    ])
    session = open_session(engine)
    failed = command(session, PrepareProposalReview, session.view.rows[0])
    assert failed.result.diagnostics[0].code == 'capture-failed'
    result = command(session, BatchSetApproval, approved=False)
    assert result.view.rows[0].diagnostics == failed.result.diagnostics
    assert result.view.rows[0].proposal is None
    assert command(session, Preview).result.status == 'failed'
