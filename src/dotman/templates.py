from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, TemplateNotFound, TemplateSyntaxError, Undefined, UndefinedError, meta


class DotmanUndefined(Undefined):
    def __bool__(self) -> bool:
        return False

    def __str__(self) -> str:
        self._fail_with_undefined_error()
        return ""


class JinjaRenderError(ValueError):
    def __init__(self, path: Path | None, detail: str) -> None:
        self.path = path
        self.detail = detail
        super().__init__(detail)

    def __str__(self) -> str:
        return format_jinja_render_error(self)


def _base_environment(base_dir: Path) -> Environment:
    return Environment(
        autoescape=False,
        loader=FileSystemLoader(str(base_dir)),
        undefined=DotmanUndefined,
        keep_trailing_newline=True,
    )


def _string_environment(base_dir: Path) -> Environment:
    return _base_environment(base_dir)


def _file_environment(base_dir: Path) -> Environment:
    return Environment(
        autoescape=False,
        loader=FileSystemLoader(str(base_dir)),
        undefined=DotmanUndefined,
        keep_trailing_newline=True,
        # File templates are line-oriented config text, so standalone control
        # lines should not leave extra blank lines in rendered output.
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _resolve_node(value: Any, context: dict[str, Any]) -> Any:
    """Recursively resolve Jinja2 references in a var value using the given context."""
    if isinstance(value, str) and ("{{" in value or "{%" in value):
        env = _string_environment(Path("."))
        return env.from_string(value).render(context)
    if isinstance(value, dict):
        return {k: _resolve_node(v, context) for k, v in value.items()}
    return value


def _resolve_vars_templates(variables: dict[str, Any]) -> dict[str, Any]:
    """Iteratively resolve Jinja2 references within var values so vars can reference each other.

    Repeats until stable so chains like A = "{{ B }}", B = "{{ C }}" resolve fully.
    """
    resolved = copy.deepcopy(variables)
    for _ in range(10):
        context = {**resolved, "vars": resolved}
        new_resolved = {k: _resolve_node(v, context) for k, v in resolved.items()}
        if new_resolved == resolved:
            break
        resolved = new_resolved
    return resolved


def build_template_context(
    variables: dict[str, Any],
    *,
    profile: str,
    inferred_os: str,
) -> dict[str, Any]:
    resolved = _resolve_vars_templates(variables)
    context = dict(resolved)
    context["vars"] = resolved
    context["profile"] = profile
    context["os"] = inferred_os
    return context


def format_jinja_render_error(error: JinjaRenderError) -> str:
    location = f" for {error.path}" if error.path is not None else ""
    return f"jinja render failed{location}: {error.detail}"


def _render_template(
    env: Environment,
    value: str,
    context: dict[str, Any],
    *,
    source_path: Path | None = None,
) -> str:
    try:
        return env.from_string(value).render(context)
    except (TemplateNotFound, TemplateSyntaxError, UndefinedError, ValueError) as exc:
        raise JinjaRenderError(path=source_path, detail=str(exc)) from exc


def render_template_string(
    value: str,
    context: dict[str, Any],
    *,
    base_dir: Path,
    source_path: Path | None = None,
) -> str:
    return _render_template(_string_environment(base_dir), value, context, source_path=source_path)


def discover_template_file_dependencies(path: Path) -> tuple[Path, ...]:
    env = _file_environment(path.parent)
    loader = env.loader
    if not isinstance(loader, FileSystemLoader):  # pragma: no cover - guarded by _file_environment.
        raise ValueError("jinja dependency discovery requires a filesystem loader")

    source_name = path.name
    visited_names: set[str] = set()
    discovered_paths: list[Path] = []

    def visit(template_name: str) -> None:
        if template_name in visited_names:
            return
        visited_names.add(template_name)
        try:
            source_text, filename, _uptodate = loader.get_source(env, template_name)
        except TemplateNotFound as exc:
            raise ValueError(f"jinja template dependency not found from {path}: {template_name}") from exc

        resolved_path = Path(filename).resolve()
        if resolved_path != path.resolve():
            discovered_paths.append(resolved_path)

        try:
            parsed = env.parse(source_text)
        except TemplateSyntaxError as exc:
            raise ValueError(f"jinja template dependency parse failed for {filename}: {exc}") from exc

        for reference in meta.find_referenced_templates(parsed):
            if reference is None:
                # Built-in Jinja reconcile must know the full editable source set
                # before launching the editor, so dynamic template references are
                # intentionally rejected instead of guessed.
                raise ValueError(
                    f"jinja reconcile requires static template references: {filename} contains a dynamic reference"
                )
            visit(reference)

    visit(source_name)
    return tuple(discovered_paths)



def render_template_file(path: Path, context: dict[str, Any]) -> tuple[bytes, str]:
    try:
        source_text = path.read_text(encoding="utf-8")
    except IsADirectoryError as exc:
        raise JinjaRenderError(path=path, detail="source path must be a file") from exc
    except UnicodeDecodeError as exc:
        raise JinjaRenderError(path=path, detail="source file must be UTF-8 text") from exc

    rendered = _render_template(_file_environment(path.parent), source_text, context, source_path=path)
    return rendered.encode("utf-8"), "template"
