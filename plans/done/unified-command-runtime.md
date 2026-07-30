# Unified command runtime

## Goal

Implement GitHub issue #20 by routing every process Dotman launches through one internal command runtime while preserving each caller's domain-specific exit-status interpretation.

## Intention

Separate command policy from process mechanics. Callers construct typed requests and interpret typed results; the production adapter alone creates processes, merges the ambient environment, applies elevation, chooses pipe or terminal I/O, streams output, and normalizes interruption. A deterministic in-memory adapter records requests and supplies queued results for behavior tests without patching process globals.

## Scope & Constraints

- Preserve observable behavior at the existing public seams: planning APIs for guards, probes, and projections; execution APIs and CLI output for normal commands; CLI/editor/review and privileged-operation behavior where those paths launch processes.
- Preserve configured shell-command semantics through `/bin/sh`; represent direct helper/editor invocations as argument vectors without shell interpretation.
- Pipe requests own stdin/stdout/stderr, may provide byte input, capture byte output, and optionally stream decoded text to explicit sinks. TTY requests inherit the terminal and return no captured output.
- Request environment is an overlay on the ambient environment with explicit exclusions. Callers do not merge `os.environ` themselves.
- Elevation modes remain `none`, `root`, `lease`, `broker`, and `intercept`; the production adapter owns their preparation.
- The runtime normalizes SIGINT termination to exit status `130`; callers continue deciding whether that becomes interruption, a guard/probe outcome, or another domain error.
- No compatibility adapter or retained direct-launch implementation.

## Command-runtime contract

- `CommandRequest` carries a typed command (`ShellCommand` or `ArgvCommand`), working directory, environment overlay/exclusions, I/O mode, optional pipe input, output-streaming policy/sinks, and elevation mode.
- `CommandResult` carries normalized exit status plus captured stdout/stderr bytes and exposes strict UTF-8 text decoding for text-command consumers.
- `CommandRuntime.run(request)` is the only launch seam.
- `ProductionCommandRuntime` performs environment construction, elevation preparation, process creation, pipe pumping or terminal passthrough, terminal preservation, and signal cleanup/normalization.
- `MemoryCommandRuntime` records immutable requests and consumes deterministic queued results; exhaustion fails fast.
- Runtime selection is explicit dependency injection at planning/execution helper boundaries, with one production default at application composition edges.

## Work Plan

1. Add focused runtime contract tests, then implement the request/result types and deterministic in-memory adapter.
2. Add production-adapter integration tests for argv and shell execution, environment overlays/exclusions, pipe input/capture/streaming, TTY passthrough, elevation preparation, and interruption; implement the production adapter.
3. Migrate normal execution and planning guards to injected runtime requests, preserving caller-owned exit semantics.
4. Migrate probes and render/capture command projections to the same runtime, preserving public planning results and projection bytes.
5. Migrate remaining editor, review/pager, reconcile, add, ignore, and privileged-helper launches; remove all direct subprocess creation outside the production adapter.
6. Update mapped pseudocode and internal structure/domain documentation where the new runtime boundary is worth review.
7. Run focused tests and compile checks throughout, then the complete suite once.
8. Review the final diff independently on Standards and Spec axes, resolve findings, move this plan to `plans/done/`, and commit with a semantic message closing #20.

## Validation

- Focused command-runtime unit and production integration tests.
- Existing focused planning-guard, probe, projection, execution, terminal, review, and privileged-operation tests.
- `uv run python -m compileall -q src tests`
- `uv run pytest -q`
- Three-dot Standards and Spec review against the pre-implementation commit.

## Progress

- [x] Issue #20, repository instructions, domain context, prior command/elevation plans, and current launch sites inspected.
- [x] Test seams confirmed by the issue's acceptance criteria.
- [x] Runtime contract and migration sequence recorded before implementation.
- [x] Runtime contract and adapters implemented test-first.
- [x] Normal execution, guards, probes, projections, editors, review commands, and privileged helpers migrated.
- [x] Direct process creation removed outside the production adapter.
- [x] Runtime boundary documented in domain and code-structure docs.
- [x] Compile validation and complete suite pass (`960 passed`).
- [x] Full validation and independent review complete.
- [x] Commit created.

## Decisions

- The runtime owns process mechanics, not domain interpretation. Exit `100`, for example, remains meaningful only to guards/probes.
- Requests distinguish configured shell source from direct argument vectors so callers cannot accidentally change quoting semantics.
- Captured output is bytes at the runtime boundary; text consumers decode explicitly and privileged/file helpers retain byte fidelity.

## Surprises & Discoveries

- The repository has no current mapped-pseudocode artifact for command execution; the durable boundary is recorded in `CONTEXT.md` and `docs/code-structure.md` instead.
- Runtime selection stored in a `ContextVar` does not cross ordinary thread boundaries. Long-lived sudo and elevation-broker threads must capture or propagate the session context when they are created.
- The generated sudo shim must use Dotman's active Python interpreter. An ambient `python3` cannot import Dotman after an isolated `uv tool install`.

## Outcomes & Retrospective

The migration now has one typed request/result boundary for every production command launch. Execution, planning, projections, interactive tools, privileged helpers, and the generated sudo shim all route process creation through `ProductionCommandRuntime`; a source scan leaves `subprocess.Popen` only in that adapter.

The memory adapter replaced process-global monkeypatching at the public planning and execution seams. Focused adapter tests cover shell and argv commands, environment policy, pipes, TTY passthrough, streaming, elevation, and interruption. The final complete suite passes with `960 passed`.

Independent review exposed two thread-context leaks and interruption paths that had been translated into ordinary errors. Capturing the sudo lease runtime, propagating the elevation-broker context, and sharing normalized-interruption handling preserve the one-runtime session contract across those boundaries.
