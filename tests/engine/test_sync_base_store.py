from __future__ import annotations

import json
import os

import pytest

from dotman.sync_base_store import (
    DirectoryChildPresent,
    FilePresent,
    Missing,
    SyncBaseEnvelope,
    SyncBaseRecord,
    SyncBaseRecordCorruptionError,
    SyncBaseStore,
    SyncBaseStoreError,
    SyncBaseStoreLockedError,
    SyncBaseStoreSecurityError,
)


def record(identity=b"unit", payload=None):
    return SyncBaseRecord(
        identity,
        payload if payload is not None else FilePresent(b"data"),
        SyncBaseEnvelope("a" * 64),
    )


def record_path(store):
    return next(store.repo_state_directory.glob("sync-base-*.json"))


@pytest.mark.parametrize(
    "payload",
    [
        Missing(),
        FilePresent(b""),
        FilePresent(b"\x00\xff"),
        DirectoryChildPresent(b"script", True),
        DirectoryChildPresent(b"script", False),
    ],
)
def test_typed_payload_roundtrip(tmp_path, payload):
    root = tmp_path / "manager"
    with SyncBaseStore.open(root, "repo") as store:
        store.replace(record(payload=payload))
        assert store.record_path(b"unit").is_file()
        assert store.record_path(b"unit").parent == store.repo_state_directory
    with SyncBaseStore.open(root, "repo", read_only=True) as store:
        assert store.read(b"unit") == record(payload=payload)
        assert store.read(b"absent") is None
        assert store.identities() == (b"unit",)


def test_private_self_contained_records(tmp_path):
    with SyncBaseStore.open(tmp_path / "manager", "repo") as store:
        store.replace(record(b"a"))
        store.replace(record(b"b"))
        files = list(store.repo_state_directory.glob("sync-base-*.json"))
        assert len(files) == 2
        for path in (tmp_path / "manager").rglob("*"):
            assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)
        files[0].unlink()
        assert len(store.identities()) == 1


def test_corruption_is_per_unit_and_explicitly_discardable(tmp_path):
    with SyncBaseStore.open(tmp_path / "manager", "repo") as store:
        store.replace(record())
        path = record_path(store)
        store.replace(record(b"healthy"))
        path.write_bytes(b"broken")
        with pytest.raises(SyncBaseRecordCorruptionError) as error:
            store.read(b"unit")
        assert error.value.affected_identities == (b"unit",)
        assert store.read(b"healthy") == record(b"healthy")
        assert store.discard_corrupt(b"unit")
        assert not store.discard_corrupt(b"healthy")
        assert store.read(b"unit") is None


def test_scan_isolates_corruption_without_cleanup(tmp_path):
    with SyncBaseStore.open(tmp_path / "manager", "repo") as store:
        store.replace(record(b"healthy"))
        store.replace(record(b"broken"))
        broken_path = store.record_path(b"broken")
        broken_path.write_bytes(b"broken")
        before = broken_path.read_bytes()
        with store.read_transaction():
            scanned = store.scan()
            assert scanned.records == (record(b"healthy"),)
            assert scanned.corrupt_count == 1
            assert store.identities() == (b"healthy",)
            assert store.read(b"healthy") == record(b"healthy")
            with pytest.raises(SyncBaseRecordCorruptionError):
                store.read(b"broken")
        assert broken_path.read_bytes() == before


def test_failed_replacement_preserves_previous_record(tmp_path, monkeypatch):
    with SyncBaseStore.open(tmp_path / "manager", "repo") as store:
        store.replace(record())

        def fail(*args, **kwargs):
            raise OSError("injected replacement failure")

        monkeypatch.setattr(os, "replace", fail)
        with pytest.raises(SyncBaseStoreError):
            store.replace(record(payload=FilePresent(b"new")))
        assert store.read(b"unit") == record()
        assert len(list(store.repo_state_directory.glob("sync-base-*.json"))) == 1


def test_read_transaction_excludes_writer_and_refreshes(tmp_path):
    root = tmp_path / "manager"
    with (
        SyncBaseStore.open(root, "repo") as first,
        SyncBaseStore.open(root, "repo") as second,
    ):
        first.replace(record())
        with first.read_transaction():
            assert first.read(b"unit") == record()
            with pytest.raises(SyncBaseStoreLockedError):
                second.replace(record(payload=Missing()))
            with second.read_transaction():
                assert second.read(b"unit") == record()
            with pytest.raises(SyncBaseStoreError):
                first.delete(b"unit")
        second.replace(record(payload=Missing()))
        assert first.read(b"unit") == record(payload=Missing())


@pytest.mark.parametrize("mode", [0o644, 0o666])
def test_rejects_nonprivate_record_without_repair(tmp_path, mode):
    with SyncBaseStore.open(tmp_path / "manager", "repo") as store:
        store.replace(record())
        path = record_path(store)
        path.chmod(mode)
        with pytest.raises(SyncBaseStoreSecurityError):
            store.read(b"unit")
        with pytest.raises(SyncBaseStoreSecurityError):
            store.scan()
        with pytest.raises(SyncBaseStoreSecurityError):
            store.replace(record(payload=Missing()))
        assert path.stat().st_mode & 0o777 == mode


def test_rejects_symlink_record_and_directory(tmp_path):
    root = tmp_path / "manager"
    with SyncBaseStore.open(root, "repo") as store:
        store.replace(record())
        path = record_path(store)
        outside = tmp_path / "outside"
        path.rename(outside)
        path.symlink_to(outside)
        with pytest.raises(SyncBaseStoreSecurityError):
            store.read(b"unit")
        with pytest.raises(SyncBaseStoreSecurityError):
            store.delete(b"unit")
        assert outside.exists()
    root.rename(tmp_path / "moved")
    root.symlink_to(tmp_path / "moved", target_is_directory=True)
    with pytest.raises(SyncBaseStoreSecurityError):
        SyncBaseStore.open(root, "repo")


def test_read_only_never_creates_and_cannot_mutate(tmp_path):
    root = tmp_path / "manager"
    with pytest.raises(SyncBaseStoreError):
        SyncBaseStore.open(root, "repo", read_only=True)
    assert not root.exists()
    with SyncBaseStore.open(root, "repo") as store:
        store.replace(record())
    before = {
        p: (p.read_bytes(), p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file()
    }
    with SyncBaseStore.open(root, "repo", read_only=True) as store:
        assert store.read(b"unit") == record()
        with pytest.raises(SyncBaseStoreError):
            store.delete(b"unit")
    assert before == {
        p: (p.read_bytes(), p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file()
    }


def test_identity_binding_and_integrity(tmp_path):
    with SyncBaseStore.open(tmp_path / "manager", "repo") as store:
        store.replace(record())
        first = record_path(store)
        original = first.read_bytes()
        store.replace(record(b"other"))
        other = next(
            p for p in store.repo_state_directory.glob("sync-base-*.json") if p != first
        )
        other.write_bytes(original)
        with pytest.raises(SyncBaseRecordCorruptionError):
            store.read(b"other")
        first.write_bytes(original.replace(b'"fingerprint":"a', b'"fingerprint":"b'))
        with pytest.raises(SyncBaseRecordCorruptionError):
            store.read(b"unit")


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("content", "YmFk", "payload_corrupt"),
        ("content", "YmFkIQ==", "payload_corrupt"),
        ("content", "!invalid-base64!", "payload_corrupt"),
        ("content", None, "payload_corrupt"),
        ("fingerprint", "b" * 64, "record_corrupt"),
    ],
)
def test_payload_corruption_is_distinct_from_record_corruption(
    tmp_path, field, value, reason
):
    with SyncBaseStore.open(tmp_path / "manager", "repo") as store:
        store.replace(record())
        path = store.record_path(b"unit")
        encoded = json.loads(path.read_bytes())
        encoded["record"][field] = value
        path.write_text(json.dumps(encoded, sort_keys=True, separators=(",", ":")))
        before = path.read_bytes()
        with pytest.raises(SyncBaseRecordCorruptionError) as error:
            store.read(b"unit")
        assert error.value.reason == reason
        assert error.value.affected_identities == (b"unit",)
        assert store.scan().corrupt_count == 1
        assert path.read_bytes() == before
        assert store.discard_corrupt(b"unit")


def test_delete_contract(tmp_path):
    with SyncBaseStore.open(tmp_path / "manager", "repo") as store:
        assert not store.delete(b"unit")
        store.replace(record())
        assert store.delete(b"unit")
        assert not store.delete(b"unit")
        assert store.identities() == ()


def test_failed_file_flush_preserves_previous_record(tmp_path, monkeypatch):
    with SyncBaseStore.open(tmp_path / "manager", "repo") as store:
        store.replace(record())

        def fail(descriptor):
            raise OSError("injected flush failure")

        monkeypatch.setattr(os, "fsync", fail)
        with pytest.raises(SyncBaseStoreError):
            store.replace(record(payload=Missing()))
        assert store.read(b"unit") == record()
        assert not list(store.repo_state_directory.glob("*.tmp"))


def test_rejects_hardlinked_record(tmp_path):
    with SyncBaseStore.open(tmp_path / "manager", "repo") as store:
        store.replace(record())
        os.link(record_path(store), tmp_path / "alias")
        with pytest.raises(SyncBaseStoreSecurityError):
            store.read(b"unit")
        with pytest.raises(SyncBaseStoreSecurityError):
            store.discard_corrupt(b"unit")


def test_missing_lock_is_not_recreated_over_existing_records(tmp_path):
    from dotman.sync_base_store import LOCK_FILE_NAME

    root = tmp_path / "manager"
    with SyncBaseStore.open(root, "repo") as store:
        store.replace(record())
        directory = store.repo_state_directory
    (directory / LOCK_FILE_NAME).unlink()
    with pytest.raises(SyncBaseStoreSecurityError):
        SyncBaseStore.open(root, "repo")
    assert not (directory / LOCK_FILE_NAME).exists()


def test_pinned_directory_substitution_is_rejected(tmp_path):
    with SyncBaseStore.open(tmp_path / "manager", "repo") as store:
        store.replace(record())
        directory = store.repo_state_directory
        directory.rename(directory.with_name("moved"))
        directory.mkdir(mode=0o700)
        with pytest.raises(SyncBaseStoreSecurityError):
            store.read(b"unit")
        with pytest.raises(SyncBaseStoreSecurityError):
            store.replace(record())
        assert list(directory.iterdir()) == []


def test_failed_unit_replacement_does_not_prevent_another_unit(tmp_path, monkeypatch):
    with SyncBaseStore.open(tmp_path / "manager", "repo") as store:
        store.replace(record())
        original = os.replace

        def fail_once(*args, **kwargs):
            monkeypatch.setattr(os, "replace", original)
            raise OSError("injected per-unit failure")

        monkeypatch.setattr(os, "replace", fail_once)
        with pytest.raises(SyncBaseStoreError):
            store.replace(record(payload=Missing()))
        store.replace(record(b"other"))
        assert store.read(b"unit") == record()
        assert store.read(b"other") == record(b"other")
