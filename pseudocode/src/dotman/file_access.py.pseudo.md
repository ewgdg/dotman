# File Access and Sudo Helpers

## Intent

Use direct filesystem operations when allowed and privileged helper operations when required.

## Behavior

```pseudo
needs_sudo_for_read(path):
  if current user can read path directly:
    return false
  return true

needs_sudo_for_write(path):
  if destination or nearest existing parent is writable by current user:
    return false
  return true

needs_sudo_for_chmod(path):
  if current user can chmod path directly:
    return false
  return true

read_bytes(path):
  if needs_sudo_for_read(path):
    request sudo with read reason
    run privileged read operation
  else:
    read path directly

write_bytes_atomic / write_symlink_atomic / delete / chmod:
  if operation needs sudo:
    request sudo with operation reason
    run privileged helper operation
    restore repo path access when needed
  else:
    run local filesystem helper

sudo_session(reason):
  start sudo keepalive lease
  yield to caller
  close lease on exit
```

## Review Needed

Permission probes, keepalive lifecycle, and access restoration need implementation review before privileged behavior changes.
