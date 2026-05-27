# Repository Access Repair

## Intent

Restore repository path ownership and write access after privileged filesystem operations.

## Behavior

```pseudo
invoking_user_ids():
  if process was launched through sudo with original user ids:
    return original user and group ids
  return current process user and group ids

repo_access_paths(path):
  return path plus relevant existing parents that may need owner/write repair

ensure_owner_write_access(path):
  if path is owned by invoking user and writable:
    do nothing
  else:
    adjust ownership or permissions when allowed

restore_repo_path_access_for_invoking_user(path):
  for each repo access path:
    ensure invoking user can access/write it as needed
```

## Review Needed

Ownership and chmod behavior are platform-sensitive; verify implementation before changes.
