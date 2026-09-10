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
- Completed CLI/global unattended cutover, executable preview checks, safe structured diagnostics, and current documentation.
- Completed boolean Git-ignore enablement and repository-to-target ignore chains across shared directory operations.
- Independent review found interrupted-hook JSON leakage and overbroad Guard documentation; both fixed and independently rechecked.
- Full acceptance: `uv run pytest -q` reports **1891 passed** in 76.66 seconds. `git diff --check` passes.

## Decisions

- The parent contract is authoritative; JSON changes output only, never consent.
- Documentation remains unversioned and responsibility-specific.

## Outcomes and retrospective

Implementation and documentation are complete. The first full run found two outdated expectations (implicit Push consent and captured projection stderr); updated those to the current contract and reran the complete suite successfully.

An unisolated delegated red test overwrote the user's `~/.gitconfig` and used host snapshot storage. Added suite-wide HOME and XDG data isolation, alongside existing config/state isolation. The old global configuration has not been reconstructed: an existing dated backup is available, but restoration awaits user confirmation. Several task commits inherited the example identity before discovery; subsequent commits explicitly use the pre-incident identity. Do not silently restore a potentially stale backup or guess user configuration.

Implementation commits: `43332e4`, `c10649f`, `4172417`, `ebfd65c`, `bcfa34a`, `065890f`, `432f4e8`, `d32aa5a`, `3e7842e`, `40daeb3`, `b78a244`.

Documentation commits: `27913c2`, `d23a371`, `0756fe8`. Test isolation/acceptance commits: `fa884f5`, `fa38743`, `c058c39`.
