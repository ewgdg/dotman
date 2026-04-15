# dotman Snapshot Model

This document captures the planned snapshot lifecycle and rollback semantics for `dotman`.

## Purpose

- A snapshot is a restore point created from the finalized selected plan of a real `push`.
- Snapshots exist to let the user roll managed live paths back to their pre-push state.
- Snapshots are manager-level, not repo-level. One `push` run produces at most one snapshot even when it spans multiple repos.
- `push --dry-run` does not create a snapshot.

## Scope

- A snapshot should capture only live paths that the finalized push plan will mutate.
- That includes file-target create, update, and delete actions.
- For directory targets, that includes each selected child-path create, update, and delete action.
- Snapshots should record the pre-push state of those live paths, not the repo-side source state.
- Rollback should restore only the live paths recorded by the snapshot.

## Lifecycle

- Interactive target exclusion and diff review must finish before snapshot creation, so the snapshot matches the exact set of planned mutations.
- Dotman should create the snapshot before the first live mutation of a real `push`.
- A new snapshot should start in status `prepared` while dotman is still executing the push.
- If the push completes successfully, dotman should mark the snapshot as `applied`.
- If the push fails after snapshot creation, dotman should keep the snapshot and mark it as `failed`.
- If the user later restores a snapshot, dotman should keep its lifecycle status unchanged and instead update restore metadata such as restore count and last restore time.

## Storage

- The default snapshot root should be `$XDG_DATA_HOME/dotman/snapshots/`.
- If `XDG_DATA_HOME` is unset, dotman should fall back to `~/.local/share/dotman/snapshots/`.
- Each snapshot should live in its own directory under that root.
- Snapshot storage should be independent from repo binding state under `$XDG_STATE_HOME`.
- Snapshot restore must not depend on the current repo contents or the current tracked binding set.

Example layout:

```text
$XDG_DATA_HOME/dotman/snapshots/
  2026-04-09T14-22-11Z-8f3d2c/
    manifest.toml
    entries/
      0001.bin
      0002.bin
```

## Manifest Expectations

- Each snapshot should have a manifest that is sufficient to plan and execute rollback without consulting repo manifests.
- The manifest should record snapshot metadata such as:
  - snapshot ID
  - creation time
  - status
  - restore count
  - last restore time when the snapshot has been restored before
  - selected operation scope summary
- For each recorded live path, the manifest should record enough pre-push state to restore it later, including:
  - absolute live path
  - whether the path existed before the push
  - the pre-push file content blob reference when the path existed
  - the pre-push file mode when relevant to restore
  - the original symlink target when the live path itself was a symlink
  - the symlink handling mode needed for rollback
  - the push action that triggered snapshot capture, for example `create`, `update`, or `delete`
- Provenance such as repo, binding, package, and target labels may be recorded for human inspection, but rollback correctness must not depend on them.

## Rollback Semantics

- `rollback` should restore the selected snapshot state against the current live filesystem.
- If a recorded path existed before the push, rollback should restore its recorded bytes and mode.
- If a recorded path was a symlink before the push, rollback should either recreate the link target or restore the resolved target path, depending on the snapshot entry's recorded symlink handling mode.
- If a recorded path did not exist before the push, rollback should remove it.
- Rollback may prune empty parent directories left behind by deleting snapshot-created paths.
- Rollback planning and diff review should compare current live content against the snapshot-recorded desired restore state.
- Rollback should not run package hooks.
- Rollback should fail fast if the snapshot manifest is invalid or required stored content is missing.

## Guarantees And Limits

- Snapshots cover managed live paths selected for a real `push`.
- Snapshots do not guarantee undoing side effects outside those recorded live paths.
- In particular, snapshots do not promise to undo:
  - hook side effects
  - package-manager operations triggered by hooks
  - external commands that changed unrelated files
  - manual user changes outside the recorded path set
- Snapshot behavior is generation-like UX for managed files, not a full Nix-style environment rollback.

## Retention

- Snapshot retention should be count-based through `snapshots.max_generations`.
- The default retained snapshot count should be `10`.
- Dotman should prune the oldest snapshots when the retained snapshot count exceeds that limit.
- Retention should apply to snapshot directories after snapshot status has been finalized for the push run.

## CLI Surface

- `push` creates a snapshot only for real execution.
- `list snapshots` shows available snapshot history in a concise overview form.
- `info snapshot <snapshot>` shows detailed metadata and recorded paths for one snapshot. `latest` should resolve to the newest available snapshot.
- `rollback [<snapshot>]` restores the latest restorable snapshot by default, and should also accept `latest` explicitly as a snapshot reference alias.
