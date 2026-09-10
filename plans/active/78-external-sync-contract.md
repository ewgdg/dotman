# Finalize the external Sync contract

## Goal and intention

Complete issue #78 against the exact external contract in parent issue #56, so interactive, unattended, dry-run, human, and JSON modes describe and execute the same reviewed work.

## Scope and constraints

- Finalize CLI grammar, unattended decision safety, non-mutating previews, compact confirmation, final structured output, and exit meanings.
- Keep current lifecycle, CLI, configuration, contributor architecture, and indexes consistent; do not retain superseded contracts.
- Preserve existing convergence responsibilities and Command Runtime. Avoid unrelated changes.

## Work plan

1. Inspect parent requirements and existing implementation/tests.
2. Implement missing runtime contracts with regression tests (runtime agent owns source and tests).
3. Consolidate current documentation (documentation agent owns docs and indexes).
4. Independently review changes, run the complete acceptance suite, resolve failures, and commit at meaningful boundaries.

## Validation

Focused CLI/session regressions cover syntax, interactive availability, unattended defaults and blockers, dry-run safety, frozen confirmation, JSON fields/privacy/stdout, and exits. Run the full pytest suite once integrated changes are ready. Check diff hygiene and a clean worktree before handoff.

## Progress

- Read #78 and fetched the parent contract; worktree initially clean.
- Partitioned runtime/tests and documentation into disjoint work units.

## Decisions

- The parent contract is authoritative; JSON changes output only, never consent.
- Documentation remains unversioned and responsibility-specific.

## Outcomes and retrospective

Pending implementation and validation.
