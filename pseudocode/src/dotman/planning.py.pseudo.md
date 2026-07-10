# Operation Planning

## Intent

Turn tracked or requested package selections into ordered operation plans with hooks, target plans, and conflict validation.

## Behavior

```pseudo
resolve_tracked_package_selections(engine):
  read effective tracked entries from every repo
  expand entries into package selections in dependency-before-dependent order
  merge duplicate selections by resolved package identity

  if same package instance is claimed with incompatible profiles:
    reject TrackedPackageProfileConflictError

  return ordered selections

resolve selection roots:
  for each requested root package:
    resolve related package ids in dependency-before-dependent order
    for each related package id:
      if it is the root package:
        add explicit root selection at that ordered position
      else:
        add implicit dependency selection owned by the root selection

build_package_planning_context(engine, repo, selection):
  resolve package with bound profile
  combine repo/package/profile variables
  include operation direction and config needed by projection
  return context

build_package_plan(engine, repo, selection, selected target metadata, run_noop):
  reuse package planning context from static candidate collection
  project only selected target metadata
  hook_plans = plan package hooks
  remove package operation guards from executable hook plans
  filter pre/post hooks to executable selected targets
  if run_noop:
    retain pre/post hooks for standalone noop work
  return PackagePlan(selection, hooks, targets)

build_package_plans(engine, selections, run_noop, optional progress sink):
  if progress sink exists:
    start it with the selection count

  for each selection before host-state planning:
    build static package context and operation-eligible target metadata
    validate static target configuration
    collect ownership candidates only for targets that claim operation write paths
    keep probes outside ownership candidate collection

  resolve ownership winners and reject same-precedence conflicts

  filter static metadata to ownership winners and probes
  validate winner collisions and reserved paths from static metadata

  for each resolved package instance with Potential Work:
    render its operation guard commands from static package context
    run commands once in declaration order with closed stdin and captured output
    if a command exits 0:
      continue the guard list
    if a command exits 100:
      record one package guard skip with optional first stderr/stdout line
      omit that package from later host-state planning
    if a command is interrupted:
      interrupt planning
    otherwise:
      reject planning with status and captured detail

  for each admitted selection after collision validation and package guards:
    build host-state target metadata only for ownership winners and probes

  for each selection in original order:
    build package plan from winner and probe metadata
    after the package plan is built, advance progress
  always close progress sink before returning or raising
  return package plans plus package guard-skip diagnostics and considered repos

build_tracked_plans(engine, run_noop, optional progress sink):
  selections = resolve tracked package selections
  build package plans through static ownership and package-guard pipeline
  wrap package plans and guard-skip diagnostics in operation plan
  return operation plan

build_operation_plan(package_plans, guard skips, considered repos, run_noop):
  validate direct package conflicts
  validate reserved path conflicts
  collect repo hook plans
  finalize repo hooks against package plans
  treat an active probe target as lower-scope work for repo hook eligibility
  return OperationPlan(repo_hooks, package_plans, guard skips)

preview_package_selections_implicit_overrides(selections):
  collect tracked target candidates
  resolve candidate winners
  return overrides that would be introduced by selections
```

## Review Needed

Selection merge precedence, profile conflicts, implicit overrides, hook filtering, and operation ordering need implementation review before changes.
