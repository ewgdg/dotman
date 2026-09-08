import pytest

from dotman.sync_base_store import FilePresent
from dotman.sync_session import PrepareProposalReview, SetApproval, SetResolutionIntent, RetryMaterialization
from tests.engine.test_sync_convergence import command
from tests.engine.test_sync_session import make_engine, open_session

BASE = b"first\nmiddle\nlast\n"
REPO = b"repository\nmiddle\nlast\n"
LIVE = b"first\nmiddle\nlive\n"
MERGED = b"repository\nmiddle\nlive\n"


def established(tmp_path, monkeypatch, extra=""):
    engine = make_engine(tmp_path, monkeypatch, [("unit", "both", BASE, BASE, extra)])
    with open_session(engine, preview=False):
        pass
    (tmp_path / "repo/packages/app/unit").write_bytes(REPO)
    (tmp_path / "live/unit").write_bytes(LIVE)
    return engine


def test_merge_default_is_lazy_and_executes_frozen_three_way_outcome(tmp_path, monkeypatch):
    marker = tmp_path / "captures"
    engine = established(tmp_path, monkeypatch,
        f'capture = "echo capture >> {marker}; cat $DOTMAN_LIVE_PATH"\ncompare = {{ repo = "raw", live = "raw" }}')
    with open_session(engine, preview=False) as session:
        row = session.view.rows[0]
        assert row.intent == "merge"
        assert set(row.allowed_intents) == {"merge", "use-live", "use-repository"}
        assert row.fallback_reason is None and row.proposal is None
        assert not marker.exists()
        (tmp_path / "repo/packages/app/unit").write_bytes(b"external")
        (tmp_path / "live/unit").write_bytes(b"external")
        command(session, PrepareProposalReview, row.row_id)
        assert not session.view.rows[0].approved
        command(session, SetApproval, row.row_id, True)
        proposal = session.view.rows[0].proposal
        assert proposal.repository == FilePresent(MERGED)
        assert proposal.live == FilePresent(MERGED)
        assert proposal.capture == FilePresent(LIVE)
        assert proposal.primary_source_change == FilePresent(MERGED)
        assert proposal.publication_effects[0].content == MERGED
        assert session.execute().result.units[0].status == "converged"
    assert marker.read_text().splitlines() == ["capture"]
    assert (tmp_path / "repo/packages/app/unit").read_bytes() == MERGED
    assert (tmp_path / "live/unit").read_bytes() == MERGED
    with open_session(engine) as session:
        assert session.view.observations[0].base.record.payload == FilePresent(BASE)


def test_without_base_live_fallback_and_disallowed_merge_are_visible(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [("unit", "both", b"repo", b"live", "")])
    with open_session(engine) as session:
        row = session.view.rows[0]
        assert row.intent == "use-live" and row.fallback_reason == "absent"
        assert set(row.allowed_intents) == {"use-live", "use-repository"}
        before = session.view
        assert command(session, SetResolutionIntent, row.row_id, "merge").reason == "disallowed"
        assert session.view == before


@pytest.mark.parametrize("intent,expected", [("use-repository", REPO), ("use-live", LIVE)])
def test_intent_effects_and_capture_cache(tmp_path, monkeypatch, intent, expected):
    marker = tmp_path / "captures"
    engine = established(tmp_path, monkeypatch,
        f'capture = "echo capture >> {marker}; cat $DOTMAN_LIVE_PATH"\ncompare = {{ repo = "raw", live = "raw" }}')
    with open_session(engine, preview=False) as session:
        command(session, SetResolutionIntent, "main:app.unit", intent)
        assert not marker.exists()
        command(session, SetApproval, "main:app.unit", True)
        row = session.view.rows[0]
        assert row.proposal.repository == FilePresent(expected)
        assert (row.proposal.primary_source_change is None) == (intent == "use-repository")
        assert bool(row.proposal.publication_effects) == (intent == "use-repository")
        assert session.execute().result.units[0].status == "converged"
    assert marker.exists() == (intent == "use-live")


def test_conflict_is_typed_retryable_and_does_not_change_intent(tmp_path, monkeypatch):
    engine = established(tmp_path, monkeypatch)
    (tmp_path / "live/unit").write_bytes(b"conflict\nmiddle\nlast\n")
    with open_session(engine) as session:
        command(session, SetApproval, "main:app.unit", True)
        row = session.view.rows[0]
        assert row.intent == "merge" and row.proposal is None and not row.approved
        assert row.diagnostics[0].code == "reconciliation-conflict"
        command(session, RetryMaterialization, row.row_id)
        assert session.view.rows[0].diagnostics[0].code == "reconciliation-conflict"
        command(session, SetResolutionIntent, row.row_id, "use-live")
        assert session.view.rows[0].proposal is None
        command(session, SetApproval, row.row_id, True)
        assert session.view.rows[0].approved


def test_standing_approval_rematerializes_intent_without_recapture(tmp_path, monkeypatch):
    marker = tmp_path / "captures"
    engine = established(tmp_path, monkeypatch,
        f'capture = "echo capture >> {marker}; cat $DOTMAN_LIVE_PATH"\ncompare = {{ repo = "raw", live = "raw" }}')
    with open_session(engine) as session:
        command(session, SetApproval, "main:app.unit", True)
        for intent in ("use-live", "use-repository", "merge"):
            command(session, SetResolutionIntent, "main:app.unit", intent)
            row = session.view.rows[0]
            assert row.approved and row.proposal.intent == intent
        assert marker.read_text().splitlines() == ["capture"]


def test_use_live_publishes_rendered_capture_but_acknowledges_frozen_capture_input(tmp_path, monkeypatch):
    from dotman.sync_base_lifecycle import SyncBaseLifecycle
    extra = 'capture = "sed s/live/repository/ $DOTMAN_LIVE_PATH"\nrender = "sed s/repository/published/ $DOTMAN_SOURCE"\ncompare = { repo = "raw", live = "raw" }'
    engine = make_engine(tmp_path, monkeypatch, [("unit", "both", b"old", b"live", extra)])
    facts = []
    complete = SyncBaseLifecycle.complete
    def record_fact(self, frozen, proposal):
        facts.append(proposal.live_fact)
        assert (tmp_path / "repo/packages/app/unit").read_bytes() == b"repository"
        assert (tmp_path / "live/unit").read_bytes() == b"published"
        return complete(self, frozen, proposal)
    monkeypatch.setattr(SyncBaseLifecycle, "complete", record_fact)
    with open_session(engine, preview=False) as session:
        command(session, SetApproval, "main:app.unit", True)
        proposal = session.view.rows[0].proposal
        assert proposal.repository == FilePresent(b"repository")
        assert proposal.live == FilePresent(b"published")
        result = session.execute().result
        assert result.units[0].status == "converged"
        assert [step.stage for step in result.steps] == ["repository-apply", "live-publication"]
    assert facts == ["frozen-live-capture-input"]


@pytest.mark.parametrize("intent,fact", [
    ("merge", "frozen-merged-live-outcome"),
    ("use-repository", "published-repository-outcome"),
])
def test_acknowledgment_occurs_after_own_effects_and_has_intent_fact(tmp_path, monkeypatch, intent, fact):
    from dotman.sync_base_lifecycle import SyncBaseLifecycle
    engine = established(tmp_path, monkeypatch)
    facts = []
    complete = SyncBaseLifecycle.complete
    def record_fact(self, frozen, proposal):
        facts.append(proposal.live_fact)
        expected = MERGED if intent == "merge" else REPO
        assert (tmp_path / "live/unit").read_bytes() == expected
        assert (tmp_path / "repo/packages/app/unit").read_bytes() == expected
        return complete(self, frozen, proposal)
    monkeypatch.setattr(SyncBaseLifecycle, "complete", record_fact)
    with open_session(engine, preview=False) as session:
        command(session, SetResolutionIntent, "main:app.unit", intent)
        command(session, SetApproval, "main:app.unit", True)
        assert session.execute().result.units[0].status == "converged"
    assert facts == [fact]


def test_publication_failure_keeps_previous_base_after_repository_apply(tmp_path, monkeypatch):
    from dotman import file_access
    from dotman.sync_base_lifecycle import SyncBaseLifecycle
    engine = established(tmp_path, monkeypatch)
    write = file_access.write_bytes_atomic
    def fail_live(path, content, **kwargs):
        if path == tmp_path / "live/unit":
            raise OSError("live unavailable")
        return write(path, content, **kwargs)
    monkeypatch.setattr(file_access, "write_bytes_atomic", fail_live)
    def forbidden(*args, **kwargs):
        pytest.fail("Failed unit must not acknowledge")
    monkeypatch.setattr(SyncBaseLifecycle, "complete", forbidden)
    with open_session(engine, preview=False) as session:
        before = session.view.observations[0].base.record
        command(session, SetApproval, "main:app.unit", True)
        result = session.execute().result
        assert result.units[0].status == "execution-failed"
    assert (tmp_path / "repo/packages/app/unit").read_bytes() == MERGED
    assert (tmp_path / "live/unit").read_bytes() == LIVE
    with open_session(engine) as later:
        assert later.view.observations[0].base.record == before


def test_capture_failure_is_typed_and_retry_preserves_frozen_intent_and_endpoints(tmp_path, monkeypatch):
    ready = tmp_path / "ready"
    engine = established(tmp_path, monkeypatch,
        f'capture = "test -f {ready} || exit 8; cat $DOTMAN_LIVE_PATH"\ncompare = {{ repo = "raw", live = "raw" }}')
    with open_session(engine) as session:
        command(session, SetApproval, "main:app.unit", True)
        row = session.view.rows[0]
        assert row.intent == "merge" and not row.approved and row.proposal is None
        assert row.diagnostics[0].code == "capture-failed"
        ready.touch()
        (tmp_path / "live/unit").write_bytes(b"external")
        command(session, RetryMaterialization, row.row_id)
        row = session.view.rows[0]
        assert row.intent == "merge" and not row.approved and not row.diagnostics
        assert row.proposal.repository == FilePresent(MERGED)


def test_reconciliation_provider_failure_is_typed_and_retry_reuses_capture(tmp_path, monkeypatch):
    from dotman.command_runtime import ArgvCommand, CommandResult
    marker = tmp_path / "captures"
    engine = established(tmp_path, monkeypatch,
        f'capture = "echo capture >> {marker}; cat $DOTMAN_LIVE_PATH"\ncompare = {{ repo = "raw", live = "raw" }}')
    with open_session(engine) as session:
        runtime = session._context.projection.command_runtime
        run = runtime.run
        def failing_merge(request):
            if isinstance(request.command, ArgvCommand) and request.command.arguments[:2] == ("git", "merge-file"):
                return CommandResult(255, stderr=b"provider failed")
            return run(request)
        monkeypatch.setattr(runtime, "run", failing_merge)
        command(session, SetApproval, "main:app.unit", True)
        row = session.view.rows[0]
        assert row.intent == "merge" and not row.approved
        assert row.diagnostics[0].code == "reconciliation-failed"
        monkeypatch.setattr(runtime, "run", run)
        command(session, RetryMaterialization, row.row_id)
        assert session.view.rows[0].proposal.repository == FilePresent(MERGED)
        assert marker.read_text().splitlines() == ["capture"]


def test_failed_standing_intent_change_clears_only_that_approval(tmp_path, monkeypatch):
    engine = established(tmp_path, monkeypatch)
    (tmp_path / "live/unit").write_bytes(b"conflict\nmiddle\nlast\n")
    with open_session(engine) as session:
        command(session, SetResolutionIntent, "main:app.unit", "use-repository")
        command(session, SetApproval, "main:app.unit", True)
        command(session, SetResolutionIntent, "main:app.unit", "merge")
        row = session.view.rows[0]
        assert row.intent == "merge" and not row.approved and row.proposal is None
        assert row.diagnostics[0].code == "reconciliation-conflict"


def test_use_repository_reuses_frozen_render_comparison(tmp_path, monkeypatch):
    marker = tmp_path / "renders"
    engine = make_engine(tmp_path, monkeypatch, [
        ("unit", "both", b"repository", b"live",
         f'render = "echo render >> {marker}; cat $DOTMAN_SOURCE"\ncompare = {{ repo = "render", live = "raw" }}'),
    ])
    with open_session(engine) as session:
        assert marker.read_text().splitlines() == ["render"]
        command(session, SetResolutionIntent, "main:app.unit", "use-repository")
        command(session, SetApproval, "main:app.unit", True)
        assert session.view.rows[0].proposal.live == FilePresent(b"repository")
        assert marker.read_text().splitlines() == ["render"]


def test_failed_acknowledgment_keeps_previous_base_after_both_effects(tmp_path, monkeypatch):
    from dotman.sync_base_store import SyncBaseStore, SyncBaseStoreError
    engine = established(tmp_path, monkeypatch)
    with open_session(engine, preview=False) as session:
        before = session.view.observations[0].base.record
        command(session, SetApproval, "main:app.unit", True)
        def fail(*args, **kwargs):
            raise SyncBaseStoreError("ack unavailable")
        with monkeypatch.context() as patch:
            patch.setattr(SyncBaseStore, "replace", fail)
            result = session.execute().result
        assert result.status == "failed"
        assert result.units[0].status == "execution-failed"
        assert result.units[0].diagnostics[0].code == "base-acknowledgment-failed"
    assert (tmp_path / "repo/packages/app/unit").read_bytes() == MERGED
    assert (tmp_path / "live/unit").read_bytes() == MERGED
    with open_session(engine) as later:
        assert later.view.observations[0].base.record == before


def test_earlier_published_base_commits_before_later_publication_failure(tmp_path, monkeypatch):
    from dotman import file_access
    engine = make_engine(tmp_path, monkeypatch, [
        (name, "both", BASE, BASE, "") for name in ("first", "second")
    ])
    with open_session(engine, preview=False):
        pass
    for name in ("first", "second"):
        (tmp_path / f"repo/packages/app/{name}").write_bytes(REPO)
        (tmp_path / f"live/{name}").write_bytes(LIVE)
    write = file_access.write_bytes_atomic
    def fail_second(path, content, **kwargs):
        if path == tmp_path / "live/second":
            raise OSError("second failed")
        return write(path, content, **kwargs)
    monkeypatch.setattr(file_access, "write_bytes_atomic", fail_second)
    with open_session(engine, preview=False) as session:
        before = [unit.base.record for unit in session.view.observations]
        for row in session.view.rows:
            command(session, SetApproval, row.row_id, True)
        result = session.execute().result
        assert [unit.status for unit in result.units] == ["converged", "execution-failed"]
    with open_session(engine) as later:
        after = [unit.base.record for unit in later.view.observations]
        assert after[0].envelope.provenance == "conservative"
        assert before[0].envelope.provenance == "exact"
        assert after[1] == before[1]
