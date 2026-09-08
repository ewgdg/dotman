from dataclasses import replace

import pytest

from dotman import sync_publication as publication
from dotman.models import HookPlan, SnapshotConfig
from dotman.sync_session import PublicationEffect
from tests.engine.test_sync_session import make_engine, open_session


def prepare(tmp_path, monkeypatch, targets):
    engine = make_engine(tmp_path, monkeypatch, targets)
    session = open_session(engine)
    metadata = session._publication_metadata
    units = tuple(
        publication.PublicationUnit(
            row.row_id, row.observation.identity,
            (PublicationEffect("write", row.observation.live_path, b"frozen"),),
        )
        for row in session.view.rows
    )
    session.abort()
    return metadata, units


def execute(tmp_path, metadata, units):
    return publication.execute_publication(
        metadata, units,
        snapshot_config=SnapshotConfig(True, tmp_path / "snapshots", 10),
    )


def test_frozen_bytes_and_snapshot(tmp_path, monkeypatch):
    metadata, units = prepare(tmp_path, monkeypatch, [
        ("unit", "push-only", b"repo", b"live", ""),
    ])
    (tmp_path / "repo/packages/app/unit").write_bytes(b"later source")
    result = execute(tmp_path, metadata, units)
    assert result.error is None
    assert [unit.status for unit in result.units] == ["ok"]
    assert (tmp_path / "live/unit").read_bytes() == b"frozen"
    assert result.snapshot.status == "applied"
    assert (result.snapshot.root / result.snapshot.entries[0].content_path).read_bytes() == b"live"
    assert [step.status for step in result.steps] == ["ok"]


def test_no_effects_do_not_run_hooks_or_snapshot(tmp_path, monkeypatch):
    marker = tmp_path / "hook"
    metadata, units = prepare(tmp_path, monkeypatch, [
        ("unit", "push-only", b"repo", b"live",
         f'[targets.unit.hooks]\npre_push = "touch {marker}"'),
    ])
    result = execute(tmp_path, metadata, [replace(units[0], effects=())])
    assert result.error is None
    assert result.units[0].status == "ok"
    assert result.snapshot is None
    assert not marker.exists()
    assert not (tmp_path / "snapshots").exists()


def test_post_hook_failure_preserves_completed_unit_and_stops_next(tmp_path, monkeypatch):
    metadata, units = prepare(tmp_path, monkeypatch, [
        ("first", "push-only", b"repo", b"live",
         '[targets.first.hooks]\npost_push = "exit 7"'),
        ("second", "push-only", b"repo", b"live", ""),
    ])
    result = execute(tmp_path, metadata, units)
    assert result.error
    assert [unit.status for unit in result.units] == ["ok", "skipped"]
    assert [step.status for step in result.steps] == ["ok", "failed"]
    assert (tmp_path / "live/first").read_bytes() == b"frozen"
    assert (tmp_path / "live/second").read_bytes() == b"live"
    assert result.snapshot.status == "failed"


def test_failed_chmod_retains_content_but_not_completion(tmp_path, monkeypatch):
    metadata, units = prepare(tmp_path, monkeypatch, [
        ("unit", "push-only", b"repo", b"live", ""),
    ])
    units = [replace(units[0], effects=(*units[0].effects,
        PublicationEffect("chmod", units[0].effects[0].path, mode=0o600)))]
    def fail(*args, **kwargs):
        raise PermissionError("mode denied")
    monkeypatch.setattr(publication.file_access, "chmod", fail)
    result = execute(tmp_path, metadata, units)
    assert result.units[0].status == "failed"
    assert "mode denied" in result.error
    assert (tmp_path / "live/unit").read_bytes() == b"frozen"
    assert [step.status for step in result.steps] == ["ok", "failed"]


def test_nested_scope_order_and_snapshot_after_pre_hooks(tmp_path, monkeypatch):
    metadata, units = prepare(tmp_path, monkeypatch, [
        ("first", "push-only", b"repo", b"live", ""),
        ("second", "push-only", b"repo", b"live", ""),
    ])
    log = tmp_path / "order"
    def hook(scope, phase, target=None):
        label = target or scope
        return HookPlan(
            hook_name=f"{phase}_push",
            command=f"printf '{phase}:{label}\\n' >> {log}",
            cwd=tmp_path, repo_name="main",
            package_id="app" if scope != "repo" else None,
            scope_kind=scope, target_name=target,
        )
    hooks = [hook(scope, phase, target)
             for scope, target in [("package", None), ("target", "first"), ("target", "second")]
             for phase in ["pre", "post"]]
    package = replace(metadata.packages[0], hooks={
        phase + "_push": [h for h in hooks if h.hook_name == phase + "_push"]
        for phase in ["pre", "post"]
    })
    metadata = replace(metadata, packages=(package,), repo_hooks=(
        ("main", (hook("repo", "pre"), hook("repo", "post"))),
    ))
    original = publication.create_push_snapshot
    def snapshot(*args):
        assert log.read_text().splitlines() == ["pre:repo", "pre:package", "pre:first"]
        return original(*args)
    monkeypatch.setattr(publication, "create_push_snapshot", snapshot)
    result = execute(tmp_path, metadata, units)
    assert result.error is None
    assert log.read_text().splitlines() == [
        "pre:repo", "pre:package", "pre:first", "post:first",
        "pre:second", "post:second", "post:package", "post:repo",
    ]


def test_pre_hook_interruption_has_no_snapshot_and_no_attempted_units(tmp_path, monkeypatch):
    metadata, units = prepare(tmp_path, monkeypatch, [
        ("unit", "push-only", b"repo", b"live",
         '[targets.unit.hooks]\npre_push = "exit 130"'),
    ])
    result = execute(tmp_path, metadata, units)
    assert result.interrupted
    assert result.units[0].status == "skipped"
    assert result.steps[0].status == "interrupted"
    assert result.snapshot is None
    assert (tmp_path / "live/unit").read_bytes() == b"live"


def test_followed_link_publishes_to_current_referent(tmp_path, monkeypatch):
    metadata, units = prepare(tmp_path, monkeypatch, [
        ("unit", "push-only", b"repo", b"live", ""),
    ])
    package = metadata.packages[0]
    metadata = replace(metadata, packages=(replace(package, target_plans=[
        replace(package.target_plans[0], file_symlink_mode="follow")
    ]),))
    live = tmp_path / "live/unit"
    referent = tmp_path / "referent"
    live.rename(referent)
    live.symlink_to(referent)
    result = execute(tmp_path, metadata, units)
    assert result.error is None
    assert live.is_symlink()
    assert referent.read_bytes() == b"frozen"


def test_prompt_delete_unlinks_without_deleting_referent(tmp_path, monkeypatch):
    metadata, units = prepare(tmp_path, monkeypatch, [
        ("unit", "push-only-delete", b"repo", b"live", ""),
    ])
    live = tmp_path / "live/unit"
    referent = tmp_path / "referent"
    live.rename(referent)
    live.symlink_to(referent)
    result = execute(tmp_path, metadata, [
        replace(units[0], effects=(PublicationEffect("delete", live),))
    ])
    assert result.error is None
    assert not live.is_symlink()
    assert referent.read_bytes() == b"live"


def test_live_shape_failure_is_typed_and_precedes_snapshot(tmp_path, monkeypatch):
    metadata, units = prepare(tmp_path, monkeypatch, [
        ("unit", "push-only", b"repo", b"live", ""),
    ])
    live = tmp_path / "live/unit"
    live.rename(tmp_path / "old")
    live.mkdir()
    result = execute(tmp_path, metadata, units)
    assert result.units[0].status == "failed"
    assert "regular file" in result.error
    assert result.snapshot is None


def test_snapshot_failure_reports_exact_step_before_mutation(tmp_path, monkeypatch):
    metadata, units = prepare(tmp_path, monkeypatch, [
        ("unit", "push-only", b"repo", b"live", ""),
    ])
    def fail(*args):
        raise OSError("snapshot unavailable")
    monkeypatch.setattr(publication, "create_push_snapshot", fail)
    result = execute(tmp_path, metadata, units)
    assert result.error == "snapshot unavailable"
    assert result.units[0].status == "failed"
    assert [(step.step.kind, step.status) for step in result.steps] == [("snapshot", "failed")]
    assert (tmp_path / "live/unit").read_bytes() == b"live"


def test_no_write_completion_survives_later_live_failure(tmp_path, monkeypatch):
    metadata, units = prepare(tmp_path, monkeypatch, [
        ("first", "push-only", b"repo", b"live", ""),
        ("second", "push-only", b"repo", b"live",
         '[targets.second.hooks]\npre_push = "exit 1"'),
    ])
    result = execute(tmp_path, metadata, [replace(units[0], effects=()), units[1]])
    assert result.error
    assert [unit.status for unit in result.units] == ["ok", "skipped"]
    assert result.snapshot is None


@pytest.mark.parametrize("phase", ["write", "snapshot-create", "snapshot-finalize"])
@pytest.mark.parametrize("exception_type", [InterruptedError, KeyboardInterrupt])
def test_interrupted_io_is_typed_and_preserves_completed_units(tmp_path, monkeypatch, phase, exception_type):
    metadata, units = prepare(tmp_path, monkeypatch, [
        ("unit", "push-only", b"repo", b"live", ""),
    ])
    def interrupt(*args, **kwargs):
        raise exception_type("I/O interrupted")
    if phase == "write":
        monkeypatch.setattr(publication.file_access, "write_bytes_atomic", interrupt)
    elif phase == "snapshot-create":
        monkeypatch.setattr(publication, "create_push_snapshot", interrupt)
    else:
        monkeypatch.setattr(publication, "mark_snapshot_status", interrupt)
    result = execute(tmp_path, metadata, units)
    assert result.interrupted
    assert result.error == "I/O interrupted"
    assert result.steps[-1].status == "interrupted"
    assert result.steps[-1].exit_code == 130
    assert result.units[0].status == ("ok" if phase == "snapshot-finalize" else "failed")
    assert (tmp_path / "live/unit").read_bytes() == (
        b"frozen" if phase == "snapshot-finalize" else b"live"
    )
    if phase == "snapshot-create":
        assert result.steps[-1].step.kind == "snapshot"
        assert result.snapshot is None
    elif phase == "write":
        assert result.snapshot.status == "failed"
