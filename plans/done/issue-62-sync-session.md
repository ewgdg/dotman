# One-shot file SyncSession

## Goal and intention
Open a process-local session over a resolved file scope, freeze Observation once,
and expose immutable evidence and semantic command results without private plans.

## Scope and constraints
Issue #62 and parent #56 define the public seam. The confirmed test seam is
`DotmanEngine.open_sync_session` / `SyncSession`, with real temporary Git,
filesystem and Base stores. Proposal materialization, Approval, Editor,
publication, directory census and CLI Sync are not part of this checkpoint.
Execute terminates with direct/failure/pending results, never claims Converged.
The existing push/pull command runner must share the new manager operation lock.

## Work plan
1. Test and implement frozen file Observation, ordering and policy comparison.
2. Test immutable views, typed open/dispatch failures, inclusion and terminal use.
3. Freeze Git/Base evidence and invoke real direct-agreement / policy maintenance.
4. Add operation-lock lifetime and minimal push/pull integration.
5. Update durable lifecycle/module documentation and validate.

## Validation
Run targeted session/runner tests after each red/green slice, then full pytest,
compileall and whitespace checks. Verify preview leaves managed files, Git and
state untouched; projections and Guards are not rerun by commands.

## Progress
- Read parent/spec, domain glossary, relevant ADRs, TDD skill and existing scope,
  projection, Guards, Git/Base and command-runner seams.
- Parent confirmed empty-approved-set Execute boundary and minimal shared locking.

## Decisions
- Observation remains exactly Directly InSync, Drifted or Observation Failed.
- Inclusion is explicit whole-unit participation, not Approval.
- Commands carry session identity and expected revision; rejection is mutation-free.
- Expected opening failures return a typed result; programming failures escape.

## Progress checkpoints
- Implemented and validated policy-defined frozen bytes/Missing, stable drift
  and diagnostic rows, immutable revision-bearing commands and terminal results.
- Integrated existing directional Guard hierarchy and concrete Git/Base lifecycle:
  one status batch per repository, direct exact/conservative acknowledgment,
  pre-Guard configured-policy deletion, retained unit-local failures.
- Added the shared non-blocking operation lock and minimal Push/Pull runner
  ownership around planning, review and execution.
- Confirmed with parent that directory exclusion/census belongs outside this
  file-only checkpoint and scratch is permitted in non-mutating preview.
- Synchronized lifecycle, CLI locking, storage and contributor documentation.

## Surprises and discoveries
- No manager operation lock existed; repository store transaction locks alone
  cannot protect review or coordinate Push/Pull. New operation ownership is
  deliberately outside those short storage transactions.
- Path.glob suppresses inaccessible-directory errors on the active Python
  runtime. A red preview regression demonstrated unsafe existing Base storage
  could be reported absent; artifact discovery now preserves those I/O failures.
- Provider stderr can contain private staged endpoint paths. A red regression
  now verifies diagnostics translate them to managed endpoint identities.
- The foundations still accept directional Git-ignore enablement lists.
  Parent explicitly kept that separate discrepancy outside this checkpoint;
  directory behavior and its current syntax documentation were not changed.

## Validation and outcomes
- Test-first slices covered classification, projections, command rejection,
  terminal use, locks, Base acknowledgment/maintenance, failure isolation and
  preview non-mutation; real temporary Git and SQLite resources were used.
- Full suite checkpoint: 1,307 tests passed in 11.40s; subsequent staged-path
  privacy regression adds one passing targeted test.
- Targeted session suite currently has 36 passing cases. Compileall, focused
  Ruff checks/format and whitespace checks passed at the full-suite checkpoint.
- No commit, push or PR performed. Parent will independently review.
- Final validation is recorded below after the final local changes.

## Final local validation
- Full suite: **1,308 passed in 11.27s**.
- `uv run python -m compileall -q src tests` passed.
- Ruff passed for all new modules/tests and changed facade/runner/template files.
  `projection.py` retains one pre-existing unused `HookCommandSpec` import
  (confirmed against HEAD); checking it with only F401 excluded passes. No
  unrelated cleanup was included.
- `git diff --check` passed. Native macOS was not run; POSIX locking and
  cross-process contention were exercised with real Linux resources.
- Implementation is complete for the agreed file Observation boundary.
  Independent review remains with the parent; no commit/push/PR was made.
