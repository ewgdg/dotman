# dotman Template Targets

Use this when the repo stores a Jinja source file, but the live file is the rendered result.

## The Important Part

For template targets, `push` is easy. `pull` is the tricky part.

If you do not configure pull correctly, dotman cannot infer how to update the repo template from the rendered live file. It will fall back to writing or diffing against the rendered output, which can overwrite the template source with the wrong content.

For a Jinja target, make the forward render explicit:

- `render = "jinja"`

`jinja` is just a shortcut for the built-in renderer. If you want the command form explicitly, use:

- `render = 'dotman render jinja "$DOTMAN_SOURCE"'`

Important: that command form is only equivalent when **dotman** launches it as a target command. In that case, dotman injects the resolved binding context through env vars such as `DOTMAN_PROFILE`, `DOTMAN_OS`, and `DOTMAN_VAR_*`.

If you run `dotman render jinja ...` manually in your shell, dotman does **not** look up a repo, package, or profile on its own. You must provide context explicitly with `--profile`, `--os`, and `--var`, or export the corresponding `DOTMAN_*` env vars first.

The shortcut is preferred in manifests. The explicit command form is mainly useful for understanding what dotman runs internally and for manual debugging with explicit inputs.

For pull, you usually also want:

- `pull_view_repo = "render"`
- `pull_view_live = "raw"`
- and **either**:
  - `capture` for a non-interactive reverse projection, or
  - `reconcile` for an interactive workflow

For the common Jinja editor workflow, you can use the built-in shortcut:

- `reconcile = "jinja"`

`reconcile = "jinja"` recursively discovers static Jinja template dependencies such as `{% include %}`, `{% extends %}`, `{% import %}`, and `{% from ... import ... %}`, then runs the built-in editor reconcile flow with those files added as extra editable sources.

If you want the whole common bundle as defaults, you can also use:

- `preset = "jinja-editor"`

That preset supplies default values for:

- `render = "jinja"`
- `pull_view_repo = "render"`
- `pull_view_live = "raw"`
- `reconcile = "jinja"`
- `reconcile_io = "tty"`

Explicit target keys still win over the preset.

If your template references other files dynamically, use an explicit `reconcile = 'dotman reconcile editor ...'` command instead.

If `reconcile` opens an editor or otherwise needs a real terminal, also set:

- `reconcile_io = "tty"`

## Built-In `capture = "patch"`

`capture = "patch"` is the built-in reverse-capture helper for the narrow Jinja patch workflow.

Use it only for file targets that already have the forward render and pull review split configured as:

- `render = "jinja"`
- `pull_view_repo = "render"`
- `pull_view_live = "raw"`

The helper reads the reviewed repo/live projections, patches the raw repo source, rerenders the patched source, and fails unless the rerender matches the review live bytes exactly.

Use the explicit CLI helper when you want to debug the algorithm directly:

```sh
dotman capture patch \
  --repo-path "$DOTMAN_REPO_PATH" \
  --review-repo-path "$DOTMAN_REVIEW_REPO_PATH" \
  --review-live-path "$DOTMAN_REVIEW_LIVE_PATH" \
  --profile basic \
  --var greeting=hello
```

For the common bundle, use:

- `preset = "jinja-patch"`

That preset supplies default values for:

- `render = "jinja"`
- `capture = "patch"`
- `pull_view_repo = "render"`
- `pull_view_live = "raw"`

## Example

`package.toml`:

```toml
id = "shell"
description = "Shell profile"

[targets.profile]
source = "files/profile"
path = "~/.profile"
preset = "jinja-editor"
```

Equivalent explicit form:

```toml
id = "shell"
description = "Shell profile"

[targets.profile]
source = "files/profile"
path = "~/.profile"
render = "jinja"
pull_view_repo = "render"
pull_view_live = "raw"
reconcile_io = "tty"
reconcile = "jinja"
```

Or, if you want a fully explicit custom editor command:

```toml
id = "shell"
description = "Shell profile"

[targets.profile]
source = "files/profile"
path = "~/.profile"
render = "jinja"
pull_view_repo = "render"
pull_view_live = "raw"
reconcile_io = "tty"
reconcile = '''
dotman reconcile editor \
  --review-repo-path "${DOTMAN_REVIEW_REPO_PATH:-$DOTMAN_REPO_PATH}" \
  --review-live-path "${DOTMAN_REVIEW_LIVE_PATH:-$DOTMAN_LIVE_PATH}" \
  --repo-path "$DOTMAN_REPO_PATH" \
  --live-path "$DOTMAN_LIVE_PATH" \
  --additional-source "$DOTMAN_PACKAGE_ROOT/files/env.core.sh"
'''
```

`files/profile`:

```sh
# os: {{ os }}

{% include 'env.core.sh' %}
{% if os == "darwin" %}
export SUDO_ASKPASS="$HOME/bin/askpass-macos"
{% elif os == "linux" %}
export SUDO_ASKPASS="$HOME/bin/askpass-gui"
{% endif %}
```

`files/env.core.sh`:

```sh
export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
export XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
```

### What This Config Does

- `source = "files/profile"`
  - repo source file
- `path = "~/.profile"`
  - live target path
- `preset = "jinja-editor"`
  - optional built-in default bundle for the common Jinja editor workflow
- `render = "jinja"`
  - shortcut for the built-in Jinja renderer
  - equivalent command form: `dotman render jinja "$DOTMAN_SOURCE"`
- `pull_view_repo = "render"`
  - pull review compares the rendered repo-side result, not raw source text
- `pull_view_live = "raw"`
  - pull review compares against the actual live file
- `reconcile = "dotman reconcile editor ..."`
  - explicit editor-based reconcile workflow
- `reconcile = "jinja"`
  - shortcut for the built-in Jinja reconcile helper
  - auto-adds recursively discovered static template dependencies as editable sources
- `reconcile_io = "tty"`
  - required for full-screen editor workflows
- `--additional-source ...`
  - includes extra repo source files that also need to be editable, such as included partials

## When To Use `capture` vs `reconcile`

Use `capture` when you can convert the live file back into the canonical repo source **without user interaction**.

Use `capture = "patch"` for the narrow Jinja reverse-capture case where dotman can patch the source automatically and verify that the rerender matches the reviewed live bytes exactly.

Use `reconcile` when a human needs to decide how the live change maps back to one or more template source files.

Typical template target:

- forward path: template source -> rendered live file
- reverse path: `reconcile`, not blind file copy

## Built-In `dotman reconcile editor`

`dotman reconcile editor` is the built-in low-level reconcile helper for template workflows.

It is meant for cases where the live file is rendered output, but the repo stores editable source files.

Use it like this inside `reconcile`:

```sh
dotman reconcile editor \
  --review-repo-path "${DOTMAN_REVIEW_REPO_PATH:-$DOTMAN_REPO_PATH}" \
  --review-live-path "${DOTMAN_REVIEW_LIVE_PATH:-$DOTMAN_LIVE_PATH}" \
  --repo-path "$DOTMAN_REPO_PATH" \
  --live-path "$DOTMAN_LIVE_PATH" \
  --additional-source "$DOTMAN_PACKAGE_ROOT/files/env.core.sh"
```

Notes:

- `--repo-path` is the main repo source file to edit
- `--live-path` is the actual live file
- `DOTMAN_REVIEW_REPO_PATH` is the repo-side file for the editor diff view; it should contain the result of applying `pull_view_repo` to the repo side
- `DOTMAN_REVIEW_LIVE_PATH` is the live-side file for the editor diff view; it should contain the result of applying `pull_view_live` to the live side
- `--review-repo-path` and `--review-live-path` tell `dotman reconcile editor` which two files to show in the diff view
- `--additional-source` adds extra editable source files, usually included partials

If your template uses `{% include %}`, shared fragments, or helper files, add them with `--additional-source`. Otherwise the reconcile flow only edits the top-level source file.

## Built-In `reconcile = "jinja"` and `dotman reconcile jinja`

`reconcile = "jinja"` is the shortcut form for the common Jinja editor reconcile workflow.

It uses the same helper as:

```sh
dotman reconcile jinja \
  --review-repo-path "${DOTMAN_REVIEW_REPO_PATH:-$DOTMAN_REPO_PATH}" \
  --review-live-path "${DOTMAN_REVIEW_LIVE_PATH:-$DOTMAN_LIVE_PATH}" \
  --repo-path "$DOTMAN_REPO_PATH" \
  --live-path "$DOTMAN_LIVE_PATH"
```

The Jinja reconcile helper:

- starts from `--repo-path`
- recursively discovers static template dependencies
- adds those files to the editor session the same way you would with repeated `--additional-source`
- still uses the projected review files from `DOTMAN_REVIEW_REPO_PATH` and `DOTMAN_REVIEW_LIVE_PATH`

Keep the explicit `dotman reconcile editor ... --additional-source ...` form when you need custom extra files or dynamic template references.

## Built-In `dotman render jinja`

`dotman render jinja <source-path>` renders a Jinja source file to stdout.

It uses the same built-in renderer as `render = "jinja"`.

When dotman runs it as a target command, it first resolves the binding context, then provides it through env vars.

Resolution source:

- `profile` comes from the binding itself, for example `...@basic`
- template vars come from `package vars -> composed profile vars -> repo local override vars`
- `os` is the inferred target OS for that binding

Injected env:

- `DOTMAN_PROFILE`
- `DOTMAN_OS`
- `DOTMAN_VAR_*` (flattened with `__` as the nested key separator, for example `vars.git.user_name` -> `DOTMAN_VAR_git__user_name`)

`dotman render jinja` reconstructs the nested `vars` object from those env vars unless you override them with CLI flags.

For manual testing, you can also pass values explicitly:

```sh
dotman render jinja --profile basic --os linux --var git.user_name='Example User' path/to/file
```

## Rules Of Thumb

- No `template = true` flag or separate template target type exists
- Jinja file rendering is explicit: use `render = "jinja"`, `preset = "jinja-editor"`, or `preset = "jinja-patch"`
- `capture = "patch"` is the narrow automatic reverse-capture helper; it expects `render = "jinja"`, `pull_view_repo = "render"`, and `pull_view_live = "raw"`
- dotman follows the configured `render`, `pull_view_*`, `capture`, and `reconcile` workflow
- `.tmpl` is optional naming only
- If your source uses Jinja `{% include %}`, relative paths resolve from the source file directory
- For Jinja pull review, use:
  - `pull_view_repo = "render"`
  - `pull_view_live = "raw"`
- For template-style pull execution, use either `capture = "patch"` for automatic source patching or `reconcile` for interactive/manual work
- If `reconcile` is interactive, set `reconcile_io = "tty"`
