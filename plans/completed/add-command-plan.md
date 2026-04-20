# `dotman add` implementation plan

Date: 2026-04-09

Updated: 2026-04-17

## Why this file exists

I previously proposed the plan inline and did not persist it under `./plans/`.
That was a miss. The agent instruction says plans should be stored in `./plans/`.

## Goal

Implement `dotman add` for unmanaged-to-managed adoption with a strict v1 scope:

- create or update only the target package manifest
- edit only `packages/.../package.toml`
- do not copy live files into the repo yet
- do not change tracked state
- do not run `push` or `pull`

## Locked command contract

```bash
dotman add <live-path> [<repo>:]<package-query>
```

Examples:

```bash
dotman add ~/.gitconfig git
dotman add .config/nvim/init.lua main:nvim
dotman add ~/.config/gtk-3.0/settings.ini desktop/gtk
dotman add ~/.config/foo.conf
```

### Parsing rule

- `live-path` is always the first positional argument.
- package query is optional and always the second positional argument.
- package omission is allowed only in interactive mode.

## Locked behavior

### Live path resolution

- accept relative, `~`-prefixed, or absolute paths
- resolve relative paths against the current working directory
- fail if the path does not exist
- fail on symlinks in v1
- detect whether the target is a file or directory
- read actual mode bits from the live filesystem

### Manifest `path`

- if the resolved live path is under `$HOME`, store it as `~/<...>`
- otherwise store it as an absolute path

Examples:

- `~/.gitconfig` -> `~/.gitconfig`
- `/etc/ssh/ssh_config` -> `/etc/ssh/ssh_config`

### Target naming

Target keys must follow `~/dotfiles/docs/target-naming-convention.md`:

- file target -> `f_...`
- directory target -> `d_...`

If the generated key already exists in the package:

- append a numeric suffix: `_2`, `_3`, ...

If the same live `path` already exists in the same package:

- fail

### Source path generation

Mirror the live path under `files/` with these transforms:

- if under `$HOME`, drop the home prefix
- otherwise drop the leading `/`
- for every path component, strip any leading `.`
- do not keep leading dots even for paths outside home

Examples:

- `~/.gitconfig` -> `files/gitconfig`
- `~/.config/nvim/init.lua` -> `files/config/nvim/init.lua`
- `~/.codex/AGENTS.codex.md` -> `files/codex/AGENTS.codex.md`
- `/etc/.ssh/ssh_config` -> `files/etc/ssh/ssh_config`
- `/opt/.foo/.barrc` -> `files/opt/foo/barrc`

### `chmod` generation

Use actual live mode only when unusual.

Omit when default:

- regular file: `644`
- directory: `755`

Include otherwise, for example:

- `600`
- `700`
- `640`

### Package resolution

If package query is provided:

- support exact package match
- support partial package match
- support partial repo + partial package match
- use interactive resolver selection when needed
- include a synthetic create option as the first menu entry

If package query is omitted:

- interactive mode: show all packages across repos plus `(create new package)`
- non-interactive mode: fail

### Create-package flow

Creation must end with an explicit final:

- repo
- package id

Do not silently create from an unresolved vague partial query.

### Editor behavior

In interactive mode after the manifest update:

- open a diff view of old `package.toml` vs new `package.toml`
- open the real editable `package.toml`
- do not open the live path

## Implementation phases

## Phase 1: tests first

Add tests for:

### CLI

- help for `dotman add <live-path> [<package-query>]`
- omitted package query is interactive-only
- unknown repo errors

### resolver behavior

- exact package resolution
- partial package resolution
- partial repo + partial package resolution
- create option appears first
- omitted package query shows all packages plus create option
- create flow prompts for repo and package id

### manifest generation

- create new package manifest
- append target to existing package manifest
- namespaced package path writes to `packages/work/git/package.toml`
- target naming follows the naming convention exactly
- duplicate key gets suffix
- duplicate target path fails
- source path strips home/root and removes leading dots from all components
- relative live path resolves from cwd
- file `644` omits `chmod`
- file `600` includes `chmod`
- directory `755` omits `chmod`
- directory `700` includes `chmod`
- symlink fails
- missing path fails

### editor workflow

- review diff is old/new manifest content
- editable file is `package.toml`
- live path is not opened

## Phase 2: add a focused implementation module

Create `src/dotman/add.py`.

Responsibilities:

- inspect and classify the live path
- normalize target key
- derive source path
- derive manifest `path`
- derive optional `chmod`
- resolve or create package target
- update manifest content
- launch post-write editor review

Keep `cli.py` thin.

## Phase 3: round-trip TOML editing

Use a round-trip TOML library instead of brittle text surgery.

Recommended dependency:

- `tomlkit`

Reason:

- preserve comments where possible
- preserve existing formatting better
- safely append `targets.<name>` entries
- avoid full-text rewrite hacks

## Phase 4: integrate the CLI subcommand

Add:

```bash
dotman add <live-path> [<package-query>]
```

Expected result payload should include:

- repo
- package id
- manifest path
- target key
- generated source
- manifest `path`
- `chmod` if written
- whether the package was newly created

## Phase 5: editor review wiring

Reuse editor discovery / launch logic patterns where useful, but the add flow should review:

- before manifest
- after manifest
- editable `package.toml`

This is not a live-file reconcile flow.

## Phase 6: documentation updates

Update at least:

- `docs/cli.md`
- `docs/repository.md`

Replace the current "add is reserved" wording with the implemented behavior.

## Non-goals for v1

- no source file copy into `files/`
- no tracked binding updates
- no push/pull execution
- no hook creation
- no symlink adoption support
