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


def test_info_tracked_cli_interactively_selects_ambiguous_package(
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
            "info",
            "tracked",
            "sunshine",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Select a tracked package for 'sunshine':" in output
    assert "alpha:sunshine" in output
    assert "beta:sunshine" in output
    assert "beta:sunshine" in output

def test_info_tracked_cli_emits_package_details_for_tracked_package(
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
            "info",
            "tracked",
            "git",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "info-tracked"
    package = payload["package"]
    assert package["repo"] == "example"
    assert package["package_id"] == "git"
    assert package["description"] == "Base Git configuration"
    assert [binding["selector"] for binding in package["bindings"]] == ["core-cli-meta", "git"]
    assert [binding["tracked_reason"] for binding in package["bindings"]] == ["implicit", "explicit"]
    assert package["owned_targets"] == [
        {
            "capture_command": None,
            "live_path": str(home / ".gitconfig"),
            "profile": "basic",
            "pull_ignore": [],
            "pull_view_live": "raw",
            "pull_view_repo": "raw",
            "push_ignore": [],
            "reconcile_command": None,
            "render_command": None,
            "repo": "example",
            "repo_path": str(EXAMPLE_REPO / "packages" / "git" / "files" / "gitconfig"),
            "selector": "git",
            "selector_kind": "package",
            "target_kind": "file",
            "target_name": "gitconfig",
        }
    ]
    target_names = {target["target_name"] for target in package["bindings"][0]["targets"]}
    assert target_names == {"gitconfig"}
    assert package["bindings"][0]["hooks"] == {}
    pre_push = package["bindings"][1]["hooks"]["pre_push"]
    assert pre_push[0]["package_id"] == "git"
    assert "git" in pre_push[0]["command"]

def test_info_tracked_cli_emits_readable_text_output(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(cli, "colors_enabled", lambda: False)

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
            "info",
            "tracked",
            "git",
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out == "\n".join(
        [
            "example:git",
            "  Base Git configuration",
            "",
            "  :: provenance",
            "    implicit: example:core-cli-meta@basic",
            "    explicit: example:git@basic",
            "",
            "  :: hooks",
            "    [check]",
            "      command -v git >/dev/null 2>&1",
            "    [pre_push]",
            "      [1] brew install git",
            '      [2] "$DOTMAN_REPO_ROOT/scripts/log-package-event.sh" "install-packages" "$DOTMAN_PACKAGE_ID"',
            "    [post_push]",
            "      sh hooks/post-push.sh",
            "",
            "  :: owned targets",
            f"    gitconfig -> {home / '.gitconfig'}",
            "",
        ]
    )

def test_info_tracked_cli_emits_hooks_even_when_package_targets_are_noop(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(cli, "colors_enabled", lambda: False)

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

    engine = cli.DotmanEngine.from_config_path(config_path)
    plan = engine.plan_push_binding("example:git@basic")
    (home / ".gitconfig").write_text(plan.target_plans[0].desired_text or "", encoding="utf-8")

    exit_code = main(
        [
            "--config",
            str(config_path),
            "info",
            "tracked",
            "git",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "  :: hooks" in output
    assert "    [pre_push]" in output
    assert "      [1] brew install git" in output
    assert "  :: owned targets" in output

def test_info_tracked_cli_requires_specific_multi_instance_package_identity_in_non_interactive_mode(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    write_multi_instance_repo(repo_root)
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    state_dir = tmp_path / "state" / "fixture"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "bindings.toml").write_text(
        "\n".join(
            [
                "version = 1",
                "",
                "[[bindings]]",
                'repo = "fixture"',
                'selector = "profiled"',
                'profile = "basic"',
                "",
                "[[bindings]]",
                'repo = "fixture"',
                'selector = "profiled"',
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
            "--json",
            "info",
            "tracked",
            "profiled",
        ]
    )

    assert exit_code == 2
    assert "ambiguous" in capsys.readouterr().err

    exit_code = main(
        [
            "--config",
            str(config_path),
            "--json",
            "info",
            "tracked",
            "profiled<work>",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["package"]["package_ref"] == "profiled<work>"
    assert payload["package"]["bound_profile"] == "work"
    assert [binding["profile"] for binding in payload["package"]["bindings"]] == ["work"]

def test_info_tracked_cli_uses_resolver_for_ambiguous_multi_instance_identity_in_interactive_mode(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(cli, "colors_enabled", lambda: False)
    monkeypatch.setattr(cli, "interactive_mode_enabled", lambda *, json_output: True)
    monkeypatch.setattr(cli, "select_menu_option", lambda **_kwargs: 1)

    repo_root = tmp_path / "repo"
    write_multi_instance_repo(repo_root)
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    state_dir = tmp_path / "state" / "fixture"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "bindings.toml").write_text(
        "\n".join(
            [
                "version = 1",
                "",
                "[[bindings]]",
                'repo = "fixture"',
                'selector = "profiled"',
                'profile = "basic"',
                "",
                "[[bindings]]",
                'repo = "fixture"',
                'selector = "profiled"',
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
            "info",
            "tracked",
            "profiled",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert output.startswith("fixture:profiled<work>\n")
    assert f"managed -> {home / '.config' / 'profiled' / 'work.conf'}" in output
