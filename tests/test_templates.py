from __future__ import annotations

from pathlib import Path

from dotman.templates import render_template_file, render_template_string


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
