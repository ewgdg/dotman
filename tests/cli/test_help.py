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


def test_track_help_uses_explicit_binding_placeholder(capsys) -> None:
    output = capture_parser_help(capsys, "track")
    assert "usage: dotman track [-h] <binding>" in output
    assert "positional arguments:" in output
    assert "<binding>" in output

def test_push_help_lists_dry_run_and_full_path_flags(capsys) -> None:
    output = capture_parser_help(capsys, "push")
    assert "usage: dotman push [-h] [-d] [--full-path] [<binding>]" in output
    assert "[<binding>]" in output
    assert "-d, --dry-run" in output
    assert "--full-path" in output


def test_pull_help_lists_dry_run_and_full_path_flags(capsys) -> None:
    output = capture_parser_help(capsys, "pull")
    assert "usage: dotman pull [-h] [-d] [--full-path] [<binding>]" in output
    assert "-d, --dry-run" in output
    assert "--full-path" in output

def test_top_level_help_uses_command_placeholder_and_summaries(capsys) -> None:
    output = capture_parser_help(capsys)
    assert "usage: dotman [-h] [--config <config-path>] [--json]" in output
    assert "[--file-symlink-mode <mode>] [--dir-symlink-mode <mode>]" in output
    assert "<command>" in output
    assert "commands:" in output
    assert "Track a binding in manager state" in output
    assert "Patch review content back into repo source" in output
    assert "Re-run a reconcile helper subcommand" in output
    assert "Render built-in template helpers" in output

def test_info_help_uses_nested_command_placeholder_and_summaries(capsys) -> None:
    output = capture_parser_help(capsys, "info")
    assert "usage: dotman info [-h] <info-command> ..." in output
    assert "info commands:" in output
    assert "Show tracked package details" in output
    assert "==SUPPRESS==" not in output

def test_list_help_hides_hidden_installed_subcommand(capsys) -> None:
    output = capture_parser_help(capsys, "list")
    assert "usage: dotman list [-h] <list-command> ..." in output
    assert "list commands:" in output
    assert "List tracked packages" in output
    assert "==SUPPRESS==" not in output

def test_reconcile_editor_help_uses_explicit_option_placeholders(capsys) -> None:
    output = capture_parser_help(capsys, "reconcile", "editor")
    assert "--repo-path <repo-path>" in output
    assert "--live-path <live-path>" in output
    assert "--review-repo-path <review-repo-path>" in output
    assert "--review-live-path <review-live-path>" in output
    assert "--additional-source <source-path>" in output
    assert "--editor <editor-command>" in output


def test_capture_help_lists_patch_shortcut(capsys) -> None:
    output = capture_parser_help(capsys, "capture")
    assert "usage: dotman capture [-h] <capture-command> ..." in output
    assert "capture commands:" in output
    assert "patch" in output
    assert "Patch review content back into repo source" in output


def test_capture_patch_help_uses_explicit_option_placeholders(capsys) -> None:
    output = capture_parser_help(capsys, "capture", "patch")
    assert "usage: dotman capture patch [-h] --repo-path <repo-path>" in output
    assert "--review-repo-path <review-repo-path>" in output
    assert "--review-live-path <review-live-path>" in output
    assert "--profile <profile>" in output
    assert "--os <os>" in output
    assert "--var <key=value>" in output


def test_reconcile_help_lists_jinja_shortcut(capsys) -> None:
    output = capture_parser_help(capsys, "reconcile")
    assert "usage: dotman reconcile [-h] <reconcile-command> ..." in output
    assert "reconcile commands:" in output
    assert "editor" in output
    assert "Open repo and live files in an editor" in output
    assert "jinja" in output
    assert "Reconcile a Jinja source with its recursive template" in output



def test_reconcile_jinja_help_uses_explicit_option_placeholders(capsys) -> None:
    output = capture_parser_help(capsys, "reconcile", "jinja")
    assert "usage: dotman reconcile jinja [-h] --repo-path <repo-path>" in output
    assert "--live-path <live-path>" in output
    assert "--review-repo-path <review-repo-path>" in output
    assert "--review-live-path <review-live-path>" in output
    assert "--editor <editor-command>" in output


def test_render_help_uses_nested_command_placeholder_and_summaries(capsys) -> None:
    output = capture_parser_help(capsys, "render")
    assert "usage: dotman render [-h] <render-command> ..." in output
    assert "render commands:" in output
    assert "Render a file with the built-in Jinja renderer" in output


def test_render_jinja_help_uses_explicit_placeholders(capsys) -> None:
    output = capture_parser_help(capsys, "render", "jinja")
    assert "usage: dotman render jinja [-h] [--profile <profile>] [--os <os>]" in output
    assert "[--var <key=value>]" in output
    assert "<source-path>" in output
    assert "--profile <profile>" in output
    assert "--os <os>" in output
    assert "--var <key=value>" in output

def test_selection_prompt_mentions_help(monkeypatch) -> None:
    monkeypatch.setattr(cli, "colors_enabled", lambda: False)

    assert cli.selection_prompt() == 'Select a number ("?"; default: 1): '

def test_select_menu_option_shows_help_then_accepts_selection(monkeypatch, capsys) -> None:
    prompts = iter(["?", "2"])

    monkeypatch.setattr(cli, "prompt", lambda _message: next(prompts))
    monkeypatch.setattr(cli, "colors_enabled", lambda: False)

    selected_index = cli.select_menu_option(
        header_text="Select a profile:",
        option_labels=["basic", "work"],
    )

    output = capsys.readouterr().out
    assert selected_index == 1
    assert "Selection help:" in output
    assert "  <number>  choose that item" in output
    assert "Enter" not in output

def test_prompt_for_excluded_items_shows_help_then_returns_selection(monkeypatch, capsys) -> None:
    prompts = iter(["?", "1 3-4"])
    items = [
        cli.PendingSelectionItem(
            binding_label="example:git@basic",
            package_id="git",
            target_name="gitconfig",
            action="update",
            source_path="/repo/gitconfig",
            destination_path="/live/gitconfig",
        )
        for _ in range(4)
    ]

    monkeypatch.setattr(cli, "prompt", lambda _message: next(prompts))
    monkeypatch.setattr(cli, "colors_enabled", lambda: False)

    excluded = cli.prompt_for_excluded_items(items, operation="push")

    output = capsys.readouterr().out
    assert excluded == {1, 3, 4}
    assert "Selection help:" in output
    assert "  ^<selection>   keep only the selected items" in output
    assert "Enter" not in output

def test_prompt_for_excluded_items_uses_full_paths_when_requested(monkeypatch, capsys) -> None:
    prompts = iter([""])
    items = [
        cli.PendingSelectionItem(
            binding_label="example:git@basic",
            package_id="git",
            target_name="gitconfig",
            action="update",
            source_path="/repo/very/long/path/gitconfig",
            destination_path="/live/very/long/path/gitconfig",
        )
    ]

    monkeypatch.setattr(cli, "prompt", lambda _message: next(prompts))
    monkeypatch.setattr(cli, "colors_enabled", lambda: False)

    excluded = cli.prompt_for_excluded_items(items, operation="push", full_paths=True)

    output = capsys.readouterr().out
    assert excluded == set()
    assert "/repo/very/long/path/gitconfig -> /live/very/long/path/gitconfig" in output
    assert "repo/.../path/gitconfig" not in output


def test_run_diff_review_menu_shows_help_then_continues(monkeypatch, capsys) -> None:
    review_item = cli.ReviewItem(
        binding_label="example:git@basic",
        package_id="git",
        target_name="gitconfig",
        action="update",
        operation="push",
        repo_path=Path("/repo/gitconfig"),
        live_path=Path("/live/gitconfig"),
        source_path="/repo/gitconfig",
        destination_path="/live/gitconfig",
        before_bytes=b"before\n",
        after_bytes=b"after\n",
    )
    prompts = iter(["?", "c"])

    monkeypatch.setattr(cli, "prompt", lambda _message: next(prompts))
    monkeypatch.setattr(cli, "colors_enabled", lambda: False)

    assert cli.run_diff_review_menu([review_item], operation="push") is True

    output = capsys.readouterr().out
    assert "Review commands:" in output
    assert "  n          inspect next diff" in output
    assert "  a          inspect all diffs" in output
    assert '  "?"        show this help' in output


def test_run_diff_review_menu_uses_full_paths_when_requested(monkeypatch, capsys) -> None:
    review_item = cli.ReviewItem(
        binding_label="example:git@basic",
        package_id="git",
        target_name="gitconfig",
        action="update",
        operation="push",
        repo_path=Path("/repo/very/long/path/gitconfig"),
        live_path=Path("/live/very/long/path/gitconfig"),
        source_path="/repo/very/long/path/gitconfig",
        destination_path="/live/very/long/path/gitconfig",
        before_bytes=b"before\n",
        after_bytes=b"after\n",
    )
    prompts = iter(["c"])

    monkeypatch.setattr(cli, "prompt", lambda _message: next(prompts))
    monkeypatch.setattr(cli, "colors_enabled", lambda: False)

    assert cli.run_diff_review_menu([review_item], operation="push", full_paths=True) is True

    output = capsys.readouterr().out
    assert "/repo/very/long/path/gitconfig -> /live/very/long/path/gitconfig" in output
    assert "repo/.../path/gitconfig" not in output

@pytest.mark.parametrize("command", ["apply", "upgrade", "import", "remove"])
def test_legacy_top_level_cli_commands_are_not_available(command: str) -> None:
    parser = cli.build_parser()

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args([command, "example:nvim@basic"])

    assert exc_info.value.code == 2
