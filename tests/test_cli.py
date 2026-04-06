from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import dotman.cli as cli
import pytest
from dotman.cli import PendingSelectionItem, main, prompt_for_excluded_items
from dotman.models import Binding, BindingPlan, DirectoryPlanItem, TargetPlan


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_REPO = PROJECT_ROOT / "examples" / "repo"
REFERENCE_REPO = PROJECT_ROOT / "tests" / "fixtures" / "reference_repo"


def write_manager_config(tmp_path: Path) -> Path:
    return write_named_manager_config(
        tmp_path,
        {
            "example": EXAMPLE_REPO,
            "sandbox": REFERENCE_REPO,
        },
    )


def write_named_manager_config(tmp_path: Path, repos: dict[str, Path]) -> Path:
    config_path = tmp_path / "config.toml"
    lines: list[str] = []
    for index, (repo_name, repo_path) in enumerate(repos.items(), start=1):
        lines.extend(
            [
                f"[repos.{repo_name}]",
                f'path = "{repo_path}"',
                f"order = {index * 10}",
                f'state_path = "{tmp_path / "state" / repo_name}"',
                "",
            ]
        )
    config_path.write_text("\n".join(lines), encoding="utf-8")
    return config_path


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
    assert payload["bindings"][0]["targets"][0]["action"] == "install"


def test_prompt_for_excluded_items_uses_archived_colored_style(
    monkeypatch,
    capsys,
) -> None:
    selection_items = [
        PendingSelectionItem(
            binding_label="example:git@basic",
            package_id="git",
            target_name="gitconfig",
            action="install",
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
    assert "\033[1;32m[install]\033[0m" in output
    assert "\033[2;34mexample\033[0m" in output
    assert "\033[2m/\033[0m" in output
    assert "\033[1mgit\033[0m" in output
    assert "\033[2m(gitconfig)\033[0m" in output
    assert "\033[2m->\033[0m" in output
    assert "(example:git@basic)" not in output
    assert "example:git@basic \033[1;32m[install]\033[0m" not in output
    assert "Select items to exclude from push:" in output


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
                action="pull",
                repo_path=Path("/repo/bin/alpha.sh"),
                live_path=Path("/home/bin/alpha.sh"),
            ),
            DirectoryPlanItem(
                relative_path="beta.sh",
                action="pull",
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
    monkeypatch.setattr(cli, "prompt_for_excluded_items", lambda selection_items, *, operation: {1})

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
                action="pull",
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
        ("pull", "/home/bin/alpha.sh", "/repo/bin/alpha.sh"),
    ]


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
    assert "git:gitconfig -> install" not in output
    assert "example:nvim@basic\n" not in output
    assert "nvim:init_lua -> install" in output


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
    assert "git:gitconfig -> install" not in output
    assert "nvim:init_lua -> noop" not in output


def test_track_cli_updates_existing_binding_when_profile_selection_changes(
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
            "git",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "pull"
    assert payload["bindings"][0]["selector"] == "git"
    assert payload["bindings"][0]["profile"] == "basic"
    assert payload["bindings"][0]["targets"][0]["action"] == "missing"


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
            "nvim@basic",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "pull"
    assert payload["bindings"][0]["selector"] == "nvim"
    assert payload["bindings"][0]["profile"] == "basic"
    assert payload["bindings"][0]["targets"][0]["action"] == "missing"


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
    assert payload["bindings"][0]["targets"][0]["action"] == "install"


@pytest.mark.parametrize("command", ["apply", "upgrade", "import", "remove"])
def test_legacy_top_level_cli_commands_are_not_available(command: str) -> None:
    parser = cli.build_parser()

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args([command, "example:nvim@basic"])

    assert exc_info.value.code == 2


def test_untrack_cli_updates_state(
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
            "untrack",
            "example:git@basic",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "state-only"
    assert payload["operation"] == "untrack"
    assert payload["binding"] == {
        "repo": "example",
        "selector": "git",
        "profile": "basic",
    }
    assert (state_dir / "bindings.toml").read_text(encoding="utf-8") == "\n".join(
        [
            "version = 1",
            "",
            "[[bindings]]",
            'repo = "example"',
            'selector = "core-cli-meta"',
            'profile = "basic"',
            "",
        ]
    )


def test_untrack_cli_allows_selector_only_when_unique(
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
            "untrack",
            "example:git",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["binding"] == {
        "repo": "example",
        "selector": "git",
        "profile": "basic",
    }


def test_untrack_cli_errors_for_untracked_binding(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    config_path = write_manager_config(tmp_path)

    exit_code = main(
        [
            "--config",
            str(config_path),
            "untrack",
            "example:git@basic",
        ]
    )

    assert exit_code == 2
    assert "is not currently tracked" in capsys.readouterr().err


def test_untrack_cli_reports_dependency_owner_for_untracked_package_selector(
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
            "untrack",
            "nvim@basic",
        ]
    )

    assert exit_code == 2
    assert "cannot untrack 'example:nvim': required by tracked bindings: example:core-cli-meta@basic" in capsys.readouterr().err


def test_list_installed_cli_lists_unique_installed_packages_with_bindings(
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
            "list",
            "installed",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "list-installed"
    packages = {item["package_id"]: item for item in payload["packages"]}
    assert set(packages) == {"core-cli-meta", "git", "nvim"}
    assert [binding["selector"] for binding in packages["git"]["bindings"]] == ["core-cli-meta", "git"]
    assert packages["git"]["description"] == "Base Git configuration"


def test_info_installed_cli_emits_package_details_for_installed_package(
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
            "installed",
            "git",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "info-installed"
    package = payload["package"]
    assert package["repo"] == "example"
    assert package["package_id"] == "git"
    assert package["description"] == "Base Git configuration"
    assert [binding["selector"] for binding in package["bindings"]] == ["core-cli-meta", "git"]
    target_names = {target["target_name"] for target in package["bindings"][0]["targets"]}
    assert target_names == {"gitconfig"}
    pre_push = package["bindings"][0]["hooks"]["pre_push"]
    assert pre_push[0]["package_id"] == "git"
    assert "git" in pre_push[0]["command"]


def test_reconcile_editor_subcommand_invokes_editor_with_repo_live_and_additional_sources(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo_path = tmp_path / "repo-file"
    live_path = tmp_path / "live-file"
    include_path = tmp_path / "include-file"
    repo_path.write_text("repo\n", encoding="utf-8")
    live_path.write_text("live\n", encoding="utf-8")
    include_path.write_text("include\n", encoding="utf-8")

    recorded: dict[str, object] = {}

    def fake_run(command: list[str], check: bool):
        recorded["command"] = command
        recorded["check"] = check
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("dotman.reconcile.subprocess.run", fake_run)

    exit_code = main(
        [
            "reconcile",
            "editor",
            "--editor",
            "nvim -d",
            "--repo-path",
            str(repo_path),
            "--live-path",
            str(live_path),
            "--additional-source",
            str(include_path),
        ]
    )

    assert exit_code == 0
    assert recorded["check"] is False
    assert recorded["command"] == [
        "nvim",
        "-d",
        str(repo_path.resolve()),
        str(live_path.resolve()),
        str(include_path.resolve()),
    ]


def test_reconcile_editor_subcommand_fails_for_missing_additional_source(
    tmp_path: Path,
    capsys,
) -> None:
    repo_path = tmp_path / "repo-file"
    live_path = tmp_path / "live-file"
    repo_path.write_text("repo\n", encoding="utf-8")
    live_path.write_text("live\n", encoding="utf-8")

    exit_code = main(
        [
            "reconcile",
            "editor",
            "--editor",
            "true",
            "--repo-path",
            str(repo_path),
            "--live-path",
            str(live_path),
            "--additional-source",
            str(tmp_path / "missing-source"),
        ]
    )

    assert exit_code == 2
    assert "additional source does not exist" in capsys.readouterr().err
