# Transactional Sync Proposal Editor (#68)

## Goal and intention

Make deliberate repository editing available for every drifted file Sync Unit
with a surviving convergence route, without writing tracked sources before
execution. Preserve trusted frozen previews and standing Approval.

## Scope and constraints

Implements GitHub issue #68, following parent #56 sections 3–4 and the Editor
acknowledgment rule in section 6. Root CONTEXT vocabulary and ADR 0001 directional
Guard constraints apply; ADRs 0002–0003 retain projection/provider boundaries.
Do not widen automatic policy flow. No automatic Editor launch. Directory
census, canonical shared Additional Approval deck controls (#69), and other
sibling capabilities are not part of this change.

Reuse existing projection workspaces, Editor configuration and Command Runtime.
Configured providers are trusted programs, not a security sandbox.

## Work plan

1. Add executable public SyncSession tests before implementing Editor dispatch,
   typed outcomes, transactional Primary/permitted Additional staging, generation,
   cancellation, rematerialization and policy constraints.
2. Add public CLI/Command Deck tests before wiring explicit E Editor action,
   terminal handoff, Edited rendering, diagnostic notices and review details.
3. Update durable lifecycle/CLI docs and style; run targeted affected suites and
   review the complete diff. Commit all task-owned changes at meaningful boundaries.

The core implementation/tests are delegated to editor-core; this agent owns
CLI/UI, documentation, this plan, integration validation and commits. No file
overlap is intended. Parent independently reviews the completed diff.

## Validation

Agreed observable seams: SyncSession commands/immutable views and actual
repository/live bytes; CLI output and Textual input/rendering. Tests must show
one-sided and failed-materialization editing, no pre-execution tracked writes,
policy-safe effects, saved/identical/cancelled outcomes, typed recoverable errors,
and local Approval behavior. UI tests use a five-second interaction limit.

## Progress

- Read #68 (no comments), precise parent Editor/Approval/policy/Base sections,
  CONTEXT and applicable ADRs. Consulted find-skills and TDD guidance.
- CLI red/green slices: shared Edited style and repository/live Edited review
  now pass; explicit E action test first failed on absent help/action. Added
  unattended no-Editor regression and real-PTY local cancellation coverage.
- Consulted current Textual App.suspend documentation and installed source for
  terminal ownership/restoration semantics.
- Established clean baseline at #67 completion; implementation split by file
  ownership between session core and CLI adapter/UI.

## Decisions and discoveries

- Parent confirmed the #68/#69 boundary: retain permitted Additional candidate
  changes session-locally and show truthful review metadata. Do not implement
  canonical rows, Additional Approval controls or execution from #69. Unapproved
  candidates never enter Proposal projection or execution; use frozen preimages.
- The existing deck already serializes materialization; explicit Editor work must
  preserve that admission boundary and hand terminal ownership to TTY providers.
- Existing documentation incorrectly still lists auxiliary work as unsupported;
  adjust this boundary sentence while adding the newly supported Editor.

## Outcomes

Implemented transactional file Proposal editing and explicit E action. Saved edits
receive monotonic generations; byte-identical saves retain frozen materialization.
Local cancellation retains prior Proposal/Approval and restores terminal ownership.
Failed saves clear only the affected Approval. Retried Render uses saved Primary
bytes, while unapproved Additional candidates remain review-only.

Integration regressions found and fixed: Command Runtime cancellation inherits
OSError and must not become Editor command failure; completed UI tasks must release
admission before the callback runs; help text must fit the existing 80-column layout
to preserve page navigation. Public provider/marker tests replace private mocks.

Validation: 237 passed in 60 seconds with:

```sh
uv run pytest -q \
  tests/engine/test_sync_session.py tests/engine/test_sync_editor.py \
  tests/engine/test_sync_both_convergence.py tests/engine/test_sync_pull_convergence.py \
  tests/engine/test_sync_endpoint_convergence.py tests/cli/test_sync_editor_ui.py \
  tests/cli/test_sync_deck_textual.py tests/cli/test_sync_interrupt.py \
  tests/cli/test_sync_deck_command.py tests/cli/test_sync_pull_ui.py \
  tests/cli/test_sync_auxiliary_ui.py tests/cli/test_sync_command_runner.py \
  tests/test_reconcile.py tests/cli/test_reconcile_editor.py \
  tests/cli/test_reconcile_jinja.py tests/engine/test_editor_execution.py
```

Includes real PTY canonical-mode handoff, SIGINT cancellation and restoration.
Full project suite intentionally not run; the targeted suites cover changed seams.
Additional Approval/execution remains #69; directory-child Sync remains out of scope.


## Independent review corrections

- P1: frozen Repository Apply metadata must retain destination-stage pull hooks
  for deliberate Editor Primary writes, independently of automatic pull capability.
  Auxiliary planning still uses Guard-filtered metadata. Execution activates hooks
  only for actual Primary writes or admitted auxiliary work; Guards never rerun.
  Added a real-provider matrix across all policies, blocked pull flow, ordinary
  execution and byte-identical editing. Red: three missing-hook failures. Green:
  114 Session, Editor, Repository Apply and auxiliary tests.
- P2: the shared Editor shell path now supplies staged Primary then Additional
  copies as actual shell positional arguments, not appended shell source. Real
  shell regressions for $1 and ordered "$@" failed before the fix. Shared Editor,
  reconcile, edit and Sync UI callers: 61 tests passed.
