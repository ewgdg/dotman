# Retire the legacy push planning pipeline (#86)

## Goal and intention

Since #85 `dotman push` runs on the shared Command Deck (`PushSession`). The
legacy `OperationPlan` pipeline no longer backs any command but survives as a
test harness. Remove it so there is one planning path, and keep only tests that
protect behavior users can still reach.

## Scope and constraints

- Delete from `src/`: `engine.plan_push`, `plan_push_query`, `_plan_query`,
  `resolve_tracked_binding`; `planning.build_tracked_plans`,
  `build_package_plans`, `build_package_plan`, `build_operation_plan`,
  `finalize_repo_hook_plans` and their private helpers; `OperationPlan`;
  `sync_base_maintenance.py`; `push_checkpoint.checkpoints_for_target` and
  `TargetPlan.push_checkpoints`; anything else only these used
  (`projection.plan_targets` and its action helpers, `evaluate_hierarchical_guards`)
  as confirmed by `uvx vulture src/dotman --min-confidence 60`.
- Keep shared primitives the Sync/Pull/Push sessions use: `PlanningContext`,
  `collect_static_target_candidates`, `collect_tracked_ownership_candidates`,
  `plan_hooks`, `plan_repo_hooks`, `PackagePlanningInput`, preview overrides.
- `info tracked`, `push`, `pull`, `sync` behavior unchanged.
- Tests: re-home surviving behaviors onto public seams (`resolve_sync_scope`,
  `open_push_session` / `open_sync_session` / `open_pull_session`, CLI, manifest
  loading); delete tests whose behavior only the legacy pipeline had, recording why.
- Out of scope: pre-existing failure
  `tests/cli/test_sync_auxiliary_ui.py::test_auxiliary_selection_and_review`
  (stale confirmation wording from 1b00a8f).

## Triage answer (open question in the issue)

- Hook plan filtering (`run_noop`, executable targets) still exists in the Sync
  path via `models.filter_hook_plans_for_targets` / `finalize_hook_plans_for_targets`
  used by `sync_publication`; tests re-home there.
- Package-plan merging and `OperationPlan` repo-hook finalization are
  legacy-only; Sync scope resolution does its own selection dedup.

## Work plan

1. Migrate tests off the pipeline (parallel, disjoint file ownership).
2. Delete the pipeline and dead code; iterate with vulture.
3. Update `docs/code-structure.md` to one planning path.
4. Full suite, review, commit, comment on #86.

## Validation

- `rg 'plan_push|_plan_query|OperationPlan|build_package_plans' src tests` empty.
- `uvx vulture src/dotman --min-confidence 60` shows nothing new.
- Full suite passes except the pre-existing failure above.

## Progress

- Baseline: 1766 passed, 1 pre-existing failure (80s).
- Tests re-homed onto Push/Sync sessions, scope resolution and manifest loading
  (commit 17cb896). Per-test mapping lives in that commit's diff.
- Pipeline and its dead dependents deleted; reserved paths ported into
  `resolve_sync_scope`; docs updated. Final: 1728 passed, same 1 pre-existing failure.

## Deleted tests and reasons

- Plan-object shape (`to_dict`, `OperationPlan`, package-plan merging): legacy-only.
- `test_package_plans_refactor.py`: covered by `test_sync_scope.py` resolution tests.
- `list_directory_files` / privileged `list-directory-files` tests: helper only
  served the legacy scan; ignore semantics re-homed onto `IgnoreMatcher`, skip
  markers covered by `test_sync_directory_observation.py` / topology tests.
- `test_command_projection_runs_without_elevation_for_protected_inputs`: patched a
  hook only the deleted `run_command_projection` consulted.
- Remaining deletions name their covering Sync/Push/Pull test in 17cb896.

## Surprises & Discoveries

- Reserved paths were enforced only by the legacy pipeline, so Push lost the
  check in #85. Ported into `resolve_sync_scope`; it now covers Sync and Pull
  scopes too, since all three resolve the same tracked graph.
- Protected (root-only) Render/Capture inputs were staged to a readable file only
  by the legacy `run_command_projection`. Sync/Push never staged them. Follow-up.
- Intended Sync semantics that differ from the old planner: per-unit
  `observation-failed` instead of aborting on Render failures or broken source
  symlinks; guard-skipped directories are still scanned; guards do not run for
  packages without work.

## Outcomes & Retrospective

- One planning path remains. About 2,400 lines removed from `src/`.
- `engine.resolve_full_spec_selector_text` is now test-only. It is a small
  selector resolver, not pipeline code, so it stays unless someone asks to remove it.
