# Push Snapshots and Restore

## Intent

Capture live state before push and restore that state through restore actions.

## Behavior

```pseudo
create_push_snapshot(plans, snapshot_config):
  collect snapshot entries for push target plans that may change live paths
  skip probe targets because they do not own or change live paths

  if no entries need snapshot:
    return no snapshot or empty snapshot according to config

  create new snapshot id and directory
  write entry payloads for existing files, symlinks, modes, and missing paths
  write snapshot manifest with pending status and provenance
  prune old snapshots according to retention config
  return snapshot record

mark_snapshot_status(snapshot, status):
  update snapshot manifest status

list_snapshots(snapshot_root):
  read snapshot manifests
  sort snapshots by timestamp/id
  return snapshot records

resolve_snapshot(snapshot_root, reference):
  find snapshots matching id or prefix

  if no matches:
    reject missing snapshot

  if multiple matches:
    reject ambiguous snapshot

  return matching snapshot

build_restore_actions(snapshot):
  for each snapshot entry:
    if entry recorded missing live path:
      create delete action
    else if entry recorded file bytes:
      create restore file action and mode action when needed
    else if entry recorded symlink:
      create restore symlink action
  return restore actions

execute_restore(snapshot, actions):
  execute actions in order
  record per-action result
  update snapshot restore metadata
  return restore result
```

## Review Needed

Snapshot schema, empty-snapshot behavior, retention pruning, and restore ordering need implementation review before changes.
