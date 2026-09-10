from dataclasses import replace
from types import SimpleNamespace

from dotman.cli_style import render_sync_term
from dotman.sync_base_store import FilePresent, SyncBaseEnvelope, SyncBaseRecord
from dotman.sync_deck import CommandDeck, row_resolution
from dotman.sync_deck_command import sync_document
from dotman.sync_session import AuxiliaryRow, SyncSession
from tests.engine.test_sync_session import make_engine


def test_directory_root_is_auxiliary_selection_not_proposal_approval():
    root = AuxiliaryRow("root:r:p.tree", "directory-root", False, "r:p.tree", ("push",))
    with SyncSession((), preview=True, auxiliary=(root,)) as session:
        deck = CommandDeck(session, use_color=False)
        assert row_resolution(root) == "Directory Root Work"
        assert render_sync_term("Directory Root Work", use_color=True) == render_sync_term(
            "Probe Work", use_color=True).replace("Probe Work", "Directory Root Work")
        deck.select_all(True)
        assert session.view.rows[0].included
        deck.open_review()
        assert not deck.reviewing
        document = sync_document(SimpleNamespace(dry_run=True, scopes=[]), session, None)
        assert document["directory_root_work"] == [{
            "identity": "r:p.tree", "selected": True, "directions": ["push"], "diagnostics": [],
        }]
        assert document["sync_units"] == []
        assert "0 approved units" in deck.confirmation_text()
        deck.select_all(False)
        assert not session.view.rows[0].included


def test_review_keeps_pull_and_base_evidence_when_using_repository(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [("unit", "both", b"repo", b"live", "")])
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as opened:
        original = opened.view.rows[0]
        record = SyncBaseRecord(b"unit", FilePresent(b"ancestor"), SyncBaseEnvelope(
            "a" * 40, "sha1", "b" * 64, "conservative",
        ))
        observation = replace(original.observation,
                              base=replace(original.observation.base, status="usable", record=record))
        row = replace(original, observation=observation, intent="use-repository")
        session = SimpleNamespace(view=replace(opened.view, rows=(row,)))
        text = CommandDeck(session, use_color=False).review_text()
        assert "Frozen Pull Views:" in text
        assert "Base provenance: conservative" in text
        assert "Base commit: " + "a" * 40 in text
        assert "Base vs frozen repository:" in text
        assert "-ancestor" in text and "+repo" in text
        assert "Capture: not required" in text


def test_confirmation_cancel_preserves_reviewed_selection(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [("unit", "push-only", b"repo", b"live", "")])
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        deck = CommandDeck(session, use_color=False)
        deck.select()
        reviewed = session.view
        deck.confirm()
        assert deck.confirming
        deck.select(False)
        deck.select_all(False)
        deck.edit()
        deck.open_review()
        assert session.view == reviewed
        assert deck.back()
        assert not deck.confirming
        assert session.view == reviewed

def test_single_resolution_is_static_and_blocked_rows_remain_visible(tmp_path, monkeypatch):
    import asyncio
    from textual.widgets import OptionList
    from dotman.sync_deck import SyncDeckApp, WorksetTable

    engine = make_engine(tmp_path, monkeypatch, [
        ("unit", "push-only", b"repo", b"live", ""),
        ("blocked", "push-only", b"repo", b"live", '[targets.blocked.hooks]\nguard_push = "exit 100"'),
    ])
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        app = SyncDeckApp(CommandDeck(session, use_color=False))

        async def interact():
            async with app.run_test() as pilot:
                table = app.query_one(WorksetTable)
                for index, row in enumerate(session.view.rows):
                    table.move_cursor(row=index)
                    app.action_resolution()
                    assert not app.query_one(OptionList).display
                    if not row.allowed_intents:
                        assert str(table.get_row_at(index)[0]) == "[-]"
                        assert row.observation.diagnostics or row.diagnostics
                app.deck.select_all(True)
                assert sum(row.approved for row in session.view.rows) == 1
                await pilot.pause()

        asyncio.run(asyncio.wait_for(interact(), timeout=5))


def test_confirmation_requires_valid_completed_approved_proposals(tmp_path, monkeypatch):
    from dotman.sync_observation import Diagnostic

    engine = make_engine(tmp_path, monkeypatch, [("unit", "push-only", b"repo", b"live", "")])
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        deck = CommandDeck(session, use_color=False)
        deck.select()
        ready = session.view.rows[0]
        assert ready.proposal is not None
        for invalid in (
            replace(ready, proposal=None),
            replace(ready, diagnostics=(Diagnostic("failed", "Materialization failed"),)),
            replace(ready, observation=replace(ready.observation, diagnostics=(
                Diagnostic("failed", "Observation failed"),))),
        ):
            for preview in (False, True):
                view = replace(session.view, preview=preview, rows=(invalid,),
                               allowed_commands=("execute", "preview", "abort"))
                reviewed = SimpleNamespace(view=view)
                confirmation = CommandDeck(reviewed, use_color=False)
                confirmation.confirm()
                assert not confirmation.confirming
                assert reviewed.view == view
                if not preview:
                    assert "ready" in confirmation.notice
            # Unapproved work never blocks a separately reviewed executable set.
            view = replace(view, preview=False, rows=(replace(invalid, approved=False),))
            confirmation = CommandDeck(SimpleNamespace(view=view), use_color=False)
            confirmation.confirm()
            assert confirmation.confirming
