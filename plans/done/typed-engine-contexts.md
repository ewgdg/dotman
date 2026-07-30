# Typed engine contexts

## Goal

Implement GitHub issue #21 by replacing reciprocal callbacks between `DotmanEngine` and its tracked-state, tracked-package, planning, and projection modules with explicit typed internal contexts. `DotmanEngine` remains the public facade; internal implementation modules receive only their actual dependencies and call one another directly.

## Intention

The engine should compose repositories, configuration, tracked-state persistence, and the command runtime once. Internal flows should depend on small immutable context values instead of accepting an untyped engine object, looking helper modules up through the facade, or calling forwarding-only private facade methods.

## Scope & Constraints

- Preserve public `DotmanEngine` behavior, CLI behavior, persisted tracked-state format, planning results, projection semantics, and command-runtime behavior.
- Test at the issue-approved seams: public engine methods and stable domain models. Remove tests that patch private planning construction seams.
- Do not retain compatibility forwarding methods or legacy helper re-exports.
- Keep contexts data-oriented. Shared repository/package resolution belongs in direct module functions rather than recreating an internal engine facade.
- Use the existing `CommandRuntime` injection; projections must receive it explicitly rather than selecting it through process-global context.
- Fixed point for final review: `09e6b8f`.

## Green Checkpoints

1. Introduce typed repository and tracked-state contexts; migrate tracked-state persistence/resolution and tracked-package inspection to direct module calls; run focused tracked-state and info tests.
2. Introduce typed planning/projection inputs; migrate selector resolution, ownership, plan construction, guards, probes, and command projections; run focused engine planning, projection, and progress tests.
3. Reduce `DotmanEngine` to public orchestration plus substantive facade behavior; remove forwarding-only private methods, untyped engine parameters, helper lookup methods, and private-seam tests; run compile validation and all focused engine/CLI tests.
4. Run the complete suite once, perform independent Standards and Spec reviews against `09e6b8f`, resolve findings, move this plan to `plans/done/`, and commit with a semantic message closing #21.

## Validation

- Focused tracked-state coverage: `uv run pytest -q tests/engine/test_bindings.py tests/cli/test_track.py tests/cli/test_untrack.py`.
- Focused planning/projection coverage selected by changed behavior, including planning guards, projections, command-runtime injection, and progress.
- `uv run python -m compileall -q src tests` after each migration checkpoint.
- Source scans confirm no `engine: Any`, `engine._...` callbacks, helper-module lookup methods, or forwarding-only private engine methods remain in migrated modules.
- `uv run pytest -q` once after focused validation is green.

## Progress

- [x] Issue #21, blocker #20, repository instructions, domain vocabulary, current facade/helper cycles, and approved test seams inspected.
- [x] Migration sequence recorded before implementation.
- [x] Tracked-state checkpoint green (`51 passed`).
- [x] Planning/projection checkpoint green (`51 passed`).
- [x] Facade cleanup and focused validation green (`465 passed`).
- [x] Complete suite green (`956 passed`).
- [x] Independent Standards and Spec reviews green.
- [x] Plan finalized for review and commit.

## Decisions

- The issue itself pre-agrees the test seams: observable engine behavior and stable domain contracts.
- This is a behavior-preserving architecture migration. Existing public tests are characterization coverage; new red/green tests will be added only where a public behavior gap is discovered, not to assert private wiring.

## Surprises & Discoveries

- The current engine contains dozens of forwarding-only methods for tracked-state and planning modules, while those modules call back into the same private methods through untyped `Any` parameters.
- Projection already owns most implementation logic, but it still receives the whole engine for two symlink modes and command environment construction, and command execution is selected indirectly through the runtime session.
- Privileged file helpers and the elevation broker still depend on the active runtime. Planning passes the runtime directly to guards/probes/projections and binds that same explicit runtime around the operation so protected file access cannot silently fall back to a different adapter.

## Outcomes & Retrospective

`DotmanEngine` now composes three typed internal contexts and remains the public API facade. Tracked-state, tracked-package, planning, guard, and projection code call direct module functions with only their required configuration, repositories, persistence state, and command runtime. Shared selector and dependency resolution moved to `package_resolution.py`, eliminating the previous facade callback cycle and forwarding-only private methods.

Focused migration suites passed at each checkpoint, Pyright reports no errors in the changed core modules, compile validation passes, and the complete suite passes with `956 passed`. The first complete-suite invocation inherited `NO_COLOR=1` from the agent environment and correctly suppressed ANSI output, conflicting with three tests that explicitly assert colored TTY errors; rerunning with that ambient override removed passed all tests without a source change.

Independent Standards and Spec reviews found four issues: divergent mutable repository state, tracked-package detail assembly in the persistence module, a progress-ordering test gap, and one remaining helper re-export. The final design uses the tracked-state context's immutable repository mapping as the single source, keeps tracked-package detail assembly in `tracked_packages.py`, verifies projection occurs before progress advances through the public engine seam, and imports collision types directly. Both reviewers confirmed their findings resolved with no regressions.
