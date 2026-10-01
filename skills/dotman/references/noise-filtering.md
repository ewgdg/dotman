# Filtering noise from a settings file

Most app settings files mix **portable** preferences with **noise**: state the app writes for itself. Sync the portable part; keep the noise live-local. Paths like `docs/...` resolve as described in `SKILL.md` § Where the docs live.

## 1. Find the noise

Look for **churn** instead of guessing from key names:

1. Copy the live file to a scratch directory.
2. Ask the user to open, use and close the app without changing any setting. Diff against the copy: every key that changed is noise.
3. Ask the user to change one real setting. Diff again: that key is portable, and its neighbours show where settings live.
4. Classify the unchanged keys with the catalogue below. Ask the user about any key that is still unclear.

Done when every top-level key, and every mapping you plan to split, has a verdict.

| Noise kind | Examples | Treatment |
| --- | --- | --- |
| UI and session state | window bounds, panel widths, last tab, zoom | live-local |
| History | recent files, recent projects, search history | live-local |
| Counters and timestamps | launch count, last update check, "tip shown" flags | live-local |
| Machine identity | device or install IDs, hostnames, absolute project paths | live-local |
| Migration markers | schema versions, `migrated*` flags | live-local; the app rewrites them |
| Accounts and secrets | tokens, emails, org IDs | live-local; secrets never enter the repo |
| Home paths inside portable values | `/home/me/...` | portable, stored as `~/...` with the home path rewrite |
| Values that differ per OS or host | fonts, scale factors | live-local inside a transform pair; a templated value cannot round-trip through `capture` |

## 2. Pick the lowest tier that expresses the split

- Whole file portable → plain target, no projection.
- Noise is whole files or subtrees of a directory target → `ignore.patterns` (`docs/repository.md` § Unified exclusions).
- Noise is keys inside one JSON, YAML, TOML, plist or XML file → **transform pair** (below) on the file target.
- Noise is keys inside child files of a directory target → the same transform pair as `render` and `capture` on a `path_rules.<name>` entry matching those files (`docs/repository.md` § Targets).
- The split depends on values, not key paths (for example rendering `null` instead of `""`) → a Jinja template for the whole file (`docs/templates.md`) or a package-local script.

## 3. Choose allowlist or denylist

Ask: when the app adds a new key upstream, should it sync by default?

- **Denylist** (yes): a preferences file with a few noisy keys. List the noise.
- **Allowlist** (no): a file that is mostly app state, such as `~/.claude.json` or Electron app data. List the portable keys.

An allowlist cannot yet exclude keys inside a selected subtree (ewgdg/dotman#97). List the subtree's portable children one by one. A negative-lookahead regex such as `re:^settings\.(?!windowBounds)` deletes the excluded live keys when nothing else in that subtree exists live (repro in #97).

## 4. Write the transform pair

`render` builds the live file (repo → live); `capture` builds the repo file (live → repo). Both use the **live file as base**, so noise never enters the repo and is never overwritten on the machine. `--selector-type` flips between the two:

| Strategy | Selector list | `render` (`--mode merge`) | `capture` (`--mode cleanup`) |
| --- | --- | --- | --- |
| Allowlist | portable keys | `--selector-type remove` | `--selector-type retain` |
| Denylist | noise keys | `--selector-type retain` | `--selector-type remove` |

Within the synced region, the repo copy wins on push: a synced key absent from the repo is deleted live. For a denylist this includes every key the app added since the last capture, so pull or sync before pushing.

If the repo already defines shorthand vars for these commands (for example `JSON_RENDER` in a profile), reuse them. Otherwise:

```toml
[vars.app]
# Everything else in app.json is window and session state and stays live-local.
synced_selectors = ["theme", "editor.fontSize", "keybindings"]

[targets.f_config_app_app_json]
source = "files/config/app/app.json"
path = "~/.config/app/app.json"
render = 'dotman transform json "$DOTMAN_LIVE_PATH" --stdout --mode merge --overlay-file "$DOTMAN_REPO_PATH" --compare-file "$DOTMAN_LIVE_PATH" --selector-type remove --selectors {{ vars.app.synced_selectors|shell_args }}'
capture = 'dotman transform json "$DOTMAN_LIVE_PATH" --stdout --mode cleanup --compare-file "$DOTMAN_REPO_PATH" --selector-type retain --selectors {{ vars.app.synced_selectors|shell_args }}'
```

- Keep the selector list in `[vars.<package>]`, grouped, with a comment per group saying why it syncs or stays local.
- `--compare-file` reuses the existing bytes when content is semantically equal, so reformatting never shows up as a change. Render compares against live; capture compares against repo.
- `{{ list|shell_args }}` passes each selector as exactly one argument. Always pass selector lists through it.
- Home paths in values (text formats only; the rewrite requires UTF-8): in render, pipe `dotman rewrite home expand "$DOTMAN_REPO_PATH" |` and use `--overlay-file -`. In capture, pipe `dotman rewrite home collapse "$DOTMAN_LIVE_PATH" |` and use base `-`.
- Leave `compare.repo` and `compare.live` unset; the defaults already fit a transform pair.
- Add `chmod = "600"` when the live file holds anything private.
- Edit the repo file only within the synced region. A key outside it is pushed live once, then dropped by capture, and shows as changed on every sync.
- Selector syntax, list handling and per-format flags: `docs/cli.md` § Structured JSON transforms, or `dotman transform <format> --help`.

## 5. Verify the round-trip

Run both commands by hand against the real live file, substituting paths for `$DOTMAN_LIVE_PATH` and `$DOTMAN_REPO_PATH`:

- Capture output holds every portable key and no noise keys.
- Render output equals the live file except for the portable keys taken from the repo.
- Capturing the render output reproduces the repo file.

Then, with the package tracked, `dotman --unattended pull --dry-run <repo>:<package>.<target>` shows `[would-apply] repository write` for a new target, and `push --dry-run` shows the expected live change. A `sync --dry-run` on a new target only reports that it needs a first review, so it verifies nothing.

## Pitfalls

- Lists are atomic: select a whole list or split at its parent.
- An app that holds the file in memory rewrites it on exit. Note in a comment that push should run while the app is closed.
- A secret that shares a subtree with portable keys: move the selector boundary down so the secret stays out.
