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

build_package_plan(engine, repo, selection):
  context = build package planning context
  target_plans = project targets for package
  hook_plans = plan package hooks
  hook_plans = filter hooks to executable selected targets
  return PackagePlan(selection, hooks, targets)

build_tracked_plans(engine, optional progress sink):
  selections = resolve tracked package selections
  if progress sink exists:
    start it with the selection count
  for each selection:
    build package plan
    after the package plan is built, advance progress
  always close progress sink before returning or raising
  collect ownership candidates only for targets that claim repo/live write paths
  keep non-path targets such as probes after ownership winner filtering
  validate tracked ownership and direct conflicts
  return package plans

build_operation_plan(package_plans):
  validate direct package conflicts
  validate reserved path conflicts
  collect repo hook plans
  finalize repo hooks against package plans
  treat an active probe target as lower-scope work for repo hook eligibility
  return OperationPlan(repo_hooks, package_plans)

preview_package_selections_implicit_overrides(selections):
  collect tracked target candidates
  resolve candidate winners
  return overrides that would be introduced by selections
```

## Review Needed

Selection merge precedence, profile conflicts, implicit overrides, hook filtering, and operation ordering need implementation review before changes.
