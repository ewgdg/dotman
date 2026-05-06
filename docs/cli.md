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
- The canonical repo-qualified selector+profile form is `repo:selector@profile`.
- The canonical tracked package-instance form for a `multi_instance` package is `repo:package<profile>`.
- The canonical human-facing target form is `repo:package.target`.
- The canonical human-facing package-instance target form is `repo:package<profile>.target`.
- `/` belongs inside selector IDs for namespacing, for example `work/git` or `os/arch`.
- `<...>` is reserved for resolved tracked package instances, not selector+profile forms or manifest IDs.
- `.` is reserved as the package/target separator in human-facing target labels.
- When a menu, confirmation, diff banner, list output, or info view includes repo context, it should print the canonical colon-qualified form.
- Slash-qualified repo input such as `repo/selector@profile` may remain accepted as a lookup alias for convenience, but dotman should normalize displays back to `repo:selector@profile`.
- `\` is not a valid selector separator or menu-display form.

## Confirmation and execution flags

- `--yes` skips confirmation prompts that already have a safe default, but it does not auto-resolve ambiguous selector/profile menus.
- Dotman also exports `DOTMAN_ASSUME_YES=1` to hooks during execution when `--yes` is active, otherwise `DOTMAN_ASSUME_YES=0`.
- `--run-noop` is only meaningful for `push` and `pull`.
- `--run-noop` now feeds normal planning and selection instead of reviving hooks late in execution.
- For the active operation, `--run-noop` temporarily treats all package hooks as noop-eligible, even if they do not declare `run_noop = true` in the manifest.
- `--run-noop` still does not fabricate target writes or snapshots.
- A `guard_*` hook that exits `100` soft-skips that package scope and lets later packages continue.
- Human execution output should show a guard skip as `skipped (guard)`.

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
- Manager config may override the default under `[ui.menus]`, and `DOTMAN_MENU_BOTTOM_UP` should still act as a single-run override.

## Search

- `dotman search <query>` is the selector-discovery command.
- It should search package IDs, group IDs, and descriptions across configured repos.
- Search is read-only and does not mutate tracked state.
- For full catalog browsing, use `dotman list trackables`.
- Exact repo-qualified or bare selector matches should rank ahead of prefix, substring, and description matches.
- Human output should render the selector as a badge-style label like `[package]` or `[group]`, followed by the canonical `repo:selector` form and a dim description in parentheses.
- JSON output should include the operation name, original query, and the ordered `matches` list.
- An empty query should fail fast.

## Profiles

- Profiles are separate from selection.
- Profiles provide variable values used during resolution.
- Profiles may include other profiles.
- The selected top-level profile is the bound profile.
- Persisted tracked package entries should store the top-level profile the user selected, not the flattened leaf profile set.
- The composed/effective profile is runtime resolution context, not tracked package identity.
- The same package or group should be applicable with different profiles.

## Track Requests

- The CLI should treat user input as a track request: `repo:selector@profile`.
- A track request combines what to manage with which variable context to resolve it under.
- Examples:
  - `main:git@default`
  - `test:os/arch@basic`
- Repo qualification is the explicit and stable form.
- CLI input may still omit the repo name when dotman can resolve it from configured repo search.
- Profile should not be encoded into repository paths or package/group IDs.
- A tracked `multi_instance` package instance is addressed as `package<bound-profile>` in tracked-package views.
- `track` is command that creates or replaces persisted explicit package entries.
- `push` with no selector and `pull` with no selector should replay persisted explicit package entries.
- `push` and `pull` should resolve profile includes from the current repo state each time, not from stored leaf profiles.
- `selector@profile` is a convenient shorthand when repo resolution is unambiguous.
- A bare selector such as `git` or `os/arch` may still be accepted for interactive commands.
- `track` may resolve a bare or partial selector first, then prompt for a profile interactively.
- If there is exactly one available profile and the user did not type a partial profile query, dotman may use it directly.
- In non-interactive mode, a missing profile should be an error instead of a hidden guess.

## Track

- `track <selector[@profile]>` should resolve the track request, prompt for a profile when needed, and persist the resulting explicit package entries into repo state.
- If the input selector is a group, `track` should expand it exactly as if the user had listed each resolved package selector explicitly.
- Persisted tracked package state should never keep historical group selectors; after tracking, state should contain package selectors only.
- Re-tracking the same effective package entry should update that entry instead of appending a duplicate.
- A tracked `singleton` package should have at most one explicit package entry per repo and package ID.
- A tracked `multi_instance` package may keep one explicit package entry per bound profile.
- If tracking would replace an existing explicit package entry with a different profile, interactive mode should ask for confirmation before writing state.
- In non-interactive mode, profile-replacing `track` should fail instead of silently overwriting that explicit package entry.
- Tracking a `multi_instance` package with a different bound profile should add a distinct explicit package entry instead of replacing the existing one.
- If `track` would make a new explicit package entry override existing implicit targets, interactive mode should ask for confirmation before writing state.
- In non-interactive mode, `track` should fail instead of silently overriding implicit tracked targets.
- `track` should validate the future expanded tracked graph before writing. A singleton dependency cannot be implicitly required by different tracked roots under different profiles unless one explicit singleton dependency profile owns that package identity.
- To fix an implicit dependency profile ambiguity, use the same profile for the roots, make the dependency `multi_instance`, explicitly track the desired singleton dependency profile, or move the overlapping target/config into the shared dependency package.
- `track` is state-only in v1. It should not run repo-to-live work by itself.
- Tracked target ownership is metadata-first: one live path may have one winning package instance. Same-precedence package instances that declare the same live path conflict even if their current rendered bytes would match.
- If multiple packages need the same live path, move that target into a shared dependency package instead of duplicating the target declaration.
- Examples:
  - `dotman track main:git@default`
  - `dotman track git`

## Push

- `push` is the repo-to-live command.
- `push` should operate only on tracked package state.
- `push` should accept `-d` / `--dry-run` as an explicit preview-only mode selector.
- Plain `push` should perform real execution after planning, interactive exclusion, and diff review.
- `push` should accept `--full-path` to disable human-output path compaction for preview, selection, review menus, and human execution output.
- `push` should accept `--yes` for the confirmation prompts that already have a safe default.
- `push` should accept `--run-noop` so hook-bearing packages still execute when the finalized selected plan has only noop target steps.
- `push <selector>` should resolve only within tracked package state and reuse the tracked profile instead of prompting for a fresh profile.
- Because groups are not tracked identities, tracked-package-state selector lookup for `push`, `pull`, `info tracked`, and `untrack` should resolve against tracked packages, not historical group names.
- `push <package>` should also work when that package is currently included through another tracked explicit package entry; dotman should reuse the owning tracked profile in that case.
- If a package selector matches multiple tracked `multi_instance` package instances, interactive mode should prompt for the specific instance and non-interactive mode should fail with the candidates.
- `push` with no selector should replay the current explicit package entries from persisted state without changing that tracked package set.
- `push` should fail before target planning if expanded tracked state contains ambiguous implicit singleton dependency profile contexts. Explicit singleton dependency entries suppress other implicit profile contexts for that package identity.
- If group membership or package `depends` change in the repo, `push` should pick up newly introduced managed packages and files.
- `push` should only touch files within the current managed selection.
- In interactive mode, `push` should present one combined selection menu for pending non-noop target actions plus synthetic repo/package/target hook-only rows when noop-eligible hook work survives without a normal executable anchor.
- Executable hooks should be derived only after tracked target winners are resolved and after the interactive exclusion menu is applied.
- An explicit package entry that no longer owns any non-noop targets after those filters should not contribute executable hooks unless its package hooks are retained as standalone noop-eligible package work.
- Synthetic hook-only selection rows should stay owner-scoped, not per-hook command rows. Supported rows are repo (`[hooks] repo`), package (`[hooks] repo:package`), and target (`[hooks] repo:package.target`).
- After the interactive selection menu, `push` should enter an inspection-only diff review stage before continuing.
- After diff review accepts, `push` should execute in nested repo/package/target order so repo and target hooks keep their real scope boundaries.
- Before the first live mutation of a real `push`, dotman should create one manager-level snapshot for the finalized selected plan.
- That snapshot should record enough state to restore the mutated paths later.
- If the finalized `push` work is hook-only, dotman should not create a snapshot.
- If package guards soft-skip before the first live mutation, dotman should keep going and create the snapshot only when the first real mutation is about to begin.
- `file_symlink_mode = prompt` means interactive replace is allowed; `follow` means dotman writes through to the resolved target.
- `dir_symlink_mode = fail` rejects symlinked directory roots; `follow` means dotman manages the resolved tree.
- `push --dry-run` should not create a snapshot.
- Default symlink policy should be `file_symlink_mode = prompt` and `dir_symlink_mode = fail`; CLI flags can override either one for a single run.
- `push` should fail fast when the active mode does not allow the live symlink shape.
- If a real `push` fails after snapshot creation, dotman should keep that snapshot so the user can inspect it or roll back manually.
- The interactive diff review stage should stay inspection-only in v1.
- Future edit-mode work belongs in [`docs/edit-mode-v2.md`](./edit-mode-v2.md), not in the v1 review contract.
- Diff review should use `git diff --no-index --color=auto`.
- Diff review headers should use explicit `live/...` and `repo/...` paths instead of opaque `before-*` or `after-*` temp names.
- Diff review headers should compact long compared paths for readability, and should additionally collapse the current home directory to `~` instead of a machine-specific absolute prefix.
- Each reviewed diff should print a compact banner before the diff output so sequential reviews do not run together.
- Each reviewed diff should print the destination path under the banner so directory-target child diffs are identifiable even when the target label names the directory root.
- Mode-only diff review should follow Git semantics: show executable-bit changes through `git diff` mode lines, but do not show or plan non-Git permission drift such as `600` vs `644` for directory-target child files.
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
- `pull` should operate only on tracked package state.
- `pull` should accept `-d` / `--dry-run` as an explicit preview-only mode selector.
- Plain `pull` should perform real execution after planning, interactive exclusion, and diff review.
- `pull` should accept `--full-path` to disable human-output path compaction for preview, selection, review menus, and human execution output.
- `pull` should accept `--yes` for the confirmation prompts that already have a safe default.
- `pull` should accept `--run-noop` so hook-bearing packages still execute when the finalized selected plan has only noop target steps.
- `pull <selector>` should resolve against tracked packages and reuse the tracked profile/local context instead of prompting for a fresh profile choice.
- `pull <package>` should also work when that package is currently included through another tracked explicit package entry; dotman should reuse the owning tracked profile in that case.
- If a package selector matches multiple tracked `multi_instance` package instances, interactive mode should prompt for the specific instance and non-interactive mode should fail with the candidates.
- `pull` with no selector should replay the current explicit package entries from persisted state.
- `pull` should use the same expanded tracked-state validation as `push`, including singleton dependency profile ambiguity detection before target planning.
- If the requested selector is not currently tracked, `pull` should fail instead of implicitly creating state. The user should use `track` first.
- `pull` should first build a reverse-sync plan before changing any sources.
- In interactive mode, `pull` should present one combined selection menu for pending non-noop target actions plus synthetic repo/package/target hook-only rows when noop-eligible hook work survives without a normal executable anchor.
- Executable hooks should be derived only after tracked target winners are resolved and after the interactive exclusion menu is applied.
- An explicit package entry that no longer owns any non-noop targets after those filters should not contribute executable hooks unless its package hooks are retained as standalone noop-eligible package work.
- Synthetic hook-only selection rows should stay owner-scoped, not per-hook command rows. Supported rows are repo (`[hooks] repo`), package (`[hooks] repo:package`), and target (`[hooks] repo:package.target`).
- After the interactive selection menu, `pull` should enter an inspection-only diff review stage before continuing.
- After diff review accepts, `pull` should execute in nested repo/package/target order so repo and target hooks keep their real scope boundaries.
- A `guard_pull` hook that exits `100` soft-skips that package scope and lets later packages continue.
- The `pull` diff preview should compare planning views, meaning `pull_view_repo` against `pull_view_live`.
- The interactive diff review stage should stay inspection-only in v1.
- Future edit-mode work belongs in [`docs/edit-mode-v2.md`](./edit-mode-v2.md), not in the v1 review contract.
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
- For the current built-in Jinja patch-capture workflow, use:
  - `render = "jinja"`
  - `capture = "patch"`
  - `pull_view_repo = "render"`
  - `pull_view_live = "raw"`
- Capture-style targets should typically keep repo side as `raw` and use `pull_view_live = "capture"`.
- `pull_view_repo` and `pull_view_live` define those projections explicitly when the defaults are not right.
- Only targets with detected drift should appear in the pull selection menu.
- `pull_view_repo` and `pull_view_live` must stay non-interactive and side-effect free.
- A `reconcile` command may be interactive, for example by opening an editor to reconcile repo source files against the current live output.
- For editor-backed reconcile helpers, dotman should prefer transactional editing: review scratch files stay readonly, editable buffers should be temporary copies, and dotman should ask for confirmation before writing those edits back to repo sources.
- `reconcile` should only run after the user selects a changed target for pull.
- If both `capture` and `reconcile` are defined, `capture` should drive planning and dotman should attempt the actual pull through `capture` first.
- If that capture attempt fails, dotman should retry the selected pull step through `reconcile` using the same review projections.
- If a transformed target has no `reconcile`, dotman may still pull by writing repo-side content from `capture` alone.
- `pull` should only touch sources owned by the current managed selection.
- Managed target paths should keep the declared pathname as identity instead of silently following a live symlink to a different path.
- `pull` may read through a symlinked declared live path, but it should still treat the declared pathname as the managed target identity.
- Examples:
  - `dotman pull --dry-run main:git@default`
  - `dotman pull -d`
  - `dotman pull --full-path main:git@default`
  - `dotman pull main:git@default`
  - `dotman pull`

## Capture

- `capture` is the helper namespace for built-in reverse-capture tools.
- `capture patch` is the built-in automatic patch helper for patchable rendered/template file targets.
- `capture patch` should accept `--repo-path`, `--render`, `--review-repo-path`, `--review-live-path`, and the same template-context flags currently used by `render jinja` (`--profile`, `--os`, and repeated `--var`).
- `capture patch` should output the patched repo source to stdout.
- `capture patch` reprojects the patched repo file through the forward render path and must match the reviewed live bytes exactly.
- If that verification fails, `capture patch` exits non-zero; `pull` stops the current package and skips later packages instead of applying an unverified patch.
- The built-in target helper should reuse the same implementation as `dotman capture patch`.
- Use `capture = "patch"` for automatic template-style reverse capture when dotman can patch source deterministically and verify the result; use `reconcile` when a human needs to inspect or edit source reconciliation manually.

## Rollback

- `rollback` should restore managed live paths from a previously recorded snapshot.
- `rollback` should not resolve current repo manifests, current tracked package entries, or profile state.
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
- `list snapshots` should not require current repo resolution or tracked package entries.
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

- `add` is the unmanaged-to-managed adoption command for creating or updating repository package config from an existing live path.
- `add` should accept `dotman add <live-path> [<repo>:]<package-query>`.
- The live path must be the first positional argument so relative paths such as `.config/nvim/init.lua` do not collide with namespaced package IDs such as `work/git`.
- `live-path` may be relative, `~`-prefixed, or absolute.
- Relative `live-path` input should resolve against the current working directory.
- `add` should fail fast if the resolved live path does not exist.
- `add` should fail fast on symlinks in v1.
- `add` should auto-detect whether the live path is a file or directory.
- If the optional package query is provided, dotman should resolve it against package IDs across configured repos using the same repo-aware exact and partial lookup model used elsewhere.
- Package queries may be package-only such as `git`, repo-qualified such as `main:git`, or partial forms such as `ma:gi` for interactive resolution.
- Interactive package resolution for `add` should include a synthetic create option as the first menu entry.
- If the package query is omitted, `add` should be interactive-only and should present a package picker across all repos, with `create a new package` as the first option.
- In non-interactive mode, omitting the package query should be an error instead of a hidden guess.
- Selecting create should always end with an explicit final repo and explicit final package ID before writing any files.
- Partial package text may help find an existing package, but dotman should not silently create a package from an unresolved partial query.
- If the selected package does not exist yet, `add` should create `packages/<package-id>/package.toml`, including namespaced package paths such as `packages/work/git/package.toml`.
- If the selected package already exists, `add` should update only that package's `package.toml`.
- `add` should not modify tracked package state.
- `add` should not run `push`, `pull`, hooks, or any repo-to-live or live-to-repo execution.
- `add` should not copy the live file or directory into the repo in v1; this phase is manifest-only.
- `add` should derive a deterministic target key from the live destination path.
- Generated target keys should use `f_` for file targets and `d_` for directory targets.
- The rest of the generated key should be lowercase path-derived snake_case text with punctuation normalized to underscores.
- If the generated target key already exists in the same package, `add` should append a numeric suffix such as `_2`, `_3`, and so on until the key is unique.
- If the same live `path` is already declared by another target in the same package, `add` should fail instead of silently replacing it.
- `add` should store the target `path` as `~/<...>` when the live path is under `$HOME`.
- `add` should store the target `path` as an absolute path when the live path is outside `$HOME`.
- `add` should derive the repo `source` path by mirroring the live path under `files/`.
- For source derivation, dotman should drop the home directory prefix when the live path is under `$HOME`; otherwise it should drop only the leading `/`.
- For every mirrored source path component, dotman should remove any leading `.` character, including for paths outside `$HOME`.
- Examples:
  - `~/.gitconfig` -> `files/gitconfig`
  - `~/.config/nvim/init.lua` -> `files/config/nvim/init.lua`
  - `/etc/.ssh/ssh_config` -> `files/etc/ssh/ssh_config`
- `add` should inspect the actual live file mode and include `chmod` only when the mode is unusual.
- For v1, the usual default modes are:
  - regular file: `644`
  - directory: `755`
- If the live mode differs from the usual default for that target kind, `add` should write the actual mode into `chmod`, for example `600` or `700`.
- In interactive mode, `add` should open an editor review when an editor is available.
- That editor review should open a diff view of the old and generated `package.toml` content, and also open an editable temporary `package.toml` copy.
- `add` should not write the edited manifest back to the repo until the editor exits and the user confirms the write.
- The editor review for `add` should not open the live path itself.
- Examples:
  - `dotman add ~/.gitconfig git`
  - `dotman add .config/nvim/init.lua main:nvim`
  - `dotman add ~/.config/gtk-3.0/settings.ini desktop/gtk`
  - `dotman add ~/.config/foo.conf`
- `import` should stay unused as a top-level command.

## Edit

- `edit package <package>` should open the tracked package directory in `$VISUAL` or `$EDITOR`.
- `edit target <target>` should open the tracked target repo-side source path in `$VISUAL` or `$EDITOR`.
- `edit target` should resolve tracked targets only.
- `edit target` should open the repo-side source file for file targets and the repo-side source directory for directory targets.
- If no editor is configured, `edit package` should print the package directory path and exit successfully.
- If no editor is configured, `edit target` should print the resolved repo-side source path and exit successfully.
- `edit package` should use the tracked-package selector flow, so bare and repo-qualified package queries follow the same ambiguity rules as `info tracked`.
- `edit target` should accept explicit target queries in the form `[<repo>:]<package>.<target>`.
- `edit target` may also accept bare target-name queries when they resolve uniquely among tracked targets.
- `edit target` should treat target identity as package-scoped, so ambiguous target-name queries must prompt interactively and fail in non-interactive or JSON mode.

## Untrack

- `untrack <selector[@profile]>` should remove one persisted explicit package entry from state.
- `untrack` should be state-only in v1.
- `untrack` should not delete live files, run hooks, or build push/pull execution plans; ownership validation should use rendered target metadata only.
- `untrack` should match persisted explicit package entries, not current repo manifests, so stale entries can still be removed after repo changes.
- `untrack` should accept either `selector` or `selector@profile`.
- If the profile is omitted, dotman should untrack the unique exact explicit package entry that matches the selector.
- Omitting the profile for a tracked `multi_instance` package selector with multiple bound profiles should be an ambiguity error.
- A single fuzzy/partial match must not execute or mutate state silently.
- In interactive mode, a single fuzzy/partial match should require explicit confirmation before dotman continues.
- In non-interactive or JSON mode, a single fuzzy/partial match should fail without guessing.
- Tracked package matches, including implicit packages, should participate in fuzzy-match ambiguity detection so `untrack` does not treat a destructive partial match as uniquely safe.
- Exact invalid or orphan explicit package entries should still be removable by matching the persisted state record.
- If the selector only names a package that is present through another explicit package entry, dotman should explain which tracked package entries currently include it instead of just saying "not tracked".
- `untrack` should validate the resulting tracked package set before writing state.
- If removing one explicit package entry would expose a tracked-target conflict among different remaining package instances, `untrack` should fail and keep state unchanged.
- Singleton package targets reached through multiple tracked roots are the same package instance and should dedupe rather than conflict.
- Repo qualification may still be omitted when the tracked package entry is unique across configured repos.
- Examples:
  - `dotman untrack main:git@default`
  - `dotman untrack git`

## Tracked Package State

- Snapshot history is separate from tracked package state.
- `list snapshots` and `rollback` should operate from snapshot manifests under the snapshot storage root, not from tracked package state.

- `list tracked` should report the packages currently tracked by persisted explicit package entries.
- `list tracked` should resolve the current tracked package state against the current repo manifests.
- `list tracked` should not guess from the live filesystem.
- `list tracked` should not run push/pull planning or execute render/capture commands.
- `list tracked` should include package-level `state` as `explicit` or `implicit`.
- `list tracked` should include invalid explicit package entries for configured repos when persisted state no longer resolves cleanly.
- `list tracked` should include orphan explicit package entries discovered under the manager state root when a persisted `state_key` no longer maps to configured repos.
- Human `list tracked` output should print one flat `repo:package-or-selector state` list.
- Human `list tracked` output should list explicit and implicit packages first, then orphan entries, then invalid entries.
- `dotman list tracked` should list tracked package identities, not collapse `multi_instance` package instances by package definition.
- `singleton` packages should be listed once by package ID.
- `multi_instance` packages should be listed once per bound instance using `package<bound-profile>`.
- `dotman list vars` should list only the winning variable occurrence per repo.
- Human `list vars` output should keep the selector+profile context visible, for example `name (repo:selector@profile)`.
- `dotman info var <var>` should show every resolved occurrence of that variable key and its provenance.
- Variable provenance should identify the winning source layer for the resolved value, such as a package, profile, or repo-local override.
- `dotman info tracked <package>` should show detailed information for one currently tracked package identity.
- `dotman info tracked package<bound-profile>` should address one tracked `multi_instance` package instance.
- Package detail should include the owning repo, description, provenance entries with explicit or implicit reasons, owned targets after tracked-target winner resolution, the bound profile for `multi_instance` instances, and the effective hook commands for that package instance, even when the current push plan would be all-noop.
- Human-readable `info tracked` hook output should stay package-centric. Do not repeat a selector+profile provenance header under `::hooks`; the package instance already implies the single bound profile/effective hook-bearing source entry.
- Package lookup for `info tracked` may use the same repo-qualified and partial-selector rules as other package-oriented commands, but it should search only tracked packages.
- When tracked package lookup is ambiguous in interactive mode, `info tracked` should use the same shared selector menu as the other package-oriented commands.
- When a `multi_instance` package name matches multiple tracked instances, `info tracked` should require or select a specific `package<bound-profile>` instance instead of silently collapsing them.
- When tracked package entries resolve the same target path, explicit provenance should override implicit provenance.
- Conflicting explicit candidates should fail, and conflicting implicit-only candidates should also fail.
- In interactive `track`, dotman may offer a non-conflicting profile switch before failing.
- In interactive `track`, an implicit-only conflict may also offer promotion of a conflicting package from the requested selector into an explicit package entry.

## State

The CLI model implies persistent state.

For v1, dotman should persist explicit package entries only.

- persisted explicit package entries only; group selectors must already be expanded
- no persisted target ownership yet
- no persisted resolved package graph yet

Tracked package state now uses `tracked-packages.toml` and `[[packages]]`, and each row represents one explicit tracked package entry.

For example:

```toml
schema_version = 1

[[packages]]
repo = "main"
package_id = "desktop/niri"
profile = "basic"

[[packages]]
repo = "test"
package_id = "git"
profile = "personal"
```

- `push` with no selector should recompute the current package set from persisted explicit package entries and the current repo state.
- `pull` with no selector should also recompute from persisted explicit package entries and the current repo state.
- `track` and `untrack` should update only the persisted explicit package-entry set.
- Target ownership and prune-oriented state can be added later when prune behavior is introduced.

## Host Entry Points

- Host-level entrypoints should use host meta packages for convenience.
- Groups still handle reusable composition beneath the host meta layer.
