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
    else if target has changed directory child items:
      create one ReviewItem per changed child
      use planned review bytes when available
      for push child after-side bytes, reuse planned desired bytes instead of rereading raw repo source
      for pull create/delete children with transformed review views, attach a lazy loader for the missing projected side
      when loading raw repo/live bytes, use privileged-aware file access and treat only actual missing files as empty
      if mode metadata cannot be read because path is missing or inaccessible, omit mode metadata instead of failing review build
      when running lazy review projection commands, preserve privileged live-read handling
      otherwise load the needed side from its repo/live path
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
