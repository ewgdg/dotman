# Package planning guards

## Goal

Implement issue #3: evaluate package `guard_push` / `guard_pull` during operation-plan construction, before host-state projection, and remove package guards from execution.

## Scope & Constraints

- Public seams: `DotmanEngine.plan_push*` / `plan_pull*`; narrow push/pull CLI behavior.
- Static ownership and collision validation remain before guards.
- Package guard exit `0` admits, `100` omits package work, other nonzero aborts planning.
- Guards use pipe I/O only, permit configured elevation, reject `run_noop`.
- `run_noop` becomes planning input and affects pre/post hook eligibility only.
- Guard skips become operation-plan diagnostics; command text stays private.
- Repo/target/path-rule planning guards remain outside this ticket.

## Work Plan

1. Add public-seam failing tests for package guard status, ordering, dedupe/rerun, `run_noop`, validation, execution exclusion, CLI diagnostics, and all-skipped flow.
2. Add guard diagnostic/result models and manifest contract validation.
3. Evaluate package guards after static ownership selection and before host projection.
4. Thread `run_noop` through planning APIs and retain only pre/post hooks.
5. Remove package guards from executable hook plans.
6. Emit human/JSON planning diagnostics and short-circuit all-skipped CLI operations.
7. Update docs and mapped pseudocode.
8. Run targeted tests, full suite, two-axis code review, fix findings, commit.

## Validation

- `uv run pytest -q tests/engine/test_package_planning_guards.py`
- relevant existing engine/CLI/progress/execution tests
- `uv run pytest -q`

## Progress

- [x] Issue, ADR, domain language, existing planning/execution flow inspected.
- [x] Test seams confirmed from parent issue: public planning APIs and narrow CLI.
- [x] Red tests added.
- [x] Implementation complete.
- [x] Full suite green (`682 passed`).
- [x] Code review complete; two-axis follow-up found no package-slice spec blockers after fixes.
- [x] Commit created for issue #3.

## Decisions

- Operation plans carry structured package guard-skip diagnostics.
- Skipped packages are omitted from `package_plans`; diagnostics preserve their identity and reason.
- Package guard commands are rendered and run from static package planning context before projection.
- Existing generic pipe command runner is reused for process/elevation behavior; planning owns guard status semantics.

## Outcomes & Retrospective

- Package guards now decide eligibility before projection and never enter package execution sessions.
- Planning diagnostics survive human and JSON flows without exposing command text.
- Ambient execution confirmation state is explicitly removed from planning guard environments.
- Repo/target guard schema and execution behavior remain unchanged for later planning slices.
