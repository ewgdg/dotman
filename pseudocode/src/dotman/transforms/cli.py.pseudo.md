# Structured Transform CLI

## Intent

Expose each bundled format through one repository-independent `dotman transform <format>` contract.

## Behavior

```pseudo
configure transform FORMAT parser:
  require base operand and cleanup-or-merge mode
  accept optional output, overlay, selector action, selectors, stdout, compare file
  add FORMAT-specific options
  describe unprefixed selector default and every supported prefix

parse selectors(raw selectors, FORMAT metadata):
  for each selector:
    if selector has recognized prefix:
      assign value to that selector type
    else:
      assign entire selector literal to FORMAT default selector type

run parsed transform:
  if base and overlay both equal "-":
    reject before reading stdin
  capture stdin once when one input operand equals "-"
  validate framework request and FORMAT options
  invoke bundled FORMAT engine without repository discovery or sync engine

  if --stdout is present:
    emit to stdout even when positional output is also present
  else if output equals "-":
    emit to stdout
  else:
    emit to output file with framework permission rules

on invalid operands, selectors, format options, parsing, or serialization:
  print actionable error to stderr
  exit nonzero
```
