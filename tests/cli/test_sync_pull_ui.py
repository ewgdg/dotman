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
        assert f"{int(kind is not None)} repository changes / 0 live writes" in deck.confirmation_text()
        document = sync_document(SimpleNamespace(dry_run=True, scopes=[]), session, None)
        unit = document["sync_units"][0]
        assert unit["resolution_intent"] == "use-live"
        assert unit["effects"] == []
        assert document["summary"]["repository_changes"] == int(kind is not None)
        assert (unit["primary_source_change"]["kind"] if kind else unit["primary_source_change"]) == kind
        assert "captured" not in str(document)


def test_use_live_uses_shared_resolution_style():
    assert render_sync_term("Use live", use_color=True) == render_sync_term("Use repository", use_color=True).replace("Use repository", "Use live")


@pytest.mark.parametrize("dry_run", [True, False])
def test_mixed_cli_reports_repository_and_live_effects(tmp_path, monkeypatch, capsys, dry_run):
    import json
    from tests.cli.test_sync_deck_command import arguments, runner_for

    engine = make_engine(tmp_path, monkeypatch, [
        ("push", "push-only", b"push-repo", b"push-live", ""),
        ("pull", "pull-only", b"pull-repo", b"pull-live", ""),
    ])
    assert runner_for(engine).run(arguments(dry_run=dry_run)) == 0
    document = json.loads(capsys.readouterr().out)
    units = {unit["identity"]: unit for unit in document["sync_units"]}
    push = units["main:app.push"]
    pull = units["main:app.pull"]
    assert document["summary"]["repository_changes"] == 1
    assert document["summary"]["live_writes"] == 1
    assert document["summary"]["live_deletions"] == 0
    assert push["resolution_intent"] == "use-repository"
    assert push["primary_source_change"] is None
    assert [effect["kind"] for effect in push["effects"]] == ["write"]
    assert pull["resolution_intent"] == "use-live"
    assert pull["primary_source_change"] == {
        "kind": "write", "path": str(tmp_path / "repo/packages/app/pull"),
        "bytes": len(b"pull-live"),
    }
    assert pull["effects"] == []
    assert pull["base"] == {
        "status": "unavailable", "provenance": None, "acknowledged": not dry_run,
    }
    assert push["base"]["acknowledged"] is False
    assert {unit["result"] for unit in units.values()} == {
        "would-converge" if dry_run else "converged"
    }
    assert (tmp_path / "repo/packages/app/pull").read_bytes() == (b"pull-repo" if dry_run else b"pull-live")
    assert (tmp_path / "live/pull").read_bytes() == b"pull-live"
    assert (tmp_path / "repo/packages/app/push").read_bytes() == b"push-repo"
    assert (tmp_path / "live/push").read_bytes() == (b"push-live" if dry_run else b"push-repo")
    if dry_run:
        assert document["stages"] == []
    else:
        stages = [step["stage"] for step in document["stages"]]
        assert "repository-apply" in stages and "live-publication" in stages
        assert stages.index("repository-apply") < stages.index("live-publication")


@pytest.mark.parametrize('projected', [False, True])
def test_no_write_review_separates_frozen_pull_views_from_repository_effect(tmp_path, monkeypatch, projected):
    marker = tmp_path / 'capture-count'
    comparison = ('{ repo = "printf compared-repo", live = "printf compared-live" }'
                  if projected else '{ repo = "raw", live = "raw" }')
    engine = make_engine(tmp_path, monkeypatch, [
        ('unit', 'pull-only', b'repo', b'live-drift',
         f'capture = "echo capture >> {marker}; printf repo"\ncompare = {comparison}'),
    ])
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        deck = CommandDeck(session, use_color=False)
        assert not marker.exists()
        (tmp_path / 'repo/packages/app/unit').write_bytes(b'external-repo')
        (tmp_path / 'live/unit').write_bytes(b'external-live')
        deck.open_review()
        assert not session.view.rows[0].approved
        assert session.view.rows[0].proposal.primary_source_change is None
        text = deck.review_text()
        evidence, outcome = text.split('  Repository effect preview:', 1)
        assert 'Frozen Pull Views:' in evidence
        assert '--- frozen repository Pull View' in evidence
        assert '+++ frozen live Pull View' in evidence
        assert ('-compared-repo' if projected else '-repo') in evidence
        assert ('+compared-live' if projected else '+live-drift') in evidence
        assert 'No content difference' in outcome
        assert 'Primary Source Change: none' in text
        assert 'Live remains unchanged' in text
        assert 'external-' not in text
        deck.select(True)
        assert session.view.rows[0].approved
        assert deck.review_text().replace('approved', 'unapproved') == text
        assert marker.read_text().splitlines() == ['capture']
