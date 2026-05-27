# Target Path Guards

## Intent

Reject declared live paths that point at symlinks when symlink targets are not allowed as normal live paths.

## Behavior

```pseudo
ensure_declared_live_path_is_not_symlink(path):
  if declared live path exists and is a symlink:
    reject target declaration
  return success
```
