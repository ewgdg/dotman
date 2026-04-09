# dotman CLI Model

This document captures the current command and selector direction for `dotman`.

## Repos

- Dotman should search configured repos from user config.
- Repos should be searched in ascending `order`.
- Lower `order` means earlier search.
- A user may omit the repo name in normal CLI usage and let dotman search across repos.
- An explicit repo-qualified form should still be allowed when needed for disambiguation, for example `test:git@default`.

## Identifier Syntax

- The canonical repo-qualified selector form is `repo:selector`.
- The canonical repo-qualified binding form is `repo:selector@profile`.
- The canonical tracked package-instance form for a `multi_instance` package is `repo:package<profile>`.
- `/` belongs inside selector IDs for namespacing, for example `work/git` or `os/arch`.
- `<...>` is reserved for resolved tracked package instances, not root bindings or manifest IDs.
- When a menu, confirmation, diff banner, list output, or info view includes repo context, it should print the canonical colon-qualified form.
- Slash-qualified repo input such as `repo/selector@profile` may remain accepted as a lookup alias for convenience, but dotman should normalize displays back to `repo:selector@profile`.
- `\` is not a valid selector separator or menu-display form.

## Selectors

- Normal CLI usage should accept a type-less selector.
- A selector may resolve to either a package or a group.
- Examples:
  - `dotman push git@default`
  - `dotman push os/arch@basic`
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
- Interactive ambiguity menus should use the shared CLI selector flow across `track`, `push`, `pull`, `untrack`, and `info tracked`.
- For shorter candidate lists, dotman should print a numbered menu and let the user pick.
- Selector labels in those menus should use canonical selector text, and include the repo only as disambiguation context.
- For longer candidate lists, dotman should prefer `fzf` when available instead of dumping a tall numbered menu.
- Interactive selector menus should render bottom-up by default.
- Bottom-up selector menus should remain user-toggleable, but the default should favor bottom-up display.

## Profiles

- Profiles are separate from selection.
- Profiles provide variable values used during resolution.
- Profiles may include other profiles.
- The selected top-level profile is the bound profile.
- Bindings should store the top-level profile the user selected, not the flattened leaf profile set.
- The composed/effective profile is runtime resolution context, not tracked package identity.
- The same package or group should be applicable with different profiles.

## Bindings

- The CLI should treat the tracked unit as a binding: `repo:selector@profile`.
- A binding combines what to manage with which variable context to resolve it under.
- Examples:
  - `main:git@default`
  - `test:os/arch@basic`
- Repo qualification is the explicit and stable form.
- CLI input may still omit the repo name when dotman can resolve it from configured repo search.
- Profile should not be encoded into repository paths or package/group IDs.
- A tracked `multi_instance` package instance is addressed as `package<bound-profile>` in tracked-package views.
- `track` is the command that creates or replaces tracked bindings in persisted state.
- `push` with no selector and `pull` with no selector should replay tracked bindings from persisted state.
- `push` and `pull` should resolve profile includes from the current repo state each time, not from stored leaf profiles.
- `selector@profile` is a convenient shorthand when repo resolution is unambiguous.
- A bare selector such as `git` or `os/arch` may still be accepted for interactive commands.
- `track` may resolve a bare or partial selector first, then prompt for a profile interactively.
- If there is exactly one available profile, dotman may use it directly.
- In non-interactive mode, a missing profile should be an error instead of a hidden guess.

## Track

- `track <binding>` should resolve the binding, prompt for a profile when needed, and persist the selected root binding into repo state.
- Re-tracking the same exact root binding should update that tracked binding instead of appending a duplicate entry.
- A root selector that resolves to a group or `singleton` package should have at most one tracked binding per repo and selector.
- A root selector that resolves to a `multi_instance` package may keep one tracked binding per bound profile.
- If tracking a group or `singleton` package would replace an existing tracked selector with a different profile, interactive mode should ask for confirmation before writing state.
- In non-interactive mode, profile-replacing `track` for a group or `singleton` package should fail instead of silently overwriting the tracked binding.
- Tracking a `multi_instance` package with a different bound profile should add a distinct tracked binding instead of replacing the existing one.
- If `track` would make a new explicit binding override existing implicit targets, interactive mode should ask for confirmation before writing state.
- In non-interactive mode, `track` should fail instead of silently overriding implicit tracked targets.
- `track` is state-only in v1. It should not run repo-to-live work by itself.
- Examples:
  - `dotman track main:git@default`
  - `dotman track git`

## Push

- `push` is the repo-to-live command.
- `push` should operate only on tracked state.
- `push` should accept `-d` / `--dry-run` as an explicit preview-only mode selector.
- Plain `push` should perform real execution after planning, interactive exclusion, and diff review.
- `push` should accept `--full-path` to disable human-output path compaction for preview, selection, review menus, and human execution output.
- `push <selector>` should resolve only within tracked state and reuse the tracked profile instead of prompting for a fresh profile.
- `push <package>` should also work when that package is currently included through a tracked higher-level binding; dotman should reuse the owning tracked profile in that case.
- If a package selector matches multiple tracked `multi_instance` package instances, interactive mode should prompt for the specific instance and non-interactive mode should fail with the candidates.
- `push` with no binding should replay the current tracked bindings from persisted state without changing the tracked binding set.
- If group membership or package `depends` change in the repo, `push` should pick up newly introduced managed packages and files.
- `push` should only touch files within the current managed selection.
- In interactive mode, `push` should present one combined selection menu for pending non-noop target actions so the user can exclude specific items before execution.
- Executable hooks should be derived only after tracked target winners are resolved and after the interactive exclusion menu is applied.
- A binding that no longer owns any non-noop targets after those filters should not contribute executable hooks.
- After the interactive selection menu, `push` should enter an inspection-only diff review stage before continuing.
- After diff review accepts, `push` should execute immediately in one package-oriented timeline.
- Before the first live mutation of a real `push`, dotman should create one manager-level snapshot for the finalized selected plan.
- That snapshot should capture the pre-push live state only for paths that the finalized plan will mutate.
- `push --dry-run` should not create a snapshot.
- If a real `push` fails after snapshot creation, dotman should keep that snapshot so the user can inspect it or roll back manually.
- The interactive diff review stage should stay inspection-only in v1.
- Future edit-mode work belongs in [`docs/edit-mode-v2.md`](/home/xian/projects/dotman/docs/edit-mode-v2.md), not in the v1 review contract.
- Diff review should use `git diff --no-index --color=auto`.
- Diff review headers should use explicit `live/...` and `repo/...` paths instead of opaque `before-*` or `after-*` temp names.
- Diff review headers should compact long compared paths for readability, and should additionally collapse the current home directory to `~` instead of a machine-specific absolute prefix.
- Each reviewed diff should print a compact banner before the diff output so sequential reviews do not run together.
- In interactive review, diff output should prefer Git's pager and fall back to `less -FRX -R` when the effective pager resolves to `cat`.
- Review commands should support inspecting one item, inspecting all items, opening an editor for supported items, continuing, or aborting.
- If the requested selector is not currently tracked, `push` should fail instead of implicitly creating or retargeting state. The user should use `track` for that.
- Group composition should let a user keep a stable entrypoint such as `host/arch-niri` without manually listing every lower-level group.
- Examples:
  - `dotman push --dry-run git`
  - `dotman push -d`
  - `dotman push --full-path git`
  - `dotman push git`
  - `dotman push`

## Pull

- `pull` is the live-to-repo command for already modeled targets.
- `pull` should operate only on tracked state.
- `pull` should accept `-d` / `--dry-run` as an explicit preview-only mode selector.
- Plain `pull` should perform real execution after planning, interactive exclusion, and diff review.
- `pull` should accept `--full-path` to disable human-output path compaction for preview, selection, review menus, and human execution output.
- `pull <binding>` should resolve against tracked bindings and reuse the tracked profile/local context instead of prompting for a fresh profile choice.
- `pull <package>` should also work when that package is currently included through a tracked higher-level binding; dotman should reuse the owning tracked profile in that case.
- If a package selector matches multiple tracked `multi_instance` package instances, interactive mode should prompt for the specific instance and non-interactive mode should fail with the candidates.
- `pull` with no binding should replay the current tracked bindings from persisted state.
- If the requested selector is not currently tracked, `pull` should fail instead of implicitly creating state. The user should use `track` first.
- `pull` should first build a reverse-sync plan before changing any sources.
- In interactive mode, `pull` should present one combined selection menu for pending non-noop target actions so the user can exclude specific items before execution.
- Executable hooks should be derived only after tracked target winners are resolved and after the interactive exclusion menu is applied.
- A binding that no longer owns any non-noop targets after those filters should not contribute executable hooks.
- After the interactive selection menu, `pull` should enter an inspection-only diff review stage before continuing.
- After diff review accepts, `pull` should execute immediately in one package-oriented timeline.
- The `pull` diff preview should compare planning views, meaning `pull_view_repo` against `pull_view_live`.
- The interactive diff review stage should stay inspection-only in v1.
- Future edit-mode work belongs in [`docs/edit-mode-v2.md`](/home/xian/projects/dotman/docs/edit-mode-v2.md), not in the v1 review contract.
- Diff review should use `git diff --no-index --color=auto`.
- Diff review headers should use explicit `repo/...` and `live/...` paths instead of opaque `before-*` or `after-*` temp names.
- Use the same diff-review path compaction rule described in the `push` section above.
- Each reviewed diff should print a compact banner before the diff output so sequential reviews do not run together.
- In interactive review, diff output should prefer Git's pager and fall back to `less -FRX -R` when the effective pager resolves to `cat`.
- Review commands should support inspecting one item, inspecting all items, opening target reconcile or an editor for supported items, continuing, or aborting.
- For plain copied files, pull planning can compare the package source directly against the live file.
- For transformed targets, pull planning should compare repo-side and live-side views.
- Default pull planning should compare:
  - repo side: `raw`
  - live side: `capture` if available, otherwise `raw`
- Template-style forward-managed targets should typically override pull planning to compare:
  - repo side: `render`
  - live side: `raw`
- Capture-style targets should typically keep repo side as `raw` and use `pull_view_live = "capture"`.
- `pull_view_repo` and `pull_view_live` define those projections explicitly when the defaults are not right.
- Only targets with detected drift should appear in the pull selection menu.
- `pull_view_repo` and `pull_view_live` must stay non-interactive and side-effect free.
- A `reconcile` command may be interactive, for example by opening an editor to reconcile repo source files against the current live output.
- `reconcile` should only run after the user selects a changed target for pull.
- If both `capture` and `reconcile` are defined, `capture` should drive planning and `reconcile` should handle the actual selected pull step.
- If a transformed target has no `reconcile`, dotman may still pull by writing repo-side content from `capture`, but `reconcile` is preferred when manual or custom merge logic is needed.
- `pull` should only touch sources owned by the current managed selection.
- Examples:
  - `dotman pull --dry-run main:git@default`
  - `dotman pull -d`
  - `dotman pull --full-path main:git@default`
  - `dotman pull main:git@default`
  - `dotman pull`

## Rollback

- `rollback` should restore managed live paths from a previously recorded snapshot.
- `rollback` should not resolve current repo manifests, tracked bindings, or profile state.
- `rollback` with no snapshot reference should target the latest restorable snapshot.
- `rollback <snapshot>` should accept either `latest`, an exact snapshot ID, or a unique leading prefix such as a date or timestamp fragment.
- If a snapshot reference matches multiple snapshots, interactive mode should prompt for one snapshot and non-interactive mode should fail with the candidates.
- `rollback` should accept `-d` / `--dry-run` as an explicit preview-only mode selector.
- `rollback` should accept `--full-path` to disable human-output path compaction for preview, review menus, and human execution output.
- Plain `rollback` should perform real execution after planning and inspection-only diff review.
- `rollback` should compare the current live state against the selected snapshot state without consulting the current repo contents.
- `rollback` should restore only the live paths recorded by the selected snapshot.
- `rollback` should not run package hooks.
- `rollback` should fail fast if the selected snapshot is missing required stored content or has an invalid manifest.
- Examples:
  - `dotman rollback`
  - `dotman rollback latest`
  - `dotman rollback 2026-04-09`
  - `dotman rollback 2026-04-09T14-22`
  - `dotman rollback --dry-run`

## Snapshot History

- `list snapshots` should list available snapshots in newest-first order.
- `list snapshots` should not require current repo resolution or tracked bindings.
- `list snapshots` should stay overview-oriented by default. It should show summary metadata, not dump every recorded path.
- Each listed snapshot should include a human-readable creation time, a copyable snapshot ref, status, and a compact count of recorded path entries.
- If a snapshot has been restored before, list output should also surface restore metadata such as restore count and most recent restore time.
- Snapshot list output should make it easy to copy a date or timestamp prefix into `rollback <snapshot>`.
- `info snapshot <snapshot>` should show detailed information for one snapshot, including recorded path entries.
- `info snapshot` should accept `latest` as a snapshot reference alias for the newest available snapshot.
- `info snapshot` should accept `--full-path` to disable path compaction in human-readable path output.
- Examples:
  - `dotman list snapshots`
  - `dotman info snapshot latest`
  - `dotman info snapshot 2026-04-09`
  - `dotman info snapshot --full-path 2026-04-09`

## Add

- `add` is reserved for future unmanaged-to-managed adoption.
- `add` should mean taking a live dotfile or directory that is not yet modeled in dotman and adding it to an existing package or a new package.
- `add` is not implemented yet.
- `import` should stay unused as a top-level command while this area is still unsettled.

## Untrack

- `untrack <binding>` should remove one tracked root binding from persisted state.
- `untrack` should be state-only in v1.
- `untrack` should not delete live files, run hooks, or infer target ownership.
- `untrack` should match tracked bindings, not current repo manifests, so stale bindings can still be untracked after repo changes.
- `untrack` should accept either `selector` or `selector@profile`.
- If the profile is omitted, dotman should untrack the unique tracked binding that matches the selector.
- Omitting the profile for a tracked `multi_instance` root selector with multiple bound profiles should be an ambiguity error.
- If the selector only names a package that is present through another tracked binding, dotman should explain which tracked bindings currently include it instead of just saying "not tracked".
- `untrack` should validate the resulting tracked binding set before writing state.
- If removing one binding would expose a tracked-target conflict among the remaining bindings, `untrack` should fail and keep state unchanged.
- `forget` may remain as a compatibility alias for `untrack` during transition, but `untrack` is the primary name.
- Repo qualification may still be omitted when the tracked binding is unique across configured repos.
- Examples:
  - `dotman untrack main:git@default`
  - `dotman untrack git`

## Tracked State

- Snapshot history is separate from tracked binding state.
- `list snapshots` and `rollback` should operate from snapshot manifests under the snapshot storage root, not from tracked binding state.

- `list tracked` should report the packages currently tracked by persisted bindings.
- `list tracked` should resolve the current binding state against the current repo manifests.
- `list tracked` should not guess from the live filesystem.
- `list tracked` should not run push/pull planning or execute render/capture commands.
- `dotman list tracked` should list tracked package identities, not collapse `multi_instance` package instances by package definition.
- `singleton` packages should be listed once by package ID.
- `multi_instance` packages should be listed once per bound instance using `package<bound-profile>`.
- `dotman info tracked <package>` should show detailed information for one currently tracked package identity.
- `dotman info tracked package<bound-profile>` should address one tracked `multi_instance` package instance.
- Package detail should include the owning repo, description, provenance entries with explicit or implicit reasons, owned targets after tracked-target winner resolution, the bound profile for `multi_instance` instances, and the effective hook commands for that package instance, even when the current push plan would be all-noop.
- Human-readable `info tracked` hook output should stay package-centric. Do not repeat a provenance binding header under `::hooks`; the package instance already implies the single bound profile/effective hook-bearing binding.
- Package lookup for `info tracked` may use the same repo-qualified and partial-selector rules as other package-oriented commands, but it should search only tracked packages.
- When tracked package lookup is ambiguous in interactive mode, `info tracked` should use the same shared selector menu as the other package-oriented commands.
- When a `multi_instance` package name matches multiple tracked instances, `info tracked` should require or select a specific `package<bound-profile>` instance instead of silently collapsing them.
- When tracked bindings resolve the same target path, explicit provenance should override implicit provenance.
- Conflicting explicit candidates should fail, and conflicting implicit-only candidates should also fail.
- In interactive `track`, dotman may offer a non-conflicting profile switch before failing.
- In interactive `track`, an implicit-only conflict may also offer promotion of a conflicting package from the requested binding into an explicit tracked binding.

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

- `push` with no selector should recompute the current package set from persisted bindings and the current repo state.
- `pull` with no selector should also recompute from persisted bindings and the current repo state.
- `track` and `untrack` should update only the persisted binding set.
- Target ownership and prune-oriented state can be added later when prune behavior is introduced.

## Host Entry Points

- Host-level entrypoints should use host meta packages for convenience.
- Groups still handle reusable composition beneath the host meta layer.
