# Atomic File Writes

## Intent

Replace files and symlinks without exposing partially-written destinations.

## Behavior

```pseudo
write_bytes_atomic(path, content):
  create parent directory if missing
  create same-directory dotman temp file
  write content to temp file
  set temp mode to target_replacement_mode(path)

  if atomic replace succeeds:
    destination now contains content
  else:
    remove temp file if possible
    re-raise failure

write_symlink_atomic(path, target):
  create parent directory if missing
  create same-directory dotman temp symlink pointing to target

  if atomic replace succeeds:
    destination now points to target
  else:
    remove temp symlink if possible
    re-raise failure

target_replacement_mode(path):
  if destination exists and is a regular file:
    return destination permission bits
  return default_created_file_mode()

cleanup_stale_atomic_temp_files(directory):
  for each dotman atomic temp path in directory:
    if temp path is not live:
      remove it best-effort
```
