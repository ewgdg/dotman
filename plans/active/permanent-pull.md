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
