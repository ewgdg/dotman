# Example Repo

This example shows one possible repository layout for the new `dotman` design.

## Layout

- `packages/`: installable packages
- `groups/`: composable package selectors
- `profiles/`: variable sets used during resolution
- `scripts/`: repo-wide helper scripts shared by packages
- `local.toml`: machine-local or private overrides
- package source trees can use `.gitignore` to exclude files from install

## Example Packages

- `git`: base Git package with hook arrays and a profile-selected install command
- `core-cli-meta`: meta-package example that uses `depends = ["git", "nvim"]`
- `profiled-note`: minimal `binding_mode = "multi_instance"` example with one profile-bound target path
- `work/git`: namespaced variant that uses `extends = ["git"]` and overrides only work-specific vars
- `nvim`: example file target with stdout-based `render`, explicit reverse-sync views, and an interactive `reconcile` step

## Example Groups

- `groups/base/cli.toml`: selects `core-cli-meta` through `members`
- `groups/os/arch.toml`: composes another group by including `base/cli` through `members`

## Example Profiles

- `profiles/os/linux.toml`: base Linux install context
- `profiles/os/mac.toml`: base macOS install context
- `profiles/basic.toml`: includes `os/linux` and adds repo-level vars
- `profiles/work.toml`: includes `os/mac` and adds work-oriented vars

Command strings in the example use explicit runners like `sh ...` rather than
relying on executable bits.

Design rules and current semantics live in:

- `../../docs/config.md`
- `../../docs/repository.md`
- `../../docs/cli.md`
