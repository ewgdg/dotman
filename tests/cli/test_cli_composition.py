from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest

from dotman.cli import build_parser, main
from dotman.interaction import TerminalInteraction
from dotman.ui_context import current_ui_config
from tests.helpers import write_named_manager_config


def test_parser_provides_full_path_default_for_commands_without_the_flag() -> None:
    args = build_parser().parse_args(["list", "tracked"])

    assert args.full_path is None


def test_parser_rejects_removed_forget_command() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["forget", "example:git"])


def test_configuration_independent_render_ignores_missing_manager_config(
    tmp_path: Path,
    capsys,
) -> None:
    template_path = tmp_path / "template.txt"
    template_path.write_text("standalone\n", encoding="utf-8")

    exit_code = main(
        [
            "--config",
            str(tmp_path / "missing-config.toml"),
            "render",
            "jinja",
            str(template_path),
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out == "standalone\n"


def test_config_only_edit_uses_manager_ui_scope(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    alpha_root = tmp_path / "alpha"
    alpine_root = tmp_path / "alpine"
    alpha_root.mkdir()
    alpine_root.mkdir()
    config_path = write_named_manager_config(
        tmp_path,
        {"alpha": alpha_root, "alpine": alpine_root},
    )
    with config_path.open("a", encoding="utf-8") as config_file:
        config_file.write("[ui.menus]\nbottom_up = false\n")
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)
    interaction_output = StringIO()
    interaction = TerminalInteraction(
        input_stream=StringIO("2\n"),
        output_stream=interaction_output,
        fzf_available=lambda: False,
        use_color=False,
    )

    exit_code = main(
        ["--config", str(config_path), "edit", "local", "al"],
        interaction=interaction,
    )

    assert exit_code == 0
    rendered_menu = interaction_output.getvalue()
    assert rendered_menu.index("alpha") < rendered_menu.index("alpine")
    assert current_ui_config() is None
    assert "Local override path:" in capsys.readouterr().out
