# CLI Styling

## Intent

Render consistent labels, colors, annotations, and compact display text for CLI output.

## Behavior

```pseudo
style_text(text, role):
  if colors are disabled:
    return text unchanged
  return text wrapped with role-specific style

menu action styles:
  style create/update/delete/install/probe action badges consistently in selection and payload output

render_package_label(identity):
  if package has instance/profile annotation:
    render repo:package<instance>
  else:
    render repo:package

render_package_target_label(identity, target):
  render package identity followed by .target

render_annotation_parentheses(text):
  if text is empty:
    return empty text
  return text in parentheses using annotation style

build_selector_match_display_fields(match):
  include identity field
  include match reason field when useful
  omit empty fields
  return fields for joined menu display
```
