from dataclasses import replace

import pytest

from dotman.engine import DotmanEngine
from dotman.execution import build_execution_session, execute_session as execute
from dotman.models import HookPlan
from dotman.sync_base_store import DirectoryChildPresent, Missing, SyncBaseStore, SyncBaseStoreError
from tests.helpers import write_shared_stack_repo, write_single_repo_config


@pytest.fixture
def push_plan(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    root = tmp_path / "repo"
    write_shared_stack_repo(root)
    engine = DotmanEngine.from_config_path(write_single_repo_config(tmp_path, repo_name="fixture", repo_path=root))
    return engine, root


def execute_session(session):
    return execute(session, stream_output=False)


def _plan(engine):
    plans = engine.plan_push_query("fixture:shared@basic")
    return plans, plans[0].target_plans[0]


def _record(checkpoint):
    with SyncBaseStore.open(checkpoint.manager_root, checkpoint.state_key, read_only=True, create=False) as store:
        return store.read(checkpoint.frozen.unit.identity_bytes)


def test_push_checkpoints_frozen_dirty_repository_without_git(push_plan):
    engine, root = push_plan
    plans, target = _plan(engine)
    expected = target.repo_path.read_bytes()
    target.repo_path.write_bytes(b"changed after planning\n")
    result = execute_session(build_execution_session(plans, operation="push"))
    assert result.status == "ok"
    assert not (root / ".git").exists()
    assert _record(target.push_checkpoints[0]).payload.content == expected
    assert target.live_path.read_bytes() == expected
    completed = [step for package in result.packages for step in package.steps if step.step.kind == "checkpoint"]
    assert len(completed) == 1
    assert completed[0].converged and completed[0].acknowledged


@pytest.mark.parametrize('repository,live,expected', [
    (b'old\ncontext\nkeep\n', b'dirty\ncontext\nkeep\n', b'old\ncontext\nkeep\n'),
    (b'repo-edit\ncontext\nkeep\n', b'dirty\ncontext\nlive-edit\n', b'repo-edit\ncontext\nlive-edit\n'),
])
def test_dirty_push_establishes_starting_point_for_revert_and_independent_edits(
    tmp_path, monkeypatch, repository, live, expected,
):
    from dotman.sync_session import PrepareProposalReview, SetApproval
    from tests.engine.test_sync_convergence import command
    from tests.engine.test_sync_session import make_engine, open_session

    engine = make_engine(tmp_path, monkeypatch, [('unit', 'both', b'old\ncontext\nkeep\n', b'old\ncontext\nkeep\n', '')])
    source = tmp_path / 'repo/packages/app/unit'
    destination = tmp_path / 'live/unit'
    source.write_bytes(b'dirty\ncontext\nkeep\n')
    plans = engine.plan_push_query('main:app@default')
    assert execute_session(build_execution_session(plans, operation='push')).status == 'ok'
    source.write_bytes(repository)
    destination.write_bytes(live)
    with open_session(engine, preview=False) as session:
        assert session.view.observations[0].base.record.payload.content == b'dirty\ncontext\nkeep\n'
        command(session, PrepareProposalReview, 'main:app.unit')
        assert session.view.rows[0].proposal.repository.content == expected
        command(session, SetApproval, 'main:app.unit', True)
        assert session.execute().result.units[0].acknowledged
    assert source.read_bytes() == destination.read_bytes() == expected


def test_push_reuses_successful_required_render_during_execution(tmp_path, monkeypatch):
    from tests.engine.test_sync_session import make_engine

    marker = tmp_path / 'renders'
    engine = make_engine(tmp_path, monkeypatch, [('unit', 'both', b'repo', b'live',
        f'render = "echo render >> {marker}; tr a-z A-Z < $DOTMAN_SOURCE"')])
    plans = engine.plan_push_query('main:app@default')
    (tmp_path / 'repo/packages/app/unit').write_bytes(b'changed')
    assert execute_session(build_execution_session(plans, operation='push')).status == 'ok'
    assert (tmp_path / 'live/unit').read_bytes() == b'REPO'
    assert marker.read_text().splitlines() == ['render']
    assert _record(plans[0].target_plans[0].push_checkpoints[0]).payload.content == b'repo'


def test_direct_agreement_acknowledges_only_during_execution(push_plan):
    engine, _ = push_plan
    _, target = _plan(engine)
    target.live_path.parent.mkdir(parents=True)
    target.live_path.write_bytes(target.repo_path.read_bytes())
    plans, target = _plan(engine)
    assert target.action == "noop"
    checkpoint = target.push_checkpoints[0]
    assert not checkpoint.manager_root.exists()
    session = build_execution_session(plans, operation="push")
    assert not checkpoint.manager_root.exists()
    result = execute_session(session)
    assert result.status == "ok"
    assert _record(checkpoint).payload.content == target.repo_path.read_bytes()


def test_direct_checkpoint_does_not_start_live_snapshot_before_later_publication(tmp_path, monkeypatch):
    from dotman.models import SnapshotConfig
    from dotman.operation_runner import SyncStepStarted, run_sync_operation
    from dotman.snapshot import list_snapshots
    from tests.engine.test_sync_session import make_engine

    engine = make_engine(tmp_path, monkeypatch, [
        ('one', 'both', b'same', b'same', ''),
        ('two', 'both', b'repo', b'live', ''),
    ])
    snapshots = SnapshotConfig(enabled=True, path=tmp_path / 'snapshots', max_generations=5)
    observations = []

    def observe(event):
        if isinstance(event, SyncStepStarted):
            observations.append((event.step.kind, bool(list_snapshots(snapshots.path))))

    result = run_sync_operation(
        operation='push', plans=engine.plan_push_query('main:app@default'),
        stream_output=False, snapshot_config=snapshots, event_sink=observe,
    )
    assert result.status == 'ok'
    assert observations == [('checkpoint', False), ('target', True), ('checkpoint', True)]


def test_checkpoint_failure_warns_but_converges_and_continues(push_plan, monkeypatch, capsys):
    engine, _ = push_plan
    plans, target = _plan(engine)
    original = SyncBaseStore.replace
    failures = []

    def fail_replace(self, record):
        failures.append(record)
        raise SyncBaseStoreError("checkpoint disk full")

    monkeypatch.setattr(SyncBaseStore, "replace", fail_replace)
    result = execute_session(build_execution_session(plans, operation="push"))
    assert result.status == "ok" and result.exit_code == 0
    step = next(step for package in result.packages for step in package.steps if step.step.kind == "checkpoint")
    assert step.converged and not step.acknowledged
    assert step.to_dict()["checkpoint_warning"] == "checkpoint disk full"
    assert "warning:" in capsys.readouterr().err
    assert target.live_path.read_bytes() == target.repo_path.read_bytes()
    monkeypatch.setattr(SyncBaseStore, "replace", original)


def test_required_chmod_failure_does_not_acknowledge(push_plan, monkeypatch):
    import dotman.execution as execution
    engine, _ = push_plan
    plans, target = _plan(engine)
    target = replace(target, chmod="0600")
    plan = replace(plans[0], target_plans=[target])

    chmod = execution.os.chmod

    def fail_mode(path, mode, **kwargs):
        if path == target.live_path:
            raise OSError("required chmod failed")
        return chmod(path, mode, **kwargs)

    monkeypatch.setattr(execution.os, "chmod", fail_mode)
    result = execute_session(build_execution_session([plan], operation="push"))
    assert result.status == "failed"
    assert target.live_path.read_bytes() == target.repo_path.read_bytes()
    assert not target.push_checkpoints[0].manager_root.exists()


def test_disappearing_live_file_before_required_chmod_does_not_acknowledge(push_plan):
    from dotman.operation_runner import SyncStepStarted, run_sync_operation

    engine, _ = push_plan
    plans, target = _plan(engine)
    target = replace(target, chmod='0600')
    plan = replace(plans[0], target_plans=[target])

    def remove_before_mode(event):
        if isinstance(event, SyncStepStarted) and event.step.kind == 'chmod':
            target.live_path.unlink()

    result = run_sync_operation(operation='push', plans=[plan], stream_output=False,
                                event_sink=remove_before_mode)
    assert result.status == 'failed'
    assert not target.push_checkpoints[0].manager_root.exists()


def test_later_post_hook_failure_preserves_checkpoint(push_plan):
    engine, root = push_plan
    plans, target = _plan(engine)
    hook = HookPlan(package_id="shared", hook_name="post_push", command="exit 9", cwd=root)
    plan = replace(plans[0], hooks={"post_push": [hook]})
    result = execute_session(build_execution_session([plan], operation="push"))
    assert result.status == "failed"
    assert _record(target.push_checkpoints[0]).payload.content == target.repo_path.read_bytes()


def test_failed_publication_render_is_not_deferred_to_execution(tmp_path, monkeypatch):
    from tests.engine.test_sync_session import make_engine

    marker = tmp_path / 'renders'
    engine = make_engine(tmp_path, monkeypatch, [('unit', 'both', b'repo', None,
        f'render = "echo render >> {marker}; exit 9"')])
    with pytest.raises(ValueError, match='command projection failed'):
        engine.plan_push_query('main:app@default')
    assert marker.read_text().splitlines() == ['render']
    assert not (tmp_path / 'live/unit').exists()


def test_directory_children_checkpoint_frozen_executable_and_missing(push_plan):
    engine, root = push_plan
    package = root / "packages/shared"
    (package / "package.toml").write_text('id = "shared"\n[targets.shared]\nsource = "files"\npath = "~/.config/shared"\n')
    engine = DotmanEngine.from_config_path(engine.config.config_path)
    source = package / "files/shared.conf"
    source.chmod(0o755)
    plans, target = _plan(engine)
    source.chmod(0o644)
    result = execute_session(build_execution_session(plans, operation="push"))
    assert result.status == "ok"
    checkpoint = target.push_checkpoints[0]
    assert _record(checkpoint).payload == DirectoryChildPresent(b"shared\n", True)
    assert (target.live_path / "shared.conf").stat().st_mode & 0o111
    source.unlink()
    plans, target = _plan(engine)
    result = execute_session(build_execution_session(plans, operation="push"))
    assert result.status == "ok"
    assert _record(target.push_checkpoints[0]).payload == Missing()
