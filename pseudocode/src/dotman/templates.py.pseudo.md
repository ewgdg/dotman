# Jinja Template Rendering

## Intent

Render Jinja strings/files and resolve templated variables with dotman-specific undefined/error behavior.

## Behavior

```pseudo
build_template_context(variables):
  flatten nested variable mappings
  resolve templated variable values against other variables

  if a variable reference cannot be resolved safely:
    reject with JinjaRenderError

  return render context

render_template_string(value, context):
  render string with dotman Jinja environment

  if rendering fails:
    raise JinjaRenderError with path/detail context

  return rendered string

render_template_file(path, context):
  create file-aware Jinja environment rooted at template directory
  load template file
  render with context
  return rendered content

discover_template_file_dependencies(path):
  inspect template dependency declarations when possible
  return referenced template paths

DotmanUndefined:
  behave consistently for boolean/string conversion
  preserve enough unresolved-variable signal for diagnosis
```

## Review Needed

Undefined behavior, recursive var resolution, and dependency discovery need implementation review before changes.
