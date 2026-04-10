from __future__ import annotations

from pathlib import Path

import pytest

from dotman.templates import discover_template_file_dependencies, render_template_file, render_template_string


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
