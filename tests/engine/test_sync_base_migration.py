from __future__ import annotations

import hashlib

import pytest

from dotman.sync_base_store import (
    FilePresent,
    Missing,
    SyncBaseRecord,
    SyncBaseStore,
    SyncBaseStoreError,
    _encode,
)

RECORDS = (
    SyncBaseRecord(b"main:app.tree/a", FilePresent(b"a")),
    SyncBaseRecord(b"main:app.tree/nested/b", Missing()),
    SyncBaseRecord(b"main:app.file", FilePresent(b"file")),
)


def legacy_store(tmp_path, records=RECORDS, *, corrupt=False):
    """Write records in the flat epoch-2 layout: one sync-base-<sha(unit)>.json per unit."""
    with SyncBaseStore.open(tmp_path, "main") as store:
        directory = store.repo_state_directory
    for item in records:
        path = directory / f"sync-base-{hashlib.sha256(item.identity).hexdigest()}.json"
        path.write_bytes(b"broken" if corrupt else _encode(item))
        path.chmod(0o600)
    return directory


def snapshot(directory):
    return {path: path.read_bytes() for path in directory.rglob("*") if path.is_file()}


def test_writable_open_moves_flat_records_into_target_groups(tmp_path):
    directory = legacy_store(tmp_path)

    with SyncBaseStore.open(tmp_path, "main") as store:
        assert store.scan().records == tuple(sorted(RECORDS, key=lambda item: item.identity))
        assert store.target_records(b"main:app.tree") == RECORDS[:2]

    assert not list(directory.glob("sync-base-*.json"))


def test_read_only_open_of_flat_layout_requires_migration_without_changes(tmp_path):
    directory = legacy_store(tmp_path)
    before = snapshot(directory)

    with pytest.raises(SyncBaseStoreError, match="migrat"):
        SyncBaseStore.open(tmp_path, "main", read_only=True)

    assert snapshot(directory) == before


def test_corrupt_flat_record_aborts_migration_before_moving_anything(tmp_path):
    directory = legacy_store(tmp_path, RECORDS[:1], corrupt=True)
    before = snapshot(directory)

    with pytest.raises(SyncBaseStoreError, match="sync-base-"):
        SyncBaseStore.open(tmp_path, "main")

    assert snapshot(directory) == before
