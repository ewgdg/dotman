from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import subprocess

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


def write_profile_switch_repo(repo_root: Path) -> None:
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "packages" / "alpha" / "files").mkdir(parents=True)
    (repo_root / "packages" / "beta" / "files").mkdir(parents=True)
    (repo_root / "profiles" / "basic.toml").write_text("", encoding="utf-8")
    (repo_root / "profiles" / "work.toml").write_text("", encoding="utf-8")
    (repo_root / "packages" / "alpha" / "files" / "alpha.conf").write_text("alpha\n", encoding="utf-8")
    (repo_root / "packages" / "beta" / "files" / "beta.conf").write_text("beta\n", encoding="utf-8")
    (repo_root / "packages" / "alpha" / "package.toml").write_text(
        "\n".join(
            [
                'id = "alpha"',
                "",
                "[targets.alpha]",
                'source = "files/alpha.conf"',
                'path = "~/.config/{{ profile }}.conf"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "beta" / "package.toml").write_text(
        "\n".join(
            [
                'id = "beta"',
                "",
                "[targets.beta]",
                'source = "files/beta.conf"',
                'path = "~/.config/basic.conf"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_implicit_conflict_repo(repo_root: Path) -> None:
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "groups").mkdir(parents=True)
    (repo_root / "packages" / "alpha" / "files").mkdir(parents=True)
    (repo_root / "packages" / "beta" / "files").mkdir(parents=True)
    (repo_root / "packages" / "alpha-meta").mkdir(parents=True)
    (repo_root / "packages" / "beta-meta").mkdir(parents=True)
    (repo_root / "profiles" / "basic.toml").write_text("", encoding="utf-8")
    (repo_root / "groups" / "alpha-stack.toml").write_text('members = ["alpha-meta"]\n', encoding="utf-8")
    (repo_root / "groups" / "beta-stack.toml").write_text('members = ["beta-meta"]\n', encoding="utf-8")
    (repo_root / "packages" / "alpha" / "files" / "shared.conf").write_text("alpha\n", encoding="utf-8")
    (repo_root / "packages" / "beta" / "files" / "shared.conf").write_text("beta\n", encoding="utf-8")
    (repo_root / "packages" / "alpha" / "package.toml").write_text(
        "\n".join(
            [
                'id = "alpha"',
                "",
                "[targets.shared]",
                'source = "files/shared.conf"',
                'path = "~/.config/shared.conf"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "beta" / "package.toml").write_text(
        "\n".join(
            [
                'id = "beta"',
                "",
                "[targets.shared]",
                'source = "files/shared.conf"',
                'path = "~/.config/shared.conf"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "alpha-meta" / "package.toml").write_text(
        "\n".join(
            [
                'id = "alpha-meta"',
                'depends = ["alpha"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "beta-meta" / "package.toml").write_text(
        "\n".join(
            [
                'id = "beta-meta"',
                'depends = ["beta"]',
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_untrack_conflict_repo(repo_root: Path) -> None:
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "packages" / "shared" / "files").mkdir(parents=True)
    (repo_root / "packages" / "stack-a").mkdir(parents=True)
    (repo_root / "packages" / "stack-b").mkdir(parents=True)

    for profile_name in ("direct", "work", "personal"):
        (repo_root / "profiles" / f"{profile_name}.toml").write_text("", encoding="utf-8")

    (repo_root / "packages" / "shared" / "files" / "shared.conf").write_text(
        "profile={{ profile }}\n",
        encoding="utf-8",
    )
    (repo_root / "packages" / "shared" / "package.toml").write_text(
        "\n".join(
            [
                'id = "shared"',
                "",
                "[targets.shared]",
                'source = "files/shared.conf"',
                'path = "~/.config/shared.conf"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "stack-a" / "package.toml").write_text(
        "\n".join(
            [
                'id = "stack-a"',
                'depends = ["shared"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "stack-b" / "package.toml").write_text(
        "\n".join(
            [
                'id = "stack-b"',
                'depends = ["shared"]',
                "",
            ]
        ),
        encoding="utf-8",
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
    assert "\033[2m/\033[0m" in output
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
    ) == "example/git"


def test_render_binding_label_can_prioritize_selector_name(monkeypatch) -> None:
    monkeypatch.setattr(cli, "colors_enabled", lambda: False)

    assert cli.render_binding_label(
        repo_name="example",
        selector="git",
        profile="basic",
        selector_first=True,
    ) == "example/git@basic"


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
    assert "alpha/sunshine [package]" in output
    assert "beta/sunshine [package]" in output
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
            display_label=f"{match[0]}/{match[1]}",
            match_fields=cli.build_selector_match_fields(repo_name=match[0], selector=match[1]),
        ),
        exact_error_text="unused",
        partial_error_text="unused",
        not_found_text="unused",
    )

    assert selected == ("sandbox", "sunshine")
    assert captured["option_labels"] == ["sandbox/sunshine", "sandbox/host/linux-meta"]


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


def test_select_menu_option_with_fzf_uses_hidden_match_fields(monkeypatch) -> None:
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
        option_labels=["sandbox/sunshine", "sandbox/host/linux-meta"],
        option_search_fields=[
            ("sunshine", "sandbox/sunshine"),
            ("host/linux-meta", "sandbox/host/linux-meta"),
        ],
    )

    assert selected_index == 1
    assert "--nth=2..3" in captured["command"]
    assert "--with-nth=4" in captured["command"]
    assert "--layout=reverse-list" in captured["command"]
    assert captured["input"] == (
        "1\tsunshine\tsandbox/sunshine\tsandbox/sunshine\n"
        "2\thost/linux-meta\tsandbox/host/linux-meta\tsandbox/host/linux-meta\n"
    )


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
    assert "alpha/sunshine@host/linux" in output
    assert "beta/sunshine@host/linux" in output
    assert "sunshine:selected_config -> create" in output


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
    assert "alpha/sunshine" in output
    assert "beta/sunshine" in output
    assert "beta/sunshine" in output


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
    monkeypatch.setattr(cli, "run_diff_review_menu", lambda review_items, *, operation: False)

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
        ]
    )

    assert exit_code == 130
    assert capsys.readouterr().err == "\ninterrupted\n"


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
    assert "----- Diff 1/2: example/git (gitconfig) [update] -----" in output
    assert "----- End Diff 1/2 -----" in output
    assert "----- Diff 2/2: example/zsh (zshrc) [update] -----" in output
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
    assert "----- Diff 1/1: example/git (gitconfig) [update] -----" in output
    assert "----- End Diff 1/1 -----" in output


def test_print_selection_header_prepends_blank_line(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "colors_enabled", lambda: False)

    cli.print_selection_header("Review pending diffs for pull:")

    assert capsys.readouterr().out == "\nReview pending diffs for pull:\n"


def test_review_menu_prompt_prepends_blank_line(monkeypatch) -> None:
    monkeypatch.setattr(cli, "colors_enabled", lambda: False)

    assert cli.review_menu_prompt() == '\nReview command ("?", number, "a", "c", "q"; default: continue): '


def test_pending_selection_prompt_prepends_blank_line(monkeypatch) -> None:
    monkeypatch.setattr(cli, "colors_enabled", lambda: False)

    assert cli.pending_selection_prompt() == '\nExclude by number or range ("?"; e.g. "1 2 4-6" or "^3"; default: none): '


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
    assert "  a          inspect all diffs" in output
    assert '  "?"        show this help' in output


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
    assert "implicit: example:os/arch@basic (git:gitconfig)" in output
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
    assert "implicit: fixture:beta-stack@basic (beta:shared)" in output
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
            "nvim@basic",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "pull"
    assert payload["bindings"][0]["selector"] == "nvim"
    assert payload["bindings"][0]["profile"] == "basic"
    assert payload["bindings"][0]["targets"][0]["action"] == "delete"


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
    assert payload["still_tracked_package"] == {
        "repo": "example",
        "package_id": "git",
        "bindings": [
            {
                "repo": "example",
                "selector": "core-cli-meta",
                "profile": "basic",
                "selector_kind": "package",
                "tracked_reason": "implicit",
            }
        ],
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


def test_untrack_cli_rejects_removal_that_would_expose_implicit_conflict(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "fixture-repo"
    write_untrack_conflict_repo(repo_root)
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})

    state_dir = tmp_path / "state" / "fixture"
    state_dir.mkdir(parents=True, exist_ok=True)
    original_state = "\n".join(
        [
            "version = 1",
            "",
            "[[bindings]]",
            'repo = "fixture"',
            'selector = "shared"',
            'profile = "direct"',
            "",
            "[[bindings]]",
            'repo = "fixture"',
            'selector = "stack-a"',
            'profile = "work"',
            "",
            "[[bindings]]",
            'repo = "fixture"',
            'selector = "stack-b"',
            'profile = "personal"',
            "",
        ]
    )
    (state_dir / "bindings.toml").write_text(original_state, encoding="utf-8")

    exit_code = main(
        [
            "--config",
            str(config_path),
            "untrack",
            "fixture:shared@direct",
        ]
    )

    assert exit_code == 2
    error_output = capsys.readouterr().err
    assert "cannot untrack 'fixture:shared@direct': removing this binding would expose conflicting implicit tracked targets" in error_output
    assert "fixture:stack-a@work (shared:shared)" in error_output
    assert "fixture:stack-b@personal (shared:shared)" in error_output
    assert (state_dir / "bindings.toml").read_text(encoding="utf-8") == original_state


def test_untrack_cli_uses_rendered_binding_label_for_terminal_output(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(cli, "colors_enabled", lambda: True)

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
            "untrack",
            "example:git@basic",
        ]
    )

    assert exit_code == 0
    assert f"untracked {cli.render_binding_label(repo_name='example', selector='git', profile='basic')}" in capsys.readouterr().out


def test_untrack_cli_reports_remaining_package_tracking_after_success(
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
            "untrack",
            "example:git@basic",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "untracked example:git@basic" in output
    assert "example/git remains tracked via:" in output
    assert "implicit: example:core-cli-meta@basic" in output


def test_list_tracked_cli_lists_unique_tracked_packages(
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
            "tracked",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "list-tracked"
    packages = {item["package_id"]: item for item in payload["packages"]}
    assert set(packages) == {"core-cli-meta", "git", "nvim"}
    assert [binding["selector"] for binding in packages["git"]["bindings"]] == ["core-cli-meta", "git"]
    assert packages["git"]["description"] == "Base Git configuration"


def test_list_tracked_cli_emits_readable_text_output(
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
            "list",
            "tracked",
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out == "\n".join(
        [
            "example/core-cli-meta",
            "example/git",
            "example/nvim",
            "",
        ]
    )


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
    assert package["effective_targets"] == [
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
    pre_push = package["bindings"][0]["hooks"]["pre_push"]
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
            "example/git",
            "  Base Git configuration",
            "  ::provenance",
            "    implicit: example:core-cli-meta@basic",
            "    explicit: example:git@basic",
            "  ::effective targets",
            f"    gitconfig@basic -> {home / '.gitconfig'}",
            "",
        ]
    )


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
        review_path = Path(command[1])
        assert review_path.name == "reconcile-review.md"
        assert review_path.read_text(encoding="utf-8") == "\n".join(
            [
                "# Dotman Reconcile Review",
                "",
                "Review only. Do not edit this file.",
                "Inspect the diff below, then edit the real repo source files listed under editable sources.",
                "",
                "## Review Inputs",
                "",
                f"- review repo path: {repo_path.resolve()}",
                f"- review live path: {live_path.resolve()}",
                "",
                "## Editable Sources",
                "",
                f"- {repo_path.resolve()}",
                f"- {include_path.resolve()}",
                "",
                "## Diff",
                "",
                "```diff",
                f"--- {repo_path.resolve()}",
                f"+++ {live_path.resolve()}",
                "@@ -1 +1 @@",
                "-repo",
                "+live",
                "```",
                "",
            ]
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("dotman.reconcile.subprocess.run", fake_run)

    exit_code = main(
        [
            "reconcile",
            "editor",
            "--editor",
            "nvim",
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
    command = recorded["command"]
    assert command[0] == "nvim"
    assert command[2:] == [
        str(repo_path.resolve()),
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
