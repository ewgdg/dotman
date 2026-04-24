from __future__ import annotations

import json
import stat
from pathlib import Path

import dotman.cli as cli
import dotman.cli_emit as cli_emit
from dotman.cli import main
from tests.helpers import write_named_manager_config


def _write_basic_execution_repo(
    repo_root: Path,
    *,
    failing_guard: bool = False,
    guard_push_exit_code: int | None = None,
    guard_pull_exit_code: int | None = None,
    package_id: str = "app",
    live_dir_name: str = "app",
) -> None:
    package_root = repo_root / "packages" / package_id
    (package_root / "files").mkdir(parents=True)
    (repo_root / "profiles").mkdir(parents=True)

    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    (package_root / "files" / "config.txt").write_text("repo value\n", encoding="utf-8")

    if failing_guard and guard_push_exit_code is None:
        guard_push_exit_code = 1

    guard_push = "printf 'guard push\\n'"
    if guard_push_exit_code is not None:
        if guard_push_exit_code == 100:
            guard_push = "printf 'guard push\\n'; exit 100"
        elif guard_push_exit_code != 0:
            guard_push = f"printf 'guard push failed\\n'; exit {guard_push_exit_code}"

    guard_pull = "printf 'guard pull\\n'"
    if guard_pull_exit_code is not None:
        if guard_pull_exit_code == 100:
            guard_pull = "printf 'guard pull\\n'; exit 100"
        elif guard_pull_exit_code != 0:
            guard_pull = f"printf 'guard pull failed\\n'; exit {guard_pull_exit_code}"

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
                f"guard_pull = \"{guard_pull}\"",
                "pre_pull = \"printf 'pre pull\\n'\"",
                "post_pull = \"printf 'post pull\\n'\"",
                "",
            ]
        ),
        encoding="utf-8",
    )



def _write_directory_execution_repo(
    repo_root: Path,
    *,
    package_id: str = "app",
    live_dir_name: str = "app",
) -> None:
    package_root = repo_root / "packages" / package_id
    (package_root / "files" / "config").mkdir(parents=True)
    (repo_root / "profiles").mkdir(parents=True)

    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    (package_root / "files" / "config" / "nested.txt").write_text("repo directory value\n", encoding="utf-8")
    (package_root / "package.toml").write_text(
        "\n".join(
            [
                f'id = "{package_id}"',
                "",
                "[targets.config]",
                'source = "files/config"',
                f'path = "~/.config/{live_dir_name}"',
                "",
                "[hooks]",
                "guard_push = \"printf 'guard push\\n'\"",
                "pre_push = \"printf 'pre push\\n'\"",
                "post_push = \"printf 'post push\\n'\"",
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
                'reconcile = { run = "sh hooks/reconcile.sh", io = "pipe" }',
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
                'reconcile = { run = "sh hooks/reconcile.sh", io = "pipe" }',
                "",
                "[hooks]",
                'guard_pull = "printf \'guard pull\\n\'"',
                'post_pull = "printf \'post pull\\n\'"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_capture_fallback_execution_repo(repo_root: Path) -> None:
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
                "printf 'capture failed\\n' >&2",
                "exit 1",
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
                'pull_view_live = "raw"',
                'reconcile = { run = "sh hooks/reconcile.sh", io = "pipe" }',
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
    (state_dir / "tracked-packages.toml").write_text(
        "\n".join(
            [
                "schema_version = 1",
                "",
                "[[packages]]",
                f'repo = "{repo_name}"',
                f'package_id = "{selector}"',
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



def test_push_cli_dry_run_emits_symlink_hazard_metadata(
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

    live_root = home / ".config" / "app"
    live_root.mkdir(parents=True)
    symlink_target = live_root / "config-real.txt"
    symlink_target.write_text("live value\n", encoding="utf-8")
    (live_root / "config.txt").symlink_to(symlink_target)

    exit_code = main(["--config", str(config_path), "--json", "push", "--dry-run"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    warning = payload["warnings"][0]
    assert warning["replaceable"] is True
    assert warning["live_path"] == str(live_root / "config.txt")
    assert warning["symlink_target"] == str(symlink_target)
    assert warning["target_kind"] == "file"
    assert payload["package_entries"][0]["package_id"] == "app"



def test_push_cli_dry_run_human_warning_uses_package_target_label(capsys) -> None:
    cli_emit.print_push_live_symlink_hazard_warning(
        [
            cli_emit.PushSymlinkHazard(
                selection_label="main:sunshine@host/linux",
                package_id="sunshine",
                target_name="f_config_sunshine_sunshine_conf",
                live_path=Path("/live/config.txt"),
                symlink_target="/real/config.txt",
                target_kind="file",
                replaceable=True,
            )
        ],
        use_color=False,
    )

    output = capsys.readouterr().out
    assert "[replaceable] main:sunshine.f_config_sunshine_sunshine_conf" in output
    assert ":f_config_sunshine_sunshine_conf" not in output



def test_push_cli_fails_fast_for_symlinked_live_target_in_non_interactive_mode(
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

    live_root = home / ".config" / "app"
    live_root.mkdir(parents=True)
    symlink_target = live_root / "config-real.txt"
    symlink_target.write_text("live value\n", encoding="utf-8")
    (live_root / "config.txt").symlink_to(symlink_target)

    exit_code = main(["--config", str(config_path), "push"])

    assert exit_code == 2
    error_output = capsys.readouterr().err
    assert "refusing to replace symlinked live target(s) in non-interactive mode" in error_output
    assert str(symlink_target) in error_output



def test_push_cli_allows_symlinked_live_target_with_yes_in_non_interactive_mode(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _write_basic_execution_repo(repo_root)
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    _write_tracked_binding(tmp_path / "state")

    live_root = home / ".config" / "app"
    live_root.mkdir(parents=True)
    symlink_target = live_root / "config-real.txt"
    symlink_target.write_text("live value\n", encoding="utf-8")
    (live_root / "config.txt").symlink_to(symlink_target)

    exit_code = main(["--config", str(config_path), "push", "--yes"])

    assert exit_code == 0
    live_path = live_root / "config.txt"
    assert live_path.is_file()
    assert not live_path.is_symlink()
    assert live_path.read_text(encoding="utf-8") == "repo value\n"
    assert symlink_target.read_text(encoding="utf-8") == "live value\n"


def test_push_cli_fails_fast_for_symlinked_directory_live_target_in_non_interactive_mode(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _write_directory_execution_repo(repo_root)
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    _write_tracked_binding(tmp_path / "state")

    live_root = home / ".config" / "app"
    live_root.parent.mkdir(parents=True, exist_ok=True)
    real_live_root = home / ".config" / "app-real"
    real_live_root.mkdir(parents=True)
    live_root.symlink_to(real_live_root, target_is_directory=True)

    exit_code = main(["--config", str(config_path), "push"])

    assert exit_code == 2
    error_output = capsys.readouterr().err
    assert "refusing to replace symlinked live target(s) in non-interactive mode" in error_output
    assert str(live_root) in error_output



def test_push_cli_follows_directory_symlink_when_configured(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _write_directory_execution_repo(repo_root)
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    _write_tracked_binding(tmp_path / "state")

    live_root = home / ".config" / "app"
    live_root.parent.mkdir(parents=True, exist_ok=True)
    real_live_root = home / ".config" / "app-real"
    real_live_root.mkdir(parents=True)
    live_root.symlink_to(real_live_root, target_is_directory=True)

    exit_code = main(["--config", str(config_path), "--dir-symlink-mode", "follow", "push"])

    assert exit_code == 0
    assert live_root.is_symlink()
    assert (real_live_root / "nested.txt").read_text(encoding="utf-8") == "repo directory value\n"



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



def test_pull_cli_allows_symlinked_live_target_and_updates_repo(
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

    live_root = home / ".config" / "app"
    live_root.mkdir(parents=True)
    symlink_target = live_root / "config-real.txt"
    symlink_target.write_text("live value\n", encoding="utf-8")
    (live_root / "config.txt").symlink_to(symlink_target)

    exit_code = main(["--config", str(config_path), "--json", "pull"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    repo_path = repo_root / "packages" / "app" / "files" / "config.txt"
    assert repo_path.read_text(encoding="utf-8") == "live value\n"
    assert payload["packages"][0]["steps"][2]["action"] == "update_repo"



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



def test_pull_cli_uses_reconcile_for_selected_target_execution(
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



def test_pull_cli_prefers_capture_over_reconcile_when_both_are_defined(
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
    assert repo_path.read_text(encoding="utf-8") == "captured live view\n"
    assert [step["action"] for step in payload["packages"][0]["steps"]] == [
        "guard_pull",
        "update_repo",
        "post_pull",
    ]
    assert payload["packages"][0]["steps"][1]["stdout"] == ""


def test_pull_cli_falls_back_to_reconcile_when_capture_fails(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _write_capture_fallback_execution_repo(repo_root)
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
        "update_repo",
        "post_pull",
    ]
    assert payload["packages"][0]["steps"][1]["stdout"] == "review:rendered repo view|raw live value\n"
    assert "capture failed; falling back to reconcile" in payload["packages"][0]["steps"][1]["stderr"]


def test_push_cli_run_noop_executes_hooks_for_all_noop_push_plan(
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
    live_path.write_text("repo value\n", encoding="utf-8")

    exit_code = main(["--config", str(config_path), "push", "--run-noop"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert ":: executing push" in output
    assert "packages: 1" in output
    assert "steps: 3" in output
    assert "[1/3] guard_push" in output
    assert "[2/3] pre_push" in output
    assert "[3/3] post_push" in output
    assert "guard push" in output
    assert "pre push" in output
    assert "post push" in output
    assert "noop" not in output
    assert "[1/3] create" not in output


def test_push_cli_run_noop_dry_run_json_shows_hook_only_package(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    data_home = tmp_path / "data"
    home.mkdir()
    data_home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))

    repo_root = tmp_path / "repo"
    _write_basic_execution_repo(repo_root)
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    _write_tracked_binding(tmp_path / "state")

    live_path = home / ".config" / "app" / "config.txt"
    live_path.parent.mkdir(parents=True)
    live_path.write_text("repo value\n", encoding="utf-8")

    exit_code = main(["--config", str(config_path), "--json", "push", "--dry-run", "--run-noop"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["package_entries"][0]["targets"] == []
    assert set(payload["package_entries"][0]["hooks"]) == {"guard_push", "pre_push", "post_push"}


def test_push_cli_run_noop_hook_only_plan_soft_skips_guard_and_does_not_create_snapshot(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    data_home = tmp_path / "data"
    home.mkdir()
    data_home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))

    repo_root = tmp_path / "repo"
    _write_basic_execution_repo(repo_root, guard_push_exit_code=100)
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    _write_tracked_binding(tmp_path / "state")

    live_path = home / ".config" / "app" / "config.txt"
    live_path.parent.mkdir(parents=True)
    live_path.write_text("repo value\n", encoding="utf-8")

    exit_code = main(["--config", str(config_path), "push", "--run-noop"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "skipped (guard)" in output
    assert "pre push" not in output
    assert "post push" not in output
    snapshots_root = data_home / "dotman" / "snapshots"
    assert not snapshots_root.exists() or list(snapshots_root.iterdir()) == []


def test_push_cli_run_noop_hook_only_plan_soft_skips_guard_in_json(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    data_home = tmp_path / "data"
    home.mkdir()
    data_home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))

    repo_root = tmp_path / "repo"
    _write_basic_execution_repo(repo_root, guard_push_exit_code=100)
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    _write_tracked_binding(tmp_path / "state")

    exit_code = main(["--config", str(config_path), "--json", "push", "--run-noop"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["packages"][0]["status"] == "skipped"
    assert payload["packages"][0]["skip_reason"] == "guard"
    assert payload["packages"][0]["steps"][0]["skip_reason"] == "guard"
    assert payload["packages"][0]["steps"][-1]["skip_reason"] == "guard"


def test_pull_cli_run_noop_executes_hooks_for_all_noop_pull_plan(
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
    live_path.write_text("repo value\n", encoding="utf-8")

    exit_code = main(["--config", str(config_path), "pull", "--run-noop"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert ":: executing pull" in output
    assert "packages: 1" in output
    assert "steps: 3" in output
    assert "[1/3] guard_pull" in output
    assert "[2/3] pre_pull" in output
    assert "[3/3] post_pull" in output
    assert "guard pull" in output
    assert "pre pull" in output
    assert "post pull" in output
    assert "noop" not in output
    assert "[1/3] update_repo" not in output


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



def test_capture_patch_cli_emits_patched_repo_bytes(
    tmp_path: Path,
    capsys,
) -> None:
    repo_path = tmp_path / "config.txt"
    review_repo_path = tmp_path / "review-repo.txt"
    review_live_path = tmp_path / "review-live.txt"

    repo_path.write_text("greeting = {{ vars.greeting }}\n", encoding="utf-8")
    review_repo_path.write_text("greeting = hello\n", encoding="utf-8")
    review_live_path.write_text("greeting = world\n", encoding="utf-8")

    exit_code = main(
        [
            "capture",
            "patch",
            "--repo-path",
            str(repo_path),
            "--render",
            "jinja",
            "--review-repo-path",
            str(review_repo_path),
            "--review-live-path",
            str(review_live_path),
            "--var",
            "greeting=hello",
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out == "greeting = world\n"



def test_capture_patch_cli_accepts_command_renderers(
    tmp_path: Path,
    capsys,
) -> None:
    repo_path = tmp_path / "config.txt"
    review_repo_path = tmp_path / "review-repo.txt"
    review_live_path = tmp_path / "review-live.txt"

    repo_path.write_text("greeting = @@greeting@@\n", encoding="utf-8")
    review_repo_path.write_text("greeting = hello\n", encoding="utf-8")
    review_live_path.write_text("greeting = world\n", encoding="utf-8")

    exit_code = main(
        [
            "capture",
            "patch",
            "--repo-path",
            str(repo_path),
            "--render",
            'sed "s/@@greeting@@/$DOTMAN_VAR_greeting/g" "$DOTMAN_SOURCE"',
            "--review-repo-path",
            str(review_repo_path),
            "--review-live-path",
            str(review_live_path),
            "--var",
            "greeting=hello",
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out == "greeting = world\n"
