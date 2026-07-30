# Sync command runners

## Goal

Implement GitHub issue #25 by moving push, pull, and restore into a focused
command runner backed directly by Dotman's typed operation plans and execution
runner, deleting the remaining global callback-dispatch record, and leaving the
root CLI as a composition and top-level error boundary.

## Intention

Each parsed command should have one declared owner. Sync orchestration should
plan, review, select, preview, execute, snapshot, and restore through direct,
typed collaborators instead of reconstructing a bag of function callbacks in
the root entrypoint. Observable ordering, output, and exit behavior remain
unchanged.

## Scope & Constraints

- Preserve public push, pull, and restore CLI behavior, including planning
  guards, review-before-selection order, dry-run and JSON output, snapshot
  timing, restore bookkeeping, interruption notices, and exit codes.
- Use `OperationPlan`, `ExecutionResult`, `RestoreAction`, `RestoreResult`, and
  the operation-runner event interfaces at their existing public seams.
- Remove `SyncCommandRuntime`, `run_sync_command`, root callback construction,
  silent dispatch fallbacks, and compatibility wrappers retained only for the
  old callback route.
- Keep configuration and engine creation lazy and scoped to the selected
  runner. Preserve unrelated user work in the tree.
- Test observable root CLI behavior and the public command-runner interface;
  do not rebuild or assert a fake internal dependency bag.
- Fixed point for review: `557093d`.

## Work Plan

1. Add one failing public runner/composition test that establishes sync command
   ownership and configuration/UI scoping without a callback record; implement
   the focused runner and make that slice green.
2. Migrate push and pull orchestration to direct typed planning, review,
   preview, and execution calls one behavior slice at a time. Update existing
   tests that monkeypatch root orchestration internals to exercise the public
   CLI or runner seam.
3. Migrate restore resolution, review, preview, and execution to the same
   focused boundary. Delete the callback record, constructor wiring, dispatch
   wrappers, and any now-empty module or compatibility surface.
4. Reduce root dispatch to parser invocation, runner composition/selection,
   engine/configuration construction, UI context setup, and top-level error
   handling. Update `docs/code-structure.md` to state the resulting design.
5. Run focused tests throughout, then static/compile checks, both complete-suite
   color environments, source scans, and standards/spec audits.

## Validation

- Focused command coverage:
  `uv run pytest -q tests/cli/test_cli_composition.py tests/cli/test_push.py tests/cli/test_pull.py tests/cli/test_snapshot.py tests/cli/test_execute.py`
- Typed lifecycle coverage:
  `uv run pytest -q tests/test_operation_runner.py tests/test_cli_emit.py`
- `uvx pyright` on changed source and test modules.
- `uv run python -m compileall -q src tests`
- `uv run pytest -q`
- `env NO_COLOR=1 uv run pytest -q`
- Source scans confirm no `SyncCommandRuntime`, callback-handler record,
  duplicate sync constructor wiring, or unsupported-command success fallback.

## Progress

- [x] Issue #25, blockers #21/#22/#24, repository instructions, domain
  vocabulary, relevant ADRs, current callback architecture, and public test
  seams inspected.
- [x] Baseline complete suite green with and without ambient `NO_COLOR`
  (`989 passed` in each environment).
- [x] Public runner/composition seam established red then green.
- [x] Push and pull migrated with focused coverage green.
- [x] Restore migrated and callback dispatch deleted.
- [x] Root composition and code-structure documentation finalized.
- [x] Complete validation and standards/spec audits green.

## Decisions

- Issue #25 pre-agrees the public CLI and command-runner interfaces as test
  seams. Existing operation-runner lifecycle tests remain authoritative for
  mutation and event ordering.
- This is a behavior-preserving architecture change. New tests cover ownership
  and composition gaps; existing CLI scenarios characterize the command flows.

## Surprises & Discoveries

- Issue #24 intentionally reduced the prior 46-field global callback record to
  the 14 sync/restore callbacks reserved for this issue.
- The baseline already has extensive end-to-end push, pull, restore, snapshot,
  output, and interruption coverage; the remaining weak tests patch root
  orchestration functions to prove negative calls.
- Moving the root interaction helpers exposed imprecise sequence annotations.
  `OperationPlan` already implemented the complete sequence protocol, so making
  that contract explicit and introducing `PlanCollection` removed untyped plan
  plumbing without adding an adapter.

## Outcomes & Retrospective

Push, pull, and restore now belong to `SyncCommandRunner`, which calls the typed
engine planning facade and operation runner directly. The former callback
record, its 14-field root constructor, and the sync fallback dispatch path are
deleted. Shared terminal selection and resolution live in
`cli_interaction.py`; edit argument normalization moved to the parser; and the
root CLI now only parses, composes/selects runners, constructs engine factories
and UI policy, and normalizes top-level failures.

Tests no longer patch a global dependency bag to prove skipped execution.
Public runner coverage verifies ownership, typed-plan preview, config
forwarding, and UI-scope cleanup, while existing root CLI and operation-runner
coverage protects review/selection order, guard exits, snapshots, restore,
JSON, and interruption behavior.

Final validation: `991 passed` normally and with `NO_COLOR=1`; focused sync
coverage `85 passed`; targeted Pyright `0 errors`; compileall, source scans,
and `git diff --check` green.
