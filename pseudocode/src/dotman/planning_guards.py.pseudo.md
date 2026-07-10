# Planning Guards

## Intent

Evaluate repo, package-instance, target, and active directory path-rule eligibility once per operation-plan build.

## Behavior

```pseudo
evaluate_hierarchical_guards(static planning inputs, operation, run_noop, progress sink):
  group static inputs by repo in planning order
  for each repo with Potential Work:
    run repo operation guard once from repo-static context
    if exit 100:
      record repo skip
      omit every lower input in that repo
      advance progress for omitted package selections

  for each package instance with Potential Work in admitted repos:
    run package operation guard once from package-static context
    if exit 100:
      record package-instance skip
      omit only that package input
      advance progress for the omitted selection
    do not propagate dependency skips to dependents

  for each operation-eligible target in admitted package inputs:
    run target operation guard once from target-static context
    if exit 100:
      record target skip
      omit only that target metadata from host planning

  return admitted static inputs and ordered repo/package/target skip diagnostics

evaluate_directory_path_rule_guards(path rules, managed candidate paths, operation, target context and environment):
  remaining paths = every managed repo/live candidate after operation ignores, control-file exclusions, and skip markers

  for each path rule in declaration order:
    select remaining paths matching its pattern
    if none match or operation guard is absent:
      continue

    run the rule operation guard once with target-root environment plus DOTMAN_PATH_RULE_PATTERN
    do not expose one child path

    if guard exits 100:
      record one path-rule skip using target identity plus separate pattern metadata
      remove every matching path from remaining paths

  return remaining paths and ordered path-rule skip diagnostics

run planning guard command list:
  render commands from declared static context
  use captured pipe I/O, configured elevation, and closed stdin
  exclude DOTMAN_ASSUME_YES from inherited environment
  exit 0 continues
  exit 100 stops the list and returns scoped skip with first stderr/stdout line
  interrupt status interrupts planning
  any other nonzero raises GuardPlanningError with status, captured detail, and separate path-rule pattern metadata when applicable
```
