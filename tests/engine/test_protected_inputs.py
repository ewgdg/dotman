"""Protected Render and Capture inputs are staged for commands."""

from pathlib import Path

import dotman.file_access as file_access
import dotman.projection as projection
from tests.engine.test_sync_session import make_engine


def _stage_as_protected(monkeypatch, real_path):
    real_needs_sudo = file_access.needs_sudo_for_read
    def _needs_sudo(path):
        if Path(path) == real_path:
            return True
        return real_needs_sudo(Path(path))
    monkeypatch.setattr(file_access, "needs_sudo_for_read", _needs_sudo)
    monkeypatch.setattr(projection, "needs_sudo_for_read", _needs_sudo, raising=False)


def test_push_render_stages_protected_repo_input(tmp_path, monkeypatch):
    repo_file = tmp_path / "repo" / "packages" / "app" / "unit"
    live_file = tmp_path / "live" / "unit"
    render_line = """render = 'test "$(cat "$DOTMAN_SOURCE")" = "secret-repo" && printf "%s" "$DOTMAN_SOURCE"'"""
    compare_line = 'compare = {repo = "render", live = "raw"}'
    extra = chr(10).join([render_line, compare_line])
    engine = make_engine(tmp_path, monkeypatch, [("unit", "both", b"secret-repo", b"live", extra)])
    _stage_as_protected(monkeypatch, repo_file)
    with engine.open_push_session(engine.resolve_sync_scope()) as session:
        assert session.execute().result.status == "completed"
    staged = Path(live_file.read_bytes().decode())
    assert staged != repo_file
    assert not staged.exists()


def test_pull_capture_stages_protected_live_input(tmp_path, monkeypatch):
    repo_file = tmp_path / "repo" / "packages" / "app" / "unit"
    live_file = tmp_path / "live" / "unit"
    capture_line = """capture = 'test "$(cat "$DOTMAN_LIVE_PATH")" = "secret-live" && printf "%s" "$DOTMAN_LIVE_PATH"'"""
    compare_line = 'compare = {repo = "raw", live = "raw"}'
    extra = chr(10).join([capture_line, compare_line])
    engine = make_engine(tmp_path, monkeypatch, [("unit", "both", b"repo", b"secret-live", extra)])
    _stage_as_protected(monkeypatch, live_file)
    with engine.open_pull_session(engine.resolve_sync_scope()) as session:
        assert session.execute().result.status == "completed"
    staged = Path(repo_file.read_bytes().decode())
    assert staged != live_file
    assert not staged.exists()
