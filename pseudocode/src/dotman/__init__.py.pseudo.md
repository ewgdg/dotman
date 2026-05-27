# dotman Package

## Intent

Expose package metadata without triggering CLI or engine work at import time.

## Behavior

```pseudo
import dotman:
  expose package doc/version metadata
  do not load config
  do not touch filesystem
  do not start CLI execution
```
