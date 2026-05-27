# UI Context

## Intent

Store active UI config for code that renders without receiving config explicitly.

## Behavior

```pseudo
ui_config_scope(ui_config):
  set current UI config to ui_config
  run caller body
  restore previous UI config afterward

current_ui_config():
  if UI config is set in current context:
    return it
  return no config
```
