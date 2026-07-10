# Repo and target planning guards

## Goal

Implement issue #4: evaluate repo and target `guard_push` / `guard_pull` during operation-plan construction, before lower-scope host work, and remove those guards from generated execution sessions.

## Scope & Constraints

- Public seams: `DotmanEngine.plan_push*` / `plan_pull*`; narrow push/pull CLI behavior.
- Static manifest, ownership, collision, and reserved-path validation remain before guard outcomes can omit work.
- Guard hierarchy is repo, package-instance, target.
- Exit `0` admits, `100` omits declared scope, other nonzero aborts planning.
- Dependency skips remain local; sibling repos/packages/targets continue.
- Target guards run before probes, file projection, and directory scanning.
- Duplicate selections run each repo, package-instance, and target guard once per plan build.
- Capture remains strict; capture exit `100` is failure and existing reconcile fallback remains unchanged.
- Human/JSON diagnostics use repo, package-instance, and `repo:package.target` identities.
- Directory path-rule guards remain outside this issue.

## Work Plan

1. Update mapped pseudocode for guard validation, planning hierarchy, diagnostics, and execution exclusion.
2. Add failing engine tests for hierarchy, dependency locality, target/probe ordering, deduplication, static validation, execution exclusion, and capture status `100`.
3. Add failing narrow CLI tests for repo/target diagnostics and all-skipped behavior.
4. Generalize guard manifest validation and diagnostic identity.
5. Add hierarchical repo and target guard evaluation to static planning pipeline.
6. Remove repo/target guards from generated hook and execution plans.
7. Update user-facing docs.
8. Run targeted tests, typecheck/lint commands, full suite, two-axis code review, fix findings, commit.

## Validation

- `uv run pytest -q tests/engine/test_repo_target_planning_guards.py`
- `uv run pytest -q tests/engine/test_package_planning_guards.py tests/engine/test_execution.py tests/cli/test_execute.py`
- configured typecheck/lint commands from `pyproject.toml`
- `uv run pytest -q`

## Progress

- [x] Issue #4, parent #1, blocker #3, ADR, domain language, and package-guard implementation inspected.
- [x] Test seams confirmed by issue: public planning APIs and narrow CLI.
- [x] Mapped pseudocode updated.
- [x] Red tests added.
- [x] Implementation complete.
- [x] Full suite green (`705 passed`).
- [x] Two-axis review complete; package-instance hard-failure identity finding fixed and pull parity coverage added.
- [x] Commit created.

## Decisions

- Reuse one planning guard command runner across repo, package, and target scopes.
- Preserve static target metadata as ownership source; guard filtering only controls later host planning.
- Keep path-rule guards for their dedicated later issue.

## Outcomes & Retrospective

- Repo, package-instance, and target guards now run hierarchically during planning after static ownership validation.
- Target skips prevent probes, projections, and directory scans while preserving sibling and higher noop-eligible work.
- Generated operation plans contain no repo/package/target guard execution steps.
- Human and JSON diagnostics preserve repo, package-instance, and target identities without command text.
- Capture exit `100` remains a strict failure and follows existing reconcile fallback behavior.
