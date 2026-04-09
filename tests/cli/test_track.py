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


def test_track_cli_emits_state_only_json(
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
            "--json",
            "track",
            "example:git@basic",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "state-only"
    assert payload["operation"] == "track"
    assert payload["binding"]["repo"] == "example"
    assert payload["binding"]["selector"] == "git"
    assert payload["binding"]["profile"] == "basic"
    assert (tmp_path / "state" / "example" / "bindings.toml").read_text(encoding="utf-8") == "\n".join(
        [
            "version = 1",
            "",
            "[[bindings]]",
            'repo = "example"',
            'selector = "git"',
            'profile = "basic"',
            "",
        ]
    )

def test_track_cli_interactively_selects_profile_when_missing(
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

    exit_code = main(
        [
            "--config",
            str(write_manager_config(tmp_path)),
            "track",
            "example:git",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Select a profile for example:git:" in output
    assert "tracked example:git@basic" in output
    assert (tmp_path / "state" / "example" / "bindings.toml").read_text(encoding="utf-8") == "\n".join(
        [
            "version = 1",
            "",
            "[[bindings]]",
            'repo = "example"',
            'selector = "git"',
            'profile = "basic"',
            "",
        ]
    )

def test_track_cli_interactively_switches_to_non_conflicting_profile(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    repo_root = tmp_path / "switch-repo"
    write_profile_switch_repo(repo_root)
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
                'selector = "beta"',
                'profile = "basic"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    answers = iter(["1", ""])
    monkeypatch.setattr(cli, "prompt", lambda _message: next(answers))

    exit_code = main(
        [
            "--config",
            str(config_path),
            "track",
            "fixture:alpha",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Select a profile for fixture:alpha:" in output
    assert "Select a non-conflicting profile for fixture:alpha:" in output
    assert "tracked fixture:alpha@work" in output
    assert (state_dir / "bindings.toml").read_text(encoding="utf-8") == "\n".join(
        [
            "version = 1",
            "",
            "[[bindings]]",
            'repo = "fixture"',
            'selector = "beta"',
            'profile = "basic"',
            "",
            "[[bindings]]",
            'repo = "fixture"',
            'selector = "alpha"',
            'profile = "work"',
            "",
        ]
    )

def test_track_cli_returns_130_on_keyboard_interrupt_during_profile_selection(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(cli, "prompt", lambda _message: (_ for _ in ()).throw(KeyboardInterrupt()))

    exit_code = main(
        [
            "--config",
            str(write_manager_config(tmp_path)),
            "track",
            "example:git",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 130
    assert "interrupted" in captured.err

def test_track_cli_interactively_selects_repo_for_exact_selector_collision(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    answers = iter(["2", ""])
    monkeypatch.setattr(cli, "prompt", lambda _message: next(answers))

    config_path = write_named_manager_config(
        tmp_path,
        {
            "alpha": REFERENCE_REPO,
            "beta": REFERENCE_REPO,
        },
    )

    exit_code = main(
        [
            "--config",
            str(config_path),
            "track",
            "sunshine@host/linux",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Select a repo for exact selector 'sunshine':" in output
    assert "alpha:sunshine [package]" in output
    assert "beta:sunshine [package]" in output
    assert "tracked beta:sunshine@host/linux" in output
    assert (tmp_path / "state" / "beta" / "bindings.toml").read_text(encoding="utf-8") == "\n".join(
        [
            "version = 1",
            "",
            "[[bindings]]",
            'repo = "beta"',
            'selector = "sunshine"',
            'profile = "host/linux"',
            "",
        ]
    )

def test_track_cli_interactively_selects_partial_selector_match(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    answers = iter(["2", ""])
    monkeypatch.setattr(cli, "prompt", lambda _message: next(answers))

    exit_code = main(
        [
            "--config",
            str(write_manager_config(tmp_path)),
            "track",
            "sandbox:1pass@host/linux",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Select a selector match for '1pass':" in output
    assert "tracked sandbox:linux/1password@host/linux" in output
    assert (tmp_path / "state" / "sandbox" / "bindings.toml").read_text(encoding="utf-8") == "\n".join(
        [
            "version = 1",
            "",
            "[[bindings]]",
            'repo = "sandbox"',
            'selector = "linux/1password"',
            'profile = "host/linux"',
            "",
        ]
    )

def test_track_cli_accepts_slash_qualified_repo_selector_lookup(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    config_path = write_named_manager_config(
        tmp_path,
        {
            "alpha": REFERENCE_REPO,
            "beta": REFERENCE_REPO,
        },
    )

    exit_code = main(
        [
            "--config",
            str(config_path),
            "--json",
            "track",
            "beta/sunshine@host/linux",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["binding"]["repo"] == "beta"
    assert payload["binding"]["selector"] == "sunshine"
    assert payload["binding"]["profile"] == "host/linux"

def test_track_cli_resolves_unique_partial_profile_in_non_interactive_mode(
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
            "--json",
            "track",
            "example:git@wor",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["binding"]["repo"] == "example"
    assert payload["binding"]["selector"] == "git"
    assert payload["binding"]["profile"] == "work"

def test_track_cli_interactively_selects_ambiguous_partial_profile_match(
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

    exit_code = main(
        [
            "--config",
            str(write_manager_config(tmp_path)),
            "track",
            "sandbox:1password@os/",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Select a profile match for 'os/' in sandbox:1password:" in output
    assert "tracked sandbox:1password@os/mac" in output

def test_track_cli_interactively_falls_back_to_full_profile_menu_for_unknown_profile(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    answers = iter(["1", "2", ""])
    monkeypatch.setattr(cli, "prompt", lambda _message: next(answers))

    exit_code = main(
        [
            "--config",
            str(write_manager_config(tmp_path)),
            "track",
            "ss@wor",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Select a selector match for 'ss':" in output
    assert "Select a profile for sandbox:1password:" in output
    assert "tracked sandbox:1password@host/linux" in output

def test_track_cli_confirms_before_updating_existing_binding_profile(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    answers = iter(["2", "y"])
    monkeypatch.setattr(cli, "prompt", lambda _message: next(answers))

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
            str(write_manager_config(tmp_path)),
            "track",
            "example:git",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Select a profile for example:git:" in output
    assert "Confirm tracked binding replacement for example:git:" in output
    assert "tracked example:git@work" in output
    assert (tmp_path / "state" / "example" / "bindings.toml").read_text(encoding="utf-8") == "\n".join(
        [
            "version = 1",
            "",
            "[[bindings]]",
            'repo = "example"',
            'selector = "git"',
            'profile = "work"',
            "",
        ]
    )

def test_track_cli_keeps_existing_binding_when_profile_replacement_is_declined(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(cli, "prompt", lambda _message: "n")

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
            str(write_manager_config(tmp_path)),
            "track",
            "example:git@work",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Confirm tracked binding replacement for example:git:" in output
    assert "kept existing tracked binding example:git@basic" in output
    assert (tmp_path / "state" / "example" / "bindings.toml").read_text(encoding="utf-8") == "\n".join(
        [
            "version = 1",
            "",
            "[[bindings]]",
            'repo = "example"',
            'selector = "git"',
            'profile = "basic"',
            "",
        ]
    )

def test_track_cli_refuses_silent_profile_replacement_in_non_interactive_mode(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
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
            str(write_manager_config(tmp_path)),
            "track",
            "example:git@work",
        ]
    )

    assert exit_code == 2
    assert (
        "refusing to replace tracked binding 'example:git@basic' with 'example:git@work' in non-interactive mode"
        in capsys.readouterr().err
    )
    assert (tmp_path / "state" / "example" / "bindings.toml").read_text(encoding="utf-8") == "\n".join(
        [
            "version = 1",
            "",
            "[[bindings]]",
            'repo = "example"',
            'selector = "git"',
            'profile = "basic"',
            "",
        ]
    )

def test_track_cli_confirms_before_overriding_implicit_targets(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(cli, "prompt", lambda _message: "y")

    state_dir = tmp_path / "state" / "example"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "bindings.toml").write_text(
        "\n".join(
            [
                "version = 1",
                "",
                "[[bindings]]",
                'repo = "example"',
                'selector = "os/arch"',
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
            "track",
            "example:work/git@work",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Confirm explicit override for example:work/git@work:" in output
    assert "implicit: example:os/arch@basic (git)" in output
    assert "tracked example:work/git@work" in output

def test_track_cli_refuses_implicit_override_in_non_interactive_mode(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
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
                'selector = "os/arch"',
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
            "track",
            "example:work/git@work",
        ]
    )

    assert exit_code == 2
    assert (
        "refusing to let 'example:work/git@work' explicitly override implicitly tracked targets in non-interactive mode"
        in capsys.readouterr().err
    )

def test_track_cli_can_promote_conflicting_package_from_implicit_conflict(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    repo_root = tmp_path / "implicit-conflict-repo"
    write_implicit_conflict_repo(repo_root)
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
                'selector = "beta-stack"',
                'profile = "basic"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    answers = iter(["y", "y"])
    monkeypatch.setattr(cli, "prompt", lambda _message: next(answers))

    exit_code = main(
        [
            "--config",
            str(config_path),
            "track",
            "fixture:alpha-stack@basic",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Resolve implicit conflict for fixture:alpha-stack@basic:" in output
    assert "promote:   fixture:alpha@basic" in output
    assert "Confirm explicit override for fixture:alpha@basic:" in output
    assert "implicit: fixture:beta-stack@basic (beta)" in output
    assert "tracked fixture:alpha@basic" in output
    assert (state_dir / "bindings.toml").read_text(encoding="utf-8") == "\n".join(
        [
            "version = 1",
            "",
            "[[bindings]]",
            'repo = "fixture"',
            'selector = "beta-stack"',
            'profile = "basic"',
            "",
            "[[bindings]]",
            'repo = "fixture"',
            'selector = "alpha"',
            'profile = "basic"',
            "",
        ]
    )

def test_track_cli_lists_package_override_once_for_multi_target_package(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(cli, "prompt", lambda _message: "y")

    repo_root = tmp_path / "override-preview-repo"
    write_package_override_preview_repo(repo_root)
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
                'selector = "beta-stack"',
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
            "track",
            "fixture:alpha@basic",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert output.count("implicit: fixture:beta-stack@basic (beta)") == 1
    assert "~/.config/shared.conf" not in output
    assert "~/.config/extra.conf" not in output
