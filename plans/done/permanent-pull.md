# Permanent Pull on shared frozen worksets

## Goal and constraints
Keep Pull fixed live-to-repository, with opt-out selection, frozen projections,
transactional editing, canonical Additional changes and fail-fast repository Apply.
No changed-unit Base reconciliation/acknowledgment, live publication or snapshots.
Push retains its independent one-sided contract.

## Work plan
1. Add session-level behavioral regressions before implementation.
2. Extract workflow-specific orchestration hooks from shared session mechanics;
   implement Pull observation, materialization and repository-only completion.
3. Route CLI/deck through the Pull session and remove obsolete Pull entry paths.
4. Validate focused regressions and affected Sync/Push coverage; update durable
   lifecycle, CLI, architecture and styles; commit task-owned changes.

## Validation
Real temporary Git repositories, files and Base storage at session/CLI seams.
Cover frozen raw/Capture/Missing, guards/hooks, Base boundaries, failures,
Additional input alignment and editing. Run affected suites after focused checks.

## Progress
- Read full issues, domain context, ADRs and plan guidance.
- Inspected existing Sync observation, workset and repository Apply boundaries.

- Added Pull session over shared ProposalSession; fixed direction, eager opt-out
  materialization, repository-only completion without Base acknowledgment.
- Routed CLI and deck; exact multi-scope parser regression failed first then passed.
- Covered raw/create/delete, guards, Base, Additional alignment and independent
  Apply, Patch Capture, explicit Editor recovery, and failure-honest post-hooks.
- Focused combined session/editor/CLI suite: 119 passed; later Pull coverage: 11 passed.
- Remaining: obsolete plan/execution cleanup, wider affected validation and final review.

- Textual validation initially found four page-navigation failures caused by
  wrapping the restored Sync help term. Shortened it to the existing "R intent".
  Targeted Pull plus all page-up/down cases now pass: 20 passed, 31 deselected.
- Moderator requested immediate partial handoff after current validation.
  Issue remains incomplete: obsolete Pull engine/planning/execution and tests
  have not been removed/ported, dedicated Pull directory/symlink regression
  coverage and full affected-suite validation remain. Keep plan active.

- Continuation authorized. Removed dead Pull dispatch from the push/restore runner and
  ported its Pull lock checks to the permanent deck boundary (including structured
  failure and release after observation failure). Both command-runner modules:
  10 passed. Engine/projection/execution cleanup and acceptance remain outstanding.


## Completion pass
- Removed the obsolete engine Pull planning facades and directional projection,
  execution, lazy review and selection branches. Shared metadata, Guards,
  projections and hook execution remain available to the session workflows.
- Added dedicated Pull directory coverage for independent opt-out child outcomes,
  frozen executable state, repository topology closure/order, and file/directory
  symlink policies. Added Capture-backed comparison reuse and lazy Capture reuse
  across repeated reviews, Approval toggles and Apply.
- Focused Pull session/directory validation: 21 passed.
- Push review/selection/execution rendering validation: 89 passed.
- Obsolete engine/CLI tests are being ported to current session/deck contracts;
  final affected validation and acceptance review remain pending.


- Completed engine ports: 934 engine tests passed; later selected cleanup: 75 passed.
- First full repository validation: 1,820 passed, 15 failed in 90.58s.
  Failures were stale CLI Pull selector/result/editor expectations, obsolete help
  syntax, and a UI fixture missing the now-required session operation.
  Porting those assertions/fixtures before final affected validation.

- CLI execution ports exposed a real auxiliary-selection bug: unattended
  `pull --run-noop` skipped its retained hooks because Pull never selected
  Auxiliary rows. Regression failed before the fix. Pull now starts eligible
  Probe/hook work selected alongside its opt-out Proposals, without batch
  reapproval that could retry failed materializations. Explicit exclusion still
  works; shared Sync opt-in behavior is unchanged.
- Pull session/CLI execution/deck plus shared auxiliary validation: 73 passed.

## Outcomes
- Permanent Pull now has one execution path: resolved tracked scope → frozen
  shared Observation/workset → repository-only Apply. Obsolete engine planning,
  Pull execution/fallback and plan-based lazy review paths/tests were removed
  or ported, while Push retains its independent behavior.
- Fixed raw, Capture, Patch Capture, Missing and transactional Editor outcomes,
  opt-out Proposal/Additional/auxiliary selection, frozen inputs, independent
  Additional approval, partial-failure honesty, pull-only Guards/hooks, no live
  snapshot, direct-agreement Base establishment and no changed-Pull Base
  reconciliation/acknowledgment are covered by executable tests.
- Added dedicated directory child/topology/symlink and Capture reuse coverage;
  ported engine, CLI, review and UI fixtures to the current public contracts.
- Durable lifecycle, CLI, vocabulary, style and architecture guidance describe
  the current shared-workset Pull behavior.
- Final full repository validation: `uv run pytest -q` — **1,833 passed** in
  89.68s. `uv run python -m compileall -q src/dotman` and
  `git diff --check` passed. No obsolete Pull facade/helper references remain
  in source, tests or domain docs.
- Completion acceptance met; no implementation or validation gaps remain.


## Provider workflow identity review
- Independent review found that frozen projections forced `DOTMAN_OPERATION=sync`
  and unified input selection leaked its first directional metadata into
  Probe/Editor environments. New regressions reproduced 11 failures before
  implementation (8 preserving cases passed).
- Sessions now supply their workflow identity once to unified provider metadata;
  frozen projections, directory children and staged Editors preserve it.
  Directional metadata remains separate for Guards and hooks. This also fixes
  Sync Probe/Editor environments without changing Push Render/Probe identity.
- Added 20 public-boundary cases covering file/child custom comparisons,
  Capture comparison reuse and lazy Capture, Render, transactional Editor under
  both/pull-only policy, Probe, and directional Guard/hook environments.
- `uv run pytest tests/engine/test_provider_workflow.py -q`: 20 passed.
- Affected validation:
  `uv run pytest -q tests/engine tests/cli/test_pull.py tests/cli/test_pull_deck_command.py tests/cli/test_sync_deck_command.py tests/cli/test_execute.py tests/cli/test_sync_base_inspection.py`
  — **1,067 passed** in 35.66s. The previously recorded full-suite run predates
  this review fix; all affected engine and CLI paths were revalidated.
