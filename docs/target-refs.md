# Target refs

Use `target_refs` when multiple packages should reuse one real target action.

## Manifest shape

```toml
id = "beta"

[target_refs]
shared = "alpha.shared"
```

- key = local target name in current package
- value = `<package>.<target>` in same repo

## Rules

- refs may point to real targets or other refs
- planner resolves chain to one canonical real target
- cycles fail hard
- missing package/target fails hard
- local ref names may not collide with real target names in same package
- refs cannot define execution metadata
  - no `source`
  - no `path`
  - no `render` / `capture` / `reconcile`
  - no ignore rules
  - no target hooks

## Planning behavior

Given:

```toml
# alpha/package.toml
[targets.shared]
source = "files/shared.conf"
path = "~/.config/shared.conf"

# beta/package.toml
[target_refs]
shared = "alpha.shared"
```

`dotman` plans one effective target action: `alpha.shared`.

- selection/review/execution show canonical target only
- canonical target hooks run once
- package hooks still run for packages whose refs retain that canonical target

## Info output

`info tracked` shows outgoing ref chains for packages that declare refs:

```text
:: target refs
  shared -> alpha.shared
```

Daisy-chain refs show full chain:

```text
:: target refs
  shared -> beta.shared -> alpha.shared
```
