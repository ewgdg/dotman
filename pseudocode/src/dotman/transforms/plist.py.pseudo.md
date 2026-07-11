# Plist Structured Transform

## Intent

Partition and merge plist dictionaries with typed semantic comparison and binary-safe output.

## Behavior

```pseudo
load plist operand:
  if missing base file: use empty dictionary
  if operand is "-": read captured stdin bytes
  parse plist bytes
  if root is not dictionary: reject with parse/type context

select dictionary paths:
  parse unprefixed and exact selectors as dotted paths with quoted literal segments
  compile regex selectors against complete dotted paths
  treat arrays and scalars as atomic values
  if selector syntax or regex is invalid: reject with selector context

cleanup(base, action, selectors):
  if selectors are empty: preserve entire base
  if action is retain: keep matches, matched dictionary subtrees, and required ancestors
  if action is remove: delete matches or whole matched dictionary subtrees

merge(base, overlay, action, selectors):
  filter base first
  recursively overlay dictionaries through selected ancestors
  replace overlay values atomically
  keep selected base values according to action
  do not restore managed descendants omitted by overlay

values_are_equal(left, right):
  require identical runtime types
  dictionaries: require same keys and recursively equal typed values
  lists: require same order and recursively equal typed elements
  scalars: compare values only after type match
  therefore boolean and integer values remain distinct

compare and emit:
  if compare plist is typed-semantically equal: reuse exact compare bytes
  else: serialize sorted keys using requested xml or binary format
  write stdout binary-safely or write file
  when writing file from file base: inherit base permissions
  surface parse, selector, and serialization failures as nonzero CLI errors
```
