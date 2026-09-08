from __future__ import annotations

import json
import pytest
from types import SimpleNamespace

from dotman.cli_parser import build_parser
from tests.engine.test_sync_session import make_engine


def arguments(**overrides):
    return SimpleNamespace(**dict(
        dict(command="sync", config=None, scopes=[], dry_run=True,
             unattended=True, json_output=True), **overrides))


def runner_for(engine):
    from dotman.sync_deck_command import SyncDeckCommandRunner
    return SyncDeckCommandRunner(engine_factory=lambda _: engine, use_color=False)


def test_sync_parser_accepts_multiple_exact_scopes_and_explicit_approval():
    args = build_parser().parse_args(["--unattended", "sync", "main:app.one", "main:app.two", "--dry-run"])
    assert args.scopes == ["main:app.one", "main:app.two"]
    assert args.unattended and args.dry_run


def test_unattended_preview_selects_defaults_without_writes_or_content_leaks(tmp_path, monkeypatch, capsys):
    engine = make_engine(tmp_path, monkeypatch, [("unit", "push-only", b"repo-secret", b"live-secret", "")])
    assert runner_for(engine).run(arguments()) == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert set(payload) == {
        "operation", "mode", "status", "scope", "summary", "sync_units",
        "additional_source_changes", "probe_work", "directory_root_work", "hook_work", "stages",
    }
    assert payload["mode"] == "dry-run"
    unit = payload["sync_units"][0]
    assert unit["approved"] is True
    assert unit["effects"][0]["kind"] == "write"
    assert "repo-secret" not in output and "live-secret" not in output
    assert "base64" not in output and "content" not in output
    assert (tmp_path / "live/unit").read_bytes() == b"live-secret"


@pytest.mark.parametrize("dry_run", [False, True])
def test_nonterminal_sync_requires_unattended_before_opening_session(tmp_path, monkeypatch, capsys, dry_run):
    engine = make_engine(tmp_path, monkeypatch, [("unit", "push-only", b"repo", b"live", "")])
    def forbidden(*args, **kwargs):
        raise AssertionError("unapproved unattended execution must not open a real session")
    monkeypatch.setattr(engine, "open_sync_session", forbidden)
    assert runner_for(engine).run(arguments(dry_run=dry_run, unattended=False)) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "failed"
    assert (tmp_path / "live/unit").read_bytes() == b"live"


def test_unattended_sync_executes_explicit_approval(tmp_path, monkeypatch, capsys):
    engine = make_engine(tmp_path, monkeypatch, [("unit", "push-only", b"repo", b"live", "")])
    assert runner_for(engine).run(arguments(dry_run=False, unattended=True)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["sync_units"][0]["result"] == "converged"
    assert (tmp_path / "live/unit").read_bytes() == b"repo"


def test_unattended_observation_failure_prevents_other_publication(tmp_path, monkeypatch, capsys):
    engine = make_engine(tmp_path, monkeypatch, [("unit", "push-only", b"repo", b"live", ""),
                                               ("bad", "push-only", b"repo", b"live", "")])
    (tmp_path / "live/bad").unlink()
    (tmp_path / "live/bad").mkdir()
    assert runner_for(engine).run(arguments(dry_run=False, unattended=True)) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "failed"
    assert (tmp_path / "live/unit").read_bytes() == b"live"


def test_review_preserves_focused_row_and_scrolls_without_changing_selection(tmp_path, monkeypatch):
    from dotman.sync_deck import CommandDeck
    engine = make_engine(tmp_path, monkeypatch, [
        ("one", "push-only", b"repo\n", b"live\n", ""),
        ("two", "push-only", b"repo\n" * 100, b"live\n" * 100, ""),
    ])
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        deck = CommandDeck(session, use_color=False)
        deck.move(1)
        identity = deck.focused_row.row_id
        deck.open_review()
        assert deck.reviewing
        deck.move(5)
        assert deck.focused_row.row_id == identity
        assert deck.review_scroll == 5
        assert not deck.focused_row.approved
        text = deck.review_text()
        assert str(tmp_path / "live/two") in text
        assert "Repository path:" in text
        assert "write" in text and "+repo" in text and "-live" in text
        deck.back()
        assert deck.focused_row.row_id == identity
        deck.open_review()
        assert deck.review_scroll == 5


def test_confirmation_freezes_selection_and_cancel_restores_workset(tmp_path, monkeypatch):
    from dotman.sync_deck import CommandDeck
    engine = make_engine(tmp_path, monkeypatch, [
        ("one", "push-only", b"repo", b"live", ""),
        ("two", "push-only", b"repo", b"live", ""),
    ])
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        deck = CommandDeck(session, use_color=False)
        deck.select()
        deck.confirm()
        assert deck.confirming
        frozen = session.view
        deck.move(1)
        deck.select()
        deck.select_all(False)
        deck.open_review()
        assert deck.focus == 0 and not deck.reviewing
        assert session.view == frozen
        assert "1 approved units" in deck.text()
        assert "1 live writes" in deck.text()
        deck.back()
        assert not deck.confirming
        assert session.view == frozen
        deck.move(1)
        deck.select()
        assert all(row.approved for row in session.view.rows)


def test_keyboard_review_return_and_confirmation_execute_frozen_selection(tmp_path, monkeypatch):
    import asyncio

    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    from dotman.sync_deck import CommandDeck, command_deck_application

    engine = make_engine(tmp_path, monkeypatch, [
        ("one", "push-only", b"repo", b"live", ""),
        ("two", "push-only", b"repo", b"live", ""),
    ])
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        deck = CommandDeck(session, use_color=False)
        with create_pipe_input() as pipe:
            application = command_deck_application(deck)
            application.input = pipe
            application.output = DummyOutput()

            async def interact():
                task = asyncio.create_task(application.run_async())
                await asyncio.sleep(0)
                # Focus second row, review, try moving, return, select, confirm.
                pipe.send_text("\x1b[B\r\x1b[B\x1b")
                await asyncio.sleep(0.1)
                pipe.send_text(" x \r")
                return await asyncio.wait_for(task, timeout=2)

            assert asyncio.run(interact()) is True
        assert deck.focus == 1
        assert [row.approved for row in session.view.rows] == [False, True]


def test_mouse_selects_only_clicked_row_and_cannot_change_confirmation(tmp_path, monkeypatch):
    from dotman.sync_deck import CommandDeck
    engine = make_engine(tmp_path, monkeypatch, [
        ("one", "push-only", b"repo", b"live", ""),
        ("two", "push-only", b"repo", b"live", ""),
    ])
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        deck = CommandDeck(session, use_color=False)
        deck.click(1, selection=False)
        assert deck.focus == 1 and not any(row.approved for row in session.view.rows)
        deck.click(1, selection=True)
        assert [row.approved for row in session.view.rows] == [False, True]
        deck.confirm()
        deck.click(0, selection=True)
        assert deck.focus == 1
        assert [row.approved for row in session.view.rows] == [False, True]


def test_review_shows_newline_only_publication_and_exact_mode(tmp_path, monkeypatch):
    from dotman.sync_deck import CommandDeck
    engine = make_engine(tmp_path, monkeypatch, [
        ("unit", "push-only", b"same\n", b"same", 'chmod = "0600"'),
    ])
    (tmp_path / "live/unit").chmod(0o644)
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        deck = CommandDeck(session, use_color=False)
        deck.open_review()
        text = deck.review_text()
        assert "No newline at end of file" in text
        assert "0600" in text
        assert "5 bytes" in text


def test_interactive_preview_leaves_unselected_healthy_work_pending(tmp_path, monkeypatch, capsys):
    from dotman import sync_deck
    import sys

    engine = make_engine(tmp_path, monkeypatch, [("unit", "push-only", b"repo", b"live", "")])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

    def leave_unselected(session, *, use_color):
        assert not session.view.rows[0].approved
        return True

    monkeypatch.setattr(sync_deck, "run_command_deck", leave_unselected)
    assert runner_for(engine).run(arguments(unattended=False, json_output=False)) == 0
    assert "pending" in capsys.readouterr().out
    assert (tmp_path / "live/unit").read_bytes() == b"live"


def test_failed_materialization_stops_unattended_publication(tmp_path, monkeypatch, capsys):
    engine = make_engine(tmp_path, monkeypatch, [
        ("one", "push-only", b"repo", b"live", ""),
        ("two", "push-only", b"repo", b"live", ""),
    ])
    live = tmp_path / "live/two"
    referent = tmp_path / "referent"
    referent.write_bytes(b"live")
    live.unlink()
    live.symlink_to(referent)
    assert runner_for(engine).run(arguments(dry_run=False)) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "failed"
    assert payload["sync_units"][1]["diagnostics"]
    assert (tmp_path / "live/one").read_bytes() == b"live"
    assert live.is_symlink() and referent.read_bytes() == b"live"


def test_json_summary_counts_only_selected_effects(tmp_path, monkeypatch):
    from dotman.sync_deck_command import review, sync_document
    from dotman.sync_session import Preview

    engine = make_engine(tmp_path, monkeypatch, [("unit", "push-only", b"repo", b"live", "")])
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        review(session, session.view.rows[0].row_id)
        view = session.view
        result = session.dispatch(Preview(view.session_id, view.revision)).result
        payload = sync_document(arguments(), session, result)
        assert payload["sync_units"][0]["effects"]
        assert payload["sync_units"][0]["approved"] is False
        assert payload["summary"]["live_writes"] == 0
        assert payload["summary"]["live_deletions"] == 0


@pytest.mark.parametrize("failure,code,status", [
    (ValueError("invalid scope"), 2, "failed"),
    (KeyboardInterrupt(), 130, "aborted"),
])
def test_json_preflight_failure_is_one_document(tmp_path, monkeypatch, capsys, failure, code, status):
    engine = make_engine(tmp_path, monkeypatch, [("unit", "push-only", b"repo", b"live", "")])
    def fail(*args):
        raise failure
    monkeypatch.setattr(engine, "resolve_sync_scope", fail)
    assert runner_for(engine).run(arguments()) == code
    assert json.loads(capsys.readouterr().out)["status"] == status


def test_interrupted_materialization_reports_abort_without_publication(tmp_path, monkeypatch, capsys):
    from dotman import sync_session
    engine = make_engine(tmp_path, monkeypatch, [("unit", "push-only", b"repo", b"live", "")])
    def interrupt(*args):
        raise KeyboardInterrupt()
    monkeypatch.setattr(sync_session, "materialize", interrupt)
    assert runner_for(engine).run(arguments(dry_run=False)) == 130
    assert json.loads(capsys.readouterr().out)["status"] == "aborted"
    assert (tmp_path / "live/unit").read_bytes() == b"live"


@pytest.mark.parametrize("interrupt", [False, True])
def test_interactive_abort_emits_final_summary_and_releases_session(tmp_path, monkeypatch, capsys, interrupt):
    import sys
    from dotman import sync_deck

    engine = make_engine(tmp_path, monkeypatch, [("unit", "push-only", b"repo", b"live", "")])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    sessions = []
    def abort(session, **kwargs):
        sessions.append(session)
        if interrupt:
            raise KeyboardInterrupt()
        return False
    monkeypatch.setattr(sync_deck, "run_command_deck", abort)
    assert runner_for(engine).run(arguments(dry_run=False, unattended=False, json_output=False)) == 130
    assert "Sync" in capsys.readouterr().out
    assert sessions[0].view.terminal
    assert (tmp_path / "live/unit").read_bytes() == b"live"


def test_json_reports_actual_failed_hook_without_leaking_captured_output(tmp_path, monkeypatch, capsys):
    engine = make_engine(tmp_path, monkeypatch, [
        ("unit", "push-only", b"repo", b"live",
         '[targets.unit.hooks]\npost_push = "printf hook-secret; exit 7"'),
    ])
    assert runner_for(engine).run(arguments(dry_run=False)) == 1
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert "hook-secret" not in output
    assert payload["status"] == "failed"
    assert payload["sync_units"][0]["result"] == "converged"
    assert payload["summary"]["diagnostics"]
    assert all(set(step) == {
        "stage", "kind", "action", "scope", "repo", "package_id",
        "status", "skip_reason", "exit_code", "error",
    } for step in payload["stages"])
    assert all(step["stage"] == "live-publication" for step in payload["stages"])
    assert any(step["action"] == "post_push" and step["status"] == "failed"
               and step["exit_code"] == 7 for step in payload["stages"])
