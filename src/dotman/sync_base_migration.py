"""One-time move of flat Sync Base records into target groups.

Older stores kept every record as `sync-base-<sha256(identity)>.json` directly
in the repository state directory. The move runs under the exclusive storage
lock, validates every record before moving any, and is safe to resume: each
record is written to its group before its flat file is removed.
"""

from __future__ import annotations

import hashlib
import os
from typing import TYPE_CHECKING, Final

from dotman.sync_base_store import (
    RECORD_FILE_SUFFIX,
    SyncBaseRecord,
    SyncBaseRecordCorruptionError,
    SyncBaseStoreError,
    _PrivateLayout,
    _record_location,
)

if TYPE_CHECKING:
    from dotman.sync_base_store import SyncBaseStore

LEGACY_RECORD_PREFIX: Final = "sync-base-"


def _legacy_name(identity: bytes) -> str:
    return f"{LEGACY_RECORD_PREFIX}{hashlib.sha256(identity).hexdigest()}{RECORD_FILE_SUFFIX}"


def flat_record_names(layout: _PrivateLayout) -> list[str]:
    return sorted(
        name
        for name in layout.file_names(layout.descriptor)
        if name.startswith(LEGACY_RECORD_PREFIX) and name.endswith(RECORD_FILE_SUFFIX)
    )


def _read_flat_records(store: SyncBaseStore) -> list[tuple[str, SyncBaseRecord]]:
    layout = store._layout
    records = []
    unmigratable = []
    for name in flat_record_names(layout):
        try:
            record = store._read_file(layout.root, name, location=name, locate=_legacy_name)
            if record is None:
                continue
            _record_location(record.identity)
        except (SyncBaseRecordCorruptionError, ValueError):
            unmigratable.append(str(layout.directory / name))
            continue
        records.append((name, record))
    if unmigratable:
        raise SyncBaseStoreError(
            "cannot migrate Sync Base records; repair or remove: " + ", ".join(unmigratable)
        )
    return records


def migrate_flat_records(store: SyncBaseStore) -> None:
    with store._write_transaction():
        records = _read_flat_records(store)
        descriptor = store._layout.descriptor
        for name, record in records:
            store._replace_locked(record)
            os.unlink(name, dir_fd=descriptor)
        os.fsync(descriptor)
