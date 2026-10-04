# Package-centric planning refactor

Date: 2026-04-20

Updated: 2026-04-20

## Goal

Replace selector/binding-centric planning with package-centric planning.

Target end state:

- `ResolvedSelector` is selector-disambiguation result only
- `FullSpecSelector` is selector+profile request input only
- `TrackedPackageEntry` is persisted tracked-state row only
- planner resolves queries/entries into canonical package selections
- one executable plan exists per resolved package instance
- `OperationPlan` aggregates `PackagePlan`, not `BindingPlan`
- binding-named planning/execution APIs are removed
- tracked-state helpers may still use binding-themed method names until that surface is renamed independently

## Why this refactor exists

Current planner still centers binding-shaped request/plan objects even though tracked state is now package-entry-based.

That mismatch leaks into:

- planner model
- execution model
- diff/review model
- snapshot model
- CLI naming
- machine-readable output

Current `BindingPlan` is overloaded:

- starts from selector input
- expands to many packages
- carries profile/query semantics
- acts as execution unit

That makes one type span too many layers.

Need clean split:

1. input query
2. persisted tracked entry
3. canonical resolved package identity
4. resolved package selection with provenance
5. per-package executable plan

## Scope

In scope:

- replace `BindingPlan` with `PackagePlan`
- remove binding-centric planning/execution APIs
- introduce canonical package identity and selection models
- make conflict resolution operate across package plans
- update CLI/review/snapshot/execution terminology where it still reflects planner model
- update tests and docs

Implementation note:

- planner/execution migration is complete in this refactor
- selector-input DTOs now use `ResolvedSelector` and `FullSpecSelector`
- some tracked-state helpers still use binding-themed method names; renaming that surface is separate cleanup, not required for package-centric planning

Out of scope:

- changing tracked-state file format again
- changing package/profile semantics
- changing repo/package hook semantics unless required by per-package planning
- changing end-user selector syntax

## Locked decisions

### 1. Keep `TrackedPackageEntry`

`TrackedPackageEntry` remains persisted tracked-state row type.

Reason:

- storage DTO should stay storage-oriented
- `Entry` cleanly distinguishes file rows from runtime selections
- using `TrackedPackageSelection` beside `ResolvedPackageSelection` would blur persisted vs runtime layers

### 2. Add canonical runtime identity

Introduce:

```py
@dataclass(frozen=True)
class ResolvedPackageIdentity:
    repo: str
    package_id: str
    bound_profile: str | None
```

Meaning:

- exact package instance planner/executor works on
- canonical dedupe/conflict key
- single-instance packages use `bound_profile = None`
- multi-instance packages use concrete profile

### 3. Add resolved package selection

Introduce:

```py
PackageSelectionSourceKind = Literal["selector_query", "tracked_entry", "dependency"]


@dataclass(frozen=True)
class ResolvedPackageSelection:
    identity: ResolvedPackageIdentity
    requested_profile: str
    explicit: bool
    source_kind: PackageSelectionSourceKind
    source_selector: str | None = None
    owner_identity: ResolvedPackageIdentity | None = None
```

Meaning:

- runtime selected package unit with provenance
- `explicit=True` only when directly chosen by selector query or tracked entry
- dependency-selected packages use `explicit=False`
- `requested_profile` preserves original selection profile even when canonical identity profile is normalized to `None`
- `owner_identity` is used only for dependency-derived selections when provenance matters

### 4. Replace `BindingPlan` with `PackagePlan`

Introduce:

```py
@dataclass(frozen=True)
class PackagePlan:
    operation: str
    selection: ResolvedPackageSelection
    variables: dict[str, Any]
    hooks: dict[str, list[HookPlan]]
    target_plans: list[TargetPlan]
    hook_plans: dict[str, list[HookPlan]] | None = field(default=None, repr=False)
    repo_root: Path | None = None
    state_path: Path | None = None
    inferred_os: str | None = None

    @property
    def repo_name(self) -> str:
        return self.selection.identity.repo

    @property
    def package_id(self) -> str:
        return self.selection.identity.package_id

    @property
    def bound_profile(self) -> str | None:
        return self.selection.identity.bound_profile
```

Rules:

- one `PackagePlan` represents one resolved package instance only
- no `package_ids: list[str]` field
- no `selector_kind` field
- no `binding` field
- hooks/targets/variables are already package-scoped

### 5. Keep resolved/full-spec selector input types

`ResolvedSelector` and `FullSpecSelector` stay command/query layer.

Recommended shape remains:

```py
@dataclass(frozen=True)
class ResolvedSelector:
    repo: str
    selector: str
    selector_kind: SelectorKind


@dataclass(frozen=True)
class FullSpecSelector(ResolvedSelector):
    profile: str
```

Rules:

- parser/CLI first resolve text into `ResolvedSelector`
- profile-aware command flow upgrades that into `FullSpecSelector`
- command-specific resolvers decide whether package/group/target selectors are legal
- planner never executes directly on raw selector text

### 6. Remove `Binding`

`Binding` should be renamed to `FullSpecSelector` after migration.

Layer replacement:

- input/query layer: `ResolvedSelector` / `FullSpecSelector`
- persisted tracked-state layer: `TrackedPackageEntry`
- runtime planning layer: `ResolvedPackageSelection`

### 7. `OperationPlan` becomes package-plan batch

Replace:

```py
@dataclass(frozen=True)
class OperationPlan:
    operation: str
    binding_plans: tuple[BindingPlan, ...]
    ...
```

With:

```py
@dataclass(frozen=True)
class OperationPlan:
    operation: str
    package_plans: tuple[PackagePlan, ...]
    repo_hooks: dict[str, dict[str, list[HookPlan]]] = field(default_factory=dict)
    repo_hook_plans: dict[str, dict[str, list[HookPlan]]] | None = field(default=None, repr=False)
    repo_order: tuple[str, ...] = ()
```

Helper rename:

- `binding_plans_for_operation_plan` -> `package_plans_for_operation_plan`

### 8. Conflict resolution works on package plans

Tracked candidate collection and winner selection should operate on package plans directly.

Rules:

- one candidate contributor = one `PackagePlan`
- explicit vs implicit precedence derives from `ResolvedPackageSelection.explicit`
- candidate labels derive from package selection / package identity, not bindings

### 9. Keep user-facing tracked-state semantics

This planner refactor does not change tracked-state product behavior:

- tracked state persists explicit package entries only
- dependencies remain derived only
- package-only tracked-state commands stay package-only
- canonical sorted tracked file remains unchanged

## Exact model definitions

### New/retained core types

```py
@dataclass(frozen=True)
class ResolvedSelector:
    repo: str
    selector: str
    selector_kind: SelectorKind


@dataclass(frozen=True)
class FullSpecSelector(ResolvedSelector):
    profile: str


@dataclass(frozen=True)
class TrackedPackageEntry:
    repo: str
    package_id: str
    profile: str


@dataclass(frozen=True)
class ResolvedPackageIdentity:
    repo: str
    package_id: str
    bound_profile: str | None


PackageSelectionSourceKind = Literal["selector_query", "tracked_entry", "dependency"]


@dataclass(frozen=True)
class ResolvedPackageSelection:
    identity: ResolvedPackageIdentity
    requested_profile: str
    explicit: bool
    source_kind: PackageSelectionSourceKind
    source_selector: str | None = None
    owner_identity: ResolvedPackageIdentity | None = None


@dataclass(frozen=True)
class PackagePlan:
    operation: str
    selection: ResolvedPackageSelection
    variables: dict[str, Any]
    hooks: dict[str, list[HookPlan]]
    target_plans: list[TargetPlan]
    hook_plans: dict[str, list[HookPlan]] | None = field(default=None, repr=False)
    repo_root: Path | None = None
    state_path: Path | None = None
    inferred_os: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.selection.identity.repo,
            "package_id": self.selection.identity.package_id,
            "bound_profile": self.selection.identity.bound_profile,
            "requested_profile": self.selection.requested_profile,
            "explicit": self.selection.explicit,
            "source_kind": self.selection.source_kind,
            "source_selector": self.selection.source_selector,
            "targets": [target.to_dict() for target in self.target_plans],
            "hooks": {name: [item.to_dict() for item in items] for name, items in self.hooks.items()},
        }


@dataclass(frozen=True)
class OperationPlan:
    operation: str
    package_plans: tuple[PackagePlan, ...]
    repo_hooks: dict[str, dict[str, list[HookPlan]]] = field(default_factory=dict)
    repo_hook_plans: dict[str, dict[str, list[HookPlan]]] | None = field(default=None, repr=False)
    repo_order: tuple[str, ...] = ()
```

### Derived helper expectations

- canonical identity key: `(repo, package_id, bound_profile)`
- tracked entry normalization key stays:
  - single-instance package: `(repo, package_id)`
  - multi-instance package: `(repo, package_id, profile)`
- resolved package identity derives from tracked entry or full-spec selector by applying package binding mode

### Resolution helpers to add

Recommended helpers:

```py
def resolve_full_spec_selector(engine: Any, request: FullSpecSelector, *, operation: str) -> list[ResolvedPackageSelection]:
    ...


def resolve_tracked_package_entry(engine: Any, entry: TrackedPackageEntry) -> list[ResolvedPackageSelection]:
    ...


def build_package_plan(
    engine: Any,
    repo: Repository,
    selection: ResolvedPackageSelection,
    *,
    operation: str,
) -> PackagePlan:
    ...
```

Resolver rules:

- direct selector queries may expand groups into many explicit package selections
- tracked package entries resolve only to one explicit package selection
- dependency closure produces additional implicit package selections
- planner receives already-resolved package selections only

## Rename map

### Core models

- `Binding` -> renamed `FullSpecSelector`; selector resolution now uses `ResolvedSelector`
- `BindingPlan` -> `PackagePlan`
- `OperationPlan.binding_plans` -> `OperationPlan.package_plans`
- `binding_plans_for_operation_plan` -> `package_plans_for_operation_plan`

### Engine/planning APIs

- `resolve_binding(...)` -> `resolve_selector_query(...)`
- `plan_push_binding(...)` -> `plan_push_selector_query(...)` or `plan_push_query(...)`
- `plan_pull_binding(...)` -> `plan_pull_selector_query(...)` or `plan_pull_query(...)`
- `_build_plan(...)` -> `_build_package_plan(...)`
- `_build_operation_plan(plans=...)` -> `_build_operation_plan(package_plans=...)`
- `_collect_tracked_candidates(...) -> tuple[list[BindingPlan], ...]` -> `_collect_tracked_candidates(...) -> tuple[list[PackagePlan], ...]`
- `preview_binding_implicit_overrides(...)` -> `preview_package_selection_implicit_overrides(...)`

### Execution/review/snapshot

- `ExecutionStep.binding_plan` -> `ExecutionStep.package_plan`
- `ReviewItem.binding_label` -> `ReviewItem.selection_label` or `ReviewItem.package_label`
- snapshot helpers taking `Sequence[BindingPlan]` -> `Sequence[PackagePlan]`

### Naming cleanup in code/comments

- `binding_label` -> `selection_label` where provenance matters, or `package_label` where canonical package identity is enough
- `repo_binding_plans` -> `repo_package_plans`
- `plan_push_binding` text in tests/docs -> query/package-centric names

### Already package-entry-specific APIs

Keep package-entry names that are now truthful:

- `TrackedPackageEntry`
- `read_tracked_package_entries`
- `record_tracked_package_entry`
- `remove_tracked_package_entry`

But update internals that still use `Binding` payloads to use `TrackedPackageEntry` or `ResolvedPackageSelection` as appropriate.

## Planned architecture changes by subsystem

### 1. `models.py`

- add `ResolvedPackageIdentity`
- add `ResolvedPackageSelection`
- add `PackagePlan`
- replace `OperationPlan.binding_plans` with `package_plans`
- delete `Binding`
- delete `BindingPlan`

### 2. `engine.py`

- replace binding resolution entrypoints with selector-query resolution entrypoints
- make direct planning return package plans, not binding plans
- make tracked planning build package selections from tracked entries
- update helper signatures and return types accordingly

### 3. `planning.py`

- build one package plan per resolved package selection
- move query expansion out of `build_plan`
- make conflict-candidate collection operate on package plans
- derive explicit/implicit precedence from selection provenance

### 4. `tracking.py`

- stop adapting tracked package entries into `Binding`
- resolve tracked entries into `ResolvedPackageSelection`
- keep tracked file I/O package-entry-centric
- ensure tracked-package summaries still show explicit owner entries and implicit dependency packages correctly

### 5. `execution.py`

- replace all `binding_plan` references with `package_plan`
- group execution by `package_plan.selection.identity.repo`
- preserve requested-profile/source metadata only where UX or env construction needs it

### 6. `diff_review.py`, `snapshot.py`, `collisions.py`

- consume `PackagePlan`
- generate labels from `ResolvedPackageSelection`
- remove residual binding-centric assumptions

### 7. CLI/UI/output

- parser resolves text into `ResolvedSelector`
- profile-aware command flow upgrades to `FullSpecSelector`
- interactive selectors resolve to `ResolvedPackageSelection`
- prompts/errors stop referring to binding plans
- machine-readable output should prefer package/package-selection terms over binding terms

## Phased ExecPlan

### Phase 0 — lock semantics and add safety tests

Goal:

- lock current behavior before structural churn

Tasks:

- add characterization tests around direct push/pull planning for package and group selectors
- add characterization tests around tracked implicit dependency package inclusion
- add characterization tests around multi-instance package profile normalization
- add characterization tests around conflict winner selection across explicit vs implicit tracked packages
- add serialization tests for operation-plan payload shape that will intentionally change later

Exit criteria:

- baseline tests capture current behavior clearly enough to refactor with confidence

### Phase 1 — introduce package-centric core models in parallel

Goal:

- land new types without removing old ones yet

Tasks:

- add `ResolvedPackageIdentity`
- add `ResolvedPackageSelection`
- add `PackagePlan`
- add `OperationPlan.package_plans` alongside temporary compatibility path if needed
- add helper formatting/rendering for package selections and identities

Exit criteria:

- codebase can construct new package-centric types in tests without changing runtime path yet

### Phase 2 — move resolution boundary from binding to selector/package selection

Goal:

- stop feeding planner with `Binding`

Tasks:

- add `resolve_full_spec_selector(...)`
- add tracked-entry -> resolved-selection helpers
- make direct command flow resolve text into `ResolvedSelector` / `FullSpecSelector`
- resolve groups/packages into explicit `ResolvedPackageSelection`
- resolve dependency closure into implicit `ResolvedPackageSelection`

Exit criteria:

- planner-facing inputs are `ResolvedPackageSelection`, not `Binding`

### Phase 3 — replace `BindingPlan` with `PackagePlan` in planner

Goal:

- make planner emit one executable unit per package instance

Tasks:

- rewrite `build_plan` into `build_package_plan`
- replace multi-package plan fanout with one-plan-per-package build
- update tracked candidate collection to return `list[PackagePlan]`
- update conflict precedence logic to use `selection.explicit`
- update repo hook aggregation to consume `package_plans`

Exit criteria:

- no planner path constructs `BindingPlan`

### Phase 4 — migrate execution, review, snapshot, collision consumers

Goal:

- downstream systems consume package plans only

Tasks:

- replace `ExecutionStep.binding_plan`
- migrate execution grouping/order code
- migrate diff review label/render logic
- migrate snapshot helpers and payloads
- migrate collision helpers/tests using old plan types

Exit criteria:

- execution/review/snapshot all run from `PackagePlan` / `OperationPlan.package_plans`

### Phase 5 — remove old binding-centric models and APIs

Goal:

- delete transitional layer

Tasks:

- delete `Binding`
- delete `BindingPlan`
- delete adapter helpers using binding-shaped planner data
- rename remaining methods/tests/docs from binding-plan to package-plan terminology
- update JSON/output field names if any binding-plan wording remains

Exit criteria:

- no binding-centric planner/execution type remains in source

### Phase 6 — docs, cleanup, final verification

Goal:

- finish rename and verify whole system

Tasks:

- update docs for planner/runtime terminology
- update plan docs if implementation scope changed
- run targeted suites during each checkpoint
- run full suite at end

Final verification:

```bash
uv run pytest -q
```

## Risks

### 1. Profile normalization drift

Biggest semantic risk.

Need strict tests for:

- single-instance package identity dropping bound profile
- multi-instance package identity keeping bound profile
- user-facing labels still showing requested profile where necessary

### 2. Lost provenance in UI/errors

If refactor collapses straight to canonical package identity, user prompts may lose context about:

- which selector was chosen
- why dependency package was included
- which tracked package entry owns implicit package

Need `ResolvedPackageSelection` to preserve this.

### 3. Hook env regressions

Current hook/target env may rely on binding-shaped profile/selector context.

Need audit before deleting old fields.

### 4. Test churn

Large rename blast radius expected. Avoid mixing semantic changes and pure text churn where possible.

## Testing plan

Add/update coverage for:

- query -> resolved package selection expansion
- tracked entry -> resolved package selection resolution
- direct package query planning yields one package plan
- direct group query planning yields many package plans
- tracked dependency closure yields implicit selections + package plans
- per-path winner selection across package plans
- execution session building from `OperationPlan.package_plans`
- diff review and snapshot output using package-plan labels
- profile normalization across single-instance and multi-instance packages
- CLI machine-readable output shape after rename

## Done criteria

- planner/execution no longer depend on `Binding`
- `BindingPlan` removed
- planner builds `PackagePlan` only
- `OperationPlan` stores `package_plans` only
- execution/review/snapshot consume package plans only
- tracked-state I/O still uses `TrackedPackageEntry` only
- no planner/execution docs or machine-readable outputs use binding-plan terminology
- `uv run pytest -q` passes

## Progress

### Done

- [x] Draft plan
- [x] Add package-centric core models (`ResolvedPackageIdentity`, `ResolvedPackageSelection`, `PackagePlan`)
- [x] Add package-centric query planning entrypoints (`plan_push_query`, `plan_pull_query`)
- [x] Switch planner core to emit `OperationPlan.package_plans`
- [x] Switch tracked planning core to explicit/implicit package-plan batches
- [x] Add focused regression tests for direct query planning, tracked planning, and multi-instance normalization
- [x] Remove binding-plan compatibility layer from planner/execution models and APIs
- [x] Migrate execution/review/snapshot/CLI selection flows to `PackagePlan` / `package_plans`
- [x] Update broader tests to package-plan/query APIs and package-selection labels
- [x] Restore full green suite after package-plan migration (`uv run pytest -q`)
- [x] Collapse selector-input DTOs into `ResolvedSelector` and `FullSpecSelector`, removing `Binding` and `SelectorQuery`

### In progress

- None

### Blocked

- None

## Open questions

1. Preferred public API names:
   - `plan_push_query` / `plan_pull_query`
   - vs `plan_push_selector_query` / `plan_pull_selector_query`
2. Whether `ResolvedPackageSelection.owner_identity` is enough for dependency provenance, or whether richer provenance object is needed.
3. Whether operation-plan JSON should expose `package_plans` or flatter `packages` key.
