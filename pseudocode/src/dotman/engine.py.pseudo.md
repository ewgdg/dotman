# Dotman Engine Facade

## Intent

Expose stable engine APIs while delegating repository loading, tracking, planning, projection, collision, and inspection work to focused modules.

## Behavior

```pseudo
DotmanEngine.from_config_path(config_path):
  load manager config
  create engine with configured repositories
  return engine

get_repo(repo_name):
  if repo_name is configured:
    return repository
  reject unknown repo

resolve_selector_text(query_text):
  parse query text
  search candidate repos for selector matches

  if no match:
    reject missing selector

  if more than one match is equally valid:
    reject ambiguous selector

  return resolved selector

plan_push_query(query_text) / plan_pull_query(query_text):
  resolve query to package selection(s)
  build package plans
  wrap them in operation plan with repo hooks and conflict validation
  return operation plan

plan_push() / plan_pull():
  read effective tracked entries
  build tracked package plans
  wrap them in operation plan
  return operation plan

record_tracked_package_entry(binding):
  delegate validation and normalization to tracking module
  write updated tracked state
  return recorded entry

remove_tracked_package_entry(binding_text):
  resolve persisted tracked entry
  remove it from tracked state
  return removed record

list/describe APIs:
  delegate to tracking, tracked_packages, or variable_inspection modules
  return model objects rather than formatted CLI text
```

## Review Needed

This broad facade keeps compatibility methods. Review delegated module pseudocode plus implementation before changing exact behavior.
