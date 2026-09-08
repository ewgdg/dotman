# Directional Guards and auxiliary Sync work (#67)

## Goal and intention
Keep configured Sync Policy and Base eligibility stable while one-shot Guards narrow executable capabilities. Expose active Probe and retained noop hooks as directly selectable work, never file Proposals.

## Scope and constraints
Implement #67 on the existing file SyncSession and shared Path Rule Guard boundary. Directory census/child SyncSession support belongs to the parent issue's separate directory work. Reuse existing nested directional execution and Command Deck styling. No new sync hook family, Probe payload, Base or convergence receipt.

## Work plan
1. Add failing behavior tests for ordered directional Guards, Probe scope/activity/selection, invalid deletion policy, and retained hook-only work.
2. Resolve each directional capability once before observation; preserve static policy/Base facts. Prepare auxiliary work without file observation.
3. Extend frozen execution scopes and adapters for direct auxiliary inclusion, preserving nested hooks and no-write behavior.
4. Update durable documentation, run targeted regressions, review and commit task-owned changes.

## Validation
Use real manifests and shell logs at engine/session and execution-runner seams. Check preview executes only planning, no repeated Guards/Probe, no auxiliary convergence/Base/snapshots, canonical scope annotations, and UI/JSON selection behavior. Run affected guard, SyncSession, directional execution and deck tests.

## Progress
- Read #67 (no comments), precise parent #56 guard/auxiliary/Base/execution contracts, root CONTEXT and ADR 0001.
- Confirmed file Observation already preserves configured policy/Base eligibility and no-route diagnostics. Existing Sync scope excludes Probes; SyncSession rejects them and drops empty hook scopes. Directional hierarchy currently completes push before beginning pull.
- Delegated non-overlapping runner and adapter changes; session/planning integration remains here.

## Decisions
Preserve separate immutable AuxiliaryRow values, not fake Observations. Existing Path Rule evaluator remains the shared directory planning seam; do not implement parent directory census in this issue.


## Completed checkpoints
- Added failing tests at session, runner and adapter seams before implementation.
  Verified missing Probe scope/validation, scope-first directional ordering,
  missing auxiliary constructor/runner APIs, ancestor noop-hook retention and
  directional target-hook environment failures before fixing each behavior.
- Added one-shot scope-first directional eligibility with independently retained
  ancestor hook scopes; file configured policy/Base evidence remains unchanged.
- Added immutable Probe/hook rows, canonical scope selection, no-Proposal
  execution, noop retention/override, JSON/human/Table presentation and styles.
- Frozen directional runners now coalesce normal and noop hook scopes and exclude
  auxiliary metadata from snapshots. Target hook environments use their actual
  execution family's operation.
- Updated the Guard ADR and Sync, CLI, repository and code-structure documentation.

## Validation results
- 507 passed in 38.04s: all `tests/engine/test_sync*.py`, repository/package/target/
  Path Rule Guard tests, and Sync deck CLI/Textual/auxiliary adapter tests.
- After the final directional-environment regression/fix, 218 passed in 4.36s:
  auxiliary session and runner tests, both directional runner suites, manifest
  vocabulary, plans and execution suites.
- `uv run python -m compileall -q src/dotman` and `git diff --check` passed.
- No full repository suite run: validation was scoped to affected planning,
  session, execution, manifest and UI contracts.

## Outcomes and remaining scope
All #67 file-session/auxiliary acceptance behavior is covered. Shared Path Rule
Guard behavior is verified through its existing executable directory planning
boundary. Directory SyncSession census/children remain separate parent work;
this task does not add an unfinished directory session path.
Probe/hook work has no file Observation, Proposal, Approval, Base or Converged
result, and auxiliary-only execution creates no snapshot. Hard hook failures
remain operation/stage diagnostics rather than invented auxiliary convergence.
