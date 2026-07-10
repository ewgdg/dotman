# Directory path-rule planning guards

## Goal

Implement GitHub issue #5: allow directory path rules to declare planning-time `guard_push` and `guard_pull` commands that prune only matching Sync Units.

## Scope & Constraints

- Public seams: `DotmanEngine.plan_push*` / `plan_pull*`; narrow push/pull CLI behavior.
- Path-rule guards run after managed repo/live candidate discovery and before child projection/comparison.
- Candidate paths already exclude operation ignores, `.gitignore` control files, and skip-marker subtrees.
- Repo-only, live-only, shared changed, and shared noop children activate matching rules.
- Each active rule runs once. Overlapping rules run in declaration order while matching Effective Work remains.
- Exit `100` removes that rule's remaining matching candidates; other rules and unmatched candidates continue.
- Scalar path-rule policy keeps existing later-value precedence.
- Guard environment uses target-root paths plus `DOTMAN_PATH_RULE_PATTERN`; no child path is exposed.
- Path-rule pre/post hooks and guard `run_noop` remain invalid.
- Diagnostics keep `repo:package.target` identity and render/store pattern separately.

## Work Plan

1. Update mapped pseudocode for models, manifest normalization, guard evaluation, directory projection, orchestration, and diagnostics.
2. Add failing engine tests for schema, activation, exclusions, overlap ordering, pruning, environment, failure reasons, and all-skipped directories.
3. Add failing narrow CLI tests for human/JSON diagnostics and prompt/execution bypass when all children are skipped.
4. Extend path-rule models and manifest normalization with guard-only hooks.
5. Evaluate active path-rule guards during directory planning, prune candidate paths, and collect diagnostics into the operation plan.
6. Update human/JSON error and skip rendering.
7. Update ADR and user-facing configuration/CLI docs.
8. Run targeted tests frequently, compile/type validation regularly, full suite once, two-axis code review, fix findings, and commit.

## Validation

- `uv run pytest -q tests/engine/test_path_rule_planning_guards.py`
- `uv run pytest -q tests/engine/test_plans.py tests/engine/test_repo_target_planning_guards.py tests/cli/test_execute.py`
- `uv run python -m compileall -q src tests`
- `uv run pytest -q`

## Progress

- [x] Issue #5, parent #1, blocker #4, ADR, domain language, and existing guard/path-rule code inspected.
- [x] Test seams confirmed by issue: public planning APIs and narrow CLI.
- [x] Mapped pseudocode updated.
- [x] Red tests added (`12 failed` before implementation).
- [x] Implementation complete.
- [x] Full suite green (`717 passed`).
- [x] Two-axis review complete; no blockers. One optional target-identity data-clump note deferred to avoid speculative scope expansion.
- [x] Commit created.

## Decisions

- Keep scalar policy and guard composition separate: scalar fields resolve per child; guards evaluate per declared rule.
- Represent path-rule skips as target-scoped identity plus separate pattern metadata.
- Reuse one planning guard runner and one elevation broker session across hierarchical and path-rule guards.

## Outcomes & Retrospective

- Path-rule guards now activate from managed repo/live candidates before child comparison and prune only their matching Sync Units.
- Overlapping guards compose in declaration order while scalar child policy keeps later-value precedence.
- Human/JSON diagnostics retain target identity and carry pattern separately without command text.
- Existing guard hierarchy, directory policy, execution, progress, and CLI tests remain green.
