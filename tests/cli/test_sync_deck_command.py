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
        assert "1 approved units" in deck.confirmation_text()
        assert "1 live writes" in deck.confirmation_text()
        deck.back()
        assert not deck.confirming
        assert session.view == frozen
        deck.move(1)
        deck.select()
        assert all(row.approved for row in session.view.rows)


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
    def interrupt(*args, **kwargs):
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
        "stage", "kind", "action", "scope", "scope_identity", "repo", "package_id",
        "status", "skip_reason", "exit_code", "error",
    } for step in payload["stages"])
    assert all(step["stage"] == "live-publication" for step in payload["stages"])
    assert any(step["action"] == "post_push" and step["status"] == "failed"
               and step["exit_code"] == 7 for step in payload["stages"])


@pytest.mark.parametrize("dry_run", [False, True])
def test_unattended_both_fallback_reports_reason(tmp_path, monkeypatch, capsys, dry_run):
    engine = make_engine(tmp_path, monkeypatch, [
        ("push", "push-only", b"repo", b"live", ""),
        ("both", "both", b"repo", b"live", ""),
    ])
    assert runner_for(engine).run(arguments(dry_run=dry_run)) == 0
    payload = json.loads(capsys.readouterr().out)
    unit = next(unit for unit in payload["sync_units"] if unit["policy"] == "both")
    assert unit["resolution_intent"] == "use-live"
    assert unit["fallback_reason"] == "absent"
    assert unit["allowed_intents"] == ["use-repository", "use-live"]
    assert (tmp_path / "repo/packages/app/both").read_bytes() == (b"repo" if dry_run else b"live")
    assert (tmp_path / "live/push").read_bytes() == (b"live" if dry_run else b"repo")


def test_json_failed_hook_identifies_exact_instance_target(tmp_path, monkeypatch, capsys):
    from dotman.engine import DotmanEngine

    engine = make_engine(tmp_path, monkeypatch, [
        ("unit", "push-only", b"repo", b"live",
         '[targets.unit.hooks]\npost_push = "exit 7"'),
    ])
    manifest = tmp_path / "repo/packages/app/package.toml"
    manifest.write_text(manifest.read_text().replace(
        'id = "app"', 'id = "app"\nbinding_mode = "multi_instance"',
    ))
    engine = DotmanEngine(engine.config)
    assert runner_for(engine).run(arguments(dry_run=False)) == 1
    payload = json.loads(capsys.readouterr().out)
    failed_hook = next(step for step in payload["stages"] if step["status"] == "failed")
    assert failed_hook["scope_identity"] == "main:app<default>.unit"
    assert failed_hook["scope"] == "target"
    assert payload["sync_units"][0]["identity"] == failed_hook["scope_identity"]
    assert payload["sync_units"][0]["result"] == "converged"

@pytest.mark.parametrize("dry_run", [True, False])
def test_unattended_topology_blocker_fails_with_typed_diagnostic(tmp_path, monkeypatch, capsys, dry_run):
    from tests.engine.test_sync_directory_observation import directory_engine, put

    engine = directory_engine(tmp_path, monkeypatch, policy="push-only",
                              extra='[targets.tree.ignore]\npatterns = ["node/private"]')
    put(tmp_path / "repo/packages/app/tree", "node", b"new")
    blocked = put(tmp_path / "live/tree", "node/private", b"private")
    assert runner_for(engine).run(arguments(dry_run=dry_run)) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "failed"
    assert payload["summary"]["diagnostics"][0]["code"] == "structural-conflict"
    assert blocked.read_bytes() == b"private"


def test_human_summary_reports_completion_counts_and_canonical_identity(tmp_path, monkeypatch, capsys):
    engine = make_engine(tmp_path, monkeypatch, [("unit", "push-only", b"repo", b"live", "")])
    assert runner_for(engine).run(arguments(json_output=False)) == 0
    output = capsys.readouterr().out
    assert "main:app.unit" in output
    assert "completed" in output
    assert "1 approved units" in output
    assert "1 live writes" in output

@pytest.mark.parametrize("projection", [
    'render = "cat $DOTMAN_SOURCE >&2; printf \'%s\' $DOTMAN_SOURCE >&2; exit 7"',
    'capture = "cat $DOTMAN_SOURCE >&2; printf \'%s\' $DOTMAN_SOURCE >&2; exit 7"\ncompare = { repo = "raw", live = "raw" }',
    'compare = { repo = "cat $DOTMAN_SOURCE >&2; printf \'%s\' $DOTMAN_SOURCE >&2; exit 7", live = "raw" }',
])
def test_failed_projection_json_never_exposes_command_output_or_workspace(tmp_path, monkeypatch, capsys, projection):
    engine = make_engine(tmp_path, monkeypatch, [
        ("unit", "both", b"repo-private-content", b"live-private-content", projection),
    ])
    assert runner_for(engine).run(arguments()) == 1
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["status"] == "failed"
    assert "private-content" not in output
    assert "dotman-projection-" not in output
    assert "dotman-comparison-" not in output

@pytest.mark.parametrize("full_path", [False, True])
def test_sync_review_honors_full_path_option(tmp_path, monkeypatch, capsys, full_path):
    import sys
    from dotman import sync_deck
    from dotman.diff_review import display_review_path

    engine = make_engine(tmp_path, monkeypatch, [("unit", "push-only", b"repo", b"live", "")])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

    def inspect(session, **kwargs):
        deck = sync_deck.CommandDeck(session, use_color=False)
        deck.open_review()
        expected = display_review_path(tmp_path / "live/unit", compact=not full_path)
        assert f"Live path: {expected}" in deck.review_text()
        return True

    monkeypatch.setattr(sync_deck, "run_command_deck", inspect)
    assert runner_for(engine).run(arguments(unattended=False, json_output=False, full_path=full_path)) == 0

@pytest.mark.parametrize("command", ["guard", "probe"])
def test_failed_planning_commands_do_not_copy_output_into_json(tmp_path, monkeypatch, capsys, command):
    engine = make_engine(tmp_path, monkeypatch, [("unit", "push-only", b"repo", b"live", "")])
    manifest = tmp_path / "repo/packages/app/package.toml"
    if command == "guard":
        manifest.write_text(manifest.read_text() + '\n[targets.unit.hooks]\nguard_push = "printf private-command-output >&2; exit 7"\n')
    else:
        manifest.write_text('id = "app"\n[targets.unit]\nprobe = "printf private-command-output >&2; exit 7"\nsync_policy = "push-only"\n')
    from dotman.engine import DotmanEngine
    engine = DotmanEngine.from_config_path(engine.config.config_path)
    assert runner_for(engine).run(arguments()) == 1
    output = capsys.readouterr().out
    assert json.loads(output)["summary"]["diagnostics"]
    assert "private-command-output" not in output

def test_unattended_sync_propagates_mode_to_both_hook_families(tmp_path, monkeypatch, capsys):
    engine = make_engine(tmp_path, monkeypatch, [
        ("unit", "both", b"repo", b"live",
         'render = "sed s/live/published/ $DOTMAN_SOURCE"\ncompare = { repo = "raw", live = "raw" }\n'
         '[targets.unit.hooks]\npre_pull = "test \\"$DOTMAN_UNATTENDED\\" = 1"\n'
         'pre_push = "test \\"$DOTMAN_UNATTENDED\\" = 1"'),
    ])
    assert runner_for(engine).run(arguments(dry_run=False)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert {step["action"] for step in payload["stages"] if step["kind"] == "hook"} == {"pre_pull", "pre_push"}
    assert all(step["status"] == "ok" for step in payload["stages"])
