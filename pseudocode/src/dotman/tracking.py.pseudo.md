# Tracked State Management

## Intent

Manage persisted tracked package entries and derive effective tracked packages, trackables, and details.

## Behavior

```pseudo
read_tracked_package_entries(engine, repo):
  path = tracked packages state file for repo

  if path does not exist:
    return empty entries

  read and parse state file
  return persisted tracked package entries

read_effective_tracked_package_entries(engine, repo):
  raw_entries = read persisted entries
  expand group entries into package entries
  bind profiles according to package/profile rules
  deduplicate normalized entries
  return effective entries

validate_tracked_package_entries(engine, bindings_by_repo):
  for each persisted entry:
    if referenced repo is not configured:
      record orphan issue
    else if referenced package/group/profile is invalid:
      record invalid issue
  return validation issues

record_tracked_package_entry(engine, binding):
  validate new binding
  read existing persisted entries
  normalize existing entries plus new binding
  write normalized entries to state file
  return recorded binding

remove_tracked_package_entry(engine, binding_text):
  matches = find persisted tracked entry matches

  if matches is empty:
    reject missing persisted entry

  if matches is ambiguous:
    reject ambiguous persisted entry

  remove matched record from state file
  return removed record

list_trackables(engine):
  enumerate configured packages and groups
  mark tracked state from effective entries
  return catalog entries

describe_tracked_package(engine, package_text):
  resolve tracked package
  include package entry, variables, hooks, target summaries, and ownership details
```

## Review Needed

Group expansion, profile binding, duplicate normalization, and removal matching need implementation review before changes.
