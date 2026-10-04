# Drop same-signature target ownership tolerance

Date: 2026-04-24

Updated: 2026-04-24

## Goal

Make tracked target ownership strict and metadata-first:

- one live path has one tracked package owner
- same-precedence targets from different package instances conflict even if rendered bytes currently match
- `track` and `untrack` validate tracked-state ownership without building full push plans
- state-only commands do not read live files, compare bytes, run render/capture commands, or build diff review bytes

## Why this change exists

Current tracked-state validation reuses push planning. That makes `track` and `untrack` pay execution-planning costs:

- all tracked packages can be planned
- source/live files can be read
- render commands can run
- directories can be scanned and byte-compared
- review bytes can be built

This exists partly because collision logic tolerates duplicate live-path candidates when their target signatures match. For file targets, signature includes rendered/desired bytes. That forces validation to inspect content, even though tracked-state ownership should be a metadata rule.

Design problem: same bytes do not imply same owner. Validity should not depend on current file content or rendered output. If two packages manage the same live path, ownership is ambiguous and should be modeled as a shared dependency package instead.

## Target semantics

### Ownership invariant

For tracked state, each live path may have exactly one winning package instance.

Conflict rule:

- explicit target beats implicit target for same live path
- explicit vs explicit, different package instance, same live path => conflict
- implicit vs implicit, different package instance, same live path => conflict
- same resolved package instance reached through multiple tracked roots => dedupe/merge, not conflict

Package instance identity:

```py
(repo_name, package_id, bound_profile)
```

For single-instance packages, `bound_profile = None`.
For multi-instance packages, `bound_profile = requested profile`.

### No same-signature tolerance

Remove this behavior:

- same live path + same precedence + same target signature => allowed

Replace with:

- same live path + same precedence + different package instance => conflict

No byte/render equivalence check is used for ownership validation.

### State validation is metadata-only

Validation may render template strings for metadata fields:

- `target.source`
- `target.path`
- reserved paths
- ignore pattern strings if needed

Validation must not:

- read repo source bytes
- read live path bytes
- run render commands
- run capture commands
- build review bytes
- call `plan_push()` / `_build_tracked_plans(operation="push")`

## Scope

In scope:

- introduce metadata-only tracked ownership validation
- use it from `track` validation
- use it from `untrack` removal validation
- update implicit override preview to avoid push planning
- remove same-signature tolerance from tracked ownership validation
- update tests for conflict semantics and no `plan_push()` calls

Out of scope:

- changing push/pull execution planning unless tests require consistency cleanup
- changing tracked-state file format
- changing selector syntax
- changing hook execution semantics
- changing user-facing target identifier syntax

## Current code paths to replace

### Track

Current path:

```py
_handle_track()
  -> engine.validate_tracked_package_entry(binding)
     -> tracking.validate_tracked_package_entry()
        -> _validate_tracked_package_entries()
           -> _build_tracked_plans(operation="push")
```

Also:

```py
ensure_track_package_entry_implicit_overrides_confirmed()
  -> preview_package_selection_implicit_overrides()
     -> _collect_tracked_candidates(operation="push")
```

And profile fallback can multiply this cost:

```py
select_non_conflicting_track_profile()
  -> for profile in profiles:
       engine.validate_tracked_package_entry(candidate_binding)
```

### Untrack

Current path:

```py
remove_persisted_tracked_package_entry()
  -> _validate_tracked_package_entries()
     -> _build_tracked_plans(operation="push")
```

## Proposed implementation

### 1. Extract shared target metadata layer first

Avoid a parallel validator. The target-rendering truth should live in one place and be reused by info, validation, and execution planning.

Introduce a metadata-only target model, likely in `projection.py` or a new focused module such as `target_metadata.py`:

```py
@dataclass(frozen=True)
class TargetMetadata:
    repo_name: str
    package_id: str
    bound_profile: str | None
    requested_profile: str
    target_name: str
    repo_path: Path
    live_path: Path
    render_command: str | None
    capture_command: str | None
    reconcile: HookCommandSpec | None
    pull_view_repo: str
    pull_view_live: str
    push_ignore: tuple[str, ...]
    pull_ignore: tuple[str, ...]
    chmod: str | None
    command_cwd: Path
    command_env: dict[str, str]
```

Rules for this layer:

- render template strings for declarations (`source`, `path`, command strings, views, reconcile command)
- compute env/context values needed later
- merge ignore patterns
- perform declaration-only target collision/reserved-path checks when possible
- do not read source bytes
- do not read live bytes
- do not run render/capture commands
- do not compute target actions
- do not build review bytes

Refactor existing code so this layer is shared by:

- `info tracked` owned target rendering
- tracked-state validation
- implicit override preview
- push/pull execution planning

### 2. Refactor execution planning to consume metadata

`plan_targets()` should become two phases:

```py
metadata_targets = build_target_metadata(...)
for metadata in metadata_targets:
    build TargetPlan action/diff/review data from metadata
```

This keeps all declaration rendering in one shared place. Execution-only work remains in planning:

- infer final target kind if filesystem is needed
- read source/live bytes
- run render/capture commands
- compare bytes
- compute directory action item lists
- build review bytes

This step should preserve existing push/pull behavior before changing validation semantics.

### 3. Add ownership candidate model from metadata

Build ownership candidates from `TargetMetadata`, not from a separate target-rendering path:

```py
@dataclass(frozen=True)
class TrackedTargetOwnershipCandidate:
    repo_name: str
    package_id: str
    bound_profile: str | None
    requested_profile: str
    source_selector: str
    explicit: bool
    live_path: Path
    target_name: str
    target_label: str
    order: int
```

Keep it internal unless CLI prompt rendering needs it.

### 4. Resolve strict winners

For each `live_path` group:

1. group candidates by package instance identity
2. dedupe candidates with same package instance identity
3. find highest precedence (`explicit=1`, `implicit=0`)
4. contenders = highest precedence candidates by package instance
5. if more than one contender package instance remains, raise `TrackedTargetConflictError`
6. winner = only contender

Conflict message should keep existing shape where possible:

```text
conflicting explicit tracked targets for /path: repo:a@profile -> repo:a.target, repo:b@profile -> repo:b.target
```

### 5. Replace validation entry points

Change:

```py
def validate_tracked_package_entries(...):
    engine._build_tracked_plans(operation="push", bindings_by_repo=...)
```

To:

```py
def validate_tracked_package_entries(...):
    validate_tracked_package_ownership(engine, bindings_by_repo)
```

Then `track` and `untrack` inherit faster validation.

### 6. Replace implicit override preview

Current preview builds candidate push plans. Replace with metadata ownership candidates.

Required output still supports existing prompt fields:

- winner selection label
- winner package id
- overridden candidates
- requested profile comparison for `_explicit_override_needs_confirmation()`

Can either:

- keep `TrackedTargetOverride` but allow metadata candidate shape with matching attrs, or
- add a small CLI-facing override DTO and adapt rendering.

Prefer keeping compatible attrs to minimize CLI changes.

### 7. Preserve info tracked metadata path

Current `info tracked` was already moved away from `plan_push()`. Align it with new ownership helper so there is one metadata ownership implementation, not a separate one-off.

### 8. Tests

Add/adjust tests:

#### No planning calls

- `track` does not call `plan_push()` / `_build_tracked_plans()`
- `untrack` does not call `plan_push()` / `_build_tracked_plans()`
- implicit override prompt path does not call `plan_push()`
- profile fallback does not call `plan_push()` per profile

#### Strict ownership conflicts

- two explicit packages same live path conflict even when source bytes are identical
- two implicit packages same live path conflict even when source bytes are identical
- explicit package same live path overrides implicit package and prompt behavior stays intact
- same package instance reached through multiple tracked entries does not conflict
- multi-instance package instances with same live path conflict when profiles differ

#### Regression

- existing track/untrack conflict tests still pass or update expected wording only when semantics intentionally changed
- `info tracked` still emits owned targets and effective hooks

### 9. Docs

Add short docs note if user-facing docs exist for tracked ownership:

- one live path has one owner
- duplicate same-path declarations should be moved to a shared package dependency

## Migration / compatibility impact

Behavior change:

- previously accepted duplicate targets with identical signatures may now error during `track` / `untrack` validation.

Expected migration:

- create shared package that owns duplicated target
- make other packages depend on it

No tracked-state file migration needed.

## Validation commands

```bash
uv run pytest -q
```

Performance smoke checks:

```bash
time uv run dotman info tracked greetd >/tmp/dotman-info-greetd.out
time uv run dotman track <candidate> --yes
time uv run dotman untrack <candidate>
```

## Progress

- [x] Confirm current duplicate-same-signature behavior with focused test
- [x] Extract shared `TargetMetadata` builder
- [x] Refactor `plan_targets()` to consume `TargetMetadata`
- [x] Refactor `info tracked` to consume `TargetMetadata`
- [x] Add metadata ownership candidate builder from `TargetMetadata`
- [x] Add strict metadata conflict resolver
- [x] Route track/untrack validation through metadata resolver
- [x] Route implicit override preview through metadata resolver
- [x] Update/add tests
- [x] Run full test suite

## Decisions

- 2026-04-24: Drop same-signature tolerance. Ownership is semantic, not byte equality.
- 2026-04-24: Use metadata-only validation for tracked-state commands.
- 2026-04-24: Implemented metadata target builder, strict package-instance winner resolution, metadata-only track/untrack validation, metadata override preview, docs, and tests.

## Blockers

None known.

## Notes

Important edge: dependency-selected package targets currently use package id as owner selector in `info tracked`. Keep user-facing labels stable unless a deliberate UI cleanup is made.

Avoid duplication: do not implement metadata validation by copying chunks from `plan_targets()` into a separate validator. First split declaration rendering from execution action planning, then reuse that shared metadata layer everywhere.
