# Symmetric directory Sync Observation

## Goal and intention
Expose independently observed canonical directory children without aggregate payloads.
Freeze a single symmetric, control-aware census, then resolve child interpretation
and directional Guards before observing selected units.

## Scope and constraints
Issue #72 and the Observation/identity/control contract in #56. Reuse existing
ignore matching, Path Rule composition, scope resolution, projection and Guard
machinery. Child convergence/Base acknowledgment and structural publication are
separate work (#73/#74); successful Observation must not imply those capabilities.
Repository directory symlinks are never traversed. Live census honors fail/follow.
Implementation, regression validation and independent review are complete.

## Work plan
1. Public engine scope + SyncSession acceptance tests, one failing slice at a time.
2. Add symmetric census with independent typed path diagnostics and selection.
3. Resolve child inputs and Guards once; freeze independent Observation.
4. Gate unavailable child convergence, update UI and durable docs.
5. Focused validation and read-only review handoff.

## Validation
Use `uv run pytest` on directory acceptance tests, related Sync/ignore/Guard tests,
and affected CLI contracts. Exercise real filesystem nodes and real commands.

## Decisions
The parent confirmed the public scope/session test seam and capability boundary:
child inclusion/exclusion is supported; Proposal/Editor/Approval/execution and child
Base acknowledgment are unavailable. Keep capability diagnostics separate from
Observation failures; never send child work through the file-target executor.

## Progress
- Read #72, normative #56, #73–#75, domain docs and Guard ADR; inspected existing
  ignore traversal, Path Rule composition, scope resolver and session adapters.
- Implemented symmetric census and per-child inputs/Observation, with independent
  diagnostics and partial selection. Public acceptance tests caught and fixed
  repository-link ancestor reads, lost ancestor failures under exact selection,
  nested Git-control precedence, and directory-only patterns hiding regular files.
- Added separate capability diagnostics and canonical child UI/JSON rendering;
  inclusion is supported without Approval or file-target execution. Existing Probe
  metadata is retained alongside expanded child inputs.
- Updated lifecycle, CLI, repository, code-structure and Guard ADR documentation.

## Validation results
- Public scope/session + CLI acceptance and related session/scope/auxiliary/deck
  contracts: 106 passed (5.70s), before the final two census regressions were added.
- Related file convergence, selection, failure honesty, auxiliary execution, ignore,
  Path Rule Guard and Textual/CLI contracts: 190 passed (27.06s).
- Final directory engine acceptance: 12 passed (0.61s).
- Final focused public acceptance + session/scope/auxiliary/deck rerun: 108 passed
  (5.41s). `git diff --check` clean. Parent independent review pending.

## Review focus and limitations
- Child convergence and Base work remain gated, as agreed. No aggregate payload,
  Base, root mode effect or topology publication was added.
- Census access errors are typed local failures; it does not introduce an elevated
  recursive census protocol. File content reads reuse the established access seam.
- Review combined control semantics, directory-link confinement during Observation,
  child rule direction overrides, and separation of capability/Observation failures.
- No task-owned changes have been committed; parent owns final review and commit.

## Independent review corrections
- Reproduced the review's live-directory-link and unknown-Git-control read defects
  with full-target and exact-child acceptance cases that reject payload reads.
  Replaced repository-link-only propagation with one ancestor-failure propagation
  over both discovered and explicitly selected identities before exclusions.
- Reproduced both-policy exact chmod drift; exact mode now participates whenever
  child push survives and composes with executable comparison. Pull-only exact
  permissions remain inactive. Added five policy/mode contract cases.
- Reproduced unrelated pull Guard activation for exact outgoing child selection
  under both push-only and both target defaults. Directory directional candidates
  now use exact selected child policies, while full targets retain potential rule
  directions. The tests also prove the full-target pull Guard remains active.
- Updated lifecycle, code-structure and Guard ADR descriptions for these contracts.
- Directory acceptance after all four fixes: 23 passed (1.41s).
- Focused directory/UI/session/scope/auxiliary/deck integration rerun: 119 passed
  (7.17s). Related ignore/Path Rule Guard/selection/file convergence coverage:
  92 passed (3.38s). `git diff --check` clean; no commits made.
- Final review regression: an exact selected child synthesized beneath a failed
  ancestor must still pass unified Dotman exclusions. The new public test first
  reproduced an excluded `*.secret` child reappearing as a failure row. Final
  census filtering now reapplies unified exclusions to every candidate, including
  synthesized diagnostics. Directory/CLI acceptance: 27 passed (1.70s);
  `git diff --check` clean. No commits made.

## Outcome
All independent review findings were corrected with reproducing tests. Parent
verified the final exclusion fix and reran all directory engine/CLI acceptance:
27 passed (1.69s), with a clean diff check. Child convergence remains explicitly
unavailable, preserving the boundary with the next implementation stage.
