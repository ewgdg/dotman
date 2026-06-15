# Tracked Package Details

## Intent

Resolve tracked package/target queries and build ownership-aware details for display/API output.

## Behavior

```pseudo
resolve_tracked_package(engine, package_text):
  matches = find_tracked_package_matches(engine, package_text)

  if matches is empty:
    reject missing tracked package

  if matches has more than one equally valid match:
    reject ambiguous tracked package

  return matched tracked package detail

find_tracked_target_matches(engine, target_text):
  list tracked targets from effective tracked package plans
  rank target_text against repo, package, profile, and target identifiers
  return ranked matches

summarize_targets(repo, package, context):
  for each package target:
    if target is a probe target:
      create probe target summary without repo/live ownership paths and without running the probe
    else:
      project enough target state to know repo path, live path, action, policy, hooks, and status
      create tracked target summary
  return target summaries

ownership candidates:
  include only targets that claim repo/live write paths
  skip probe targets because they do not participate in ownership conflict resolution

describe_owned_package_targets(engine, repo_name, package_id, bound_profile):
  find effective tracked entries that own package instance
  summarize owned targets
  include entry keys that made ownership effective
  return owned target details
```

## Review Needed

Ownership explanation, query ranking, and ambiguity behavior need implementation review before changes.
