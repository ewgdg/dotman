# Pull-only file Sync convergence

## Goal and intention
Implement #64 as the next working Sync layer: review and approve frozen live Capture, apply only repository outcomes, and acknowledge Base before claiming Converged.

## Scope and constraints
File targets configured pull-only, alongside existing push-only support. No both-policy resolution, directory children, editor or Additional Sources. Preserve policy comparison, opt-in Approval, frozen evidence, nested directional hooks and per-unit fail-fast completion. Read #64/#56, CONTEXT.md and ADRs 0001–0004.

## Work plan
1. Add failing session contract tests for lazy frozen Capture, no-write Approval, repository-only execution and Base failures.
2. Materialize Use live from frozen evidence and wire Repository Apply plus existing Base lifecycle.
3. Update Deck/CLI styles and durable docs; cover repository execution hooks/safety independently.
4. Run focused suites, inspect changes, commit meaningful boundaries.

## Validation
Focused session/convergence, repository/publication, CLI Deck and Base lifecycle suites. Verify no snapshots/live mutations, no execution recapture, committed ancestry rather than candidate bytes, and acknowledgment failure preserving old Base.

## Progress
- Planning checkpoint: existing Observation already uses configured comparisons; existing completion prematurely treats all no-publication outcomes as converged. Capture projection helper supplies private frozen endpoint files.
- Bounded delegation: repository-stage owns new execution module/tests; pull-sync-ui owns UI/docs/tests. Session/materialization/integration tests and plan remain here.

## Decisions
Reuse Base lifecycle completion and committed frozen facts, not a new acknowledgment path. Repository stage completes each unit before enclosing post hooks.
- RED checkpoint: six new end-to-end acceptance cases fail on missing Use live/effects/acknowledgment. Existing 22 push convergence tests still pass after adding lazy Capture materialization.
- Materialization checkpoint: Use live now freezes repository outcome from private frozen endpoint projections; configured Capture comparison is reused. Raw, custom, missing and patch Capture share existing projection/patch mechanics.
- UI/docs checkpoint: 771a71a updates Deck review, styled intent, JSON repository effects, README and lifecycle/CLI docs; 35 focused UI tests pass.
- Execution checkpoint: b4b2ebd adds repository-only frozen execution; 56fbc8c separates failed completion evidence from already-successful target writes. Session now executes Repository Apply before publication, acknowledges each eligible unit through frozen committed facts, and preserves earlier convergence/Base on later failure.
- Integration checkpoint: all 13 pull convergence tests pass, including custom/patch Capture, cached Capture comparisons, typed missing/empty, no-write acknowledgment, retry, stage ordering, Base replacement failure and post-hook failure. Existing push convergence and repository module tests pass.
- CLI integration checkpoint: 60f619e removes obsolete pull-only unsupported tests, adds actual mixed-policy execution/preview and reports committed acknowledgment without overwriting frozen availability evidence.
- Final validation: 238 passed across the focused session, push/pull convergence, repository/publication, Base lifecycle and CLI suites (12.30s); `git diff --check` clean. No full project suite was needed for this file-Sync scope.
- Additional regression: both no-write acknowledgment failure and acknowledgment interruption are typed and never Converged. An initial InterruptedError regression failed, then passed after preserving interruption rather than classifying it as ordinary Base I/O failure.

## Outcomes and retrospective
Delivered frozen pull-only Use live review/Approval, repository-only Apply, per-unit Base acknowledgment, fail-fast ordered partial results, styled Deck/CLI effects and current contributor/user documentation. Core commit: 3a5addd; bounded UI and repository-stage commits: 771a71a, f90a868, b4b2ebd, 60f619e, 56fbc8c.

No scope expansion into both-policy convergence, directories, Editors or Additional Sources. Configured Capture comparison necessarily runs at Observation; lazy materialization reuses that evidence, while other comparison configurations delay Capture until review/Approval. Command providers retain the established trusted side-effect-free contract and dependency access; frozen managed endpoints are supplied through private copies.
