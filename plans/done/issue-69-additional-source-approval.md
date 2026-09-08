# Canonical Additional Source Approval

## Goal and intention

Implement GitHub #69 as the next executable file-Sync slice after #68.
A shared repository mutation is reviewed once, authorized independently, and
included in Proposal inputs exactly when its write is authorized.

## Scope and constraints

Follow #56 sections 4 and 7 and root CONTEXT.md. Preserve exclusive Primary
Sources, frozen Observation/execution ordering, transactional Editor behavior,
and destination-stage pull hooks. No directory, broader Deck redesign, or Pull
orchestration work. Remove the review-only Additional limitation rather than
retaining a compatibility path. Existing ADRs remain applicable without changes.

## Work plan

1. Add observable Session and CLI regression tests for canonical identity and
   references, independent Approval, approval-aligned previews, batch alignment,
   disappeared/reappearing paths, and independent execution outcomes.
2. Implement the canonical session state and typed public command/view contract;
   integrate Source Change Review, selection, confirmation and JSON.
3. Apply selected Additional changes once before Primary changes, without
   attributing their results to dependent Proposal completion.
4. Update current user and architecture docs, run targeted validation, review
   the diff, and commit the complete checkpoint.

## Validation

Use real temporary repositories, source files and providers through public
Session/CLI seams. Run affected Sync Session, Editor, Deck and CLI tests, then
lint and diff checks. Test both successful dependency rematerialization and
failure isolation. Preserve frozen execution and Editor regressions.

## Progress

- Read #69, #56 approval/execution requirements, CONTEXT.md and all four ADRs.
- Located obsolete review-only documentation in docs/sync.md, docs/cli.md and
  docs/code-structure.md.
- Implemented canonical Additional rows/reviews, independent Approval, atomic
  batch alignment, frozen authorized projection inputs and once-only Apply.
- Added 12 public Session regression tests and dedicated Additional CLI/UI
  coverage; updated Editor UI expectations for canonical rows.
- Updated docs/sync.md, docs/cli.md and docs/code-structure.md to the executable
  contract, removing the obsolete review-only limitation.
- Coordinator validation: 325 affected engine/CLI tests passed in 64.74s;
  git diff --check passed.

## Decisions and discoveries

Additional-only work activates no directional hook by itself. Additional
Approval is retained by path even while no changed row exists. Batch selection
must finish assigning states before materialization begins.

## Outcomes

Implemented and targeted validation passed. Exact final validation:

```sh
uv run pytest -q tests/engine/test_sync_additional.py tests/engine/test_sync_session.py tests/engine/test_sync_editor.py tests/engine/test_sync_both_convergence.py tests/engine/test_sync_pull_convergence.py tests/engine/test_sync_convergence.py tests/engine/test_sync_endpoint_convergence.py tests/engine/test_sync_auxiliary.py tests/engine/test_sync_auxiliary_execution.py tests/engine/test_sync_repository_apply.py tests/engine/test_sync_publication.py tests/cli/test_sync*.py
git diff --check
```

All 325 tests passed. Full unrelated suites were not run. Parent review follows
this implementation checkpoint. Additional failure is reported independently;
the operation-wide fail-fast rule still skips all later effects rather than
rerendering or treating a source result as a Proposal completion prerequisite.
No known scoped implementation gaps.
