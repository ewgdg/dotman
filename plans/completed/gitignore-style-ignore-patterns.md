# Gitignore-style ignore matcher plan

Date: 2026-04-12

Updated: 2026-04-17

## Goal

Make `push_ignore` and `pull_ignore` behave like real gitignore-style path patterns instead of the current ad hoc `fnmatch` behavior.

This is the right fix because:

- docs already promise gitignore-style patterns
- users already expect `__pycache__/` to work
- current behavior is surprising and forces ugly workarounds like `*/__pycache__/*`

## Current problem

Current implementation in `src/dotman/engine.py`:

- matches only discovered file paths
- uses `fnmatch.fnmatchcase()` on the relative file path
- has a special basename fallback for patterns without `/`

This means:

- `__pycache__/` does **not** ignore files under that directory
- `*/__pycache__/` does **not** ignore files under that directory
- `**` has no meaningful special semantics
- docs and implementation disagree

## Decision

Implement gitignore-style matching in code.

Do **not** downgrade docs to say `fnmatch` unless we intentionally want a permanently nonstandard matcher. That would lock in a worse UX and preserve the current docs/code contract break.

## Proposed implementation

Use `pathspec` with `gitignore` semantics.

Why:

- standard Python library for gitignore-like matching
- supports directory patterns such as `__pycache__/`
- supports recursive patterns such as `**/foo`
- avoids growing a custom matcher with edge-case bugs

Planned dependency change:

- add `pathspec` to `[project].dependencies` in `pyproject.toml`

## Scope

### In scope

- `push_ignore`
- `pull_ignore`
- repo-level ignore defaults merged into target ignore lists
- tests for the expected pattern semantics
- docs update to describe exact supported semantics

### Not in scope for this change

- redesigning the entire push/pull planner
- changing target config schema
- broad `.gitignore` integration work unless it naturally shares the same matcher utility

Note: docs already mention respecting `.gitignore` during push. That appears adjacent but separate from this matcher fix and should not be silently mixed into this change unless implemented and tested deliberately.

## Test-first plan

Write tests before code changes.

### New matcher-level tests

Add focused tests for ignore semantics, ideally around a small helper instead of only high-level planning tests.

Required cases:

1. directory ignore pattern ignores descendants
   - pattern: `__pycache__/`
   - matches: `__pycache__/x.pyc`
   - matches: `foo/__pycache__/x.pyc`

2. nested directory ignore works
   - pattern: `**/__pycache__/`
   - matches nested descendants

3. existing simple file globs still work
   - `*.archived`
   - `assets/*`
   - `settings.ini`

4. basename-style patterns still work as users expect
   - `bookmarks` should match a file named `bookmarks` in nested locations if that is the intended old behavior we want to preserve
   - if `gitignore` differs here, decide and document it explicitly

5. root-anchored cases if we choose to support/document them
   - `/foo`
   - `foo/bar`

6. negation behavior
   - if we allow `!keep.pyc`, test it
   - if we decide not to allow negation for now, reject or document it explicitly

### High-level regression tests

Add or update planner tests that prove the user-facing fix:

- a directory target with `push_ignore = ["__pycache__/"]` excludes cached files
- a directory target with `pull_ignore = ["__pycache__/"]` preserves those live files during push cleanup / pull planning as intended
- keep one test that proves old workaround patterns are no longer required

## Code changes

### 1. Introduce a small ignore utility

Create a focused helper instead of scattering pattern logic through `engine.py`.

Suggested responsibilities:

- compile merged ignore patterns into a reusable matcher/spec
- answer whether a relative path is ignored
- keep path normalization in one place

Possible location:

- `src/dotman/ignore.py`

This keeps `engine.py` smaller and makes matcher tests cheap.

### 2. Replace `matches_ignore_pattern()`

Current function is too weak and encodes the wrong semantics.

Replace it with either:

- a `PathSpec`-backed helper, or
- a small wrapper object like `IgnoreMatcher`

Goal:

- call site says "is this relative path ignored?"
- implementation owns gitignore semantics

### 3. Update directory listing logic

Current `list_directory_files()` uses `root.rglob("*")` and filters only files.

Adjust it so the new matcher is used consistently.

Two acceptable options:

1. correctness-first
   - keep scanning files
   - use the new matcher on each relative file path
   - rely on gitignore semantics to make `__pycache__/` match descendant files

2. correctness + efficiency
   - switch to `os.walk()` or similar
   - prune ignored directories during traversal when safe

Recommended order:

- do correctness first
- add directory pruning only if simple and well-tested

### 4. Keep merge behavior unchanged

`merge_ignore_patterns()` is fine as a dedupe/ordering helper.

Do not change:

- repo defaults before target-level patterns
- target model fields
- serialization shape

Unless tests show a gitignore-specific ordering issue, preserve current order semantics.

## Compatibility questions to settle during implementation

### 1. Basename behavior

Current matcher has a convenience behavior for patterns without `/`:

- it matches either the whole relative path or any path segment

Need to verify whether `pathspec` `gitignore` gives the same result for patterns like `bookmarks` and `settings.ini`.

If yes:

- great, keep it

If no:

- decide whether to preserve old behavior in a compatibility wrapper
- or accept the gitignore behavior and update docs/tests

Bias: prefer real gitignore semantics unless this breaks important existing fixtures.

### 2. Negation support

Using `pathspec` may make `!pattern` available effectively for free.

Need explicit product decision:

- allow and document it
- or reject it for now to keep config surface smaller

Bias: if implementation cost is low and behavior is clear, support it. It is a normal part of gitignore semantics.

### 3. `.gitignore` interaction

Current docs claim `.gitignore` inside package source trees should be respected during push, but current code search does not show implementation.

Do not blur this with the matcher change.

Handle it one of two ways:

- implement it in a separate follow-up plan
- or explicitly include it here only if tests and code land together

## Docs updates

Update `docs/repository.md` after code/tests are done.

Need to document:

- `push_ignore` / `pull_ignore` use gitignore-style patterns relative to the directory target root
- `__pycache__/` is the right way to ignore that directory tree
- `*/__pycache__/*` is no longer needed as a workaround
- whether negation is supported
- whether root anchoring with `/` is supported

Also remove or avoid examples that imply `fnmatch` semantics.

## Suggested implementation sequence

1. add failing tests for `__pycache__/` behavior
2. add `pathspec` dependency
3. introduce ignore helper module
4. wire helper into directory file listing and planner call sites
5. run tests and fix fixture expectations
6. update docs
7. optionally add traversal pruning if still worthwhile

## Done criteria

This change is done when all are true:

- `push_ignore = ["__pycache__/"]` works
- `pull_ignore = ["__pycache__/"]` works
- common existing patterns still pass tests
- docs match implementation
- workaround patterns like `*/__pycache__/*` are no longer necessary

## Risk

Main risk is subtle behavior drift for existing patterns without `/`.

Mitigation:

- write explicit compatibility tests before swapping matcher
- compare current fixture behavior against desired gitignore semantics
- keep the matcher isolated so compatibility shims are easy if needed

## Implementation notes

Implemented 2026-04-12.

- matcher lives in `src/dotman/ignore.py`
- `pathspec` is used with `gitignore` semantics
- tests cover recursive pycache matching, root anchoring, basename compatibility, and negation
- package-local `.gitignore` files are not read here
