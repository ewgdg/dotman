from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, TemplateSyntaxError, Undefined


class DotmanUndefined(Undefined):
    def __bool__(self) -> bool:
        return False

    def __str__(self) -> str:
        self._fail_with_undefined_error()
        return ""


def build_template_context(
    variables: dict[str, Any],
    *,
    profile: str,
    inferred_os: str,
) -> dict[str, Any]:
    context = dict(variables)
    context["vars"] = variables
    context["profile"] = profile
    context["os"] = inferred_os
    return context


def _standard_environment(base_dir: Path) -> Environment:
    return Environment(
        autoescape=False,
        loader=FileSystemLoader(str(base_dir)),
        undefined=DotmanUndefined,
        keep_trailing_newline=True,
    )


def render_template_string(value: str, context: dict[str, Any], *, base_dir: Path) -> str:
    env = _standard_environment(base_dir)
    return env.from_string(value).render(context)


def render_template_file(path: Path, context: dict[str, Any]) -> tuple[bytes, str]:
    try:
        source_text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_bytes(), "raw"

    if "{{" not in source_text and "{%" not in source_text and "{#" not in source_text:
        return source_text.encode("utf-8"), "raw"

    env = _standard_environment(path.parent)
    try:
        template = env.from_string(source_text)
    except TemplateSyntaxError:
        return source_text.encode("utf-8"), "raw"
    try:
        return template.render(context).encode("utf-8"), "template"
    except (TemplateSyntaxError, ValueError):
        return source_text.encode("utf-8"), "raw"
