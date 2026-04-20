# Formal pull/push execution plan

Date: 2026-04-09

Updated: 2026-04-17

## Goal

Implement real `push` and `pull` execution on top of the existing planning UX.

Keep the user flow aligned with the current repo style:

1. resolve tracked binding(s)
2. build plan
3. interactive exclusion menu
4. diff review
5. execute

The execution stage should feel like the dry-run payload continuing into real work, not like a separate product.

## Design constraints

- Reuse the current planning model (`BindingPlan`, `TargetPlan`, `HookPlan`) instead of building a second executor-specific graph.
- Preserve current selector, exclusion, and diff-review behavior.
- Fail fast.
- Keep output package-centric and timeline-like.
- Avoid reviving archived dotdrop-style global phase UX as the primary user model.
- Still support privilege boundaries where required.

## Archived implementation to reuse selectively

Reference: `~/projects/dotman.archived`

Useful ideas to port:

- privilege detection and privilege-aware execution splitting
- one elevation handshake before execution when needed
- sudo keepalive during long-running executions
- streaming subprocess output
- summary accounting for changed / failed items

Useful ideas **not** to port directly:

- user-facing global phase banners as the main execution model
- dotdrop-key-centric execution model
- internal re-entry machinery tied to the archived install/update world

## Core decision: no top-level phase UX

After clarification, the better model is simpler:

- no user-facing global phases
- no mandatory internal phase buckets
- one ordered per-package execution timeline

What still exists is only **step ordering**, not a separate phase system.

### What this means in practice

For each package, dotman executes an ordered list of steps:

- hook steps when relevant
- target CRUD steps
- reconcile steps for pull when relevant
- chmod/finalization steps when relevant

Privilege is decided per step, but authorization can be acquired once up front if any selected step needs it.

### Why this is better here

The current repo UX is package-oriented.
Dry-run output already groups by package and shows targets/hooks together.
Execution should just continue that structure.

## Execution model

## 1. Introduce an execution plan layer derived from existing plans

Add a small execution-specific layer instead of executing `BindingPlan` ad hoc.

Suggested model:

- `ExecutionSession`
  - operation: `push` | `pull`
  - bindings: list of selected `BindingPlan`
  - packages: ordered package execution units
  - requires_privilege: bool
  - stats
- `PackageExecutionUnit`
  - binding label
  - repo name
  - package id
  - profile
  - ordered steps
- `ExecutionStep`
  - step kind: `hook` | `target` | `chmod` | `reconcile`
  - operation kind: `guard_push` / `pre_push` / `write` / `delete` / `mkdir` / `post_push` / `guard_pull` / `pre_pull` / `post_pull` / etc.
  - display label
  - cwd/env if command-backed
  - source/live/repo paths if file-backed
  - privilege requirement

This keeps execution explicit, testable, and independent from the dry-run printer.

## 2. Package execution ordering

Build execution units in the same stable order already implied by current plans:

- binding order from tracked state / requested selection
- package order from `plan.package_ids`
- target order from target declaration order
- hook command order from declaration order

That keeps dry-run and execution mentally aligned.

## 3. Per-package timeline

For each package, emit a compact execution block.

Example shape:

```text
:: executing push
  packages: 2 · steps: 7

  :: example:git@basic
    [1/4] guard_push  command -v git >/dev/null 2>&1
    [2/4] pre_push    brew install git
    [3/4] update      ~/.gitconfig
    [4/4] post_push   sh hooks/post-push.sh
    done

  :: example:nvim@basic
    [1/2] guard_push  command -v nvim >/dev/null 2>&1
    [2/2] update      ~/.config/nvim/init.lua
    done
```

For pull, the step labels should reflect actual reverse-sync behavior:

- `capture` only if it is part of the actual execution path
- `reconcile` when a reconcile command runs
- otherwise CRUD-style labels like `update repo`, `create repo`, `delete repo`

## 4. Hook ordering rules

Define explicit execution ordering.

### Recommended hook names

Replace ambiguous `check` with explicit operation-scoped guard hooks:

- `guard_push`
- `pre_push`
- `post_push`
- `guard_pull`
- `pre_pull`
- `post_pull`

### Semantics

- `guard_*`
  - preflight hooks
  - should be side-effect free or as close to that as possible
  - fail-fast if requirements are not met
- `pre_*`
  - run immediately before the package's first target step for that operation
- `post_*`
  - run only after all package target steps succeeded
  - should not run after a failed guard/target/pre step
  - eligibility should be based on execution scope, not on trying to prove a material filesystem delta after the fact
  - if a package has at least one selected target execution step and those steps complete successfully, `post_*` may run even when a reconcile flow ultimately leaves content unchanged

### Push

Per package:

1. `guard_push`
2. `pre_push`
3. target CRUD/chmod steps
4. `post_push`

### Pull

Per package:

1. `guard_pull`
2. `pre_pull`
3. target reverse-sync steps (`reconcile` or direct repo writes/deletes)
4. `post_pull`

### Why support the pull hooks now

Even if initial examples do not need them, this is the right time to make hook names explicit.
Adding only `guard_push`/`guard_pull` now and punting `pre_pull`/`post_pull` would likely force another schema change soon after real pull lands.

### Compatibility direction

Remove `check` entirely.
No backward-compatibility alias.
This schema change should be explicit and clean rather than carrying ambiguous legacy names into the new execution model.

## 5. Target execution rules

### Push file targets

- `create` / `update`: write `desired_bytes` to live path atomically when possible
- `delete`: remove live path
- enforce `chmod` after successful write when specified

### Push directory targets

Apply per `directory_items` in stable order:

- create missing files/directories
- update changed files
- delete removed files when planned
- apply chmod for created/updated items if target semantics allow it

### Pull file targets

- if `reconcile_command` exists, run it for selected changed targets
- otherwise write repo-side content from the selected live-side source/projection
- update repo-side mode from live target when `chmod` rules require it

### Pull directory targets

- create/update/delete repo-side items from the selected live tree
- preserve ignore behavior already present in planning

## 6. Privilege model

### Recommendation

Do **not** port archived privilege phases literally.
Do port the **capability**.

Meaning:

- detect which execution steps require privilege
- acquire elevation once before execution if any selected step needs it
- keep that elevation alive during execution
- execute privileged steps with sudo only when the current step needs it
- keep printing one package timeline

So the answer is:

- **separate phases internally:** no, not as buckets
- **separate ordered steps internally:** yes
- **separate phases in the UX:** no

## 7. Sudo keepalive

### Recommendation

Yes, port the sudo keepalive idea, but in a smaller and cleaner form.

Why:

- hook commands can be long-running
- package timelines may interleave privileged and non-privileged steps
- repeated sudo prompts mid-run would feel broken

How:

- scan selected execution steps ahead of time
- request sudo once only if at least one selected step needs it
- start a keepalive thread/timer only for the execution session
- stop it in `finally`
- use `sudo -n` for already-authorized follow-up commands where possible
- keep the implementation isolated in a new execution/privilege helper instead of spreading it through CLI code

Do **not** port archived temp-file re-entry machinery unless we actually need process re-entry for some future edge case.
Start with direct per-step privileged execution.

## 8. Spinner decision

### Recommendation

Spinner is optional and should **not** be v1-critical.

Justification:

- step logs already form visible progress
- hooks and subprocess output may already stream; a spinner can fight with that
- the repo’s current UX is line-oriented, not animated/TUI-oriented
- tests stay simpler without terminal animation concerns

Recommended v1 behavior:

- no spinner by default
- print step start immediately
- print streamed subprocess output beneath the step when present
- print a final status marker for the step: `ok`, `failed`, `skipped`

If later desired, add a spinner only for silent long-running command steps and auto-disable it when command output is streaming or stdout is non-TTY.

## 9. Failure behavior

Default: fail fast at the first failed step.

Rules:

- stop the current package immediately
- stop the whole execution session immediately
- print the failed step clearly
- return a non-zero exit code
- do not run later hooks like `post_push` or `post_pull` after a failed earlier step in that package

This matches the repo’s fail-fast preference and avoids hidden partial-success semantics.

## 10. JSON output

Extend JSON mode for real execution rather than reusing dry-run payload unchanged.

Suggested shape:

- `mode: "execute"`
- `operation: "push" | "pull"`
- `packages`: package execution units
- `steps`: each with
  - kind
  - action
  - package id
  - binding
  - status
  - privileged
  - started_at / finished_at optional
  - exit_code for command steps
  - captured stdout/stderr or condensed output summaries for command steps

This keeps machine output useful for later UI layers.

## 11. Testing plan

Write tests first for the executor behavior.

### CLI tests

- execution mode entrypoint for push
- execution mode entrypoint for pull
- execution reuses selection and diff review flow
- package timeline output order
- fail-fast behavior
- no `post_push` or `post_pull` after earlier failure
- privilege handshake only when needed
- keepalive lifecycle starts/stops only when needed

### Engine/execution tests

- `BindingPlan -> execution units` conversion
- hook ordering for push
- reconcile-vs-direct-write selection for pull
- directory target execution ordering
- chmod enforcement after writes
- filtering out hooks for packages with no remaining executable targets still holds in execution

### Integration-style tests

- push writes live files
- pull writes repo files
- reconcile command receives expected env
- privileged executor path can be mocked cleanly

## 12. Implementation sequence

1. add `plans/` to `.gitignore`
2. introduce execution models in `src/dotman/models.py` or a dedicated execution module
3. add step builders from existing `BindingPlan`s
4. add filesystem executor for push CRUD
5. add filesystem/reconcile executor for pull CRUD
6. add command executor with streaming output
7. add privilege helper with one-session elevation + keepalive
8. add human execution renderer using package timelines
9. add JSON execution output
10. switch `push`/`pull` so plain invocation performs real execution
11. keep `--dry-run` as explicit preview mode
12. update docs once behavior is settled

## 13. CLI rollout recommendation

Current CLI hardcodes dry-run.
Target behavior after implementation should be:

- plain `push` / `pull` perform real execution
- `--dry-run` remains the explicit preview mode

That means the implementation should replace the temporary dry-run-only behavior instead of adding a separate `--execute` flag.

## Clarified decisions

1. Real execution should **not** be gated behind `--execute` first.
2. Replace ambiguous `check` with explicit hooks: `guard_push`, `pre_push`, `post_push`, `guard_pull`, `pre_pull`, `post_pull`.
3. Execution output should include nested command stdout/stderr when present.
   - Start with plain nested logs.
   - A condensed or scrolling sub-view can be explored later if needed.
4. One sudo authentication at execution start is acceptable when any selected step needs privilege.
