# Repository Loading and Composition

## Intent

Load repo-local manifest/config data and compose packages, profiles, groups, vars, hooks, and ignore defaults.

## Behavior

```pseudo
Repository(config):
  read repo config payload from configured root
  load default command elevation
  load repo ignore defaults, including shared skip marker basenames
  load repo hooks
  load packages, groups, profiles, and local vars

compose_profile(profile_id):
  if profile_id is missing:
    return empty/default profile composition

  resolve parent profiles first
  merge parent profile data before child profile data
  return composed profile spec

resolve_package(package_id):
  if package does not exist:
    reject unknown package

  start with base package spec

  if selected profile provides package override:
    merge override into package spec

  if package declares extensions:
    apply extension merge/remove/append rules

  return resolved package spec

package_binding_mode(package_id):
  return whether package can be bound directly, by profile, by group, or requires explicit profile/instance handling

load repo ignore defaults:
  read [ignore].push, [ignore].pull, and [ignore].shared as gitignore-style patterns
  read [ignore].skip_markers as a list of marker basenames
  reject skip marker values that are empty, '.', '..', or contain path separators
  return push defaults, pull defaults, and shared skip markers

expand_group(group_id):
  if group does not exist:
    reject unknown group

  expand package and nested group members
  return package entries in group order
```

## Review Needed

Profile merge order, extension patching, and group expansion errors need implementation review before changes.
