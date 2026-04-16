from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from dotman.cli import main

from tests.helpers import write_named_manager_config


def _write_edit_repo(repo_root: Path) -> None:
    (repo_root / "profiles").mkdir(parents=True, exist_ok=True)
    (repo_root / "packages" / "git").mkdir(parents=True, exist_ok=True)

    (repo_root / "profiles" / "basic.toml").write_text("", encoding="utf-8")
    (repo_root / "packages" / "git" / "package.toml").write_text('id = "git"\n', encoding="utf-8")


def _write_tracked_binding_state(state_root: Path, *, repo_name: str, selector: str, profile: str) -> None:
    state_dir = state_root / "dotman" / "repos" / repo_name
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "bindings.toml").write_text(
        "\n".join(
            [
                "version = 1",
                "",
                "[[bindings]]",
                f'repo = "{repo_name}"',
                f'selector = "{selector}"',
                f'profile = "{profile}"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_edit_cli_prints_package_directory_when_no_editor_is_configured(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)

    repo_root = tmp_path / "repo"
    _write_edit_repo(repo_root)
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    _write_tracked_binding_state(tmp_path / "state", repo_name="fixture", selector="git", profile="basic")

    exit_code = main(["--config", str(config_path), "edit", "package", "git"])

    assert exit_code == 0
    assert capsys.readouterr().out == f"{repo_root / 'packages' / 'git'}\n"


def test_edit_cli_opens_tracked_package_directory_with_editor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo_root = tmp_path / "repo"
    _write_edit_repo(repo_root)
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    _write_tracked_binding_state(tmp_path / "state", repo_name="fixture", selector="git", profile="basic")

    monkeypatch.setenv("EDITOR", "nvim -d")
    recorded: dict[str, object] = {}

    def fake_run(command: list[str], check: bool):
        recorded["command"] = command
        recorded["check"] = check
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("dotman.cli.subprocess.run", fake_run)

    exit_code = main(["--config", str(config_path), "edit", "package", "git"])

    assert exit_code == 0
    assert recorded["check"] is False
    assert recorded["command"] == ["nvim", str(repo_root / "packages" / "git")]
