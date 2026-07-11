from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import pytest

from dotman.templates import (
    JinjaRenderError,
    build_template_context,
    discover_template_file_dependencies,
    render_template_file,
    render_template_string,
)


def test_render_template_file_trims_standalone_control_block_lines(tmp_path: Path) -> None:
    template_path = tmp_path / "template.txt"
    template_path.write_text(
        "\n".join(
            [
                "alpha",
                "{% if enabled %}",
                "beta",
                "{% endif %}",
                "gamma",
                "",
            ]
        ),
        encoding="utf-8",
    )

    rendered, projection_kind = render_template_file(template_path, {"enabled": True})

    assert projection_kind == "template"
    assert rendered.decode("utf-8") == "alpha\nbeta\ngamma\n"


def test_render_template_string_preserves_default_block_whitespace(tmp_path: Path) -> None:
    rendered = render_template_string(
        "\n".join(
            [
                "alpha",
                "{% if enabled %}",
                "beta",
                "{% endif %}",
                "gamma",
                "",
            ]
        ),
        {"enabled": True},
        base_dir=tmp_path,
    )

    assert rendered == "alpha\n\nbeta\n\ngamma\n"


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["plain", "two words"], "plain 'two words'"),
        (["single'quote", 'double"quote'], "'single'\"'\"'quote' 'double\"quote'"),
        ([r"^foo\\d+$", r"path\\part"], r"'^foo\\d+$' 'path\\part'"),
        (["$HOME", ";", "*.json", "a&b", "$(echo bad)"], "'$HOME' ';' '*.json' 'a&b' '$(echo bad)'"),
        (["line one\nline two", ""], "'line one\nline two' ''"),
        ([], ""),
    ],
)
def test_shell_args_quotes_each_string_as_one_posix_argument(
    tmp_path: Path, arguments: list[str], expected: str
) -> None:
    rendered = render_template_string("{{ arguments|shell_args }}", {"arguments": arguments}, base_dir=tmp_path)

    assert rendered == expected
    assert shlex.split(rendered) == arguments


@pytest.mark.parametrize(
    "invalid_value",
    ["bare string", {"key": "value"}, ("tuple",), ["ok", ["nested"]], None, True, 1, 1.5, ["ok", False], ["ok", 2]],
)
def test_shell_args_rejects_values_other_than_flat_lists_of_strings(
    tmp_path: Path, invalid_value: object
) -> None:
    with pytest.raises(JinjaRenderError, match="shell_args requires a flat list of strings"):
        render_template_string("{{ value|shell_args }}", {"value": invalid_value}, base_dir=tmp_path)


def test_shell_args_is_available_to_variable_templates() -> None:
    context = build_template_context(
        {"selectors": ["two words", "$HOME"], "selector_args": "{{ selectors|shell_args }}"},
        profile="basic",
        inferred_os="linux",
    )

    assert context["selector_args"] == "'two words' '$HOME'"


def test_shell_args_is_available_to_file_templates(tmp_path: Path) -> None:
    template_path = tmp_path / "command.j2"
    template_path.write_text("command {{ arguments|shell_args }}\n", encoding="utf-8")

    rendered, _ = render_template_file(template_path, {"arguments": ["two words", ""]})

    assert rendered == b"command 'two words' ''\n"


def test_shell_args_rendered_command_preserves_argv_for_real_json_transform(tmp_path: Path) -> None:
    selectors = ["two words", "single'quote", r"regex\\value", "semi;star*", "line one\nline two"]
    base_path = tmp_path / "base.json"
    output_path = tmp_path / "output.json"
    base_path.write_text(json.dumps({selector: selector for selector in selectors}), encoding="utf-8")
    command = render_template_string(
        "dotman transform json {{ base|shell_args }} {{ output|shell_args }} "
        "--mode cleanup --selector-type retain --selectors {{ selectors|shell_args }}",
        {"base": [str(base_path)], "output": [str(output_path)], "selectors": selectors},
        base_dir=tmp_path,
    )

    completed = subprocess.run(["sh", "-c", command], text=True, capture_output=True, check=False)

    assert completed.returncode == 0, completed.stderr
    assert json.loads(output_path.read_text(encoding="utf-8")) == {selector: selector for selector in selectors}


def test_render_template_string_wraps_jinja_errors(tmp_path: Path) -> None:
    source_path = tmp_path / "template.txt"

    with pytest.raises(JinjaRenderError, match="jinja render failed") as exc_info:
        render_template_string("{{ missing.value }}", {}, base_dir=tmp_path, source_path=source_path)

    assert exc_info.value.path == source_path
    assert "missing" in exc_info.value.detail


def test_render_template_file_wraps_directory_inputs(tmp_path: Path) -> None:
    with pytest.raises(JinjaRenderError, match="source path must be a file"):
        render_template_file(tmp_path, {})


def test_discover_template_file_dependencies_collects_recursive_static_refs(tmp_path: Path) -> None:
    template_path = tmp_path / "profile"
    shared_path = tmp_path / "shared.txt"
    nested_path = tmp_path / "nested.txt"
    macro_path = tmp_path / "macros.j2"

    template_path.write_text(
        "{% include 'shared.txt' %}\n{% from 'macros.j2' import render_name %}\n{{ render_name() }}\n",
        encoding="utf-8",
    )
    shared_path.write_text("{% include 'nested.txt' %}\n", encoding="utf-8")
    nested_path.write_text("nested\n", encoding="utf-8")
    macro_path.write_text("{% macro render_name() %}name{% endmacro %}\n", encoding="utf-8")

    dependencies = discover_template_file_dependencies(template_path)

    assert dependencies == (shared_path.resolve(), nested_path.resolve(), macro_path.resolve())


def test_discover_template_file_dependencies_rejects_dynamic_refs(tmp_path: Path) -> None:
    template_path = tmp_path / "profile"
    template_path.write_text("{% include vars.include_name %}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="static template references"):
        discover_template_file_dependencies(template_path)
