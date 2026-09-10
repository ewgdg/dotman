import json
import sqlite3

import pytest

from dotman.cli import main
from dotman.operation_lock import OperationLock, OperationBusy
from dotman.sync_base_inspection import _identity, _unit
from dotman.sync_base_lifecycle import SyncBaseGit
from dotman.sync_base_store import SyncBaseEnvelope, SyncBaseRecord, SyncBaseStore, FilePresent, Missing
from dotman.sync_observation import _resolve_inputs
from tests.engine.test_sync_session import make_engine


def fixture_engine(tmp_path, monkeypatch, policy="both", extra=""):
    return make_engine(tmp_path, monkeypatch, [("unit", policy, b"secret-base-payload", b"live", extra)])


def store_record(engine, *, payload=None, fingerprint=None, oid=None, identity="main:app.unit"):
    context = engine._planning_context
    inputs, _ = _resolve_inputs(context, engine.resolve_sync_scope())
    unit = _unit(context, inputs, _identity(identity))
    head = SyncBaseGit(context.repositories["main"].root, context.command_runtime).freeze_head()
    envelope = SyncBaseEnvelope(oid or head.commit_oid, head.object_format, fingerprint or unit.fingerprint, "exact")
    with SyncBaseStore.open(engine._tracked_state_context.state_root, "main") as store:
        store.replace(SyncBaseRecord(identity=identity.encode(), envelope=envelope, payload=payload if payload is not None else FilePresent(b"secret-base-payload")))
        return store.database_path


def call(engine, capsys, *args):
    code = main(["--config", str(engine.config.config_path), "--json", *args])
    output = capsys.readouterr()
    return code, json.loads(output.out) if output.out else output.err


@pytest.mark.parametrize("payload", [Missing(), FilePresent(b"secret-base-payload")])
def test_usable_details_list_and_reset_are_metadata_only(tmp_path, monkeypatch, capsys, payload):
    engine = fixture_engine(tmp_path, monkeypatch)
    database = store_record(engine, payload=payload)
    before = database.read_bytes()
    code, info = call(engine, capsys, "info", "sync-base", "main:app.unit")
    assert code == 0
    assert info["status"] == "usable"
    assert info["reason"] is None
    assert info["policy"] == "both" and info["eligibility"] is True
    assert len(info["commit"]) == 40
    assert info["provenance"] == "exact"
    assert all(info["checks"].values())
    assert "secret-base-payload" not in json.dumps(info)
    assert database.read_bytes() == before
    code, listing = call(engine, capsys, "list", "sync-bases")
    assert code == 0
    assert listing["sync_bases"] == [{key: value for key, value in info.items() if key != "operation"}]
    assert call(engine, capsys, "reset", "sync-base", "main:app.unit")[1]["status"] == "reset"
    assert call(engine, capsys, "reset", "sync-base", "main:app.unit")[1]["status"] == "already_absent"
    assert engine.info_sync_base("main:app.unit")["reason"] == "absent"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM payloads").fetchone()[0] == 0


@pytest.mark.parametrize("policy,status,reason", [("both", "unavailable", "absent"), ("push-only", "not-applicable", "ineligible"), ("push-only-delete", "not-applicable", "ineligible")])
def test_absent_and_ineligible_succeed_without_creating_store(tmp_path, monkeypatch, policy, status, reason):
    engine = fixture_engine(tmp_path, monkeypatch, policy=policy)
    state = engine._tracked_state_context.state_root
    before = sorted(str(path.relative_to(state)) for path in state.rglob("*"))
    info = engine.info_sync_base("main:app.unit")
    assert (info["status"], info["reason"]) == (status, reason)
    assert info["commit"] is info["payload"] is None
    assert engine.list_sync_bases() == []
    assert sorted(str(path.relative_to(state)) for path in state.rglob("*")) == before


@pytest.mark.parametrize("reason,changes", [
    ("inputs_changed", {"fingerprint": "f" * 64}),
    ("commit_missing", {"oid": "f" * 40}),
])
def test_unavailable_never_exposes_stale_metadata(tmp_path, monkeypatch, reason, changes):
    engine = fixture_engine(tmp_path, monkeypatch)
    database = store_record(engine, **changes)
    before = database.read_bytes()
    info = engine.info_sync_base("main:app.unit")
    assert info["status"] == "unavailable" and info["reason"] == reason
    assert info["commit"] is info["provenance"] is info["payload"] is None
    assert engine.list_sync_bases() == []
    assert database.read_bytes() == before


@pytest.mark.parametrize("sql,reason", [
    ("UPDATE base_records SET provenance = 'wrong'", "record_corrupt"),
    ("UPDATE payloads SET content = X'626164'", "payload_corrupt"),
])
def test_corruption_is_distinguished_without_cleanup_and_doctor_is_aggregate(tmp_path, monkeypatch, sql, reason):
    engine = fixture_engine(tmp_path, monkeypatch)
    database = store_record(engine)
    with sqlite3.connect(database) as connection:
        connection.execute(sql)
    before = database.read_bytes()
    assert engine.info_sync_base("main:app.unit")["reason"] == reason
    assert engine.list_sync_bases() == []
    checks = [check for check in engine.doctor().checks if check.key.startswith("sync_bases")]
    warning = next(check for check in checks if check.key == "sync_bases_corrupt")
    assert warning.status == "warn" and warning.detail == "1 corrupt Sync Bases"
    assert warning.to_dict()["count"] == 1
    assert "main:app.unit" not in json.dumps([check.to_dict() for check in checks])
    assert database.read_bytes() == before


@pytest.mark.parametrize("selector", ["unit", "app.unit", "main:app", "main:", "*", "main:app.*", "main:app.unit/../bad", "main:app.unit/child"])
def test_exact_selectors_only(tmp_path, monkeypatch, selector):
    engine = fixture_engine(tmp_path, monkeypatch)
    for method in (engine.info_sync_base, engine.reset_sync_base):
        with pytest.raises(ValueError):
            method(selector)


def test_reset_lock_and_readers_do_not_take_manager_lock(tmp_path, monkeypatch):
    engine = fixture_engine(tmp_path, monkeypatch)
    store_record(engine)
    with OperationLock.acquire(engine._tracked_state_context.state_root):
        assert engine.info_sync_base("main:app.unit")["status"] == "usable"
        assert len(engine.list_sync_bases()) == 1
        engine.doctor()
        with pytest.raises(OperationBusy):
            engine.reset_sync_base("main:app.unit")


def test_inspection_never_executes_guards_projections_observation_or_verification(tmp_path, monkeypatch):
    engine = fixture_engine(tmp_path, monkeypatch, extra='render = "exit 9"\ncapture = "exit 9"\n[targets.unit.hooks]\nguard_push = "exit 9"\nguard_pull = "exit 9"')
    store_record(engine)
    def forbidden(*args, **kwargs):
        pytest.fail("inspection must not execute payload work")
    monkeypatch.setattr("dotman.sync_observation._observe_file", forbidden)
    monkeypatch.setattr("dotman.sync_base_lifecycle.SyncBaseGit.freeze_units", forbidden)
    assert engine.info_sync_base("main:app.unit")["status"] == "usable"
    assert len(engine.list_sync_bases()) == 1


def test_orphan_record_not_listed_but_counted(tmp_path, monkeypatch):
    engine = fixture_engine(tmp_path, monkeypatch)
    database = store_record(engine)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE base_records SET identity = ?", (b"main:app.gone",))
    assert engine.list_sync_bases() == []
    check = next(check for check in engine.doctor().checks if check.key == "sync_bases_orphaned")
    assert check.detail == "1 orphaned Sync Bases" and check.status == "warn"


def test_history_changed_is_unavailable(tmp_path, monkeypatch):
    from dotman.command_runtime import ArgvCommand, CommandRequest
    engine = fixture_engine(tmp_path, monkeypatch)
    store_record(engine)
    runtime = engine._planning_context.command_runtime
    repo = tmp_path / "repo"
    for arguments in [
        ("checkout", "--orphan", "unrelated"),
        ("-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "-qm", "new history"),
    ]:
        result = runtime.run(CommandRequest(ArgvCommand(("git", *arguments)), cwd=repo))
        assert result.exit_code == 0
    info = engine.info_sync_base("main:app.unit")
    assert info["reason"] == "history_changed"
    assert info["checks"]["ancestry"] is False
    assert info["commit"] is None


def test_directory_child_metadata_policy_and_exact_reset(tmp_path, monkeypatch):
    from dotman.sync_base_store import DirectoryChildPresent
    from tests.engine.test_sync_directory_observation import directory_engine
    engine = directory_engine(tmp_path, monkeypatch, extra='[targets.tree.path_rules.only]\npattern = "*.private"\nsync_policy = "push-only"')
    # Missing children remain exact identities; no payload census is required.
    path = store_record(engine, identity="main:app.tree/nested/file", payload=DirectoryChildPresent(b"private", True))
    info = engine.info_sync_base("main:app.tree/nested/file")
    assert info["status"] == "usable" and info["payload"]["executable"] is True
    assert engine.info_sync_base("main:app.tree/secret.private")["reason"] == "ineligible"
    for method in (engine.info_sync_base, engine.reset_sync_base):
        with pytest.raises(ValueError):
            method("main:app.tree")
    assert engine.reset_sync_base("main:app.tree/nested/file")["status"] == "reset"


def test_store_failure_is_cli_error_and_doctor_failure_without_repair(tmp_path, monkeypatch, capsys):
    engine = fixture_engine(tmp_path, monkeypatch)
    database = store_record(engine)
    database.chmod(0o644)
    before = database.read_bytes()
    code, error = call(engine, capsys, "info", "sync-base", "main:app.unit")
    assert code == 2
    assert str(database) in error and "main" in error
    check = next(check for check in engine.doctor().checks if check.key == "sync_bases_store")
    assert check.status == "failed" and check.path == database and check.repo_name == "main"
    assert database.stat().st_mode & 0o777 == 0o644
    assert database.read_bytes() == before


@pytest.mark.parametrize("reason,expected", [
    ("record_corrupt", "corrupt"), ("payload_corrupt", "corrupt"),
    ("inputs_changed", "inputs changed"), ("commit_missing", "commit missing"),
    ("history_changed", "history changed"), ("absent", "absent"), ("ineligible", "ineligible"),
])
def test_human_reasons_and_styling(reason, expected, capsys):
    from dotman.cli_emit import emit_sync_base
    emit_sync_base(detail={"identity": "main:app.unit", "status": "unavailable", "reason": reason},
                   operation="info-sync-base", json_output=False, use_color=True)
    output = capsys.readouterr().out
    assert f"Reason: {expected}" in output
    assert "\x1b[" in output


def test_reset_rejects_extra_arguments_and_preview(tmp_path, monkeypatch):
    engine = fixture_engine(tmp_path, monkeypatch)
    for arguments in [
        ["reset", "sync-base"],
        ["reset", "sync-base", "main:app.unit", "main:app.unit"],
        ["reset", "sync-base", "main:app.unit", "--dry-run"],
    ]:
        with pytest.raises(SystemExit) as caught:
            main(["--config", str(engine.config.config_path), *arguments])
        assert caught.value.code == 2


@pytest.mark.parametrize("extra,expected", [
    ("", 1),
    ('[targets.tree.ignore]\npatterns = ["private/"]', 0),
    ('[targets.tree.hooks]\nguard_push = "exit 9"', 0),
    ('[targets.tree.path_rules.guarded]\npattern = "*"\n[targets.tree.path_rules.guarded.hooks]\nguard_pull = "exit 9"', 0),
])
def test_doctor_child_orphans_require_complete_unrestricted_census(tmp_path, monkeypatch, extra, expected):
    from tests.engine.test_sync_directory_observation import directory_engine
    engine = directory_engine(tmp_path, monkeypatch, extra=extra)
    database = store_record(engine, identity="main:app.tree/gone", payload=Missing())
    before = database.read_bytes()
    check = next(check for check in engine.doctor().checks if check.key == "sync_bases_orphaned")
    assert check.detail == f"{expected} orphaned Sync Bases"
    assert database.read_bytes() == before
    assert engine.info_sync_base("main:app.tree/gone")["status"] == "usable"


def test_instance_identity_remains_canonical(tmp_path, monkeypatch):
    from dotman.engine import DotmanEngine
    from tests.helpers import write_tracked_packages_state
    engine = fixture_engine(tmp_path, monkeypatch)
    manifest = tmp_path / "repo/packages/app/package.toml"
    manifest.write_text(manifest.read_text().replace('id = "app"', 'id = "app"\nbinding_mode = "multi_instance"'))
    (tmp_path / "repo/profiles/work.v2.toml").write_text("")
    write_tracked_packages_state(tmp_path / "state", repo_name="main", entries=[("app", "work.v2")])
    engine = DotmanEngine.from_config_path(engine.config.config_path)
    identity = "main:app<work.v2>.unit"
    store_record(engine, identity=identity)
    assert engine.info_sync_base(identity)["identity"] == identity
    assert engine.list_sync_bases()[0]["identity"] == identity
    with pytest.raises(ValueError):
        engine.reset_sync_base("main:app.unit")
    assert engine.reset_sync_base(identity)["status"] == "reset"


def test_absent_record_does_not_require_git_history(tmp_path, monkeypatch):
    engine = fixture_engine(tmp_path, monkeypatch)
    with SyncBaseStore.open(engine._tracked_state_context.state_root, "main"):
        pass
    monkeypatch.setattr(SyncBaseGit, "freeze_head", lambda *_: pytest.fail("absent ancestry needs no Git proof"))
    assert engine.info_sync_base("main:app.unit")["reason"] == "absent"


@pytest.mark.parametrize("scope", ["repo", "package"])
def test_doctor_child_orphan_proof_excludes_ancestor_guards(tmp_path, monkeypatch, scope):
    from dotman.engine import DotmanEngine
    from tests.engine.test_sync_directory_observation import directory_engine
    engine = directory_engine(tmp_path, monkeypatch)
    path = tmp_path / ("repo/repo.toml" if scope == "repo" else "repo/packages/app/package.toml")
    path.write_text(path.read_text() + '\n[hooks]\nguard_pull = "exit 9"\n')
    engine = DotmanEngine.from_config_path(engine.config.config_path)
    store_record(engine, identity="main:app.tree/gone", payload=Missing())
    check = next(check for check in engine.doctor().checks if check.key == "sync_bases_orphaned")
    assert check.count == 0


@pytest.mark.parametrize("missing", ["database", "lock"])
def test_reset_rejects_incomplete_existing_store_without_repair(
    tmp_path, monkeypatch, missing
):
    from dotman.sync_base_store import SyncBaseStoreError

    engine = fixture_engine(tmp_path, monkeypatch)
    database = store_record(engine)
    path = database if missing == "database" else database.with_name(database.name + ".lock")
    path.unlink()
    def artifacts():
        return {
            entry.name: (entry.stat().st_ino, entry.stat().st_mode, entry.read_bytes())
            for entry in database.parent.iterdir() if entry.is_file()
        }
    before = artifacts()
    with pytest.raises(SyncBaseStoreError):
        engine.reset_sync_base("main:app.unit")
    assert artifacts() == before


@pytest.mark.parametrize("child", [False, True])
def test_doctor_counts_record_shape_identity_mismatch_without_git(
    tmp_path, monkeypatch, child
):
    from tests.engine.test_sync_directory_observation import directory_engine
    from dotman.sync_base_store import DirectoryChildPresent

    engine = directory_engine(tmp_path, monkeypatch) if child else fixture_engine(tmp_path, monkeypatch)
    identity = "main:app.tree/nested/file" if child else "main:app.unit"
    payload = DirectoryChildPresent(b"secret", True) if child else FilePresent(b"secret")
    database = store_record(engine, identity=identity, payload=payload)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE base_records SET shape = ?, executable = ?",
            ("file", None) if child else ("directory-child", 1),
        )
    before = database.read_bytes()
    assert engine.info_sync_base(identity)["reason"] == "record_corrupt"

    def forbidden(*args, **kwargs):
        pytest.fail("doctor record consistency must not inspect Git ancestry")
    monkeypatch.setattr(SyncBaseGit, "freeze_head", forbidden)
    checks = [check for check in engine.doctor().checks if check.key.startswith("sync_bases")]
    corrupt = next(check for check in checks if check.key == "sync_bases_corrupt")
    assert corrupt.count == 1
    assert corrupt.status == "warn"
    assert identity not in json.dumps([check.to_dict() for check in checks])
    assert database.read_bytes() == before
