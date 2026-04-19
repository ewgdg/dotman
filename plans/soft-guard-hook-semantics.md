---
title: "Soft guard hook semantics plan"
status: completed
updated: 2026-04-18
---

# Soft guard hook semantics plan

Date: 2026-04-18

## Goal

Make `guard_*` hooks meaningfully different from `pre_*` hooks by giving guards a soft-skip path.

This plan applies the new contract to the current package-hook executor and establishes the scope-skipping rules that repo-level and target-level hooks will reuse later.

## Scope

In scope:

- guard exit-code contract
- package-level soft guard execution in `push` and `pull`
- executor/result-model changes needed so future repo/package/target scopes can all soft-skip cleanly
- human and JSON output for guard-skipped scopes
- push snapshot timing changes so skipped-before-mutation runs do not fabricate snapshots
- docs and tests for the new semantics

Out of scope:

- repo-hook parsing or execution
- target-hook parsing or execution
- configurable skip codes
- stdout/stderr text parsing as a skip signal
- continue-on-error semantics for non-guard hooks
- new hook phases

## Locked behavior

### Guard exit codes

- exit code `0`: guard passed; continue normally
- exit code `100`: soft-skip the entered scope; do not treat as failure
- any other non-zero exit code: hard failure
- shell `false` still exits `1`, so it remains a hard failure

Keep the contract explicit and narrow. Do not add ranges, aliases, or config for alternate skip codes in this phase.

### Scope mapping

- package guard skip: skip that package's remaining `guard_*` list, `pre_*`, targets, and `post_*`; continue with next package
- future target guard skip: skip that target's subtree; continue with next target in the package
- future repo guard skip: skip that repo's subtree; continue with next repo

### Guard list behavior

- Guard commands still run in declaration order.
- First non-zero exit code ends the guard list.
- Exit `100` means the scope is skipped immediately.
- Hard-fail non-zero means the run fails immediately.
- Do not keep evaluating later guard commands after a soft-skip or hard-fail result.

### `pre_*` and `post_*`

- `pre_*` keeps its current meaning: setup/preparation step, side effects allowed, any non-zero exit is a hard failure.
- `post_*` remains success-only.
- A scope skipped by guard must not run `post_*`.
- A scope skipped by guard must not fabricate target steps, reconcile steps, chmod steps, or snapshots.

### Result semantics

- Soft-skipped scopes are successful overall unless some other scope hard-fails.
- Command exit code for the whole operation remains `0` when the run only contains soft skips and successful work.
- Execution results should carry explicit skip reason metadata instead of collapsing everything into generic `skipped`.

Recommended shape:

- `status = "skipped"`
- `skip_reason = "guard"`

Human output should render this as `skipped (guard)`.

### Snapshot timing

Current `push` creates snapshots before execution starts. That is too early once guards can soft-skip.

New rule:

- snapshot creation must happen immediately before first live mutation, not before guard-only prefixes
- if all selected work soft-skips before any live mutation, do not create a snapshot
- if execution hard-fails before first live mutation, do not create a snapshot
- hook-only and guard-skipped-only runs still must not create snapshots

This keeps snapshot history aligned with real live mutations.

### `--run-noop`

- `--run-noop` still controls hook eligibility only.
- If a hook-bearing package is retained for hook-only execution and its `guard_*` returns `100`, that package is skipped cleanly and later packages continue.
- `--run-noop` must not upgrade a hard-failing guard into a skip.

## Design notes

### Prefer explicit skip metadata over new broad status enums

Avoid spreading `guard_skipped` as a separate primary status everywhere unless existing code makes that clearly simpler.

Prefer:

- stable primary statuses: `ok`, `failed`, `skipped`
- plus explicit skip reason metadata where needed

This keeps rendering and JSON stable while still preserving why a scope skipped.

### Prepare executor for nested scopes

The current executor is package-flat. That is enough to implement package guard skips now, but the result and control-flow shape should not block future repo/target nested hooks.

The implementation should therefore introduce a small reusable notion of:

- entered scope
- guard outcome
- subtree skipped by guard

even if package scope is the only active caller in this phase.

### Snapshot creation should move closer to execution

Because snapshot creation now depends on whether any guarded work actually reaches a mutating step, snapshot creation logic likely needs to move out of the pre-execution CLI wrapper and closer to the execution loop or execution-session materialization.

Do not solve this with ad hoc post-hoc snapshot deletion.

## Implementation order

### 1) Tests first

Add or update tests for:

- package `guard_push` exit `100` skips package target/post steps and continues next package
- package `guard_pull` exit `100` skips package reverse-sync/post steps and continues next package
- package `guard_*` exit `1` still hard-fails and stops later packages
- multiple guard commands stop on first non-zero result
- hook-only package retained by `--run-noop` can still be skipped by guard
- `post_*` does not run after guard skip
- human output shows `skipped (guard)`
- JSON output preserves skip reason metadata
- push creates no snapshot when all work guard-skips before mutation
- push still creates a snapshot once real live mutation is about to happen

### 2) Result-model updates

Likely touch files:

- `src/dotman/execution.py`
- `src/dotman/cli_emit.py`
- `src/dotman/models.py` if shared result dataclasses live there later

Changes:

- extend execution result objects with skip-reason metadata where needed
- keep package/session summaries able to distinguish hard failure from guard skip
- keep overall operation exit semantics unchanged for soft-skip-only runs

### 3) Executor control-flow refactor

Teach package execution to:

- detect guard-step exit `100`
- mark remaining steps in that package as skipped with guard reason
- continue later packages
- keep hard-fail behavior unchanged for all other non-zero results

Keep the change generic enough that repo/target scope skipping can reuse the same mechanism later.

### 4) Snapshot gating refactor

Likely touch files:

- `src/dotman/cli_commands.py`
- `src/dotman/execution.py`
- `src/dotman/snapshot.py`

Refactor push snapshot creation so it happens only when execution is about to perform the first real live mutation.

The executor should not need to replay work to answer this. Use execution-step metadata to know whether a mutating step is imminent.

### 5) Output and docs

Update user-facing behavior docs once the code is stable:

- `docs/repository.md`
- `docs/cli.md`
- any execution-output docs or examples affected by `skipped (guard)`

Document the exit-code contract explicitly. Do not describe soft skip vaguely as "return false".

## Test matrix

### Soft-skip path

- `guard_push = "exit 100"` skips package work and continues
- `guard_pull = "exit 100"` skips package work and continues
- guard skip plus later successful package returns overall success

### Hard-fail path

- `guard_* = "false"` fails run
- `pre_* = "exit 100"` is still a hard failure, not a skip
- `post_* = "exit 100"` is still a hard failure if it runs

### Snapshot path

- all skipped before mutation => no snapshot
- first package skipped, second package mutates => snapshot created once before second package's first mutation
- hook-only / noop-only / guard-skipped-only push => no snapshot

## Non-goals

- no configurable skip-code list
- no per-hook "continue on failure"
- no new success state that changes command exit code
- no repo or target hooks yet

## Decisions

- `guard_*` gains soft-skip semantics via exit code `100`
- hard-fail semantics for other non-zero exits stay unchanged
- snapshot creation must become mutation-aware

## Progress

- 2026-04-18: initial plan written
- 2026-04-18: implemented guard exit-code `100` soft-skip semantics, mutation-aware snapshot gating, executor/status updates, docs, and tests.

## Blockers

- none

## Scope changes

- none yet
