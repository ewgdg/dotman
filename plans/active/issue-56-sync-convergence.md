# Two-sided Sync convergence

## Goal

Implement GitHub issue #56 and its subissues #57–#78 as one dependency-ordered feature branch while keeping every checkpoint executable and reviewable.

## Intention

Add a one-shot `SyncSession` that observes selected repository and live state once, exposes policy-constrained proposals through immutable views and semantic commands, freezes approved work, and executes repository changes before live publication. Preserve explicit one-sided Push and Pull behavior while moving their shared mechanics behind focused modules.

## Scope and constraints

- The parent issue is the authoritative behavioral contract.
- Hard-cut configuration and command contracts; do not retain aliases or compatibility branches.
- Grow the feature through working vertical slices rather than horizontal scaffolding.
- Tests exercise public seams: resolved plans and emitted configuration for the initial hard cut, then `SyncSession` commands/views/results, CLI behavior, and real temporary Git/filesystem/SQLite resources.
- Keep the existing command runtime as the process-execution seam.
- Keep CLI and engine facades thin.
- Update executable tests and durable user documentation in the same checkpoint as behavior.

## Work plan

1. **Configuration foundations (#57–#59)**
   - Hard-cut Render, Capture, comparison, Editor, preset, Additional Source, policy, named Path Rule, exclusion, scope, and canonical identity contracts.
   - First tracer: manifest → resolved target/path rule → existing Push/Pull planning and structured output, with command-backed projection execution.
2. **Base foundations (#60–#61)**
   - Add the secure fixed-epoch SQLite object store and the policy-aware Base lifecycle module.
3. **File SyncSession tracer (#62–#66)**
   - Add frozen Observation and the one-shot session interface.
   - Converge push-only, pull-only, both/Merge, deletion-only, Missing, and no-write file outcomes.
4. **Capability, authoring, and approval (#67–#70)**
   - Add directional Guards and auxiliary work, transactional Proposal editing, canonical Additional Source approval, and the Command Deck adapter.
5. **Frozen execution and directories (#71–#75)**
   - Execute Repository Apply before Live Publication with fail-fast partial results.
   - Add child census, independent convergence, topology/root effects, and symlink modes.
6. **Operations and handoff (#76–#78)**
   - Add Base list/info/reset/doctor behavior.
   - Move permanent Pull onto shared convergence modules.
   - Finalize exact CLI, unattended, JSON, exits, documentation, and full regression coverage.

## Validation

- Run the smallest targeted test file after every red/green slice.
- Run affected engine and CLI suites at each issue checkpoint.
- Run the full suite before marking the plan done and before making the PR ready for review.
- Inspect `git diff --check`, generated CLI help, structured output, and durable documentation links.

## Progress

- [x] Created `feat/issue-56-sync-convergence` from the finalized domain-contract baseline.
- [x] Confirmed the parent-specified test seams and mapped the current implementation.
- [x] Complete #57 hard-cut projection and Editor configuration (1,053 tests passing; independent review accepted).
- [x] Complete #58 policy, named Path Rule, and unified exclusion hard cut (1,058 tests passing; independent review accepted).
- [ ] Complete #59–#78 in dependency order.
- [ ] Run final validation and open the PR.

## Decisions

- Use one branch and PR because the user explicitly requested the umbrella implementation together.
- Keep commits bounded by subissue/checkpoint even though the PR is aggregated.
- Use the public `SyncSession` seam required by #56 rather than exposing private plans or persistence handles.

## Surprises and discoveries

- The current code still exposes the superseded `reconcile` and `pull_view_*` configuration throughout models, planning, execution, and structured info output. #57 therefore requires a real vertical hard cut rather than an additive parser change.
- Existing tracking state and live snapshots are not suitable Sync Base storage; the Base object store must remain separate.

## Outcomes and retrospective

Pending.
