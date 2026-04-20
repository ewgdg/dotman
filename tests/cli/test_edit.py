from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import dotman.cli as cli

from dotman.cli import main

from tests.helpers import write_named_manager_config


def _write_edit_repo(repo_root: Path) -> None:
    (repo_root / "profiles").mkdir(parents=True, exist_ok=True)
    (repo_root / "packages" / "git" / "files").mkdir(parents=True, exist_ok=True)
    (repo_root / "packages" / "altgit" / "files").mkdir(parents=True, exist_ok=True)
    (repo_root / "packages" / "ssh" / "files" / "ssh").mkdir(parents=True, exist_ok=True)
    (repo_root / "packages" / "nvim" / "files" / "config" / "nvim").mkdir(parents=True, exist_ok=True)
    (repo_root / "packages" / "note" / "files").mkdir(parents=True, exist_ok=True)

    (repo_root / "profiles" / "basic.toml").write_text("", encoding="utf-8")
    (repo_root / "packages" / "git" / "package.toml").write_text(
        "\n".join(
            [
                'id = "git"',
                "",
                "[targets.gitconfig]",
                'source = "files/gitconfig"',
                'path = "~/.gitconfig"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "git" / "files" / "gitconfig").write_text("[user]\n", encoding="utf-8")
    (repo_root / "packages" / "altgit" / "files" / "gitconfig").write_text("[user]\n", encoding="utf-8")
    (repo_root / "packages" / "ssh" / "files" / "ssh" / "config").write_text("Host *\n", encoding="utf-8")
    (repo_root / "packages" / "nvim" / "files" / "config" / "nvim" / "init.lua").write_text(
        "return {}\n",
        encoding="utf-8",
    )
    (repo_root / "packages" / "note" / "files" / "note.txt").write_text("note\n", encoding="utf-8")
    (repo_root / "packages" / "altgit" / "package.toml").write_text(
        "\n".join(
            [
                'id = "altgit"',
                "",
                "[targets.gitconfig]",
                'source = "files/gitconfig"',
                'path = "~/.config/altgit/config"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "ssh" / "package.toml").write_text(
        "\n".join(
            [
                'id = "ssh"',
                "",
                "[targets.ssh_dir]",
                'source = "files/ssh"',
                'path = "~/.ssh"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "nvim" / "package.toml").write_text(
        "\n".join(
            [
                'id = "nvim"',
                "",
                "[targets.init_lua]",
                'source = "files/config/nvim/init.lua"',
                'path = "~/.config/nvim/init.lua"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "note" / "package.toml").write_text(
        "\n".join(
            [
                'id = "note"',
                "",
                "[targets.note]",
                'source = "files/note.txt"',
                'path = "~/.config/note.txt"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_tracked_binding_states(
    state_root: Path,
    *,
    repo_name: str,
    bindings: list[tuple[str, str]],
) -> None:
    state_dir = state_root / "dotman" / "repos" / repo_name
    state_dir.mkdir(parents=True, exist_ok=True)
    lines = ["schema_version = 1", ""]
    for selector, profile in bindings:
        lines.extend(
            [
                "[[packages]]",
                f'repo = "{repo_name}"',
                f'package_id = "{selector}"',
                f'profile = "{profile}"',
                "",
            ]
        )
    (state_dir / "tracked-packages.toml").write_text("\n".join(lines), encoding="utf-8")


def _write_tracked_binding_state(state_root: Path, *, repo_name: str, selector: str, profile: str) -> None:
    _write_tracked_binding_states(
        state_root,
        repo_name=repo_name,
        bindings=[(selector, profile)],
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
    assert (
        capsys.readouterr().out
        == f"No editor configured. Source path: {repo_root / 'packages' / 'git'}\n"
    )


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


def test_edit_cli_sugar_opens_tracked_package_directory_with_editor(
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

    exit_code = main(["--config", str(config_path), "edit", "git"])

    assert exit_code == 0
    assert recorded["check"] is False
    assert recorded["command"] == ["nvim", str(repo_root / "packages" / "git")]


def test_edit_cli_sugar_keeps_repo_qualified_target_query_target_intent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    alpha_root = tmp_path / "alpha"
    beta_root = tmp_path / "beta"
    _write_edit_repo(alpha_root)
    _write_edit_repo(beta_root)
    (beta_root / "packages" / "nvim" / "package.toml").write_text(
        "\n".join(
            [
                'id = "nvim"',
                "",
                "[targets.init_lua]",
                'source = "files/config/nvim/init.lua"',
                'path = "~/.config/nvim-beta/init.lua"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    config_path = write_named_manager_config(tmp_path, {"alpha": alpha_root, "beta": beta_root})
    _write_tracked_binding_state(tmp_path / "state", repo_name="alpha", selector="nvim", profile="basic")
    _write_tracked_binding_state(tmp_path / "state", repo_name="beta", selector="nvim", profile="basic")

    monkeypatch.setenv("EDITOR", "nvim")
    recorded: dict[str, object] = {}

    def fake_run(command: list[str], check: bool):
        recorded["command"] = command
        recorded["check"] = check
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("dotman.cli.subprocess.run", fake_run)

    exit_code = main(["--config", str(config_path), "edit", "beta:nvim.init_lua"])

    assert exit_code == 0
    assert recorded["command"] == [
        "nvim",
        str(beta_root / "packages" / "nvim" / "files" / "config" / "nvim" / "init.lua"),
    ]


def test_edit_target_cli_prints_repo_file_path_when_no_editor_is_configured(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)

    repo_root = tmp_path / "repo"
    _write_edit_repo(repo_root)
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    _write_tracked_binding_states(
        tmp_path / "state",
        repo_name="fixture",
        bindings=[("git", "basic"), ("ssh", "basic"), ("nvim", "basic"), ("altgit", "basic")],
    )

    exit_code = main(["--config", str(config_path), "edit", "target", "nvim.init_lua"])

    assert exit_code == 0
    assert (
        capsys.readouterr().out
        == "No editor configured. Source path: "
        f"{repo_root / 'packages' / 'nvim' / 'files' / 'config' / 'nvim' / 'init.lua'}\n"
    )


def test_edit_cli_sugar_rejects_cross_kind_exact_ambiguity(
    tmp_path: Path,
    capsys,
) -> None:
    repo_root = tmp_path / "repo"
    _write_edit_repo(repo_root)
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    _write_tracked_binding_states(
        tmp_path / "state",
        repo_name="fixture",
        bindings=[("note", "basic")],
    )

    exit_code = main(["--config", str(config_path), "edit", "note"])

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "edit query 'note' is ambiguous:" in err
    assert "package fixture:note" in err
    assert "target fixture:note.note" in err


def test_edit_target_cli_opens_tracked_file_target_repo_path_with_editor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo_root = tmp_path / "repo"
    _write_edit_repo(repo_root)
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    _write_tracked_binding_states(
        tmp_path / "state",
        repo_name="fixture",
        bindings=[("git", "basic"), ("ssh", "basic"), ("nvim", "basic")],
    )

    monkeypatch.setenv("EDITOR", "nvim -d")
    recorded: dict[str, object] = {}

    def fake_run(command: list[str], check: bool):
        recorded["command"] = command
        recorded["check"] = check
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("dotman.cli.subprocess.run", fake_run)

    exit_code = main(["--config", str(config_path), "edit", "target", "nvim.init_lua"])

    assert exit_code == 0
    assert recorded["check"] is False
    assert recorded["command"] == [
        "nvim",
        str(repo_root / "packages" / "nvim" / "files" / "config" / "nvim" / "init.lua"),
    ]


def test_edit_target_cli_opens_tracked_directory_target_repo_path_with_editor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo_root = tmp_path / "repo"
    _write_edit_repo(repo_root)
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    _write_tracked_binding_states(
        tmp_path / "state",
        repo_name="fixture",
        bindings=[("ssh", "basic")],
    )

    monkeypatch.setenv("EDITOR", "nvim")
    recorded: dict[str, object] = {}

    def fake_run(command: list[str], check: bool):
        recorded["command"] = command
        recorded["check"] = check
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("dotman.cli.subprocess.run", fake_run)

    exit_code = main(["--config", str(config_path), "edit", "target", "ssh_dir"])

    assert exit_code == 0
    assert recorded["check"] is False
    assert recorded["command"] == ["nvim", str(repo_root / "packages" / "ssh" / "files" / "ssh")]


def test_edit_target_cli_resolves_repo_qualified_target_query(
    tmp_path: Path,
    monkeypatch,
) -> None:
    alpha_root = tmp_path / "alpha"
    beta_root = tmp_path / "beta"
    _write_edit_repo(alpha_root)
    _write_edit_repo(beta_root)
    (beta_root / "packages" / "nvim" / "package.toml").write_text(
        "\n".join(
            [
                'id = "nvim"',
                "",
                "[targets.init_lua]",
                'source = "files/config/nvim/init.lua"',
                'path = "~/.config/nvim-beta/init.lua"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    config_path = write_named_manager_config(tmp_path, {"alpha": alpha_root, "beta": beta_root})
    _write_tracked_binding_state(tmp_path / "state", repo_name="alpha", selector="nvim", profile="basic")
    _write_tracked_binding_state(tmp_path / "state", repo_name="beta", selector="nvim", profile="basic")

    monkeypatch.setenv("EDITOR", "nvim")
    recorded: dict[str, object] = {}

    def fake_run(command: list[str], check: bool):
        recorded["command"] = command
        recorded["check"] = check
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("dotman.cli.subprocess.run", fake_run)

    exit_code = main(["--config", str(config_path), "edit", "target", "beta:nvim.init_lua"])

    assert exit_code == 0
    assert recorded["command"] == [
        "nvim",
        str(beta_root / "packages" / "nvim" / "files" / "config" / "nvim" / "init.lua"),
    ]


def test_edit_target_cli_interactively_selects_ambiguous_target(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    repo_root = tmp_path / "repo"
    _write_edit_repo(repo_root)
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    _write_tracked_binding_states(
        tmp_path / "state",
        repo_name="fixture",
        bindings=[("git", "basic"), ("altgit", "basic")],
    )

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(cli, "colors_enabled", lambda: False)
    monkeypatch.setattr(cli, "_fzf_available", lambda: False)
    answers = iter(["2"])
    monkeypatch.setattr(cli, "prompt", lambda _message: next(answers))
    monkeypatch.setenv("EDITOR", "nvim")
    recorded: dict[str, object] = {}

    def fake_run(command: list[str], check: bool):
        recorded["command"] = command
        recorded["check"] = check
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("dotman.cli.subprocess.run", fake_run)

    exit_code = main(["--config", str(config_path), "edit", "target", "gitconfig"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Select a tracked target for 'gitconfig':" in output
    assert "fixture:altgit.gitconfig" in output
    assert "fixture:git.gitconfig" in output
    assert recorded["command"] == ["nvim", str(repo_root / "packages" / "git" / "files" / "gitconfig")]


def test_edit_target_cli_rejects_ambiguous_target_query_in_non_interactive_mode(
    tmp_path: Path,
    capsys,
) -> None:
    repo_root = tmp_path / "repo"
    _write_edit_repo(repo_root)
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    _write_tracked_binding_states(
        tmp_path / "state",
        repo_name="fixture",
        bindings=[("git", "basic"), ("altgit", "basic")],
    )

    exit_code = main(["--config", str(config_path), "edit", "target", "gitconfig"])

    assert exit_code == 2
    assert "tracked target 'gitconfig' is ambiguous:" in capsys.readouterr().err


def test_edit_target_cli_rejects_untracked_package_target(
    tmp_path: Path,
    capsys,
) -> None:
    repo_root = tmp_path / "repo"
    _write_edit_repo(repo_root)
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    _write_tracked_binding_state(tmp_path / "state", repo_name="fixture", selector="git", profile="basic")

    exit_code = main(["--config", str(config_path), "edit", "target", "note.note"])

    assert exit_code == 2
    assert "tracked target 'note.note' did not match any tracked target" in capsys.readouterr().err


def test_edit_target_cli_rejects_malformed_target_query(
    tmp_path: Path,
    capsys,
) -> None:
    repo_root = tmp_path / "repo"
    _write_edit_repo(repo_root)
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    _write_tracked_binding_state(tmp_path / "state", repo_name="fixture", selector="nvim", profile="basic")

    exit_code = main(["--config", str(config_path), "edit", "target", "nvim."])

    assert exit_code == 2
    assert "invalid tracked target selector 'nvim.'" in capsys.readouterr().err
