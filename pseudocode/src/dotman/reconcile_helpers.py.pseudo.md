# Reconcile Helper Commands

## Intent

Provide specialized reconcile helper entrypoints for configured reconcile commands.

## Behavior

```pseudo
run_jinja_reconcile(args):
  read Jinja reconcile inputs

  if required input is missing or invalid:
    reject helper invocation

  run Jinja-oriented reconcile flow
  write accepted template/source changes when user confirms
  return command status
```

## Review Needed

Helper arguments and Jinja-specific source update behavior need implementation review before changes.
