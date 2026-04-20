# `dotman edit target` plan

Date: 2026-04-17

Updated: 2026-04-17

## Goal

Extend `dotman edit` beyond package directories while keeping command semantics predictable.

Phase 1 should add a strict target-oriented subcommand:

- `dotman edit package <package>`
- `dotman edit target <target>`

Phase 2 may add interactive sugar:

- `dotman edit <query>`

But that sugar must remain an alias over the strict model, not the primary contract.

## Why this file exists

This is a CLI-surface change with resolver impact, help-text impact, and likely follow-up sugar work.
It should be pinned in `./plans/` before implementation so the strict-first decision does not drift.

## Locked decision

### Canonical interface

The canonical `edit` interface should stay noun-first and explicit:

- `edit package` for package directory editing
- `edit target` for target-level editing

Do not make cross-kind bare `dotman edit <query>` the only or primary interface.

### Sugar policy

If bare `dotman edit <query>` is added later, it is interactive convenience only.

- interactive mode: may resolve across packages and targets, then prompt when ambiguous
- non-interactive mode: must not guess across kinds
- JSON mode: must not guess across kinds

### Resolver policy

Strict subcommands resolve only within their own object kind.

- `edit package ...` uses tracked-package resolution only
- `edit target ...` uses tracked-target resolution only

This keeps help, completion, scripting, and future expansion sane.

## Locked v1 behavior

### `edit package <package>`

Keep existing behavior unchanged:

- resolve through tracked-package selector flow
- open the tracked package directory in `$VISUAL` or `$EDITOR`
- if no editor is configured, print the package directory path and exit `0`

### `edit target <target>`

Add target-level editing with repo-side semantics.

- resolve one tracked target
- open the repo-side source path for that target
- file target: open the source file path
- directory target: open the source directory path
- if no editor is configured, print the resolved repo-side path and exit `0`

This command is repo-editing convenience. It is not review-mode editing and not live-path editing.

### Target lookup scope

Target lookup should search currently tracked targets only.

Reason:

- matches existing `edit package` tracked-only behavior
- avoids opening untracked repo files through selector magic
- aligns with `push` / `pull` / `info tracked` mental model

### Ambiguity behavior

Target names are not globally unique. The resolver must treat target identity as package-scoped.

Interactive mode:

- if exact query is ambiguous, show shared selector menu
- if partial query is ambiguous, show shared selector menu

Non-interactive or JSON mode:

- ambiguous lookup fails with clear candidate list
- partial-only single match must not be auto-opened silently unless that matches existing shared resolver rules deliberately reused for target selectors

Implementation should prefer matching the existing shared selector contract already used elsewhere instead of inventing a special one-off rule.

## Target query shape

Need an explicit query syntax that can identify one tracked target without relying on menu labels.

Recommended canonical form:

```bash
dotman edit target [<repo>:]<package>.<target>
```

Examples:

```bash
dotman edit target git.gitconfig
dotman edit target main:git.gitconfig
dotman edit target nvim.init.lua
```

Why this shape:

- target names already live under packages
- package + target is stable and understandable
- avoids pretending target names are repo-global
- easy to render in help and error messages

If implementation finds `.` too ambiguous with real package ids or target ids in this codebase, switch to another explicit separator before merge. Do not ship with an underspecified free-form target query.

## Out of scope for this change

- no live-side edit command
- no reconcile-mode changes
- no diff-review edit reintroduction
- no automatic multi-file editor layouts beyond what the editor itself already does
- no bare mixed-kind `dotman edit <query>` as primary contract

Future review-mode edit work stays in `docs/edit-mode-v2.md`.

## Implementation phases

## Phase 1: tests first

Add CLI and behavior tests for:

### parser and help

- `dotman edit` help lists both `package` and `target`
- `dotman edit target --help` shows explicit target placeholder
- top-level help summary still reads cleanly

### package regression coverage

- existing `edit package` behavior still works
- existing no-editor fallback still prints package directory

### target resolution and opening

- opens tracked file target repo path with editor
- opens tracked directory target repo path with editor
- no-editor fallback prints resolved repo path
- repo-qualified target query works
- bare target query works when unique
- ambiguous target query prompts in interactive mode
- ambiguous target query fails in non-interactive mode with candidates

### failure cases

- target query not found fails clearly
- untracked package target cannot be opened
- malformed target query fails clearly

## Phase 2: resolver and command plumbing

Touch likely files:

- `src/dotman/cli_parser.py`
- `src/dotman/cli_commands.py`
- `src/dotman/cli.py`
- `src/dotman/installed.py`
- `src/dotman/engine.py` if a helper belongs there

Add:

- parser support for `edit target <target>`
- dispatch support for target edit handling
- a focused tracked-target resolver helper
- an opener helper that accepts a resolved repo path

Keep package and target edit opening helpers small and reusable.

## Phase 3: tracked-target resolver design

Build a small resolver over tracked target summaries rather than scraping ad hoc labels.

Suggested output identity per candidate:

- repo name
- package id
- bound profile if needed for disambiguation
- target name
- repo path

Preferred display label form should stay aligned with current UI style, for example:

- `main:git (gitconfig)`
- `main:nvim (init.lua)`

If bound profile matters, surface it the same way package-oriented selectors already do.

## Phase 4: docs

Update at least:

- `docs/cli.md`
- maybe `docs/code-structure.md` if helper placement changes materially

Document:

- strict `edit package` and `edit target`
- tracked-only lookup scope
- editor fallback behavior
- examples for file and directory targets

## Phase 5: optional sugar follow-up

Only after strict subcommands are stable.

Possible follow-up:

```bash
dotman edit <query>
```

Rules:

- interactive only for cross-kind resolution
- exact unique package or target may open directly
- exact multi-kind or multi-match ambiguity must prompt
- non-interactive / JSON must fail instead of guessing

This should likely be implemented as a thin resolver wrapper over the explicit `package` and `target` resolvers.

## Done criteria

- `edit target` exists and works for tracked file and directory targets
- help text is clear
- package edit behavior remains unchanged
- ambiguity handling matches existing selector UX quality
- docs updated for the strict v1 surface
- no mixed-kind magic required for scripts

## Risk

Main risk is target identity design.

If target query syntax is vague, the command will become hard to explain and harder to extend.
Better to force one explicit target identity shape now than to ship a clever-but-sloppy resolver and regret it later.
