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


def test_untrack_cli_updates_state(
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
            "untrack",
            "example:git@basic",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "state-only"
    assert payload["operation"] == "untrack"
    assert payload["package_entry"] == {
        "repo": "example",
        "package_id": "git",
        "profile": "basic",
    }
    assert payload["still_tracked_package"] == {
        "repo": "example",
        "package_id": "git",
        "package_entries": [
            {
                "repo": "example",
                "package_id": "core-cli-meta",
                "profile": "basic",
                "tracked_reason": "implicit",
            }
        ],
    }
    assert (state_dir / "tracked-packages.toml").read_text(encoding="utf-8") == "\n".join(
        [
            "schema_version = 1",
            "",
            "[[packages]]",
            'repo = "example"',
            'package_id = "core-cli-meta"',
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
            "untrack",
            "example:git",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["package_entry"] == {
        "repo": "example",
        "package_id": "git",
        "profile": "basic",
    }


def test_untrack_cli_removes_tracked_entries_from_group_selector(
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
            "untrack",
            "example:os/arch",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "untracked 1 package entry from example:os/arch@basic" in output
    assert "example:core-cli-meta@basic" in output
    assert (state_dir / "tracked-packages.toml").read_text(encoding="utf-8") == "\n".join(
        [
            "schema_version = 1",
            "",
            "[[packages]]",
            'repo = "example"',
            'package_id = "git"',
            'profile = "basic"',
            "",
        ]
    )


def test_untrack_cli_emits_group_untrack_json(
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
            "untrack",
            "example:os/arch@basic",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "untrack"
    assert payload["request"] == {
        "repo": "example",
        "selector": "os/arch",
        "selector_kind": "group",
        "profile": "basic",
    }
    assert payload["package_entries"] == [
        {
            "repo": "example",
            "package_id": "core-cli-meta",
            "profile": "basic",
        }
    ]


def test_untrack_cli_removes_group_singletons_across_profiles_without_prompt(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "singleton-group-repo"
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "profiles" / "basic.toml").write_text("", encoding="utf-8")
    (repo_root / "profiles" / "work.toml").write_text("", encoding="utf-8")
    for package_id in ("alpha", "beta"):
        (repo_root / "packages" / package_id).mkdir(parents=True)
        (repo_root / "packages" / package_id / "package.toml").write_text(f'id = "{package_id}"\n', encoding="utf-8")
    (repo_root / "groups").mkdir()
    (repo_root / "groups" / "bundle.toml").write_text('members = ["alpha", "beta"]\n', encoding="utf-8")
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    state_dir = tmp_path / "state" / "dotman" / "repos" / "fixture"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "tracked-packages.toml").write_text(
        "\n".join(
            [
                "schema_version = 1",
                "",
                "[[packages]]",
                'repo = "fixture"',
                'package_id = "alpha"',
                'profile = "basic"',
                "",
                "[[packages]]",
                'repo = "fixture"',
                'package_id = "beta"',
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
            "untrack",
            "fixture:bundle",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "untracked 2 package entries from fixture:bundle" in output
    assert "fixture:alpha@basic" in output
    assert "fixture:beta@work" in output
    assert (state_dir / "tracked-packages.toml").read_text(encoding="utf-8") == "\n".join(
        [
            "schema_version = 1",
            "",
        ]
    )


def test_untrack_cli_requires_group_profile_when_tracked_members_span_profiles(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "multi-instance-repo"
    write_multi_instance_repo(repo_root)
    (repo_root / "groups").mkdir()
    (repo_root / "groups" / "bundle.toml").write_text('members = ["profiled"]\n', encoding="utf-8")
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    state_dir = tmp_path / "state" / "dotman" / "repos" / "fixture"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "tracked-packages.toml").write_text(
        "\n".join(
            [
                "schema_version = 1",
                "",
                "[[packages]]",
                'repo = "fixture"',
                'package_id = "profiled"',
                'profile = "basic"',
                "",
                "[[packages]]",
                'repo = "fixture"',
                'package_id = "profiled"',
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
            "untrack",
            "fixture:bundle",
        ]
    )

    assert exit_code == 2
    assert "tracked group 'fixture:bundle' is ambiguous across package instances: profiled<basic>, profiled<work>" in capsys.readouterr().err


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
            "untrack",
            "nvim@basic",
        ]
    )

    assert exit_code == 2
    assert "cannot untrack 'example:nvim': required by tracked package entries: example:core-cli-meta@basic" in capsys.readouterr().err

def test_untrack_cli_allows_removal_that_leaves_same_singleton_package_via_multiple_roots(
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

    state_dir = tmp_path / "state" / "dotman" / "repos" / "fixture"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "tracked-packages.toml").write_text(
        "\n".join(
            [
                "schema_version = 1",
                "",
                "[[packages]]",
                'repo = "fixture"',
                'package_id = "shared"',
                'profile = "direct"',
                "",
                "[[packages]]",
                'repo = "fixture"',
                'package_id = "stack-a"',
                'profile = "work"',
                "",
                "[[packages]]",
                'repo = "fixture"',
                'package_id = "stack-b"',
                'profile = "personal"',
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
            "fixture:shared@direct",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "untracked fixture:shared@direct" in output
    assert "fixture:shared remains tracked via:" in output
    assert "implicit: fixture:stack-a@work" in output
    assert "implicit: fixture:stack-b@personal" in output
    assert (state_dir / "tracked-packages.toml").read_text(encoding="utf-8") == "\n".join(
        [
            "schema_version = 1",
            "",
            "[[packages]]",
            'repo = "fixture"',
            'package_id = "stack-a"',
            'profile = "work"',
            "",
            "[[packages]]",
            'repo = "fixture"',
            'package_id = "stack-b"',
            'profile = "personal"',
            "",
        ]
    )

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
            "untrack",
            "example:git@basic",
        ]
    )

    assert exit_code == 0
    assert f"untracked {cli.render_full_spec_selector_label(repo_name='example', selector='git', profile='basic')}" in capsys.readouterr().out

def test_untrack_cli_reports_remaining_package_tracking_after_success(
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
            "untrack",
            "example:git@basic",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "untracked example:git@basic" in output
    assert "example:git remains tracked via:" in output
    assert "implicit: example:core-cli-meta@basic" in output
