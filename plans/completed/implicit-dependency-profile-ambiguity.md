# Implicit dependency profile ambiguity

Date: 2026-04-24

Updated: 2026-04-24

## Goal

Make tracked-state expansion deterministic for both `push` and `pull`.

A package identity may not be planned under two different runtime profile contexts unless the package is `multi_instance` and those profiles are part of distinct identities.

## Problem

Current tracked planning can silently pick one profile when two explicit roots pull in the same singleton dependency with different bound profiles:

```text
host:linux-meta@profile1      -> host:dep1@profile1
host:linux-sway-meta@profile2 -> host:dep1@profile2
```

For singleton `dep1`, identity is `host:dep1`, but runtime context differs. Target conflict resolution can hide this by picking the first/explicit winner. That is unsafe for:

- rendered target paths/content
- hooks and hook env
- packages with no targets
- future repo changes adding targets/hooks
- `pull` as well as `push`

## Locked decisions

### 1. Validate package-profile ownership before target ownership

Add a tracked expansion validation step before target metadata/action planning.

Invariant:

```text
Each resolved package identity has exactly one runtime requested profile after explicit overrides are applied.
```

### 2. Same-profile overlap is OK

```text
meta-a@profile1 -> dep1@profile1
meta-b@profile1 -> dep1@profile1
```

Deduplicate normally.

### 3. Singleton implicit dependency with multiple profiles is an error

```text
meta-a@profile1 -> dep1@profile1
meta-b@profile2 -> dep1@profile2
```

If `dep1` is singleton and neither profile is explicitly selected for `dep1`, fail.

Error should name the dependency and owners:

```text
ambiguous implicit profile contexts for host:dep1:
  host:dep1@profile1 required by host:meta-a@profile1
  host:dep1@profile2 required by host:meta-b@profile2
```

### 4. Multi-instance dependencies remain valid

If `dep1` has `binding_mode = "multi_instance"`, these are distinct identities:

```text
host:dep1<profile1>
host:dep1<profile2>
```

No-arg `push`/`pull` may operate on both. Bare `push dep1` / `pull dep1` remains selector ambiguity and should prompt/fail as existing tracked-package resolution does.

### 5. Explicit singleton selection can resolve ambiguity

If user explicitly tracks singleton `dep1@profile2`, then `host:dep1` has an explicit owner profile. Implicit claims for `host:dep1@profile1` must be suppressed before planning, not merely lose target conflicts.

Reason: otherwise profile-specific paths/hooks from the losing implicit context may still run.

Rules per resolved package identity:

- one requested profile total: keep it
- multiple requested profiles, exactly one explicit profile: keep only that profile context
- multiple requested profiles, zero explicit profiles: fail ambiguous implicit profile contexts
- multiple requested profiles, multiple explicit profiles: fail conflicting explicit profile contexts

### 6. `push` and `pull` share exact same validation

No operation-specific drift. Both commands expand tracked state through one shared package-selection resolver.

### 7. `track` validates future tracked graph before writing

`track` should fail if the new tracked state would make implicit package-profile ownership ambiguous.

This already belongs in tracked-state validation, so `record_tracked_package_entry(..., validate=True)` should use same shared resolver.

## Design

### New helper: resolve tracked package selections

Introduce one planning-layer helper used by validation and planning:

```py
def resolve_tracked_package_selections(
    engine,
    *,
    entries_by_repo: dict[str, list[TrackedPackageEntry]] | None = None,
) -> list[ResolvedPackageSelection]:
    ...
```

Responsibilities:

1. read effective tracked entries when `entries_by_repo` omitted
2. expand each explicit entry into root + dependencies
3. preserve repo/tracked-entry order
4. resolve package-profile claims per `ResolvedPackageIdentity`
5. return filtered, deduped selections safe to plan

Then both `collect_tracked_candidates()` and `collect_tracked_ownership_candidates()` consume this selection list instead of independently expanding entries.

### Extend dependency provenance

`ResolvedPackageSelection.owner_identity` loses the owner's requested profile for singleton owners. Add explicit provenance text or richer owner selection data.

Preferred small change:

```py
owner_selection_label: str | None = None
```

Set only for dependency selections:

```py
owner_selection_label=root_selection.selection_label
```

Use it only in diagnostics and JSON output.

### Error type

Add a typed `ValueError` subclass, likely in `planning.py` or a small new module:

```py
class TrackedPackageProfileConflictError(ValueError):
    package_identity: ResolvedPackageIdentity
    conflict_kind: Literal["ambiguous_implicit", "conflicting_explicit"]
    contenders: tuple[str, ...]
```

Human message must be stable enough for tests.

### Sorting / stability

Candidate/profile conflict lines should sort by:

```text
selection_label, owner_selection_label or ""
```

Do not rely on dict iteration for diagnostics.

## Implementation phases

### Phase 1: tests first

Add fixtures in `tests/helpers.py`:

- singleton shared dependency with profile-specific rendered path/content so target collision does **not** catch it
- same shape but `shared` marked `multi_instance`

Add tests:

1. `track` rejects adding second meta when singleton implicit dependency profiles differ
2. `plan_push()` fails from manually written invalid tracked state
3. `plan_pull()` fails from same invalid state
4. same singleton dependency profile from two metas is OK and dedupes
5. multi-instance dependency with different profiles is OK
6. explicit singleton dependency profile suppresses conflicting implicit profile before planning

### Phase 2: shared resolver

Refactor `planning.py`:

- add `resolve_tracked_package_selections()`
- move entry expansion out of `collect_tracked_candidates()` and `collect_tracked_ownership_candidates()`
- keep public engine wrappers stable where possible

### Phase 3: profile-claim resolver

Add internal function:

```py
def _resolve_package_profile_claims(selections: list[ResolvedPackageSelection]) -> list[ResolvedPackageSelection]:
    ...
```

This groups by `resolved_package_identity_key(selection)` and applies locked decision #5.

Important: this returns filtered selections. It is not only validation.

### Phase 4: wire validation

Update `validate_tracked_package_ownership()`:

1. call shared resolver first, surfacing profile ambiguity
2. pass resolved selections into ownership candidate collection
3. keep existing target ownership checks after profile validation

`record_tracked_package_entry()` then rejects bad implicit deps before writing.

### Phase 5: push/pull plan path

Update `_build_tracked_plans()` / `collect_tracked_candidates()` path so no-arg `push` and `pull` use same resolver.

Confirm query commands remain separate:

- `plan_push_query()` / `plan_pull_query()` may still plan user-requested selector directly
- tracked `push <package>` / `pull <package>` should continue using tracked-package lookup and then selected tracked state

### Phase 6: docs / CLI text

Update:

- `docs/cli.md` Track, Push, Pull sections
- `docs/repository.md` dependency/profile semantics

Add examples for resolving ambiguity:

- use same profile
- make dependency `multi_instance`
- explicitly track singleton dependency profile
- move overlapping target/config into shared package design

## Acceptance criteria

- `track meta-b@profile2` fails if it would make singleton implicit dependency ambiguous with existing `meta-a@profile1`
- `push` with no selector fails on already-invalid state before target planning/execution
- `pull` with no selector fails the same way
- no hidden “first profile wins” behavior remains in tracked-state replay
- multi-instance dependencies with different profiles still work
- explicit singleton dependency profile is respected and losing implicit contexts are not planned
- existing target ownership precedence tests still pass

## Open questions

1. Should CLI interactive `track` ask before explicit singleton dependency override, or is explicit `track dep@profile` enough intent?
   - Initial implementation: no prompt; explicit package entry is the resolution.
2. Should `info tracked` show profile ambiguity as an invalid tracked-state issue instead of failing hard?
   - Initial implementation: planning/validation commands fail; info UX can improve later if needed.
