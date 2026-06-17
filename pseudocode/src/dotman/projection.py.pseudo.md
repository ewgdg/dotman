# Target Projection

## Intent

Compare repo/live target state and target configuration to produce concrete push/pull actions.

## Behavior

```pseudo
plan_targets(engine):
  build target metadata for selected package

  for each target:
    if target is a probe target:
      validate it has no file payload fields
      run probe command with target/package/repo planning environment
      if probe exits 0:
        emit action = "probe" with target_kind = "probe"
      else if probe exits 100:
        emit action = "noop" with target_kind = "probe"
      else:
        reject planning with probe failure details
      skip file/directory validation and path collision ownership
      continue

    validate target type and patch-capture config

    if target is directory:
      plan_directory_action
    else:
      plan_file_action

  validate projected target collisions
  return target plans

plan_file_action(engine):
  determine target kind from explicit type, repo path, live path, and sync policy

  if sync policy disallows current operation:
    return noop target plan

  if live path is a forbidden symlink declaration:
    reject target

  if repo and live content are equal and mode is acceptable:
    return noop target plan

  if operation is push:
    choose write/delete/symlink/chmod/reconcile/patch-capture action based on repo desired state and live state

  if operation is pull:
    choose write/delete/chmod/reconcile/patch-capture action based on live desired state and repo state

  if states conflict and cannot be resolved by configured reconcile/capture:
    return conflict target plan

plan_directory_action(engine):
  choose operation-scoped ignore rules
  use repo-level skip markers for both push and pull
  set follow_dir_symlinks from engine config symlinks.dir_symlink_mode == "follow"
  list repo children and live children with ignore rules, skip markers, and follow_dir_symlinks

  if scanner sees an ignored nested directory symlink:
    ignore it
  else if scanner sees a nested directory symlink and follow_dir_symlinks is false:
    reject planning instead of silently skipping it
  else if scanner follows nested directory symlinks:
    include files under symlink-relative paths and reject symlink loops

  for each child path:
    derive child policy from target path rules

    if child is ignored or policy excludes operation:
      skip child
    else:
      compare repo/live bytes and modes
      produce child noop/write/delete/chmod/conflict action
      when push planning already materialized child review-side bytes:
        store those planned bytes on the child item for review reuse
      when bytes were not needed during planning:
        leave review bytes absent so review can load them lazily if requested

  return directory target plan with child items

plan_live_delete_directory_action(engine):
  set follow_dir_symlinks from engine config for push-only-delete directory targets
  list live children with ignore rules, skip markers, and follow_dir_symlinks
  create delete item for each listed live child
  return delete plan when any listed child exists, otherwise noop

project_repo_file(engine):
  if target has render command:
    run render command and use stdout bytes
  else if source is Jinja template:
    render template with target context
  else:
    read source file bytes
  return desired repo-side bytes

run_command_projection(engine):
  build target command environment
  run configured command

  if command exits nonzero:
    reject projection

  return command stdout bytes

run_probe_command(engine):
  build target command environment without assuming meaningful repo/live file paths
  run configured probe command during push/pull planning
  return active when command exits 0
  return inactive when command exits 100
  reject on any other nonzero exit
```

## Review Needed

Sync-policy matrix, target-kind inference, directory child policy, patch-capture validation, and command environments need implementation review before changes.
