# Enforce Sync symlink policy
## Goal and constraints
Implement #75 under #56: frozen lexical identities, current-link execution, explicit prompt replacement authorization, and typed unsafe-path diagnostics. Preserve #74 topology ordering; no unrelated fixes.
## Work plan
1. Add regression tests before changes.
2. Carry semantic authorization through session and publication; enforce current directory policy and bounded pruning.
3. Expose authorization in the deck and document the current contract.
4. Run focused engine/CLI tests, inspect diff, commit.
## Validation
Prompt replacement and deletion; followed missing/retargeted endpoints; directory fail/follow; no execution content re-observation; unsafe repository shapes; stable fingerprints; existing topology tests.
## Progress
- Read #75, #56 symlink contract, domain guidance and Guard ADR.
- Existing census implements directory traversal policy; prompt authorization has no command and always fails materialization.

- Regression tests first exposed missing authorization and unchecked directory links at execution; corrected test config to use the declared [symlinks] section.
- Implemented semantic authorization, deck action and review styling, typed shape diagnostics, current-link publication, and non-pruning external referent deletion.
- 14 focused new engine/UI tests pass; 62 publication/repository/topology tests passed before the final expanded checks.
## Decisions
- Authorization is session-row state, separate from Proposal Approval; unattended adapters never issue it.
- Mode-only prompt publication writes frozen bytes before chmod so it replaces rather than mutates the link referent.
- Retargeted followed referents may lie outside the lexical root by explicit follow policy; deletion never prunes their external parents.

## Outcomes and validation
- All 675 Sync engine and CLI tests passed (`uv run pytest -q tests/engine/test_sync*.py tests/cli/test_sync*.py --tb=short`, 84.36s).
- Final review added regression-first coverage for retargeting across snapshot access and followed root shape changes; focused publication, topology, auxiliary and new UI tests rerun afterward.
- Existing tests now assert specific symlink-authorization and unsupported-entry diagnostic codes. Deck link help is conditional to preserve unrelated workset page geometry.
- Diff reviewed and whitespace checked. No unrelated baseline execution test changes. No full repository suite run.
- Durable contract is updated in docs/sync.md and docs/cli.md; shared CLI styling covers authorization state.
