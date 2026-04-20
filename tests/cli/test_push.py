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


def test_push_cli_uses_tracked_binding_profile_without_prompting(
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
            "--dry-run",
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

    config_path = write_named_manager_config(
        tmp_path,
        {
            "alpha": REFERENCE_REPO,
            "beta": REFERENCE_REPO,
        },
    )
    for repo_name in ("alpha", "beta"):
        state_dir = tmp_path / "state" / "dotman" / "repos" / repo_name
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
            "--dry-run",
            "sunshine",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Select a tracked package entry for 'sunshine':" in output
    assert "alpha:sunshine@host/linux" in output
    assert "beta:sunshine@host/linux" in output
    assert "sunshine.selected_config -> create" in output


def test_push_cli_uses_resolver_when_input_is_ambiguous_between_partial_binding_and_owned_package(
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
                "[[bindings]]",
                'repo = "example"',
                'selector = "work/git"',
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
            "push",
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
    assert "git.gitconfig -> create" in output


def test_push_cli_accepts_partial_owned_package_match(
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
            "push",
            "--dry-run",
            "nv",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert prompts
    assert "nvim@basic" in prompts[0]
    assert ":: example:nvim@basic" in output
    assert "nvim.init_lua -> create" in output


def test_push_cli_accepts_short_dry_run_flag(
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
            "-d",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "dry-run"
    assert payload["operation"] == "push"
    assert payload["bindings"][0]["selector"] == "git"
    assert payload["bindings"][0]["profile"] == "basic"


def test_push_cli_uses_state_bindings_in_dry_run_json(
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
            "--dry-run",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "push"
    assert payload["bindings"][0]["selector"] == "git"
    assert payload["bindings"][0]["profile"] == "basic"


def test_push_cli_human_dry_run_output_includes_context_and_hooks(
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
            "--dry-run",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "\n:: dry-run push\n" in output
    assert "preview only; no files or hooks will be changed" in output
    assert "packages: 1" in output
    assert "target actions: 1" in output
    assert "hook commands: 4" in output
    assert ":: example:git@basic" in output
    assert "hooks:" in output
    assert "[guard_push]" in output
    assert "[pre_push]" in output
    assert "[post_push]" in output
    assert "targets:" in output
    assert "git.gitconfig -> create" in output
    assert output.index("targets:") < output.index("hooks:")


def test_push_cli_human_dry_run_output_uses_full_path_when_requested(
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
            "--dry-run",
            "--full-path",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert str(EXAMPLE_REPO / "packages" / "git" / "files" / "gitconfig") in output
    assert str(home / ".gitconfig") in output
    assert "home/.../files/gitconfig" not in output
    assert "~/.gitconfig" not in output


def test_push_cli_human_dry_run_output_highlights_leaf_packages_not_root_binding(
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
            "push",
            "--dry-run",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert ":: example:core-cli-meta@basic" not in output
    assert ":: example:git@basic" in output
    assert ":: example:nvim@basic" in output

def test_push_cli_combined_selection_menu_excludes_selected_targets_across_tracked_bindings(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    answers = iter(["c", "1"])
    monkeypatch.setattr(cli, "prompt", lambda _message: next(answers))

    config_path = write_manager_config(tmp_path)
    state_dir = tmp_path / "state" / "dotman" / "repos" / "example"
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
            "--dry-run",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Select items to exclude from push:" in output
    assert ":: example:git@basic" not in output
    assert "git.gitconfig -> add" not in output
    assert ":: example:nvim@basic" in output
    assert "nvim.init_lua -> create" in output

def test_push_cli_reviews_diffs_before_selection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
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
        recorded["assume_yes"] = assume_yes
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

    config_path = write_manager_config(tmp_path)
    state_dir = tmp_path / "state" / "dotman" / "repos" / "example"
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
            "--dry-run",
        ]
    )

    assert exit_code == 0
    assert order == ["diff", "selection"]
    assert recorded == {
        "operation": "push",
        "item_count": 1,
        "full_paths": False,
        "assume_yes": False,
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

    def fake_run_diff_review_menu(
        review_items,
        *,
        operation: str,
        full_paths: bool = False,
        assume_yes: bool = False,
    ) -> bool:
        recorded["operation"] = operation
        recorded["item_count"] = len(review_items)
        recorded["first_action"] = review_items[0].action
        recorded["full_paths"] = full_paths
        recorded["assume_yes"] = assume_yes
        return True

    monkeypatch.setattr(cli, "run_diff_review_menu", fake_run_diff_review_menu)

    config_path = write_manager_config(tmp_path)
    state_dir = tmp_path / "state" / "dotman" / "repos" / "example"
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
            "--dry-run",
        ]
    )

    assert exit_code == 0
    assert recorded == {
        "operation": "push",
        "item_count": 1,
        "first_action": "create",
        "full_paths": False,
        "assume_yes": False,
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
    answers = iter(["c", "1"])
    monkeypatch.setattr(cli, "prompt", lambda _message: next(answers))

    config_path = write_manager_config(tmp_path)
    state_dir = tmp_path / "state" / "dotman" / "repos" / "example"
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
            "--dry-run",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Select items to exclude from push:" in output
    assert ":: example:git@basic" not in output
    assert ":: example:nvim@basic" not in output
    assert "git.gitconfig -> create" not in output
    assert "nvim.init_lua -> noop" not in output
    assert "no pending target actions" in output

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
        lambda review_items, *, operation, assume_yes=False: (_ for _ in ()).throw(AssertionError("review menu should not run")),
    )

    config_path = write_manager_config(tmp_path)
    state_dir = tmp_path / "state" / "dotman" / "repos" / "example"
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
            "--dry-run",
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
    state_dir = tmp_path / "state" / "dotman" / "repos" / "example"
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
            "--dry-run",
            "nvim",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "push"
    assert payload["bindings"][0]["selector"] == "nvim"
    assert payload["bindings"][0]["profile"] == "basic"
    assert payload["bindings"][0]["targets"][0]["action"] == "create"
