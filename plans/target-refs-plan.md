---
title: "Target refs plan"
status: proposed
updated: 2026-04-19
---

# Target refs plan

Date: 2026-04-19

## Goal

Replace heuristic overlap tolerance with explicit target-level reuse.

Introduce package manifest support for:

```toml
[target_refs]
shared = "alpha.shared"
```

Meaning:

- current package declares local target ref `shared`
- ref points to another package target in same repo
- ref does not create new execution work
- planner resolves ref chain to one canonical/root target action
- review, selection, diff, and execution operate on canonical action once
- provenance still records local ref owners

This feature is intended to solve "same target reused by multiple packages" without ambiguous collision heuristics.

## Motivation

Recent collision-tolerance experiment exposed undefined behavior:

- direct binding plans can show duplicate review rows
- direct binding plans can execute same live path twice
- target-hook behavior becomes ambiguous
- tracked and direct flows diverge
- nested dir/file overlaps are especially muddy

Explicit refs are cleaner than inferring sameness from path collisions.

## Scope

In scope:

- manifest schema for package-level target refs
- manifest validation for target ref syntax
- target ref resolution to canonical/root target
- cycle detection and missing-target errors
- canonical planning so one effective action exists per resolved target root
- provenance model for local ref owner -> canonical target
- selection/review/execution behavior on canonical targets
- info/output updates so refs remain visible
- tests and docs

Out of scope:

- cross-repo target refs
- overrides on refs
- hooks declared on refs
- nested dir/file overlap heuristics
- arbitrary target-sharing inferred from repo/live path equality

## Locked behavior

### Manifest shape

Use top-level package table:

```toml
[target_refs]
shared = "alpha.shared"
gtk_settings = "base.gtk_settings"
```

Rules:

- keys are local target names in current package namespace
- values must be `<package_id>.<target_name>` in same repo
- local target name validation uses normal target-name rules
- local ref names must not collide with real target names in same package

### Resolution model

- refs may point to real targets or to other refs
- planner resolves every ref chain to one canonical/root real target
- ref cycles fail hard with full cycle text
- missing package/target fails hard
- canonical/root target must ultimately be a real target, not an unresolved ref

Example:

- `c.shared -> b.shared -> a.shared`
- canonical target = `a.shared`

### Same-repo only

- refs may only point to targets in same repo
- no repo-qualified ref syntax in v1

### No overrides on refs

Refs are pointers, not partial target definitions.

So refs may not define:

- `source`
- `path`
- `render`
- `capture`
- `reconcile`
- `chmod`
- ignores
- hooks
- any other target execution metadata

If users need a distinct action, they must declare a real target.

### Canonical planning

Operational flows should work on canonical actions, not declaration aliases.

For one canonical target action:

- review menu shows one row
- selection menu shows one row
- diff review runs once
- execution runs once
- reconcile/capture semantics attach to canonical target only

Refs are provenance only.

### Hooks

- target hooks belong to real canonical targets only
- refs cannot define target hooks
- target hooks run once with canonical target action
- package hooks still run per retained package as today
- repo hooks remain unchanged

Package-hook behavior needs one explicit rule during implementation:

- a package that contributes only target refs should still count as retained work for its own package hooks when at least one of its refs resolves to a retained canonical action

This prevents package semantics from disappearing just because target action was reused.

### UI / output

Operational UIs should be canonical-first.

Selection / review / execution rows:

- primary label should use canonical target
- ref provenance should be shown as annotation when helpful

Example shapes:

- `a.shared (refs: b.shared)`
- `a.shared (refs: b.shared, c.shared)`

Info / inspect output may show full chain:

- `c.shared -> b.shared -> a.shared`

### Collision behavior

Target refs are intended to replace relaxed overlap inference.

Plan assumption:

- revert current collision-tolerance change
- keep current hard collision rules for independently declared targets
- if users want reuse, they should declare `target_refs`

## Design notes

### Separate declaration refs from effective target actions

Need distinct model layers:

1. declaration/provenance layer
   - local package target name
   - optional ref chain
2. effective canonical action layer
   - one real target plan
   - one repo/live/projection behavior

Without this split, direct and tracked execution will keep diverging.

### Prefer canonicalization before UI/execution

Do not patch review, selection, and execution separately.

Instead:

- resolve refs during planning
- produce canonical target/action set once
- let downstream flows consume one shape

## Implementation outline

1. Revert current collision-tolerance commit.
2. Extend manifest/package model with `target_refs`.
3. Add parser + validation for `<package>.<target>` references.
4. Add ref resolver with cycle detection and root flattening.
5. Extend planning model to keep canonical target plus ref provenance.
6. Update selection/review/execution to consume canonical targets once.
7. Update info/payload output to surface ref provenance.
8. Add docs and tests.

## Testing plan

Add coverage for:

- manifest parse of `target_refs`
- invalid ref syntax
- missing target/package errors
- ref cycles
- daisy-chain flattening to root target
- direct binding plan shows one canonical action
- tracked plan shows one canonical action
- selection menu shows one row for canonical action
- review menu shows one row for canonical action
- execution runs target action once
- target hooks run once for canonical target
- package hooks still run for packages owning refs
- info/debug output shows ref chain / provenance

## Progress

- 2026-04-19: plan created after collision-tolerance experiment exposed ambiguous execution/review semantics.
- 2026-04-19: manifest parsing, repo-level ref resolution, canonical target planning, package-hook retention for canonical-owner/ref packages, and core tests landed. Operational UI currently stays canonical-only with no ref annotation.
- 2026-04-19: `info tracked` now exposes declaration-centric outgoing ref chains for packages that define `target_refs`; JSON includes structured chain data and human output shows compact `local -> ... -> canonical` chains.

## Decisions

- Use `target_refs`, not `foreign_targets` or `target_links`.
- Allow daisy-chain refs; resolve to canonical/root real target.
- Same-repo only in v1.
- Operational selection/review/execution labels stay canonical-only; no inline provenance annotation in primary UI.
- No overrides on refs.
- No target hooks on refs.
- Operational UI is canonical-first; info/debug UI should show outgoing declaration chains, not backlink/contributor annotations.
- Current heuristic collision-tolerance change should be reverted before feature work lands.

## Open questions

- None right now. Remaining work is integration/doc follow-through, not product-shape uncertainty.
