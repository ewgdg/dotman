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
- `tracking.py` — persisted tracked-package state flows
- `tracked_packages.py` — tracked package lookup and detail helpers
- `planning.py` — high-level plan orchestration, including the top-level operation-plan wrapper used for repo-scoped hooks
- `collisions.py` — tracked-target winner resolution and conflict checks
- `projection.py` — target projection and file/directory action planning

Current execution shape is intentionally nested:

- operation plan
- repo-scoped hook buckets
- selector/package plans
- target plans and target-scoped hooks

That structure keeps repo/package/target hook ordering explicit instead of hiding it in ad hoc sorting.

If a new engine feature clearly belongs to one of those areas, put it there first and keep `engine.py` as the public facade.

## Contribution rule of thumb

Before adding more logic to `cli.py` or `engine.py`, ask:

- Is this public facade glue?
- Or is it a focused responsibility that belongs in a dedicated module?

Prefer the dedicated module unless there is a strong reason not to.
