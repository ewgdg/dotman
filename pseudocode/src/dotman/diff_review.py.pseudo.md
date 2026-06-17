# Diff Review Materialization

## Intent

Create reviewable before/after items and run external diff/edit tools for planned target changes.

## Behavior

```pseudo
build_review_items(plans):
  for each target plan in operation plan:
    if target is active probe target:
      create probe ReviewItem with package, target, install action, probe badge data, and related target hook command summaries
    else if target has reviewable before/after content:
      create file ReviewItem with package, target, action, paths, bytes, and modes
  return review items in plan order so review menu numbers match selection menu numbers for active target rows

run_review_item_diff(item):
  if item is probe review item:
    print probe summary instead of a file diff
    include related hook command summaries when any exist
    return success
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
