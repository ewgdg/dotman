from dataclasses import replace
from types import SimpleNamespace

import pytest

from dotman.cli_style import render_sync_term
from dotman.sync_base_store import FilePresent, Missing
from dotman.sync_deck import CommandDeck
from dotman.sync_deck_command import sync_document
from dotman.sync_session import Proposal
from tests.engine.test_sync_session import make_engine


@pytest.mark.parametrize("outcome,kind", [(FilePresent(b"captured\n"), "write"), (Missing(), "delete"), (None, None)])
def test_pull_review_and_document_show_repository_effect(tmp_path, monkeypatch, outcome, kind):
    engine = make_engine(tmp_path, monkeypatch, [("unit", "push-only", b"repo\n", b"live\n", "")])
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as opened:
        row = opened.view.rows[0]
        observation = replace(row.observation, configured_policy="pull-only", effective_policy="pull-only")
        proposal = Proposal(
            repository=outcome if outcome is not None else observation.repository,
            live=observation.live, primary_source_change=outcome,
            publication_effects=(), intent="use-live",
        )
        row = replace(row, observation=observation, allowed_intents=("use-live",), proposal=proposal, approved=True)
        session = SimpleNamespace(view=replace(opened.view, observations=(observation,), rows=(row,)))
        deck = CommandDeck(session, use_color=False)
        assert "Use live" in deck.text()
        review = deck.review_text()
        assert "Resolution: Use live" in review
        assert "Capture: frozen live" in review
        assert f"Primary Source Change: {kind or 'none'}" in review
        assert "Live remains unchanged" in review
        if kind == "write":
            assert "-repo" in review and "+captured" in review
        elif kind == "delete":
            assert "+++ /dev/null" in review
        else:
            assert "Approval still required" in review
        deck.confirming = True
        assert f"{int(kind is not None)} repository changes / 0 live writes" in deck.text()
        document = sync_document(SimpleNamespace(dry_run=True, scopes=[]), session, None)
        unit = document["sync_units"][0]
        assert unit["resolution_intent"] == "use-live"
        assert unit["effects"] == []
        assert document["summary"]["repository_changes"] == int(kind is not None)
        assert (unit["primary_source_change"]["kind"] if kind else unit["primary_source_change"]) == kind
        assert "captured" not in str(document)


def test_use_live_uses_shared_resolution_style():
    assert render_sync_term("Use live", use_color=True) == render_sync_term("Use repository", use_color=True).replace("Use repository", "Use live")
