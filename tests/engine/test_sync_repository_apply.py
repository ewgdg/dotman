from dataclasses import replace

import pytest

from dotman import sync_repository_apply as apply
from dotman.models import HookPlan
from dotman.sync_base_store import FilePresent, Missing
from tests.engine.test_sync_publication import prepare as prepare_publication


def prepare(tmp_path, monkeypatch, names):
    metadata, publications = prepare_publication(tmp_path, monkeypatch, [
        (name, "both", b"repo", b"live", "") for name in names
    ])
    units = tuple(apply.RepositoryApplyUnit(unit.row_id, unit.identity, FilePresent(b"frozen"))
                  for unit in publications)
    return metadata, units


def test_frozen_repository_only_and_completion(tmp_path, monkeypatch):
    metadata, units = prepare(tmp_path, monkeypatch, ["first"])
    completed = []
    result = apply.execute_repository_apply(metadata, units, complete=completed.append)
    assert result.error is None
    assert completed == list(units)
    assert (tmp_path / "repo/packages/app/first").read_bytes() == b"frozen"
    assert (tmp_path / "live/first").read_bytes() == b"live"
    assert result.snapshot is None
    assert [step.status for step in result.steps] == ["ok"]


def test_no_write_stays_ordered_and_does_not_activate_hooks(tmp_path, monkeypatch):
    metadata, units = prepare(tmp_path, monkeypatch, ["first", "second"])
    marker = tmp_path / "hook"
    hook = HookPlan(hook_name="pre_pull", command=f"touch {marker}", cwd=tmp_path,
                    repo_name="main", scope_kind="repo")
    metadata = replace(metadata, repo_hooks=(("main", (hook,)),))
    completed = []
    result = apply.execute_repository_apply(
        metadata, [replace(unit, outcome=None) for unit in units], complete=completed.append)
    assert [unit.row_id for unit in completed] == [unit.row_id for unit in units]
    assert not marker.exists()
    assert result.error is None


@pytest.mark.parametrize("failure", ["callback", "post_hook", "write"])
def test_failure_preserves_prior_completion_and_skips_remaining(tmp_path, monkeypatch, failure):
    metadata, units = prepare(tmp_path, monkeypatch, ["first", "second", "third"])
    completed = []
    def complete(unit):
        if failure == "callback" and unit == units[1]:
            raise RuntimeError("ack failed")
        completed.append(unit.row_id)
    if failure == "post_hook":
        hook = HookPlan(hook_name="post_pull", command="exit 7", cwd=tmp_path,
                        repo_name="main", package_id="app", scope_kind="target", target_name="second")
        metadata = replace(metadata, packages=(replace(metadata.packages[0], hooks={"post_pull": [hook]}),))
    if failure == "write":
        (tmp_path / "repo/packages/app/second").unlink()
        (tmp_path / "repo/packages/app/second").mkdir()
    result = apply.execute_repository_apply(metadata, units, complete=complete)
    assert result.error
    assert [unit.status for unit in result.units] == [
        "ok", "ok" if failure == "post_hook" else "failed", "skipped"]
    assert completed == [unit.row_id for unit in units[:2 if failure == "post_hook" else 1]]
    assert (tmp_path / "repo/packages/app/third").read_bytes() == b"repo"


def test_delete_and_nested_hooks_ack_before_post(tmp_path, monkeypatch):
    metadata, units = prepare(tmp_path, monkeypatch, ["first"])
    log = tmp_path / "order"
    def hook(scope, phase):
        return HookPlan(hook_name=f"{phase}_pull", command=f"echo {phase}:{scope} >> {log}",
                        cwd=tmp_path, repo_name="main", scope_kind=scope,
                        package_id="app" if scope != "repo" else None,
                        target_name="first" if scope == "target" else None)
    hooks = [hook(scope, phase) for scope in ["package", "target"] for phase in ["pre", "post"]]
    metadata = replace(metadata, packages=(replace(metadata.packages[0], hooks={"all": hooks}),),
                       repo_hooks=(("main", tuple(hook("repo", phase) for phase in ["pre", "post"])),))
    def complete(unit):
        assert not (tmp_path / "repo/packages/app/first").exists()
        with log.open("a") as output:
            output.write("ack\n")
    result = apply.execute_repository_apply(metadata, [replace(units[0], outcome=Missing())], complete=complete)
    assert result.error is None
    assert log.read_text().splitlines() == [
        "pre:repo", "pre:package", "pre:target", "ack", "post:target", "post:package", "post:repo"]


def test_no_write_after_failure_is_not_completed(tmp_path, monkeypatch):
    metadata, units = prepare(tmp_path, monkeypatch, ["first", "second"])
    def fail(unit):
        raise RuntimeError("ack failed")
    result = apply.execute_repository_apply(metadata, [units[0], replace(units[1], outcome=None)], complete=fail)
    assert [unit.status for unit in result.units] == ["failed", "skipped"]


def test_prepare_freezes_pull_hooks_without_guards(tmp_path, monkeypatch):
    from dotman import sync_session
    from tests.engine.test_sync_session import make_engine, open_session

    captured = []
    original = sync_session.prepare_publication
    def capture(inputs, **kwargs):
        captured.extend(inputs)
        return original(inputs, **kwargs)
    monkeypatch.setattr(sync_session, "prepare_publication", capture)
    engine = make_engine(tmp_path, monkeypatch, [
        ("first", "both", b"repo", b"live",
         '[targets.first.hooks]\npre_pull = "true"\npost_pull = "true"\npre_push = "false"'),
    ])
    session = open_session(engine)
    metadata = apply.prepare_repository_apply(captured)
    session.abort()
    assert metadata.packages[0].operation == "pull"
    hooks = [hook for values in metadata.packages[0].hooks.values() for hook in values]
    assert {hook.hook_name for hook in hooks} == {"pre_pull", "post_pull"}


def test_repository_symlink_rejected_without_touching_referent(tmp_path, monkeypatch):
    metadata, units = prepare(tmp_path, monkeypatch, ["first"])
    path = tmp_path / "repo/packages/app/first"
    referent = tmp_path / "referent"
    path.rename(referent)
    path.symlink_to(referent)
    result = apply.execute_repository_apply(metadata, units, complete=lambda unit: None)
    assert result.units[0].status == "failed"
    assert referent.read_bytes() == b"repo"


@pytest.mark.parametrize("outcome", [FilePresent(b"frozen"), None])
def test_completion_failure_reports_separate_boundary(tmp_path, monkeypatch, outcome):
    metadata, units = prepare(tmp_path, monkeypatch, ["first"])
    def fail(unit):
        raise RuntimeError("ack failed")
    result = apply.execute_repository_apply(
        metadata, [replace(units[0], outcome=outcome)], complete=fail)
    assert result.units[0].status == "failed"
    expected = [] if outcome is None else [("target", "update", "ok")]
    assert [(step.step.kind, step.step.action, step.status) for step in result.steps] == [
        *expected, ("unit-completion", "complete", "failed")]
    assert (tmp_path / "repo/packages/app/first").read_bytes() == (
        b"repo" if outcome is None else b"frozen")
