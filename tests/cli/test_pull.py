from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import dotman.cli as cli
import pytest
from dotman.cli import PendingSelectionItem, main, prompt_for_excluded_items
from dotman.models import FullSpecSelector, DirectoryPlanItem, HookPlan, TargetPlan

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
    state_dir = tmp_path / "state" / "dotman" / "repos" / "example"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "tracked-packages.toml").write_text(
        "\n".join(
            [
                "schema_version = 1",
                "",
                "[[packages]]",
                'repo = "example"',
                'package_id = "nvim"',
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
    assert len(payload["package_entries"]) == 1
    assert payload["package_entries"][0]["repo"] == "example"
    assert payload["package_entries"][0]["package_id"] == "nvim"
    assert payload["package_entries"][0]["profile"] == "basic"
    assert payload["package_entries"][0]["targets"][0]["action"] == "update"
    assert (tmp_path / "state" / "dotman" / "repos" / "example" / "tracked-packages.toml").read_text(encoding="utf-8") == "\n".join(
        [
            "schema_version = 1",
            "",
            "[[packages]]",
            'repo = "example"',
            'package_id = "nvim"',
            'profile = "basic"',
            "",
        ]
    )

def test_pull_cli_reviews_diffs_before_selection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    (home / ".config" / "nvim").mkdir(parents=True)
    (home / ".config" / "nvim" / "init.lua").write_text(
        'vim.g.mapleader = ","\nvim.cmd.colorscheme("industry")\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    order: list[str] = []
    recorded: dict[str, object] = {}

    def fake_run_diff_review_menu(
        review_items,
        *,
        operation: str,
        full_paths: bool = False,
        assume_yes: bool = False,
    ) -> bool:
        order.append("diff")
        recorded["operation"] = operation
        recorded["item_count"] = len(review_items)
        recorded["full_paths"] = full_paths
        return True

    monkeypatch.setattr(cli, "run_diff_review_menu", fake_run_diff_review_menu)

    original_filter = cli.filter_plans_for_interactive_selection

    def fake_filter_plans_for_interactive_selection(*, plans, operation, json_output, full_paths=False):
        order.append("selection")
        return original_filter(
            plans=plans,
            operation=operation,
            json_output=json_output,
            full_paths=full_paths,
        )

    monkeypatch.setattr(cli, "filter_plans_for_interactive_selection", fake_filter_plans_for_interactive_selection)
    monkeypatch.setattr(cli, "prompt", lambda _message: "")

    state_dir = tmp_path / "state" / "dotman" / "repos" / "example"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "tracked-packages.toml").write_text(
        "\n".join(
            [
                "schema_version = 1",
                "",
                "[[packages]]",
                'repo = "example"',
                'package_id = "nvim"',
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

    assert exit_code == 0
    assert order == ["diff", "selection"]
    assert recorded == {
        "operation": "pull",
        "item_count": 1,
        "full_paths": False,
    }


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
        lambda review_items, *, operation, full_paths=False, assume_yes=False: False,
    )

    state_dir = tmp_path / "state" / "dotman" / "repos" / "example"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "tracked-packages.toml").write_text(
        "\n".join(
            [
                "schema_version = 1",
                "",
                "[[packages]]",
                'repo = "example"',
                'package_id = "nvim"',
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


def test_pull_cli_uses_resolver_when_input_is_ambiguous_between_partial_binding_and_owned_package(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    selected_menu: dict[str, object] = {}

    def select_git_target(*, header_text, option_labels, option_search_fields=None, option_display_fields=None):
        selected_menu["header_text"] = header_text
        selected_menu["option_labels"] = tuple(option_labels)
        return next(index for index, label in enumerate(option_labels) if "example:git@basic" in label)

    monkeypatch.setattr(cli, "select_menu_option", select_git_target)
    monkeypatch.setattr(
        cli,
        "prompt",
        lambda _message: (_ for _ in ()).throw(AssertionError("expected resolver menu, not partial confirmation")),
    )
    monkeypatch.setattr(
        cli,
        "filter_plans_for_interactive_selection",
        lambda *, plans, operation, json_output, full_paths=False: list(plans),
    )
    monkeypatch.setattr(
        cli,
        "review_plans_for_interactive_diffs",
        lambda *, plans, operation, json_output, full_paths=False, assume_yes=False: True,
    )

    config_path = write_manager_config(tmp_path)
    state_dir = tmp_path / "state" / "dotman" / "repos" / "example"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "tracked-packages.toml").write_text(
        "\n".join(
            [
                "schema_version = 1",
                "",
                "[[packages]]",
                'repo = "example"',
                'package_id = "core-cli-meta"',
                'profile = "basic"',
                "",
                "[[packages]]",
                'repo = "example"',
                'package_id = "work/git"',
                'profile = "work"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--config",
            str(config_path),
            "pull",
            "--dry-run",
            "git",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert selected_menu["header_text"] == "Select a tracked package entry for 'git':"
    assert any("example:git@basic" in label for label in selected_menu["option_labels"])
    assert any("example:work/git@work" in label for label in selected_menu["option_labels"])
    assert ":: example:git@basic" in output
    assert "git.gitconfig -> delete" in output


def test_pull_cli_accepts_partial_owned_package_match(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    prompts: list[str] = []

    def confirm_partial_match(message: str) -> str:
        prompts.append(message)
        return "y"

    monkeypatch.setattr(cli, "prompt", confirm_partial_match)
    monkeypatch.setattr(
        cli,
        "filter_plans_for_interactive_selection",
        lambda *, plans, operation, json_output, full_paths=False: list(plans),
    )
    monkeypatch.setattr(
        cli,
        "review_plans_for_interactive_diffs",
        lambda *, plans, operation, json_output, full_paths=False, assume_yes=False: True,
    )

    config_path = write_manager_config(tmp_path)
    state_dir = tmp_path / "state" / "dotman" / "repos" / "example"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "tracked-packages.toml").write_text(
        "\n".join(
            [
                "schema_version = 1",
                "",
                "[[packages]]",
                'repo = "example"',
                'package_id = "core-cli-meta"',
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
            "pull",
            "--dry-run",
            "nv",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert prompts
    assert "nvim@basic" in prompts[0]
    assert ":: example:nvim@basic" in output
    assert "nvim.init_lua -> delete" in output


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
    state_dir = tmp_path / "state" / "dotman" / "repos" / "example"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "tracked-packages.toml").write_text(
        "\n".join(
            [
                "schema_version = 1",
                "",
                "[[packages]]",
                'repo = "example"',
                'package_id = "nvim"',
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
    assert payload["package_entries"][0]["repo"] == "example"
    assert payload["package_entries"][0]["package_id"] == "nvim"
    assert payload["package_entries"][0]["targets"][0]["action"] == "update"


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
    state_dir = tmp_path / "state" / "dotman" / "repos" / "example"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "tracked-packages.toml").write_text(
        "\n".join(
            [
                "schema_version = 1",
                "",
                "[[packages]]",
                'repo = "example"',
                'package_id = "nvim"',
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
    assert payload["package_entries"][0]["repo"] == "example"
    assert payload["package_entries"][0]["package_id"] == "nvim"
    assert payload["package_entries"][0]["targets"][0]["action"] == "update"


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
    state_dir = tmp_path / "state" / "dotman" / "repos" / "example"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "tracked-packages.toml").write_text(
        "\n".join(
            [
                "schema_version = 1",
                "",
                "[[packages]]",
                'repo = "example"',
                'package_id = "nvim"',
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
    assert "nvim.init_lua -> update" in output

def test_pull_cli_uses_tracked_binding_profile_without_prompting(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    config_path = write_manager_config(tmp_path)
    state_dir = tmp_path / "state" / "dotman" / "repos" / "example"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "tracked-packages.toml").write_text(
        "\n".join(
            [
                "schema_version = 1",
                "",
                "[[packages]]",
                'repo = "example"',
                'package_id = "git"',
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
    assert payload["package_entries"][0]["package_id"] == "git"
    assert payload["package_entries"][0]["profile"] == "basic"
    assert payload["package_entries"][0]["targets"][0]["action"] == "delete"

def test_pull_cli_allows_package_selected_through_tracked_owner_binding(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    config_path = write_manager_config(tmp_path)
    state_dir = tmp_path / "state" / "dotman" / "repos" / "example"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "tracked-packages.toml").write_text(
        "\n".join(
            [
                "schema_version = 1",
                "",
                "[[packages]]",
                'repo = "example"',
                'package_id = "core-cli-meta"',
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
    assert payload["package_entries"][0]["package_id"] == "nvim"
    assert payload["package_entries"][0]["profile"] == "basic"
    assert payload["package_entries"][0]["targets"][0]["action"] == "delete"
