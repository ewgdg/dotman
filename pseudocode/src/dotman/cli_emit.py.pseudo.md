# CLI Output and Execution Emission

## Intent

Render stable human/JSON output and run execution when requested.

## Behavior

```pseudo
emit_payload(plans, format):
  if format is json:
    print package entries, repo hooks, and structured guard skips
    return

  print operation header
  print repo hook sections with hook labels
  print package sections with package identity and target actions
  render probe target actions without fake repo/live path lines

emit_planning_guard_skips(plans):
  for each package guard skip:
    print `skipped (guard)` with package-instance label and optional reason
  never print guard command text

run_execution(plans, args):
  mode = effective_execution_mode(args)

  if mode is dry_run:
    emit_payload(plans)
    return success

  if push plans contain unapproved live-symlink replacement hazards:
    warn user
    if user does not approve:
      reject execution
    mark hazards approved

  execute plans using execution module
  attach planning guard skips to JSON execution result
  emit execution result
  return result exit code

emit_repos / emit_tracked_packages / emit_trackables / emit_search_matches / emit_variables:
  if JSON requested:
    serialize model dictionaries
  else:
    render styled grouped human output with stable identifiers

emit_error(error):
  if error has structured fields:
    include those fields in JSON output
  else:
    render readable message and metadata labels
```

## Review Needed

Human output is user-facing. Preserve identifier format, color roles, and JSON field contracts when changing behavior.
