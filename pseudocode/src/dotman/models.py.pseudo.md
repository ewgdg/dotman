# Domain Models

## Intent

Define normalized data containers and stable serialization for config, manifests, selections, tracked state, plans, details, and execution objects.

## Behavior

```pseudo
reference text helpers:
  package_ref_text(identity) returns repo:package or repo:package<instance>
  target_ref_text(identity, target) appends .target
  repo_qualified_target_text includes repo-qualified target identity

target models:
  TargetSpec may contain a probe command instead of file source/path fields
  active probe TargetPlan uses target_kind = "probe" and action = "probe"
  inactive probe TargetPlan uses target_kind = "probe" and action = "noop"
  probe targets are executable for hook eligibility but do not represent repo/live file paths

to_dict methods:
  serialize model fields into JSON-compatible dictionaries
  serialize nested model objects recursively
  preserve field names expected by CLI/API output
  serialize probe command metadata and avoid exposing fake file paths for probe target plans

ResolvedPackageSelection helpers:
  repo_name() returns selected repo
  package_id() returns selected package id
  bound_profile() returns effective profile binding
  selection_label() returns user-facing selector label

filter_hook_plans_for_targets(hooks, target_plans):
  keep hooks that apply to selected executable targets
  drop hooks that no longer have matching targets

finalize_hook_plans_for_targets(hooks, target_plans):
  attach standalone package/target summaries for hooks whose targets are filtered or absent
  return finalized hook plans

OperationPlan compatibility:
  iteration, length, and indexing expose package plans
  to_dict serializes repo hooks and package plans
```

## Review Needed

Serialization field contracts and compatibility iteration should be verified before model changes.
