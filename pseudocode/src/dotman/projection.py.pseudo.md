# Target Projection

## Intent

Compare repo/live target state and target configuration to produce concrete push/pull actions.

## Behavior

```pseudo
plan_targets(engine):
  build target metadata for selected package

  for each target:
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
  list repo children and live children with ignore rules
  for each child path:
    derive child policy from target path rules

    if child is ignored or policy excludes operation:
      skip child
    else:
      compare repo/live bytes and modes
      produce child noop/write/delete/chmod/conflict action

  return directory target plan with child items

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
```

## Review Needed

Sync-policy matrix, target-kind inference, directory child policy, patch-capture validation, and command environments need implementation review before changes.
