# dotman Repository Model

This document captures the current repository structure and configuration schema shown by the example repo.

## Current Scope

- This is a new package-oriented `dotman` design.
- The main reference lives under `examples/repo/`.
- The example is meant to clarify the model, not freeze every detail forever.

## Core Objects

- A package is the atomic install unit.
- Packages live under `packages/`.
- A package manifest lives at `packages/<package-id>/package.toml`.
- Namespaced package IDs map to nested directories under `packages/`, for example `work/git` -> `packages/work/git/package.toml`.
- Packages may define `binding_mode = "singleton" | "multi_instance"`.
- `binding_mode` defaults to `singleton`.
- `singleton` means the package has one tracked identity regardless of bound profile.
- `multi_instance` means the package definition may produce multiple independent package instances, keyed by bound profile.
- Packages may declare `depends` for hard requirements.
- `depends` entries may reference either package IDs or group selectors.
- Group dependencies expand to their member packages during dependency resolution.
- Dependency resolution must reject cycles, including mixed package/group cycles.
- Packages that exist only for hard dependency aggregation should use a `-meta` suffix by convention.
- A package's target live paths are implicitly reserved.
- Packages may define `reserved_paths = [...]` for additional live paths that must stay exclusive to that package.
- A package directly under `packages/` is in the default namespace.
- Namespaced packages stay explicit, for example `work/git`.
- Groups live under `groups/` and are used for package selection and composition.
- Group IDs may be namespaced, for example `os/arch`.
- Groups are not packages and are not tracked identities.
- Tracking a group should behave like passing its resolved member packages as track arguments with the same bound profile.
- Meta packages are still normal packages; they may be tracked explicitly and may use `depends` for aggregation.
- Terminology should follow ArchWiki's distinction between meta packages and package groups: <https://wiki.archlinux.org/title/Meta_package_and_package_group>
- `/` is reserved for namespacing inside package and group IDs.
- Repo qualification stays outside the selector with `repo:selector`, not `repo/selector`.
- `\` is not a valid selector separator.
- Groups should use a single `members` list for both package selectors and nested group selectors.
- Profiles provide variable values only.
- Profiles may define `includes = [...]` to compose other profiles.
- Included profiles merge in declaration order.
- Later included profiles override earlier included profiles.
- The profile's own vars override all included profiles.
- Repos may define optional repo-wide defaults in `repo.toml`.
- Machine-local or private overrides should not live in the repo.
- Per-repo local overrides should be read from `$XDG_CONFIG_HOME/dotman/repos/<repo-name>/local.toml`, with fallback to `~/.config/dotman/repos/<repo-name>/local.toml`.
- In v1, per-repo local overrides are limited to `[vars]` data.

## Resolution Model

- Package values act as defaults unless overridden.
- Variable resolution order is `package defaults -> composed profile -> local`.
- `local` means the per-repo XDG local override file, not a repo-root file.
- Local override merge rules should match the rest of resolution:
  - keyed maps use deep merge by key
  - scalars use last-wins replacement
  - lists replace the earlier value; they do not merge
- A binding stores the selected bound profile.
- Persisted tracked state stores package bindings, not group selectors.
- The composed/effective profile is runtime resolution context, not package identity.
- Packages with no file payload may still be useful as meta packages when they only declare `depends`.
- Any string value may contain template expressions and is rendered during resolution.
- A package may define `extends = [...]` to inherit from one or more parent packages before profile and local values are applied.
- Parent packages resolve in declaration order.
- The child package is applied last.
- The selected package is still resolved into one final package before `push` or `pull`.
- Target and reserved-path collision rules apply across all resolved package instances, including instances that come from the same `multi_instance` package definition.

## Package Binding Modes

- `binding_mode` controls package identity semantics, not file rendering semantics.
- File naming conventions such as `.tmpl` should not change package binding behavior.
- A `singleton` package is directly trackable as one package identity.
- A `multi_instance` package definition is not itself a tracked package identity.
- Tracking a `multi_instance` package always produces a package instance bound to one selected profile.
- A `multi_instance` package instance is identified by package ID plus bound profile.
- Effective/composed profile data may be shown for resolution context, but it is not part of package instance identity.
- A dependency on a `multi_instance` package should inherit the current bound profile unless the dependency entry explicitly requests a different bound profile.
- `multi_instance` allows multiple instances from the same package definition to coexist as identities.
- Coexisting identities do not bypass normal target ownership or reserved-path conflict checks.

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
- Target keys are arbitrary manifest identifiers.
- Tools may still generate deterministic path-derived target keys for convenience, but that naming is a convention rather than a schema rule.
- `path` may use `~/...` for home-relative destinations or an absolute path otherwise.
- Targets may define `chmod` when the installed root path needs an explicit mode.
- `chmod` is optional and should usually be omitted unless the target needs a non-default live mode.
- Targets may define `preset` as a built-in default bundle for common target workflows.
- Explicit target keys override preset defaults.
- Built-in target presets currently include `jinja-editor` for the common Jinja render + reconcile workflow.
- Targets may define `render` as a forward transform used during `push`.
- `render` may be a built-in renderer such as `jinja`, or a non-interactive stdout-producing command string.
- Built-in renderers are shortcuts for equivalent dotman helper commands; for example, `render = "jinja"` means dotman runs the built-in Jinja renderer as if it had executed `dotman render jinja "$DOTMAN_SOURCE"` **with the current binding context already injected through `DOTMAN_PROFILE`, `DOTMAN_OS`, and `DOTMAN_VAR_*`**.
- Running `dotman render jinja ...` manually is different: it does not resolve repo/profile context by itself, so manual use must pass `--profile` / `--os` / `--var` or set the matching `DOTMAN_*` env vars.
- Targets may define `capture` as a non-interactive live-to-repo projection used during pull planning.
- `capture` should be a non-interactive stdout producer.
- Targets may define `reconcile` as the actual reverse-sync action used during `pull`.
- `reconcile` may be interactive and should receive both repo and live paths.
- Built-in reconcile helpers are also available; for example, `reconcile = "jinja"` uses dotman's Jinja-aware editor reconcile flow for static template dependency trees.
- Targets may define `pull_view_repo` to control how repo-side content is projected during pull planning.
- Targets may define `pull_view_live` to control how live-side content is projected during pull planning.
- `pull_view_repo` and `pull_view_live` may use built-in values such as `raw`, `render`, and `capture`, or an explicit script/command string when needed.
- Default pull planning should compare:
  - repo side: `raw`
  - live side: `capture` if the target defines `capture`, otherwise `raw`
- A template-style forward-managed target should typically set:
  - `pull_view_repo = "render"`
  - `pull_view_live = "raw"`
- See [`templates.md`](./templates.md) for a concrete package-manifest setup, including reconcile configuration for template sources with includes.
- A live-dump-style target should typically keep:
  - `pull_view_repo = "raw"`
  - `pull_view_live = "capture"`
- Targets may define `push_ignore` as gitignore-style patterns relative to the source root.
- `push_ignore` is for tracked files that should stay in the repo but should not be installed, for example `*.archived` or `__pycache__/`.
- The old `*/__pycache__/*` workaround is no longer needed.
- Targets may define `pull_ignore` as gitignore-style patterns relative to the live target root.
- `pull_ignore` is for live-side ignore during pull planning and reconciliation.
- Patterns follow gitignore semantics: `**`, leading `/`, trailing `/`, and `!` negation are all supported.
- Repos may define repo-wide ignore defaults in `repo.toml`:
  - `[ignore]`
  - `push = [...]`
  - `pull = [...]`
- Repo-level ignore defaults are prepended to target-level ignore lists.
- For directory targets, old install-ignore style rules should map to `push_ignore`.
- For directory targets, old update-ignore style rules should map to `pull_ignore`.
- In v1, directory-target `pull_ignore` should also preserve matching live paths during push cleanup, so users do not need to maintain a duplicate preserve list.
- Dotman does not read package-local `.gitignore` files here; use `push_ignore` and `pull_ignore` instead.
- For directory targets, `push` should install everything under the source tree except paths matched by `push_ignore`.
- For directory targets, `push` should also remove stale live paths that are no longer present in the repo source, except paths matched by `pull_ignore`.
- Source files can follow a default reverse-sync convention by mirroring the live path under `files/`.
- Template suffixes such as `.tmpl` are optional conventions, not the source of truth.

Example repo defaults:

```toml
[ignore]
pull = ["*.dotdropbak"]
```

## Hooks And Commands

- Supported hook names are `guard_push`, `pre_push`, `post_push`, `guard_pull`, `pre_pull`, and `post_pull`.
- `check` is removed; there is no backward-compatibility alias.
- Hook entries may be a single item or an ordered list.
- Hook lists run in declaration order and stop on first failure.
- `guard_*` runs before package target work for that operation.
- `pre_*` runs immediately before the package's selected target steps.
- `post_*` runs only when the package still owns at least one selected target execution step and all selected target steps succeed.
- Package hooks are executable only when the package still owns at least one non-noop effective target after tracked-target winner resolution and any interactive target exclusion.
- Provenance alone should not cause hooks to execute.
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
- `reconcile_io` controls how the selected reconcile step is executed.
  - `pipe`: default behavior; dotman captures stdout/stderr like other command-backed steps.
  - `tty`: run attached to the current terminal and require an interactive tty.
- Use `reconcile_io = "tty"` for full-screen editors or other terminal-native tools that would break if dotman piped and prefixed their output.
- Dotman may provide helper commands for package-authored `reconcile` workflows; for example, `dotman reconcile editor` can accept repeated `--additional-source` args for multi-source reconcile workflows.
- For `dotman reconcile editor`, `--repo-path` is the primary repo-side target source and repeated `--additional-source` args are for extra repo files that should be opened alongside it during reconciliation.
- `dotman reconcile editor` may receive separate review paths, so the review content can use planning projections while the editor buffers point at temporary transactional copies of the repo-side source files.
- The preferred contract for reconcile helpers is review-side projections via `DOTMAN_REVIEW_REPO_PATH` and `DOTMAN_REVIEW_LIVE_PATH`.
- Temporary review artifacts should be readonly, since they are inspection-only scratch files.
- `dotman reconcile editor` should open the review diff first, and then open temporary editable copies of the repo-side source files.
- `dotman reconcile editor` should only write those edited copies back to the repo after the editor exits and the user confirms the write.
- `reconcile` should run only after the target has already been selected for pull work.
- If both `capture` and `reconcile` are defined, dotman should use `capture` for pull planning and `reconcile` for the actual selected pull step.
- If a transformed file target has no `reconcile`, dotman may still pull by writing repo-side content from `capture`, but `reconcile` is preferred when interactive or custom logic is needed.
- When `pull` writes repo-side files while dotman is running under `sudo`, dotman should restore ownership of the written repo path back to the invoking user so the repo does not get stranded as root-owned.
- Live file mode checks should compare against target `chmod` after both `push` and `pull`.

## V1 Bias

- Copy-only install behavior for now.
- No `var_schema` support in v1.
- Prefer complete packages over hidden merging or cross-package coupling.

## Reference Paths

- `examples/repo/packages/`
- `examples/repo/groups/`
- `examples/repo/profiles/`
- `examples/repo/local.example.toml`
- `$XDG_CONFIG_HOME/dotman/repos/<repo-name>/local.toml`
