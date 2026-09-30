---
name: dotman
description: Reference index for the dotman dotfile manager. Use when writing or editing dotman manifests (package.toml, repo.toml, profiles, groups), filtering noisy app state out of synced settings files, running dotman commands, or changing dotman itself.
---

# dotman

This file is an index. Read only the entries your task needs.

## Where the docs live

- `docs/...`, `CONTEXT.md` and `examples/...` are paths in the dotman repo. Inside a dotman checkout, read them locally. Elsewhere, fetch `https://raw.githubusercontent.com/ewgdg/dotman/main/<path>`.
- `§ Heading` names a section inside that file.
- `references/...` files ship with this skill.
- For flags, `dotman <command> --help` from the installed CLI is authoritative.

## Operating rules

- Run commands without prompts using `dotman --unattended ...`, and add `--json` when you parse the output.
- Preview with `--dry-run`. Run `push`, `pull`, `sync` and `restore` for real only when the user asks: they write to the user's machine or repo.
- Leave elevation (sudo) prompts to the user.
- Name targets as `repo:package.target` and package instances as `repo:package<instance>.target`.

## Index

### Concepts

| Need | Read |
| --- | --- |
| Domain vocabulary (Package, Target, Profile, Proposal, Sync Base, ...) | `CONTEXT.md` |
| Repo layout: packages, groups, profiles, vars, local overrides | `docs/repository.md` § Core Objects, § Resolution Model |
| A complete example repo | `examples/repo/` |

### Writing manifests

| Need | Read |
| --- | --- |
| Adopt an existing live file into a package | `docs/cli.md` § Add |
| Target fields: `path`, `type`, `chmod`, `sync_policy`, `probe`, `path_rules` | `docs/repository.md` § Targets |
| Package instances and inheritance | `docs/repository.md` § Package Identity Modes, § Package Inheritance |
| Skip files or subtrees in directory targets | `docs/repository.md` § Unified exclusions |
| Sync only part of a JSON/YAML/TOML/plist/XML settings file | `references/noise-filtering.md` |
| `render`, `capture`, `editor`, `compare` fields | `docs/repository.md` § Projection and Editor configuration |
| Per-machine values with Jinja templates | `docs/templates.md` |
| Hooks, install/update probes, elevation, hook env vars | `docs/repository.md` § Hooks And Commands |
| `~` vs absolute home paths inside file content | `docs/cli.md` § Home Path Rewrites |
| Local per-machine overrides | `docs/config.md` § Local Overrides |

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

### Changing dotman itself

| Need | Read |
| --- | --- |
| Contributor rules, code layout | `AGENTS.md`, `docs/code-structure.md` |
| Past design decisions | `docs/adr/` |
