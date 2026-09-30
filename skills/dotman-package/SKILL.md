---
name: dotman-package
description: Write or edit dotman `package.toml` manifests. Use when tracking an app's config with dotman, adding or changing a target, or filtering noise (machine state, caches, window geometry, secrets) out of a synced settings file.
---

# Writing dotman packages

A package lives at `packages/<id>/package.toml`; its repo-side files live under `packages/<id>/files/`. Most settings files mix **portable** preferences with **noise**: machine state, caches, recent lists, window geometry, timestamps, account IDs, secrets. The job is to sync the portable part and leave the noise live-local.

## Steps

1. **Learn the repo's conventions.** Read `repo.toml`, a few sibling packages, and profile vars. Many repos define command shorthands such as `JSON_RENDER` / `JSON_CAPTURE`; reuse them instead of spelling the commands out. Done when you can name the repo's existing pattern for a filtered file, or have confirmed it has none.
2. **Declare the target.** Follow `dotman add` naming: key `f_<path_snake>` for files and `d_<path_snake>` for directories; `source` mirrors the live path under `files/` with leading dots dropped (`~/.config/app/x.json` → `files/config/app/x.json`); `path` uses `~/...` under home. Add `chmod = "600"` when the file holds anything private. If the repo source does not exist yet (first Pull creates it), set `type = "file"`.
3. **Classify every key in the live file** as portable or noise. Read the real file; ask the user about any key whose nature is unclear. Done when every top-level key (and every nested section you plan to split) has a verdict.
4. **Pick the lowest tier that expresses the split:**
   - Whole file portable → plain target, no projection.
   - Noise is whole files or subtrees of a directory target → `ignore.patterns` (Git ignore syntax) or `path_rules`.
   - Noise is keys inside one JSON/YAML/TOML/plist/XML file → **transform pair** (below).
   - The split depends on values, not key paths (e.g. render `null` instead of `""`) → Jinja template or a package-local script under `scripts/`.
5. **Choose allowlist or denylist** and write the render/capture pair from the table below. Put the selector list in `[vars.<package>]` with a comment per group saying why it syncs or stays local.
6. **Verify the round-trip** by running the capture and render commands by hand against the real live file (substitute the paths for `$DOTMAN_LIVE_PATH` / `$DOTMAN_REPO_PATH`, output to stdout):
   - Capture output holds every portable key and zero noise keys.
   - Render output equals the live file except for the portable keys taken from the repo.
   - Capturing the rendered output reproduces the repo file.
   Then preview with `dotman --unattended sync --dry-run <repo>:<package>.<target>`. Leave real `push` / `pull` / `sync` to the user.

## Transform pair

`render` builds the live file (repo → live); `capture` builds the repo file (live → repo). Both use the **live file as base**, so noise never enters the repo and is never overwritten on the machine. The `--selector-type` flips between the two commands:

| Strategy | Selectors list | `render` (`--mode merge`) | `capture` (`--mode cleanup`) |
| --- | --- | --- | --- |
| **Allowlist** | portable keys | `--selector-type remove` | `--selector-type retain` |
| **Denylist** | noise keys | `--selector-type retain` | `--selector-type remove` |

- **Allowlist** when the file is mostly app state (e.g. `~/.claude.json`, Electron app data). New upstream keys stay local by default. Deleting a synced key in the repo deletes it live.
- **Denylist** when the file is mostly preferences with a few noisy keys. New upstream keys sync by default.

Allowlist JSON example:

```toml
[vars.app]
# Everything else in app.json is window/session state and stays live-local.
synced_selectors = ["theme", "editor.fontSize", "keybindings"]

[targets.f_config_app_app_json]
source = "files/config/app/app.json"
path = "~/.config/app/app.json"
render = 'dotman transform json "$DOTMAN_LIVE_PATH" --stdout --mode merge --overlay-file "$DOTMAN_REPO_PATH" --compare-file "$DOTMAN_LIVE_PATH" --selector-type remove --selectors {{ vars.app.synced_selectors|shell_args }}'
capture = 'dotman transform json "$DOTMAN_LIVE_PATH" --stdout --mode cleanup --compare-file "$DOTMAN_REPO_PATH" --selector-type retain --selectors {{ vars.app.synced_selectors|shell_args }}'
```

Why each piece is there:

- `--compare-file` reuses the existing bytes when content is semantically equal, so formatting churn never shows up as a change. Render compares against live; capture compares against repo.
- `{{ list|shell_args }}` quotes each selector as exactly one argument. Always pass selector lists through it.
- Home paths in values: pipe the repo file through `dotman rewrite home expand "$DOTMAN_REPO_PATH" |` and use `--overlay-file -` in render; pipe the live file through `dotman rewrite home collapse "$DOTMAN_LIVE_PATH" |` and use base `-` in capture. The repo then stores `~/...`.
- The default `compare` settings (`compare.repo = "raw"`, `compare.live = "capture"`) are already right for a transform pair; leave them unset.

## Selector gotchas

- Unprefixed selectors are exact dotted key paths; quote a segment that contains a dot (`'"files.exclude".node_modules'`). `re:` selectors are Python regex searches over the full dotted path.
- Selecting a mapping selects its whole subtree. Lists are atomic: you cannot filter items inside an array, so pick the whole list or split at its parent.
- `toml` and `xml` require at least one selector; `json`, `yaml` and `plist` treat no selectors as identity.
- `plist` needs `--output-format binary` when the app stores binary plists.
- XML selectors are `fnmatch`-style element paths, and merge pairs repeated siblings by `id`/`name`/`key`/`uuid`. Use `--sort-children` for lists whose order the app shuffles.
- Apps that hold the file in memory rewrite it on exit; note in a comment that push should run while the app is closed.
- Put secrets in env vars or a local override, never in a synced key. If a secret key shares a subtree with portable keys, move the boundary down so the secret stays out.

Full flags: `dotman transform <json|yaml|toml|plist|xml> --help`. Manifest reference: [repository model](https://github.com/ewgdg/dotman/blob/main/docs/repository.md), [transforms](https://github.com/ewgdg/dotman/blob/main/docs/cli.md#structured-json-transforms), [templates](https://github.com/ewgdg/dotman/blob/main/docs/templates.md).
