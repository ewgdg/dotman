---
title: "Target sync policy plan"
status: completed
updated: 2026-04-17
---

# Target sync policy plan

Date: 2026-04-15

## Goal

Add a manifest-level policy for whether a target can participate in:

- `push`
- `pull`
- both

This is not a new sync algorithm. It is an execution gate.

## Locked naming

- field name: `sync_policy`
- values: `push-only`, `pull-only`, `both`
- default: `both`

Do not use plural naming.
Do not use `pull-or-push`.

## Locked semantics

- `both` means the target is eligible for both push and pull.
- `push-only` means the target is eligible only for push.
- `pull-only` means the target is eligible only for pull.

This setting controls allowed operations, not file ownership or conflict resolution.

## Scope decision

Support the policy at both levels:

1. package level as a default
2. target level as an override

Precedence should be:

1. target explicit value
2. package explicit value
3. builtin default `both`

Package inheritance should continue to work with the same last-wins merge behavior.

## Behavior

- During planning, dotman should skip target work that is not allowed for the requested operation.
- Hooks should continue to follow selected executable target work, so a package with no eligible targets stays quiet.
- Existing default behavior must remain unchanged because the default is `both`.

## Implementation phases

### Phase 1: tests first

Add tests for:

- default `both` at package level
- target override of package-level policy
- package inheritance of policy
- invalid policy value rejection
- push planning excludes `pull-only` targets
- pull planning excludes `push-only` targets
- hook filtering still follows the eligible target set

### Phase 2: model and manifest parsing

Touch:

- `src/dotman/models.py`
- `src/dotman/manifest.py`
- `src/dotman/repository.py`

Add a normalized enum helper for `sync_policy`, then thread it through package and target spec construction.

### Phase 3: planning and execution gates

Touch:

- `src/dotman/projection.py`
- `src/dotman/planning.py` if needed for summary behavior
- `src/dotman/installed.py` if the field is exposed in info output

Filter target plans by operation before execution and before hook selection.

### Phase 4: docs and examples

Update the repository docs to describe:

- what `sync_policy` means
- where package-level defaults live
- how target overrides work
- the default `both` behavior

Also update example manifests where a concrete example helps.

## Non-goals

- No new sync engine.
- No conflict resolution feature.
- No behavioral change for existing configs that omit the field.

