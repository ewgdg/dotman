# Sync Base storage

Dotman stores Sync Bases separately from tracked-package state and snapshots.
Each configured repository has one fixed-epoch SQLite database:

```text
$XDG_STATE_HOME/dotman/repos/<state_key>/sync-bases.sqlite3
```

An unsupported epoch, unsafe filesystem layout, recovery sidecar, or corrupt
container fails closed. Dotman does not repair, recreate, downgrade, quarantine,
or delete rejected evidence.

## Records and atomic acknowledgment

A record uses the exact canonical Sync Unit identity bytes supplied by
resolution. Its value is `Missing`, a present file with exact bytes, or a present
directory child with exact bytes and boolean executable state. Inputs are
recursively validated before starting a mutation.

Present content is shared only when SHA-256, byte length, **and exact bytes**
match. Digest and length narrow the lookup; they are not a unique identity.
Unequal bytes remain distinct even when a digest collision occurs.

Each replacement is an independent SQLite `BEGIN IMMEDIATE` transaction:
verify or insert the payload, replace one record, then remove only its prior
payload if no record references it. Exact deletion has the same ownership rule.
Filesystem validation happens before commit; a validation or SQL failure before
commit rolls back the record and payload together. There is no post-commit
security check that could misreport an acknowledged mutation as rejected.

The on-disk store uses SQLite's `DELETE` rollback-journal mode. A journal created
by the current transaction is private and is consumed by SQLite when that
transaction commits or rolls back. No pre-existing journal is opened for recovery.

## Read-only inspection and locking

`SyncBaseStore.open(root, state_key, read_only=True)` never creates directories,
a database, or a missing lock, and rejects replacement/deletion.
Store handles hold **no lifetime transaction or exclusive lock**.

- `read(identity)` uses a fresh SQLite read transaction.
- `with store.read_transaction(): ...` groups reads into one committed snapshot.
- Multiple readers share the repository storage lock.
- Mutations acquire an exclusive repository storage lock only for their
  transaction. Contention fails immediately rather than waiting.
- These locks are separate from the manager operation lock. Future mutation
  callers take the manager lock first; inspection never takes it.

Keep an explicit read transaction short: collect inspection data, exit the
transaction, then render it or wait for user review. Holding it across review
would unnecessarily prevent mutation.

A reader copies the current validated database inode into an in-memory,
query-only SQLite database under the shared storage lock and begins its read
transaction there. The shared lock remains held for that transaction, so
cooperating writers cannot change the source mid-snapshot. Each subsequent
transaction copies fresh committed bytes; a handle never reuses an old snapshot
for a later acknowledgment.

## Filesystem trust and rejection

The manager state directory, `repos`, and repository state directory must be
current-user-owned directories with exact mode `0700`. The database, lock, and
SQLite sidecars must be current-user-owned regular files with exact mode `0600`.
Symlinks, hard-linked files, unexpected store filenames, nonregular files,
wrong owners, and incorrect modes are rejected without permission repair.
A umask that removes required owner directory permissions therefore fails
rather than causing Dotman to chmod an unbound directory pathname.

Managed directories, the database, and the lock are pinned by file descriptors.
Opens use no-follow flags and compare `fstat` device/inode identities against
the validated directory entries. Directory-relative I/O and repeated binding
checks detect observed directory/file substitution before SQLite mutation.

Before any writable SQLite open, Dotman reads through the validated database
descriptor and validates a memory-only copy. Checks include:

- the rollback-format SQLite header and application ID;
- both epoch declarations and the exact metadata row;
- the complete fixed schema, with only its one genuine SQLite autoindex excluded;
- full `integrity_check`, including index/table consistency;
- `foreign_key_check`.

SQLite never receives an untrusted on-disk pathname for preflight. Any existing
`-journal`, `-wal`, or `-shm` is rejected, including orphan sidecars without a
main database and apparently stale or empty sidecars. This intentionally
preserves interrupted-transaction evidence rather than letting SQLite recover
or delete it. A live cooperating writer is reported as lock contention instead.

Payload digest/length failures are distinct from container corruption and
report every referencing record identity. No automatic record cleanup occurs.

SQLite temporary tables and indexes stay in memory. A SQLite build that forces
disk temporary storage is rejected; preflight makes no disk copy.

## Supported boundary and costs

Supported platforms are Linux and macOS with:

- POSIX descriptor-relative I/O, descriptor directory listing, no-follow opens,
  `pread`, and `flock`;
- Python 3.11 or newer with `sqlite3.Connection.deserialize` available (the
  Python version alone does not guarantee this optional build capability);
- SQLite 3.37 or newer for the fixed STRICT schema, with memory temporary storage.

Runtime capabilities are checked using an in-memory connection and platform
capability registries **before creating any directory, database, or lock**.
Missing capabilities raise `SyncBaseStoreUnsupportedRuntimeError`; a build
forcing disk temporary storage raises `SyncBaseStoreSecurityError`.

Writable connections use the validated database's ordinary file URI with
`mode=rw`, so SQLite cannot create a missing database. No descriptor filesystem
is required. Inode, owner, mode, directory binding, and sidecar checks surround
the native SQLite open under the transaction lock.

Portability regression tests inject unavailable descriptor-filesystem paths
and missing runtime capabilities while using real SQLite and filesystem
resources on Linux. **Native macOS execution has not been verified.**

Preflight and each read transaction require **O(database size) memory and a full
integrity scan**. Inspection should batch related reads into one short read
transaction.

The caller trusts the XDG parent outside the managed private tree. The security
boundary protects against unsafe stored state and detects observed substitution;
it does **not** isolate Dotman from a hostile process running as the same user
or root. SQLite's native VFS resolves pathnames internally. A process bypassing
the storage lock can race those internal database/sidecar accesses or overwrite
a pinned inode directly.
Such processes require external isolation, not stronger pathname claims here.
Only cooperating Dotman processes may mutate an active store.

References: [SQLite integrity checks](https://www.sqlite.org/pragma.html#pragma_integrity_check),
[rollback journaling](https://www.sqlite.org/pragma.html#pragma_journal_mode),
[temporary storage](https://www.sqlite.org/pragma.html#pragma_temp_store),
[file URI modes](https://www.sqlite.org/uri.html),
[Python deserialization availability](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.deserialize), and
[deserialization](https://www.sqlite.org/c3ref/deserialize.html).
