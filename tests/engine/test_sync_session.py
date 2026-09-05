from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from dotman.command_runtime import ArgvCommand, CommandRequest, current_command_runtime
from dotman.engine import DotmanEngine
from dotman.sync_session import SyncSession
from dotman.sync_base_store import FilePresent, Missing
from tests.helpers import write_named_manager_config, write_tracked_packages_state


def make_engine(tmp_path, monkeypatch, targets):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    repo = tmp_path / "repo"
    package = repo / "packages" / "app"
    package.mkdir(parents=True)
    (repo / "profiles").mkdir()
    (repo / "profiles" / "default.toml").write_text("")
    lines = ['id = "app"']
    for name, policy, source, live, extra in targets:
        lines += [
            f"[targets.{name}]",
            f'source = "{name}"',
            f'path = "{tmp_path / "live" / name}"',
            'type = "file"',
            f'sync_policy = "{policy}"',
            extra,
        ]
        if source is not None:
            (package / name).write_bytes(source)
        if live is not None:
            (tmp_path / "live").mkdir(exist_ok=True)
            (tmp_path / "live" / name).write_bytes(live)
    (package / "package.toml").write_text("\n".join(lines))
    runtime = current_command_runtime()
    for args in [
        ("init", "-q"),
        ("add", "."),
        (
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.test",
            "commit",
            "-qm",
            "fixture",
        ),
    ]:
        result = runtime.run(CommandRequest(ArgvCommand(("git", *args)), cwd=repo))
        assert result.exit_code == 0, result.stderr
    write_tracked_packages_state(
        tmp_path / "state", repo_name="main", entries=[("app", "default")]
    )
    for directory in (
        tmp_path / "state/dotman",
        tmp_path / "state/dotman/repos",
        tmp_path / "state/dotman/repos/main",
    ):
        directory.chmod(0o700)
    config = write_named_manager_config(tmp_path, {"main": repo})
    return DotmanEngine.from_config_path(config)


def open_session(engine, *, preview=True, **kwargs):
    session = engine.open_sync_session(
        engine.resolve_sync_scope(), preview=preview, **kwargs
    )
    assert isinstance(session, SyncSession), session
    return session


def test_open_freezes_classification_typed_missing_and_stable_rows(
    tmp_path, monkeypatch
):
    engine = make_engine(
        tmp_path,
        monkeypatch,
        [
            ("agree", "both", b"same", b"same", ""),
            ("drift", "push-only", b"repo", b"live", ""),
            ("missing", "pull-only", None, b"", ""),
        ],
    )
    session = open_session(engine)
    view = session.view
    assert [unit.state for unit in view.observations] == [
        "directly-in-sync",
        "drifted",
        "drifted",
    ]
    assert [row.row_id for row in view.rows] == ["main:app.drift", "main:app.missing"]
    assert view.observations[2].repository == Missing()
    assert view.observations[2].live == FilePresent(b"")
    (tmp_path / "repo/packages/app/drift").write_bytes(b"new")
    (tmp_path / "live/drift").write_bytes(b"new")
    assert session.view == view
    assert view.observations[1].repository == FilePresent(b"repo")
    with pytest.raises(FrozenInstanceError):
        view.rows[0].included = False


@pytest.mark.parametrize(
    "policy,extra,source,live,state,comparison",
    [
        (
            "push-only",
            'render = "printf rendered"\n[targets.unit.compare]\nrepo = "exit 9"\nlive = "exit 9"',
            b"repo",
            b"rendered",
            "directly-in-sync",
            b"rendered",
        ),
        (
            "both",
            '[targets.unit.compare]\nrepo = "printf equal"\nlive = "printf equal"',
            b"repo",
            b"live",
            "directly-in-sync",
            b"equal",
        ),
        (
            "pull-only",
            'capture = "printf captured"',
            b"captured",
            b"live",
            "directly-in-sync",
            b"captured",
        ),
        (
            "push-only-delete",
            'render = "exit 9"',
            b"repo",
            None,
            "directly-in-sync",
            None,
        ),
        ("push-only", "", None, b"", "drifted", None),
        ("both", "", None, None, "directly-in-sync", None),
    ],
)
def test_policy_defined_comparison(
    policy, extra, source, live, state, comparison, tmp_path, monkeypatch
):
    engine = make_engine(tmp_path, monkeypatch, [("unit", policy, source, live, extra)])
    observation = open_session(engine).view.observations[0]
    assert observation.state == state
    assert observation.comparison_repository == (
        Missing() if comparison is None else FilePresent(comparison)
    )


def test_observation_failure_is_visible_and_does_not_discard_peers(
    tmp_path, monkeypatch
):
    engine = make_engine(
        tmp_path,
        monkeypatch,
        [
            ("bad", "push-only", b"source", b"live", 'render = "exit 7"'),
            ("good", "pull-only", b"repo", b"live", ""),
            ("last", "push-only", b"repo", b"live", ""),
        ],
    )
    view = open_session(engine).view
    assert [unit.identity.target_name for unit in view.observations] == [
        "bad",
        "good",
        "last",
    ]
    assert [row.kind for row in view.rows] == ["diagnostic", "drift", "drift"]
    assert view.rows[0].included is False
    assert view.rows[0].observation.diagnostics[0].code == "observation-failed"


def test_commands_are_revision_checked_immutable_and_terminal(tmp_path, monkeypatch):
    from dotman.sync_session import (
        Abort,
        CommandAccepted,
        CommandRejected,
        Execute,
        SetIncluded,
    )

    engine = make_engine(
        tmp_path, monkeypatch, [("unit", "both", b"repo", b"live", "")]
    )
    session = open_session(engine)
    original = session.view
    result = session.dispatch(
        SetIncluded(original.session_id, original.revision, "main:app.unit", False)
    )
    assert isinstance(result, CommandAccepted)
    assert not result.view.rows[0].included
    assert original.rows[0].included
    changed = session.view
    stale = session.dispatch(
        SetIncluded(original.session_id, original.revision, "main:app.unit", True)
    )
    assert isinstance(stale, CommandRejected)
    assert stale.reason == "stale"
    assert session.view is changed
    for command, reason in [
        (
            SetIncluded("other", changed.revision, "main:app.unit", True),
            "foreign-session",
        ),
        (
            SetIncluded(changed.session_id, changed.revision, "unknown", True),
            "unknown-row",
        ),
        (
            SetIncluded(changed.session_id, changed.revision, "main:app.unit", 1),
            "invalid",
        ),
        (Execute(changed.session_id, changed.revision), "preview"),
        (object(), "invalid"),
    ]:
        rejection = session.dispatch(command)
        assert isinstance(rejection, CommandRejected)
        assert rejection.reason == reason
        assert rejection.view is changed
        assert session.view is changed
    ended = session.dispatch(Abort(changed.session_id, changed.revision))
    assert isinstance(ended, CommandAccepted)
    assert ended.result.status == "aborted"
    assert ended.view.terminal
    assert ended.view.allowed_commands == ()
    rejected = session.dispatch(Abort(ended.view.session_id, ended.view.revision))
    assert isinstance(rejected, CommandRejected)
    assert rejected.reason == "terminal"


def test_diagnostic_rows_cannot_be_included_and_execute_is_honest(
    tmp_path, monkeypatch
):
    from dotman.sync_session import CommandRejected, SetIncluded

    engine = make_engine(
        tmp_path,
        monkeypatch,
        [
            ("bad", "push-only", b"repo", b"live", 'render = "exit 7"'),
            ("good", "both", b"repo", b"live", ""),
        ],
    )
    session = open_session(engine, preview=False)
    view = session.view
    rejected = session.dispatch(
        SetIncluded(view.session_id, view.revision, "main:app.bad", True)
    )
    assert isinstance(rejected, CommandRejected)
    assert rejected.reason == "disallowed"
    assert session.view is view
    result = session.execute()
    assert result.result.status == "failed"
    assert [unit.status for unit in result.result.units] == [
        "observation-failed",
        "pending",
    ]
    assert result.view.terminal
    assert (tmp_path / "live/good").read_bytes() == b"live"


def test_execute_with_drift_does_not_claim_convergence(tmp_path, monkeypatch):
    engine = make_engine(
        tmp_path, monkeypatch, [("unit", "both", b"repo", b"live", "")]
    )
    session = open_session(engine, preview=False)
    result = session.execute()
    assert result.result.status == "incomplete"
    assert result.result.exit_code == 1
    assert result.result.units[0].status == "pending"
    assert session.view.terminal


def test_real_sessions_share_manager_lock_preview_does_not_take_or_create_it(
    tmp_path, monkeypatch
):
    from dotman.sync_session import SessionOpenFailed

    engine = make_engine(
        tmp_path, monkeypatch, [("unit", "push-only", b"same", b"same", "")]
    )
    state_root = tmp_path / "state/dotman"
    before = {
        p.relative_to(state_root): p.read_bytes()
        for p in state_root.rglob("*")
        if p.is_file()
    }
    preview = open_session(engine)
    after = {
        p.relative_to(state_root): p.read_bytes()
        for p in state_root.rglob("*")
        if p.is_file()
    }
    assert before == after
    preview.abort()
    session = open_session(engine, preview=False)
    blocked = engine.open_sync_session(engine.resolve_sync_scope())
    assert isinstance(blocked, SessionOpenFailed)
    assert blocked.diagnostic.code == "operation-busy"
    with open_session(engine) as concurrent_preview:
        assert concurrent_preview.view.preview
    session.abort()
    with open_session(engine, preview=False) as next_session:
        assert not next_session.view.terminal
    assert next_session.view.terminal


def test_real_execute_releases_operation_lock(tmp_path, monkeypatch):
    engine = make_engine(
        tmp_path, monkeypatch, [("unit", "push-only", b"same", b"same", "")]
    )
    session = open_session(engine, preview=False)
    assert session.execute().result.status == "completed"
    with open_session(engine, preview=False):
        pass


def test_base_facts_freeze_and_real_direct_agreement_acknowledges(
    tmp_path, monkeypatch
):
    engine = make_engine(
        tmp_path, monkeypatch, [("unit", "both", b"same", b"same", "")]
    )
    with open_session(engine) as preview:
        before = preview.view.observations[0]
        assert before.base.status == "unavailable"
        assert before.base.reason == "absent"
        assert not before.base.acknowledged
        assert before.git.primary_clean
        assert before.git.committed == FilePresent(b"same")
        assert len(before.git.head.commit_oid) == 40
    with open_session(engine, preview=False) as real:
        observation = real.view.observations[0]
        assert observation.base.acknowledged
        frozen = real.view
        (tmp_path / "repo/packages/app/unit").write_bytes(b"changed")
        assert real.view == frozen
    with open_session(engine) as later:
        base = later.view.observations[0].base
        assert base.status == "usable"
        assert base.record.payload == FilePresent(b"same")
        assert base.record.envelope.provenance == "exact"


def test_real_direct_dirty_agreement_uses_committed_conservative_base(
    tmp_path, monkeypatch
):
    engine = make_engine(
        tmp_path, monkeypatch, [("unit", "both", b"committed", b"dirty", "")]
    )
    (tmp_path / "repo/packages/app/unit").write_bytes(b"dirty")
    with open_session(engine, preview=False) as session:
        unit = session.view.observations[0]
        assert unit.state == "directly-in-sync"
        assert unit.base.acknowledged
        assert not unit.git.primary_clean
    with open_session(engine) as later:
        record = later.view.observations[0].base.record
        assert record.payload == FilePresent(b"committed")
        assert record.envelope.provenance == "conservative"


def test_eligibility_cleanup_precedes_guards_but_preview_preserves_base(
    tmp_path, monkeypatch
):
    engine = make_engine(
        tmp_path, monkeypatch, [("unit", "both", b"same", b"same", "")]
    )
    with open_session(engine, preview=False):
        pass
    manifest = tmp_path / "repo/packages/app/package.toml"
    manifest.write_text(
        manifest.read_text().replace(
            'sync_policy = "both"', 'sync_policy = "push-only"'
        )
        + '\n[targets.unit.hooks]\nguard_push = "exit 100"'
    )
    engine = DotmanEngine(engine.config)
    with open_session(engine) as preview:
        assert preview.view.observations[0].diagnostics[0].code == "no-route"
    with open_session(engine, preview=False) as real:
        assert real.view.observations[0].base.deleted
    manifest.write_text(
        manifest.read_text()
        .replace('sync_policy = "push-only"', 'sync_policy = "both"')
        .split("[targets.unit.hooks]")[0]
    )
    engine = DotmanEngine(engine.config)
    with open_session(engine) as later:
        assert later.view.observations[0].base.reason == "absent"


@pytest.mark.parametrize(
    "guard,policy,expected",
    [
        ("guard_pull", "both", "push-only"),
        ("guard_push", "both", "pull-only"),
        ("guard_push", "push-only", "no-route"),
        ("guard_pull", "pull-only", "no-route"),
    ],
)
def test_guards_freeze_effective_capability_without_changing_eligibility(
    guard, policy, expected, tmp_path, monkeypatch
):
    engine = make_engine(
        tmp_path,
        monkeypatch,
        [
            (
                "unit",
                policy,
                b"repo",
                b"live",
                f'[targets.unit.hooks]\n{guard} = "exit 100"',
            ),
        ],
    )
    with open_session(engine) as session:
        unit = session.view.observations[0]
        assert unit.effective_policy == expected
        assert unit.configured_policy == policy
        assert unit.base.status == (
            "not-applicable" if policy == "push-only" else "unavailable"
        )
        if expected == "no-route":
            assert unit.state == "observation-failed"
            assert session.view.rows[0].allowed_commands == ()


def test_hard_guard_failure_and_interruption_are_typed_and_release_lock(
    tmp_path, monkeypatch
):
    from dotman.sync_session import SessionOpenFailed

    engine = make_engine(
        tmp_path,
        monkeypatch,
        [
            (
                "unit",
                "both",
                b"repo",
                b"live",
                '[targets.unit.hooks]\nguard_push = "exit 7"',
            ),
        ],
    )
    failed = engine.open_sync_session(engine.resolve_sync_scope())
    assert isinstance(failed, SessionOpenFailed)
    assert failed.diagnostic.code == "planning-failed"
    manifest = tmp_path / "repo/packages/app/package.toml"
    manifest.write_text(manifest.read_text().replace("exit 7", "exit 0"))
    with open_session(DotmanEngine(engine.config), preview=False):
        pass


def test_observation_git_failure_remains_visible_nonapprovable(tmp_path, monkeypatch):
    engine = make_engine(
        tmp_path, monkeypatch, [("unit", "both", b"same", b"same", "")]
    )
    # A repository without a current commit has no ancestry proof.
    (tmp_path / "repo/.git/HEAD").write_text("ref: refs/heads/unborn\n")
    with open_session(engine) as session:
        assert session.view.observations[0].state == "observation-failed"
        assert session.view.rows[0].kind == "diagnostic"
        assert session.view.rows[0].observation.diagnostics[0].code == "git-failed"


def test_preview_uses_frozen_endpoint_copies_for_command_views_and_events(
    tmp_path, monkeypatch
):
    from dotman.command_runtime import CommandResult, MemoryCommandRuntime, ShellCommand
    from dotman.sync_session import (
        SessionOpened,
        SessionChanged,
        SessionFinished,
        SetIncluded,
    )

    engine = make_engine(
        tmp_path,
        monkeypatch,
        [
            (
                "unit",
                "both",
                b"repo",
                b"live",
                '[targets.unit.compare]\nrepo = "repo-view"\nlive = "live-view"',
            )
        ],
    )
    actual = engine.command_runtime
    calls = []

    def respond(request):
        runtime.queue(respond)
        if isinstance(request.command, ShellCommand):
            calls.append(request.command.source)
            repository_copy = Path(request.env["DOTMAN_REPO_PATH"])
            live_copy = Path(request.env["DOTMAN_LIVE_PATH"])
            assert repository_copy.read_bytes() == b"repo"
            assert live_copy.read_bytes() == b"live"
            (tmp_path / "repo/packages/app/unit").write_bytes(b"changed")
            (tmp_path / "live/unit").write_bytes(b"changed")
            return CommandResult(0, stdout=request.command.source.encode())
        return actual.run(request)

    runtime = MemoryCommandRuntime([respond])
    engine = DotmanEngine(engine.config, command_runtime=runtime)
    events = []
    with open_session(engine, event_sink=events.append) as session:
        original = session.view
        session.dispatch(
            SetIncluded(original.session_id, original.revision, "main:app.unit", False)
        )
        session.abort()
        assert original.observations[0].repository == FilePresent(b"repo")
        assert original.observations[0].live == FilePresent(b"live")
    assert calls == ["repo-view", "live-view"]
    assert [type(event) for event in events] == [
        SessionOpened,
        SessionChanged,
        SessionFinished,
    ]


def test_open_interruption_is_typed_and_releases_lock(tmp_path, monkeypatch):
    from dotman.command_runtime import CommandResult, MemoryCommandRuntime, ShellCommand
    from dotman.sync_session import SessionOpenFailed

    engine = make_engine(
        tmp_path,
        monkeypatch,
        [("unit", "push-only", b"repo", b"live", 'render = "interrupt"')],
    )
    actual = engine.command_runtime

    def respond(request):
        runtime.queue(respond)
        return (
            CommandResult(130)
            if isinstance(request.command, ShellCommand)
            else actual.run(request)
        )

    runtime = MemoryCommandRuntime([respond])
    interrupted = DotmanEngine(
        engine.config, command_runtime=runtime
    ).open_sync_session(engine.resolve_sync_scope())
    assert isinstance(interrupted, SessionOpenFailed)
    assert interrupted.diagnostic.code == "interrupted"
    with open_session(engine, preview=False):
        pass


def test_open_callback_programming_error_escapes_and_releases_lock(
    tmp_path, monkeypatch
):
    engine = make_engine(
        tmp_path, monkeypatch, [("unit", "push-only", b"same", b"same", "")]
    )

    def broken_sink(_event):
        raise ValueError("broken adapter")

    with pytest.raises(ValueError, match="broken adapter"):
        engine.open_sync_session(engine.resolve_sync_scope(), event_sink=broken_sink)
    with open_session(engine, preview=False):
        pass


def test_direct_agreement_acknowledgment_failure_stays_visible(tmp_path, monkeypatch):
    from dotman.command_runtime import CommandResult, MemoryCommandRuntime

    engine = make_engine(
        tmp_path, monkeypatch, [("unit", "both", b"same", b"same", "")]
    )
    actual = engine.command_runtime

    def respond(request):
        runtime.queue(respond)
        if "cat-file" in request.command.arguments:
            return CommandResult(1, stderr=b"object access failed")
        return actual.run(request)

    runtime = MemoryCommandRuntime([respond])
    engine = DotmanEngine(engine.config, command_runtime=runtime)
    with open_session(engine, preview=False) as session:
        assert session.view.observations[0].state == "directly-in-sync"
        row = session.view.rows[0]
        assert row.kind == "diagnostic"
        assert row.allowed_commands == ()
        assert row.observation.diagnostics[0].code == "base-acknowledgment-failed"
        assert not row.observation.base.acknowledged
        assert session.execute().result.status == "failed"


@pytest.mark.parametrize("shape", ["symlink", "fifo", "directory"])
def test_bad_repository_endpoint_is_a_typed_visible_failure(
    shape, tmp_path, monkeypatch
):
    import os

    engine = make_engine(tmp_path, monkeypatch, [("unit", "both", None, b"live", "")])
    source = tmp_path / "repo/packages/app/unit"
    if shape == "symlink":
        source.symlink_to(tmp_path / "live/unit")
    elif shape == "fifo":
        os.mkfifo(source)
    else:
        source.mkdir()
    with open_session(engine) as session:
        unit = session.view.observations[0]
        assert unit.state == "observation-failed"
        assert session.view.rows[0].allowed_commands == ()
        assert unit.repository is None


def test_follow_dangling_live_link_is_missing_but_prompt_requires_valid_referent(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    engine = make_engine(tmp_path, monkeypatch, [("unit", "both", b"repo", None, "")])
    (tmp_path / "live").mkdir()
    (tmp_path / "live/unit").symlink_to(tmp_path / "missing-referent")
    with open_session(engine) as prompt:
        assert prompt.view.observations[0].state == "observation-failed"
    with open_session(
        DotmanEngine(replace(engine.config, file_symlink_mode="follow"))
    ) as follow:
        assert follow.view.observations[0].live == Missing()
        assert follow.view.observations[0].state == "drifted"


def test_preview_rejects_unsafe_existing_store_instead_of_hiding_it(
    tmp_path, monkeypatch
):
    from dotman.sync_session import SessionOpenFailed
    from dotman.sync_base_store import DATABASE_FILE_NAME

    engine = make_engine(
        tmp_path, monkeypatch, [("unit", "both", b"same", b"same", "")]
    )
    directory = tmp_path / "state/dotman/repos/main"
    (directory / DATABASE_FILE_NAME).symlink_to(tmp_path / "absent")
    result = engine.open_sync_session(engine.resolve_sync_scope(), preview=True)
    assert isinstance(result, SessionOpenFailed)
    assert result.diagnostic.code == "base-failed"
    assert (directory / DATABASE_FILE_NAME).is_symlink()


def test_push_only_exact_live_outcome_includes_configured_file_mode(
    tmp_path, monkeypatch
):
    engine = make_engine(
        tmp_path,
        monkeypatch,
        [("unit", "push-only", b"same", b"same", 'chmod = "600"')],
    )
    (tmp_path / "live/unit").chmod(0o644)
    with open_session(engine) as session:
        unit = session.view.observations[0]
        assert unit.state == "drifted"
        assert unit.live_mode == 0o644
        assert unit.chmod == "600"


def test_unit_local_base_git_inspection_failure_preserves_unrelated_observation(
    tmp_path, monkeypatch
):
    from dotman.command_runtime import CommandResult, MemoryCommandRuntime

    engine = make_engine(
        tmp_path,
        monkeypatch,
        [
            ("first", "both", b"same", b"same", ""),
            ("second", "both", b"same", b"same", ""),
        ],
    )
    with open_session(engine, preview=False):
        pass
    actual = engine.command_runtime
    fail_next_lookup = True

    def respond(request):
        nonlocal fail_next_lookup
        runtime.queue(respond)
        if "cat-file" in request.command.arguments and fail_next_lookup:
            fail_next_lookup = False
            return CommandResult(1, stderr=b"object access failed")
        return actual.run(request)

    runtime = MemoryCommandRuntime([respond])
    with open_session(DotmanEngine(engine.config, command_runtime=runtime)) as session:
        assert [unit.state for unit in session.view.observations] == [
            "observation-failed",
            "directly-in-sync",
        ]
        assert [row.row_id for row in session.view.rows] == ["main:app.first"]


def test_preview_preserves_managed_files_git_and_existing_base_bytes(
    tmp_path, monkeypatch
):
    engine = make_engine(
        tmp_path, monkeypatch, [("unit", "both", b"same", b"same", "")]
    )
    with open_session(engine, preview=False):
        pass

    def snapshot():
        return {
            path.relative_to(tmp_path): (path.read_bytes(), path.stat().st_mode)
            for path in tmp_path.rglob("*")
            if path.is_file()
        }

    before = snapshot()
    with open_session(engine) as preview:
        assert preview.view.observations[0].base.status == "usable"
        assert not preview.view.observations[0].base.acknowledged
    assert snapshot() == before


def test_preview_does_not_treat_unreadable_store_directory_as_absent(
    tmp_path, monkeypatch
):
    from dotman.sync_session import SessionOpenFailed

    engine = make_engine(
        tmp_path, monkeypatch, [("unit", "both", b"same", b"same", "")]
    )
    with open_session(engine, preview=False):
        pass
    scope = engine.resolve_sync_scope()
    directory = tmp_path / "state/dotman/repos/main"
    directory.chmod(0)
    try:
        result = engine.open_sync_session(scope, preview=True)
        assert isinstance(result, SessionOpenFailed)
    finally:
        directory.chmod(0o700)


def test_projection_diagnostics_do_not_expose_private_staging_paths(
    tmp_path, monkeypatch
):
    engine = make_engine(
        tmp_path,
        monkeypatch,
        [
            (
                "unit",
                "push-only",
                b"repo",
                b"live",
                """render = 'printf "%s" "$DOTMAN_REPO_PATH" >&2; exit 1'""",
            ),
        ],
    )
    with open_session(engine) as session:
        message = session.view.rows[0].observation.diagnostics[0].message
        assert "dotman-observation-" not in message
        assert str(tmp_path / "repo/packages/app/unit") in message
