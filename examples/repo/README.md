# Example Repo

This example shows one possible repository layout for the new `dotman` design.

## Layout

- `packages/`: installable packages
- `groups/`: composable package selectors
- `profiles/`: variable sets used during resolution
- `scripts/`: repo-wide helper scripts shared by packages
- `local.example.toml`: example machine-local vars; real machine-local overrides live under XDG config, not in the repo
- package source trees can use `.gitignore` to exclude files from install

## Example Packages

- `note`: minimal single-file package example for quick starts
- `git`: base Git package with ordered push hooks and a profile-selected simulated install command
- `core-cli-meta`: meta-package example that uses `depends = ["git", "nvim"]`
- `depends` may reference groups as well as packages.
- Meta packages are one common way to aggregate reusable group selectors, but this is not special-case behavior.
- Meta packages are still normal packages. They can be tracked explicitly.
- For terminology, see ArchWiki's distinction between meta packages and package groups: <https://wiki.archlinux.org/title/Meta_package_and_package_group>
- `profile-note`: minimal `binding_mode = "multi_instance"` example with one profile-bound target path
- `work/git`: namespaced variant that uses `extends = ["git"]` and overrides only work-specific vars
- `nvim`: example file target with stdout-based `render`, explicit reverse-sync views, and an interactive tty-backed `reconcile` step

## Example Groups

- `groups/base/cli.toml`: selects `core-cli-meta` through `members`
- `groups/os/arch.toml`: composes another group by including `base/cli` through `members`
- Groups are selection/composition helpers, not tracked identities.
- Tracking a group should behave like tracking each resolved member package with same profile.

## Example Profiles

- `profiles/os/linux.toml`: base Linux install context
- `profiles/os/mac.toml`: base macOS install context
- `profiles/basic.toml`: includes `os/linux` and adds shared profile vars
- `profiles/work.toml`: includes `os/mac` and adds work-oriented profile vars

Command strings in the example use explicit runners like `sh ...` rather than
relying on executable bits. The example `INSTALL` commands are intentionally
safe `printf` stubs so plain `push` can execute without trying to install real
system packages.

For a configured repo named `example`, the real machine-local override path is:

- `$XDG_CONFIG_HOME/dotman/repos/example/local.toml`
- fallback: `~/.config/dotman/repos/example/local.toml`

In v1, that local file is limited to `[vars]` overrides.

Design rules and current semantics live in:

- `../../docs/config.md`
- `../../docs/repository.md`
- `../../docs/templates.md`
- `../../docs/cli.md`
- `../../docs/snapshot.md`
