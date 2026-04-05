from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from dotman.cli import main


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_REPO = PROJECT_ROOT / "examples" / "repo"
REFERENCE_REPO = PROJECT_ROOT / "tests" / "fixtures" / "reference_repo"


def write_manager_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[repos.example]",
                f'path = "{EXAMPLE_REPO}"',
                "order = 10",
                f'state_path = "{tmp_path / "state" / "example"}"',
                "",
                "[repos.sandbox]",
                f'path = "{REFERENCE_REPO}"',
                "order = 20",
                f'state_path = "{tmp_path / "state" / "sandbox"}"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def test_apply_cli_emits_dry_run_json(
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
            "apply",
            "example:git@basic",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "dry-run"
    assert payload["operation"] == "apply"
    assert payload["bindings"][0]["repo"] == "example"
    assert payload["bindings"][0]["selector"] == "git"
    assert payload["bindings"][0]["targets"][0]["action"] == "install"
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


def test_upgrade_cli_uses_state_bindings_in_dry_run_json(
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
            "upgrade",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "upgrade"
    assert payload["bindings"][0]["selector"] == "git"
    assert payload["bindings"][0]["profile"] == "basic"


def test_remove_binding_cli_updates_state(
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
            "remove",
            "binding",
            "example:git@basic",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "state-only"
    assert payload["operation"] == "remove-binding"
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


def test_remove_binding_cli_allows_selector_only_when_unique(
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
            "remove",
            "binding",
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


def test_remove_binding_cli_errors_for_untracked_binding(
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
            "remove",
            "binding",
            "example:git@basic",
        ]
    )

    assert exit_code == 2
    assert "is not currently tracked" in capsys.readouterr().err


def test_remove_binding_cli_reports_dependency_owner_for_untracked_package_selector(
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
            "remove",
            "binding",
            "nvim@basic",
        ]
    )

    assert exit_code == 2
    assert "cannot remove 'example:nvim': required by tracked bindings: example:core-cli-meta@basic" in capsys.readouterr().err


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
    pre_apply = package["bindings"][0]["hooks"]["pre_apply"]
    assert pre_apply[0]["package_id"] == "git"
    assert "git" in pre_apply[0]["command"]


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
