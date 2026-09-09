"""Endpoint absence is evidence; only approved drift may converge."""

from dataclasses import replace
import os

import pytest

from dotman.sync_base_store import FilePresent, Missing, SyncBaseStore, SyncBaseStoreError
from dotman.sync_session import PrepareProposalReview, SetApproval
from tests.engine.test_sync_convergence import command
from tests.engine.test_sync_session import make_engine, open_session


@pytest.mark.parametrize("policy", ["push-only", "pull-only"])
@pytest.mark.parametrize("source,live", [(None, b""), (b"", None)])
def test_missing_and_empty_remain_distinct_through_approved_execution(
    policy, source, live, tmp_path, monkeypatch
):
    engine = make_engine(tmp_path, monkeypatch, [("unit", policy, source, live, "")])
    expected = source if policy == "push-only" else live
    with open_session(engine, preview=False) as session:
        observation = session.view.observations[0]
        assert observation.state == "drifted"
        assert observation.repository == (Missing() if source is None else FilePresent(source))
        assert observation.live == (Missing() if live is None else FilePresent(live))
        command(session, PrepareProposalReview, "main:app.unit")
        row = session.view.rows[0]
        assert not row.approved
        outcome = Missing() if expected is None else FilePresent(expected)
        assert row.proposal.repository == (observation.repository if policy == "push-only" else outcome)
        assert row.proposal.live == (outcome if policy == "push-only" else observation.live)
        command(session, SetApproval, row.row_id, True)
        assert session.execute().result.units[0].status == "converged"
    for path in (tmp_path / "repo/packages/app/unit", tmp_path / "live/unit"):
        assert (path.read_bytes() if path.exists() else None) == expected
    with open_session(engine) as later:
        base = later.view.observations[0].base
        if policy == "pull-only":
            assert base.status == "usable"
            # Acknowledgment stores committed ancestry, not the Proposal.
            assert base.record.payload == (Missing() if source is None else FilePresent(source))
        else:
            assert base.record is None


@pytest.mark.parametrize("source", [None, b"", b"retained source"])
def test_delete_policy_ignores_transforms_and_retains_repository(
    source, tmp_path, monkeypatch
):
    extra = 'render = "exit 9"\ncapture = "exit 9"\ncompare = { repo = "exit 9", live = "exit 9" }'
    engine = make_engine(tmp_path, monkeypatch, [("unit", "push-only-delete", source, b"live", extra)])
    with open_session(engine, preview=False) as session:
        observation = session.view.observations[0]
        assert observation.comparison_repository == Missing()
        assert observation.comparison_live == FilePresent(b"live")
        row = session.view.rows[0]
        assert row.allowed_intents == ("use-repository",)
        assert not row.approved
        command(session, SetApproval, row.row_id, True)
        proposal = session.view.rows[0].proposal
        assert proposal.primary_source_change is None
        assert proposal.live == Missing()
        assert [effect.kind for effect in proposal.publication_effects] == ["delete"]
        assert session.execute().result.units[0].status == "converged"
    repository = tmp_path / "repo/packages/app/unit"
    assert (repository.read_bytes() if repository.exists() else None) == source
    assert not (tmp_path / "live/unit").exists()
    with open_session(engine) as later:
        assert later.view.observations[0].state == "directly-in-sync"
        assert later.view.observations[0].base.record is None
        assert not later.view.rows


@pytest.mark.parametrize("side", ["repository", "live"])
@pytest.mark.parametrize("shape", ["directory", "fifo", "socket"])
def test_unsupported_endpoint_does_not_block_approved_interactive_peer(
    side, shape, tmp_path, monkeypatch
):
    import socket

    engine = make_engine(tmp_path, monkeypatch, [
        ("bad", "push-only", None, None, ""),
        ("good", "push-only", b"repo", b"live", ""),
    ])
    endpoint = tmp_path / ("repo/packages/app/bad" if side == "repository" else "live/bad")
    sock = None
    if shape == "directory":
        endpoint.mkdir()
    elif shape == "fifo":
        os.mkfifo(endpoint)
    else:
        sock = socket.socket(socket.AF_UNIX)
        sock.bind(str(endpoint))
    try:
        with open_session(engine, preview=False) as session:
            bad = session.view.rows[0]
            assert bad.kind == "diagnostic"
            assert bad.observation.state == "observation-failed"
            assert bad.observation.diagnostics[0].code == "observation-failed"
            assert bad.allowed_commands == ()
            command(session, SetApproval, "main:app.good", True)
            result = session.execute().result
            assert [unit.status for unit in result.units] == ["observation-failed", "converged"]
        assert (tmp_path / "live/good").read_bytes() == b"repo"
        assert endpoint.exists()
    finally:
        if sock is not None:
            sock.close()


@pytest.mark.parametrize("fail_acknowledgment", [False, True])
def test_eligible_no_write_requires_approval_and_successful_base_commit(
    fail_acknowledgment, tmp_path, monkeypatch
):
    engine = make_engine(tmp_path, monkeypatch, [
        ("unit", "pull-only", b"repo", b"live",
         'capture = "printf repo"\ncompare = { repo = "raw", live = "raw" }'),
    ])
    with open_session(engine, preview=False) as session:
        command(session, PrepareProposalReview, "main:app.unit")
        assert session.view.rows[0].proposal.primary_source_change is None
        assert session.view.rows[0].proposal.publication_effects == ()
        assert session.execute().result.units[0].status == "pending"
    with open_session(engine, preview=False) as session:
        assert session.view.observations[0].state == "drifted"
        assert session.view.observations[0].base.record is None
        command(session, SetApproval, "main:app.unit", True)
        with monkeypatch.context() as patch:
            if fail_acknowledgment:
                def fail(*args, **kwargs):
                    raise SyncBaseStoreError("acknowledgment unavailable")
                patch.setattr(SyncBaseStore, "replace", fail)
            result = session.execute().result
        assert all(step.kind == "unit-completion" for step in result.steps)
        assert result.units[0].status == ("execution-failed" if fail_acknowledgment else "converged")
    assert (tmp_path / "repo/packages/app/unit").read_bytes() == b"repo"
    assert (tmp_path / "live/unit").read_bytes() == b"live"
    with open_session(engine) as later:
        base = later.view.observations[0].base
        assert (base.record is not None) is not fail_acknowledgment
    assert not list((tmp_path / "state").rglob("manifest.json"))


@pytest.mark.parametrize("policy", ["push-only", "push-only-delete"])
def test_ineligible_drift_no_write_needs_approval_but_no_receipt(
    policy, tmp_path, monkeypatch
):
    live = b"repo" if policy == "push-only" else None
    engine = make_engine(tmp_path, monkeypatch, [("unit", policy, b"repo", live, "")])
    with open_session(engine, preview=False) as observed:
        observation = observed.view.observations[0]
    # Isolate the frozen classification while keeping real execution metadata.
    from dotman import sync_session
    observe = sync_session.observe_scope
    def drifted(*args, **kwargs):
        result = observe(*args, **kwargs)
        return replace(result, observations=(replace(result.observations[0], state="drifted"),))
    monkeypatch.setattr(sync_session, "observe_scope", drifted)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with open_session(engine, preview=False) as unapproved:
        assert unapproved.execute().result.units[0].status == "pending"
    with open_session(engine, preview=False) as approved:
        command(approved, SetApproval, observation.identity.canonical, True)
        proposal = approved.view.rows[0].proposal
        assert proposal.primary_source_change is None
        assert proposal.publication_effects == ()
        result = approved.execute().result
        assert result.units[0].status == "converged"
        assert result.steps == ()
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
