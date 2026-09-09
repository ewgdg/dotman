# Independent directory-child convergence

## Goal and intention
Implement #73 on the #72 census: each regular-file child owns a policy-constrained Proposal, Approval, frozen effects, result and conditional Base. Reuse file-session machinery without directory source bundles.

## Scope and constraints
Missing or Present(bytes, executable) is the child repository representation. Merge bytes and executable independently (agreement wins, otherwise the side changed from Base wins). Exact Path Rule chmod is live-only, never ancestry. Rename identities never transfer Approval or Base. Additional Sources remain canonical and cannot be another child's Primary Source.
Directory topology closure/root work and extended symlink authorization belong to subsequent issues; no speculative implementation of those surfaces.

## Work plan
1. Add observable child policy, executable Merge, frozen execution, independent rename and Additional tests; verify failures.
2. Generalize representation handling and freeze child stage metadata under one target hook scope.
3. Enable existing session commands and independent Base completion for children.
4. Update review rendering and durable docs; run targeted engine/CLI regressions and commit.

## Validation
Targeted pytest child tests, existing Sync convergence/editor/additional/failure tests, CLI directory tests; lint touched code. Tests use real Git and filesystem where ancestry/executable behavior matters.

## Progress
- Read #73, parent #56, prerequisites #65/#66/#69/#71/#72, CONTEXT and ADRs. Issues had no comments.
- Confirmed #72 deliberately blocks child Proposal commands and stage metadata only contains aggregate roots; Base storage already supports child executable payloads.

- Added test-first policy and executable/bytes Merge regressions; initial failure confirmed child Approval was rejected.
- Unified child Git/Base freezing with file observations, enabled shared session commands, and froze independent child stage paths under one target hook scope.
- Added child Missing/rename, Additional authorization, executable Editor, frozen stage/hook, and chmod-failure coverage. Editor needed opt-in executable preservation through its nested source-copy transaction.
- Removed the obsolete unsupported-child capability field and UI path. Child review uses Git executable mode lines; durable Sync/CLI/architecture docs now describe supported child convergence.

## Validation and outcomes
- Targeted Sync engine/publication/Editor/Additional/failure/child and directory UI run: **260 passed** (17.35s).
- Reconcile CLI/helpers, Sync CLI review/results and Base lifecycle run: **146 passed** (8.51s).
- Final child convergence + directory UI run after no-write/conflict tests and naming cleanup: **29 passed** (3.09s).
- Python compileall and git diff --check passed. No full unrelated project suite run.
- Implemented independent policy/intent/Approval/Source Change/publication/result and Base behavior, Missing and executable Merge, live-only chmod, canonical Additional input authorization, and independent rename identities.
- Remaining parent-epic work is topology closure/root-mode work and extended symlink authorization; this change does not implement those separately scoped features.

## Retrospective
The smallest reusable implementation was to expand child evidence before common Git/Base processing, then attach frozen child execution paths beneath retained root hook scopes. No directory Proposal or source bundle was introduced. Executable Editor changes required preserving the primary's Git mode through the existing inner Editor source-copy transaction.
