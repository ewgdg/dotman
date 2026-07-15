# Restore Command Canonical Rename

## Goal
Replace the public and internal `rollback` concept with canonical `restore`, preserving snapshot-to-live semantics.

## Scope & Constraints
- Top-level `dotman restore [<snapshot>]`; omitted snapshot selects latest.
- No rollback alias or migration behavior.
- Rename source, pseudocode, tests, JSON/human contracts, and current docs.
- Preserve action values and filesystem/diff direction.
- Do not edit historical completed plans or commit.

## Work Plan
1. Establish a failing public CLI contract test.
2. Update pseudocode before each mapped source change, then implement parser/dispatch and canonical symbols in bounded slices.
3. Update behavior tests and documentation.
4. Run focused and full validation plus command and residue checks.

## Validation
Use the exact commands requested in the task, plus focused red/green tests.

## Progress
- [x] Reconnaissance and constraints reviewed.
- [x] Public CLI contract red/green.
- [x] Canonical source and pseudocode rename.
- [x] Tests and docs updated.
- [x] Full validation complete.

## Decisions
- This is an intentionally breaking clean rename, including JSON `operation` values.

## Outcomes & Retrospective
- Canonical restore naming now spans parser, dispatch, snapshot models/actions, review, emitters, output, tests, pseudocode, and current docs.
- Omitted snapshot behavior remains deterministic through the existing latest-restorable resolver.
- Focused and full suites pass; restore help succeeds; the removed command is rejected; residue scan is clean.
- Regression coverage explicitly verifies parser status 2 for the removed command and canonical JSON operation values for restore dry-run and execution.
- Current documentation consistently describes the operation with canonical restore vocabulary.
