# Home Path Rewrite

## Goal

Implement GitHub issue #17: a repository-independent `dotman rewrite home expand|collapse [INPUT|-]` command that performs reversible, lexical home-path rewriting while preserving all unmatched UTF-8 bytes.

## Intention

Keep the pure rewrite rules under `src/dotman/rewrites/` and isolate strict byte input/output in the rewrite CLI adapter. Exercise behavior through the pure functions and the public root CLI.

## Scope & Constraints

- `$HOME` is the only home source and must normalize to a non-root absolute POSIX path.
- Rewriting is lexical; it does not inspect or normalize filesystem paths.
- Input is buffered and strictly decoded before any stdout write.
- No output operand, in-place mode, override, framework, registry, or public Python API.
- `rewrite` remains structure-agnostic; `transform` remains structure-aware.

## Work Plan

1. Add pure-behavior tests and the minimal home rewrite implementation in vertical red/green slices.
2. Add public root CLI tests and minimal parser, dispatch, strict byte I/O, and concise error integration.
3. Add help/style, standalone-dispatch, fidelity, boundary, and failure-atomicity coverage.
4. Update user-facing CLI and domain documentation.
5. Run focused tests regularly, the full suite once, then perform the required two-axis code review.
6. Resolve review findings, move this plan to `plans/done/`, and commit the completed work.

## Validation

- Focused rewrite behavior tests.
- Focused public root CLI tests.
- Project type/static checks if configured.
- Full `pytest` suite once implementation is complete.
- Standards and issue-spec review against the pre-implementation fixed point.

## Progress

- [x] Issue, domain vocabulary, ADR, and test seams inspected.
- [x] Pure rewrite behavior implemented test-first.
- [x] Root CLI behavior implemented test-first.
- [x] Documentation updated.
- [x] Full validation passed.
- [x] Two-axis review completed and findings resolved.
- [x] Work committed.

## Decisions

- Tests use the seams explicitly required by issue #17: observable pure behavior and the public root CLI.

## Outcomes & Retrospective

The standalone Home Path Rewrite ships behind the public root CLI with focused pure behavior and byte-I/O modules. Review caught two substantive edge cases—unreadable stdin and Unicode combining-mark attachments—and the corrected implementation passes the full suite. Keeping original bytes on no-op input makes the byte-fidelity contract explicit.
