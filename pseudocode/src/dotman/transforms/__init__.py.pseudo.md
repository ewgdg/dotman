# Bundled Structured Transforms

## Intent

Keep reusable format transformation behavior owned by dotman and independent of repository policy.

## Behavior

```pseudo
import transforms package:
  expose bundled framework support for JSON, TOML, plist, and XML
  do not discover or load a repository
  do not construct sync engine state
  do not register third-party plugins
  do not define transform-specific manifest schema

repository needs application-specific preprocessing or policy:
  compose repository helper with public dotman transform command
```
