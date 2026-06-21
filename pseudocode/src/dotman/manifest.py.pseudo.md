# Manifest Normalization and Merge

## Intent

Validate manifest identity rules, normalize manifest payloads into models, and merge package/profile overrides.

## Behavior

```pseudo
validate_package_id(package_id):
  if id is empty or contains reserved separator/path syntax:
    reject package id
  return package id

validate_target_name(target_name):
  if name is empty or contains reserved separator/path syntax:
    reject target name
  return target name

deep_merge(base, override):
  if both values are mappings:
    merge keys recursively
  else:
    override replaces base
  return merged value

normalize_* helpers(value):
  if value shape/type is unsupported:
    reject with validation error
  return normalized Python/domain value

build_target_spec(payload):
  resolve target schema aliases
  apply target preset if present
  normalize path, type, sync policy, probe, render/pull/reconcile/patch-capture config, ignore rules (including ignore.gitignore), metadata, and path rules
  if probe is present:
    require probe to be a non-empty command string
    reject source, path, type, chmod, render, capture, reconcile, pull views, ignore rules, and path rules
  return TargetSpec

merge_target_specs(base, override):
  merge scalar target fields, including probe, by override precedence
  merge maps and hooks according to manifest merge rules
  return merged TargetSpec

merge_package_specs(base, override):
  merge package vars, hooks, targets, metadata, and extensions
  return merged PackageSpec

patch_remove_and_append(package, remove_paths, append_payload):
  remove requested dotted paths from package payload
  append payload values at requested dotted paths
  return patched package payload
```

## Review Needed

Alias handling, merge precedence, and exact validation messages need implementation review before changes.
