# Tracked package entry model refactor

Date: 2026-04-19

Updated: 2026-04-20

## Goal

Replace overloaded `Binding`/`bindings.toml` tracked-state model with a package-first model that matches actual semantics.

Target end state:

- selector input is represented separately from persisted tracked state
- persisted tracked state stores only explicit tracked package entries
- group selectors are input-time sugar only and are expanded immediately during `track`
- dependency packages are never persisted; they are derived only during planning/validation
- on-disk naming says truth:
  - `tracked-packages.toml`
  - `[[packages]]`
  - `package_id`

## Why this refactor exists

Current terminology and types blur together several different concepts:

- raw selector input from CLI
- resolved selector identity in repo config
- persisted tracked state rows
- package-owner relationships inferred from dependency closure

That drift already leaked into help text, runtime prompts, and internal APIs.

The product decision is now clear:

- tracked state is package-only
- groups are not tracked identities
- dependencies are not tracked identities
- `push` / `pull` / `untrack` operate against tracked package state, not historical selector requests

The code and file format should now follow that model directly.

## Locked decisions

### 1. No backward compatibility

Project is still early. Do not keep compatibility shims for:

- `bindings.toml`
- `[[bindings]]`
- `selector` in persisted tracked-state rows
- old binding-named tracked-state helpers where replacement is straightforward

Replace old format outright.

### 2. Persist only explicit tracked package entries

Persisted tracked state should contain only:

- repo
- package id
- profile

Persisted entry identity/normalization key is:

- single-instance package: `(repo, package_id)`
- multi-instance package: `(repo, package_id, profile)`

Do not persist:

- group names
- selector kind
- dependency packages
- source selector text
- owner bindings
- dependency closure

### 3. Group expansion happens only during `track`

`track` may still accept selector queries that resolve to either:

- one package
- one group that expands to many packages

But after resolution, the result must be converted to explicit tracked package entries immediately.

When persisted, package entries are written in canonical sorted order by:

- `repo`
- `package_id`
- `profile`

### 4. Dependency closure stays derived

When a tracked package depends on other packages:

- those dependency packages participate in planning/execution/validation
- those dependency packages do **not** become persisted tracked entries automatically

This keeps tracked state explicit and stable.

### 5. No first-class `ResolvedSelector` model required

The real domain split is:

- selector-side request/query
- persisted tracked package entry

A first-class `ResolvedSelector` type is optional implementation detail, not a required domain model.

### 6. Neutral selector-side type

Introduce a selector-side type for CLI/request flow.

Recommended shape:

```py
@dataclass(frozen=True)
class SelectorQuery:
    selector: str
    repo: str | None = None
    profile: str | None = None
```

This type may be used for:

- `track` requests
- `push` / `pull` / `untrack` queries

Command-specific resolvers still enforce command-specific semantics.

### 7. Package-only tracked-state type

Introduce a package-only tracked-state type.

Recommended shape:

```py
@dataclass(frozen=True)
class TrackedPackageEntry:
    repo: str
    package_id: str
    profile: str
```

This type should back:

- tracked-state file IO
- tracked-state normalization
- tracked package resolution
- tracked package listing / summaries where appropriate

### 8. Legacy tracked-state files hard fail

Do not read or silently migrate legacy tracked-state files.

Rules:

- if `bindings.toml` exists, commands that read tracked state should fail hard
- do not fall back from `tracked-packages.toml` to `bindings.toml`
- do not auto-migrate legacy files during normal command execution

### 9. New schema is strict

`tracked-packages.toml` must use the new schema only.

Rules:

- `schema_version = 1` is required
- missing `schema_version` is a hard error
- unknown or unsupported schema versions are hard errors
- persisted rows must use `[[packages]]`
- persisted rows must use `package_id`

### 10. Rename all tracked-state language now

This refactor should rename tracked-state language completely where meaning is explicit tracked package-entry state.

That includes:

- storage/file naming
- helper/function/type naming
- runtime wording
- docs
- machine-readable output keys

Temporary selector-side holdovers are acceptable only where the object still genuinely represents selector/query/planning input rather than persisted tracked state.

Rename boundary for this plan:

- rename all tracked-state/package-entry storage, resolution, UX, docs, and machine-readable output language now
- do not require renaming every selector-side/planning-side `Binding` usage in the same slice

### 11. Remove `forget`

`forget` should be removed as a redundant alias of `untrack`.

Keep one tracked-state removal command only:

- `untrack`

### 12. Tracked-state command resolution is package-only

For tracked-state commands such as `push`, `pull`, and `untrack`:

- package selectors are allowed
- group selectors are not allowed
- dependency-only package resolution via explicit tracked owner package entries remains allowed

This preserves current owner-derived package workflows without reintroducing group identities into tracked state.

## Proposed on-disk schema

Replace `bindings.toml` with `tracked-packages.toml`.

```toml
schema_version = 1

[[packages]]
repo = "example"
package_id = "git"
profile = "basic"
```

Notes:

- one row = one explicit tracked package entry
- file is dumb on purpose
- all dependency and ownership information remains derived at runtime
- rows are written in canonical sorted order by `repo`, `package_id`, `profile`

## Naming direction

### Strong rename targets

Rename tracked-state concepts away from `binding` where the meaning is package-only state.

Examples:

- `read_bindings_file` -> `read_tracked_packages_file`
- `read_bindings` -> `read_tracked_packages`
- `PersistedBindingRecord` -> `PersistedTrackedPackageRecord`
- `TrackedBindingSummary` -> rename to an owner/entry-oriented name if it still represents tracked-state provenance
- errors mentioning tracked bindings -> tracked package entries
- machine-readable output keys mentioning tracked bindings -> tracked package entries / tracked packages

### Acceptable temporary holdovers

Some selector/planning internals may continue to use binding-shaped helpers temporarily if that reduces churn during the first slice.

But any remaining `binding` naming should be justified by one of these cases only:

- selector-side query/planning code not yet migrated
- execution-plan naming where the object still genuinely represents selector-oriented planning input
- implementation checkpoints that will be cleaned in a follow-up slice already captured in this plan

## Command semantics after refactor

### `track`

Input:

- `SelectorQuery`

Resolution:

1. resolve selector against repo config
2. package selector -> one package entry
3. group selector -> expand to package entries
4. normalize/replace tracked entries by package scope
5. persist `TrackedPackageEntry[]`

Important:

- dependency packages are not added to persisted tracked state
- later operations should not remember the original group selector
- persisted rows are sorted canonically before write

### `push` / `pull`

Input:

- selector-shaped query, likely `SelectorQuery`

Resolution:

- resolve against persisted tracked package entries and owner-derived package matches
- package selectors are supported
- group semantics are not supported
- package-owner/ambiguity logic remains command-specific

Planning:

- build dependency closure from persisted tracked package entries
- execute on derived closure/winners/targets as needed

### `untrack`

Input:

- selector-shaped query, likely `SelectorQuery`

Resolution:

- resolve against persisted tracked package entries only, plus current owner-derived helper logic for package requirements
- allow package selectors only
- do not reintroduce generic selector/group semantics

## Scope

## In scope

- new `SelectorQuery` model
- new `TrackedPackageEntry` model
- new tracked-state file/schema
- tracking IO and normalization refactor
- `track` persistence flow refactor
- `push` / `pull` / `untrack` tracked-state resolution refactor where needed
- remove `forget` command
- targeted internal renames where tracked-state meaning is package-only
- docs and tests updates

## Out of scope

- adding dependency packages to persisted state
- keeping `forget` as a command alias
- preserving old file format compatibility
- broad execution-plan redesign beyond what this refactor forces
- unrelated cleanup of every historical internal `binding` name in one pass

## Implementation sequence

## Phase 1: tests and fixtures for new tracked-state contract

Add or update tests first for these facts:

- tracked-state file path is `tracked-packages.toml`
- `bindings.toml` presence hard-fails tracked-state commands
- tracked-state file requires `schema_version = 1`
- persisted rows use `[[packages]]`
- persisted rows use `package_id`
- `track group@profile` persists expanded package entries only
- dependency packages are not persisted after tracking
- persisted package rows are written in canonical sorted order
- `push` / `pull` / `untrack` do not accept group semantics via tracked-state resolution
- `push` / `pull` / `untrack` still allow dependency-only package resolution through tracked owner package entries
- untrack errors still explain owner requirements in package-entry language
- machine-readable output uses package-entry naming rather than binding naming
- `forget` command is removed

Likely files:

- `tests/cli/test_track.py`
- `tests/cli/test_push.py`
- `tests/cli/test_pull.py`
- `tests/cli/test_untrack.py`
- `tests/cli/test_list_tracked.py`
- `tests/cli/test_info_tracked.py`
- `tests/cli/test_snapshot.py`
- `tests/cli/test_execute.py`
- `tests/cli/test_edit.py`
- `tests/engine/test_bindings.py` or renamed successor if test naming changes
- any tracking/file IO focused tests that should be added

## Phase 2: add domain types

Add models:

- `SelectorQuery`
- `TrackedPackageEntry`

Placement likely starts in `src/dotman/models.py`.

Decision point during implementation:

- either keep old `Binding` temporarily for selector/planning code
- or replace selector-side `Binding` usage incrementally with `SelectorQuery`

Rule: do not rename selector-capable objects to `TrackedPackageEntry`.

## Phase 3: replace tracked-state file IO

Change tracking IO to use package-entry semantics end to end.

Tasks:

- replace `bindings.toml` path with `tracked-packages.toml`
- replace `read_bindings_file` and related readers/writers
- hard-fail on legacy `bindings.toml`
- parse/write `schema_version = 1`
- hard-fail on missing/unsupported schema version
- parse/write `[[packages]]` rows with `repo`, `package_id`, `profile`
- sort rows canonically before write
- update repo/state path helpers and any direct file assertions in tests

## Phase 4: refactor track persistence path

Refactor `track` flow so persistence always operates on `TrackedPackageEntry`.

Tasks:

- parse CLI selector into `SelectorQuery`
- resolve selector against repo config
- expand groups to package ids
- materialize `TrackedPackageEntry` values
- normalize tracked package entries by package scope/profile rules
- persist only explicit package entries
- write entries in canonical sorted order

This is the phase that should break the last semantic tie between group selectors and persisted state.

## Phase 5: refactor tracked-state resolution for push/pull/untrack

Refactor tracked-state commands to resolve against package-entry data rather than selector-oriented binding records.

Tasks:

- replace persisted binding records with tracked package records
- keep ambiguity/interactive resolution behavior aligned with current UX
- preserve package-owner requirement checks
- allow package-selector owner-derived resolution where current UX depends on it
- reject group selectors in tracked-state resolution
- ensure all error text stays package-entry oriented

This phase should make the core invariant obvious in code: tracked-state commands work from tracked package entries.

## Phase 6: rename tracked-state helpers and summaries

After data flow is stable, rename helpers/types that still misdescribe package-entry state.

This phase is for completing tracked-state/package-entry renames, not for forcing a full selector/planning model rename in the same slice.

Expected targets include parts of:

- `src/dotman/tracking.py`
- `src/dotman/engine.py`
- `src/dotman/cli.py`
- `src/dotman/cli_emit.py`
- docs under `docs/`

Be selective. Rename where the old name is now false, not merely old.

## Phase 7: docs and UX cleanup

Update docs to match final model:

- tracked state file name and schema
- legacy file hard-fail behavior
- `track` group expansion behavior
- explicit-vs-implicit package behavior
- package-entry terminology in CLI docs and code-structure docs
- `forget` removal

At minimum update:

- `README.md`
- `docs/cli.md`
- `docs/config.md`
- `docs/code-structure.md`
- any tracked-state/storage docs

## Risks

### 1. Partial rename confusion

Risk:

- code ends up with both package-entry and binding language in the same tracked-state path

Mitigation:

- prioritize tracked-state/storage/resolution renames first
- tolerate temporary selector-side holdovers only when meaning is still selector-oriented

### 2. Over-refactoring selector-side flow

Risk:

- trying to eliminate every `Binding` use in one pass balloons scope

Mitigation:

- keep selector-side refactor incremental
- focus first on tracked-state truth and file format truth

### 3. Subtle normalization regressions

Risk:

- package scope/profile replacement rules could change accidentally during type migration

Mitigation:

- add tests around replacement, multi-instance profile behavior, and group expansion before touching normalization code

### 4. Dependency closure accidentally persisted

Risk:

- refactor might blur explicit tracked entries with derived package closure

Mitigation:

- assert in tests that dependency packages do not appear in `tracked-packages.toml`
- keep closure-building logic separate from persistence helpers

## Done criteria

This plan is done when:

- `bindings.toml` is no longer used as tracked-state storage, and legacy presence hard-fails
- tracked state lives in `tracked-packages.toml`
- tracked-state rows are `[[packages]]` with `package_id`
- tracked-state files require `schema_version = 1`
- legacy `bindings.toml` causes hard failure instead of fallback/migration
- package-only tracked-state type exists and is used in tracking persistence/resolution
- `track` expands groups immediately into persisted package entries
- persisted tracked package rows are written in canonical sorted order
- dependency packages are derived only at planning/validation time
- tracked-state commands operate on package-entry semantics
- tracked-state commands reject group selectors
- tracked-state commands still allow owner-derived package resolution where applicable
- `forget` command is removed
- user-facing docs and runtime wording match the new model
- tests cover the new invariants

## Progress

- [ ] Plan approved
- [ ] Tests updated for package-entry tracked-state contract
- [ ] New models added
- [ ] Tracked-state file/schema replaced
- [ ] `track` persistence path migrated
- [ ] tracked-state resolution migrated for `push` / `pull` / `untrack`
- [ ] docs updated

## Open questions

No product or behavior questions block implementation start.

Minor implementation choice left open:

- whether selector-side `Binding` is replaced by `SelectorQuery` immediately or in a later cleanup slice

That choice should be decided during Phase 2 based on churn, but must not block the tracked-state/file-format refactor.
