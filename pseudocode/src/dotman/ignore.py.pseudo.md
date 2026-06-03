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

IgnoreMatcher.matches_directory(relative_path):
  normalize relative path as a directory path with trailing slash
  return whether gitignore-style rules exclude that directory

list_directory_files(root, ignore_patterns, follow_dir_symlinks = false):
  if root can be listed without sudo:
    walk directory directly
  else:
    list files through privileged helper, passing ignore rules and follow_dir_symlinks

  while walking directory tree:
    if a regular directory is encountered:
      descend into it

    if a symlink to a directory is encountered:
      if directory path matches ignore rules:
        skip it without error
      else if follow_dir_symlinks is false:
        reject scan with symlink-dir error
      else:
        descend through symlink path

    if descending would revisit a directory already in current ancestry:
      reject scan with symlink-loop error

    if a non-directory entry does not match ignore patterns:
      include relative path mapped to its path under root

  return sorted included relative file paths
```
