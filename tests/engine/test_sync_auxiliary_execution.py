from dataclasses import replace

import pytest

from dotman import sync_publication as publication, sync_repository_apply as apply
from dotman.models import HookPlan, SnapshotConfig
from tests.engine.test_sync_publication import prepare


@pytest.mark.parametrize("operation", ["push", "pull"])
@pytest.mark.parametrize("probe,override", [(True, False), (False, False), (False, True)])
def test_auxiliary_nested_hooks_without_payload(tmp_path, monkeypatch, operation, probe, override):
    metadata, units = prepare(tmp_path, monkeypatch, [
        ("first", "both", b"repo", b"live", ""),
    ])
    log = tmp_path / "order"
    def hook(scope, phase, noop):
        label = f"{phase}:{scope}:{noop}"
        return HookPlan(
            hook_name=f"{phase}_{operation}", command=f"echo {label} >> {log}",
            cwd=tmp_path, repo_name="main", scope_kind=scope,
            package_id="app" if scope != "repo" else None,
            target_name="first" if scope == "target" else None, run_noop=noop,
        )
    hooks = [hook(scope, phase, noop) for scope in ("package", "target")
             for phase in ("pre", "post") for noop in (False, True)]
    metadata = replace(metadata, packages=(replace(metadata.packages[0], hooks={"all": hooks}),),
        repo_hooks=(("main", tuple(hook("repo", phase, noop)
            for phase in ("pre", "post") for noop in (False, True))),))
    identity = units[0].identity
    activation = publication.HookActivation(identity.canonical, identity if probe else None)
    completed = []
    kwargs = dict(auxiliary=[activation, activation], complete=completed.append, run_noop=override)
    if operation == "push":
        result = publication.execute_publication(metadata, [], snapshot_config=SnapshotConfig(
            True, tmp_path / "snapshots", 10), **kwargs)
    else:
        result = apply.execute_repository_apply(metadata, [], **kwargs)
    assert result.error is None
    eligible = (False, True) if probe or override else (True,)
    assert log.read_text().splitlines() == [
        f"{phase}:{scope}:{noop}"
        for phase, scope in [("pre", "repo"), ("pre", "package"), ("pre", "target"),
                             ("post", "target"), ("post", "package"), ("post", "repo")]
        for noop in eligible]
    assert result.units == ()
    assert result.snapshot is None
    assert completed == []
    assert (tmp_path / "live/first").read_bytes() == b"live"
    assert (tmp_path / "repo/packages/app/first").read_bytes() == b"repo"

@pytest.mark.parametrize("operation", ["push", "pull"])
@pytest.mark.parametrize("scope", ["main", "main:app"])
def test_hook_only_empty_package_scope(tmp_path, monkeypatch, operation, scope):
    metadata, _ = prepare(tmp_path, monkeypatch, [("first", "both", b"repo", b"live", "")])
    log = tmp_path / "order"
    def hook(kind, name, noop=True):
        return HookPlan(hook_name=name, command=f"echo {kind}:{name} >> {log}",
            cwd=tmp_path, repo_name="main", package_id="app" if kind == "package" else None,
            scope_kind=kind, run_noop=noop)
    metadata = replace(metadata,
        packages=(replace(metadata.packages[0], target_plans=[], hooks={"all": [
            hook("package", f"pre_{operation}"), hook("package", f"guard_{operation}"),
            hook("package", f"post_{operation}", False)]}),),
        repo_hooks=(("main", (hook("repo", f"pre_{operation}"),
                             hook("repo", f"guard_{operation}"))),))
    kwargs = dict(auxiliary=[publication.HookActivation(scope)], complete=lambda _: pytest.fail("completion"))
    if operation == "push":
        result = publication.execute_publication(metadata, [], snapshot_config=SnapshotConfig(
            True, tmp_path / "snapshots", 10), **kwargs)
    else:
        result = apply.execute_repository_apply(metadata, [], **kwargs)
    assert result.error is None
    assert log.read_text().splitlines() == [f"repo:pre_{operation}"] + (
        [f"package:pre_{operation}"] if scope == "main:app" else [])
    assert result.units == ()
    assert result.snapshot is None


@pytest.mark.parametrize("operation", ["push", "pull"])
def test_probe_coalesces_with_file_work_and_snapshot_excludes_probe(tmp_path, monkeypatch, operation):
    metadata, units = prepare(tmp_path, monkeypatch, [
        ("first", "both", b"repo", b"live", ""),
        ("second", "both", b"repo", b"live", ""),
    ])
    package = metadata.packages[0]
    probe = replace(package.target_plans[1], target_kind="probe",
                    live_path=tmp_path / "nonexistent-probe", repo_path=tmp_path / "nonexistent-source")
    log = tmp_path / "order"
    hook = HookPlan(hook_name=f"pre_{operation}", command=f"echo repo >> {log}",
                    cwd=tmp_path, repo_name="main", scope_kind="repo")
    metadata = replace(metadata, packages=(replace(package, target_plans=[package.target_plans[0], probe]),),
                       repo_hooks=(("main", (hook,)),))
    completed = []
    kwargs = dict(auxiliary=[publication.HookActivation(units[1].identity.canonical, units[1].identity)],
                  complete=completed.append)
    if operation == "push":
        result = publication.execute_publication(metadata, units[:1], snapshot_config=SnapshotConfig(
            True, tmp_path / "snapshots", 10), **kwargs)
        assert len(result.snapshot.entries) == 1
        assert result.snapshot.entries[0].live_path == package.target_plans[0].live_path
    else:
        from dotman.sync_base_store import FilePresent
        result = apply.execute_repository_apply(metadata, [
            apply.RepositoryApplyUnit(units[0].row_id, units[0].identity, FilePresent(b"frozen"))], **kwargs)
    assert result.error is None
    assert log.read_text().splitlines() == ["repo"]
    assert len(completed) == 1
    assert len(result.units) == 1
    assert not probe.live_path.exists()
    assert not probe.repo_path.exists()


@pytest.mark.parametrize("prepare_metadata", [publication.prepare_publication, apply.prepare_repository_apply])
def test_prepare_retains_probe_and_empty_package(tmp_path, monkeypatch, prepare_metadata):
    from dotman import sync_session

    captured = []
    original = sync_session.prepare_publication
    def capture(inputs, **kwargs):
        captured.extend(inputs)
        return original(inputs, **kwargs)
    monkeypatch.setattr(sync_session, "prepare_publication", capture)
    prepare(tmp_path, monkeypatch, [("first", "both", b"repo", b"live", "")])
    item = captured[0]
    probe = replace(item.target_metadata[0], probe_command="true")
    metadata = prepare_metadata([replace(item, target_metadata=[probe])])
    assert metadata.packages[0].target_plans[0].target_kind == "probe"
    metadata = prepare_metadata([replace(item, target_metadata=[])])
    assert len(metadata.packages) == 1
    assert metadata.packages[0].target_plans == []
    assert metadata.repo_hooks[0][0] == "main"
