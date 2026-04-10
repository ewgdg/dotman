from __future__ import annotations

from pathlib import Path

import pytest

from dotman.cli import main


def test_render_jinja_cli_renders_with_dotman_env(tmp_path: Path, monkeypatch, capsys) -> None:
    template_path = tmp_path / "profile"
    template_path.write_text(
        "\n".join(
            [
                "profile={{ profile }}",
                "os={{ os }}",
                "name={{ vars.git.user_name }}",
                "{% include 'shared.txt' %}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "shared.txt").write_text("shared=1\n", encoding="utf-8")

    monkeypatch.setenv("DOTMAN_PROFILE", "basic")
    monkeypatch.setenv("DOTMAN_OS", "linux")
    monkeypatch.setenv("DOTMAN_VAR_git__user_name", "Example User")

    exit_code = main(["render", "jinja", str(template_path)])

    assert exit_code == 0
    assert capsys.readouterr().out == "profile=basic\nos=linux\nname=Example User\nshared=1\n"


def test_render_jinja_cli_accepts_explicit_profile_os_and_vars(tmp_path: Path, capsys) -> None:
    template_path = tmp_path / "profile"
    template_path.write_text(
        "profile={{ profile }} os={{ os }} name={{ vars.git.user_name }}\n",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "render",
            "jinja",
            "--profile",
            "work",
            "--os",
            "darwin",
            "--var",
            "git.user_name=Work User",
            str(template_path),
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out == "profile=work os=darwin name=Work User\n"


@pytest.mark.parametrize(
    ("args", "expected_error"),
    [
        (["render", "jinja", "--var", "missing_equals", "template.txt"], "invalid --var assignment"),
        (["render", "jinja", "missing.txt"], "jinja render failed"),
    ],
)
def test_render_jinja_cli_reports_invalid_input(args: list[str], expected_error: str, capsys) -> None:
    exit_code = main(args)

    assert exit_code == 2
    assert expected_error in capsys.readouterr().err
