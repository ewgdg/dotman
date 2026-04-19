---
title: "Repo-level and target-level hooks plan"
status: completed
updated: 2026-04-18
---

# Repo-level and target-level hooks plan

Date: 2026-04-18

## Goal

Implement:

- repo-level hooks in `repo.toml` under `[hooks]`
- target-level hooks under `[targets.<name>.hooks]`
- synthetic hook-only selection rows for targets, using `[hooks] repo:package.target`

Keep the model consistent with existing package hooks while reusing the soft-guard contract from [`soft-guard-hook-semantics.md`](./soft-guard-hook-semantics.md).

## Dependency

This plan depends on the soft-guard foundation landing first.

Do not implement repo/target hooks on top of the current hard-fail-only guard behavior.

## Scope

In scope:

- repo-hook parsing from `repo.toml [hooks]`
- target-hook parsing from `[targets.<name>.hooks]`
- target-hook metadata parity with package hooks (`commands` + `run_noop`)
- repo/package/target hook planning and final filtering
- interactive selection rows for target hook-only work
- synthetic repo hook-only selection rows when repo hooks are retained without lower-scope work
- execution ordering across repo/package/target scopes
- human and JSON output updates
- docs and tests

Out of scope:

- new hook phases or names
- per-command selection rows
- rollback/add/untrack hook execution
- hook parallelism
- global reusable hook definitions outside repo/package/target manifests

## Locked behavior

### Hook names

All hook scopes use the same six names:

- `guard_push`
- `pre_push`
- `post_push`
- `guard_pull`
- `pre_pull`
- `post_pull`

### Manifest shapes

#### Repo hooks

`repo.toml` uses top-level `[hooks]`.

Supported forms:

```toml
[hooks]
pre_push = "echo hi"
post_pull = ["echo one", "echo two"]

[hooks.guard_push]
commands = ["exit 100"]
run_noop = true
```

#### Target hooks

Target hooks live under the target payload:

```toml
[targets.gitconfig]
source = "files/gitconfig"
path = "~/.gitconfig"

[targets.gitconfig.hooks]
pre_push = "echo hi"

[targets.gitconfig.hooks.post_pull]
commands = ["echo one", "echo two"]
run_noop = true
```

Target hooks support the same shorthand/table normalization as package hooks.

### Repo hook context

Repo hooks run once per repo per operation, not once per binding.

Because one repo may contribute multiple tracked bindings/profiles in the same run, repo hooks must use repo-scoped context only.

Locked rules:

- repo hook template expansion may use repo-local vars and repo-static context only
- repo hooks do **not** receive binding-specific package/profile vars
- repo hook env should include repo-scoped values such as `DOTMAN_REPO_NAME`, `DOTMAN_OPERATION`, `DOTMAN_REPO_ROOT`, and `DOTMAN_STATE_PATH`
- repo hooks should not expose ambiguous single-binding values like `DOTMAN_PROFILE` or `DOTMAN_PACKAGE_ID`

Document this clearly. It will otherwise look arbitrary during review.

### Target hook context

Target hooks run with the same binding/package context as the target they belong to, plus target-specific env:

- `DOTMAN_TARGET_NAME`
- `DOTMAN_TARGET_REPO_PATH`
- `DOTMAN_TARGET_LIVE_PATH`

Keep existing repo/package/profile env vars for target hooks too.

### Noop eligibility

#### Target hooks

Target hooks follow the same noop model as package hooks:

- by default, target hooks run when the target has executable non-noop work
- `run_noop = true` allows standalone hook-only target execution when the target action is noop
- `--run-noop` broadens noop eligibility for the current run even when metadata does not set `run_noop = true`

#### Repo hooks

Repo hooks also support `run_noop` and `--run-noop`.

Repo-level noop eligibility is evaluated after final selection of lower scopes:

- if the repo still has retained package/target work, repo hooks run normally
- if the repo has no retained lower-scope work but repo hooks are noop-eligible, dotman may retain the repo as standalone hook-only work

### Synthetic selection rows

#### Target hook-only rows

When a target has no executable target action but retains noop-eligible target hooks, add one synthetic row:

- `[hooks] repo:package.target`

Optional hook summary annotation may be shown in parentheses.

Rules:

- one row per target owner
- not one row per hook command
- not merged into package hook-only rows
- excluding the row drops standalone target-hook execution for that target
- keeping the row retains all noop-eligible target hooks for that target in normal hook order

#### Repo hook-only rows

When a repo has no retained lower-scope work but retains noop-eligible repo hooks, add one synthetic row:

- `[hooks] repo`

This keeps repo-level `run_noop` meaningful and avoids hidden executor fallback.

### Execution order

For each repo, execution order is:

1. repo `guard_*`
2. repo `pre_*`
3. each retained package in stable order
   1. package `guard_*`
   2. package `pre_*`
   3. each retained target in stable order
      1. target `guard_*`
      2. target `pre_*`
      3. target action steps (`create` / `update` / `delete` / `reconcile` / `chmod` as applicable)
      4. target `post_*`
   4. package `post_*`
4. repo `post_*`

Soft-guard behavior comes from the dependency plan:

- repo guard skip => skip repo subtree, continue next repo
- package guard skip => skip package subtree, continue next package
- target guard skip => skip target subtree, continue next target

### Failure semantics

- any non-guard non-zero hook exit is a hard failure
- target/package/repo `post_*` are success-only
- hook lists stop on first non-zero result
- no rollback hooks

## Design notes

### Introduce a top-level operation-plan wrapper

Current `push`/`pull` flows mostly pass `list[BindingPlan]` around. That is awkward for repo hooks because repo-scoped work is not owned by one binding.

Prefer introducing an explicit top-level wrapper, for example:

- `OperationPlan`
  - operation
  - binding plans
  - repo hook plans
  - any future repo-scoped selection state

This avoids smearing repo hooks onto the "first" binding and reduces later special cases.

### Generalize hook ownership

Current `HookPlan` is package-only. Repo and target hooks need richer identity.

Prefer one generalized hook-plan model over three unrelated classes.

Suggested minimum fields:

- `scope_kind`: `repo` | `package` | `target`
- `repo_name`
- `package_id` optional
- `target_name` optional
- `hook_name`
- `command`
- `cwd`
- `run_noop`

Keep owner identity structured. Do not flatten everything into rendered strings.

### Generalize hook finalization by retained owners, not only executable package ids

Current helpers filter hooks by executable package ids. That is too narrow once target and repo owners exist.

Refactor toward owner-aware finalization helpers that can answer:

- which hook scopes execute because they own executable work
- which hook scopes survive as standalone noop-eligible synthetic rows
- which hook scopes were excluded interactively

### Selection must stay owner-scoped

Do not expose per-command hook rows.

Allowed synthetic rows in this phase:

- repo hook-only row
- package hook-only row
- target hook-only row

That is enough power without letting users build nonsense half-hook states.

## Implementation order

### 1) Tests first

Add or update tests for:

- repo-hook parsing from `repo.toml [hooks]`
- target-hook parsing from `[targets.<name>.hooks]`
- target-hook shorthand and table-form normalization
- target-hook merge/override through package `extends`
- repo hooks planned once per repo even with multiple tracked bindings in that repo
- repo hooks do not receive ambiguous single-binding env/template context
- target hook-only synthetic row rendering `[hooks] repo:package.target`
- repo hook-only synthetic row rendering `[hooks] repo`
- excluding a synthetic target or repo row drops only that standalone hook work
- package hook-only rows and target hook-only rows can coexist for same package
- target guard skip continues next target
- package guard skip continues next package
- repo guard skip continues next repo
- human and JSON dry-run output show repo/package/target hooks in stable order

### 2) Parser and manifest normalization

Likely touch files:

- `src/dotman/repository.py`
- `src/dotman/manifest.py`
- `src/dotman/models.py`

Changes:

- load repo hooks from `repo.toml [hooks]`
- load target hooks from `[targets.<name>.hooks]`
- reuse or extend hook-spec normalization for repo/package/target scopes
- keep `run_noop` behavior aligned for package and target hooks, and supported for repo hooks too

### 3) Planning-model refactor

Likely touch files:

- `src/dotman/planning.py`
- `src/dotman/models.py`
- `src/dotman/engine.py`
- `src/dotman/installed.py`

Changes:

- introduce repo-scoped operation-plan wrapper
- generalize `HookPlan` ownership
- plan target hooks after target plans exist so hook env can reference concrete target paths
- plan repo hooks once per repo with repo-scoped context
- refactor hook finalization away from package-id-only helpers

### 4) Interactive selection refactor

Likely touch files:

- `src/dotman/cli.py`
- `src/dotman/cli_style.py`
- `src/dotman/cli_emit.py`

Changes:

- add synthetic repo hook-only rows
- add synthetic target hook-only rows
- keep package hook-only rows working
- re-finalize repo/package/target hooks after interactive exclusion
- preserve the canonical `.target` identifier style for target hook rows

### 5) Execution-session refactor

Likely touch files:

- `src/dotman/execution.py`

Refactor executor assembly so nested hook scopes are explicit enough to preserve ordering and soft skips.

Prefer repo/package/target execution units or an equivalent structured timeline. Avoid hiding repo/target hook ordering inside ad hoc sorting.

### 6) Output and docs

Update docs after the implementation shape settles:

- `docs/repository.md`
- `docs/cli.md`
- `docs/code-structure.md` if plan-wrapper / executor structure changes enough to merit mention

Also update examples if they are the best place to demonstrate repo hooks and target hooks.

## Test matrix

### Planning path

- repo with two bindings still gets one repo hook bucket
- target hook plans use rendered live/repo paths for env
- package extends overriding target hooks replaces the inherited hook definition cleanly

### Selection path

- noop target with `run_noop = true` gets `[hooks] repo:package.target`
- noop repo with `run_noop = true` gets `[hooks] repo`
- excluding synthetic target row removes only that target's standalone hooks
- excluding synthetic repo row removes only repo standalone hooks

### Execution path

- repo/package/target hooks nest in the locked order
- target guard exit `100` skips only that target subtree
- package guard exit `100` skips only that package subtree
- repo guard exit `100` skips only that repo subtree
- `post_*` stays success-only at all three levels

## Non-goals

- no hook command parallelism
- no per-command selection rows
- no rollback/add/untrack hook execution
- no binding-scoped repo hooks

## Decisions

- repo hooks live in `repo.toml [hooks]`
- repo hooks run once per repo per operation
- target hook-only rows use `[hooks] repo:package.target`
- repo hook-only rows are included so repo `run_noop` has visible plan state

## Progress

- 2026-04-18: initial plan written
- 2026-04-18: implemented repo-level hooks, target-level hooks, synthetic repo/target hook-only rows, nested execution ordering, docs, and tests.

## Blockers

- none

## Scope changes

- none yet
