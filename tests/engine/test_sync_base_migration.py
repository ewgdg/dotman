import json

import pytest

from dotman.migrations.sync_base_drop_fingerprint import migrate_state_root
from dotman.sync_base_store import (
    DirectoryChildPresent, FilePresent, Missing, SyncBaseRecord, SyncBaseRecordCorruptionError,
    SyncBaseStore, _canonical_json, _metadata_digest,
)


def write_epoch_one_record(store, identity, payload):
    """Rewrite a current record into the epoch-1 layout that still carried a fingerprint."""
    store.replace(SyncBaseRecord(identity, payload))
    path = store.record_path(identity)
    body = json.loads(path.read_bytes())["record"]
    body.update(epoch=1, fingerprint="a" * 64)
    path.write_bytes(_canonical_json({"record": body, "digest": _metadata_digest(body)}))
    return path


PAYLOADS = {b"main:app.file": FilePresent(b"data"), b"main:app.gone": Missing(),
            b"main:app.tree/tool": DirectoryChildPresent(b"#!", True)}


def test_epoch_one_records_are_rewritten_and_stay_usable(tmp_path):
    state = tmp_path / "state"
    with SyncBaseStore.open(state, "main") as store:
        for identity, payload in PAYLOADS.items():
            write_epoch_one_record(store, identity, payload)
        with pytest.raises(SyncBaseRecordCorruptionError):
            store.read(b"main:app.file")
    assert migrate_state_root(state) == {"main": 3}
    assert migrate_state_root(state) == {"main": 0}
    with SyncBaseStore.open(state, "main", read_only=True) as store:
        scan = store.scan()
        assert scan.corrupt_count == 0
        assert {record.identity: record.payload for record in scan.records} == PAYLOADS


def test_tampered_epoch_one_record_fails_without_rewrite(tmp_path):
    state = tmp_path / "state"
    with SyncBaseStore.open(state, "main") as store:
        path = write_epoch_one_record(store, b"main:app.file", FilePresent(b"data"))
    tampered = path.read_bytes().replace(b'"epoch":1', b'"epoch":1 ')
    path.write_bytes(tampered)
    with pytest.raises(ValueError, match="epoch-1"):
        migrate_state_root(state)
    assert path.read_bytes() == tampered
