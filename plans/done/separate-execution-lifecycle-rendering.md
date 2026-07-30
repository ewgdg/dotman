# Separate execution lifecycle from output rendering

## Goal

Implement GitHub issue #22 by making one execution runner own the complete push, pull, and restore mutation lifecycle while human and JSON renderers consume typed lifecycle events/results without performing filesystem or privilege work.

## Intention

Execution policy belongs in one domain boundary. CLI dispatch prepares reviewed work, selects a renderer, invokes the runner once, and renders the returned result. The runner owns privilege scope, mutation ordering, snapshot transactions, restore bookkeeping, failure cleanup, and conversion of execution callbacks into typed events.

## Scope & Constraints

- Preserve the existing public CLI, JSON payloads, human output, package/repo/step order, child-output streaming, TTY passthrough, interruption semantics, and exit codes.
- Preserve `ExecutionSession` and `ExecutionResult` as the sync engine contracts; do not duplicate step execution logic.
- Test lifecycle behavior through runner events/results without terminal-output capture.
- Test human and JSON rendering from constructed events/results without executing commands or filesystem mutations.
- Do not retain compatibility wrappers whose only purpose is the removed emitter-owned execution path.
- Fixed point for final review: `bf7edbf`.

## Execution lifecycle

### Sync operations

1. Build the `ExecutionSession` from the reviewed plans.
2. Enter one sudo lease scope for the operation.
3. Emit the operation-started event before package or step events.
4. Let `execute_session` retain repo/package/step ordering, command runtime selection, TTY passthrough, child-output streaming, interruption normalization, and step exit interpretation.
5. On each execution callback, emit the corresponding typed package/step event in callback order.
6. For push only, lazily create one snapshot immediately before the first non-hook step starts. Pull never creates a snapshot.
7. After a returned result, finalize a created snapshot as `applied` only for exit code 0 and `failed` otherwise, then prune once.
8. If execution raises after snapshot creation, finalize it as `failed`, prune once, and re-raise the original exception.
9. Attach planning guard diagnostics to the returned execution result and emit operation-finished last.

### Restore operations

1. Enter one sudo lease scope for the operation and emit restore-started.
2. Ignore noop actions for mutation and progress, preserving their current invisibility.
3. For each visible action in order, emit action-started, apply that mutation once, and emit action-finished.
4. Stop at the first failed action and return the existing failed result/exit-code contract.
5. On success, including no pending actions, record one restore occurrence before returning.
6. Emit restore-finished last. Unexpected exceptions that are outside the reported action-failure contract propagate after the sudo scope closes.

## Typed event and result contracts

- Sync events: operation started, package started, step started, step finished, package finished, operation finished.
- Restore events: operation started, action started, action finished, operation finished.
- Events carry the existing immutable session/unit/step/action/result objects plus stable indexes/counts needed by renderers; they do not expose mutation callbacks.
- Event order is synchronous and matches observable execution order. An event consumer may render immediately or collect events for deterministic tests.
- Sync returns `ExecutionResult`; restore returns `RestoreResult`. Existing `to_dict()` and `exit_code` contracts remain authoritative.

## Renderer contracts

- The human renderer consumes lifecycle events and prints the current headers, package/action progress, statuses, errors, and path formatting. It never calls execution, snapshot, restore, sudo, or filesystem helpers.
- The JSON renderer does not print progress events. It serializes exactly one final result document, so JSON stdout cannot be mixed with human progress.
- Human execution enables child-output streaming; JSON execution disables it. TTY commands still use terminal passthrough because that behavior belongs to the command runtime, not the renderer.
- Renderer tests construct typed events/results directly and assert output without running mutation code.

## Work Plan

1. Add lifecycle tests for typed event order, lazy snapshot success/failure finalization, one sudo scope, restore mutation order/fail-fast behavior, and restore bookkeeping; implement the runner in vertical red/green slices.
2. Add renderer-only tests for human event output and single-document JSON output; move execution presentation out of the current emitter orchestration.
3. Rewire CLI command handlers to choose presentation policy and invoke each lifecycle exactly once; remove duplicated sudo, preflight, snapshot, restore mutation, and cleanup ownership.
4. Run focused execution/snapshot/CLI checks and compile/type checks throughout, then the complete suite once.
5. Review the three-dot diff against `bf7edbf` on Standards and Spec axes, resolve findings, move this plan to `plans/done/`, and commit with a semantic message closing #22.

## Validation

- New runner lifecycle tests without `capsys` or terminal capture.
- New renderer tests without command execution or filesystem mutation.
- Focused existing suites: execution engine, CLI execution, push/pull, snapshots, CLI emitters, and command dispatch.
- `uv run python -m compileall -q src tests`
- Available static type checker on changed modules; if none is configured, compile validation is the repository check.
- `uv run pytest -q` once after focused validation is green.
- Source scans confirm renderers do not import or call execution/snapshot mutation or sudo helpers, and CLI dispatch does not own execution sudo/snapshot/restore cleanup.

## Progress

- [x] Issue #22, blocker #20, repository instructions, domain vocabulary, current lifecycle coupling, and approved test seams inspected.
- [x] Lifecycle, event ordering, snapshot transaction, restore behavior, renderer contracts, and validation sequence recorded before implementation.
- [x] Runner lifecycle implemented test-first.
- [x] Renderers separated and CLI composition migrated test-first.
- [x] Focused and complete validation pass.
- [x] Independent Standards and Spec reviews pass.
- [x] Plan finalized for commit.

## Decisions

- The issue acceptance criteria pre-agree the public test seams: typed runner events/results and renderer output from those values.
- The existing execution engine continues to own step semantics; the new runner owns the operation-level transaction around it.
- Streaming is an execution option selected by presentation composition, while all formatting remains in renderer consumers.
- Elevation requested outside a runner-owned operation scope is released immediately, preventing a planning runtime from leaking into a later operation while preserving scoped keepalive behavior.

## Surprises & Discoveries

- The current path preflights sudo in both `cli_emit.execute_plans` and `execution.execute_session`, and nests sudo scopes in command dispatch and the emitter.
- Snapshot creation/finalization/failure cleanup is embedded in the human/JSON emitter branch, while restore mutation and restore-count recording are split between the emitter and command dispatch.
- Snapshot capture itself needed transactional cleanup so a failure before the prepared manifest could not leave a partial generation.
- Snapshot failure finalization must cover interruption as well as ordinary exceptions because interruption can occur after lazy capture and before mutation returns.
- A `None` snapshot is a valid completed capture outcome, so the runner tracks capture attempt state separately from the optional snapshot value.
- The first complete-suite run inherited `NO_COLOR=1`, which correctly disabled ANSI output and conflicted with three explicit color assertions. Running validation without that ambient override exposed and then verified the independent planning-elevation lease cleanup fix.
- Standards review identified duplicate restore orchestration. Reducing snapshot mutation to one action primitive leaves filtering, ordering, fail-fast status, result assembly, and metadata exclusively in the runner.

## Outcomes & Retrospective

Push, pull, and restore now cross one typed operation-runner boundary. The runner owns sudo lifetime, sync session execution, lazy snapshot transactions, restore action ordering, failure cleanup, and final results. Human and JSON renderers consume typed events/results only; JSON ignores progress and emits one final document.

Lifecycle tests exercise event order, snapshot timing/finalization/interruption cleanup, single capture attempts, restore fail-fast behavior, and metadata without terminal capture. Renderer tests construct events/results without executing filesystem mutations. Focused suites passed throughout, compile validation and diff checks pass, and the final complete suite passes with `967 passed`.

Independent Standards and Spec reviews pass after the review/fix loop. The final design removes duplicated restore orchestration and keeps standalone planning elevation from retaining an unowned runtime lease.
