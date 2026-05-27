# Diff Review Materialization

## Intent

Create reviewable before/after items and run external diff/edit tools for planned target changes.

## Behavior

```pseudo
build_review_items(plans):
  for each target plan in operation plan:
    if target has reviewable before/after content:
      create ReviewItem with package, target, action, paths, bytes, and modes
  return review items in plan order

run_review_item_diff(item):
  materialize before and after sides in temporary files
  choose diff/pager command
  run command with side names and file mode context
  return command status

run_review_item_edit(item):
  materialize editable review paths
  open editor

  if user changes review side:
    return changed bytes
  else:
    return keep status

display_review_path(path):
  if compact path can preserve useful identity:
    return compact path
  return full display path
```

## Review Needed

Temp-file layout, pager selection, mode display, and directory review bytes need implementation review before changes.
