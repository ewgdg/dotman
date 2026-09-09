# Complete standing Approval and the Command Deck (#70)

## Goal and intention

Finish one reviewable workset without changing the already-established frozen
Observation, Additional Source authorization, or destination-stage execution.
Users must see every available work kind, select it using its own semantics,
and execute only the exact valid materialized outcomes they reviewed.

## Scope and constraints

Read GitHub #70 and parent #56 sections 1–4, 5 (root boundary), and 7; root
CONTEXT.md and ADR 0001 establish the vocabulary and Guard contract.
Starting HEAD: a0bf2a9. Existing #67–69 implementation already supplies auxiliary
work, transactional editing, canonical Additional rows and final-state batch
Approval. Preserve candidate-only cache validity, failure diagnostics on
unapproval, destination-stage Editor hooks, frozen order and command arguments.

#74 explicitly owns production and execution of directory root mode drift;
#72–73 own directory discovery and child convergence. This change supports the
semantic Directory Root row in UI/serialization, not fabricated production.
No migration or compatibility path is needed.

## Work plan

1. Inspect Session, adapter, deck, styles and their existing tests.
2. Add public-contract coverage for mixed Selection, order-independent batch
   outcomes and frozen execution; fix only demonstrated gaps.
3. Complete focused review and confirmation, auxiliary rendering and styles,
   with targeted CLI/UI regression tests (delegated bounded UI file ownership).
4. Update durable CLI/lifecycle docs; run relevant engine and CLI tests; commit
   task-owned changes at meaningful boundaries.

## Validation

Use `uv run pytest` on affected engine and CLI test modules, with individual
UI waits bounded. Avoid the unrelated full suite. Tests observe public session
commands/views, adapter output and user interactions rather than private plans.

## Progress

- Inspected existing implementation and parent contracts. Confirmed #74 root
  production boundary from its acceptance criteria.
- Existing batch implementation establishes Additional and Proposal states
  before materialization and preserves failure diagnostics on unapproval.
- Added public Session tests for both Proposal orders, mixed workset selection,
  excluded Directly InSync rows, rejected auxiliary Approval and frozen execution.
- Completed semantic root presentation, policy/Pull View/Base review evidence,
  and static single-choice Resolution; regression tests passed.
- Combined engine/CLI validation: 162 passed in 35.42s. Renamed the engine
  contract module to avoid pytest basename collision with the CLI module.
- Added an explicit real-confirmation gate for approved participating Proposals:
  missing materialization or unresolved diagnostics cannot be confirmed.
- Final combined validation: 163 passed in 34.99s; `git diff --check` clean.

## Decisions and discoveries

Directory Root Work is Auxiliary inclusion, never Proposal Approval. Its UI
support must not pretend file-session planners can discover directory roots.

## Outcomes

Completed. Commits: cbeec37 (Session contracts, semantic Auxiliary kind and docs),
90bad71 (review/auxiliary UI), 2d92770 (test collection), c95ed42 (confirmation gate).

Final command:

```sh
uv run pytest -q tests/engine/test_sync_selection_contract.py tests/engine/test_sync_additional.py tests/engine/test_sync_auxiliary.py tests/engine/test_sync_session.py tests/cli/test_sync_deck_contract.py tests/cli/test_sync_deck_textual.py tests/cli/test_sync_deck_command.py tests/cli/test_sync_additional_ui.py tests/cli/test_sync_auxiliary_ui.py tests/cli/test_sync_pull_ui.py tests/cli/test_sync_editor_ui.py
```

163 passed in 34.99s. No unrelated full suite run. Root mode drift production
and structural execution remain intentionally owned by #74; current file
sessions do not manufacture root work. Independent parent review follows.

Existing deep Session behavior largely satisfied #70. The demonstrated gaps
were presentation of frozen evidence, single-choice Resolution interaction,
semantic root rendering/serialization, and an explicit confirmation validity
check. No extra materialization or new orchestration layer was introduced.
