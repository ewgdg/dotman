# Trackable Catalog

`dotman list trackables` is the discovery command for repo definitions.

## What it shows

- every package definition across configured repos
- every group definition across configured repos
- canonical `repo:selector` labels
- package `binding_mode`
- group member count

## What it does not do

- does not consult tracked state
- does not search or rank by query
- does not inspect the live filesystem

## Examples

- `dotman list trackables`
- `dotman search git` for ranked lookup instead of catalog listing
