# Variable Inspection

## Intent

List resolved variables and explain final values through provenance layers.

## Behavior

```pseudo
list_resolved_variables(engine):
  collect variable occurrences from repo defaults, local vars, profiles, packages, and tracked bindings
  attach provenance to each occurrence
  return occurrences

list_winning_variables(engine):
  collect resolved variable occurrences
  apply variable precedence rules
  return final visible value for each variable name

find_variable_matches(engine, variable_text):
  normalize variable query
  rank query against resolved variable names
  return matches

describe_resolved_variable(engine, variable_text):
  matches = find variable matches

  if matches is empty:
    reject missing variable

  if matches is ambiguous:
    reject ambiguous variable

  return final value plus all contributing occurrences and provenance

provenance serialization:
  include source repo/package/profile/binding information when available
  preserve enough detail to explain why a value won
```

## Review Needed

Variable precedence and provenance layering need implementation review before changes.
