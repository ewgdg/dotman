# `dotman --yes` / `--run-noop` mode plan

Date: 2026-04-15

Updated: 2026-04-17

## Goal

Add two explicit controls:

- `--yes`: accept default yes/no confirmations without prompting.
- `--run-noop`: keep push/pull execution alive even when the final selected plan has no non-noop target steps, so hooks rerun.

Do not add a third semantic bucket.

## Locked behavior

### `--yes`

`--yes` should short-circuit confirmation prompts that already have a safe default:

- diff review continue
- write manifest confirmation
- symlink replacement confirmation
- reconcile write confirmation
- tracked binding replacement / implicit override confirmations

`--yes` does **not** auto-resolve ambiguous selector/profile menus. Those still need explicit selection or must fail in non-interactive mode.

### `--run-noop`

`--run-noop` applies after planning and interactive exclusion.

If the final selected plan still has a package, but all of its target steps are noop, keep that package alive for execution so its hooks run.

Rules:

- run `guard_*`, `pre_*`, and `post_*` in order
- allow hook-only package execution
- do not synthesize file writes
- do not force snapshot creation for hook-only runs
- target steps stay noop

`--run-noop` is execution-only for `push` / `pull`. It does not change drift detection or target selection.

## Implementation order

### 1) Tests first

Add tests for:

- parser/help output for the new flags
- `--yes` bypassing confirmation prompts
- `push` / `pull` with all-noop target plans still rerunning hooks under `--run-noop`
- `--run-noop` not fabricating target writes or snapshots
- current hard failures still staying hard failures
  - symlink hazards
  - missing paths
  - invalid config

### 2) Parser + command plumbing

Update the CLI parser and command dispatch to carry two explicit booleans:

- `assume_yes`
- `run_noop`

Prefer a small options object or tightly-scoped kwargs over threading ad hoc flags through every callsite.

### 3) Prompt helper refactor

Centralize yes/no confirmation handling so `--yes` can short-circuit:

- `review_plans_for_interactive_diffs`
- `filter_plans_for_interactive_selection`
- tracked binding replacement prompts
- add / reconcile confirmation prompts
- symlink replacement prompts

Leave ambiguity menus alone. `--yes` is not a resolver.

### 4) Execution refactor

Teach the execution path to retain hook-bearing packages when `run_noop` is set.

Likely touch points:

- hook filtering after selection
- execution-session assembly
- post-hook eligibility for hook-only packages
- human output and JSON output for hook-only runs

Important: `run_noop` should only preserve existing work. It should not create phantom target actions.

### 5) Docs

Update the behavior docs once the code shape is locked:

- `docs/cli.md`
- `docs/repository.md`

The docs need to reflect the new hook eligibility rule and the meaning of `--yes` / `--run-noop`.

## Test matrix

### Confirmation path

- `push --yes` continues past diff review and yes/no confirmations
- `pull --yes` continues past diff review and reconcile-write confirmation
- add/track reconcile confirmations do not block under `--yes`

### No-op execution path

- `push --run-noop` reruns hooks when all target steps collapse to noop
- `pull --run-noop` does the same
- hook-only execution still runs in package order
- no target writes happen when there are no non-noop target steps

### Safety path

- symlink hazards still fail
- invalid inputs still fail
- ambiguous selectors still require explicit resolution

## Non-goals

- no change to symlink safety policy
- no auto-resolution of ambiguous selector/profile menus
- no noop target writes
- no forced snapshot for hook-only execution
- no silent behavior change outside push/pull and existing confirmation prompts
