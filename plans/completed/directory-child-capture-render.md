# Directory child capture/render

## Goal
Add directory-target child transform policy:

- target-level `capture` / `render` act as defaults for every child file in the directory target.
- `[[targets.<name>.path_rules]]` can override `capture` / `render` by relative child path `pattern`.

## Decisions
- Keep table name `path_rules`; matcher field is `pattern`.
- Later matching path rule wins.
- Rule fields override independently: if rule sets only `capture`, inherited `render` remains.
- Apply only to regular child files. Directory root `chmod` behavior unchanged.
- `capture = "patch"` is valid for any file-like sync unit, including directory child files, when effective render/pull-view requirements are met.

## Scope
- Manifest/model: add optional `render` and `capture` to `TargetPathRule`.
- Planning: resolve effective child `render`/`capture` for directory plan items.
- Execution: use child-level render during push, child-level capture during pull planning/reconcile where child bytes are projected, including per-child patch capture.
- Docs/tests.

## Progress
- [x] Wrote ExecPlan.
- [x] Audit current directory push/pull projection/execution flow.
- [x] Add model/schema support.
- [x] Thread effective child transforms into directory plan items.
- [x] Apply transforms in execution.
- [x] Add tests.
- [x] Update docs.
- [x] Run test suite (`uv run pytest -q` → 549 passed).

## Blockers
- None.
