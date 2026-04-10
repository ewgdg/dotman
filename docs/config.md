# dotman Config

This document captures the user-level dotman manager configuration.

## Config Path

- Dotman should read user config from `$XDG_CONFIG_HOME/dotman/config.toml`.
- If `XDG_CONFIG_HOME` is unset, dotman should fall back to `~/.config/dotman/config.toml`.

## Repos

- User config defines the available dotman repos, not the package/group/profile schema inside a repo.
- Repos should be declared under `[repos.<name>]`.
- Each repo entry must define `path`.
- Repos may define `state_path` to override where binding state is stored.
- Repos should define `order`, where lower values are searched first.
- Repo `order` values should be unique; ties should fail config validation.
- If `state_path` is omitted, dotman may default it from the repo name under `$XDG_STATE_HOME/dotman/`.
- Repo names should be treated as stable identifiers, because dotman also uses the repo name to locate per-repo local overrides under XDG config.

## Local Overrides

- Machine-local or private overrides should not live in the repo.
- Dotman should read optional per-repo local overrides from `$XDG_CONFIG_HOME/dotman/repos/<repo-name>/local.toml`.
- If `XDG_CONFIG_HOME` is unset, dotman should fall back to `~/.config/dotman/repos/<repo-name>/local.toml`.
- Local override files are scoped to one configured repo.
- In v1, per-repo local overrides are limited to `[vars]` data.
- Local overrides should not redefine package structure or behavior such as targets, hooks, dependencies, groups, or profiles.
- Unknown top-level keys in a local override file should fail validation.
- A missing local override file means that repo has no machine-local overrides.
- If a repo is renamed in manager config, its local override path should move with the new repo name.

## Local Override Loading

- Dotman should derive the local override path from the configured repo name, not from the repo filesystem path.
- Dotman should load at most one local override file for a repo.
- Dotman should attempt local override loading only after the manager config resolves the repo set.
- A missing local override file is normal and should not produce a warning.
- A present but unreadable or malformed local override file should fail fast.
- Local override loading should be independent per repo; one repo's local file should not affect another repo.
- Local override data should participate only in variable resolution.
- Local override data should not change tracked binding identity.

## Snapshots

- Snapshot config is manager-level and applies to real `push` execution across the whole dotman run, even when that run spans multiple repos.
- Snapshot settings should be declared under `[snapshots]`.
- `enabled` is optional and defaults to `true`.
- `path` is optional and overrides the snapshot storage root.
- If `path` is omitted, dotman should default snapshot storage to `$XDG_DATA_HOME/dotman/snapshots/`.
- If `XDG_DATA_HOME` is unset, dotman should fall back to `~/.local/share/dotman/snapshots/`.
- `max_generations` is optional, must be a positive integer, and defaults to `10`.
- `max_generations` is count-based retention. Dotman should prune the oldest snapshots when the retained snapshot count exceeds that limit.
- Snapshot storage is distinct from repo binding state. Snapshots belong under data home, while tracked binding state stays under state home.

Example:

```toml
[repos.main]
path = "~/projects/dotfiles"
order = 10

[repos.test]
path = "~/sandbox/dotfiles"
order = 20

[snapshots]
enabled = true
max_generations = 10
```

Example per-repo local override:

```toml
[vars]
INSTALL = "printf 'install %s\\n'"

[vars.git]
user_email = "local@example.test"

[vars.nvim]
colorscheme = "industry"
```
