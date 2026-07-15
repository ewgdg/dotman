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
  if output is JSON or stderr is not interactive:
    use no planning progress sink
  else:
    use terminal planning progress sink

  if query provided:
    plan operation for query with run_noop input
  else:
    plan operation from tracked state with run_noop input and progress sink

  emit human package guard-skip diagnostics before review or selection

  if guard decisions leave no effective work:
    emit structured JSON planning payload when requested
    return success without review, selection, execution, or snapshots

  apply diff review when requested
  apply interactive target selection when requested

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

edit repo:
  load manager config without constructing the engine
  resolve the requested configured repo name
  open the configured repo root with the editor
  if no editor is configured, print the repo root and succeed

list repo:
  read configured repos from manager config in configured order
  emit repo list before engine construction
  do not load repo manifests, tracked package state, or package catalogs

_handle_restore(args):
  resolve args.snapshot; when omitted, resolver selects latest restorable snapshot
  build restore actions and run snapshot-to-live diff review
  if dry-run, emit restore plan; otherwise execute restore and record successful restore metadata
```

## Review Needed

Command-specific confirmation order and dry-run/write split need implementation review before changes.
