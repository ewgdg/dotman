from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import dotman.cli as cli
import pytest
from dotman.cli import PendingSelectionItem, main, prompt_for_excluded_items
from dotman.models import Binding, BindingPlan, DirectoryPlanItem, HookPlan, TargetPlan

from test_support import (
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


def test_push_cli_uses_tracked_binding_profile_without_prompting(
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
            "push",
            "git",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "push"
    assert payload["bindings"][0]["selector"] == "git"
    assert payload["bindings"][0]["profile"] == "basic"
    assert payload["bindings"][0]["targets"][0]["action"] == "create"

def test_push_cli_errors_for_untracked_binding(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    exit_code = main(
        [
            "--config",
            str(write_manager_config(tmp_path)),
            "push",
            "example:git",
        ]
    )

    assert exit_code == 2
    assert "is not currently tracked" in capsys.readouterr().err

def test_push_cli_interactively_selects_ambiguous_tracked_binding(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    answers = iter(["2"])
    monkeypatch.setattr(cli, "prompt", lambda _message: next(answers))
    monkeypatch.setattr(cli, "filter_plans_for_interactive_selection", lambda *, plans, operation, json_output: list(plans))
    monkeypatch.setattr(cli, "review_plans_for_interactive_diffs", lambda *, plans, operation, json_output: True)

    config_path = write_named_manager_config(
        tmp_path,
        {
            "alpha": REFERENCE_REPO,
            "beta": REFERENCE_REPO,
        },
    )
    for repo_name in ("alpha", "beta"):
        state_dir = tmp_path / "state" / repo_name
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "bindings.toml").write_text(
            "\n".join(
                [
                    "version = 1",
                    "",
                    "[[bindings]]",
                    f'repo = "{repo_name}"',
                    'selector = "sunshine"',
                    'profile = "host/linux"',
                    "",
                ]
            ),
            encoding="utf-8",
        )

    exit_code = main(
        [
            "--config",
            str(config_path),
            "push",
            "sunshine",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Select a tracked binding for 'sunshine':" in output
    assert "alpha:sunshine@host/linux" in output
    assert "beta:sunshine@host/linux" in output
    assert "sunshine:selected_config -> create" in output

def test_push_cli_uses_state_bindings_in_dry_run_json(
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
            "push",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "push"
    assert payload["bindings"][0]["selector"] == "git"
    assert payload["bindings"][0]["profile"] == "basic"

def test_push_cli_combined_selection_menu_excludes_selected_targets_across_tracked_bindings(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    answers = iter(["1", ""])
    monkeypatch.setattr(cli, "prompt", lambda _message: next(answers))

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
            "push",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Select items to exclude from push:" in output
    assert "example:git@basic\n" not in output
    assert "git:gitconfig -> add" not in output
    assert "example:nvim@basic\n" not in output
    assert "nvim:init_lua -> create" in output

def test_push_cli_enters_diff_review_menu_after_selection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(cli, "prompt", lambda _message: "")
    recorded: dict[str, object] = {}

    def fake_run_diff_review_menu(review_items, *, operation: str) -> bool:
        recorded["operation"] = operation
        recorded["item_count"] = len(review_items)
        return True

    monkeypatch.setattr(cli, "run_diff_review_menu", fake_run_diff_review_menu)

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
            "push",
        ]
    )

    assert exit_code == 0
    assert recorded == {
        "operation": "push",
        "item_count": 1,
    }

def test_push_cli_runs_diff_review_menu_when_user_accepts_default_yes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(cli, "prompt", lambda _message: "")
    recorded: dict[str, object] = {}

    def fake_run_diff_review_menu(review_items, *, operation: str) -> bool:
        recorded["operation"] = operation
        recorded["item_count"] = len(review_items)
        recorded["first_action"] = review_items[0].action
        return True

    monkeypatch.setattr(cli, "run_diff_review_menu", fake_run_diff_review_menu)

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
            "push",
        ]
    )

    assert exit_code == 0
    assert recorded == {
        "operation": "push",
        "item_count": 1,
        "first_action": "create",
    }

def test_push_cli_hides_noop_bindings_after_combined_selection_filter(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    (home / ".config" / "nvim").mkdir(parents=True)
    (home / ".config" / "nvim" / "init.lua").write_text(
        'vim.g.mapleader = " "\nvim.cmd.colorscheme("industry")\n',
        encoding="utf-8",
    )
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(cli, "prompt", lambda _message: "1")

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
            "push",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Select items to exclude from push:" in output
    assert "example:git@basic\n" not in output
    assert "example:nvim@basic\n" not in output
    assert "git:gitconfig -> create" not in output
    assert "nvim:init_lua -> noop" not in output

def test_push_cli_skips_diff_review_for_json_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(
        cli,
        "run_diff_review_menu",
        lambda review_items, *, operation: (_ for _ in ()).throw(AssertionError("review menu should not run")),
    )

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
            "push",
        ]
    )

    assert exit_code == 0

def test_push_cli_allows_package_selected_through_tracked_owner_binding(
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
            "push",
            "nvim",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "push"
    assert payload["bindings"][0]["selector"] == "nvim"
    assert payload["bindings"][0]["profile"] == "basic"
    assert payload["bindings"][0]["targets"][0]["action"] == "create"
