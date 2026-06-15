# Probe Targets

## Goal

Add no-file probe targets that run a side-effect-free command during push/pull planning. Active probes appear in selection and make hooks eligible; inactive probes become normal noops and stay out of default selection.

## Scope & Constraints

- User config uses `probe = "..."`; no `type = "probe"` required.
- Existing `type` remains filesystem-only (`file` / `directory`).
- Probe targets must not define file payload fields such as `source`, `path`, transforms, chmod, or path rules.
- Probe command exits: `0` active, `100` inactive/noop, other nonzero hard planning error.
- Probe targets do not claim repo/live paths, do not collide, do not snapshot, and do not execute target file steps.
- Keep target/package/repo hook eligibility consistent with active vs noop target actions.

## Work Plan

1. Update pseudocode source artifacts for models, manifest, projection/planning, execution, CLI selection/output, and tracked metadata.
2. Add tests for manifest validation, planning active/inactive/error probes, selection rendering/filtering, and execution hook behavior.
3. Implement models/manifest parser fields.
4. Implement probe planning and path-claim skipping.
5. Implement execution/selection/output/review exclusions for probe target actions.
6. Update repository docs with schema and semantics.
7. Run targeted tests then full test suite if time permits.

## Validation

- `uv run pytest tests/engine/test_plans.py tests/engine/test_execution.py tests/cli/test_selection_ui.py tests/cli/test_execute.py`
- `uv run pytest`

## Progress

- Pseudocode artifacts updated for probe target behavior.
- Tests added for planning, tracked ownership, direct collision filtering, selection, execution, diff-review, snapshot, and info output behavior.
- Implementation added for probe parsing, planning, hook eligibility, selection/output rendering, and path-ownership skipping.
- Repository docs updated with probe target schema and example.

## Outcomes & Retrospective

- Probe targets use `probe = "..."` without `type = "probe"`.
- Active probes use action/kind `probe`; inactive probes are normal `noop` targets.
- Probe targets do not claim paths, so tracked ownership and direct target collision checks ignore them.
- Reviewer follow-up addressed: added tracked ownership/direct collision regression tests and moved new dataclass fields to append-only positions for positional-call compatibility.
- Full test suite passes: `uv run pytest` (604 tests).
