# Interactive Reconcile

## Intent

Let users edit temporary review copies and write accepted changes back to selected source paths.

## Behavior

```pseudo
run_basic_reconcile(args):
  resolve editor command
  resolve required existing input paths
  build review content from source/live/diff inputs
  write review file and editable source copies
  open editor

  changed_sources = editable sources whose content changed

  if no editable sources changed:
    return success without writing

  if confirmation is required:
    ask user to confirm writing changed sources
    if user rejects:
      return success without writing

  write each changed source back to original path
  return success

_build_review_content(inputs):
  include source/live paths and diff text when available
  deduplicate repeated paths
  preserve enough context for user to decide edits
```

## Review Needed

Review file format, source-copy mapping, and confirmation behavior need implementation review before changes.
