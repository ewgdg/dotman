from __future__ import annotations

import json
import stat
from pathlib import Path

import dotman.cli as cli
from dotman.cli import main
from tests.helpers import write_named_manager_config


def _write_basic_execution_repo(
    repo_root: Path,
    *,
    failing_guard: bool = False,
    package_id: str = "app",
    live_dir_name: str = "app",
) -> None:
    package_root = repo_root / "packages" / package_id
    (package_root / "files").mkdir(parents=True)
    (repo_root / "profiles").mkdir(parents=True)

    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    (package_root / "files" / "config.txt").write_text("repo value\n", encoding="utf-8")

    guard_push = "printf 'guard push\\n'"
    if failing_guard:
        guard_push = "printf 'guard push failed\\n'; exit 1"

    (package_root / "package.toml").write_text(
        "\n".join(
            [
                f'id = "{package_id}"',
                "",
                "[targets.config]",
                'source = "files/config.txt"',
                f'path = "~/.config/{live_dir_name}/config.txt"',
                'chmod = "600"',
                "",
                "[hooks]",
                f"guard_push = \"{guard_push}\"",
                "pre_push = \"printf 'pre push\\n'\"",
                "post_push = \"printf 'post push\\n'\"",
                "guard_pull = \"printf 'guard pull\\n'\"",
                "pre_pull = \"printf 'pre pull\\n'\"",
                "post_pull = \"printf 'post pull\\n'\"",
                "",
            ]
        ),
        encoding="utf-8",
    )



def _write_reconcile_execution_repo(repo_root: Path) -> None:
    package_root = repo_root / "packages" / "app"
    (package_root / "files").mkdir(parents=True)
    (package_root / "hooks").mkdir(parents=True)
    (repo_root / "profiles").mkdir(parents=True)

    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    (package_root / "files" / "config.txt").write_text("repo value\n", encoding="utf-8")
    (package_root / "hooks" / "reconcile.sh").write_text(
        "\n".join(
            [
                "#!/bin/sh",
                "set -eu",
                "printf 'reconcile:%s:%s\\n' \"$DOTMAN_REPO_PATH\" \"$DOTMAN_LIVE_PATH\"",
                "cp \"$DOTMAN_LIVE_PATH\" \"$DOTMAN_REPO_PATH\"",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (package_root / "package.toml").write_text(
        "\n".join(
            [
                'id = "app"',
                "",
                "[targets.config]",
                'source = "files/config.txt"',
                'path = "~/.config/app/config.txt"',
                'reconcile = "sh hooks/reconcile.sh"',
                "",
                "[hooks]",
                'guard_pull = "printf \'guard pull\\n\'"',
                'post_pull = "printf \'post pull\\n\'"',
                "",
            ]
        ),
        encoding="utf-8",
    )



def _write_reconcile_preview_execution_repo(repo_root: Path) -> None:
    package_root = repo_root / "packages" / "app"
    (package_root / "files").mkdir(parents=True)
    (package_root / "hooks").mkdir(parents=True)
    (repo_root / "profiles").mkdir(parents=True)

    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    (package_root / "files" / "config.txt").write_text("repo source value\n", encoding="utf-8")
    (package_root / "hooks" / "render.sh").write_text(
        "\n".join(
            [
                "#!/bin/sh",
                "set -eu",
                "printf 'rendered repo view\\n'",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (package_root / "hooks" / "capture.sh").write_text(
        "\n".join(
            [
                "#!/bin/sh",
                "set -eu",
                "printf 'captured live view\\n'",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (package_root / "hooks" / "reconcile.sh").write_text(
        "\n".join(
            [
                "#!/bin/sh",
                "set -eu",
                "printf 'review:%s|%s\\n' \"$(tr -d '\\n' < \"$DOTMAN_REVIEW_REPO_PATH\")\" \"$(tr -d '\\n' < \"$DOTMAN_REVIEW_LIVE_PATH\")\"",
                "cp \"$DOTMAN_LIVE_PATH\" \"$DOTMAN_REPO_PATH\"",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (package_root / "package.toml").write_text(
        "\n".join(
            [
                'id = "app"',
                "",
                "[targets.config]",
                'source = "files/config.txt"',
                'path = "~/.config/app/config.txt"',
                'render = "sh hooks/render.sh"',
                'capture = "sh hooks/capture.sh"',
                'pull_view_repo = "render"',
                'pull_view_live = "capture"',
                'reconcile = "sh hooks/reconcile.sh"',
                "",
                "[hooks]",
                'guard_pull = "printf \'guard pull\\n\'"',
                'post_pull = "printf \'post pull\\n\'"',
                "",
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



def test_push_cli_executes_tracked_binding_and_emits_json_results(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _write_basic_execution_repo(repo_root)
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    _write_tracked_binding(tmp_path / "state")

    exit_code = main(["--config", str(config_path), "--json", "push"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    live_path = home / ".config" / "app" / "config.txt"
    assert live_path.read_text(encoding="utf-8") == "repo value\n"
    assert stat.S_IMODE(live_path.stat().st_mode) == 0o600
    assert payload["mode"] == "execute"
    assert payload["operation"] == "push"
    assert payload["packages"][0]["package_id"] == "app"
    assert [step["action"] for step in payload["packages"][0]["steps"]] == [
        "guard_push",
        "pre_push",
        "create",
        "chmod",
        "post_push",
    ]
    assert payload["packages"][0]["steps"][0]["stdout"] == "guard push\n"



def test_push_cli_human_execution_emits_package_timeline_and_nested_logs(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _write_basic_execution_repo(repo_root)
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    _write_tracked_binding(tmp_path / "state")

    exit_code = main(["--config", str(config_path), "push"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "\n:: executing push\n" in output
    assert "packages: 1" in output
    assert "steps: 5" in output
    assert ":: fixture:app@default" in output
    assert "[1/5] guard_push" in output
    assert "[4/5] chmod" in output
    assert "600" in output
    assert "[5/5] post_push" in output
    assert "guard push" in output
    assert "post push" in output
    assert "\n    done\n" not in output



def test_push_cli_human_execution_colors_step_status_only(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(cli, "colors_enabled", lambda: True)

    repo_root = tmp_path / "repo"
    _write_basic_execution_repo(repo_root)
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    _write_tracked_binding(tmp_path / "state")

    exit_code = main(["--config", str(config_path), "push"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "\033[1;32mok\033[0m" in output
    assert "\033[1;32mdone\033[0m" not in output



def test_pull_cli_creates_missing_repo_source_file_and_emits_json_results(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _write_basic_execution_repo(repo_root)
    repo_path = repo_root / "packages" / "app" / "files" / "config.txt"
    repo_path.unlink()
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    _write_tracked_binding(tmp_path / "state")

    live_path = home / ".config" / "app" / "config.txt"
    live_path.parent.mkdir(parents=True)
    live_path.write_text("live value\n", encoding="utf-8")

    exit_code = main(["--config", str(config_path), "--json", "pull"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert repo_path.read_text(encoding="utf-8") == "live value\n"
    assert payload["mode"] == "execute"
    assert payload["operation"] == "pull"
    assert [step["action"] for step in payload["packages"][0]["steps"]] == [
        "guard_pull",
        "pre_pull",
        "create_repo",
        "post_pull",
    ]



def test_pull_cli_executes_directory_pull_when_repo_source_directory_is_missing(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    package_root = repo_root / "packages" / "app"
    (repo_root / "profiles").mkdir(parents=True)
    package_root.mkdir(parents=True)
    (package_root / "package.toml").write_text(
        "\n".join(
            [
                'id = "app"',
                "",
                "[targets.config]",
                'source = "files/config"',
                'path = "~/.config/app"',
                "",
                "[hooks]",
                'guard_pull = "printf \'guard pull\\n\'"',
                'post_pull = "printf \'post pull\\n\'"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    _write_tracked_binding(tmp_path / "state")

    live_path = home / ".config" / "app" / "config.toml"
    live_path.parent.mkdir(parents=True)
    live_path.write_text("live value\n", encoding="utf-8")

    exit_code = main(["--config", str(config_path), "--json", "pull"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    repo_path = repo_root / "packages" / "app" / "files" / "config" / "config.toml"
    assert repo_path.read_text(encoding="utf-8") == "live value\n"
    assert [step["action"] for step in payload["packages"][0]["steps"]] == [
        "guard_pull",
        "create_repo",
        "post_pull",
    ]



def test_pull_cli_executes_direct_repo_update_and_emits_json_results(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _write_basic_execution_repo(repo_root)
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    _write_tracked_binding(tmp_path / "state")

    live_path = home / ".config" / "app" / "config.txt"
    live_path.parent.mkdir(parents=True)
    live_path.write_text("live value\n", encoding="utf-8")

    exit_code = main(["--config", str(config_path), "--json", "pull"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    repo_path = repo_root / "packages" / "app" / "files" / "config.txt"
    assert repo_path.read_text(encoding="utf-8") == "live value\n"
    assert payload["mode"] == "execute"
    assert payload["operation"] == "pull"
    assert [step["action"] for step in payload["packages"][0]["steps"]] == [
        "guard_pull",
        "pre_pull",
        "update_repo",
        "post_pull",
    ]



def test_pull_cli_uses_reconcile_command_for_selected_target_execution(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _write_reconcile_execution_repo(repo_root)
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    _write_tracked_binding(tmp_path / "state")

    live_path = home / ".config" / "app" / "config.txt"
    live_path.parent.mkdir(parents=True)
    live_path.write_text("live value\n", encoding="utf-8")

    exit_code = main(["--config", str(config_path), "--json", "pull"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    repo_path = repo_root / "packages" / "app" / "files" / "config.txt"
    assert repo_path.read_text(encoding="utf-8") == "live value\n"
    assert [step["action"] for step in payload["packages"][0]["steps"]] == [
        "guard_pull",
        "reconcile",
        "post_pull",
    ]
    assert payload["packages"][0]["steps"][1]["stdout"].startswith("reconcile:")



def test_pull_cli_passes_projected_review_paths_to_reconcile_execution(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _write_reconcile_preview_execution_repo(repo_root)
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    _write_tracked_binding(tmp_path / "state")

    live_path = home / ".config" / "app" / "config.txt"
    live_path.parent.mkdir(parents=True)
    live_path.write_text("raw live value\n", encoding="utf-8")

    exit_code = main(["--config", str(config_path), "--json", "pull"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    repo_path = repo_root / "packages" / "app" / "files" / "config.txt"
    assert repo_path.read_text(encoding="utf-8") == "raw live value\n"
    assert [step["action"] for step in payload["packages"][0]["steps"]] == [
        "guard_pull",
        "reconcile",
        "post_pull",
    ]
    assert payload["packages"][0]["steps"][1]["stdout"] == "review:rendered repo view|captured live view\n"



def test_push_cli_fails_fast_and_skips_post_push_after_failed_guard(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _write_basic_execution_repo(repo_root, failing_guard=True)
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    _write_tracked_binding(tmp_path / "state")

    exit_code = main(["--config", str(config_path), "--json", "push"])

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    live_path = home / ".config" / "app" / "config.txt"
    assert not live_path.exists()
    assert payload["packages"][0]["steps"][0]["action"] == "guard_push"
    assert payload["packages"][0]["steps"][0]["status"] == "failed"
    assert payload["packages"][0]["steps"][-1]["action"] == "post_push"
    assert payload["packages"][0]["steps"][-1]["status"] == "skipped"



def test_push_cli_human_execution_prints_package_skipped_only_for_skipped_packages(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    failing_repo_root = tmp_path / "repo-failing"
    skipped_repo_root = tmp_path / "repo-skipped"
    _write_basic_execution_repo(failing_repo_root, failing_guard=True, package_id="app", live_dir_name="app")
    _write_basic_execution_repo(skipped_repo_root, package_id="other", live_dir_name="other")
    config_path = write_named_manager_config(
        tmp_path,
        {"fixture-a": failing_repo_root, "fixture-b": skipped_repo_root},
    )
    _write_tracked_binding(tmp_path / "state", repo_name="fixture-a", selector="app")
    _write_tracked_binding(tmp_path / "state", repo_name="fixture-b", selector="other")

    exit_code = main(["--config", str(config_path), "push"])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert ":: fixture-a:app@default" in output
    assert "guard push failed" in output
    assert "\n    failed\n" not in output
    assert ":: fixture-b:other@default" in output
    assert "\n    skipped\n" in output
