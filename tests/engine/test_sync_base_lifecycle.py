from __future__ import annotations

from dataclasses import replace

import pytest

from dotman.command_runtime import ArgvCommand, CommandRequest, ProductionCommandRuntime
from dotman.models import ResolvedSyncTarget
from dotman.sync_base_lifecycle import (
    BaseInputs,
    BaseProfileContext,
    BaseUnit,
    ProposalCompletion,
    SyncBaseGit,
    SyncBaseLifecycle,
)
from dotman.sync_base_store import FilePresent, Missing, SyncBaseStore


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    runtime = ProductionCommandRuntime()

    def git(*args):
        result = runtime.run(CommandRequest(ArgvCommand(("git", *args)), cwd=root))
        assert result.exit_code == 0, result.stderr_text
        return result.stdout.strip().decode()

    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (root / "source").write_bytes(b"committed\n")
    git("add", ".")
    git("commit", "-qm", "initial")
    return root, runtime, git


def unit(policy="both", child=None, source="source", **inputs):
    return BaseUnit(
        ResolvedSyncTarget("main", "app", "config", child_path=child),
        source,
        policy,
        BaseInputs(**inputs),
    )


def test_direct_agreement_freezes_commit_checkout_and_exact_status(
    repository, tmp_path
):
    root, runtime, git = repository
    facts = SyncBaseGit(root, runtime)
    head = facts.freeze_head()
    (frozen,) = facts.freeze_units(head, (unit(),))
    # Neither a later working-tree edit nor a new HEAD changes frozen ancestry.
    (root / "source").write_bytes(b"later\n")
    git("commit", "-qam", "later")
    with SyncBaseStore.open(tmp_path / "state" / "dotman", "main") as store:
        lifecycle = SyncBaseLifecycle(store, facts, operation="sync")
        result = lifecycle.direct_agreement(frozen)
        assert result.acknowledged
        record = store.read(unit().identity_bytes)
        assert record.payload == FilePresent(b"committed\n")
        assert record.envelope.commit_oid == head.commit_oid
        assert record.envelope.object_format == head.object_format
        assert record.envelope.provenance == "exact"
        inspected = lifecycle.inspect(unit(), head)
        assert inspected.status == "usable"
        assert inspected.record == record


@pytest.mark.parametrize(
    "policy,eligible",
    [
        ("push-only", False),
        ("push-only-delete", False),
        ("pull-only", True),
        ("both", True),
    ],
)
def test_configured_policy_controls_no_write_completion(
    repository, tmp_path, policy, eligible
):
    root, runtime, _ = repository
    facts = SyncBaseGit(root, runtime)
    (frozen,) = facts.freeze_units(facts.freeze_head(), (unit(policy),))
    with SyncBaseStore.open(tmp_path / "state" / "dotman", "main") as store:
        lifecycle = SyncBaseLifecycle(store, facts, operation="sync")
        result = lifecycle.complete(
            frozen, ProposalCompletion(approved=True, intent="use-live")
        )
        assert result.converged
        assert result.acknowledged is eligible
        assert (store.read(unit().identity_bytes) is not None) is eligible
        assert result.live_fact == ("approved-no-write" if eligible else None)


@pytest.mark.parametrize("child", [None, "nested/tool"])
@pytest.mark.parametrize("present", [False, True])
def test_shared_payload_lifecycle_and_missing_usability(
    repository, tmp_path, child, present
):
    from dotman.sync_base_store import DirectoryChildPresent

    root, runtime, git = repository
    current = unit(child=child, source="tool")
    if present:
        (root / "tool").write_bytes(b"#!/bin/sh\n")
        (root / "tool").chmod(0o755)
        git("add", "tool")
        git("commit", "-qm", "executable")
    facts = SyncBaseGit(root, runtime)
    head = facts.freeze_head()
    (frozen,) = facts.freeze_units(head, (current,))
    with SyncBaseStore.open(tmp_path / "state" / "dotman", "main") as store:
        lifecycle = SyncBaseLifecycle(store, facts, operation="sync")
        assert lifecycle.complete(
            frozen, ProposalCompletion(approved=True, intent="merge")
        ).converged
        inspection = lifecycle.inspect(current, head)
        assert inspection.status == "usable"
        expected = (
            Missing()
            if not present
            else (
                FilePresent(b"#!/bin/sh\n")
                if child is None
                else DirectoryChildPresent(b"#!/bin/sh\n", True)
            )
        )
        assert inspection.record.payload == expected


@pytest.mark.parametrize(
    "dirty", ["clean", "unstaged", "staged", "untracked", "ignored", "additional"]
)
@pytest.mark.parametrize("primary_changed", [False, True])
def test_provenance_uses_only_frozen_primary_status_and_final_primary_change(
    repository,
    tmp_path,
    dirty,
    primary_changed,
):
    root, runtime, git = repository
    current = unit()
    if dirty in ("unstaged", "staged"):
        (root / "source").write_bytes(b"working\n")
        if dirty == "staged":
            git("add", "source")
    elif dirty in ("untracked", "ignored"):
        current = unit(source="new")
        (root / "new").write_bytes(b"new\n")
        if dirty == "ignored":
            (root / ".gitignore").write_text("new\n")
    elif dirty == "additional":
        (root / "additional").write_bytes(b"unrelated\n")
    facts = SyncBaseGit(root, runtime)
    (frozen,) = facts.freeze_units(facts.freeze_head(), (current,))
    # Cleaning later must not sharpen frozen dirty status.
    git("reset", "--hard", "-q", "HEAD")
    with SyncBaseStore.open(tmp_path / "state" / "dotman", "main") as store:
        lifecycle = SyncBaseLifecycle(store, facts, operation="sync")
        proposal = ProposalCompletion(
            approved=True,
            intent="editor",
            primary_effect="succeeded" if primary_changed else "not-required",
        )
        assert lifecycle.complete(frozen, proposal).converged
        record = store.read(current.identity_bytes)
        exact = dirty in ("clean", "additional") and not primary_changed
        assert record.envelope.provenance == ("exact" if exact else "conservative")
        assert record.payload == (
            Missing()
            if dirty in ("untracked", "ignored")
            else FilePresent(b"committed\n")
        )


def test_checkout_uses_committed_attributes_and_preserves_real_index(
    repository, tmp_path
):
    root, runtime, git = repository
    (root / ".gitattributes").write_text("source text eol=crlf\n")
    git("add", ".gitattributes")
    git("commit", "-qm", "checkout conversion")
    (root / ".gitattributes").write_text("source -text\n")
    (root / "source").write_bytes(b"staged\n")
    git("add", ".")
    index = (root / ".git" / "index").read_bytes()
    facts = SyncBaseGit(root, runtime)
    (frozen,) = facts.freeze_units(facts.freeze_head(), (unit(),))
    assert frozen.payload == FilePresent(b"committed\r\n")
    assert (root / ".git" / "index").read_bytes() == index
    assert (root / "source").read_bytes() == b"staged\n"


@pytest.mark.parametrize(
    "intent,fact",
    [
        ("use-repository", "published-repository-outcome"),
        ("use-live", "frozen-live-capture-input"),
        ("merge", "frozen-merged-live-outcome"),
        ("editor", "rematerialized-policy-outcome"),
    ],
)
def test_each_approved_intent_acknowledges_fact_not_proposal_bytes(
    repository, tmp_path, intent, fact
):
    root, runtime, _ = repository
    facts = SyncBaseGit(root, runtime)
    (frozen,) = facts.freeze_units(facts.freeze_head(), (unit(),))
    with SyncBaseStore.open(tmp_path / "state" / "dotman", "main") as store:
        lifecycle = SyncBaseLifecycle(store, facts, operation="sync")
        result = lifecycle.complete(
            frozen,
            ProposalCompletion(
                approved=True,
                intent=intent,
                primary_effect="succeeded",
                publication_effects="succeeded",
            ),
        )
        assert result.converged and result.acknowledged
        assert result.live_fact == fact
        assert store.read(unit().identity_bytes).payload == FilePresent(b"committed\n")


@pytest.mark.parametrize("operation", ["push", "pull", "sync"])
@pytest.mark.parametrize("suppressed", ["preview", "reuse", "removed", "none"])
def test_direct_boundary_excludes_preview_reuse_and_removed_units(
    repository,
    tmp_path,
    operation,
    suppressed,
):
    root, runtime, _ = repository
    facts = SyncBaseGit(root, runtime)
    (frozen,) = facts.freeze_units(facts.freeze_head(), (unit(),))
    with SyncBaseStore.open(tmp_path / "state" / "dotman", "main") as store:
        lifecycle = SyncBaseLifecycle(
            store, facts, operation=operation, preview=suppressed == "preview"
        )
        result = lifecycle.direct_agreement(
            frozen,
            fresh_observation=suppressed != "reuse",
            participating=suppressed != "removed",
        )
        assert result.acknowledged is (suppressed == "none")
        assert not result.converged
        assert (store.read(unit().identity_bytes) is not None) is (suppressed == "none")


@pytest.mark.parametrize(
    "change",
    [
        {"approved": False},
        {"included": False},
        {"materialized": False},
        {"primary_effect": "pending"},
        {"primary_effect": "failed"},
        {"publication_effects": "pending"},
        {"publication_effects": "failed"},
    ],
)
def test_incomplete_proposals_preserve_prior_base(repository, tmp_path, change):
    root, runtime, git = repository
    facts = SyncBaseGit(root, runtime)
    (prior,) = facts.freeze_units(facts.freeze_head(), (unit(),))
    with SyncBaseStore.open(tmp_path / "state" / "dotman", "main") as store:
        lifecycle = SyncBaseLifecycle(store, facts, operation="sync")
        assert lifecycle.direct_agreement(prior).acknowledged
        original = store.read(unit().identity_bytes)
        (root / "source").write_bytes(b"new commit\n")
        git("commit", "-qam", "new")
        (current,) = facts.freeze_units(facts.freeze_head(), (unit(),))
        result = lifecycle.complete(
            current,
            ProposalCompletion(intent="use-live", **{"approved": True, **change}),
        )
        assert not result.converged and not result.acknowledged
        assert store.read(unit().identity_bytes) == original


@pytest.mark.parametrize(
    "operation,preview,deleted",
    [
        ("push", False, True),
        ("sync", False, True),
        ("pull", False, False),
        ("sync", True, False),
        ("push", True, False),
    ],
)
@pytest.mark.parametrize("policy", ["push-only", "push-only-delete"])
def test_policy_cleanup_precedes_guards_review_approval_and_effects(
    repository,
    tmp_path,
    operation,
    preview,
    deleted,
    policy,
):
    root, runtime, _ = repository
    facts = SyncBaseGit(root, runtime)
    (frozen,) = facts.freeze_units(facts.freeze_head(), (unit(),))
    with SyncBaseStore.open(tmp_path / "state" / "dotman", "main") as store:
        store.replace(frozen.record())
        other = replace(
            frozen,
            unit=replace(
                unit(), identity=ResolvedSyncTarget("main", "other", "config")
            ),
        )
        store.replace(other.record())
        lifecycle = SyncBaseLifecycle(
            store, facts, operation=operation, preview=preview
        )
        assert lifecycle.selected_policy_resolved(unit(policy)).deleted is deleted
        assert (store.read(unit().identity_bytes) is None) is deleted
        assert store.read(other.unit.identity_bytes) is not None
        # A failed/excluded Proposal does not undo maintenance.
        changed = replace(frozen, unit=unit(policy))
        assert not lifecycle.complete(
            changed,
            ProposalCompletion(approved=True, intent="use-repository", included=False),
        ).converged
        assert (store.read(unit().identity_bytes) is None) is deleted


def test_eligible_policy_change_and_transient_one_sided_completion_preserve_eligibility(
    repository, tmp_path
):
    root, runtime, _ = repository
    facts = SyncBaseGit(root, runtime)
    (frozen,) = facts.freeze_units(facts.freeze_head(), (unit(),))
    with SyncBaseStore.open(tmp_path / "state" / "dotman", "main") as store:
        lifecycle = SyncBaseLifecycle(store, facts, operation="sync")
        assert lifecycle.direct_agreement(frozen).acknowledged
        assert not lifecycle.selected_policy_resolved(unit("pull-only")).deleted
        assert lifecycle.inspect(unit("pull-only"), frozen.head).status == "usable"
        # guard_pull narrowing changes effects, not the configured policy operand.
        result = lifecycle.complete(
            frozen,
            ProposalCompletion(
                approved=True,
                intent="use-repository",
                publication_effects="succeeded",
            ),
        )
        assert result.acknowledged
        assert store.read(unit().identity_bytes).envelope.provenance == "exact"


def test_changed_pull_and_preview_never_acknowledge_proposals(repository, tmp_path):
    root, runtime, _ = repository
    facts = SyncBaseGit(root, runtime)
    (frozen,) = facts.freeze_units(facts.freeze_head(), (unit(),))
    with SyncBaseStore.open(tmp_path / "state" / "dotman", "main") as store:
        for operation, preview in (("pull", False), ("sync", True)):
            lifecycle = SyncBaseLifecycle(
                store, facts, operation=operation, preview=preview
            )
            result = lifecycle.complete(
                frozen,
                ProposalCompletion(
                    approved=True, intent="use-live", primary_effect="succeeded"
                ),
            )
            assert not result.acknowledged
            assert not result.converged
            assert store.read(unit().identity_bytes) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"render": "jinja"},
        {"capture": "custom"},
        {"profile_context": BaseProfileContext({"host": "other"})},
        {"path_rules": ("new-rule",)},
        {"file_symlink_mode": "follow"},
        {"dir_symlink_mode": "follow"},
    ],
)
def test_effective_interpretation_changes_make_base_unavailable(
    repository, tmp_path, changes
):
    root, runtime, _ = repository
    facts = SyncBaseGit(root, runtime)
    (frozen,) = facts.freeze_units(facts.freeze_head(), (unit(),))
    with SyncBaseStore.open(tmp_path / "state" / "dotman", "main") as store:
        store.replace(frozen.record())
        result = SyncBaseLifecycle(store, facts, operation="sync").inspect(
            unit(**changes), frozen.head
        )
        assert result.reason == "inputs_changed"
        assert result.record is None


def test_inspection_validates_identity_shape_policy_and_fingerprint_without_writes(
    repository, tmp_path
):
    from dotman.sync_base_store import DirectoryChildPresent

    root, runtime, _ = repository
    facts = SyncBaseGit(root, runtime)
    (frozen,) = facts.freeze_units(facts.freeze_head(), (unit(),))
    with SyncBaseStore.open(tmp_path / "state" / "dotman", "main") as store:
        lifecycle = SyncBaseLifecycle(store, facts, operation="sync")
        assert lifecycle.inspect(unit(), frozen.head).reason == "absent"
        store.replace(
            replace(
                frozen.record(), payload=DirectoryChildPresent(b"wrong shape", False)
            )
        )
        before = store.database_path.read_bytes()
        assert lifecycle.inspect(unit(), frozen.head).reason == "record_corrupt"
        assert (
            lifecycle.inspect(unit("push-only"), frozen.head).status == "not-applicable"
        )
        assert lifecycle.inspect(unit("push-only"), frozen.head).reason == "ineligible"
        assert store.database_path.read_bytes() == before
        store.replace(frozen.record())
        assert (
            lifecycle.inspect(
                replace(unit(), primary_source="elsewhere"), frozen.head
            ).reason
            == "inputs_changed"
        )
        other = replace(unit(), identity=ResolvedSyncTarget("main", "other", "config"))
        assert lifecycle.inspect(other, frozen.head).reason == "absent"


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("commit_oid", "not an oid", "record_corrupt"),
        ("object_format", "unknown", "record_corrupt"),
        ("fingerprint", "truncated", "record_corrupt"),
        ("provenance", "guessed", "record_corrupt"),
        ("content", b"corrupted", "payload_corrupt"),
    ],
)
def test_corrupt_record_and_payload_reasons_do_not_reveal_stale_metadata(
    repository,
    tmp_path,
    field,
    value,
    reason,
):
    import sqlite3

    root, runtime, _ = repository
    facts = SyncBaseGit(root, runtime)
    (frozen,) = facts.freeze_units(facts.freeze_head(), (unit(),))
    state = tmp_path / "state" / "dotman"
    with SyncBaseStore.open(state, "main") as store:
        store.replace(frozen.record())
        path = store.database_path
    with sqlite3.connect(path) as connection:
        table = "payloads" if field == "content" else "base_records"
        connection.execute(f"UPDATE {table} SET {field} = ?", (value,))
    before = path.read_bytes()
    with SyncBaseStore.open(state, "main", read_only=True) as reader:
        result = SyncBaseLifecycle(reader, facts, operation="sync").inspect(
            unit(), frozen.head
        )
        assert result.status == "unavailable"
        assert result.reason == reason
        assert result.record is None
    assert path.read_bytes() == before


def test_missing_commit_unrelated_history_and_frozen_head_ancestry(
    repository, tmp_path
):
    root, runtime, git = repository
    facts = SyncBaseGit(root, runtime)
    original_head = facts.freeze_head()
    (frozen,) = facts.freeze_units(original_head, (unit(),))
    with SyncBaseStore.open(tmp_path / "state" / "dotman", "main") as store:
        lifecycle = SyncBaseLifecycle(store, facts, operation="sync")
        store.replace(frozen.record())
        (root / "source").write_bytes(b"descendant\n")
        git("commit", "-qam", "descendant")
        descendant = facts.freeze_head()
        assert lifecycle.inspect(unit(), descendant).status == "usable"
        git("checkout", "--orphan", "unrelated")
        git("commit", "-qm", "unrelated")
        assert (
            lifecycle.inspect(unit(), facts.freeze_head()).reason == "history_changed"
        )
        # Inspection is anchored to its argument, never a later repository HEAD.
        assert lifecycle.inspect(unit(), descendant).status == "usable"
        bad = replace(
            frozen.record(),
            envelope=replace(frozen.record().envelope, commit_oid="f" * 40),
        )
        store.replace(bad)
        result = lifecycle.inspect(unit(), original_head)
        assert result.reason == "commit_missing" and result.record is None
        blob = git("rev-parse", f"{original_head.commit_oid}:source")
        store.replace(replace(bad, envelope=replace(bad.envelope, commit_oid=blob)))
        assert lifecycle.inspect(unit(), original_head).reason == "commit_missing"


def test_failed_replacement_preserves_prior_record_and_earlier_unit_commit(
    repository, tmp_path, monkeypatch
):
    from dotman.sync_base_store import SyncBaseStoreError

    root, runtime, git = repository
    facts = SyncBaseGit(root, runtime)
    first = unit()
    second = replace(first, identity=ResolvedSyncTarget("main", "app", "second"))
    old_first, old_second = facts.freeze_units(facts.freeze_head(), (first, second))
    with SyncBaseStore.open(tmp_path / "state" / "dotman", "main") as store:
        lifecycle = SyncBaseLifecycle(store, facts, operation="sync")
        assert lifecycle.direct_agreement(old_first).acknowledged
        assert lifecycle.direct_agreement(old_second).acknowledged
        original = store.read(second.identity_bytes)
        (root / "source").write_bytes(b"new\n")
        git("commit", "-qam", "new")
        new_first, new_second = facts.freeze_units(facts.freeze_head(), (first, second))
        assert lifecycle.complete(
            new_first, ProposalCompletion(approved=True, intent="use-live")
        ).converged
        committed_first = store.read(first.identity_bytes)

        def fail_before_commit(*args):
            raise SyncBaseStoreError("injected pre-commit failure")

        # Fault after SQL replacement, before COMMIT: exercise real rollback.
        with monkeypatch.context() as fault:
            fault.setattr(store, "_garbage_collect_payload", fail_before_commit)
            result = lifecycle.complete(
                new_second, ProposalCompletion(approved=True, intent="merge")
            )
        assert not result.converged and not result.acknowledged
        assert isinstance(result.failure, SyncBaseStoreError)
        assert store.read(second.identity_bytes) == original
        assert store.read(first.identity_bytes) == committed_first
        assert committed_first.envelope.commit_oid == new_first.head.commit_oid


def test_checkout_and_status_handle_literal_newline_and_metacharacter_paths(repository):
    root, runtime, git = repository
    sources = ("nested/a[1]\nfile", "nested/a1\nfile", "-option")
    for source in sources:
        path = root / source
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(source.encode())
    git("add", ".")
    git("commit", "-qm", "unusual paths")
    (root / sources[1]).write_bytes(b"dirty neighbor")
    facts = SyncBaseGit(root, runtime)
    units = tuple(unit(source=path, child=path) for path in (sources[0], sources[2]))
    frozen = facts.freeze_units(facts.freeze_head(), units)
    assert all(item.primary_clean for item in frozen)
    assert tuple(item.payload.content for item in frozen) == tuple(
        item.primary_source.encode() for item in units
    )


def test_git_environment_cannot_redirect_frozen_repository(
    repository, tmp_path, monkeypatch
):
    root, runtime, git = repository
    expected = git("rev-parse", "HEAD")
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "not-a-repo"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "not-an-index"))
    facts = SyncBaseGit(root, runtime)
    head = facts.freeze_head()
    assert head.commit_oid == expected
    (frozen,) = facts.freeze_units(head, (unit(),))
    assert frozen.payload == FilePresent(b"committed\n")
    assert not (tmp_path / "not-an-index").exists()


def test_git_failure_is_not_misreported_as_missing_commit(repository, tmp_path):
    from dotman.sync_base_lifecycle import SyncBaseGitError

    root, runtime, _ = repository
    facts = SyncBaseGit(root, runtime)
    (frozen,) = facts.freeze_units(facts.freeze_head(), (unit(),))
    with SyncBaseStore.open(tmp_path / "state" / "dotman", "main") as store:
        store.replace(frozen.record())
        # The object database is no longer a usable Git repository.
        (root / ".git").rename(root / "saved-git")
        lifecycle = SyncBaseLifecycle(store, facts, operation="sync")
        with pytest.raises(SyncBaseGitError):
            lifecycle.inspect(unit(), frozen.head)


@pytest.mark.parametrize("child", [None, "tool"])
def test_sha256_repository_envelope(repository, tmp_path, child):
    root, runtime, _ = repository
    # A separate real repository uses Git's other supported object format.
    sha = root / "sha256"
    sha.mkdir()
    result = runtime.run(
        CommandRequest(
            ArgvCommand(("git", "init", "-q", "--object-format=sha256")), cwd=sha
        )
    )
    assert result.exit_code == 0, result.stderr_text
    for args in (
        ("config", "user.email", "test@example.com"),
        ("config", "user.name", "Test"),
        ("commit", "--allow-empty", "-qm", "initial"),
    ):
        result = runtime.run(CommandRequest(ArgvCommand(("git", *args)), cwd=sha))
        assert result.exit_code == 0, result.stderr_text
    facts = SyncBaseGit(sha, runtime)
    head = facts.freeze_head()
    (frozen,) = facts.freeze_units(head, (unit(child=child),))
    assert head.object_format == "sha256" and len(head.commit_oid) == 64
    with SyncBaseStore.open(tmp_path / "state" / "dotman", "main") as store:
        lifecycle = SyncBaseLifecycle(store, facts, operation="sync")
        assert lifecycle.direct_agreement(frozen).acknowledged
        assert lifecycle.inspect(unit(child=child), head).status == "usable"


def test_one_batched_path_scoped_status_observation_for_file_and_child(repository):
    root, runtime, git = repository
    (root / "child").write_bytes(b"child")
    git("add", "child")
    git("commit", "-qm", "child")
    requests = []

    class RecordingRuntime:
        def run(self, request):
            requests.append(request)
            return runtime.run(request)

    facts = SyncBaseGit(root, RecordingRuntime())
    frozen = facts.freeze_units(
        facts.freeze_head(), (unit(), unit(child="child", source="child"))
    )
    status_requests = [
        request for request in requests if "status" in request.command.arguments
    ]
    assert len(status_requests) == 1
    arguments = status_requests[0].command.arguments
    assert arguments[arguments.index("--") + 1 :] == ("source", "child")
    assert all(item.primary_clean for item in frozen)


@pytest.mark.parametrize("kind", ["symlink", "directory"])
def test_committed_nonregular_primary_source_cannot_be_acknowledged(repository, kind):
    from dotman.sync_base_lifecycle import SyncBaseGitError

    root, runtime, git = repository
    if kind == "symlink":
        (root / "unsafe").symlink_to("source")
    else:
        (root / "unsafe").mkdir()
        (root / "unsafe" / "child").write_bytes(b"child")
    git("add", ".")
    git("commit", "-qm", "nonregular source")
    facts = SyncBaseGit(root, runtime)
    (frozen,) = facts.freeze_units(facts.freeze_head(), (unit(source="unsafe"),))
    assert isinstance(frozen.failure, SyncBaseGitError)
    assert frozen.payload is None


def test_real_ancestry_ignores_replace_refs(repository, tmp_path):
    root, runtime, git = repository
    facts = SyncBaseGit(root, runtime)
    original = facts.freeze_head()
    (frozen,) = facts.freeze_units(original, (unit(),))
    (root / "source").write_bytes(b"replacement\n")
    git("commit", "-qam", "replacement")
    replacement = git("rev-parse", "HEAD")
    git("replace", original.commit_oid, replacement)
    git("checkout", "--detach", original.commit_oid)
    # The real original tree wins, not the replacement commit's tree.
    current = facts.freeze_head()
    (actual,) = facts.freeze_units(current, (unit(),))
    assert actual.payload == FilePresent(b"committed\n")
    with SyncBaseStore.open(tmp_path / "state" / "dotman", "main") as store:
        store.replace(frozen.record())
        assert (
            SyncBaseLifecycle(store, facts, operation="sync")
            .inspect(unit(), current)
            .status
            == "usable"
        )


def test_failed_acknowledgment_of_unavailable_frozen_commit_preserves_prior(
    repository, tmp_path
):
    from dotman.sync_base_lifecycle import SyncBaseGitError

    root, runtime, _ = repository
    facts = SyncBaseGit(root, runtime)
    (frozen,) = facts.freeze_units(facts.freeze_head(), (unit(),))
    with SyncBaseStore.open(tmp_path / "state" / "dotman", "main") as store:
        store.replace(frozen.record())
        missing = replace(frozen, head=replace(frozen.head, commit_oid="f" * 40))
        lifecycle = SyncBaseLifecycle(store, facts, operation="sync")
        result = lifecycle.complete(
            missing, ProposalCompletion(approved=True, intent="merge")
        )
        assert not result.converged and isinstance(result.failure, SyncBaseGitError)
        assert store.read(unit().identity_bytes) == frozen.record()


@pytest.mark.parametrize(
    "primary", ["", ".", "../escape", "/absolute", "a//b", "a/../b", ".git/config"]
)
def test_primary_source_must_be_a_canonical_relative_file(primary):
    with pytest.raises(ValueError):
        unit(source=primary)


@pytest.mark.parametrize(
    "identity",
    [
        ResolvedSyncTarget("main", "app", "bad.name"),
        ResolvedSyncTarget("main", "app", "config", child_path="../escape"),
        ResolvedSyncTarget("main", "app", "config", child_path="a//b"),
        ResolvedSyncTarget("main", "app", "config", child_path=""),
    ],
)
def test_base_keys_require_exact_unit_identity(identity):
    with pytest.raises(ValueError):
        replace(unit(), identity=identity)


def test_missing_git_executable_is_a_typed_fact_failure(repository, monkeypatch):
    from dotman.sync_base_lifecycle import SyncBaseGitError

    root, runtime, _ = repository
    monkeypatch.setenv("PATH", "")
    with pytest.raises(SyncBaseGitError):
        SyncBaseGit(root, runtime).freeze_head()


def test_fresh_direct_agreement_can_sharpen_conservative_provenance(
    repository, tmp_path
):
    root, runtime, git = repository
    facts = SyncBaseGit(root, runtime)
    (root / "source").write_bytes(b"dirty\n")
    (dirty,) = facts.freeze_units(facts.freeze_head(), (unit(),))
    with SyncBaseStore.open(tmp_path / "state" / "dotman", "main") as store:
        lifecycle = SyncBaseLifecycle(store, facts, operation="sync")
        assert lifecycle.direct_agreement(dirty).acknowledged
        assert store.read(unit().identity_bytes).envelope.provenance == "conservative"
        git("reset", "--hard", "-q", "HEAD")
        (clean,) = facts.freeze_units(facts.freeze_head(), (unit(),))
        assert lifecycle.direct_agreement(clean).acknowledged
        assert store.read(unit().identity_bytes).envelope.provenance == "exact"


def test_profile_context_freezes_nested_values_and_preserves_value_types():
    context = {"profile": "work", "variables": {"enabled": True, "ports": [1, 2]}}
    inputs = BaseInputs(profile_context=BaseProfileContext(context))
    original = unit()
    frozen = replace(original, inputs=inputs)
    fingerprint = frozen.fingerprint
    context["variables"]["ports"].append(3)
    assert frozen.fingerprint == fingerprint
    assert replace(inputs, render="jinja").profile_context == inputs.profile_context
    equivalent = BaseProfileContext(
        {"variables": {"ports": [1, 2], "enabled": True}, "profile": "work"}
    )
    assert (
        replace(original, inputs=BaseInputs(profile_context=equivalent)).fingerprint
        == fingerprint
    )
    different = BaseProfileContext(
        {"profile": "work", "variables": {"enabled": "true", "ports": [1, 2]}}
    )
    assert (
        replace(original, inputs=BaseInputs(profile_context=different)).fingerprint
        != fingerprint
    )


def test_one_bad_checkout_does_not_discard_other_units_frozen_facts(
    repository, tmp_path
):
    root, runtime, git = repository
    (root / "unsafe").symlink_to("source")
    git("add", "unsafe")
    git("commit", "-qm", "unsafe neighbor")
    bad = replace(
        unit(source="unsafe"), identity=ResolvedSyncTarget("main", "app", "bad")
    )
    facts = SyncBaseGit(root, runtime)
    good, failed = facts.freeze_units(facts.freeze_head(), (unit(), bad))
    assert good.failure is None
    assert failed.failure is not None and failed.payload is None
    with SyncBaseStore.open(tmp_path / "state" / "dotman", "main") as store:
        lifecycle = SyncBaseLifecycle(store, facts, operation="sync")
        assert lifecycle.direct_agreement(good).acknowledged
        result = lifecycle.direct_agreement(failed)
        assert not result.acknowledged and result.failure is failed.failure
        assert store.read(unit().identity_bytes) is not None
        assert store.read(bad.identity_bytes) is None


def test_failed_checkout_conversion_is_local_to_its_unit(repository):
    root, runtime, git = repository
    (root / "bad").write_bytes(b"bad")
    (root / ".gitattributes").write_text("bad filter=reject\n")
    git("add", ".")
    git("commit", "-qm", "filtered file")
    git("config", "filter.reject.clean", "cat")
    git("config", "filter.reject.smudge", "false")
    git("config", "filter.reject.required", "true")
    facts = SyncBaseGit(root, runtime)
    bad = replace(unit(source="bad"), identity=ResolvedSyncTarget("main", "app", "bad"))
    failed, good = facts.freeze_units(facts.freeze_head(), (bad, unit()))
    assert failed.payload is None and failed.failure is not None
    assert good.payload == FilePresent(b"committed\n") and good.failure is None


def test_read_only_inspection_never_fetches_missing_promisor_commit(
    repository, tmp_path
):
    import json

    from dotman.sync_base_store import SyncBaseEnvelope, SyncBaseRecord

    origin, runtime, git = repository
    branch = git("branch", "--show-current")
    git("checkout", "-qb", "remote-only")
    (origin / "source").write_bytes(b"remote-only\n")
    git("commit", "-qam", "remote-only commit")
    remote_commit = git("rev-parse", "HEAD")
    assert git("cat-file", "-t", remote_commit) == "commit"
    git("checkout", "-q", branch)
    git("config", "uploadpack.allowFilter", "true")
    git("config", "uploadpack.allowAnySHA1InWant", "true")
    clone = tmp_path / "partial-clone"
    result = runtime.run(
        CommandRequest(
            ArgvCommand(
                (
                    "git",
                    "clone",
                    "--no-checkout",
                    "--filter=blob:none",
                    "--single-branch",
                    "--branch",
                    branch,
                    origin.as_uri(),
                    str(clone),
                )
            ),
            cwd=tmp_path,
        )
    )
    assert result.exit_code == 0, result.stderr_text
    local_check = runtime.run(
        CommandRequest(
            ArgvCommand(
                ("git", "--no-lazy-fetch", "cat-file", "--batch-check=%(objecttype)")
            ),
            cwd=clone,
            input=remote_commit.encode() + b"\n",
        )
    )
    assert local_check.exit_code == 0
    assert local_check.stdout == remote_commit.encode() + b" missing\n"

    trace = tmp_path / "inspection-trace.json"

    class TracedRuntime:
        def run(self, request):
            return runtime.run(
                replace(
                    request,
                    env={**request.env, "GIT_TRACE2_EVENT": str(trace)},
                )
            )

    facts = SyncBaseGit(clone, TracedRuntime())
    head = facts.freeze_head()
    current = unit()
    state = tmp_path / "state" / "dotman"
    with SyncBaseStore.open(state, "main") as store:
        store.replace(
            SyncBaseRecord(
                current.identity_bytes,
                FilePresent(b"remote-only\n"),
                SyncBaseEnvelope(
                    remote_commit, head.object_format, current.fingerprint, "exact"
                ),
            )
        )

    def repository_evidence():
        return {
            path.relative_to(clone): path.read_bytes()
            for path in (clone / ".git").rglob("*")
            if path.is_file()
        }

    before = repository_evidence()
    with SyncBaseStore.open(state, "main", read_only=True) as store:
        result = SyncBaseLifecycle(store, facts, operation="sync").inspect(
            current, head
        )
    # Read-only means no downloaded packs or other Git metadata mutations.
    assert repository_evidence() == before
    assert result.status == "unavailable" and result.reason == "commit_missing"
    assert result.record is None
    events = [json.loads(line) for line in trace.read_text().splitlines()]
    remote_children = [
        event["argv"]
        for event in events
        if event["event"] == "child_start"
        and any("fetch" in arg or "upload-pack" in arg for arg in event["argv"])
    ]
    assert remote_children == []


def test_repository_graft_cannot_make_unrelated_commit_a_usable_base(
    repository, tmp_path
):
    root, runtime, git = repository
    facts = SyncBaseGit(root, runtime)
    (original,) = facts.freeze_units(facts.freeze_head(), (unit(),))
    git("checkout", "--orphan", "unrelated")
    (root / "source").write_bytes(b"unrelated\n")
    git("commit", "-qam", "unrelated root")
    unrelated = git("rev-parse", "HEAD")
    grafts = root / ".git" / "info" / "grafts"
    graft_content = f"{unrelated} {original.head.commit_oid}\n"
    grafts.write_text(graft_content)
    # Establish that the real repository override would forge ancestry.
    forged = runtime.run(
        CommandRequest(
            ArgvCommand(
                (
                    "git",
                    "merge-base",
                    "--is-ancestor",
                    original.head.commit_oid,
                    unrelated,
                )
            ),
            cwd=root,
        )
    )
    assert forged.exit_code == 0
    state = tmp_path / "state" / "dotman"
    with SyncBaseStore.open(state, "main") as store:
        store.replace(original.record())
    with SyncBaseStore.open(state, "main", read_only=True) as store:
        result = SyncBaseLifecycle(store, facts, operation="sync").inspect(
            unit(), facts.freeze_head()
        )
    assert result.status == "unavailable" and result.reason == "history_changed"
    assert result.record is None
    assert grafts.read_text() == graft_content
