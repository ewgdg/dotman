from dataclasses import FrozenInstanceError, replace

import pytest

from dotman.sync_base_store import FilePresent
from dotman.sync_session import (
    CommandRejected, PrepareProposalReview, Preview, SetApproval, SetIncluded, SyncSession,
)
from tests.engine.test_sync_session import make_engine, open_session


def command(session, kind, *args):
    view = session.view
    return session.dispatch(kind(view.session_id, view.revision, *args))


def test_approval_is_opt_in_review_freezes_exact_outcome(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [
        ("unit", "push-only", b"repo", b"live", 'render = "printf rendered"'),
    ])
    with open_session(engine) as session:
        initial = session.view
        row = initial.rows[0]
        assert not row.approved
        assert row.proposal is None
        assert row.allowed_intents == ("use-repository",)
        reviewed = command(session, PrepareProposalReview, row.row_id)
        proposal = reviewed.view.rows[0].proposal
        assert proposal.repository == FilePresent(b"repo")
        assert proposal.live == FilePresent(b"rendered")
        assert proposal.primary_source_change is None
        assert [effect.kind for effect in proposal.publication_effects] == ["write"]
        assert not session.view.rows[0].approved
        with pytest.raises(FrozenInstanceError):
            proposal.live = FilePresent(b"other")
        command(session, SetApproval, row.row_id, True)
        assert session.view.rows[0].approved
        assert not initial.rows[0].approved
        command(session, SetIncluded, row.row_id, False)
        assert session.view.rows[0].approved
        assert not session.view.rows[0].included


def test_preview_reports_approved_frozen_outcome_without_mutation(tmp_path, monkeypatch):
    marker = tmp_path / "hook"
    engine = make_engine(tmp_path, monkeypatch, [
        ("unit", "push-only", b"repo", b"live",
         f'[targets.unit.hooks]\npre_push = "touch {marker}"'),
    ])
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with open_session(engine) as session:
        command(session, SetApproval, "main:app.unit", True)
        result = command(session, Preview).result
        assert result.units[0].status == "would-converge"
        assert not session.view.terminal
        assert isinstance(session.execute(), CommandRejected)
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
    assert not marker.exists()


def test_real_execution_publishes_frozen_render_and_never_base(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [
        ("unit", "push-only", b"repo", b"live", 'render = "printf rendered"'),
    ])
    with open_session(engine, preview=False) as session:
        command(session, SetApproval, "main:app.unit", True)
        (tmp_path / "repo/packages/app/unit").write_bytes(b"external")
        result = session.execute().result
        assert result.status == "completed"
        assert result.units[0].status == "converged"
        assert session.view.terminal
    assert (tmp_path / "live/unit").read_bytes() == b"rendered"
    assert (tmp_path / "repo/packages/app/unit").read_bytes() == b"external"
    with open_session(engine) as later:
        assert later.view.observations[0].base.record is None


def test_unapproved_and_excluded_do_not_publish(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [("unit", "push-only", b"repo", b"live", "")])
    with open_session(engine, preview=False) as session:
        result = session.execute().result
        assert result.units[0].status == "pending"
        assert result.status == "completed"
        assert result.exit_code == 0
    assert (tmp_path / "live/unit").read_bytes() == b"live"


def test_direct_agreement_distinct_from_approved_drift_no_write(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [("unit", "push-only", b"same", b"same", "")])
    with open_session(engine) as direct:
        assert not direct.view.rows
        observation = direct.view.observations[0]
    # The classification is frozen independently of materialization. This
    # exercises the no-effect completion boundary without inventing new intents.
    with SyncSession((replace(observation, state="drifted"),), preview=False) as session:
        command(session, SetApproval, observation.identity.canonical, True)
        assert session.view.rows[0].proposal.publication_effects == ()
        assert session.execute().result.units[0].status == "converged"


def test_guard_narrowed_eligible_drift_cannot_approve_in_this_slice(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [
        ("unit", "both", b"repo", b"live", '[targets.unit.hooks]\nguard_pull = "exit 100"'),
    ])
    with open_session(engine) as session:
        rejected = command(session, SetApproval, "main:app.unit", True)
        assert isinstance(rejected, CommandRejected)
        assert rejected.reason == "disallowed"


@pytest.mark.parametrize("policy,source", [("push-only", None), ("push-only-delete", b"retain")])
def test_approved_missing_outcome_deletes_only_live(policy, source, tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [("unit", policy, source, b"live", "")])
    with open_session(engine, preview=False) as session:
        command(session, SetApproval, "main:app.unit", True)
        assert [effect.kind for effect in session.view.rows[0].proposal.publication_effects] == ["delete"]
        assert session.execute().result.units[0].status == "converged"
    assert not (tmp_path / "live/unit").exists()
    path = tmp_path / "repo/packages/app/unit"
    assert path.read_bytes() == source if source is not None else not path.exists()


def test_prompt_link_replacement_is_typed_unapproved_materialization_failure(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [("unit", "push-only", b"repo", None, "")])
    referent = tmp_path / "referent"
    referent.write_bytes(b"live")
    (tmp_path / "live").mkdir()
    link = tmp_path / "live/unit"
    link.symlink_to(referent)
    with open_session(engine) as session:
        result = command(session, SetApproval, "main:app.unit", True)
        assert not result.view.rows[0].approved
        assert result.view.rows[0].diagnostics[0].code == "materialization-failed"
        assert command(session, Preview).result.status == "failed"
    assert link.is_symlink()
    assert referent.read_bytes() == b"live"


def test_approval_validation_and_cached_review(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [("unit", "push-only", b"repo", b"live", "")])
    with open_session(engine) as session:
        original = session.view
        assert command(session, SetApproval, "main:app.unit", 1).reason == "invalid"
        assert session.view is original
        assert command(session, PrepareProposalReview, "unknown").reason == "unknown-row"
        command(session, PrepareProposalReview, "main:app.unit")
        proposal = session.view.rows[0].proposal
        command(session, SetApproval, "main:app.unit", True)
        command(session, SetApproval, "main:app.unit", False)
        command(session, PrepareProposalReview, "main:app.unit")
        assert session.view.rows[0].proposal is proposal


def test_mode_only_effect_and_content_then_mode_are_frozen(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [
        ("mode", "push-only", b"same", b"same", 'chmod = "600"'),
        ("both", "push-only", b"repo", b"live", 'chmod = "600"'),
    ])
    for name in ("mode", "both"):
        (tmp_path / "live" / name).chmod(0o644)
    with open_session(engine, preview=False) as session:
        for row in session.view.rows:
            command(session, SetApproval, row.row_id, True)
        assert [[effect.kind for effect in row.proposal.publication_effects]
                for row in session.view.rows] == [["chmod"], ["write", "chmod"]]
        assert session.execute().result.status == "completed"
    assert (tmp_path / "live/mode").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "live/both").stat().st_mode & 0o777 == 0o600


def test_excluded_approved_proposal_does_not_publish(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [("unit", "push-only", b"repo", b"live", "")])
    with open_session(engine, preview=False) as session:
        command(session, SetApproval, "main:app.unit", True)
        command(session, SetIncluded, "main:app.unit", False)
        result = session.execute().result
        assert result.status == "completed"
        assert result.units[0].status == "excluded"
    assert (tmp_path / "live/unit").read_bytes() == b"live"


def test_pre_hook_failure_is_terminal_typed_and_releases_lock(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [
        ("unit", "push-only", b"repo", b"live",
         '[targets.unit.hooks]\npre_push = "exit 7"'),
    ])
    with open_session(engine, preview=False) as session:
        command(session, SetApproval, "main:app.unit", True)
        result = session.execute().result
        assert result.status == "failed"
        assert result.exit_code == 1
        assert result.units[0].status == "skipped"
        assert session.view.terminal
    assert (tmp_path / "live/unit").read_bytes() == b"live"
    with open_session(engine, preview=False) as later:
        assert not later.view.terminal


def test_publication_does_not_rerun_frozen_render(tmp_path, monkeypatch):
    marker = tmp_path / "render-count"
    engine = make_engine(tmp_path, monkeypatch, [
        ("unit", "push-only", b"repo", b"live",
         f'render = "echo render >> {marker}; printf rendered"'),
    ])
    with open_session(engine, preview=False) as session:
        assert marker.read_text().splitlines() == ["render"]
        command(session, PrepareProposalReview, "main:app.unit")
        command(session, SetApproval, "main:app.unit", True)
        command(session, Preview)
        assert session.execute().result.status == "completed"
    assert marker.read_text().splitlines() == ["render"]
    assert (tmp_path / "live/unit").read_bytes() == b"rendered"


def test_interrupted_materialization_is_typed_and_not_approved(tmp_path, monkeypatch):
    import dotman.sync_session as boundary

    engine = make_engine(tmp_path, monkeypatch, [("unit", "push-only", b"repo", b"live", "")])

    def interrupt(_observation):
        raise KeyboardInterrupt

    with open_session(engine, preview=False) as session:
        monkeypatch.setattr(boundary, "materialize", interrupt)
        changed = command(session, SetApproval, "main:app.unit", True)
        assert not changed.view.rows[0].approved
        assert changed.view.rows[0].diagnostics[0].code == "interrupted"
        preview = command(session, Preview).result
        assert preview.status == "aborted"
        assert preview.exit_code == 130
        result = session.execute().result
        assert result.status == "aborted"
        assert result.exit_code == 130
        assert session.view.terminal
    assert (tmp_path / "live/unit").read_bytes() == b"live"


def test_unapproval_preserves_materialization_failure(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [("unit", "push-only", b"repo", None, "")])
    referent = tmp_path / "referent"
    referent.write_bytes(b"live")
    (tmp_path / "live").mkdir()
    (tmp_path / "live/unit").symlink_to(referent)
    with open_session(engine) as session:
        failed = command(session, SetApproval, "main:app.unit", True)
        diagnostics = failed.view.rows[0].diagnostics
        assert diagnostics[0].code == "materialization-failed"
        unapproved = command(session, SetApproval, "main:app.unit", False)
        assert unapproved.view.rows[0].diagnostics == diagnostics
        assert command(session, Preview).result.status == "failed"


def test_successful_materialization_clears_prior_failure(tmp_path, monkeypatch):
    import dotman.sync_session as boundary

    engine = make_engine(tmp_path, monkeypatch, [("unit", "push-only", b"repo", b"live", "")])
    materialize = boundary.materialize

    def fail(_observation):
        raise OSError("temporary failure")

    with open_session(engine) as session:
        monkeypatch.setattr(boundary, "materialize", fail)
        assert command(session, SetApproval, "main:app.unit", True).view.rows[0].diagnostics
        monkeypatch.setattr(boundary, "materialize", materialize)
        recovered = command(session, SetApproval, "main:app.unit", True).view.rows[0]
        assert recovered.approved
        assert recovered.proposal is not None
        assert recovered.diagnostics == ()
        assert command(session, Preview).result.status == "completed"


def test_post_hook_failure_preserves_convergence_and_reports_actual_steps(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [
        ("unit", "push-only", b"repo", b"live",
         '[targets.unit.hooks]\npost_push = "exit 7"'),
    ])
    with open_session(engine, preview=False) as session:
        command(session, SetApproval, "main:app.unit", True)
        assert command(session, Preview).result.steps == ()
        result = session.execute().result
        assert result.status == "failed"
        assert result.units[0].status == "converged"
        assert result.diagnostics
        assert any(step.step.action == "post_push" and step.status == "failed"
                   for step in result.steps)
    assert (tmp_path / "live/unit").read_bytes() == b"repo"


def test_session_preserves_follow_symlink_publication_policy(tmp_path, monkeypatch):
    from dotman.engine import DotmanEngine

    engine = make_engine(tmp_path, monkeypatch, [("unit", "push-only", b"repo", None, "")])
    engine = DotmanEngine(replace(engine.config, file_symlink_mode="follow"))
    referent = tmp_path / "referent"
    referent.write_bytes(b"live")
    (tmp_path / "live").mkdir()
    link = tmp_path / "live/unit"
    link.symlink_to(referent)
    with open_session(engine, preview=False) as session:
        command(session, SetApproval, "main:app.unit", True)
        assert session.execute().result.status == "completed"
    assert link.is_symlink()
    assert referent.read_bytes() == b"repo"
