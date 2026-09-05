# dotman Code Structure

This document records the current code-organization intent at a high level.

It is guidance, not a promise that every internal module name or boundary is permanent.

## Stable facades

- `src/dotman/cli.py` remains the main CLI entrypoint.
- `src/dotman/engine.py` remains the main engine-facing public facade.
- When practical, new internal work should preserve those public import surfaces instead of pushing callers toward internal modules.

## CLI structure

`src/dotman/cli.py` should stay thin and mostly coordinate:

- parser construction
- command-runner composition and selection
- configuration and engine construction
- top-level error handling

Focused CLI responsibilities live in dedicated modules:

- `cli_parser.py` — argparse construction
- `cli_emit.py` — text/JSON output formatting
- `standalone_commands.py` — configuration-independent rewrite, transform,
  elevation, capture, editor, and render workflows
- `inspection_commands.py` — configuration-aware list, info, search, and doctor
  workflows
- `state_commands.py` — track, untrack, add, and edit workflows composed with
  command-specific resolution and editor interfaces
- `sync_commands.py` — typed push, pull, and restore planning, review, preview,
  and execution workflows
- `cli_interaction.py` — shared terminal selection, resolution, diff review,
  and focused runtime adapters used by command runners
- `cli_style.py` — labels, colors, and display helpers
- `interaction.py` — typed terminal choices, confirmations, and text input, with
  production and deterministic scripted adapters
- `track_resolution.py`, `untrack_resolution.py`, `add_resolution.py`, and
  `edit_resolution.py` — command-specific matching, ambiguity, profile, label,
  and confirmation policy

Execution presentation is event-driven. `operation_runner.py` owns the push,
pull, and restore mutation lifecycle and emits typed events. Human and JSON
renderers in `cli_emit.py` consume those events and final results without
performing command, privilege, snapshot, or restore work.

The root selects every command through the same declared runner map. Sync
commands call typed engine planning and operation-runner interfaces directly;
there is no global callback-dispatch record or sync-specific fallback path.

If new CLI behavior grows beyond a small helper, prefer adding or extending a focused module instead of rebuilding a large `cli.py` monolith.

## Engine structure

`src/dotman/engine.py` should stay a facade that wires together narrower modules.

Current responsibility split:

- `repository.py` — repository loading and profile/group/package composition
- `manifest.py` — manifest merge and schema helpers
- `package_resolution.py` — selector parsing, package dependency closure, and resolved package selection construction
- `tracking.py` — persisted tracked-package state flows through `TrackedStateContext`
- `tracked_packages.py` — tracked package lookup and detail helpers
- `planning.py` — high-level plan orchestration through `PlanningContext`, including the top-level operation-plan wrapper used for repo-scoped hooks
- `planning_guards.py` — repo/package-instance/target/path-rule planning eligibility and guard diagnostics
- `collisions.py` — tracked-target winner resolution and conflict checks
- `projection.py` — target projection and file/directory action planning through `ProjectionContext`
- `sync_scope.py` — static tracked scope resolution and canonical file/child identity keys
- `sync_base_lifecycle.py` — configured-policy Base eligibility, frozen Git facts, input fingerprints, applicability inspection, and per-unit acknowledgment/deletion decisions
- `sync_base_store.py` — secure fixed-epoch, per-repository SQLite storage for exact Sync Base records and content-addressed payloads

The Base foundation exposes explicit boundaries rather than running a session.
`BaseUnit` carries successfully resolved selected configuration, never a
Guard-narrowed policy. `SyncBaseGit` freezes real HEAD/object format, one batched
Primary-path status observation, and isolated-checkout payloads through
Command Runtime. It uses an alternate index/worktree, disables replace refs,
repository graft input and ambient Git selectors, and never updates the
repository's real index. All Base Git operations require Git's `--no-lazy-fetch`
control: a commit available only from a promisor remote is missing locally,
not permission to fetch or modify the object store. Status is
batched once; checkout is path-local so unsupported committed shapes or a failed
conversion produce unit-local frozen failures without discarding successful
peers or re-observing status. Repository-wide Git failures still abort freezing.
`BaseInputs` accepts effective projection strings, named Path Rule identities,
profile context (including type-preserving frozen JSON variable inputs), and
symlink modes; it deliberately cannot accept policy, Guards,
Pull Views, chmod, or live referent paths.

`SyncBaseLifecycle.inspect` is read-only and needs no checkout or live access.
Unavailable results omit stored metadata. Git infrastructure errors and
store-level failures remain errors rather than being reclassified as missing
ancestry. `selected_policy_resolved` is the pre-Guard/pre-review maintenance
boundary; `direct_agreement` is the fresh-Observation boundary; `complete`
consumes explicit Approval and final unit-owned effect results at the earliest
ordered completion boundary. Acknowledgment failures are typed results with
`converged = false`; the store transaction preserves the old record.

Session/operation adapters own the manager lock, exclusions, actual Observation,
final effect execution, and invoking these boundaries in order. These foundation
seams do not themselves add a SyncSession or Base inspection CLI. Aggregate
directory discovery and orphan reclamation are not responsibilities of the
per-unit lifecycle.

Git plumbing references: [status porcelain](https://git-scm.com/docs/git-status),
[alternate-index read-tree](https://git-scm.com/docs/git-read-tree),
[checkout-index](https://git-scm.com/docs/git-checkout-index), and
[batch object lookup](https://git-scm.com/docs/git-cat-file),
[local-only object access](https://git-scm.com/docs/git#Documentation/git.txt---no-lazy-fetch),
and Git's [ancestry input environment control](https://github.com/git/git/blob/master/environment.h).

The engine composes those immutable contexts once. Internal modules receive configuration, repositories, tracked state, and command execution directly; they do not receive `DotmanEngine` or call back through private facade methods.

Current execution shape is intentionally nested:

- operation plan
- repo-scoped hook buckets
- resolved package selections / package plans
- target plans and target-scoped hooks

That structure keeps repo/package/target hook ordering explicit instead of hiding it in ad hoc sorting.

## Command runtime

`src/dotman/command_runtime.py` is the only process-creation boundary.

- Callers submit `CommandRequest` values containing a shell command or argument vector, environment overlay, working directory, pipe/TTY mode, streaming policy, and elevation mode.
- `ProductionCommandRuntime` constructs the ambient environment, applies elevation, launches the process, owns terminal or pipe behavior, streams and captures output, and normalizes interruption.
- Callers interpret `CommandResult.exit_code` in their own domain. The runtime does not decide whether a status means guard exclusion, probe absence, diff presence, or execution failure.
- `MemoryCommandRuntime` supplies deterministic queued outcomes and records requests for behavior tests.

Planning passes the engine's runtime explicitly while evaluating guards, probes, and projections, and binds that same runtime for shared privileged file helpers. Execution binds one runtime for the complete session. Editor, review, and privileged-helper commands use the same active runtime.

## Operation runner

`src/dotman/operation_runner.py` is the operation-level mutation boundary.

- Sync execution builds one session, owns one sudo lease scope, emits ordered repo/package/step events, and preserves command-runtime streaming, TTY, interruption, and exit behavior.
- Push snapshots are created lazily before the first live mutation, finalized once, and pruned only after final status is durable.
- Restore executes visible actions in order, stops at the first failure, records successful restore metadata, and emits typed action events/results.
- Human and JSON output policy is selected at CLI composition. JSON consumes no progress events and emits one final result document.

Planning is package-centric now:

- selector queries and tracked package entries resolve into `ResolvedPackageSelection`
- execution/review/snapshot flows consume `OperationPlan.package_plans`
- tracked-package persistence remains a separate storage concern from runtime package planning

If a new engine feature clearly belongs to one of those areas, put it there first and keep `engine.py` as the public facade.

## Contribution rule of thumb

Before adding more logic to `cli.py` or `engine.py`, ask:

- Is this public facade glue?
- Or is it a focused responsibility that belongs in a dedicated module?

Prefer the dedicated module unless there is a strong reason not to.
