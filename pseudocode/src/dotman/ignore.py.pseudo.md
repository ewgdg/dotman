# Ignore Matching and Directory Listing

## Intent

Apply gitignore-style ignore rules while collecting directory target files.

## Behavior

```pseudo
matches_ignore_pattern(relative_path, pattern):
  normalize relative path separators
  apply gitignore-style pattern semantics
  return whether pattern excludes path

IgnoreMatcher.matches(relative_path):
  normalize relative path
  return pathspec match result

list_directory_files(root, ignore_patterns):
  if root can be listed without sudo:
    walk directory directly
  else:
    list files through privileged helper

  for each discovered file:
    if file does not match ignore patterns:
      include relative path

  return sorted included relative file paths
```
