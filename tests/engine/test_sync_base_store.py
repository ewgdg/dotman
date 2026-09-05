from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from dotman.sync_base_store import (
    DirectoryChildPresent,
    FilePresent,
    Missing,
    SyncBaseRecord,
    SyncBaseRecordCorruptionError,
    SyncBaseStore,
    SyncBaseStoreCorruptionError,
    SyncBaseStoreEpochError,
    SyncBaseStoreError,
    SyncBaseStoreLockedError,
    SyncBaseStoreSecurityError,
    SyncBaseStoreUnsupportedRuntimeError,
)


def _open_store(tmp_path: Path) -> SyncBaseStore:
    return SyncBaseStore.open(tmp_path / "state" / "dotman", "main")


def _database_path(tmp_path: Path) -> Path:
    return tmp_path / "state" / "dotman" / "repos" / "main" / "sync-bases.sqlite3"


def _row_count(database_path: Path, table: str) -> int:
    with sqlite3.connect(database_path) as connection:
        return connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_round_trips_typed_records_and_reuses_exact_payloads(tmp_path: Path) -> None:
    file_record = SyncBaseRecord(b"main:app.config", FilePresent(b"same"))
    child_record = SyncBaseRecord(
        b"main:app.settings/bin/tool",
        DirectoryChildPresent(b"same", executable=True),
    )
    missing_record = SyncBaseRecord(b"main:app.absent", Missing())

    with _open_store(tmp_path) as store:
        store.replace(file_record)
        store.replace(child_record)
        store.replace(missing_record)
        assert store.read(file_record.identity) == file_record
        assert store.read(child_record.identity) == child_record
        assert store.read(missing_record.identity) == missing_record
        assert store.read(b"main:app.unknown") is None

    database_path = _database_path(tmp_path)
    assert _row_count(database_path, "payloads") == 1
    assert _row_count(database_path, "base_records") == 3

    with _open_store(tmp_path) as reopened:
        assert reopened.read(child_record.identity) == child_record


def test_equal_digest_and_length_uses_exact_bytes_and_retains_collisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "dotman.sync_base_store._sha256_digest",
        lambda _content: b"x" * hashlib.sha256().digest_size,
    )
    one = SyncBaseRecord(b"one", FilePresent(b"aaaa"))
    two = SyncBaseRecord(b"two", FilePresent(b"bbbb"))

    with _open_store(tmp_path) as store:
        store.replace(one)
        store.replace(two)
        assert store.read(one.identity) == one
        assert store.read(two.identity) == two

    database_path = _database_path(tmp_path)
    assert _row_count(database_path, "payloads") == 2


def test_replace_and_delete_garbage_collect_only_unreferenced_payloads(
    tmp_path: Path,
) -> None:
    shared_one = SyncBaseRecord(b"one", FilePresent(b"shared"))
    shared_two = SyncBaseRecord(b"two", FilePresent(b"shared"))

    with _open_store(tmp_path) as store:
        store.replace(shared_one)
        store.replace(shared_two)
        store.replace(SyncBaseRecord(b"one", FilePresent(b"new")))
        assert store.delete(b"missing") is False
        assert store.delete(b"two") is True
        assert store.read(b"two") is None

    database_path = _database_path(tmp_path)
    assert _row_count(database_path, "payloads") == 1
    assert _row_count(database_path, "base_records") == 1


def test_invalid_input_cannot_partially_replace_existing_record(tmp_path: Path) -> None:
    original = SyncBaseRecord(b"one", FilePresent(b"original"))
    with _open_store(tmp_path) as store:
        store.replace(original)
        with pytest.raises(TypeError):
            store.replace(SyncBaseRecord(b"one", FilePresent("not bytes")))  # type: ignore[arg-type]
        assert store.read(b"one") == original


def test_created_store_layout_is_private(tmp_path: Path) -> None:
    store = _open_store(tmp_path)
    repo_state = _database_path(tmp_path).parent
    expected_modes = {
        tmp_path / "state" / "dotman": 0o700,
        tmp_path / "state" / "dotman" / "repos": 0o700,
        repo_state: 0o700,
        _database_path(tmp_path): 0o600,
        repo_state / "sync-bases.sqlite3.lock": 0o600,
    }
    for path, expected_mode in expected_modes.items():
        assert stat.S_IMODE(path.lstat().st_mode) == expected_mode
        assert path.lstat().st_uid == os.geteuid()

    with _open_store(tmp_path):
        pass

    store.close()
    with _open_store(tmp_path) as reopened:
        reopened.replace(SyncBaseRecord(b"key", FilePresent(b"value")))
        assert not list(repo_state.glob("*-wal"))
        assert not list(repo_state.glob("*-shm"))
        assert not list(repo_state.glob("*-journal"))


def test_rejects_symlink_nonregular_and_insecure_storage_without_mutation(
    tmp_path: Path,
) -> None:
    manager_root = tmp_path / "state" / "dotman"
    manager_root.parent.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "elsewhere"
    target.mkdir()
    manager_root.symlink_to(target, target_is_directory=True)
    with pytest.raises(SyncBaseStoreSecurityError, match="symlink"):
        SyncBaseStore.open(manager_root, "main")
    assert list(target.iterdir()) == []

    manager_root.unlink()
    manager_root.mkdir(mode=0o755)
    with pytest.raises(SyncBaseStoreSecurityError, match="mode"):
        SyncBaseStore.open(manager_root, "main")
    assert stat.S_IMODE(manager_root.stat().st_mode) == 0o755

    os.chmod(manager_root, 0o700)
    (manager_root / "repos").mkdir(mode=0o700)
    repo_state = manager_root / "repos" / "main"
    repo_state.mkdir(mode=0o700)
    database_path = repo_state / "sync-bases.sqlite3"
    database_path.mkdir()
    with pytest.raises(SyncBaseStoreSecurityError, match="regular file"):
        SyncBaseStore.open(manager_root, "main")
    assert database_path.is_dir()


def test_rejects_insecure_database_and_sidecar_modes_without_repair(
    tmp_path: Path,
) -> None:
    with _open_store(tmp_path):
        pass
    database_path = _database_path(tmp_path)
    os.chmod(database_path, 0o644)
    with pytest.raises(SyncBaseStoreSecurityError, match="mode"):
        _open_store(tmp_path)
    assert stat.S_IMODE(database_path.stat().st_mode) == 0o644

    os.chmod(database_path, 0o600)
    sidecar = Path(str(database_path) + "-wal")
    sidecar.write_bytes(b"evidence")
    os.chmod(sidecar, 0o644)
    with pytest.raises(SyncBaseStoreSecurityError, match="mode"):
        _open_store(tmp_path)
    assert sidecar.read_bytes() == b"evidence"
    assert stat.S_IMODE(sidecar.stat().st_mode) == 0o644


def test_unsupported_epoch_fails_closed_and_preserves_database(tmp_path: Path) -> None:
    with _open_store(tmp_path):
        pass
    database_path = _database_path(tmp_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute("UPDATE store_metadata SET epoch = 99 WHERE singleton = 1")

    with pytest.raises(SyncBaseStoreEpochError, match="99"):
        _open_store(tmp_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT epoch FROM store_metadata").fetchone() == (
            99,
        )


def test_structurally_corrupt_database_fails_closed_and_preserves_evidence(
    tmp_path: Path,
) -> None:
    database_path = _database_path(tmp_path)
    database_path.parent.mkdir(parents=True, mode=0o700)
    os.chmod(database_path.parent.parent, 0o700)
    os.chmod(database_path.parent.parent.parent, 0o700)
    database_path.write_bytes(b"not sqlite")
    os.chmod(database_path, 0o600)

    with pytest.raises(SyncBaseStoreCorruptionError):
        _open_store(tmp_path)

    assert database_path.read_bytes() == b"not sqlite"


def test_payload_corruption_reports_every_reference_without_cleanup(
    tmp_path: Path,
) -> None:
    with _open_store(tmp_path) as store:
        store.replace(SyncBaseRecord(b"one", FilePresent(b"shared")))
        store.replace(
            SyncBaseRecord(b"two", DirectoryChildPresent(b"shared", executable=False))
        )
    database_path = _database_path(tmp_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute("UPDATE payloads SET content = ?", (b"broken",))

    with _open_store(tmp_path) as store:
        with pytest.raises(SyncBaseRecordCorruptionError) as error:
            store.read(b"one")
        assert error.value.affected_identities == (b"one", b"two")

    assert _row_count(database_path, "payloads") == 1
    assert _row_count(database_path, "base_records") == 2


def test_rejects_non_bytes_or_empty_canonical_identity(tmp_path: Path) -> None:
    with pytest.raises((TypeError, ValueError)):
        SyncBaseRecord(b"", Missing())
    with pytest.raises(TypeError):
        SyncBaseRecord("main:app.target", Missing())  # type: ignore[arg-type]

    with _open_store(tmp_path) as store, pytest.raises(TypeError):
        store.read("main:app.target")  # type: ignore[arg-type]


def _evidence(directory: Path) -> dict[str, tuple[int, int, bytes | str]]:
    """Names, inode bindings, modes and bytes; reads must not consume evidence."""
    return {
        path.name: (
            path.lstat().st_ino,
            stat.S_IMODE(path.lstat().st_mode),
            str(path.readlink()) if path.is_symlink() else path.read_bytes(),
        )
        for path in directory.iterdir()
        if not path.is_dir()
    }


@pytest.mark.parametrize(
    "statement, error",
    [
        ("CREATE TABLE sqliteX (value BLOB)", SyncBaseStoreCorruptionError),
        (
            "CREATE TRIGGER sqliteX AFTER INSERT ON base_records BEGIN DELETE FROM base_records; END",
            SyncBaseStoreCorruptionError,
        ),
        ("PRAGMA application_id = 42", SyncBaseStoreCorruptionError),
        ("PRAGMA user_version = 99", SyncBaseStoreEpochError),
        ("DELETE FROM store_metadata", SyncBaseStoreCorruptionError),
        ("UPDATE store_metadata SET epoch = 99", SyncBaseStoreEpochError),
        ("DROP INDEX payload_lookup", SyncBaseStoreCorruptionError),
        ("PRAGMA journal_mode = WAL", SyncBaseStoreCorruptionError),
        (
            "INSERT INTO base_records VALUES (x'78', 'file', 999, NULL)",
            SyncBaseStoreCorruptionError,
        ),
    ],
)
def test_untrusted_database_rejection_preserves_all_evidence(
    tmp_path: Path, statement: str, error: type[Exception]
) -> None:
    with _open_store(tmp_path):
        pass
    path = _database_path(tmp_path)
    connection = sqlite3.connect(path)
    connection.execute(statement)
    connection.commit()
    connection.close()
    # Even absence of a lock is evidence: rejecting an existing store must not
    # create a lock or let SQLite clean up any sidecar.
    path.with_name(path.name + ".lock").unlink()
    before = _evidence(path.parent)
    with pytest.raises(error):
        _open_store(tmp_path)
    assert _evidence(path.parent) == before


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
@pytest.mark.parametrize("with_database", [False, True])
def test_rejects_sidecar_evidence_without_touching_it(
    tmp_path: Path, suffix: str, with_database: bool
) -> None:
    with _open_store(tmp_path):
        pass
    path = _database_path(tmp_path)
    if not with_database:
        path.unlink()
    sidecar = path.with_name(path.name + suffix)
    sidecar.write_bytes(b"untrusted pending recovery evidence")
    sidecar.chmod(0o600)
    path.with_name(path.name + ".lock").unlink()
    before = _evidence(path.parent)
    with pytest.raises(SyncBaseStoreCorruptionError):
        _open_store(tmp_path)
    assert _evidence(path.parent) == before


def test_full_integrity_check_rejects_index_inconsistency(tmp_path: Path) -> None:
    with _open_store(tmp_path) as store:
        store.replace(SyncBaseRecord(b"one", FilePresent(b"original")))
    path = _database_path(tmp_path)
    connection = sqlite3.connect(path)
    # Make SQLite maintain the index using different columns, then restore its
    # declared schema. quick_check intentionally does not verify index contents.
    connection.execute("PRAGMA writable_schema = ON")
    connection.execute(
        "UPDATE sqlite_schema SET sql = "
        "'CREATE INDEX payload_lookup ON payloads (content, byte_length)' "
        "WHERE name = 'payload_lookup'"
    )
    connection.commit()
    connection.close()
    connection = sqlite3.connect(path)
    connection.execute("REINDEX payload_lookup")
    connection.commit()
    connection.execute("PRAGMA writable_schema = ON")
    connection.execute(
        "UPDATE sqlite_schema SET sql = "
        "'CREATE INDEX payload_lookup ON payloads (digest, byte_length)' "
        "WHERE name = 'payload_lookup'"
    )
    connection.commit()
    connection.close()
    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA quick_check").fetchall() == [("ok",)]
    assert connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]
    connection.close()
    before = _evidence(path.parent)
    with pytest.raises(SyncBaseStoreCorruptionError, match="integrity"):
        _open_store(tmp_path)
    assert _evidence(path.parent) == before


@pytest.mark.parametrize(
    "payload_type, field, value",
    [
        (FilePresent, "content", bytearray(b"bad")),
        (DirectoryChildPresent, "content", "bad"),
        (DirectoryChildPresent, "executable", 1),
        (DirectoryChildPresent, "executable", "false"),
    ],
)
def test_replace_recursively_validates_forged_payload_before_transaction(
    tmp_path: Path,
    payload_type: type,
    field: str,
    value: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = SyncBaseRecord(b"one", FilePresent(b"original"))
    with _open_store(tmp_path) as store:
        store.replace(original)
        payload = object.__new__(payload_type)
        object.__setattr__(payload, "content", b"replacement")
        if payload_type is DirectoryChildPresent:
            object.__setattr__(payload, "executable", False)
        object.__setattr__(payload, field, value)
        record = SyncBaseRecord(b"one", payload)
        with pytest.raises(TypeError):
            store.replace(record)
        assert store.read(b"one") == original
    assert _row_count(_database_path(tmp_path), "payloads") == 1


def test_read_only_open_is_side_effect_free_and_readers_do_not_take_writer_lifetime_lock(
    tmp_path: Path,
) -> None:
    with _open_store(tmp_path) as writer:
        original = SyncBaseRecord(b"one", FilePresent(b"original"))
        writer.replace(original)
        before = _evidence(writer.repo_state_directory)
        with SyncBaseStore.open(
            tmp_path / "state" / "dotman", "main", read_only=True
        ) as reader:
            assert reader.read(b"one") == original
            with pytest.raises(SyncBaseStoreError, match="read.only"):
                reader.replace(SyncBaseRecord(b"one", Missing()))
            with pytest.raises(SyncBaseStoreError, match="read.only"):
                reader.delete(b"one")
            with reader.read_transaction():
                assert reader.read(b"one") == original
                with pytest.raises(SyncBaseStoreLockedError):
                    writer.replace(SyncBaseRecord(b"one", Missing()))
            writer.replace(SyncBaseRecord(b"one", Missing()))
            assert reader.read(b"one") == SyncBaseRecord(b"one", Missing())
        assert _evidence(writer.repo_state_directory).keys() == before.keys()


def test_read_only_missing_store_does_not_create_anything(tmp_path: Path) -> None:
    before = set(tmp_path.iterdir())
    with pytest.raises(SyncBaseStoreError):
        SyncBaseStore.open(tmp_path / "absent" / "dotman", "main", read_only=True)
    assert set(tmp_path.iterdir()) == before


@pytest.mark.parametrize("journal_mode", ["DELETE", "WAL"])
def test_real_crashed_writer_evidence_is_never_recovered_on_open(
    tmp_path: Path,
    journal_mode: str,
) -> None:
    with _open_store(tmp_path) as store:
        store.replace(SyncBaseRecord(b"one", FilePresent(b"original")))
    path = _database_path(tmp_path)
    script = """
import os, sqlite3, sys
connection = sqlite3.connect(sys.argv[1])
connection.execute('PRAGMA journal_mode = ' + sys.argv[2])
connection.execute('PRAGMA cache_size = 1')
connection.execute('BEGIN IMMEDIATE')
connection.execute('UPDATE payloads SET content = zeroblob(1000000)')
if sys.argv[2] == 'WAL':
    connection.commit()
os._exit(0)
"""
    subprocess.run(
        [sys.executable, "-c", script, str(path), journal_mode],
        check=True,
        timeout=5,
    )
    suffix = "-journal" if journal_mode == "DELETE" else "-wal"
    sidecar = path.with_name(path.name + suffix)
    assert sidecar.stat().st_size > 0
    if journal_mode == "DELETE":
        assert sidecar.read_bytes()[:8] == bytes.fromhex("d9d505f920a163d7")
    before = _evidence(path.parent)
    for read_only in (True, False):
        with pytest.raises(SyncBaseStoreCorruptionError, match="sidecar"):
            SyncBaseStore.open(
                tmp_path / "state" / "dotman", "main", read_only=read_only
            )
        assert _evidence(path.parent) == before


@pytest.mark.parametrize(
    "name",
    [
        "sync-bases.sqlite3",
        "sync-bases.sqlite3.lock",
        "sync-bases.sqlite3-wal",
        "sync-bases.sqlite3-shm",
        "sync-bases.sqlite3-journal",
    ],
)
@pytest.mark.parametrize("unsafe", ["symlink", "fifo", "mode"])
def test_rejects_unsafe_files_before_open_without_repair(
    tmp_path: Path,
    name: str,
    unsafe: str,
) -> None:
    with _open_store(tmp_path):
        pass
    path = _database_path(tmp_path).with_name(name)
    target = tmp_path / "evidence"
    target.write_bytes(b"do not touch")
    if path.exists():
        path.unlink()
    if unsafe == "symlink":
        path.symlink_to(target)
    elif unsafe == "fifo":
        os.mkfifo(path, mode=0o600)
    else:
        path.write_bytes(b"evidence")
        path.chmod(0o644)
    status = path.lstat()
    with pytest.raises(SyncBaseStoreSecurityError):
        _open_store(tmp_path)
    assert path.lstat() == status
    assert target.read_bytes() == b"do not touch"
    if unsafe == "mode":
        assert path.read_bytes() == b"evidence"


@pytest.mark.parametrize(
    "relative",
    [
        "",
        "repos",
        "repos/main",
        "repos/main/sync-bases.sqlite3",
        "repos/main/sync-bases.sqlite3.lock",
        "repos/main/sync-bases.sqlite3-journal",
    ],
)
def test_wrong_owner_is_rejected_at_stat_boundary(
    tmp_path: Path,
    relative: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _open_store(tmp_path):
        pass
    root = tmp_path / "state" / "dotman"
    target = root / relative
    if relative.endswith("-journal"):
        target.write_bytes(b"evidence")
        target.chmod(0o600)
    target_inode = target.stat().st_ino
    before = _evidence(_database_path(tmp_path).parent)
    original_stat = os.stat
    original_fstat = os.fstat

    def wrong_owner(status: os.stat_result) -> os.stat_result:
        if status.st_ino != target_inode:
            return status
        values = list(status)
        values[4] = os.geteuid() + 1
        return os.stat_result(values)

    monkeypatch.setattr(
        os, "stat", lambda *a, **kw: wrong_owner(original_stat(*a, **kw))
    )
    monkeypatch.setattr(
        os, "fstat", lambda *a, **kw: wrong_owner(original_fstat(*a, **kw))
    )
    with pytest.raises(SyncBaseStoreSecurityError, match="owner"):
        _open_store(tmp_path)
    assert _evidence(_database_path(tmp_path).parent) == before


@pytest.mark.parametrize("name", ["sync-bases.sqlite3", "sync-bases.sqlite3.lock"])
def test_file_open_is_bound_to_prevalidated_inode(
    tmp_path: Path,
    name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _open_store(tmp_path):
        pass
    path = _database_path(tmp_path).with_name(name)
    original_bytes = path.read_bytes()
    saved = path.with_name("saved-" + name)
    original_open = os.open
    substituted = False

    def swap_before_open(
        file: object, flags: int, *args: object, **kwargs: object
    ) -> int:
        nonlocal substituted
        if not substituted and file == name:
            substituted = True
            path.rename(saved)
            path.write_bytes(original_bytes)
            path.chmod(0o600)
        return original_open(file, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap_before_open)
    with pytest.raises(SyncBaseStoreSecurityError, match="changed|substituted"):
        _open_store(tmp_path)
    assert substituted
    assert path.read_bytes() == saved.read_bytes() == original_bytes


@pytest.mark.parametrize("relative", ["repos", "repos/main"])
@pytest.mark.parametrize("replacement", ["directory", "symlink"])
def test_directory_substitution_between_validation_and_open_is_rejected(
    tmp_path: Path,
    relative: str,
    replacement: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _open_store(tmp_path):
        pass
    path = tmp_path / "state" / "dotman" / relative
    saved = path.with_name("saved-" + path.name)
    original_open = os.open
    substituted = False

    def swap_before_open(
        file: object, flags: int, *args: object, **kwargs: object
    ) -> int:
        nonlocal substituted
        if not substituted and file == path.name and flags & os.O_DIRECTORY:
            substituted = True
            path.rename(saved)
            if replacement == "symlink":
                path.symlink_to(saved, target_is_directory=True)
            else:
                path.mkdir(mode=0o700)
        return original_open(file, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap_before_open)
    with pytest.raises(SyncBaseStoreSecurityError):
        _open_store(tmp_path)
    assert substituted
    if replacement == "directory":
        assert list(path.iterdir()) == []


@pytest.mark.parametrize("relative", ["repos", "repos/main"])
def test_open_store_rejects_later_directory_substitution(
    tmp_path: Path,
    relative: str,
) -> None:
    with _open_store(tmp_path) as store:
        original = SyncBaseRecord(b"one", FilePresent(b"original"))
        store.replace(original)
        path = tmp_path / "state" / "dotman" / relative
        saved = path.with_name("saved-" + path.name)
        path.rename(saved)
        path.mkdir(mode=0o700)
        with pytest.raises(SyncBaseStoreSecurityError, match="substituted"):
            store.replace(SyncBaseRecord(b"one", Missing()))
        assert list(path.iterdir()) == []
        path.rmdir()
        saved.rename(path)
        assert store.read(b"one") == original


@pytest.mark.parametrize("suffix", ["-journal", "-wal", "-shm"])
def test_sidecar_created_at_sqlite_open_is_rejected_without_consuming_evidence(
    tmp_path: Path,
    suffix: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _open_store(tmp_path) as store:
        original = SyncBaseRecord(b"one", FilePresent(b"original"))
        store.replace(original)
        path = _database_path(tmp_path)
        sidecar = path.with_name(path.name + suffix)
        original_connect = sqlite3.connect

        def add_sidecar(
            database: object, *args: object, **kwargs: object
        ) -> sqlite3.Connection:
            if database != ":memory:":
                sidecar.write_bytes(b"raced sidecar evidence")
                sidecar.chmod(0o600)
            return original_connect(database, *args, **kwargs)

        monkeypatch.setattr(sqlite3, "connect", add_sidecar)
        before = path.read_bytes()
        with pytest.raises(SyncBaseStoreCorruptionError, match="sidecar"):
            store.replace(SyncBaseRecord(b"one", Missing()))
        assert sidecar.read_bytes() == b"raced sidecar evidence"
        assert path.read_bytes() == before


@pytest.mark.parametrize("operation", ["replace", "delete"])
def test_precommit_security_failure_rolls_back_record_and_payload(
    tmp_path: Path,
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _open_store(tmp_path) as store:
        original = SyncBaseRecord(b"one", FilePresent(b"original"))
        store.replace(original)
        original_check = store._layout.check

        def fail_precommit(*, allow_journal: bool = False) -> set[str]:
            if allow_journal:
                raise SyncBaseStoreSecurityError("injected before commit")
            return original_check(allow_journal=allow_journal)

        with monkeypatch.context() as patch:
            patch.setattr(store._layout, "check", fail_precommit)
            with pytest.raises(SyncBaseStoreSecurityError, match="injected"):
                if operation == "replace":
                    store.replace(SyncBaseRecord(b"one", FilePresent(b"replacement")))
                else:
                    store.delete(b"one")
        assert store.read(b"one") == original
    assert _row_count(_database_path(tmp_path), "payloads") == 1


def test_sidecars_are_private_under_permissive_umask_and_sqlite_temp_is_memory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temp = tmp_path / "sqlite-temp"
    temp.mkdir()
    monkeypatch.setenv("SQLITE_TMPDIR", str(temp))
    old_umask = os.umask(0)
    try:
        with _open_store(tmp_path) as store:
            original_collect = store._garbage_collect_payload
            observed = []

            def inspect_sqlite(
                connection: sqlite3.Connection, payload_id: int | None
            ) -> None:
                journal = Path(str(_database_path(tmp_path)) + "-journal")
                observed.append(journal.stat())
                assert connection.execute("PRAGMA temp_store").fetchone() == (2,)
                connection.execute(
                    "CREATE TEMP TABLE temp_probe AS SELECT content FROM payloads"
                )
                assert list(temp.iterdir()) == []
                original_collect(connection, payload_id)

            monkeypatch.setattr(store, "_garbage_collect_payload", inspect_sqlite)
            store.replace(SyncBaseRecord(b"one", FilePresent(b"payload")))
            assert observed
            assert all(stat.S_IMODE(status.st_mode) == 0o600 for status in observed)
            assert all(status.st_uid == os.geteuid() for status in observed)
            assert {path.name for path in store.repo_state_directory.iterdir()} == {
                "sync-bases.sqlite3",
                "sync-bases.sqlite3.lock",
            }
    finally:
        os.umask(old_umask)


@pytest.mark.parametrize("name", ["repos", "main"])
def test_directory_substituted_immediately_after_mkdir_is_not_chmodded(
    tmp_path: Path,
    name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_mkdir = os.mkdir
    substituted: Path | None = None

    def swap_after_mkdir(path: object, mode: int = 0o777, **kwargs: object) -> None:
        nonlocal substituted
        original_mkdir(path, mode, **kwargs)
        if path == name and "dir_fd" in kwargs:
            root = tmp_path / "state" / "dotman"
            actual = root / "repos" if path == "repos" else root / "repos" / "main"
            actual.rename(actual.with_name("original-" + str(path)))
            original_mkdir(path, 0o755, **kwargs)
            substituted = actual

    monkeypatch.setattr(os, "mkdir", swap_after_mkdir)
    with pytest.raises(SyncBaseStoreSecurityError):
        _open_store(tmp_path)
    assert substituted is not None
    assert stat.S_IMODE(substituted.stat().st_mode) == 0o755
    assert list(substituted.iterdir()) == []


@pytest.mark.parametrize("operation", ["replace", "delete"])
def test_successful_mutation_never_runs_security_validation_after_commit(
    tmp_path: Path,
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _open_store(tmp_path) as store:
        store.replace(SyncBaseRecord(b"one", FilePresent(b"original")))
        original_connect = sqlite3.connect
        original_check = store._layout.check
        committed = False

        def trace(sql: str) -> None:
            nonlocal committed
            if sql == "COMMIT":
                committed = True

        def traced_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
            connection = original_connect(*args, **kwargs)
            connection.set_trace_callback(trace)
            return connection

        def reject_after_commit(*, allow_journal: bool = False) -> set[str]:
            if committed:
                raise SyncBaseStoreSecurityError("security checked after commit")
            return original_check(allow_journal=allow_journal)

        monkeypatch.setattr(sqlite3, "connect", traced_connect)
        monkeypatch.setattr(store._layout, "check", reject_after_commit)
        if operation == "replace":
            store.replace(SyncBaseRecord(b"one", Missing()))
        else:
            assert store.delete(b"one") is True
        assert committed


@pytest.mark.parametrize("operation", ["replace", "delete"])
def test_sql_failure_after_record_dml_rolls_back_payload_gc(
    tmp_path: Path,
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _open_store(tmp_path) as store:
        original = SyncBaseRecord(b"one", FilePresent(b"original"))
        store.replace(original)
        original_gc = store._garbage_collect_payload

        def fail_after_gc(
            connection: sqlite3.Connection, payload_id: int | None
        ) -> None:
            original_gc(connection, payload_id)
            raise sqlite3.IntegrityError("injected transaction failure")

        with monkeypatch.context() as patch:
            patch.setattr(store, "_garbage_collect_payload", fail_after_gc)
            with pytest.raises(SyncBaseStoreCorruptionError, match="injected"):
                if operation == "replace":
                    store.replace(SyncBaseRecord(b"one", FilePresent(b"replacement")))
                else:
                    store.delete(b"one")
        assert store.read(b"one") == original
    assert _row_count(_database_path(tmp_path), "payloads") == 1


@pytest.mark.parametrize("name", ["sync-bases.sqlite3", "sync-bases.sqlite3.lock"])
def test_descriptor_owner_is_checked_even_when_path_stat_is_trusted(
    tmp_path: Path,
    name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _open_store(tmp_path):
        pass
    path = _database_path(tmp_path).with_name(name)
    inode = path.stat().st_ino
    original_fstat = os.fstat

    def wrong_descriptor_owner(descriptor: int) -> os.stat_result:
        status = original_fstat(descriptor)
        if status.st_ino == inode:
            values = list(status)
            values[4] = os.geteuid() + 1
            return os.stat_result(values)
        return status

    monkeypatch.setattr(os, "fstat", wrong_descriptor_owner)
    before = _evidence(path.parent)
    with pytest.raises(SyncBaseStoreSecurityError, match="owner"):
        _open_store(tmp_path)
    assert _evidence(path.parent) == before


def test_database_substitution_at_sqlite_open_cannot_change_either_inode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _open_store(tmp_path) as store:
        store.replace(SyncBaseRecord(b"one", FilePresent(b"original")))
        path = _database_path(tmp_path)
        saved = path.with_name("saved-database")
        before = path.read_bytes()
        original_connect = sqlite3.connect

        def substitute(
            database: object, *args: object, **kwargs: object
        ) -> sqlite3.Connection:
            if database != ":memory:":
                path.rename(saved)
                path.write_bytes(before)
                path.chmod(0o600)
            return original_connect(database, *args, **kwargs)

        monkeypatch.setattr(sqlite3, "connect", substitute)
        with pytest.raises(SyncBaseStoreSecurityError, match="substituted"):
            store.replace(SyncBaseRecord(b"one", Missing()))
        assert path.read_bytes() == saved.read_bytes() == before


def test_secure_sidecar_substitution_during_preflight_is_rejected_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _open_store(tmp_path):
        pass
    path = _database_path(tmp_path)
    sidecar = path.with_name(path.name + "-journal")
    original_pread = os.pread
    substituted = False

    def substitute(descriptor: int, length: int, offset: int) -> bytes:
        nonlocal substituted
        data = original_pread(descriptor, length, offset)
        if not substituted:
            sidecar.write_bytes(b"new recovery evidence")
            sidecar.chmod(0o600)
            substituted = True
        return data

    monkeypatch.setattr(os, "pread", substitute)
    before = path.read_bytes()
    with pytest.raises(SyncBaseStoreCorruptionError, match="sidecar"):
        _open_store(tmp_path)
    assert path.read_bytes() == before
    assert sidecar.read_bytes() == b"new recovery evidence"


def test_forged_payload_does_not_even_enter_sqlite_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _open_store(tmp_path) as store:
        store.replace(SyncBaseRecord(b"one", FilePresent(b"original")))
        payload = object.__new__(DirectoryChildPresent)
        object.__setattr__(payload, "content", b"replacement")
        object.__setattr__(payload, "executable", 1)
        original_connect = sqlite3.connect
        statements = []

        def traced_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
            connection = original_connect(*args, **kwargs)
            connection.set_trace_callback(statements.append)
            return connection

        monkeypatch.setattr(sqlite3, "connect", traced_connect)
        with pytest.raises(TypeError, match="bool"):
            store.replace(SyncBaseRecord(b"one", payload))
        assert not statements


def test_multiple_reader_transactions_can_coexist_and_writers_fail_nonblocking(
    tmp_path: Path,
) -> None:
    with _open_store(tmp_path) as writer:
        original = SyncBaseRecord(b"one", FilePresent(b"original"))
        writer.replace(original)
        root = tmp_path / "state" / "dotman"
        with SyncBaseStore.open(root, "main", read_only=True) as one:
            with (
                one.read_transaction(),
                SyncBaseStore.open(root, "main", read_only=True) as two,
                two.read_transaction(),
            ):
                assert one.read(b"one") == two.read(b"one") == original
                with pytest.raises(SyncBaseStoreLockedError):
                    writer.delete(b"one")
            assert writer.delete(b"one") is True


def test_reader_snapshots_and_write_contention_work_across_processes(
    tmp_path: Path,
) -> None:
    with _open_store(tmp_path) as writer:
        original = SyncBaseRecord(b"one", FilePresent(b"original"))
        writer.replace(original)
        root = tmp_path / "state" / "dotman"
        # The writer handle remains open; another process can still read.
        script = """
import sys
from dotman.sync_base_store import SyncBaseStore, SyncBaseStoreLockedError, FilePresent, SyncBaseRecord
with SyncBaseStore.open(sys.argv[1], 'main', read_only=True) as reader:
    assert reader.read(b'one') == SyncBaseRecord(b'one', FilePresent(b'original'))
"""
        subprocess.run([sys.executable, "-c", script, str(root)], check=True, timeout=5)
        with writer.read_transaction():
            script = """
import sys
from dotman.sync_base_store import SyncBaseStore, SyncBaseStoreLockedError
try:
    with SyncBaseStore.open(sys.argv[1], 'main') as writer:
        writer.delete(b'one')
except SyncBaseStoreLockedError:
    pass
else:
    raise AssertionError('mutation did not fail nonblocking')
"""
            subprocess.run(
                [sys.executable, "-c", script, str(root)], check=True, timeout=5
            )


def test_sqlite_build_that_forces_disk_temp_storage_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _open_store(tmp_path):
        pass
    path = _database_path(tmp_path)
    before = _evidence(path.parent)
    original_connect = sqlite3.connect

    class DiskTempConnection(sqlite3.Connection):
        def execute(self, sql: str, parameters: object = ()) -> sqlite3.Cursor:
            if sql == "PRAGMA compile_options":
                return super().execute("SELECT 'TEMP_STORE=0'")
            return super().execute(sql, parameters)

    def connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        return original_connect(*args, factory=DiskTempConnection, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    with pytest.raises(SyncBaseStoreSecurityError, match="temporary"):
        _open_store(tmp_path)
    assert _evidence(path.parent) == before


@pytest.mark.parametrize("existing_database", [False, True])
@pytest.mark.parametrize(
    "capability", ["deserialize", "dir_fd", "fd_listdir", "nofollow", "strict"]
)
def test_unsupported_runtime_fails_before_creating_database_or_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing_database: bool,
    capability: str,
) -> None:
    tmp_path = tmp_path / "runtime-probe"
    tmp_path.mkdir()
    path = _database_path(tmp_path)
    if existing_database:
        with _open_store(tmp_path):
            pass
        path.with_name(path.name + ".lock").unlink()
        before = _evidence(path.parent)
    original_connect = sqlite3.connect

    class WithoutDeserialize(sqlite3.Connection):
        def __getattribute__(self, name: str) -> object:
            if name == "deserialize":
                raise AttributeError(name)
            return super().__getattribute__(name)

    def connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        return original_connect(*args, factory=WithoutDeserialize, **kwargs)

    if capability == "deserialize":
        monkeypatch.setattr(sqlite3, "connect", connect)
    elif capability == "dir_fd":
        monkeypatch.setattr(os, "supports_dir_fd", set())
    elif capability == "fd_listdir":
        monkeypatch.setattr(os, "supports_fd", set())
    elif capability == "nofollow":
        monkeypatch.delattr(os, "O_NOFOLLOW")
    else:
        monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 36, 0))

    with pytest.raises(SyncBaseStoreUnsupportedRuntimeError, match="runtime"):
        _open_store(tmp_path)
    if existing_database:
        assert _evidence(path.parent) == before
    else:
        assert list(tmp_path.iterdir()) == []


def test_writes_use_portable_database_uri_without_descriptor_filesystem(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # This is a Linux-hosted capability fault injection, not a native macOS test.
    # Real SQLite/file I/O remains active; descriptor-filesystem paths fail.
    root = tmp_path / "state with ? and # and %" / "dotman"
    path = root / "repos" / "main" / "sync-bases.sqlite3"
    original_connect = sqlite3.connect
    opened = []

    def connect(
        database: object, *args: object, **kwargs: object
    ) -> sqlite3.Connection:
        if database != ":memory:":
            name = str(database)
            if "/proc/" in name or "/dev/fd/" in name:
                raise sqlite3.OperationalError("descriptor filesystem unavailable")
            assert name == path.as_uri() + "?mode=rw"
            assert kwargs.get("uri") is True
            assert path.is_file()
            opened.append(path.stat().st_ino)
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    original = SyncBaseRecord(b"one", FilePresent(b"original"))
    replacement = SyncBaseRecord(b"one", FilePresent(b"replacement"))
    with SyncBaseStore.open(root, "main") as store:
        store.replace(original)
        assert store.read(b"one") == original
        store.replace(replacement)
    with SyncBaseStore.open(root, "main", read_only=True) as reader:
        assert reader.read(b"one") == replacement
    with SyncBaseStore.open(root, "main") as store:
        assert store.delete(b"one")
        assert store.read(b"one") is None
    assert len(opened) == 4
    assert set(opened) == {path.stat().st_ino}
    assert {item.name for item in path.parent.iterdir()} == {
        path.name,
        path.name + ".lock",
    }
