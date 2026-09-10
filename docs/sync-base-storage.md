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
recursively validated before starting a mutation. Every record also requires an
envelope containing the full real commit OID, Git object format (SHA-1 or
SHA-256), effective-input SHA-256 fingerprint, and exact/conservative provenance.
The envelope and payload are replaced together in the same transaction.

The shared [Base lifecycle](sync.md) owns applicability, provenance construction,
and acknowledgment/deletion timing; the store does not infer any of them.

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
- These locks are separate from the manager operation lock. Real SyncSession
  opening takes the manager lock first; inspection never takes it. Direct
  infrastructure mutation callers must provide their own operation lifetime.

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
and wrong owners are rejected. Opening a store never changes permissions. Insecure modes are a
store-level security failure, including during read-only inspection. No root
access is requested; the XDG parent and unrelated files are not changed.
Unexpected sidecars remain rejected with contents and inode bindings preserved.

Managed directories, the database, and the lock are pinned by file descriptors.
Opens use no-follow flags and compare `fstat` device/inode identities against
the validated directory entries. Directory-relative I/O and repeated binding
checks detect observed directory/file substitution before SQLite mutation.
Each directory scan opens a fresh stream relative to its pinned directory to
avoid stale enumeration state after creating store files.

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

Envelope validation failures report `record_corrupt` for the affected record.
Payload digest/length failures report `payload_corrupt` and every referencing
record identity. Both remain distinct from container corruption. Read-only applicability performs no cleanup. During real selected Base handling,
individually corrupt records can be deleted; a corrupt shared payload removes
every referencing Base in one transaction. Proven stale applicability may also
be removed. Store-level failures are never repaired or deleted automatically.

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


## Obsolete directory-child identities

A child deletion commits its acknowledgment independently. Only a later
successful real Sync with complete unrestricted target census proof may reclaim
the absent identity. Candidate keys are frozen before review, so the operation
cannot reclaim a deletion it just acknowledged. Partial selectors, exclusions,
ignores, markers, Guard restrictions and discovery failures cannot provide that
proof; preview and aborted sessions do no reclamation.


## Public inspection and reset

- `dotman list sync-bases` returns only currently usable entries, including usable
  Missing payloads. Empty output succeeds; unusable metadata is not disclosed.
- `dotman info sync-base main:app.settings` inspects exactly one current file Sync
  Unit. A directory child uses `main:app.tree/nested/file`; package instances use
  `main:app<work>.tree/nested/file`.
- `dotman reset sync-base main:app.settings` immediately discards exactly that
  record and payloads made unreferenced. Already absent succeeds. There is no
  confirmation, preview, wildcard, fuzzy selector, package scope, directory-target
  scope, or all-units form. Reset takes the manager non-blocking operation lock
  before resolution and fails while a real Push, Pull, or Sync owns it.
- `dotman doctor` warns with aggregate corrupt and proven orphaned record counts
  per repository, without identities or repair plans. A complete unrestricted
  directory census can prove absent children; excluded, guarded, or failed discovery cannot.
  Unsafe/unreadable stores are failures carrying repository, database path, and
  cause. Nothing is repaired.

Info reports `usable`, `unavailable`, or human `not applicable` (structured
`not-applicable`). Successful unavailable/ineligible inspection exits successfully;
invalid identities and store failures are errors. Human reasons are exactly
`absent`, `ineligible`, `inputs changed`, `commit missing`, `history changed`,
or `corrupt`. JSON reason codes are respectively `absent`, `ineligible`,
`inputs_changed`, `commit_missing`, `history_changed`, and
`record_corrupt`/`payload_corrupt`.

Structured list entries and info carry canonical identity, policy, eligibility,
status, reason, full commit OID, provenance, payload kind/size/full digest and
directory-child executable state, plus integrity, fingerprint match, commit
availability, and ancestry checks. Unknown/not-performed checks are null.
Unavailable output has null commit, provenance, and payload rather than stale
record metadata. Payload bytes are never output. Reset returns identity and
`reset` or `already_absent`.

List, info, and doctor use short read transactions, never take the manager
operation lock, and never perform cleanup. They never inspect or mutate
Verification Records or run Guards, Render, Capture, Pull View, live comparison,
or drift Observation. Static identity resolution and doctor directory-control
census do not establish ancestry. A stored Missing directory-child Base remains
inspectable until a real operation safely reclaims it.

### Real Push maintenance boundary

The real Push command opts into configured-ineligibility cleanup while holding
the manager operation lock. Cleanup follows successful static package, target,
ownership, and conflict resolution and precedes Guards and review. Only selected
configured `push-only` or `push-only-delete` file units and successfully discovered,
unexcluded directory children lose existing Bases. Eligible units and unrelated
package selections remain untouched; missing or failed child discovery never
proves a stored identity obsolete. Cleanup does not create a missing Base store.

Dry-run and ordinary engine planning remain read-only. Push acknowledgment and
full Pull convergence/Base wiring are not implemented by this maintenance path;
they remain existing integration gaps, with Pull assigned to issue #77.

Exact reset opens an existing store without creation: missing database or lock
artifacts are failures, not permission to reconstruct storage. Doctor and info
share record-shape validation against canonical file/child identities; a mismatch
is record corruption even when SQLite and the payload digest remain valid.
