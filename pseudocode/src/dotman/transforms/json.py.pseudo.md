# JSON Structured Transform

## Intent

Partition and recompose top-level JSON objects while preserving stable output and exact compare bytes when semantics do not change.

## Behavior

```pseudo
load JSON operand:
  if operand is "-": read captured stdin
  else if file exists: read file
  else: use empty object
  parse JSON
  if root is not object: reject with parse/type context

select base paths:
  parse unprefixed and exact selectors as dotted paths with quoted literal segments
  compile regex selectors against complete dotted key paths
  if selector syntax or regex is invalid: reject with selector context

cleanup(base, action, selectors):
  if selectors are empty: preserve base object
  if action is retain: keep matched values and required ancestor objects
  if action is remove: delete matched values
  preserve surviving key order

merge(base, overlay, action, selectors):
  filter base before applying overlay
  recursively overlay objects into original base key slots
  append new overlay keys and preserved keys without original slots
  replace non-object values atomically
  do not restore managed values omitted by overlay

serialize(result):
  preserve detected indentation style when available
  emit Unicode JSON followed by newline

compare and emit:
  if compare file parses to value semantically equal to result:
    reuse exact compare bytes, including original line endings
  else:
    emit serialized result
  apply shared stdout, file, error, and permission behavior
```
