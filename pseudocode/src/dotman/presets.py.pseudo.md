# Built-in Target Presets

## Intent

Return built-in target preset payloads by name.

## Behavior

```pseudo
get_builtin_target_preset(name):
  if name matches a known built-in preset:
    return copy of preset payload
  return no preset
```
