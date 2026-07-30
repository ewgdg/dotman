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
- command dispatch
- compatibility wrappers used by tests and callers
- top-level error handling

Focused CLI responsibilities live in dedicated modules:

- `cli_parser.py` — argparse construction
- `cli_emit.py` — text/JSON output formatting
- `cli_commands.py` — per-command handlers
- `cli_style.py` — labels, colors, and display helpers

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
