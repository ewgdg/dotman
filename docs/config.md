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

Example:

```toml
[repos.main]
path = "~/projects/dotfiles"
order = 10

[repos.test]
path = "~/sandbox/dotfiles"
order = 20
```
