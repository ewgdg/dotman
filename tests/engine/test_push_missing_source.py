"""Permanent Push treats a missing explicit repository file as an endpoint, not a Render input."""

import pytest

from dotman.sync_base_store import Missing, SyncBaseStore
from tests.engine.test_sync_session import make_engine


def push_tracked_scope(engine):
    with engine.open_push_session(engine.resolve_sync_scope()) as session:
        result = session.execute().result
    assert result.status == "completed", result
    return result


@pytest.mark.parametrize("transformed", [False, True])
@pytest.mark.parametrize("live", [b"live", None])
def test_missing_repository_file_publishes_typed_absence(tmp_path, monkeypatch, transformed, live):
    marker = tmp_path / "render-ran"
    render = f'render = "touch {marker}; printf rendered"' if transformed else ""
    engine = make_engine(tmp_path, monkeypatch, [("unit", "both", None, live, render)])
    source = tmp_path / "repo/packages/app/unit"
    destination = tmp_path / "live/unit"
    assert not source.exists()

    plans = engine.plan_push_query("main:app@default")
    target = plans[0].target_plans[0]
    assert target.checkpoint_payload == Missing()
    assert target.action == ("delete" if live is not None else "noop")
    assert target.desired_bytes is None
    assert target.push_checkpoints[0].frozen.payload == Missing()
    push_tracked_scope(engine)
    assert not destination.exists()
    assert not source.exists()
    assert not marker.exists()
    with SyncBaseStore.open(engine._tracked_state_context.state_root, "main", read_only=True) as store:
        assert store.read(b"main:app.unit").payload == Missing()


@pytest.mark.parametrize("live", [b"live", None])
def test_missing_patch_capture_source_is_valid_push_endpoint(tmp_path, monkeypatch, live):
    patch_settings = 'render = "jinja"\ncapture = "patch"\n[targets.unit.compare]\nrepo = "render"\nlive = "raw"'
    engine = make_engine(tmp_path, monkeypatch, [("unit", "both", None, live, patch_settings)])
    source = tmp_path / "repo/packages/app/unit"
    destination = tmp_path / "live/unit"
    plans = engine.plan_push_query("main:app@default")
    target = plans[0].target_plans[0]
    assert target.action == ("delete" if live is not None else "noop")
    assert target.checkpoint_payload == Missing()
    assert target.desired_bytes is None
    push_tracked_scope(engine)
    assert not source.exists()
    assert not destination.exists()


def test_missing_patch_capture_source_still_validates_static_configuration(tmp_path, monkeypatch):
    invalid_patch_settings = 'capture = "patch"\n[targets.unit.compare]\nrepo = "render"\nlive = "raw"'
    with pytest.raises(ValueError, match='capture = "patch" requires non-raw render'):
        make_engine(tmp_path, monkeypatch, [("unit", "both", None, b"live", invalid_patch_settings)])


@pytest.mark.parametrize("live", [b"live", None])
def test_missing_repository_preview_is_read_only(tmp_path, monkeypatch, live):
    engine = make_engine(tmp_path, monkeypatch, [("unit", "both", None, live, 'render = "printf rendered"')])
    source = tmp_path / "repo/packages/app/unit"
    destination = tmp_path / "live/unit"
    plans = engine.plan_push_query("main:app@default", maintain_sync_bases=False)
    target = plans[0].target_plans[0]
    assert target.action == ("delete" if live is not None else "noop")
    checkpoint = target.push_checkpoints[0]
    assert not list((checkpoint.manager_root / "repos" / checkpoint.state_key).glob("*.json"))
    assert not source.exists()
    assert (destination.read_bytes() if live is not None else None) == live


def test_missing_repository_source_deletion_has_restorable_snapshot(tmp_path, monkeypatch):
    from dotman.snapshot import build_restore_actions, execute_restore_action, list_snapshots

    engine = make_engine(tmp_path, monkeypatch, [("unit", "both", None, b"live", "")])
    push_tracked_scope(engine)
    destination = tmp_path / "live/unit"
    assert not destination.exists()
    saved, = list_snapshots(engine.config.snapshots.path)
    assert saved.entries[0].push_action == "delete"
    assert saved.entries[0].existed_before
    for action in build_restore_actions(saved):
        assert execute_restore_action(action).status == "ok"
    assert destination.read_bytes() == b"live"


def test_missing_repository_source_follow_link_deletes_referent_only(tmp_path, monkeypatch):
    from dotman.engine import DotmanEngine

    engine = make_engine(tmp_path, monkeypatch, [("unit", "both", None, None, "")])
    config = engine.config.config_path
    config.write_text(config.read_text() + '\n[symlinks]\nfile_symlink_mode = "follow"\n')
    engine = DotmanEngine.from_config_path(config)
    referent = tmp_path / "referent"
    referent.write_bytes(b"remove")
    link = tmp_path / "live/unit"
    link.parent.mkdir(exist_ok=True)
    link.symlink_to(referent)
    plans = engine.plan_push_query("main:app@default")
    assert plans[0].target_plans[0].action == "delete"
    push_tracked_scope(engine)
    assert link.is_symlink()
    assert not referent.exists()


def test_broken_repository_source_symlink_is_not_typed_missing(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [("unit", "both", None, b"live", "")])
    (tmp_path / "repo/packages/app/unit").symlink_to("unavailable")
    with pytest.raises(ValueError, match="repo source path does not exist"):
        engine.plan_push_query("main:app@default")
    assert (tmp_path / "live/unit").read_bytes() == b"live"
