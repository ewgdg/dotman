# `patch` capture helper and `jinja-patch` preset plan

Date: 2026-04-14

Updated: 2026-04-17

## Goal

Add a narrow non-interactive reverse-capture workflow for rendered Jinja targets whose live edits can be recovered by:

- diffing projected review content
- patching the raw repo source
- re-projecting to verify an exact match

This should support the intended simple variable-injection case and fail fast everywhere else.

## Locked naming

- builtin capture helper: `patch`
- CLI command: `dotman capture patch`
- builtin target value: `capture = "patch"`
- preset: `jinja-patch`

We are **not** adding a second alias such as `diff-patch`.

## Locked scope

### Supported shape

The primary intended workflow is:

- `render = "jinja"`
- `capture = "patch"`
- `pull_view_repo = "render"`
- `pull_view_live = "raw"`

The helper itself should stay projection-based rather than Jinja-specific in name or API, but v1 only needs to work for this Jinja workflow.

### Hard restrictions

`capture = "patch"` is valid only when all of these hold:

- target is a **file** target
- resolved `pull_view_repo` is configured
- resolved `pull_view_live` is configured
- resolved review pair is **not** `raw` + `raw`
- pull execution provides prepared review files through:
  - `DOTMAN_REVIEW_REPO_PATH`
  - `DOTMAN_REVIEW_LIVE_PATH`

If any requirement is missing, fail fast with a targeted error.

### Success contract

Patch capture succeeds only if:

1. read raw repo source
2. read prepared review repo projection
3. read prepared review live projection
4. compute a patch from review repo -> review live
5. apply that patch to the raw repo source
6. re-project the patched repo source through the repo review view
7. resulting projected bytes exactly equal the review live bytes

If step 7 fails, abort without writing repo content.

### Non-goals

Do not try to support these in v1:

- directory targets
- ambiguous or heuristic multi-strategy fallback
- silent fallback to direct copy
- broad claims that arbitrary Jinja templates are supported
- interactive conflict resolution inside `patch`

## Why this needs design work first

Current pull execution can write repo-side bytes from `_pull_desired_bytes()`, but that path only receives normal target env. Review scratch paths are currently materialized only for reconcile execution. `patch` needs those review paths during normal pull target execution, so the execution contract must change.

Also, current target spec resolution keeps only resolved `pull_view_*` values. That is fine for v1 if we validate the resolved values rather than trying to detect whether the user wrote the keys directly or inherited them from a preset.

## Proposed user-facing behavior

### Manifest

Low-level explicit form:

```toml
render = "jinja"
capture = "patch"
pull_view_repo = "render"
pull_view_live = "raw"
```

Preferred bundle:

```toml
preset = "jinja-patch"
```

### CLI

Add a new top-level helper namespace:

```bash
dotman capture patch --repo-path <repo-path> --review-repo-path <review-repo-path> --review-live-path <review-live-path> [projection args...]
```

The built-in target helper should reuse the same implementation as the CLI subcommand.

The CLI exists for:

- debugging the algorithm directly
- keeping built-ins symmetric with `render ...` and `reconcile ...`
- avoiding hidden one-off execution logic in pull

## Proposed preset contents

Add a builtin preset:

```toml
preset = "jinja-patch"
```

which expands to:

- `render = "jinja"`
- `capture = "patch"`
- `pull_view_repo = "render"`
- `pull_view_live = "raw"`

This should be parallel to the existing `jinja-editor` preset.

## Implementation plan

## Phase 1: tests first

Add tests before source changes.

### Preset and manifest validation

Add tests for:

- `jinja-patch` expands to the expected values
- `capture = "patch"` rejects directory targets
- `capture = "patch"` rejects resolved `raw/raw` review views
- `capture = "patch"` rejects missing review-path env during execution
- preset-provided `pull_view_repo` / `pull_view_live` counts as valid configuration

### CLI parsing and help

Add tests for:

- `dotman capture patch --help`
- top-level help lists `capture`
- `capture patch` required args and descriptions

### Pull execution behavior

Add tests for:

- pull update with `capture = "patch"` uses review scratch paths rather than direct live copy
- successful patch capture writes patched repo source bytes
- verification mismatch aborts and does not write repo content
- patch helper errors surface cleanly

### Algorithm tests

Add focused tests for the helper implementation:

- no-op review diff keeps raw repo unchanged
- simple variable-value change patches repo source and verifies
- impossible patch application fails
- patch applies but rerendered/projection output mismatch fails

Keep these tests narrow and deterministic. Do not overfit to incidental patch formatting.

## Phase 2: add the `capture` CLI namespace

Touch:

- `src/dotman/cli_parser.py`
- `src/dotman/cli_commands.py`

Add:

- top-level `capture` command
- `capture patch` subcommand

Keep the command surface minimal. No extra aliases.

## Phase 3: implement the patch helper

Create a focused module, for example:

- `src/dotman/capture.py`

Responsibilities:

- validate review-path preconditions
- load repo and review files
- compute/apply patch to raw repo source
- run verification projection against the patched repo candidate
- return final repo bytes or raise a clear error

Prefer a small, well-named pure function for the core transform so tests do not need full engine setup.

## Phase 4: wire builtin capture execution

Touch pull execution flow so builtin `capture = "patch"` can access review scratch files during normal target execution.

Likely touch:

- `src/dotman/execution.py`

Required change:

- when pull writes repo bytes for a target with builtin patch capture, materialize the same review scratch env contract used by reconcile helpers
- then invoke the shared patch helper instead of `_pull_desired_bytes()` direct copy/capture behavior

This is the critical seam. Do not hide it behind fragile env magic scattered across modules.

## Phase 5: register builtin capture semantics

Touch planning / projection path handling as needed so builtin `capture = "patch"` remains declarative in plans/info but executes through the helper.

Likely touch:

- `src/dotman/projection.py`
- installed/summary surfaces if they special-case builtin names elsewhere

Need to ensure:

- pull planning still compares configured review views
- capture execution uses patch helper only for actual pull write path
- info output shows `capture = "patch"` without expanding it into shell text

## Phase 6: docs

Update:

- `docs/templates.md`
- `docs/repository.md`
- `docs/cli.md`

Document:

- `patch` capture contract
- required review-view configuration
- verification behavior
- `jinja-patch` preset
- when to use `jinja-patch` vs `jinja-editor`

Be explicit that this is a narrow helper for simple reversible template workflows, not a general Jinja reverse compiler.

## Design notes to keep implementation honest

- Fail fast. No fallback to copying live bytes into repo bytes when patch preconditions are not met.
- Keep naming honest. `patch` is mechanism-level; `jinja-patch` is workflow-level.
- Validate the **resolved** `pull_view_repo` / `pull_view_live` pair, so preset-backed configuration works without extra schema plumbing.
- Treat review scratch files as the source of truth for the patch diff. Do not recompute them independently inside the helper unless needed for verification.
- Verification must be exact byte equality on the projected review output.
- If later we discover this only works with Jinja-specific source restrictions, we should tighten docs and validation rather than pretending it is generic.

## Expected touched files

Likely minimum set:

- `src/dotman/cli_parser.py`
- `src/dotman/cli_commands.py`
- `src/dotman/capture.py` (new)
- `src/dotman/execution.py`
- `src/dotman/presets.py`
- tests for CLI, plans, and execution
- docs: `templates.md`, `repository.md`, `cli.md`

## Exit criteria

This work is done when:

- `preset = "jinja-patch"` works for the intended simple case
- `capture = "patch"` fails loudly on invalid configuration
- pull execution uses review projections and verification instead of blind copy
- a direct CLI helper exists as `dotman capture patch`
- docs clearly explain limits and intended use
