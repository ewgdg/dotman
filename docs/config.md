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
