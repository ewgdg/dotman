# Remove target refs

Date: 2026-04-20

Updated: 2026-04-20

## Goal

Remove `target_refs` from dotman and restore simpler package/target semantics:

- packages declare only real targets under `[targets]`
- target planning no longer canonicalizes ref chains
- hooks/execution/info output no longer need ref-specific behavior
- `depends` remains package/group-only; no `package.target` dependency syntax is introduced

## Why remove it

`target_refs` adds a narrow capability with broad semantic cost.

Current pain:

- hook behavior is surprising and already drifted from docs
- planning must special-case canonical target ownership and contributor provenance
- info/output models carry ref-only data structures and rendering
- future target/package features keep reopening ref semantics questions
- the feature has no clear replacement path that preserves package-level clarity except smaller packages / normal `depends`

Conclusion:

- feature is not paying for its complexity
- package/target model should become explicit again

## Scope

In scope:

- remove manifest support for `[target_refs]`
- remove target-ref parsing/validation/model types/resolution
- remove canonical target planning and ref provenance fields
- remove tracking/info/CLI output for ref chains
- remove target-ref docs/tests/examples/plans from active surface
- update docs to state package dependencies stay package/group-only

Out of scope:

- adding `depends = ["package.target"]`
- introducing contributor/ref hooks
- redesigning package model around target-level packages
- automatically migrating existing user repos

## Locked decisions

### 1. Do not overload `depends`

`depends` stays package/group-only.

Reason:

- `depends` means package requirement
- `package.target` would mean target-level selection/aliasing
- reusing old `target_refs` behavior under `depends` would only hide the same complexity under worse naming

### 2. Prefer manual removal over blind revert

Commit `f80b9de` introduced target refs, but later commits touched many of the same files.

Plan:

- use revert attempt only as a reference/checkpoint if useful
- do not assume `git revert f80b9de` will be clean or correct
- remove feature intentionally from current HEAD

### 3. No backward-compatibility shim

When removed:

- manifests using `[target_refs]` should fail hard with clear error
- no deprecated compatibility path
- no hidden translation to `depends`

### 4. Keep package model explicit

After removal:

- package remains atomic lifecycle/config unit
- target remains executable unit inside package
- reuse should happen through smaller packages, meta packages, groups, or normal `depends`

## Files likely affected

Core removal targets:

- `src/dotman/manifest.py`
- `src/dotman/models.py`
- `src/dotman/projection.py`
- `src/dotman/repository.py`
- `src/dotman/planning.py`
- `src/dotman/tracking.py`
- `src/dotman/cli_emit.py`
- `tests/engine/test_plans.py`
- `tests/cli/test_info_tracked.py`
- `docs/repository.md`
- `docs/target-refs.md`
- `plans/completed/target-refs-plan.md`

Likely follow-on touch points:

- `README.md`
- any output/docs mentioning target refs or ref provenance

## Removal strategy

### Phase 0 — prove revert shape, but do not trust it blindly

Tasks:

- attempt `git revert --no-commit f80b9de` on scratch branch or temporary worktree
- inspect conflicts and resulting diff shape
- capture which chunks are still useful vs stale due to later refactors

Exit criteria:

- know whether revert is mostly usable or only informative

### Phase 1 — remove public schema and model support

Tasks:

- delete `TargetRefSpec`, `TargetRefStep`, `TargetRefChain`, `ResolvedTargetReference`, `TrackedTargetRefDetail`
- remove `PackageSpec.target_refs`
- remove parsing/validation/build helpers for target refs from `manifest.py`
- remove repository loading/merging/resolution of target refs
- make manifests with `[target_refs]` fail with explicit unsupported-feature error

Exit criteria:

- no model/schema path accepts target refs anymore

### Phase 2 — simplify planning/projection back to real targets only

Tasks:

- remove `resolve_target_reference` and related repository helpers
- make `projection.plan_targets()` iterate only real targets
- remove canonicalization/provenance fields such as `contributor_package_ids` and `ref_chains` if now unused
- remove package-planning exceptions added only for ref provenance retention

Exit criteria:

- planning emits only directly declared real targets
- no ref-specific planning data remains

### Phase 3 — remove tracking/info/output surface

Tasks:

- remove tracked-package/trackable-package `target_refs` payload fields
- remove CLI emit rendering for `:: target refs`
- update JSON/text output snapshots and related tests

Exit criteria:

- user-visible output has no target-ref sections or fields

### Phase 4 — remove docs/tests and update guidance

Tasks:

- delete `docs/target-refs.md`
- remove target-ref mentions from `docs/repository.md`, `README.md`, and any other docs
- update docs to recommend smaller packages / normal `depends` instead
- remove target-ref tests and fixtures
- archive or annotate old completed plan as historical only

Exit criteria:

- docs match runtime again
- no active tests expect target-ref behavior

### Phase 5 — final verification

Tasks:

- run focused suites during each checkpoint
- run full suite at end

Final verification:

```bash
uv run pytest -q
```

## Testing plan

Add/update coverage for:

- manifests with `[target_refs]` now fail with clear unsupported-feature error
- normal target planning still works for direct package queries
- tracked/info output no longer emits `target_refs`
- package hooks/target hooks still behave correctly for ordinary targets
- package dependency flows via `depends` remain unchanged

Tests to remove or rewrite:

- canonical target-ref planning tests
- ref-cycle tests
- ref-syntax tests
- `info tracked` target-ref output tests

## Risks

### 1. Later code now relies on ref-added fields indirectly

Need careful audit for:

- `contributor_package_ids`
- `ref_chains`
- any UI helpers assuming those fields exist

### 2. Revert-shaped diff may over-delete newer unrelated changes

Need compare revert output against current HEAD semantics before applying anything.

### 3. Error messaging churn

Need one clear message for removed feature so users are not left with confusing parser failures.

Recommended shape:

- `package manifest <path> uses unsupported [target_refs]; split package or use normal package depends instead`

## Done criteria

- no manifest/model/runtime support for `target_refs` remains
- no planner/execution/info code carries ref-specific logic or payloads
- docs no longer recommend or describe target refs as active feature
- manifests using `[target_refs]` fail clearly
- `uv run pytest -q` passes

## Progress

### Done

- [x] Decide not to add `depends = ["package.target"]`
- [x] Decide to remove `target_refs` instead of expanding semantics
- [x] Draft removal plan

### In progress

- None

### Blocked

- None

## Open questions

1. Should the removal be one commit or split into:
   - schema/model removal
   - planner/runtime cleanup
   - docs/tests cleanup
2. Whether to keep historical completed plan untouched or add note pointing to removal plan.
