# Planning Progress Reporting

## Intent

Provide optional terminal progress reporting for tracked push/pull planning without affecting JSON output or non-interactive runs.

## Behavior

```pseudo
make_planning_sink(json_output):
  if JSON output is enabled:
    return no sink
  if stderr is not an interactive terminal:
    return no sink
  return a terminal progress sink

ProgressSink:
  start(total):
    prepare reporting for total package selections

  update(count):
    advance completed package count by count
    keep a stable description; do not show per-package labels in the progress bar

  close():
    finish and clear terminal progress display
    do not fail when no label was shown
```
