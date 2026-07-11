# TOML Structured Transform

## Intent

Transform selected TOML paths without discarding document order, comments, spacing, or reusable compare bytes.

## Behavior

```pseudo
load TOML operand:
  if operand is "-": read captured stdin through tomlkit
  else if file exists: read file through tomlkit
  else: use empty TOML document
  if document cannot parse: reject with parse context
  if selectors are empty: reject because TOML transform requires selectors

select TOML paths:
  parse unprefixed and exact selectors as dotted key paths
  compile regex selectors against dotted table and key paths
  table match selects whole subtree; key match selects that key
  treat arrays and arrays of tables as atomic selected values
  if selector syntax or regex is invalid: reject with selector context

preserve trivia around each table item:
  traverse tables inside arrays of tables only to assign tail-trivia ownership
  comments before blank separator remain attached to owning table or array
  blank-separated independent comment block remains detached after owning item
  detached comments survive selected item deletion or replacement

cleanup(base, action):
  if action is retain:
    rebuild selected items with required ancestor tables
  if action is remove:
    delete selected matches deepest first
  normalize blank-line runs without fresh semantic serialization

merge(base, overlay, action):
  partition base using cleanup rules
  recursively overlay tables into original base slots
  treat scalars, arrays, and arrays of tables as atomic overlay values
  preserve selected base according to action
  do not restore managed items omitted by overlay
  restore leading/trailing comments, ordering, and section separators

compare and emit:
  if compare bytes decode and parse to same unwrapped TOML value:
    reuse exact compare bytes, including CRLF
  else:
    emit preserved tomlkit document text
  support one stdin operand and stdout via --stdout or "-"
  when writing file from file base: inherit base permissions
  surface parse, selector, and serialization failures as nonzero CLI errors
```
