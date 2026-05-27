# Add Command Planning

## Intent

Turn a live path into a reviewed package manifest target update.

## Behavior

```pseudo
resolve_live_path_spec(live_path_text):
  if live_path_text is empty:
    reject missing live path

  parse optional config path from live_path_text
  resolve live path relative to current working directory

  if live path does not exist:
    reject invalid live path

  return live path plus optional config path

prepare_add_to_package(engine, package_query, live_path_spec, target_name, source_path):
  if package_query resolves to existing package:
    use that package manifest
  else if creating a package is allowed by query:
    choose new package manifest path
  else:
    reject ambiguous or missing package

  if target_name is missing:
    derive target name from live path
    if derived name collides with existing target:
      choose next available target name

  if source_path is missing:
    derive repo source path from live path and package root

  if manifest exists:
    read existing manifest text
    read existing target metadata
  else:
    start new manifest text

  render manifest text with proposed target block

  if rendered manifest is invalid TOML:
    reject manifest update

  return add result with write path, rendered manifest text, and target metadata

review_add_manifest(result):
  if result is not writable:
    return result unchanged

  if no editor is available:
    return result unchanged

  open review content in editor

  if user leaves manifest unchanged:
    return kept result

  if edited manifest is invalid TOML:
    reject edited manifest

  return result using edited manifest text

write_add_result(result):
  if result is noop or kept:
    do not write files
    return result

  write rendered manifest text to target manifest path
  return written result
```

## Review Needed

Exact TOML formatting, comment preservation, and editor-review edge cases need implementation review before changing add behavior.
