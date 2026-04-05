# dotman CLI Model

This document captures the current command and selector direction for `dotman`.

## Repos

- Dotman should search configured repos from user config.
- Repos should be searched in ascending `order`.
- Lower `order` means earlier search.
- A user may omit the repo name in normal CLI usage and let dotman search across repos.
- An explicit repo-qualified form should still be allowed when needed for disambiguation, for example `test:git@default`.

## Selectors

- Normal CLI usage should accept a type-less selector.
- A selector may resolve to either a package or a group.
- Examples:
  - `dotman apply git@default`
  - `dotman apply os/arch@basic`
- Selectors should be searched across configured repos in repo `order`.
- Exact selector matches should take priority over search behavior.
- If the same exact selector exists in multiple repos, dotman should display an interactive repo selection menu.
- In non-interactive mode, an exact selector collision across repos should fail and print the candidates.
- If a selector matches both a package and a group, that is an ambiguity error.
- Only in collision/debug cases should explicit forms such as `packages/...` or `groups/...` be needed.
- Partial selector input should also be accepted for interactive usage.
- If the input is not an exact selector, dotman should search package and group IDs for matches.
- If partial lookup finds exactly one match, dotman may use it directly.
- If partial lookup finds multiple matches, dotman should display an interactive selection menu.
- In non-interactive mode, partial lookup with multiple matches should fail and print the candidates.
- If no matches are found, dotman should fail fast.

## Profiles

- Profiles are separate from selection.
- Profiles provide variable values used during resolution.
- Profiles may include other profiles.
- Bindings should store the top-level profile the user selected, not the flattened leaf profile set.
- The same package or group should be applicable with different profiles.

## Bindings

- The CLI should treat the applied unit as a binding: `repo:selector@profile`.
- A binding combines what to apply with which variable context to resolve it under.
- Examples:
  - `main:git@default`
  - `test:os/arch@basic`
- Repo qualification is the explicit and stable form.
- CLI input may still omit the repo name when dotman can resolve it from configured repo search.
- Profile should not be encoded into repository paths or package/group IDs.
- `upgrade` and `import` should replay tracked bindings from persisted state.
- `upgrade` and `import` should resolve profile includes from the current repo state each time, not from stored leaf profiles.
- `selector@profile` is a convenient shorthand when repo resolution is unambiguous.
- A bare selector such as `git` or `os/arch` may still be accepted for interactive commands.
- If the user provides a bare or partial selector, dotman should resolve the selector first, then prompt for a profile interactively.
- If there is exactly one available profile, dotman may use it directly.
- In non-interactive mode, a missing profile should be an error instead of a hidden guess.

## Apply

- `apply` is the command for selecting and applying a binding.
- `apply` should resolve the binding into a package set, then merge profile and local values, then install/update the managed files for that desired state.
- `apply` should also persist the selected root binding into repo state so later `upgrade`, `import`, and installed-state queries can replay the current managed selection.
- Reapplying the same root selector in the same repo should update that tracked binding instead of appending a duplicate entry.
- Group composition should let a user keep a stable entrypoint such as `host/arch-niri` without manually listing every lower-level group.
- Examples:
  - `dotman apply main:git@default`
  - `dotman apply git`
  - `dotman apply g`
  - `dotman apply os/arch`

## Upgrade

- `upgrade` should be the equivalent of “reapply what is already selected”.
- `upgrade` should re-resolve the previously installed root bindings from persisted state.
- If group membership or package `depends` change in the repo, `upgrade` should pick up newly introduced managed packages and files.
- `upgrade` should only touch files within the current managed selection.
- Removal of dropped packages/files should stay a separate concern, for example a future `prune`.

## Remove

- `remove binding <binding>` should remove one tracked root binding from persisted state.
- `remove binding` should be state-only in v1.
- `remove binding` should not delete live files, run hooks, or infer target ownership.
- `remove binding` should match tracked bindings, not current repo manifests, so stale bindings can still be removed after repo changes.
- `remove binding` should accept either `selector` or `selector@profile`.
- If the profile is omitted, dotman should remove the unique tracked binding that matches the selector.
- If the selector only names a package that is present through another tracked binding, dotman should explain which tracked bindings currently include it instead of just saying "not tracked".
- Repo qualification may still be omitted when the tracked binding is unique across configured repos.
- Example:
  - `dotman remove binding main:git@default`
  - `dotman remove binding git`

## Installed State

- `list installed` should report the packages currently tracked by persisted bindings.
- `list installed` should resolve the current binding state against the current repo manifests.
- `list installed` should not guess from the live filesystem.
- `list installed` should not run apply/import planning or execute render/capture commands.
- `dotman list installed` should list unique installed packages and the bindings that currently include them.
- `dotman info installed <package>` should show detailed information for one currently installed package.
- Package detail should include the owning repo, description, bindings, resolved targets, and rendered hook commands for each binding context.
- Package lookup for `info installed` may use the same repo-qualified and partial-selector rules as other package-oriented commands, but it should search only tracked installed packages.

## Import Direction

- `update` is too ambiguous for this CLI model and should be avoided as a top-level command.
- `sync` is also too ambiguous because it does not imply direction.
- Repo-to-live flow should stay under `apply` and `upgrade`.
- Live-to-repo flow should use an explicit source-oriented command: `import`.

## Import

- `import` is the command for bringing live changes back into the repository sources.
- Example:
  - `dotman import main:git@default`
  - `dotman import test:os/arch@basic`
- `dotman import` with no selector should import the current managed selection from persisted state, just as `upgrade` reapplies the current managed selection.
- `import` should resolve the binding into a package set using the same profile/local context as `apply`.
- If the user provides a bare or partial selector to `import`, dotman should use the same interactive selector and profile resolution rules as `apply`.
- `import` should first build an import plan before changing any sources.
- For plain copied files, import planning can compare the package source directly against the live file.
- For transformed targets, import planning should compare repo-side and live-side views.
- Default import planning should compare:
  - repo side: `raw`
  - live side: `capture` if available, otherwise `raw`
- Template-style forward-managed targets should typically override import planning to compare:
  - repo side: `render`
  - live side: `raw`
- Capture-style targets should typically keep repo side as `raw` and use `import_view_live = "capture"`.
- `import_view_repo` and `import_view_live` define those projections explicitly when the defaults are not right.
- Only targets with detected drift should appear in the import selection menu.
- `import_view_repo` and `import_view_live` must stay non-interactive and side-effect free.
- A `reconcile` command may be interactive, for example by opening an editor to reconcile repo source files against the current live output.
- `reconcile` should only run after the user selects a changed target for import.
- If both `capture` and `reconcile` are defined, `capture` should drive planning and `reconcile` should handle the actual selected import step.
- If a transformed target has no `reconcile`, dotman may still import by writing repo-side content from `capture`, but `reconcile` is preferred when manual or custom merge logic is needed.
- `import` should only touch sources owned by the current managed selection.

## State

The CLI model implies persistent state.

For v1, dotman should persist bindings only.

- tracked root bindings
- no persisted target ownership yet
- no persisted resolved package graph yet

For example:

```toml
version = 1

[[bindings]]
repo = "main"
selector = "os/arch"
profile = "basic"

[[bindings]]
repo = "test"
selector = "git"
profile = "personal"
```

- `upgrade` should recompute the current package set from persisted bindings and the current repo state.
- `import` with no selector should also recompute from persisted bindings and the current repo state.
- `remove binding` should update only the persisted binding set.
- Target ownership and prune-oriented state can be added later when prune behavior is introduced.

## Host Entry Points

- Host-level entrypoints should use host meta packages for convenience.
- Groups still handle reusable composition beneath the host meta layer.
