# Structured Transform Framework

## Intent

Define format-independent requests, validation, selection metadata, output handling, and byte-preservation rules.

## Behavior

```pseudo
validate(request, engine):
  if mode is merge and overlay is missing:
    reject with observable argument error
  if mode is cleanup and overlay is present:
    reject with observable argument error
  if neither output path nor stdout is selected:
    reject with observable argument error
  if engine requires selectors and selector collection is empty:
    reject with observable argument error
  if request contains selector type unknown to engine:
    reject with engine name and unknown selector types
  if a populated selector type does not support requested mode:
    reject with engine name, mode, and unsupported selector types

format engine validation:
  compile its regex selector values
  if regex cannot compile: reject with selector and regex error context

emit(transform_output, request):
  if semantic compare produced reusable bytes:
    output bytes = exact compare-file bytes
  else:
    output bytes = transformed serialization

  if destination is stdout or "-":
    write bytes through binary stdout
    return

  if destination and compare path are same and bytes are reusable:
    do not rewrite file
    return

  write output bytes to destination
  if base operand is a file:
    copy base permission bits to destination
  if base operand is stdin:
    do not synthesize base permissions
```
