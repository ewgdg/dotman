# TOML Utilities

## Intent

Load TOML text/files and expose consistent parse/load errors.

## Behavior

```pseudo
load_toml_file(path):
  if file cannot be read:
    raise TomlLoadError(path, detail)

  parse TOML text

  if parse fails:
    raise TomlLoadError(path, detail)

  return parsed payload

load_toml_text(text):
  if text is invalid TOML:
    raise TomlLoadError(detail)
  return parsed payload

format_toml_load_error(error):
  if error has path:
    include path in message
  include parse/read detail
```
