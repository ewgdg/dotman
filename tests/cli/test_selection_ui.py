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


def test_prompt_for_excluded_items_uses_archived_colored_style(
    monkeypatch,
    capsys,
) -> None:
    selection_items = [
        PendingSelectionItem(
            binding_label="example:git@basic",
            package_id="git",
            target_name="gitconfig",
            action="create",
            source_path="/repo/gitconfig",
            destination_path="/home/.gitconfig",
        )
    ]

    monkeypatch.setattr(cli, "colors_enabled", lambda: True)
    monkeypatch.setattr(cli, "prompt", lambda _message: "")

    excluded = prompt_for_excluded_items(selection_items, operation="push")
    output = capsys.readouterr().out

    assert excluded == set()
    assert "\033[1;34m::\033[0m" in output
    assert "\033[1;36m 1)\033[0m" in output
    assert "\033[1;32m[create]\033[0m" in output
    assert "\033[2;34mexample\033[0m" in output
    assert "\033[2m:\033[0m" in output
    assert "\033[1mgit\033[0m" in output
    assert "\033[2m(gitconfig)\033[0m" in output
    assert "\033[2m->\033[0m" in output
    assert "(example:git@basic)" not in output
    assert "example:git@basic \033[1;32m[create]\033[0m" not in output
    assert "Select items to exclude from push:" in output

def test_render_tracked_binding_label_uses_selection_menu_style(monkeypatch) -> None:
    monkeypatch.setattr(cli, "colors_enabled", lambda: True)

    assert cli.render_binding_label(repo_name="example", selector="git", profile="basic") == (
        "\033[2;34mexample\033[0m"
        "\033[2m:\033[0m"
        "\033[1mgit\033[0m"
        "\033[2m@basic\033[0m"
    )

def test_render_package_label_can_prioritize_package_name(monkeypatch) -> None:
    monkeypatch.setattr(cli, "colors_enabled", lambda: False)

    assert cli.render_package_label(
        repo_name="example",
        package_id="git",
        package_first=True,
        include_repo_context=True,
    ) == "example:git"

def test_render_binding_label_can_prioritize_selector_name(monkeypatch) -> None:
    monkeypatch.setattr(cli, "colors_enabled", lambda: False)

    assert cli.render_binding_label(
        repo_name="example",
        selector="git",
        profile="basic",
        selector_first=True,
    ) == "example:git@basic"

def test_filter_plans_for_interactive_selection_excludes_directory_child_pull_item(
    monkeypatch,
) -> None:
    target_plan = TargetPlan(
        package_id="sandbox/bin",
        target_name="bin",
        repo_path=Path("/repo/bin"),
        live_path=Path("/home/bin"),
        action="update",
        target_kind="directory",
        projection_kind="directory",
        directory_items=(
            DirectoryPlanItem(
                relative_path="alpha.sh",
                action="update",
                repo_path=Path("/repo/bin/alpha.sh"),
                live_path=Path("/home/bin/alpha.sh"),
            ),
            DirectoryPlanItem(
                relative_path="beta.sh",
                action="update",
                repo_path=Path("/repo/bin/beta.sh"),
                live_path=Path("/home/bin/beta.sh"),
            ),
        ),
    )
    plan = BindingPlan(
        operation="pull",
        binding=Binding(repo="sandbox", selector="sandbox/bin", profile="default"),
        selector_kind="package",
        package_ids=["sandbox/bin"],
        variables={},
        hooks={},
        target_plans=[target_plan],
    )

    monkeypatch.setattr(cli, "interactive_mode_enabled", lambda *, json_output: True)
    monkeypatch.setattr(
        cli,
        "prompt_for_excluded_items",
        lambda selection_items, *, operation, full_paths=False: {1},
    )

    filtered_plans = cli.filter_plans_for_interactive_selection(
        plans=[plan],
        operation="pull",
        json_output=False,
    )

    filtered_target = filtered_plans[0].target_plans[0]
    assert [item.relative_path for item in filtered_target.directory_items] == ["beta.sh"]

def test_collect_pending_selection_items_for_pull_uses_live_to_repo_paths() -> None:
    target_plan = TargetPlan(
        package_id="sandbox/bin",
        target_name="bin",
        repo_path=Path("/repo/bin"),
        live_path=Path("/home/bin"),
        action="update",
        target_kind="directory",
        projection_kind="directory",
        directory_items=(
            DirectoryPlanItem(
                relative_path="alpha.sh",
                action="update",
                repo_path=Path("/repo/bin/alpha.sh"),
                live_path=Path("/home/bin/alpha.sh"),
            ),
        ),
    )
    plan = BindingPlan(
        operation="pull",
        binding=Binding(repo="sandbox", selector="sandbox/bin", profile="default"),
        selector_kind="package",
        package_ids=["sandbox/bin"],
        variables={},
        hooks={},
        target_plans=[target_plan],
    )

    selection_items = cli.collect_pending_selection_items_for_operation([plan], operation="pull")

    assert [(item.action, item.source_path, item.destination_path) for item in selection_items] == [
        ("update", "/home/bin/alpha.sh", "/repo/bin/alpha.sh"),
    ]

def test_filter_plans_for_interactive_selection_recomputes_hooks_from_remaining_targets(monkeypatch) -> None:
    alpha_target = TargetPlan(
        package_id="alpha",
        target_name="config",
        repo_path=Path("/repo/alpha.conf"),
        live_path=Path("/home/alpha.conf"),
        action="update",
        target_kind="file",
        projection_kind="file",
    )
    beta_target = TargetPlan(
        package_id="beta",
        target_name="config",
        repo_path=Path("/repo/beta.conf"),
        live_path=Path("/home/beta.conf"),
        action="update",
        target_kind="file",
        projection_kind="file",
    )
    plan = BindingPlan(
        operation="push",
        binding=Binding(repo="sandbox", selector="stack", profile="default"),
        selector_kind="group",
        package_ids=["alpha", "beta"],
        variables={},
        hooks={
            "pre_push": [
                HookPlan(package_id="alpha", hook_name="pre_push", command="echo alpha", cwd=Path("/repo/alpha")),
                HookPlan(package_id="beta", hook_name="pre_push", command="echo beta", cwd=Path("/repo/beta")),
            ]
        },
        target_plans=[alpha_target, beta_target],
    )

    monkeypatch.setattr(cli, "interactive_mode_enabled", lambda *, json_output: True)
    monkeypatch.setattr(
        cli,
        "prompt_for_excluded_items",
        lambda selection_items, *, operation, full_paths=False: {1},
    )

    filtered_plan = cli.filter_plans_for_interactive_selection(
        plans=[plan],
        operation="push",
        json_output=False,
    )[0]

    assert [target.package_id for target in filtered_plan.target_plans] == ["beta"]
    assert [hook.package_id for hook in filtered_plan.hooks["pre_push"]] == ["beta"]

def test_collect_pending_selection_items_for_push_uses_repo_to_live_paths() -> None:
    target_plan = TargetPlan(
        package_id="sandbox/bin",
        target_name="bin",
        repo_path=Path("/repo/bin"),
        live_path=Path("/home/bin"),
        action="update",
        target_kind="directory",
        projection_kind="directory",
        directory_items=(
            DirectoryPlanItem(
                relative_path="alpha.sh",
                action="update",
                repo_path=Path("/repo/bin/alpha.sh"),
                live_path=Path("/home/bin/alpha.sh"),
            ),
        ),
    )
    plan = BindingPlan(
        operation="push",
        binding=Binding(repo="sandbox", selector="sandbox/bin", profile="default"),
        selector_kind="package",
        package_ids=["sandbox/bin"],
        variables={},
        hooks={},
        target_plans=[target_plan],
    )

    selection_items = cli.collect_pending_selection_items_for_operation([plan], operation="push")

    assert [(item.action, item.source_path, item.destination_path) for item in selection_items] == [
        ("update", "/repo/bin/alpha.sh", "/home/bin/alpha.sh"),
    ]

def test_select_menu_option_prefers_fzf_for_long_lists(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_should_use_fzf_for_selection", lambda _option_labels: True)
    monkeypatch.setattr(cli, "_fzf_available", lambda: True)
    monkeypatch.setattr(cli, "_select_menu_option_with_prompt", lambda **_kwargs: pytest.fail("prompt fallback should not run"))
    monkeypatch.setattr(cli, "_select_menu_option_with_fzf", lambda **_kwargs: 1)

    assert cli.select_menu_option(header_text="Select a package:", option_labels=["alpha", "beta"]) == 1

def test_select_menu_option_passes_search_terms_to_fzf(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(cli, "_should_use_fzf_for_selection", lambda _option_labels: True)
    monkeypatch.setattr(cli, "_fzf_available", lambda: True)
    monkeypatch.setattr(cli, "_select_menu_option_with_prompt", lambda **_kwargs: pytest.fail("prompt fallback should not run"))

    def fake_fzf(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(cli, "_select_menu_option_with_fzf", fake_fzf)

    assert (
        cli.select_menu_option(
            header_text="Select a package:",
            option_labels=["alpha/sunshine", "beta/sunshine"],
            option_search_fields=[
                ("sunshine", "alpha/sunshine"),
                ("sunshine", "beta/sunshine"),
            ],
        )
        == 0
    )
    assert captured["option_search_fields"] == [
        ("sunshine", "alpha/sunshine"),
        ("sunshine", "beta/sunshine"),
    ]

def test_resolve_candidate_match_ranks_leftmost_selector_segments_first(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_select_menu_option(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(cli, "select_menu_option", fake_select_menu_option)

    selected = cli.resolve_candidate_match(
        exact_matches=[],
        partial_matches=[
            ("sandbox", "host/linux-meta"),
            ("sandbox", "sunshine"),
        ],
        query_text="s",
        interactive=True,
        exact_header_text="unused",
        partial_header_text="Select a selector match for 's':",
        option_resolver=lambda match: cli.ResolverOption(
            display_label=f"{match[0]}:{match[1]}",
            match_fields=cli.build_selector_match_fields(repo_name=match[0], selector=match[1]),
        ),
        exact_error_text="unused",
        partial_error_text="unused",
        not_found_text="unused",
    )

    assert selected == ("sandbox", "sunshine")
    assert captured["option_labels"] == ["sandbox:sunshine", "sandbox:host/linux-meta"]

def test_select_menu_option_with_prompt_renders_bottom_up_by_default(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "colors_enabled", lambda: False)
    monkeypatch.setattr(cli, "selection_menu_bottom_up_enabled", lambda: True)
    monkeypatch.setattr(cli, "prompt", lambda _message: "")

    assert cli._select_menu_option_with_prompt(
        header_text="Select a package:",
        option_labels=["alpha", "beta", "gamma"],
    ) == 0

    output = capsys.readouterr().out
    assert output.index("  3) gamma") < output.index("  2) beta") < output.index("  1) alpha")

def test_select_menu_option_with_fzf_uses_structured_display_fields(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, *, input, text, capture_output, check):
        captured["command"] = command
        captured["input"] = input
        captured["text"] = text
        captured["capture_output"] = capture_output
        captured["check"] = check
        return subprocess.CompletedProcess(command, 0, stdout="2\n", stderr="")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setattr(cli, "selection_menu_bottom_up_enabled", lambda: True)

    selected_index = cli._select_menu_option_with_fzf(
        header_text="Select a package:",
        option_labels=["sandbox/sunshine [package]", "sandbox/host/linux-meta [group]"],
        option_search_fields=[
            ("sunshine", "sandbox/sunshine"),
            ("host/linux-meta", "sandbox/host/linux-meta"),
        ],
        option_display_fields=[
            ("sandbox/sunshine", "[package]"),
            ("sandbox/host/linux-meta", "[group]"),
        ],
    )

    assert selected_index == 1
    assert f"--delimiter={cli.FZF_FIELD_DELIMITER}" in captured["command"]
    assert "--nth=1" in captured["command"]
    assert "--with-nth=2..3" in captured["command"]
    assert "--accept-nth=1" in captured["command"]
    assert "--layout=reverse-list" in captured["command"]
    assert captured["input"] == (
        f"1{cli.FZF_FIELD_DELIMITER}sandbox/sunshine{cli.FZF_FIELD_DELIMITER}[package]\n"
        f"2{cli.FZF_FIELD_DELIMITER}sandbox/host/linux-meta{cli.FZF_FIELD_DELIMITER}[group]\n"
    )

def test_run_diff_review_menu_prints_separator_before_each_diff_for_all(
    monkeypatch,
    capsys,
) -> None:
    review_items = [
        cli.ReviewItem(
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
        ),
        cli.ReviewItem(
            binding_label="example:zsh@basic",
            package_id="zsh",
            target_name="zshrc",
            action="update",
            operation="push",
            repo_path=Path("/repo/.zshrc"),
            live_path=Path("/live/.zshrc"),
            source_path="/repo/.zshrc",
            destination_path="/live/.zshrc",
            before_bytes=b"before\n",
            after_bytes=b"after\n",
        ),
    ]
    prompts = iter(["a", "c"])
    inspected: list[str] = []

    monkeypatch.setattr(cli, "prompt", lambda _message: next(prompts))
    monkeypatch.setattr(cli, "run_review_item_diff", lambda item: inspected.append(item.target_name))
    monkeypatch.setattr(cli, "colors_enabled", lambda: False)

    assert cli.run_diff_review_menu(review_items, operation="push") is True

    output = capsys.readouterr().out
    assert inspected == ["gitconfig", "zshrc"]
    assert "----- Diff 1/2: example:git (gitconfig) [update] -----" in output
    assert "----- End Diff 1/2 -----" in output
    assert "----- Diff 2/2: example:zsh (zshrc) [update] -----" in output
    assert "----- End Diff 2/2 -----" in output

def test_run_diff_review_menu_prints_footer_after_single_inspect(
    monkeypatch,
    capsys,
) -> None:
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
    prompts = iter(["1", "c"])

    monkeypatch.setattr(cli, "prompt", lambda _message: next(prompts))
    monkeypatch.setattr(cli, "run_review_item_diff", lambda item: None)
    monkeypatch.setattr(cli, "colors_enabled", lambda: False)

    assert cli.run_diff_review_menu([review_item], operation="push") is True

    output = capsys.readouterr().out
    assert "----- Diff 1/1: example:git (gitconfig) [update] -----" in output
    assert "----- End Diff 1/1 -----" in output


def test_print_review_diff_header_dims_metadata_prefix_when_colored(
    monkeypatch,
    capsys,
) -> None:
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

    monkeypatch.setattr(cli, "colors_enabled", lambda: True)

    cli.print_review_diff_header(review_item, index=1, total=1)

    output = capsys.readouterr().out
    assert "\033[2m-----\033[0m \033[2mDiff 1/1:\033[0m " in output
    assert "\033[2;34mexample\033[0m\033[2m:\033[0m\033[1mgit\033[0m \033[2m(gitconfig)\033[0m" in output
    assert "\033[1;36m[update]\033[0m" in output
    assert output.endswith(" \033[2m-----\033[0m\n")


def test_run_diff_review_menu_default_command_views_next_diff(
    monkeypatch,
    capsys,
) -> None:
    review_items = [
        cli.ReviewItem(
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
        ),
        cli.ReviewItem(
            binding_label="example:zsh@basic",
            package_id="zsh",
            target_name="zshrc",
            action="update",
            operation="push",
            repo_path=Path("/repo/.zshrc"),
            live_path=Path("/live/.zshrc"),
            source_path="/repo/.zshrc",
            destination_path="/live/.zshrc",
            before_bytes=b"before\n",
            after_bytes=b"after\n",
        ),
    ]
    prompts = iter(["", "", "c"])
    inspected: list[str] = []

    monkeypatch.setattr(cli, "prompt", lambda _message: next(prompts))
    monkeypatch.setattr(cli, "run_review_item_diff", lambda item: inspected.append(item.target_name))
    monkeypatch.setattr(cli, "colors_enabled", lambda: False)

    assert cli.run_diff_review_menu(review_items, operation="push") is True

    assert inspected == ["gitconfig", "zshrc"]
    output = capsys.readouterr().out
    assert "----- Diff 1/2: example:git (gitconfig) [update] -----" in output
    assert "----- Diff 2/2: example:zsh (zshrc) [update] -----" in output


def test_run_diff_review_menu_next_command_uses_last_viewed_file(
    monkeypatch,
    capsys,
) -> None:
    review_items = [
        cli.ReviewItem(
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
        ),
        cli.ReviewItem(
            binding_label="example:zsh@basic",
            package_id="zsh",
            target_name="zshrc",
            action="update",
            operation="push",
            repo_path=Path("/repo/.zshrc"),
            live_path=Path("/live/.zshrc"),
            source_path="/repo/.zshrc",
            destination_path="/live/.zshrc",
            before_bytes=b"before\n",
            after_bytes=b"after\n",
        ),
        cli.ReviewItem(
            binding_label="example:nvim@basic",
            package_id="nvim",
            target_name="init.lua",
            action="update",
            operation="push",
            repo_path=Path("/repo/init.lua"),
            live_path=Path("/live/init.lua"),
            source_path="/repo/init.lua",
            destination_path="/live/init.lua",
            before_bytes=b"before\n",
            after_bytes=b"after\n",
        ),
    ]
    prompts = iter(["2", "n", "c"])
    inspected: list[str] = []

    monkeypatch.setattr(cli, "prompt", lambda _message: next(prompts))
    monkeypatch.setattr(cli, "run_review_item_diff", lambda item: inspected.append(item.target_name))
    monkeypatch.setattr(cli, "colors_enabled", lambda: False)

    assert cli.run_diff_review_menu(review_items, operation="push") is True

    assert inspected == ["zshrc", "init.lua"]
    output = capsys.readouterr().out
    assert "----- Diff 2/3: example:zsh (zshrc) [update] -----" in output
    assert "----- Diff 3/3: example:nvim (init.lua) [update] -----" in output


def test_run_diff_review_menu_next_command_at_end_prompts_for_continue(monkeypatch) -> None:
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
    prompt_messages: list[str] = []
    prompts = iter(["", "", ""])
    inspected: list[str] = []

    def fake_prompt(message: str) -> str:
        prompt_messages.append(message)
        return next(prompts)

    monkeypatch.setattr(cli, "prompt", fake_prompt)
    monkeypatch.setattr(cli, "run_review_item_diff", lambda item: inspected.append(item.target_name))
    monkeypatch.setattr(cli, "colors_enabled", lambda: False)

    assert cli.run_diff_review_menu([review_item], operation="push") is True

    assert inspected == ["gitconfig"]
    assert prompt_messages == [
        '\nReview command ("?", number, "n", "a", "c", "q"; default: next): ',
        '\nReview command ("?", number, "n", "a", "c", "q"; default: next): ',
        'Continue? [Y/n] ',
    ]


def test_print_selection_header_prepends_blank_line(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "colors_enabled", lambda: False)

    cli.print_selection_header("Review pending diffs for pull:")

    assert capsys.readouterr().out == "\nReview pending diffs for pull:\n"

def test_review_menu_prompt_prepends_blank_line(monkeypatch) -> None:
    monkeypatch.setattr(cli, "colors_enabled", lambda: False)

    assert cli.review_menu_prompt() == '\nReview command ("?", number, "n", "a", "c", "q"; default: next): '

def test_pending_selection_prompt_prepends_blank_line(monkeypatch) -> None:
    monkeypatch.setattr(cli, "colors_enabled", lambda: False)

    assert cli.pending_selection_prompt() == '\nExclude by number or range ("?"; e.g. "1 2 4-6" or "^3"; default: none): '


def test_write_manifest_confirmation_prompt_uses_bracket_style(monkeypatch) -> None:
    monkeypatch.setattr(cli, "colors_enabled", lambda: False)

    assert (
        cli.write_manifest_confirmation_prompt(repo_name="fixture", package_id="git")
        == 'Write package config changes for fixture:git? [y/N] '
    )


def test_push_symlink_replacement_prompt_uses_bracket_style(monkeypatch) -> None:
    monkeypatch.setattr(cli, "colors_enabled", lambda: False)

    assert cli.push_symlink_replacement_prompt() == 'Replace symlinked live target(s) before push? [y/N] '


def test_select_menu_option_renders_bottom_up_by_default(monkeypatch, capsys) -> None:
    monkeypatch.delenv("DOTMAN_MENU_BOTTOM_UP", raising=False)
    monkeypatch.setattr(cli, "prompt", lambda _message: "")
    monkeypatch.setattr(cli, "colors_enabled", lambda: False)

    selected_index = cli.select_menu_option(
        header_text="Select a profile:",
        option_labels=["basic", "work", "host/linux"],
    )

    output = capsys.readouterr().out
    assert selected_index == 0
    assert output.index("  3) host/linux") < output.index("  2) work") < output.index("  1) basic")

def test_select_menu_option_can_disable_bottom_up_with_env(monkeypatch, capsys) -> None:
    monkeypatch.setenv("DOTMAN_MENU_BOTTOM_UP", "0")
    monkeypatch.setattr(cli, "prompt", lambda _message: "")
    monkeypatch.setattr(cli, "colors_enabled", lambda: False)

    selected_index = cli.select_menu_option(
        header_text="Select a profile:",
        option_labels=["basic", "work", "host/linux"],
    )

    output = capsys.readouterr().out
    assert selected_index == 0
    assert output.index("  1) basic") < output.index("  2) work") < output.index("  3) host/linux")

def test_print_review_item_compacts_long_paths(monkeypatch, capsys) -> None:
    review_item = cli.ReviewItem(
        binding_label="example:git@basic",
        package_id="git",
        target_name="gitconfig",
        action="update",
        operation="push",
        repo_path=Path.home() / ".config" / "git" / "config",
        live_path=Path.home() / ".local" / "share" / "git" / "config",
        source_path=str(Path.home() / ".config" / "git" / "config"),
        destination_path=str(Path.home() / ".local" / "share" / "git" / "config"),
        before_bytes=b"before\n",
        after_bytes=b"after\n",
    )

    monkeypatch.setattr(cli, "colors_enabled", lambda: False)

    cli.print_review_item(1, review_item)

    output = capsys.readouterr().out
    assert "  1) [update] example:git (gitconfig) [diff]:" in output
    assert "~/.../git/config -> ~/.../git/config" in output
    assert str(Path.home()) not in output
