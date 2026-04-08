from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, TemplateSyntaxError, Undefined


class DotmanUndefined(Undefined):
    def __bool__(self) -> bool:
        return False

    def __str__(self) -> str:
        self._fail_with_undefined_error()
        return ""


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


def render_template_string(value: str, context: dict[str, Any], *, base_dir: Path) -> str:
    env = _string_environment(base_dir)
    return env.from_string(value).render(context)


def render_template_file(path: Path, context: dict[str, Any]) -> tuple[bytes, str]:
    try:
        source_text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_bytes(), "raw"

    if "{{" not in source_text and "{%" not in source_text and "{#" not in source_text:
        return source_text.encode("utf-8"), "raw"

    env = _file_environment(path.parent)
    try:
        template = env.from_string(source_text)
    except TemplateSyntaxError:
        return source_text.encode("utf-8"), "raw"
    try:
        return template.render(context).encode("utf-8"), "template"
    except (TemplateSyntaxError, ValueError):
        return source_text.encode("utf-8"), "raw"
