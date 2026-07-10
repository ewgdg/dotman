# Collision Detection

## Intent

Choose tracked target owners and reject plans that would write conflicting paths.

## Behavior

```pseudo
resolve_tracked_target_winners(candidates_by_live_path):
  for each live_path and candidates:
    deduplicate repeated declarations of the same package instance and target identity
    keep distinct target names from the same package instance as separate contenders
    valid_overrides = candidates that explicitly override conflicting candidates

    if valid_overrides has exactly one candidate:
      choose that candidate as winner
      record other candidates as overridden
      continue

    reject with TrackedTargetConflictError for live_path

  return winners and override records

Ownership candidates contain rendered static repo/live paths and declaration identity only.
Their resolution never depends on projected bytes, probes, directory contents, or other host-state results.

validate_target_collisions(rendered_targets):
  for each pair of rendered targets:
    if their operation write paths conflict and are not ignored by operation ignore rules:
      reject target collision

validate_reserved_path_conflicts(engine, packages, rendered_targets, context):
  compute paths reserved for dotman repo/package metadata
  for each rendered target write path:
    if write path equals or contains a reserved path, or reserved path contains write path:
      reject reserved path conflict

paths_conflict(left, right):
  return true when paths are equal or one path is under the other
```

## Review Needed

Override validity and ignore-pattern interaction are conflict-critical; verify implementation before changes.
