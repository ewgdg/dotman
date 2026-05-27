# CLI Command Dispatch

## Intent

Route parsed subcommands to engine operations, review gates, state writes, and output emitters.

## Behavior

```pseudo
dispatch_command(args, handlers):
  if command does not need engine:
    run pre-engine command
    return result

  create engine from config path
  run command-specific handler
  return handler result

_handle_track(args):
  resolve track request to package entry

  if request would replace existing tracked entry:
    require replacement confirmation

  if request creates implicit overrides:
    require override confirmation

  if dry-run:
    emit planned tracked entry without writing
  else:
    record tracked package entry
    emit recorded result

_handle_push_or_pull(args):
  if query provided:
    plan operation for query
  else:
    plan operation from tracked state

  apply interactive target selection when requested
  apply diff review when requested

  if run requested:
    execute plans
  else:
    emit dry-run payload

_handle_untrack(args):
  resolve persisted tracked entry or group selection
  if ambiguous:
    prompt or reject
  remove selected persisted entries
  emit removed entries

_handle_info(args):
  resolve requested tracked or trackable object
  emit detail in requested format
```

## Review Needed

Command-specific confirmation order and dry-run/write split need implementation review before changes.
