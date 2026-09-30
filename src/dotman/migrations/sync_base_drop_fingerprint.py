"""One-off migration: rewrite epoch-1 Sync Base records as epoch 2.

Epoch 1 stored an interpretation fingerprint that no longer gates Base use.
Epoch 2 drops that field; payloads and their digests are unchanged. The
runtime reader rejects epoch-1 records, so run this once after upgrading:

    python -m dotman.migrations.sync_base_drop_fingerprint [MANAGER_STATE_ROOT]
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

from dotman.config import default_state_root
from dotman.operation_lock import OperationLock
from dotman.sync_base_store import (
    RECORD_FILE_PREFIX,
    STORE_EPOCH,
    SyncBaseStore,
    _canonical_json,
    _decode,
    _metadata_digest,
)

LEGACY_EPOCH = 1


def _migrated_content(content: bytes, name: str) -> bytes | None:
    """Return epoch-2 bytes, or None when the record is already current."""
    container = json.loads(content)
    body = container["record"]
    if body["epoch"] == STORE_EPOCH:
        return None
    # Only an intact, canonical epoch-1 record may be rewritten; anything else
    # stays for `dotman doctor` rather than being laundered into a valid record.
    if (
        body["epoch"] != LEGACY_EPOCH
        or container["digest"] != _metadata_digest(body)
        or _canonical_json(container) != content
    ):
        raise ValueError(f"{name}: not an intact epoch-1 Sync Base record")
    del body["fingerprint"]
    body["epoch"] = STORE_EPOCH
    migrated = _canonical_json({"record": body, "digest": _metadata_digest(body)})
    _decode(migrated, name, None)  # full current-format validation, incl. payload integrity
    return migrated


def _migrate_store(store: SyncBaseStore) -> int:
    migrated = 0
    with store._write_transaction():
        directory = store._layout.descriptor
        for name in sorted(store._layout.check()):
            if not name.startswith(RECORD_FILE_PREFIX):
                continue
            with open(name, "rb", opener=lambda path, flags: os.open(path, flags | os.O_NOFOLLOW, dir_fd=directory)) as stream:
                content = _migrated_content(stream.read(), name)
            if content is None:
                continue
            temporary = f".sync-base-{uuid.uuid4().hex}.tmp"
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            try:
                os.write(descriptor, content)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
            migrated += 1
        os.fsync(directory)
    return migrated


def migrate_state_root(state_root: Path) -> dict[str, int]:
    """Migrate every repository store under the manager state root; return counts per state key."""
    repos = state_root / "repos"
    results = {}
    # Hold the manager lock so no Push, Pull, or Sync reads a half-migrated store.
    with OperationLock.acquire(state_root):
        for repo_directory in sorted(repos.iterdir()) if repos.is_dir() else ():
            if not any(path.name.startswith(RECORD_FILE_PREFIX) for path in repo_directory.iterdir()):
                continue
            with SyncBaseStore.open(state_root, repo_directory.name, create=False) as store:
                results[repo_directory.name] = _migrate_store(store)
    return results


def main(argv: list[str]) -> int:
    state_root = Path(argv[0]).expanduser().absolute() if argv else default_state_root()
    for state_key, count in migrate_state_root(state_root).items():
        print(f"{state_key}: migrated {count} Sync Base records")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
