# Skip-marker ignore

Date: 2026-06-15

## Goal

Add an explicit ignore mode for directory targets: if a directory contains a configured marker file, dotman treats that directory as unmanaged and skips the whole subtree during `push` and `pull` planning.

Recommended marker name: `.dotman-skip`.

## Decision

Allow the marker name to be configured.

Do not hard-code an always-active marker. Default should be empty for compatibility. Repos opt in with a repo-level setting:

```toml
[ignore]
skip_markers = [".dotman-skip"]
```

Why configurable:

- avoids surprising existing repos if they already have a file with the chosen name
- lets a repo choose a clearer house style without changing dotman code
- keeps behavior explicit in `repo.toml`, next to existing repo-wide ignore defaults

Why still recommend one name:

- docs and examples need one clear convention
- `.dotman-skip` says marker-only behavior; `.dotmanignore` would imply gitignore-style pattern contents

## Current behavior

Ignore rules are gitignore-style path patterns loaded from:

- repo-level `[ignore]` in `repo.toml`
- target-level `ignore` tables / legacy `push_ignore` and `pull_ignore`

The scanner currently lists directory target files with `dotman.ignore.list_directory_files(root, ignore_patterns, ...)`.

Existing ignore patterns can ignore known paths, but cannot express:

> ignore the parent directory if it contains this marker file

A pattern like `**/.dotman-skip` only ignores the marker file itself. It does not skip siblings or protect the directory subtree from cleanup.

## Intended semantics

For each scanned directory target root:

- before descending into a child directory, check whether that directory contains any configured skip marker
- if yes, skip the directory entirely
- do not include marker files in the file map
- apply the same skipping to repo-side scans and live-side scans
- therefore skipped paths are absent from desired and actual maps, so dotman neither creates, updates, chmods, nor deletes them
- skipping applies equally to `push` and `pull` unless a later operation-scoped extension is added

Root behavior:

- if the target root itself contains a skip marker, skip its children and produce an empty file map for that target
- do not skip target hooks or target ownership metadata just because the root is skipped; this feature only affects child file synchronization

Symlink behavior:

- do not follow a directory symlink just to inspect markers when `follow_dir_symlinks = false`
- for followed directory symlinks, marker checks use the resolved directory contents during the normal scan path
- existing symlink loop detection remains authoritative

## Config shape

Start with shared repo-level config only:

```toml
[ignore]
skip_markers = [".dotman-skip"]
```

Normalize like existing string-list fields:

- must be a list of non-empty strings
- each entry must be a basename, not a path
- reject names containing `/` or `\\`
- reject `.` and `..`

Do not support marker file contents in this feature.

Do not call this `.dotmanignore` in docs because contents are ignored. If a future feature reads ignore pattern files, use a separate setting such as `ignore_files = [".dotmanignore"]`.

## Possible follow-up config

Target-level override can be added later if needed:

```toml
[targets.config.ignore]
skip_markers = [".dotman-skip"]
```

Operation-scoped markers can be added later if real use cases appear:

```toml
[ignore.skip_markers]
shared = [".dotman-skip"]
push = [".dotman-push-prune"]
pull = [".dotman-pull-prune"]
```

Do not add these now unless implementation becomes awkward without them. Keep first version small.

## Implementation plan

### 1. Model config

Extend `RepoIgnoreDefaults` or add a companion repo defaults dataclass field for skip markers.

Likely small change:

- `RepoIgnoreDefaults.push`
- `RepoIgnoreDefaults.pull`
- new `RepoIgnoreDefaults.skip_markers`

Load it in `Repository._load_repo_ignore_defaults()` from `repo.toml [ignore].skip_markers`.

Validation should happen during config load so errors point at `repo.toml`.

### 2. Carry markers into target metadata

Directory target planning currently passes ignore pattern tuples through rendered target metadata and `list_directory_files()` calls.

Add `skip_markers` beside `push_ignore` / `pull_ignore` where directory scans need it.

Keep it shared across operations in the first version.

### 3. Update scanner API

Change directory listing signature from:

```python
list_directory_files(root, ignore_patterns, follow_dir_symlinks=False)
```

to:

```python
list_directory_files(root, ignore_patterns, skip_markers=(), follow_dir_symlinks=False)
```

Apply same parameter to:

- `_list_directory_files_without_sudo`
- `_list_directory_files_via_sudo`
- `dotman.privileged_ops` payload handling

### 4. Implement skipping in `src/dotman/ignore.py`

In `scan_directory(directory, relative_directory)`:

- check marker presence before iterating child entries, except root handling can be explicit
- for child directories, check markers before recursion
- if a marker exists, return without adding files under that directory

Use direct basename checks for configured marker names. Avoid routing marker detection through `PathSpec`; this is not pattern matching.

Pseudo-flow:

```python
if relative_directory and directory_contains_skip_marker(directory, skip_markers):
    return

for child in sorted(directory.iterdir(), key=lambda path: path.name):
    relative = ...
    if child.is_dir() or followed_dir_symlink:
        if matcher.matches_directory(relative):
            continue
        if directory_contains_skip_marker(child, skip_markers):
            continue
        scan_directory(child, relative)
        continue
    if child.name in skip_markers:
        continue
    if matcher.matches(relative):
        continue
    files[relative] = child
```

Keep permission behavior simple:

- if checking marker presence raises `PermissionError`, existing sudo fallback should handle protected roots the same way directory iteration does
- do not swallow permission errors silently

### 5. Tests first

Add tests before code changes.

Suggested tests in `tests/engine/test_ignore_patterns.py`:

1. marker skips nested repo directory
   - tree: `keep.txt`, `cache/.dotman-skip`, `cache/data.txt`
   - `list_directory_files(..., skip_markers=(".dotman-skip",))` returns only `keep.txt`

2. marker file itself is absent from results

3. root marker returns empty map

4. no marker configured means marker file is just a normal file unless ignored by patterns

5. followed directory symlink with marker is skipped only when `follow_dir_symlinks=True`

Planner/execution regression tests:

6. push cleanup preserves live skipped subtree
   - live has `cache/.dotman-skip` and `cache/local-state`
   - repo lacks `cache/`
   - push plan should not delete `cache/local-state`

7. pull preserves repo skipped subtree
   - repo has `cache/.dotman-skip` and `cache/local-state`
   - live differs
   - pull plan should not delete/update repo skipped files

8. repo-level config loads markers from `[ignore].skip_markers`

Validation tests:

9. reject marker names containing `/`
10. reject empty marker names

### 6. Docs

Update `docs/repository.md` near repo-level ignore defaults.

Document:

- `skip_markers` are marker filenames, not patterns
- markers skip whole directory subtrees
- recommended marker is `.dotman-skip`
- marker file contents are ignored
- `.dotmanignore` is not used for this feature
- skipping affects both push and pull scans

Add short example:

```toml
[ignore]
shared = ["*.bak"]
skip_markers = [".dotman-skip"]
```

Then:

```text
files/config/app/cache/.dotman-skip
files/config/app/cache/state.db
```

Dotman ignores all of `cache/`.

## Non-goals

- reading marker file contents
- implementing package-local `.gitignore` support
- replacing existing push/pull ignore pattern semantics
- operation-scoped skip markers in first version
- target-level skip markers in first version unless needed by tests or architecture

## Validation commands

Run focused tests first:

```sh
uv run pytest tests/engine/test_ignore_patterns.py
```

Then relevant broader tests:

```sh
uv run pytest tests/engine/test_plans.py tests/test_privileged_ops.py
```

Before completion:

```sh
uv run pytest
```

## Done criteria

- repo can opt into marker skipping with `[ignore].skip_markers`
- `.dotman-skip` skips the containing directory subtree during push and pull scans
- skipped live paths are protected from push cleanup
- skipped repo paths are protected from pull cleanup
- marker names are validated as basenames
- docs describe exact behavior and recommended marker name

## Risks

Main risk: skipping both repo and live side can hide real drift. That is intentional for this feature; marker means unmanaged subtree.

Secondary risk: adding more fields through rendered target metadata may touch many model serialization tests. Keep schema additive and default empty to reduce churn.
