# Manager Configuration

## Intent

Resolve default paths and load normalized manager configuration.

## Behavior

```pseudo
default_config_path():
  choose configured environment path when present
  otherwise use platform default config root plus config filename

default_state_root() / default_snapshot_root():
  choose configured environment path when present
  otherwise use platform default state location

validate_state_key(state_key):
  if key is empty, absolute, contains parent traversal, or has unsafe path parts:
    reject key
  return valid key

load_manager_config(config_path):
  if config_path is missing:
    use default_config_path()

  read TOML payload

  if payload cannot be read or parsed:
    raise ManagerConfigLoadError(path, detail)

  normalize repos, state dirs, snapshot config, and UI config
  apply defaults for omitted optional fields
  return ManagerConfig
```
