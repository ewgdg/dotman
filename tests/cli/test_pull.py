from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import dotman.cli as cli
import pytest
from dotman.cli import PendingSelectionItem, main, prompt_for_excluded_items
from dotman.models import Binding, BindingPlan, DirectoryPlanItem, HookPlan, TargetPlan

from tests.helpers import (
    EXAMPLE_REPO,
    REFERENCE_REPO,
    capture_parser_help,
    write_implicit_conflict_repo,
    write_manager_config,
    write_multi_instance_repo,
    write_named_manager_config,
    write_package_override_preview_repo,
    write_profile_switch_repo,
    write_untrack_conflict_repo,
)


def test_pull_cli_accepts_explicit_binding_and_does_not_write_state(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    (home / ".config" / "nvim").mkdir(parents=True)
    (home / ".config" / "nvim" / "init.lua").write_text(
        'vim.g.mapleader = ","\nvim.cmd.colorscheme("industry")\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))

    config_path = write_manager_config(tmp_path)
    state_dir = tmp_path / "state" / "example"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "bindings.toml").write_text(
        "\n".join(
            [
                "version = 1",
                "",
                "[[bindings]]",
                'repo = "example"',
                'selector = "nvim"',
                'profile = "basic"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--config",
            str(config_path),
            "--json",
            "pull",
            "--dry-run",
            "example:nvim@basic",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "pull"
    assert len(payload["bindings"]) == 1
    assert payload["bindings"][0]["repo"] == "example"
    assert payload["bindings"][0]["selector"] == "nvim"
    assert payload["bindings"][0]["profile"] == "basic"
    assert payload["bindings"][0]["targets"][0]["action"] == "update"
    assert (tmp_path / "state" / "example" / "bindings.toml").read_text(encoding="utf-8") == "\n".join(
        [
            "version = 1",
            "",
            "[[bindings]]",
            'repo = "example"',
            'selector = "nvim"',
            'profile = "basic"',
            "",
        ]
    )

def test_pull_cli_returns_130_when_diff_review_aborts(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    (home / ".config" / "nvim").mkdir(parents=True)
    (home / ".config" / "nvim" / "init.lua").write_text(
        'vim.g.mapleader = ","\nvim.cmd.colorscheme("industry")\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(cli, "prompt", lambda _message: "")
    monkeypatch.setattr(
        cli,
        "run_diff_review_menu",
        lambda review_items, *, operation, full_paths=False: False,
    )

    state_dir = tmp_path / "state" / "example"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "bindings.toml").write_text(
        "\n".join(
            [
                "version = 1",
                "",
                "[[bindings]]",
                'repo = "example"',
                'selector = "nvim"',
                'profile = "basic"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--config",
            str(write_manager_config(tmp_path)),
            "pull",
            "--dry-run",
        ]
    )

    assert exit_code == 130
    assert capsys.readouterr().err == "\ninterrupted\n"

def test_pull_cli_accepts_long_dry_run_flag(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    (home / ".config" / "nvim").mkdir(parents=True)
    (home / ".config" / "nvim" / "init.lua").write_text(
        'vim.g.mapleader = ","\nvim.cmd.colorscheme("industry")\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    state_dir = tmp_path / "state" / "example"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "bindings.toml").write_text(
        "\n".join(
            [
                "version = 1",
                "",
                "[[bindings]]",
                'repo = "example"',
                'selector = "nvim"',
                'profile = "basic"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--config",
            str(write_manager_config(tmp_path)),
            "--json",
            "pull",
            "--dry-run",
            "example:nvim@basic",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "dry-run"
    assert payload["operation"] == "pull"
    assert payload["bindings"][0]["repo"] == "example"
    assert payload["bindings"][0]["selector"] == "nvim"
    assert payload["bindings"][0]["targets"][0]["action"] == "update"


def test_pull_cli_emits_dry_run_json(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    (home / ".config" / "nvim").mkdir(parents=True)
    (home / ".config" / "nvim" / "init.lua").write_text(
        'vim.g.mapleader = ","\nvim.cmd.colorscheme("industry")\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    state_dir = tmp_path / "state" / "example"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "bindings.toml").write_text(
        "\n".join(
            [
                "version = 1",
                "",
                "[[bindings]]",
                'repo = "example"',
                'selector = "nvim"',
                'profile = "basic"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--config",
            str(write_manager_config(tmp_path)),
            "--json",
            "pull",
            "--dry-run",
            "example:nvim@basic",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "dry-run"
    assert payload["operation"] == "pull"
    assert payload["bindings"][0]["repo"] == "example"
    assert payload["bindings"][0]["selector"] == "nvim"
    assert payload["bindings"][0]["targets"][0]["action"] == "update"


def test_pull_cli_human_dry_run_output_includes_header_and_context(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    (home / ".config" / "nvim").mkdir(parents=True)
    (home / ".config" / "nvim" / "init.lua").write_text(
        'vim.g.mapleader = ","\nvim.cmd.colorscheme("industry")\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    state_dir = tmp_path / "state" / "example"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "bindings.toml").write_text(
        "\n".join(
            [
                "version = 1",
                "",
                "[[bindings]]",
                'repo = "example"',
                'selector = "nvim"',
                'profile = "basic"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--config",
            str(write_manager_config(tmp_path)),
            "pull",
            "--dry-run",
            "example:nvim@basic",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "\n:: dry-run pull\n" in output
    assert "preview only; no files or hooks will be changed" in output
    assert "packages: 1" in output
    assert "target actions: 1" in output
    assert "hook commands: 1" in output
    assert ":: example:nvim@basic" in output
    assert "targets:" in output
    assert "nvim:init_lua -> update" in output

def test_pull_cli_uses_tracked_binding_profile_without_prompting(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    config_path = write_manager_config(tmp_path)
    state_dir = tmp_path / "state" / "example"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "bindings.toml").write_text(
        "\n".join(
            [
                "version = 1",
                "",
                "[[bindings]]",
                'repo = "example"',
                'selector = "git"',
                'profile = "basic"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--config",
            str(config_path),
            "--json",
            "pull",
            "--dry-run",
            "git",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "pull"
    assert payload["bindings"][0]["selector"] == "git"
    assert payload["bindings"][0]["profile"] == "basic"
    assert payload["bindings"][0]["targets"][0]["action"] == "delete"

def test_pull_cli_allows_package_selected_through_tracked_owner_binding(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    config_path = write_manager_config(tmp_path)
    state_dir = tmp_path / "state" / "example"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "bindings.toml").write_text(
        "\n".join(
            [
                "version = 1",
                "",
                "[[bindings]]",
                'repo = "example"',
                'selector = "core-cli-meta"',
                'profile = "basic"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--config",
            str(config_path),
            "--json",
            "pull",
            "--dry-run",
            "nvim@basic",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "pull"
    assert payload["bindings"][0]["selector"] == "nvim"
    assert payload["bindings"][0]["profile"] == "basic"
    assert payload["bindings"][0]["targets"][0]["action"] == "delete"
