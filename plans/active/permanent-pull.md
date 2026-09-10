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
