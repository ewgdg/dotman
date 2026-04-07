# dotman Repository Model

This document captures the current repository structure and configuration schema shown by the example repo.

## Current Scope

- This is a new package-oriented `dotman` design.
- The main reference lives under `examples/repo/`.
- The example is meant to clarify the model, not freeze every detail forever.

## Core Objects

- A package is the atomic install unit.
- Packages live under `packages/`.
- Packages may declare `depends` for hard requirements.
- Packages that exist only for hard dependency aggregation should use a `-meta` suffix by convention.
- A package's target live paths are implicitly reserved.
- Packages may define `reserved_paths = [...]` for additional live paths that must stay exclusive to that package.
- A package directly under `packages/` is in the default namespace.
- Namespaced packages stay explicit, for example `work/git`.
- Groups live under `groups/` and are used for package selection and composition.
- Group IDs may be namespaced, for example `os/arch`.
- Groups should use a single `members` list for both package selectors and nested group selectors.
- Profiles provide variable values only.
- Profiles may define `includes = [...]` to compose other profiles.
- Included profiles merge in declaration order.
- Later included profiles override earlier included profiles.
- The profile's own vars override all included profiles.
- Repos may define optional repo-wide defaults in `repo.toml`.
- `local.toml` is the convention for machine-local or private overrides.

## Resolution Model

- Package values act as defaults unless overridden.
- Resolution order is `selection -> composed profile -> local`.
- Packages with no file payload may still be useful as meta packages when they only declare `depends`.
- Any string value may contain template expressions and is rendered during resolution.
- A package may define `extends = [...]` to inherit from one or more parent packages before profile and local values are applied.
- Parent packages resolve in declaration order.
- The child package is applied last.
- The selected package is still resolved into one final package before `push` or `pull`.

## Package Inheritance

- `extends` is for package reuse, not runtime binding or profile selection.
- `extends` should accept a list of package IDs.
- Resolution order is:
  - first parent
  - later parents in declaration order
  - child package
- Scalars use last-wins replacement.
- Keyed maps use deep merge by key.
- Lists replace the earlier value; they do not merge.
- Packages may define `remove = [...]` with dotted paths to delete inherited fields or keyed entries explicitly.
- Packages may define `append` to append to inherited list-valued fields without replacing the whole list.
- `append` should mirror the object shape it targets, for example:
  - `[append.hooks]`
  - `pre_push = ["{{ INSTALL }} extra-tool"]`
- `append` should fail if the targeted inherited field is not a list.
- Targets and hooks should stay keyed so merges remain deterministic.
- Conflicting target ownership or incompatible target/path collisions should fail hard.
- Reserved path collisions should also fail hard when one package reserves a live path used or reserved by another package.
- A child package may override inherited targets, hooks, vars, and metadata.
- Platform variants such as `linux/1password` are a primary use case for `extends`.
- `extends` is preferable to cross-package relative `source` references when a variant wants to inherit most of a base package.

Example:

```toml
id = "linux/1password"
extends = ["1password"]

[targets.quickaccess_desktop]
source = "files/local/share/applications/1password-quickaccess.desktop"
path = "~/.local/share/applications/1password-quickaccess.desktop"
chmod = "600"
```

## Targets

- Targets may define `path` for the live destination.
- Targets may define `chmod` when the installed root path needs an explicit mode.
- Targets may define `render` as a forward transform used during `push`.
- `render` should be a non-interactive stdout producer.
- Targets may define `capture` as a non-interactive live-to-repo projection used during pull planning.
- `capture` should be a non-interactive stdout producer.
- Targets may define `reconcile` as the actual reverse-sync action used during `pull`.
- `reconcile` may be interactive and should receive both repo and live paths.
- Targets may define `pull_view_repo` to control how repo-side content is projected during pull planning.
- Targets may define `pull_view_live` to control how live-side content is projected during pull planning.
- `pull_view_repo` and `pull_view_live` may use built-in values such as `raw`, `render`, and `capture`, or an explicit script/command string when needed.
- Default pull planning should compare:
  - repo side: `raw`
  - live side: `capture` if the target defines `capture`, otherwise `raw`
- A template-style forward-managed target should typically set:
  - `pull_view_repo = "render"`
  - `pull_view_live = "raw"`
- A live-dump-style target should typically keep:
  - `pull_view_repo = "raw"`
  - `pull_view_live = "capture"`
- Targets may define `push_ignore` as gitignore-style patterns relative to the source root.
- `push_ignore` is for tracked files that should stay in the repo but should not be installed, for example `*.archived`.
- Targets may define `pull_ignore` as gitignore-style patterns relative to the live target root.
- `pull_ignore` is for live-side ignore during pull planning and reconciliation.
- Repos may define repo-wide ignore defaults in `repo.toml`:
  - `[ignore]`
  - `push = [...]`
  - `pull = [...]`
- Repo-level ignore defaults are prepended to target-level ignore lists.
- For directory targets, old install-ignore style rules should map to `push_ignore`.
- For directory targets, old update-ignore style rules should map to `pull_ignore`.
- In v1, directory-target `pull_ignore` should also preserve matching live paths during push cleanup, so users do not need to maintain a duplicate preserve list.
- Standard `.gitignore` files inside the package source tree should still be respected during push.
- `push` should install everything under the source tree except paths excluded by `.gitignore` or `push_ignore`.
- For directory targets, `push` should also remove stale live paths that are no longer present in the repo source, except paths matched by `pull_ignore`.
- Source files can follow a default reverse-sync convention by mirroring the live path under `files/`.
- Template suffixes such as `.tmpl` are optional conventions, not the source of truth.

Example repo defaults:

```toml
[ignore]
pull = ["*.dotdropbak"]
```

## Hooks And Commands

- Hook entries may be a single item or an ordered list.
- Hook lists run in declaration order and stop on first failure.
- Repo-wide helper scripts live under `scripts/`.
- Package-specific scripts live inside the package, for example `hooks/`.
- Prefer explicit runner commands such as `sh hooks/push.sh`, `python3 hooks/render.py`, or `uv run hooks/render.py` instead of relying on executable bits.
- When a Python helper depends on repo-managed dependencies, prefer `uv run --project "$DOTMAN_REPO_ROOT" ...` so it uses the repo `pyproject.toml` and lockfile.
- Bare script paths should be reserved for cases where the repository intentionally manages executability.
- Reusable root-level action definitions are not needed in v1. Shared behavior should live in `scripts/` and be invoked from hooks or transform strings with template-expanded args.
- Target-level command strings may be repo scripts, package-local scripts, or inline command strings.
- Command strings may use the same template expansion rules as other string values.
- Dotman may pass standard path and context values to target commands through both env vars and command args.
- `render` and `capture` should treat stdout as the primary output channel; dotman owns file writes.
- Existing helper scripts may support stdout either when no output path is passed or when an explicit stdout-style output argument is used.
- A target with `render` is implicitly a transformed/template-like target; no separate template flag is needed.
- `render` is the forward path used during `push`.
- `capture` is the live-side planning projection used during `pull`.
- `reconcile` is the reverse action used during `pull`.
- Directory targets should not support `render`, `capture`, or `reconcile` in v1.
- During pull planning, dotman should compare:
  - repo-side view output against live-side view output
  - default repo-side view: `raw`
  - default live-side view: `capture` if available, otherwise `raw`
- `pull_view_repo` and `pull_view_live` must stay non-interactive and side-effect free.
- `reconcile` is the explicit reverse workflow. A `reconcile` command may open an editor or otherwise guide manual source reconciliation.
- Dotman may provide helper commands for package-authored `reconcile` workflows; for example, `dotman reconcile editor` can accept repeated `--additional-source` args for multi-source reconcile workflows.
- For `dotman reconcile editor`, `--repo-path` is the primary repo-side target source and repeated `--additional-source` args are for extra repo files that should be opened alongside it during reconciliation.
- `dotman reconcile editor` may receive separate review paths, so the review content can use planning projections while the editable buffers still point at real repo-side source files.
- The preferred contract for reconcile helpers is review-side projections via `DOTMAN_REVIEW_REPO_PATH` and `DOTMAN_REVIEW_LIVE_PATH`.
- Temporary review artifacts should be readonly, since they are inspection-only scratch files.
- `dotman reconcile editor` should open the review diff first, and then open the actual repo-side source files for editing.
- `reconcile` should run only after the target has already been selected for pull work.
- If both `capture` and `reconcile` are defined, dotman should use `capture` for pull planning and `reconcile` for the actual selected pull step.
- If a transformed file target has no `reconcile`, dotman may still pull by writing repo-side content from `capture`, but `reconcile` is preferred when interactive or custom logic is needed.
- Live file mode checks should compare against target `chmod` after both `push` and `pull`.

## V1 Bias

- Copy-only install behavior for now.
- No `var_schema` support in v1.
- Prefer complete packages over hidden merging or cross-package coupling.

## Reference Paths

- `examples/repo/packages/`
- `examples/repo/groups/`
- `examples/repo/profiles/`
- `examples/repo/local.toml`
