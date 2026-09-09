# Directory topology and root effects

## Goal and intention
Implement #74 under #56 sections 5 and 7: independent child convergence with safe structural prerequisites, selectable root mode work, and conservative obsolete Base reclamation.

## Scope and constraints
No symlink interpretation changes (#75). Preserve two operation-wide stages and per-child completion. Never approve blockers implicitly or create empty roots.

## Work plan
1. Add observable regressions before implementation.
2. Freeze topology evidence; validate deletion approval closure before execution.
3. Order prerequisites and preserve root boundaries in both stages.
4. Add selected root mode work inside push hooks.
5. Reclaim only identities proven obsolete by later complete unrestricted successful census.
6. Run focused coverage, update durable docs, commit.

## Validation
Directory observation/convergence and stage tests, plus auxiliary UI contracts. Verify fail-fast mutation boundaries and independent completion.

## Progress
Read #74, parent contract, CONTEXT.md, guard ADR, and ExecPlan guidelines. Existing census drops directory nodes; endpoint observation currently rejects structural absence. Stage execution uses lexical child order and prunes only to leaf parent.


## Progress
- Added failing topology/pruning tests before source changes; initial six tests failed as expected.
- Implemented frozen blocker evidence, typed closure rejection, topology ordering, structural pruning/root retention, and selectable root modes.
- Snapshot file-only assumptions blocked valid directory-to-file publication. Added selected structural directory preimages and topology-safe restore as an enabling change, without recursive payload capture.
- Added later-session obsolete Base proof and conservative restrictions.
- Focused topology suite: 22 passing; snapshot plus topology coverage: 42 passing. Existing directory observation/convergence/session coverage: 101 passing before final refinements.
- Durable Sync, CLI, snapshot, and Base-storage docs updated.

## Decisions
Root work shares frozen stage infrastructure but has no public Sync Unit completion.
Census restrictions invalidate the whole target's reclamation proof rather than guessing absence beneath controls.
Snapshot restores never recursively remove payload files.


## Outcomes and verification
- Final focused topology coverage: 26 passing, including both transition directions, exact selection closure, typed unmanaged conflicts, root hook ordering, prerequisite failure honesty, snapshot restoration, and restricted-census retention.
- Broad affected-area run: 199 passed in 12.39s.
- Full suite: 1751 passed, 1 failed in 89.51s. The sole failure is existing `tests/engine/test_execution.py::test_execute_session_runs_tty_editor_steps_with_terminal_passthrough`, expecting ShellCommand instead of current ArgvCommand. Reproduced independently against an untouched HEAD archive (1 failed in 0.22s); not changed by this task.
- Full-suite evidence: disposable logs under the system temporary directory. No unrelated correction included.

- Final stage/observation/Textual verification after ordering refinement: 115 passed; git diff --check clean.
