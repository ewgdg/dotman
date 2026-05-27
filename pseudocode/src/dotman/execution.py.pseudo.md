# Plan Execution

## Intent

Execute operation plans in order, applying filesystem changes, hooks, reconcile commands, patch capture, sudo, and failure propagation.

## Behavior

```pseudo
build_execution_session(plans):
  if push plans contain unapproved live-symlink replacement hazards:
    reject execution

  group package plans into repo execution units
  build ordered steps for repo hooks, package hooks, target actions, chmods, and guards
  return execution session

execute_session(session):
  preflight sudo requirements for all steps that need it

  for each repo unit in order:
    for each package unit in order:
      execute package unit

      if package has blocking failure:
        mark remaining dependent steps/packages skipped as required

  return ExecutionResult with repo/package/step results

_execute_step(step):
  if step is guard:
    return guard status

  if step is hook command:
    build hook env
    require terminal when hook uses terminal passthrough
    run command
    return command status

  if step is target action:
    execute target step
    return target status

_execute_target_step(step):
  if action writes bytes:
    write desired bytes atomically to destination
  else if action writes symlink:
    replace symlink atomically
  else if action deletes path:
    delete path and prune empty parents
  else if action chmods path:
    apply desired mode
  else if action uses reconcile:
    materialize review env and run reconcile command
  else if action uses patch capture:
    run patch capture and apply resulting bytes
  else:
    return noop or skipped status

patch-capture fallback:
  if patch capture fails and reconcile fallback is allowed:
    run reconcile action for the same target
  else:
    return failure

filesystem operations:
  if direct access is insufficient:
    request sudo or elevation path
  use atomic writes for file and symlink replacement
```

## Review Needed

Execution ordering, failure propagation, sudo/elevation, patch-capture fallback, and filesystem side effects are high-risk; verify implementation before changes.
