# Safely atomic durable writes

## Goal

Implement GitHub issue #19 so every durable Dotman output uses one atomic replacement seam whose crash-recovery sweep cannot invalidate another active writer.

## Intention

Define one validated temporary-name contract carrying the creator PID and a collision-resistant suffix. Writers create, replace, and finally clean their own temporary path. Directory sweeps remove only names that satisfy that contract and whose creator process is definitely absent; active or unverifiable ownership is left untouched.

## Scope & Constraints

- Preserve byte, text, and symlink target modes and normal creation-mode behavior.
- Route add-manifest persistence and file-based structured-transform output through the shared seam.
- Preserve add and transform content, semantic no-op reuse, mode synchronization, and stdout behavior.
- Test observable behavior at public atomic-file and CLI/framework seams.
- Do not add age-based stale-file decisions or legacy-name migration handling.

## Work Plan

1. Characterize the current atomic, add-manifest, and structured-transform write paths.
2. Add a failing overlap regression at the atomic replacement seam.
3. Establish and test the PID-plus-random-suffix naming/liveness contract and finally cleanup.
4. Add text and symlink mode/creation behavior coverage and implement the shared seam.
5. Add failing add/transform integration coverage where existing tests do not prove seam use, then route both paths through it.
6. Run targeted tests and type checks throughout, then the complete suite once.
7. Review the final diff independently against repository standards and issue #19, address findings, and commit.

## Validation

- `uv run pytest tests/test_atomic_files.py`
- Targeted add and transform test files selected during reconnaissance.
- Project type/style checks declared by repository configuration or existing workflows.
- `uv run pytest`

## Progress

- [x] Issue #19 and repository/domain instructions read.
- [x] Public seams identified: atomic replacement API, add CLI manifest persistence, transform CLI path output.
- [x] Red/green slices complete.
- [x] Full validation complete: 944 tests pass with `NO_COLOR` unset as required by the ANSI assertions.
- [x] Independent Standards and Spec reviews complete.
- [x] Commit created.

## Surprises & Discoveries

- The existing sweeper expects a PID in temporary names, but `NamedTemporaryFile` currently creates names without one. Active temporary files can therefore be classified as stale.
- Byte and symlink writes already share `atomic_files.py`; add-manifest and transform path outputs still write directly.
- New files can rely on exclusive creation for normal umask behavior, avoiding a process-global umask read in concurrent writer paths.
- Durable tracked-package state, reconcile source writes, and snapshot files also needed routing; remaining direct writes are transient review/editor/shim materializations.

## Decisions

- Malformed or otherwise unverifiable temporary names are not sweep candidates; only a valid contract plus a definitely absent PID authorizes deletion.
- Liveness is process-based only. File age does not participate.
- Keep the byte and symlink lifecycle explicit. A callback extraction would make ownership transfer less obvious and could weaken the rule that cleanup may start only after exclusive creation succeeds.

## Outcomes & Retrospective

All durable output paths now converge on the atomic replacement seam. Temporary names carry an ASCII creator PID plus a full UUID4 hex token; sweep deletion requires a valid name and `ProcessLookupError`. Add, transforms, tracked state, reconcile writes, snapshot files, and the standalone plist writer retain their observable contracts. Independent review found no hard standards violations and no spec findings. The complete suite passes with 946 tests when `NO_COLOR` is unset for the ANSI-specific tests.
