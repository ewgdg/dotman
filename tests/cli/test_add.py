from __future__ import annotations

import json
from pathlib import Path

from types import SimpleNamespace

import dotman.add as add_module
import dotman.cli as cli
from dotman.add import AddOperationResult, AddReviewResult, review_add_manifest
from dotman.cli import main

from tests.helpers import capture_parser_help, write_named_manager_config


def _write_repo(repo_root: Path, packages: dict[str, str] | None = None) -> None:
    for package_id, manifest_text in (packages or {}).items():
        package_root = repo_root / "packages" / Path(*package_id.split("/"))
        package_root.mkdir(parents=True, exist_ok=True)
        (package_root / "package.toml").write_text(manifest_text, encoding="utf-8")


def test_add_help_uses_live_path_then_optional_package_query(capsys) -> None:
    output = capture_parser_help(capsys, "add")

    assert "usage: dotman add [-h] <live-path> [<package-query>]" in output
    assert "<live-path>" in output
    assert "[<package-query>]" in output


def test_add_cli_updates_existing_package_manifest_in_json_mode(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    live_path = home / ".gitconfig"
    live_path.write_text("[user]\nname = Example\n", encoding="utf-8")
    live_path.chmod(0o600)

    repo_root = tmp_path / "repo"
    _write_repo(repo_root, {"git": 'id = "git"\n'})
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})

    exit_code = main([
        "--config",
        str(config_path),
        "--json",
        "add",
        str(live_path),
        "fixture:git",
    ])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "add"
    assert payload["repo"] == "fixture"
    assert payload["package_id"] == "git"
    assert payload["created_package"] is False
    assert payload["target"]["name"] == "f_gitconfig"
    assert payload["target"]["source"] == "files/gitconfig"
    assert payload["target"]["path"] == "~/.gitconfig"
    assert payload["target"]["chmod"] == "600"

    manifest_text = (repo_root / "packages" / "git" / "package.toml").read_text(encoding="utf-8")
    assert '[targets.f_gitconfig]' in manifest_text
    assert 'source = "files/gitconfig"' in manifest_text
    assert 'path = "~/.gitconfig"' in manifest_text
    assert 'chmod = "600"' in manifest_text


def test_add_cli_creates_new_package_manifest_from_relative_live_path(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(home)

    live_path = home / ".config" / "nvim" / "init.lua"
    live_path.parent.mkdir(parents=True)
    live_path.write_text("vim.g.mapleader = ','\n", encoding="utf-8")
    live_path.chmod(0o644)

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})

    exit_code = main([
        "--config",
        str(config_path),
        "--json",
        "add",
        ".config/nvim/init.lua",
        "fixture:newpkg",
    ])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["created_package"] is True
    assert payload["package_id"] == "newpkg"
    assert payload["target"]["name"] == "f_config_nvim_init_lua"
    assert payload["target"]["source"] == "files/config/nvim/init.lua"
    assert payload["target"]["path"] == "~/.config/nvim/init.lua"
    assert "chmod" not in payload["target"]

    manifest_path = repo_root / "packages" / "newpkg" / "package.toml"
    assert manifest_path.read_text(encoding="utf-8") == "\n".join(
        [
            'id = "newpkg"',
            "",
            "[targets.f_config_nvim_init_lua]",
            'source = "files/config/nvim/init.lua"',
            'path = "~/.config/nvim/init.lua"',
            "",
        ]
    )


def test_add_cli_suffixes_duplicate_target_key_in_same_package(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    live_path = home / ".gitconfig"
    live_path.write_text("new\n", encoding="utf-8")

    repo_root = tmp_path / "repo"
    _write_repo(
        repo_root,
        {
            "git": "\n".join(
                [
                    'id = "git"',
                    "",
                    "[targets.f_gitconfig]",
                    'source = "files/other"',
                    'path = "~/.other"',
                    "",
                ]
            )
        },
    )
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})

    exit_code = main([
        "--config",
        str(config_path),
        "--json",
        "add",
        str(live_path),
        "fixture:git",
    ])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["target"]["name"] == "f_gitconfig_2"
    manifest_text = (repo_root / "packages" / "git" / "package.toml").read_text(encoding="utf-8")
    assert "[targets.f_gitconfig_2]" in manifest_text


def test_add_cli_fails_when_package_already_declares_same_live_path(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    live_path = home / ".gitconfig"
    live_path.write_text("new\n", encoding="utf-8")

    repo_root = tmp_path / "repo"
    _write_repo(
        repo_root,
        {
            "git": "\n".join(
                [
                    'id = "git"',
                    "",
                    "[targets.existing]",
                    'source = "files/gitconfig"',
                    'path = "~/.gitconfig"',
                    "",
                ]
            )
        },
    )
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})

    exit_code = main([
        "--config",
        str(config_path),
        "add",
        str(live_path),
        "fixture:git",
    ])

    assert exit_code == 2
    assert "already declares target path '~/.gitconfig'" in capsys.readouterr().err


def test_add_cli_strips_leading_dots_from_source_components_outside_home(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    live_path = tmp_path / "outside" / ".ssh" / "ssh_config"
    live_path.parent.mkdir(parents=True)
    live_path.write_text("Host *\n", encoding="utf-8")

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})

    exit_code = main([
        "--config",
        str(config_path),
        "--json",
        "add",
        str(live_path),
        "fixture:ssh",
    ])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["target"]["path"] == str(live_path.resolve())
    assert payload["target"]["source"].endswith("/outside/ssh/ssh_config")
    assert "/.ssh/" not in payload["target"]["source"]


def test_add_cli_interactively_allows_create_when_package_query_is_omitted(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    live_path = home / ".config" / "foo.conf"
    live_path.parent.mkdir(parents=True)
    live_path.write_text("value\n", encoding="utf-8")

    repo_root = tmp_path / "repo"
    _write_repo(repo_root, {"existing": 'id = "existing"\n'})
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})

    answers = iter(["", "1", "newpkg"])
    monkeypatch.setattr(cli, "prompt", lambda _message: next(answers))

    exit_code = main([
        "--config",
        str(config_path),
        "add",
        str(live_path),
    ])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Select a package for add:" in output
    assert "created package config fixture:newpkg" in output
    assert (repo_root / "packages" / "newpkg" / "package.toml").exists()


def test_review_add_manifest_uses_dedicated_add_review_wording(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manifest_path = tmp_path / "package.toml"
    manifest_path.write_text('id = "git"\n', encoding="utf-8")
    monkeypatch.setenv("EDITOR", "nvim")

    recorded: dict[str, object] = {}

    def fake_run(command: list[str], check: bool):
        recorded["command"] = command
        recorded["check"] = check
        review_path = Path(command[1])
        editable_path = Path(command[2])
        assert review_path.name == "add-review.md"
        review_text = review_path.read_text(encoding="utf-8")
        assert "# Dotman Add Review" in review_text
        assert "Nothing is written back to the repo until dotman asks for confirmation after the editor exits." in review_text
        assert "## Summary" in review_text
        assert "- action: update package manifest" in review_text
        assert "- package: fixture:git" in review_text
        assert "- target: f_gitconfig [file]" not in review_text
        assert "- source: files/gitconfig" not in review_text
        assert "- path: ~/.gitconfig" not in review_text
        assert f"- package manifest path: {manifest_path}" in review_text
        assert f"- current manifest: {manifest_path}" in review_text
        assert "- proposed manifest: proposed package.toml" in review_text
        assert f"- editable manifest copy: {editable_path}" in review_text
        assert "--- current package.toml" in review_text
        assert "+++ proposed package.toml" in review_text
        editable_path.write_text('id = "git"\n\n[targets.f_gitconfig]\n', encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(add_module.subprocess, "run", fake_run)

    result = AddOperationResult(
        repo_name="fixture",
        package_id="git",
        manifest_path=manifest_path,
        target_name="f_gitconfig",
        target_kind="file",
        source_path="files/gitconfig",
        config_path="~/.gitconfig",
        chmod=None,
        created_package=False,
        before_text='id = "git"\n',
        after_text='id = "git"\n\n[targets.f_gitconfig]\n',
    )

    review_result = review_add_manifest(result)
    assert review_result is not None
    assert review_result.exit_code == 0
    assert review_result.manifest_text == 'id = "git"\n\n[targets.f_gitconfig]\n'
    assert recorded["check"] is False
    assert recorded["command"][0] == "nvim"


def test_add_cli_reports_no_effective_package_config_changes_after_editor_review(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    live_path = home / ".config" / "foo.conf"
    live_path.parent.mkdir(parents=True)
    live_path.write_text("value\n", encoding="utf-8")

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    manifest_path = repo_root / "packages" / "git" / "package.toml"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text('id = "git"\n', encoding="utf-8")
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})

    monkeypatch.setattr(cli, "add_editor_available", lambda: True)

    def fake_review(result: AddOperationResult) -> AddReviewResult:
        return AddReviewResult(exit_code=0, manifest_text=result.before_text)

    monkeypatch.setattr(cli, "review_add_manifest", fake_review)
    prompt_messages: list[str] = []
    monkeypatch.setattr(
        cli,
        "prompt",
        lambda message: prompt_messages.append(message) or "",
    )

    exit_code = main([
        "--config",
        str(config_path),
        "add",
        str(live_path),
        "fixture:git",
    ])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert prompt_messages == []
    assert "No package config changes." in output
    assert manifest_path.read_text(encoding="utf-8") == 'id = "git"\n'



def test_add_cli_keeps_manifest_unchanged_when_editor_review_is_declined(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    live_path = home / ".config" / "foo.conf"
    live_path.parent.mkdir(parents=True)
    live_path.write_text("value\n", encoding="utf-8")

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})

    monkeypatch.setattr(cli, "add_editor_available", lambda: True)
    monkeypatch.setattr(
        cli,
        "review_add_manifest",
        lambda result: AddReviewResult(exit_code=0, manifest_text=result.after_text),
    )
    prompt_messages: list[str] = []
    monkeypatch.setattr(
        cli,
        "prompt",
        lambda message: prompt_messages.append(message) or "",
    )

    exit_code = main([
        "--config",
        str(config_path),
        "add",
        str(live_path),
        "fixture:newpkg",
    ])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert prompt_messages[-1] == "Write package config changes for fixture:newpkg? [y/N] "
    assert "kept package config unchanged fixture:newpkg" in output
    assert not (repo_root / "packages" / "newpkg" / "package.toml").exists()
