# XML Structured Transform

## Intent

Partition, merge, sort, and compare XML trees while retaining application-significant structure and exact no-op bytes.

## Behavior

```pseudo
load XML operand:
  if operand is "-": read captured stdin
  else if file exists: read file
  else if mode is merge and overlay exists: use overlay tree directly as result
  else: reject missing required XML operand
  parse one XML tree
  if XML is malformed: reject with parse context
  if selectors are empty: reject because XML transform requires selectors

select element paths:
  expand comma-separated selector values
  match unprefixed and exact selectors with fnmatch against root-inclusive paths
  search root-inclusive paths with compiled regex selectors
  if selector or regex is invalid: reject with selector context

cleanup(base, action):
  if action is retain:
    copy every matched subtree and required ancestor chain
  if action is remove:
    copy tree then delete matched subtrees
    if root matches: clear root contents

merge(base, overlay, action):
  filter base first
  overlay attributes and text onto preserved elements
  place overlay children into original base sibling slots
  identify repeated siblings by tag plus available id, name, key, uuid, or nonempty text
  if identity lookup has no match, or no identity exists: match next sibling with same tag
  do not restore managed elements omitted by overlay

apply requested output sorting:
  if sort attributes requested: alphabetize attributes on emitted elements
  expand repeated or comma-separated sort-children paths
  sort only immediate children of matching parents

normalize only for semantic comparison:
  copy each compared tree
  discard whitespace-only text and tails
  sort attributes
  sort children only under requested sort parents

compare and emit:
  if normalized result equals normalized compare tree:
    reuse exact compare-file bytes
  else:
    pretty-print transformed tree without comparison-only normalization
  support one stdin operand and stdout via --stdout or "-"
  when writing file from file base: inherit base permissions
  surface parse, selector, and serialization failures as nonzero CLI errors
```
