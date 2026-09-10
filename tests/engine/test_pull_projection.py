"""Pull projection contracts exercised through tracked scope and frozen sessions."""
import json

import pytest

from dotman.sync_base_store import FilePresent
from tests.engine.test_sync_session import make_engine


@pytest.mark.parametrize("preset", ["jinja-editor", "jinja-patch", "jinja-patch-editor"])
def test_pull_presets_compare_rendered_repository_without_capturing_agreement(tmp_path, monkeypatch, preset):
    engine = make_engine(tmp_path, monkeypatch, [
        ("unit", "both", b"{{ profile }}\n", b"default\n", f'preset = "{preset}"'),
    ])
    with engine.open_pull_session(engine.resolve_sync_scope(), preview=True) as session:
        observation = session.view.observations[0]
        assert observation.state == "directly-in-sync"
        assert observation.compare_repo == "render"
        assert observation.compare_live == "raw"
        assert session.view.rows == ()
    assert (tmp_path / "repo/packages/app/unit").read_bytes() == b"{{ profile }}\n"


@pytest.mark.parametrize("extra,source,live,expected", [
    ("", b"repo", b"live", b"live"),
    ('capture = "tr a-z A-Z < \\\"$DOTMAN_LIVE_PATH\\\""\ncompare = {repo = "raw", live = "raw"}',
     b"repo", b"live", b"LIVE"),
    ('render = "sed s/template/rendered/ \\\"$DOTMAN_SOURCE\\\""\ncapture = "patch"\ncompare = {repo = "render", live = "raw"}',
     b"template\nold\n", b"rendered\nnew\n", b"template\nnew\n"),
    ('preset = "jinja-patch"', b"{{ profile }}\nold\n", b"default\nnew\n", b"{{ profile }}\nnew\n"),
])
def test_pull_capture_outcome_matches_review_and_repository_apply(tmp_path, monkeypatch, extra, source, live, expected):
    engine = make_engine(tmp_path, monkeypatch, [("unit", "both", source, live, extra)])
    with engine.open_pull_session(engine.resolve_sync_scope()) as session:
        row = session.view.rows[0]
        assert row.approved and row.proposal.repository == FilePresent(expected)
        assert row.proposal.publication_effects == ()
        assert (tmp_path / "repo/packages/app/unit").read_bytes() == source
        assert session.execute().result.status == "completed"
    assert (tmp_path / "repo/packages/app/unit").read_bytes() == expected
    assert (tmp_path / "live/unit").read_bytes() == live


@pytest.mark.parametrize("exit_code", [7, 100])
def test_pull_capture_failure_does_not_launch_configured_editor(tmp_path, monkeypatch, exit_code):
    marker = tmp_path / "editor-ran"
    editor = json.dumps(f"touch {marker}")
    engine = make_engine(tmp_path, monkeypatch, [(
        "unit", "both", b"repo", b"live",
        f'capture = "exit {exit_code}"\ncompare = {{repo = "raw", live = "raw"}}\n'
        f'editor = {{run = {editor}, io = "pipe"}}',
    )])
    with engine.open_pull_session(engine.resolve_sync_scope()) as session:
        row = session.view.rows[0]
        assert not row.approved and row.proposal is None
        assert row.diagnostics
        assert not marker.exists()
        assert session.execute().result.status == "failed"
    assert (tmp_path / "repo/packages/app/unit").read_bytes() == b"repo"
    assert not marker.exists()


def test_pull_patch_capture_live_only_is_blocked_without_guessing_template(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [(
        "unit", "both", None, b"live", 'render = "jinja"\ncapture = "patch"\ncompare = {repo = "render", live = "raw"}',
    )])
    with engine.open_pull_session(engine.resolve_sync_scope(), preview=True) as session:
        row = session.view.rows[0]
        assert not row.approved and row.proposal is None
        assert row.diagnostics
    assert not (tmp_path / "repo/packages/app/unit").exists()


def test_pull_materialization_uses_frozen_capture_input_once(tmp_path, monkeypatch):
    marker = tmp_path / "capture-count"
    capture = json.dumps(f'printf x >> {marker}; cat "$DOTMAN_LIVE_PATH"')
    engine = make_engine(tmp_path, monkeypatch, [(
        "unit", "both", b"repo", b"live", f'capture = {capture}\ncompare = {{repo = "raw", live = "raw"}}',
    )])
    with engine.open_pull_session(engine.resolve_sync_scope()) as session:
        assert marker.read_text() == "x"
        (tmp_path / "live/unit").write_bytes(b"external")
        assert session.execute().result.status == "completed"
    assert marker.read_text() == "x"
    assert (tmp_path / "repo/packages/app/unit").read_bytes() == b"live"


@pytest.mark.parametrize("mode", [0o600, 0o755])
def test_pull_file_never_applies_live_chmod_policy(tmp_path, monkeypatch, mode):
    engine = make_engine(tmp_path, monkeypatch, [("unit", "both", b"repo", b"live", 'chmod = "0644"')])
    live = tmp_path / "live/unit"
    live.chmod(mode)
    with engine.open_pull_session(engine.resolve_sync_scope()) as session:
        assert session.execute().result.status == "completed"
    assert live.stat().st_mode & 0o777 == mode
