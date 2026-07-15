# dotman Snapshot Model

This document captures the current snapshot lifecycle and restore semantics for `dotman`.

## Purpose

- A snapshot is a restore point created from the finalized selected plan of a real `push`.
- Snapshots exist to let the user restore managed live paths to their pre-push state.
- Snapshots are manager-level, not repo-level. One `push` run produces at most one snapshot even when it spans multiple repos.
- `push --dry-run` does not create a snapshot.

## Scope

- A snapshot captures only live paths that the finalized push plan will mutate.
- That includes file-target create, update, and delete actions.
- For directory targets, that includes each selected child-path create, update, and delete action.
- Snapshots record the pre-push state of those live paths, not the repo-side source state.
- Restore restores only the live paths recorded by the snapshot.

## Lifecycle

- Interactive target exclusion and diff review finish before snapshot creation, so the snapshot matches the exact set of planned mutations.
- Dotman creates the snapshot only when the first live mutation of a real `push` is about to begin.
- Guard-only prefixes may soft-skip without creating a snapshot.
- If the finalized work is hook-only or every selected package soft-skips before any live mutation, dotman does not create a snapshot.
- A new snapshot starts in status `prepared` while dotman is still executing the push.
- If the push completes successfully, dotman marks the snapshot as `applied`.
- If the push fails after snapshot creation, dotman keeps the snapshot and marks it as `failed`.
- If the user later restores a snapshot, dotman keeps its lifecycle status unchanged and updates restore metadata such as restore count and last restore time.

## Storage

- The default snapshot root is `$XDG_DATA_HOME/dotman/snapshots/`.
- If `XDG_DATA_HOME` is unset, dotman falls back to `~/.local/share/dotman/snapshots/`.
- Each snapshot lives in its own directory under that root.
- Snapshot storage is independent from repo tracked package state under `$XDG_STATE_HOME`.
- Snapshot restore does not depend on the current repo contents or the current tracked package set.

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

- Each snapshot has a manifest sufficient to plan and execute restore without consulting repo manifests.
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
  - the symlink handling mode needed for restore
  - the push action that triggered snapshot capture, for example `create`, `update`, or `delete`
- Provenance such as repo, selection label, package, and target labels may be recorded for human inspection, but restore correctness must not depend on them.

## Restore Semantics

- `restore` restores the selected snapshot state against the current live filesystem.
- If a recorded path existed before the push, restore should restore its recorded bytes and mode.
- If a recorded path was a symlink before the push, restore should either recreate the link target or restore the resolved target path, depending on the snapshot entry's recorded symlink handling mode.
- If a recorded path did not exist before the push, restore should remove it.
- Restore may prune empty parent directories left behind by deleting snapshot-created paths.
- Restore planning and diff review compare current live content against the snapshot-recorded desired restore state.
- Restore does not run package hooks.
- Restore fails fast if the snapshot manifest is invalid or required stored content is missing.

## Guarantees And Limits

- Snapshots cover managed live paths selected for a real `push`.
- Snapshots do not guarantee undoing side effects outside those recorded live paths.
- In particular, snapshots do not promise to undo:
  - hook side effects
  - package-manager operations triggered by hooks
  - external commands that changed unrelated files
  - manual user changes outside the recorded path set
- Snapshot behavior is generation-like UX for managed files, not a full Nix-style environment restore.

## Retention

- Snapshot retention is count-based through `snapshots.max_generations`.
- The default retained snapshot count is `10`.
- Dotman prunes the oldest snapshots when the retained snapshot count exceeds that limit.
- Retention applies to snapshot directories after snapshot status has been finalized for the push run.

## CLI Surface

- A mutation-bearing real `push` may create a snapshot when snapshots are enabled.
- `list snapshots` shows available snapshot history in a concise overview form.
- `info snapshot <snapshot>` shows detailed metadata and recorded paths for one snapshot. `latest` resolves to the newest available snapshot.
- `restore [<snapshot>]` restores the latest restorable snapshot by default and accepts `latest` explicitly as a snapshot reference alias.
