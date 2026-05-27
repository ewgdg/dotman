# Patch Capture

## Intent

Capture user edits from review files and apply them back to projected target bytes.

## Behavior

```pseudo
capture_patch():
  resolve required before/after review paths
  if either path is missing:
    reject with CaptureError

  read review bytes
  produce patch bytes describing after-vs-before changes
  write patch bytes to stdout or configured output

apply_review_patch(raw_bytes, review_repo_bytes, review_live_bytes):
  if patch bytes are not valid UTF-8 patch content:
    reject with CaptureError

  apply review patch against repo review bytes

  if patch cannot apply cleanly to live review bytes:
    reject with CaptureError

  return patched live bytes

format_capture_error(error):
  include path when failure is path-specific
  include detail for CLI display
```

## Review Needed

Patch format, conflict detection, and CLI I/O contract need implementation review before behavior changes.
