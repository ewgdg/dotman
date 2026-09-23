"""Permanent Push CLI reports missing repository sources as deletion/noop work."""

import json

import pytest

from dotman.cli import main
from tests.engine.test_sync_session import make_engine


@pytest.mark.parametrize("live,expected_action", [(b"live", "delete"), (None, "noop")])
@pytest.mark.parametrize("patch_capture", [False, True])
def test_push_preview_missing_source_preserves_live_and_checkpoint(
    tmp_path, monkeypatch, capsys, live, expected_action, patch_capture,
):
    settings = (
        'render = "jinja"\ncapture = "patch"\n[targets.unit.compare]\nrepo = "render"\nlive = "raw"'
        if patch_capture else 'render = "printf generated"'
    )
    engine = make_engine(tmp_path, monkeypatch, [("unit", "both", None, live, settings)])
    args = ["--config", str(engine.config.config_path), "--json", "push", "--dry-run", "main:app"]
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    if expected_action == "delete":
        assert result["package_entries"][0]["targets"][0]["action"] == "delete"
    else:
        assert result["package_entries"] == []
    source = tmp_path / "repo/packages/app/unit"
    destination = tmp_path / "live/unit"
    assert not source.exists()
    assert (destination.read_bytes() if live is not None else None) == live
    assert not list((tmp_path / "state/dotman/repos/main").glob("*.json"))
