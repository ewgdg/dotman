# dotman Code Structure

This document owns contributor module orientation. [Sync lifecycle](sync.md)
owns behavior, [CLI](cli.md) owns the external command contract, and
[repository configuration](repository.md) owns schema and provider rules.

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
- `sync_deck_command.py` — Sync CLI authorization and final output over public
  session views and commands
- `sync_deck.py` — Textual DataTable workset, scrollable frozen review and
  confirmation over public SyncSession commands; ordinary terminal prompts
  continue to use `prompt_toolkit`
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
- `sync_directory.py` — symmetric control-aware census and identity-derived child metadata; no aggregate payload or publication
- `projection.py` — shared frozen Render, Capture and comparison providers plus Push file/directory action planning through `ProjectionContext`
- `sync_scope.py` — static tracked scope resolution and canonical file/child identity keys
- `sync_reconciliation.py` — typed three-way repository reconciliation from frozen Base, repository, and Capture evidence
- `sync_path_policy.py` — endpoint traversal, live-link interpretation, and execution-time path safety
- `sync_session.py` — shared Proposal workset, immutable views, semantic commands, transactional Approval and Sync convergence orchestration
- `pull_session.py` — fixed live-to-repository Observation, opt-out Proposal/Additional Approval and repository-only completion over the shared workset
- `execution.py` — Push execution and the shared command-hook execution boundary
- `sync_editor.py` — isolated configured/default Editor invocation and permitted source staging
- `sync_observation.py` — file endpoint evidence, policy comparisons, frozen Guards/Git/Base facts and opening-time Base lifecycle
- `sync_auxiliary.py` — immutable Probe/hook rows, one-shot Probe activity and Guard-admitted directional hook retention
- `operation_lock.py` — manager-wide non-blocking real-operation ownership shared by sessions and Push/Pull command workflows
- `sync_base_lifecycle.py` — configured-policy Base eligibility, frozen Git facts, input fingerprints, applicability inspection, and per-unit acknowledgment/deletion decisions
- `sync_base_maintenance.py` — real Push selected-policy cleanup after static ownership/conflict resolution and before Guards; preserves excluded or unresolved children and never acknowledges ancestry
- `sync_base_inspection.py` — metadata-only list/info, exact manager-locked reset, and aggregate doctor diagnostics using shared static resolution and Base applicability
- `sync_base_store.py` — secure fixed-epoch, per-repository SQLite storage for exact Sync Base records and content-addressed payloads

Interactive Sync materialization uses one dedicated thread in `sync_deck.py`.
Its awaitable dispatch copies ContextVars, rejects competing deck input while busy,
and drains the actual thread before session abort or operation-lock release.
Observation-time SQLite stores are already closed; only frozen evidence and static
metadata cross into materialization. Apply and Publication remain synchronous.
`command_runtime.py` owns operation-scoped cancellation and child process handles.
`command_operation()` establishes the CLI operation; nested commands share its
`CommandOperation`. A standalone runtime command establishes its own scope when
none is active. Sync retains the operation identity from opening and reactivates it
for each dispatch, including materialization and final execution/cleanup. Its
thread-safe cancellation latch never resets: copied contexts and late callbacks
still refer to the cancelled operation, while subsequent operations on the same
engine or default/shared runtime get independent latches. ContextVar activation
is lexical, so a session can dispatch from a copied context without resetting a
token created in another context. `check_cancelled()` raises typed
`InterruptedError`. Cancellation never uses Textual thread-worker cancellation as
evidence that provider cleanup finished.

Explicit Proposal editing shares the same admission lane. TTY providers run while
Textual is suspended; pipe providers leave the deck visible. Each Editor attempt
has a separate cancellation scope: cancelling an attempt drains its subprocess
and discards only that transaction, without poisoning the enclosing SyncSession.
Session abort still cancels active Editor work. Canonical Additional Source rows retain staged changes independently from
Proposal generations, with reverse references and path-local standing Approval.
Only approved candidate bytes enter dependent projection inputs; unapproved
paths use frozen preimages. Approval changes eagerly rematerialize approved
references and invalidate unapproved previews for lazy regeneration. Batch
commands assign final selection states before materialization. Repository Apply
consumes each approved Additional change once before exclusive Primary changes;
its result is independent of Proposal completion.

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

The Sync Observation adapter expands selected directory scopes into canonical
children after target Guards. It reuses `IgnoreMatcher`, Path Rule composition,
and directional Path Rule Guard evaluation; it does not reuse aggregate
one-sided directory plans. The census retains local diagnostics and combined
controls before narrowing to selected identities. Any ancestor discovery failure
propagates to existing and explicitly selected descendants before payload reads.
Root metadata remains only a hook/discovery scope. Expanded children enter the
shared Proposal and Base adapters as `Missing` or `DirectoryChildPresent` with
bytes and executable state. Capture and Render preserve that state; three-way
Reconciliation merges bytes and executable independently. Exact chmod is applied
only while freezing the live outcome.

Session/operation adapters own the manager lock, exclusions, actual Observation,
final effect execution, and invoking these boundaries in order. Base inspection
CLI behavior is separate from these foundation seams. Aggregate
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

- Push execution builds one execution session, owns one sudo lease scope, emits ordered repo/package/step events, and preserves command-runtime streaming, TTY, interruption, and exit behavior.
- Sync and Pull use their process-local Proposal sessions and shared destination stages rather than this plan runner.
- Push snapshots are created lazily before the first live mutation, finalized once, and pruned only after final status is durable.
- Restore executes visible actions in order, stops at the first failure, records successful restore metadata, and emits typed action events/results.
- Human and JSON output policy is selected at CLI composition. JSON consumes no progress events and emits one final result document.

Planning is package-centric:

- selector queries and tracked package entries resolve into `ResolvedPackageSelection`
- execution/review/snapshot flows consume `OperationPlan.package_plans`
- tracked-package persistence remains a separate storage concern from runtime package planning

If a new engine feature clearly belongs to one of those areas, put it there first and keep `engine.py` as the public facade.

## Contribution rule of thumb

Before adding more logic to `cli.py` or `engine.py`, ask:

- Is this public facade glue?
- Or is it a focused responsibility that belongs in a dedicated module?

Prefer the dedicated module unless there is a strong reason not to.

## Session orchestration boundary

Static resolution belongs to `sync_scope.py`: tracked selectors, profile and
dependency expansion, ownership, and collisions are settled before a session
opens. `SyncSession` and `PullSession` retain distinct orchestration over shared
frozen observation/projection, transactional sources, review, repository writes,
path safety, and Base storage mechanics. Do not move workflow semantics into a
pervasive `sync | pull` branch in those shared modules.

`sync_deck_command.py` owns CLI consent, unattended default selection, output,
and exit mapping. The deck consumes immutable public views and semantic commands;
it owns focus, keybindings, menus and presentation, never private execution plans.
JSON is an output choice, not authorization. Providers and hooks use the shared
Command Runtime so unattended execution cannot accidentally fall into terminal
interaction and JSON stdout contains only the final result.

## SyncSession clients

Resolve selectors with `engine.resolve_sync_scope(...)`, then call
`engine.open_sync_session(scope, preview=..., run_noop=..., event_sink=...)`. Opening returns
a `SyncSession` or typed `SessionOpenFailed`. File targets, directory children and
auxiliary scopes are supported. File and child drift materializes policy-allowed
Proposals and supports transactional editing; Probe/hook work is directly included
without an Observation or Proposal.

Read `session.view` rather than private plans. `SetIncluded`, `SetApproval`,
`PrepareProposalReview`, `Preview`, `Execute` and `Abort` carry the view's session
ID and revision. Dispatch returns
`CommandAccepted(view, result)` or mutation-free `CommandRejected(view, reason)`.
Row-local allowed commands distinguish drift, non-approvable diagnostics and
auxiliary rows, which expose only `SetIncluded`.
Convenience `execute()` and `abort()` act on the current view; adapters holding
cached views should dispatch explicit revision-bearing commands.
Accepted commands yield new immutable snapshots; old views remain valid evidence.
Terminal views allow no further commands.

`SessionOpened`, `SessionChanged` and `SessionFinished` are immutable lifecycle
events for recording or presentation sinks. Callback programming failures
escape; failed opening still releases owned resources. Context-manager exit
aborts an unfinished session. Review and Approval materialize immutable Proposals
from frozen inputs. Preview reports approved effects without mutation; Execute
applies those same repository and live effects without re-observation or projection. Results keep
direct agreement, approved convergence, pending/excluded drift and failures
distinct. File inclusion never substitutes for Proposal Approval; auxiliary
inclusion authorizes only retained directional hooks.

`sync_capture.py` materializes Use live from frozen endpoints and comparison
evidence through shared projection and patch mechanics. A Capture-backed comparison
is reused rather than run again. `sync_repository_apply.py` applies independently approved Additional Source
Changes before exclusive Proposal-owned Primary Source outcomes through pull hooks without accessing live state or
snapshots. Units without required live effects complete at their ordered position
before the enclosing post-hook; the session uses
the shared Base lifecycle with frozen committed Git facts to acknowledge eligible
outcomes. A failed repository stage prevents all Live Publication.

`sync_publication.py` freezes hook and target execution metadata at opening and
publishes approved file effects through the existing file-access, snapshot and
hook mechanics. Both stages use its shared ordered stage-step construction:
repository order, package plan order, target plan order and nested directional
hooks. Child execution paths are frozen separately from their enclosing target,
so hooks run once per target while each child retains its own effects and result.
That frozen sequence drives execution and explicit unattempted-tail
reporting; it is not reconstructed from filesystem outcomes. Cancellation checks
precede hooks, effects and completion. Publication acknowledgment failures have
their own failed completion step, distinct from successful content or chmod.
The session preserves repository partial success as not-converged when later
publication is skipped. Public results retain immutable semantic step outcomes and
operation-level diagnostics separately from unit completion, so an enclosing
post-hook failure does not erase convergence. Each scoped outcome carries a
canonical `scope_identity`, preserving package instances and target names
without exposing execution plans.

The operation lock is a POSIX advisory `flock(LOCK_EX | LOCK_NB)` on the
owner-only `$XDG_STATE_HOME/dotman/operation.lock` file. Never unlink this file on
release: another process may already hold its inode. The manager directory must
be current-user-owned and not writable by other users; Base storage additionally
requires its documented exact private-directory modes. Lock acquisition rejects
symlink, nonregular, hard-linked, and wrong-owner lock files. For a verified
current-user-owned regular single-link file, acquisition automatically sets
its mode to `0600` through the open descriptor and revalidates it. This needs
no root access; failed permission repair aborts acquisition without replacing
or unlinking the lock.
Low-level planning and execution helpers do not acquire a second nested lock;
their direct callers must own an operation lifetime explicitly.
Restore and unrelated state commands do not participate in this Push/Pull/Sync
operation lock. It coordinates cooperating operations, not external filesystem
writers or hostile same-user code.

Projection providers receive private read-only copies of frozen Primary/live
endpoints; custom commands retain configured dependency access and cwd.
Session input metadata carries the invoking workflow into every provider,
including Probe and transactional Editor. Directional Guard/hook metadata stays
separate so provider identity cannot change which directional family executes.
Jinja renders frozen Primary bytes through the shared template renderer.
The public view exposes no staging paths, mutable configuration dictionaries,
store handles or execution steps.

References: [Python file locking](https://docs.python.org/3/library/fcntl.html#fcntl.flock)
and [descriptor-relative file access](https://docs.python.org/3/library/os.html#os.open).

`sync_deck_command.py` adapts Sync and Pull to the shared Command Deck.
The engine opens Pull through `open_pull_session`; Push retains its own plan
and execution runner.
