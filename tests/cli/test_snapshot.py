from __future__ import annotations

import json
from pathlib import Path

from dotman.cli import main
from dotman.snapshot import list_snapshots
from tests.helpers import capture_parser_help, write_named_manager_config


def _write_snapshot_execution_repo(repo_root: Path, *, package_id: str = "app") -> None:
    package_root = repo_root / "packages" / package_id
    (package_root / "files").mkdir(parents=True)
    (repo_root / "profiles").mkdir(parents=True)

    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    (package_root / "files" / "config.txt").write_text("repo value\n", encoding="utf-8")
    (package_root / "package.toml").write_text(
        "\n".join(
            [
                f'id = "{package_id}"',
                "",
                "[targets.config]",
                'source = "files/config.txt"',
                f'path = "~/.config/{package_id}/config.txt"',
                '',
            ]
        ),
        encoding="utf-8",
    )


def _write_tracked_binding(state_root: Path, *, repo_name: str = "fixture", selector: str = "app") -> None:
    state_dir = state_root / "dotman" / "repos" / repo_name
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "bindings.toml").write_text(
        "\n".join(
            [
                "version = 1",
                "",
                "[[bindings]]",
                f'repo = "{repo_name}"',
                f'selector = "{selector}"',
                'profile = "default"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_snapshot_config(tmp_path: Path, repo_root: Path, *, max_generations: int = 5) -> Path:
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    with config_path.open("a", encoding="utf-8") as handle:
        handle.write(
            "\n".join(
                [
                    "[snapshots]",
                    f'path = "{tmp_path / "snapshots"}"',
                    f"max_generations = {max_generations}",
                    "",
                ]
            )
        )
    return config_path


def test_rollback_help_lists_dry_run_and_full_path_flags(capsys) -> None:
    output = capture_parser_help(capsys, "rollback")
    assert "usage: dotman rollback [-h] [-d] [--full-path] [<snapshot>]" in output
    assert "-d, --dry-run" in output
    assert "--full-path" in output


def test_list_help_includes_snapshots_subcommand(capsys) -> None:
    output = capture_parser_help(capsys, "list")
    assert "List available snapshots" in output


def test_info_help_includes_snapshot_subcommand(capsys) -> None:
    output = capture_parser_help(capsys, "info")
    assert "Show snapshot details" in output


def test_info_snapshot_help_lists_full_path_flag(capsys) -> None:
    output = capture_parser_help(capsys, "info", "snapshot")
    assert "usage: dotman info snapshot [-h] [--full-path] <snapshot>" in output
    assert "--full-path" in output


def test_push_execute_creates_snapshot_and_rollback_restores_latest_snapshot(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _write_snapshot_execution_repo(repo_root)
    config_path = _write_snapshot_config(tmp_path, repo_root)
    _write_tracked_binding(tmp_path / "state")

    live_path = home / ".config" / "app" / "config.txt"
    live_path.parent.mkdir(parents=True, exist_ok=True)
    live_path.write_text("before push\n", encoding="utf-8")

    push_exit_code = main(["--config", str(config_path), "push"])

    assert push_exit_code == 0
    assert live_path.read_text(encoding="utf-8") == "repo value\n"
    snapshots = list_snapshots(tmp_path / "snapshots")
    assert len(snapshots) == 1
    assert snapshots[0].status == "applied"
    assert snapshots[0].entries[0].live_path == live_path
    assert snapshots[0].entries[0].existed_before is True
    assert snapshots[0].entries[0].push_action == "update"
    assert snapshots[0].entries[0].content_path is not None
    assert (snapshots[0].root / snapshots[0].entries[0].content_path).read_text(encoding="utf-8") == "before push\n"

    live_path.write_text("mutated after push\n", encoding="utf-8")

    rollback_exit_code = main(["--config", str(config_path), "rollback"])

    assert rollback_exit_code == 0
    assert live_path.read_text(encoding="utf-8") == "before push\n"
    restored_snapshots = list_snapshots(tmp_path / "snapshots")
    assert restored_snapshots[0].status == "applied"
    assert restored_snapshots[0].restore_count == 1
    assert restored_snapshots[0].last_restored_at is not None
    assert "executing rollback" in capsys.readouterr().out


def test_push_dry_run_does_not_create_snapshot(tmp_path: Path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _write_snapshot_execution_repo(repo_root)
    config_path = _write_snapshot_config(tmp_path, repo_root)
    _write_tracked_binding(tmp_path / "state")

    exit_code = main(["--config", str(config_path), "--json", "push", "--dry-run"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "dry-run"
    assert payload["operation"] == "push"
    assert list_snapshots(tmp_path / "snapshots") == []


def test_push_snapshot_retention_prunes_oldest_generations(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _write_snapshot_execution_repo(repo_root)
    config_path = _write_snapshot_config(tmp_path, repo_root, max_generations=1)
    _write_tracked_binding(tmp_path / "state")

    live_path = home / ".config" / "app" / "config.txt"
    live_path.parent.mkdir(parents=True, exist_ok=True)

    live_path.write_text("first\n", encoding="utf-8")
    assert main(["--config", str(config_path), "push"]) == 0
    first_snapshot = list_snapshots(tmp_path / "snapshots")[0].snapshot_id

    live_path.write_text("second\n", encoding="utf-8")
    assert main(["--config", str(config_path), "push"]) == 0

    snapshots = list_snapshots(tmp_path / "snapshots")
    assert len(snapshots) == 1
    assert snapshots[0].snapshot_id != first_snapshot


def test_rollback_without_snapshot_argument_restores_latest_snapshot(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _write_snapshot_execution_repo(repo_root)
    config_path = _write_snapshot_config(tmp_path, repo_root)
    _write_tracked_binding(tmp_path / "state")

    live_path = home / ".config" / "app" / "config.txt"
    live_path.parent.mkdir(parents=True, exist_ok=True)

    live_path.write_text("first\n", encoding="utf-8")
    assert main(["--config", str(config_path), "push"]) == 0

    live_path.write_text("second\n", encoding="utf-8")
    assert main(["--config", str(config_path), "push"]) == 0

    live_path.write_text("mutated after push\n", encoding="utf-8")

    rollback_exit_code = main(["--config", str(config_path), "rollback"])

    assert rollback_exit_code == 0
    assert live_path.read_text(encoding="utf-8") == "second\n"


def test_rollback_latest_argument_restores_latest_snapshot(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _write_snapshot_execution_repo(repo_root)
    config_path = _write_snapshot_config(tmp_path, repo_root)
    _write_tracked_binding(tmp_path / "state")

    live_path = home / ".config" / "app" / "config.txt"
    live_path.parent.mkdir(parents=True, exist_ok=True)

    live_path.write_text("first\n", encoding="utf-8")
    assert main(["--config", str(config_path), "push"]) == 0

    live_path.write_text("second\n", encoding="utf-8")
    assert main(["--config", str(config_path), "push"]) == 0

    live_path.write_text("mutated after push\n", encoding="utf-8")

    rollback_exit_code = main(["--config", str(config_path), "rollback", "latest"])

    assert rollback_exit_code == 0
    assert live_path.read_text(encoding="utf-8") == "second\n"


def test_info_snapshot_latest_argument_resolves_latest_snapshot(tmp_path: Path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _write_snapshot_execution_repo(repo_root)
    config_path = _write_snapshot_config(tmp_path, repo_root)
    _write_tracked_binding(tmp_path / "state")

    live_path = home / ".config" / "app" / "config.txt"
    live_path.parent.mkdir(parents=True, exist_ok=True)

    live_path.write_text("first\n", encoding="utf-8")
    assert main(["--config", str(config_path), "push"]) == 0

    live_path.write_text("second\n", encoding="utf-8")
    assert main(["--config", str(config_path), "push"]) == 0
    latest_snapshot = list_snapshots(tmp_path / "snapshots")[0].snapshot_id
    capsys.readouterr()

    exit_code = main(["--config", str(config_path), "--json", "info", "snapshot", "latest"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["snapshot"]["snapshot_id"] == latest_snapshot


def test_list_snapshots_cli_emits_human_summary(tmp_path: Path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)

    repo_root = tmp_path / "repo"
    _write_snapshot_execution_repo(repo_root)
    config_path = _write_snapshot_config(tmp_path, repo_root)
    _write_tracked_binding(tmp_path / "state")

    live_path = home / ".config" / "app" / "config.txt"
    live_path.parent.mkdir(parents=True, exist_ok=True)
    live_path.write_text("before push\n", encoding="utf-8")
    assert main(["--config", str(config_path), "push"]) == 0
    capsys.readouterr()

    exit_code = main(["--config", str(config_path), "list", "snapshots"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert ":: snapshots" in output
    assert "ref:" in output
    assert "status:" in output
    assert "paths:" in output
    assert "applied" in output


def test_list_snapshots_cli_emits_json(tmp_path: Path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _write_snapshot_execution_repo(repo_root)
    config_path = _write_snapshot_config(tmp_path, repo_root)
    _write_tracked_binding(tmp_path / "state")

    live_path = home / ".config" / "app" / "config.txt"
    live_path.parent.mkdir(parents=True, exist_ok=True)
    live_path.write_text("before push\n", encoding="utf-8")
    assert main(["--config", str(config_path), "push"]) == 0
    capsys.readouterr()

    exit_code = main(["--config", str(config_path), "--json", "list", "snapshots"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "list-snapshots"
    assert payload["snapshots"][0]["status"] == "applied"
    assert payload["snapshots"][0]["entry_count"] == 1
    assert payload["snapshots"][0]["restore_count"] == 0
    assert payload["snapshots"][0]["last_restored_at"] is None


def test_info_snapshot_cli_uses_full_paths_when_requested(tmp_path: Path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)

    repo_root = tmp_path / "repo"
    _write_snapshot_execution_repo(repo_root, package_id="very-long-app")
    config_path = _write_snapshot_config(tmp_path, repo_root)
    _write_tracked_binding(tmp_path / "state", selector="very-long-app")

    live_path = home / ".config" / "very-long-app" / "config.txt"
    live_path.parent.mkdir(parents=True, exist_ok=True)
    live_path.write_text("before push\n", encoding="utf-8")
    assert main(["--config", str(config_path), "push"]) == 0
    snapshot_id = list_snapshots(tmp_path / "snapshots")[0].snapshot_id
    capsys.readouterr()

    exit_code = main(["--config", str(config_path), "info", "snapshot", "--full-path", snapshot_id])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert str(live_path) in output
    assert ".../very-long-app/config.txt" not in output
    assert "reason: before update (push)" in output
    assert "provenance:" in output
    assert "fixture:very-long-app@default (config)" in output
    assert "target:" not in output
    assert "binding:" not in output
    assert "[update]" not in output


def test_info_snapshot_cli_emits_json_with_recorded_paths(tmp_path: Path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _write_snapshot_execution_repo(repo_root)
    config_path = _write_snapshot_config(tmp_path, repo_root)
    _write_tracked_binding(tmp_path / "state")

    live_path = home / ".config" / "app" / "config.txt"
    live_path.parent.mkdir(parents=True, exist_ok=True)
    live_path.write_text("before push\n", encoding="utf-8")
    assert main(["--config", str(config_path), "push"]) == 0
    snapshot_id = list_snapshots(tmp_path / "snapshots")[0].snapshot_id
    capsys.readouterr()

    exit_code = main(["--config", str(config_path), "--json", "info", "snapshot", snapshot_id])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "info-snapshot"
    assert payload["snapshot"]["snapshot_id"] == snapshot_id
    assert payload["snapshot"]["entries"][0]["live_path"] == str(live_path)
    assert payload["snapshot"]["restore_count"] == 0
