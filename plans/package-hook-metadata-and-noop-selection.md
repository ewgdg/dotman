---
title: "Package hook metadata and synthetic noop selection plan"
status: completed
updated: 2026-04-18
---

# Package hook metadata and synthetic noop selection plan

Date: 2026-04-18

## Goal

Add per-package-hook metadata so package `guard_*`, `pre_*`, and `post_*` hooks can opt into standalone execution when their package has no executable target work.

Refactor `--run-noop` to use the same planning and selection path instead of reviving raw hooks late in execution.

This plan is **package hooks only**. Do not implement target-level hooks here.

## Scope

In scope:

- package hook metadata parsing and normalization
- synthetic hook-only selection items for package hooks
- refactoring `--run-noop` to use synthetic selection items
- final hook filtering based on retained synthetic items instead of executor fallback
- human and JSON output updates for hook-only package execution
- docs and tests for the new package-hook behavior

Out of scope:

- target-level hooks
- target-level hook metadata
- per-hook command selection menu items
- new hook names or hook phases
- global repo hooks

## Locked behavior

### Hook metadata

Package hooks gain one optional metadata flag:

```toml
[hooks.pre_push]
commands = ["echo hi"]
run_noop = true
```

Supported package-hook forms after this change:

```toml
[hooks]
pre_push = "echo hi"
post_push = ["echo one", "echo two"]

[hooks.pre_pull]
commands = ["echo pull"]
run_noop = true
```

Semantics:

- `run_noop = false` is the default.
- `run_noop = false`: hook runs only when its package has at least one executable non-noop target step after tracked-target winner resolution and interactive exclusion.
- `run_noop = true`: hook may run as standalone hook work when its package has no executable non-noop target steps for the operation.

Do **not** describe `run_noop = true` as literal "always runs" in code or docs. It still depends on final plan retention and normal failure rules.

### `--run-noop`

`--run-noop` stops being an execution-only fallback.

New meaning:

- after planning and tracked-target winner resolution
- after interactive exclusion
- if a package has no executable non-noop target steps
- dotman may still retain that package as a standalone hook-only selection item

For this package-hook phase, `--run-noop` should treat all package hooks for the active operation as noop-eligible for the current run, even if they do not declare `run_noop = true`.

This keeps the flag useful as a broad run-level override while the metadata provides per-hook default behavior.

### Synthetic selection items

When a package has no executable target work but still has noop-eligible hooks for the operation, dotman should surface one synthetic standalone selection item for that package.

Shape:

- one item per package owner
- not one item per hook command
- not one giant row merging package and future target hooks

Suggested display shape:

- `[hooks] repo:package`
- optionally include compact hook-name summary, for example `guard_push, pre_push, post_push`

Menu rules:

- if package already has executable target rows in the menu, do not add extra synthetic hook-only row for that package
- if package has no executable target rows but has noop-eligible hooks, add one synthetic row
- excluding the row drops standalone hook-only execution for that package
- keeping the row retains all noop-eligible hooks for that package in normal hook order

Non-interactive rules:

- there is no menu prompt, so synthetic hook-only items are implicitly retained
- preview and JSON output should still make hook-only package execution visible

### Execution rules

- `guard_*`, `pre_*`, and `post_*` keep existing ordering
- `post_*` remains success-only
- hook-only package execution must not fabricate target writes
- hook-only package execution must not fabricate snapshots
- symlink hazards and other hard failures still apply

### Package-only constraint

Do not add target-level hook parsing, planning, filtering, menu rows, or execution in this change.

Any data-model shape introduced now should avoid blocking target-level hooks later, but this plan should not implement them.

## Design notes

### Parser and manifest normalization

Current package hooks accept only string/list values under `[hooks]`.

Refactor package-hook loading so each hook entry normalizes to one `HookSpec` shape:

- `name`
- `commands`
- `declared_in`
- `run_noop`

Shorthand string/list syntax should normalize to `run_noop = false`.

### Keep final filtering out of executor fallback

Current behavior splits hooks into:

- filtered `plan.hooks`
- raw `plan.hook_plans`

and `build_execution_session()` revives raw hooks when `run_noop` is set.

That path should be removed for package hooks in favor of:

- planning synthetic hook-only items
- selection retaining or excluding them
- final filtered plans already containing the exact hooks that will execute

Executor should consume final retained plans, not reconstruct hidden eligibility.

### Menu granularity

Do not expose per-hook command items in the selection menu.

Reason:

- menu would get noisy fast
- users could create nonsensical partial states
- package hooks are meant to run as one ordered owner bucket

The unit of selection should be the package-level standalone hook bucket.

## Implementation order

### 1) Tests first

Add or update tests for:

- package-manifest parsing for hook table form with `commands` + `run_noop`
- shorthand package-hook syntax still normalizing to `run_noop = false`
- manifest merge behavior preserving `run_noop`
- planning/filtering retaining hook-only packages through synthetic selection items
- interactive selection showing synthetic package hook rows
- excluding a synthetic package hook row dropping hook-only execution
- keeping a synthetic package hook row retaining hook-only execution
- `--run-noop` broadening noop eligibility for package hooks without metadata
- hook-only package execution order and post-hook success rules
- no target writes or snapshots for hook-only package execution

### 2) Data model updates

Touch likely files:

- `src/dotman/models.py`
- `src/dotman/repository.py`
- `src/dotman/manifest.py`

Changes:

- extend `HookSpec` with `run_noop: bool = False`
- keep existing `HookPlan` package-oriented for this phase
- add enough selection/planning metadata to represent synthetic package hook rows cleanly

Prefer introducing an explicit selection item kind or equivalent marker instead of overloading target identity tuples to pretend hook-only rows are targets.

### 3) Repository loading + merge behavior

Update package manifest loading to accept either:

- string
- list of strings
- table with `commands` and optional `run_noop`

Validation rules:

- `commands` required for table form
- `commands` must normalize to string list
- empty command lists are allowed and mean the hook is effectively disabled for that package layer
- `run_noop` must be boolean when present
- unsupported keys should fail fast

Update package merge logic so hook overrides replace hook definitions cleanly, including metadata.

### 4) Planning refactor

Touch likely files:

- `src/dotman/planning.py`
- `src/dotman/models.py`
- `src/dotman/engine.py`

Required refactor:

- stop treating hook eligibility as only `package_id in executable_package_ids`
- derive package hook buckets for the active operation
- separate:
  - hooks anchored by executable target work
  - hooks eligible for standalone noop execution
- retain enough information for the selection phase to decide whether a package hook-only bucket stays alive

Do not let executor be the first place that knows about noop-only hook eligibility.

### 5) Interactive selection refactor

Touch likely files:

- `src/dotman/cli.py`
- `tests/cli/test_selection_ui.py`

Refactor selection flow so menu items can include:

- normal target items
- synthetic package hook-only items

Requirements:

- synthetic package hook-only items have stable identity
- excluding them removes standalone hook-only execution for that package
- if a package still has executable target work, normal target exclusions continue to drive hook eligibility for non-standalone hooks
- hook-only rows should only appear when there is no executable target anchor for that package

`--run-noop` should feed this stage by making package hooks noop-eligible for the run before the menu is built.

### 6) Final plan filtering

After interactive exclusion, finalize plans so they already contain the exact hook set that will execute.

Likely changes:

- replace current `filter_hook_plans_for_targets()`-only behavior with a package-hook-aware finalization step
- keep package hook-only retention explicit
- remove raw hook revival logic from execution session building

At end of selection/finalization, there should be no hidden execution-only hook eligibility left.

### 7) Execution refactor

Touch likely files:

- `src/dotman/execution.py`
- `tests/engine/test_execution.py`
- `tests/cli/test_execute.py`

Required behavior:

- execution session builder accepts final package hook-only plans directly
- package hook-only units run ordered hook steps with no fabricated target steps
- `post_*` runs only when earlier steps for that package succeed
- session output stays package-oriented
- snapshot creation logic remains off when final selected work is hook-only

Remove the executor path that falls back to `hook_plans` when `run_noop` is set.

### 8) Human and JSON output

Touch likely files:

- `src/dotman/cli_emit.py`
- JSON execution payload code paths

Make hook-only package execution visible in:

- preview output
- real execution output
- JSON payloads

Users should be able to tell when a package is running hooks without file work.

### 9) Docs

Update after behavior is locked:

- `docs/repository.md`
- `docs/cli.md`

Doc requirements:

- package hook metadata syntax
- exact `run_noop` semantics
- synthetic hook-only selection-item behavior
- clarified meaning of `--run-noop`
- package-only scope for this phase

## Test matrix

### Parsing and merge

- `[hooks] pre_push = "echo hi"` still works
- `[hooks.pre_push] commands = ["echo hi"]` works
- `[hooks.pre_push] ... run_noop = true` works
- invalid table payload fails fast
- override package replacing a base hook also replaces metadata

### Planning and selection

- package with changed targets and hooks: no synthetic hook-only row
- package with no executable targets and `run_noop = true`: synthetic package hook-only row appears
- package with no executable targets and no metadata: synthetic row absent by default
- same package under `--run-noop`: synthetic row appears
- excluding synthetic row removes hook-only execution
- keeping synthetic row retains hook-only execution

### Execution

- hook-only package runs `guard_*`, `pre_*`, `post_*` in order when retained
- failed `guard_*` skips later hooks
- failed `pre_*` skips `post_*`
- no file target step appears in hook-only execution
- no snapshot created for hook-only final selection

### Compatibility and safety

- packages with executable targets keep existing hook behavior by default
- symlink hazard checks still fail when applicable
- invalid manifests still fail fast
- non-interactive runs retain synthetic hook-only work automatically

## Decisions

- Use one per-hook boolean metadata flag: `run_noop`.
- For this phase, package hooks only.
- Synthetic selection items are grouped per package owner, not per hook command.
- `--run-noop` and hook metadata must use the same synthetic-selection mechanism.
- Executor should consume already-finalized hook plans and should not revive raw hooks.

## Progress

- 2026-04-18: Plan created.
- 2026-04-18: Implemented package hook metadata parsing for table form with `commands` and `run_noop`.
- 2026-04-18: Refactored hook finalization so standalone noop-eligible package hooks are retained before execution instead of revived inside executor fallback.
- 2026-04-18: Added synthetic package hook-only selection items and wired `--run-noop` through same planning and selection path.
- 2026-04-18: Updated execution, preview, JSON payloads, snapshot behavior, docs, and tests for hook-only package execution.
- 2026-04-18: Verified with `uv run pytest -q` (`377 passed`).

## Blockers

- None.

## Scope changes

- None.

## Non-goals

- no target-level hook execution model yet
- no per-hook menu item granularity
- no target-level synthetic hook-only rows in this phase
- no hidden executor-only noop hook revival after selection
