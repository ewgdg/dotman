# Privileged Operation Helper

## Intent

Expose a narrow sudo-only filesystem operation surface for the main process.

## Behavior

```pseudo
main(argv):
  parse operation name and operation arguments

  if operation name is unsupported:
    return failure

  if arguments are invalid:
    return failure

  run requested supported operation

  if operation succeeds:
    write result to stdout when operation returns data
    return success

  if operation fails:
    render PrivilegedOperationError
    return failure

supported operations:
  read bytes
  write bytes atomically
  write symlink atomically
  delete path and prune empty parents
  chmod path
  list directory files
```

## Review Needed

Argument format and allowed operation set are privilege boundaries; verify implementation before changes.
