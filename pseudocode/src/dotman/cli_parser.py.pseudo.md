# CLI Parser

## Intent

Define the argparse command surface and shared options for `dotman`.

## Behavior

```pseudo
build_parser():
  create root parser
  add global config/output/interactivity options

  for each supported subcommand:
    create subparser
    add shared arguments through helper functions
    add command-specific flags and positional args
    assign command name for dispatch

  list supports configured repo discovery plus tracked state, trackables, variables, and snapshots

  edit supports repository roots in addition to package, target, local override, and config paths

  hide internal aliases from help when requested
  return parser

argument helper functions:
  add one shared concept consistently across commands
  set metavar, default, action, and help text for that concept
```
