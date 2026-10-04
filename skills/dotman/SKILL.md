---
name: dotman
description: dotman dotfile manager reference. Use when working in a dotfiles repo managed by dotman (`packages/*/package.toml`, `repo.toml`, profiles, groups), filtering noisy app state out of synced settings files, or running dotman commands.
---

# dotman

This file is an index. Read only the entries your task needs.

## Where the docs live

- `README.md`, `docs/...` and `examples/...` are paths in the dotman repo. A dotman checkout is a repo containing `src/dotman/`; there, read them locally.
- Elsewhere, fetch `https://raw.githubusercontent.com/ewgdg/dotman/<ref>/<path>`. Take `<ref>` from `dotman --version`: the version for a release (`0.9.1`), the hash after `+g` for a dev build (`0.10.5.dev55+ge34356e88` → `e34356e88`), or `main` if neither resolves. Docs at the wrong ref describe flags the installed CLI may lack.
- To list a directory remotely, use `gh api repos/ewgdg/dotman/contents/<dir>?ref=<ref>`.
- `§ Heading` names a section inside that file. `references/...` files ship with this skill.
- For flags, `dotman <command> --help` from the installed CLI is authoritative.

## Operating rules

- Global options go before the command: `dotman --unattended --json <command> ...`. Use `--unattended` to avoid prompts and `--json` when you parse output.
- Run freely: `list`, `info`, `doctor`, `search`, `transform` and `rewrite` to stdout, `render jinja`, and any command with `--dry-run`.
- Every other command (`push`, `pull`, `sync`, `restore`, `track`, `untrack`, `add`, `capture`, `reset`, ...) writes to the user's machine, repo or dotman state. Run it only when the user asks. `reset sync-base` has no confirmation and no dry-run.
- Leave elevation (sudo) prompts to the user.
- Name targets as `repo:package.target` and package instances as `repo:package<profile>.target`.
- A package scope (`dotman pull repo:package`) also covers the packages it depends on; add `--no-deps` to keep only its own targets (`docs/cli.md` § Sync scope resolution).
- `on_demand = true` targets run only when named exactly (`dotman push repo:package.target`); package selectors and plain `push`/`pull`/`sync` leave them out without any output (`docs/cli.md` § Sync scope resolution).

## Index

### Setup and concepts

| Need | Read |
| --- | --- |
| Install dotman, register a repo, first push | `README.md` § Install, § Quick start; `docs/config.md` § Repos |
| Packages, targets, groups, profiles, vars, resolution | `docs/repository.md` § Core Objects, § Resolution Model |
| Example repo and what each package demonstrates | `examples/repo/README.md` |
| Sync vocabulary: Proposal, Sync Base, Capture, Guard | `GLOSSARY.md` |

### Writing manifests

| Need | Read |
| --- | --- |
| Adopt an existing live file into a package | `docs/cli.md` § Add |
| Target fields: `path`, `type`, `chmod`, `sync_policy`, `on_demand`, `path_rules`; install/update `probe` targets | `docs/repository.md` § Targets |
| Package instances and inheritance | `docs/repository.md` § Package Identity Modes, § Package Inheritance |
| Skip files or subtrees in directory targets | `docs/repository.md` § Unified exclusions |
| Sync only part of a JSON/YAML/TOML/plist/XML settings file | `references/noise-filtering.md` |
| `render`, `capture`, `editor`, `compare` fields | `docs/repository.md` § Projection and Editor configuration |
| Per-machine values with Jinja templates | `docs/templates.md` |
| Hooks, elevation, hook env vars | `docs/repository.md` § Hooks And Commands |
| `~` vs absolute home paths inside file content | `docs/cli.md` § Home Path Rewrites |
| Local per-machine overrides | `docs/config.md` § Local Overrides |
| Live files that are symlinks (e.g. migrating from stow) | `docs/config.md` § Symlink Handling |

### Running dotman

| Need | Read |
| --- | --- |
| Identifiers and selectors | `docs/cli.md` § Identifier Syntax, § Selectors |
| Track and untrack packages, choose profiles | `docs/cli.md` § Track, § Untrack, § Profiles |
| Push, pull and their flags | `docs/cli.md` § Confirmation and execution flags, § Push, § Pull |
| Sync, Proposals and Sync Bases | `docs/cli.md` § Sync, then `docs/sync.md` |
| Inspect state, diagnose problems | `docs/cli.md` § Diagnostics And Catalog Inspection, § Tracked Package State |
| Snapshots and restore | `docs/snapshot.md`, `docs/cli.md` § Restore |
| User config (`~/.config/dotman/config.toml`) | `docs/config.md` |
