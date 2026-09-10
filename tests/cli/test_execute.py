from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

import dotman.cli_interaction as cli
import dotman.cli_emit as cli_emit
from dotman.cli import main
from dotman.sync_session import EditProposal, SetApproval
from tests.engine.test_sync_session import make_engine
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
    target_config: list[str] | None = None,
    path_rules: list[str] | None = None,
) -> None:
    package_root = repo_root / "packages" / package_id
    (package_root / "files" / "config").mkdir(parents=True)
    (repo_root / "profiles").mkdir(parents=True)

    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    (package_root / "files" / "config" / "nested.txt").write_text("repo directory value\n", encoding="utf-8")
    target_lines = [
        f'id = "{package_id}"',
        "",
        "[targets.config]",
        'source = "files/config"',
        f'path = "~/.config/{live_dir_name}"',
    ]
    if target_config:
        target_lines.extend(target_config)
    target_lines.append("")
    if path_rules:
        target_lines.extend(path_rules)
        target_lines.append("")

    (package_root / "package.toml").write_text(
        "\n".join(
            target_lines
            + [
                "[hooks]",
                "guard_push = \"printf 'guard push\\n'\"",
                "pre_push = \"printf 'pre push\\n'\"",
                "post_push = \"printf 'post push\\n'\"",
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

    exit_code = main(["--unattended", "--config", str(config_path), "--json", "push"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    live_path = home / ".config" / "app" / "config.txt"
    assert live_path.read_text(encoding="utf-8") == "repo value\n"
    assert stat.S_IMODE(live_path.stat().st_mode) == 0o600
    assert payload["mode"] == "execute"
    assert payload["operation"] == "push"
    assert payload["packages"][0]["package_id"] == "app"
    assert [step["action"] for step in payload["packages"][0]["steps"]] == [
        "pre_push",
        "create",
        "chmod",
        "post_push",
    ]
    assert payload["guard_skips"] == []


def test_push_directory_target_create_preserves_repo_executable_bit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _write_directory_execution_repo(repo_root)
    repo_path = repo_root / "packages" / "app" / "files" / "config" / "nested.txt"
    repo_path.chmod(0o755)
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    _write_tracked_binding(tmp_path / "state")

    exit_code = main(["--unattended", "--config", str(config_path), "push"])

    assert exit_code == 0
    live_path = home / ".config" / "app" / "nested.txt"
    assert live_path.read_text(encoding="utf-8") == "repo directory value\n"
    assert stat.S_IMODE(live_path.stat().st_mode) == 0o755


def test_push_directory_target_updates_live_file_when_only_repo_executable_bit_changed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _write_directory_execution_repo(repo_root)
    repo_path = repo_root / "packages" / "app" / "files" / "config" / "nested.txt"
    repo_path.chmod(0o755)
    live_path = home / ".config" / "app" / "nested.txt"
    live_path.parent.mkdir(parents=True)
    live_path.write_text("repo directory value\n", encoding="utf-8")
    live_path.chmod(0o644)
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    _write_tracked_binding(tmp_path / "state")

    exit_code = main(["--unattended", "--config", str(config_path), "push"])

    assert exit_code == 0
    assert live_path.read_text(encoding="utf-8") == "repo directory value\n"
    assert stat.S_IMODE(live_path.stat().st_mode) == 0o755


def test_push_directory_target_ignores_non_executable_mode_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _write_directory_execution_repo(repo_root)
    repo_path = repo_root / "packages" / "app" / "files" / "config" / "nested.txt"
    repo_path.chmod(0o600)
    live_path = home / ".config" / "app" / "nested.txt"
    live_path.parent.mkdir(parents=True)
    live_path.write_text("repo directory value\n", encoding="utf-8")
    live_path.chmod(0o644)
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    _write_tracked_binding(tmp_path / "state")

    exit_code = main(["--unattended", "--config", str(config_path), "push"])

    assert exit_code == 0
    assert live_path.read_text(encoding="utf-8") == "repo directory value\n"
    assert stat.S_IMODE(live_path.stat().st_mode) == 0o644


def test_push_directory_target_path_rule_applies_child_chmod_on_create(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _write_directory_execution_repo(
        repo_root,
        path_rules=[
            "[targets.config.path_rules.rule]",
            'pattern = "nested.txt"',
            'chmod = "600"',
        ],
    )
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    _write_tracked_binding(tmp_path / "state")

    exit_code = main(["--unattended", "--config", str(config_path), "push"])

    assert exit_code == 0
    live_path = home / ".config" / "app" / "nested.txt"
    assert live_path.read_text(encoding="utf-8") == "repo directory value\n"
    assert stat.S_IMODE(live_path.stat().st_mode) == 0o600


def test_push_directory_target_path_rule_repairs_child_chmod_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _write_directory_execution_repo(
        repo_root,
        path_rules=[
            "[targets.config.path_rules.rule]",
            'pattern = "nested.txt"',
            'chmod = "600"',
        ],
    )
    live_path = home / ".config" / "app" / "nested.txt"
    live_path.parent.mkdir(parents=True)
    live_path.write_text("repo directory value\n", encoding="utf-8")
    live_path.chmod(0o644)
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    _write_tracked_binding(tmp_path / "state")

    exit_code = main(["--unattended", "--config", str(config_path), "push"])

    assert exit_code == 0
    assert live_path.read_text(encoding="utf-8") == "repo directory value\n"
    assert stat.S_IMODE(live_path.stat().st_mode) == 0o600


def test_push_directory_target_uses_render_for_child_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _write_directory_execution_repo(repo_root, target_config=['render = "sh hooks/render.sh"'])
    package_root = repo_root / "packages" / "app"
    (package_root / "hooks").mkdir()
    (package_root / "hooks" / "render.sh").write_text(
        "#!/bin/sh\nsed 's/^/rendered:/' \"$DOTMAN_SOURCE\"\n",
        encoding="utf-8",
    )
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    _write_tracked_binding(tmp_path / "state")

    exit_code = main(["--unattended", "--config", str(config_path), "push"])

    assert exit_code == 0
    live_path = home / ".config" / "app" / "nested.txt"
    assert live_path.read_text(encoding="utf-8") == "rendered:repo directory value\n"


def test_push_directory_target_path_rule_render_overrides_default_render(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    _write_directory_execution_repo(
        repo_root,
        target_config=['render = "sh hooks/default-render.sh"'],
        path_rules=[
            "[targets.config.path_rules.rule]",
            'pattern = "*.json"',
            'render = "sh hooks/json-render.sh"',
        ],
    )
    package_root = repo_root / "packages" / "app"
    (package_root / "files" / "config" / "data.json").write_text("json value\n", encoding="utf-8")
    (package_root / "hooks").mkdir()
    (package_root / "hooks" / "default-render.sh").write_text(
        "#!/bin/sh\nsed 's/^/default:/' \"$DOTMAN_SOURCE\"\n",
        encoding="utf-8",
    )
    (package_root / "hooks" / "json-render.sh").write_text(
        "#!/bin/sh\nsed 's/^/json:/' \"$DOTMAN_SOURCE\"\n",
        encoding="utf-8",
    )
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    _write_tracked_binding(tmp_path / "state")

    exit_code = main(["--unattended", "--config", str(config_path), "push"])

    assert exit_code == 0
    live_root = home / ".config" / "app"
    assert (live_root / "nested.txt").read_text(encoding="utf-8") == "default:repo directory value\n"
    assert (live_root / "data.json").read_text(encoding="utf-8") == "json:json value\n"


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

    exit_code = main(["--unattended", "--config", str(config_path), "--json", "push", "--dry-run"])

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

    exit_code = main(["--unattended", "--config", str(config_path), "push"])

    assert exit_code == 1
    error_output = capsys.readouterr().err
    assert "symlink" in error_output
    assert str(symlink_target) in error_output



def test_push_cli_unattended_refuses_unsafe_symlink_replacement(
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

    exit_code = main(["--unattended", "--config", str(config_path), "push"])

    assert exit_code == 1
    live_path = live_root / "config.txt"
    assert live_path.is_file()
    assert live_path.is_symlink()
    assert live_path.read_text(encoding="utf-8") == "live value\n"
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

    exit_code = main(["--unattended", "--config", str(config_path), "push"])

    assert exit_code == 1
    error_output = capsys.readouterr().err
    assert "symlink" in error_output
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

    exit_code = main(["--unattended", "--config", str(config_path), "--dir-symlink-mode", "follow", "push"])

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

    exit_code = main(["--unattended", "--config", str(config_path), "push"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "\n:: executing push\n" in output
    assert "packages: 1" in output
    assert "steps: 4" in output
    assert ":: fixture:app@default" in output
    assert "[1/4] pre_push" in output
    assert "[3/4] chmod" in output
    assert "600" in output
    assert "[4/4] post_push" in output
    assert "guard push" not in output
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
    monkeypatch.setattr("dotman.cli.colors_enabled", lambda: True)

    repo_root = tmp_path / "repo"
    _write_basic_execution_repo(repo_root)
    config_path = write_named_manager_config(tmp_path, {"fixture": repo_root})
    _write_tracked_binding(tmp_path / "state")

    exit_code = main(["--unattended", "--config", str(config_path), "push"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "\033[1;32mok\033[0m" in output
    assert "\033[1;32mdone\033[0m" not in output



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

    exit_code = main(["--unattended", "--config", str(config_path), "push", "--run-noop"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert ":: executing push" in output
    assert "packages: 1" in output
    assert "steps: 2" in output
    assert "[1/2] pre_push" in output
    assert "[2/2] post_push" in output
    assert "guard push" not in output
    assert "pre push" in output
    assert "post push" in output
    assert "noop" not in output
    assert "[1/2] create" not in output


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

    exit_code = main(["--unattended", "--config", str(config_path), "--json", "push", "--dry-run", "--run-noop"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["package_entries"][0]["targets"] == []
    assert set(payload["package_entries"][0]["hooks"]) == {"pre_push", "post_push"}


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

    exit_code = main(["--unattended", "--config", str(config_path), "push", "--run-noop"])

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

    exit_code = main(["--unattended", "--config", str(config_path), "--json", "push", "--run-noop"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "execute"
    assert payload["package_entries"] == []
    assert payload["guard_skips"][0]["scope"] == "fixture:app"
    assert payload["guard_skips"][0]["reason"] == "guard push"


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

    exit_code = main(["--unattended", "--config", str(config_path), "--json", "push"])

    assert exit_code == 1
    captured = capsys.readouterr()
    live_path = home / ".config" / "app" / "config.txt"
    assert not live_path.exists()
    assert captured.out == ""
    assert "GuardPlanningError" in captured.err
    assert "guard_push failed with exit 1: guard push failed" in captured.err



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

    exit_code = main(["--unattended", "--config", str(config_path), "push"])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "fixture-a:app" in captured.err
    assert "guard_push failed with exit 1: guard push failed" in captured.err
    assert "fixture-b:other" not in captured.err



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


@pytest.mark.parametrize(
    "source,live,change",
    [(None, b"live", "write"), (b"repo", b"live", "write"), (b"repo", None, "delete")],
)
def test_pull_cli_applies_repository_changes_and_emits_stages(
    tmp_path, monkeypatch, capsys, source, live, change,
):
    make_engine(tmp_path, monkeypatch, [
        ("unit", "both", source, live,
         '[targets.unit.hooks]\npre_pull = "true"\npost_pull = "true"'),
    ])
    tracked = tmp_path / "state/dotman/repos/main/tracked-packages.toml"
    tracked_before = tracked.read_bytes()

    assert main([
        "--config", str(tmp_path / "config.toml"), "--unattended", "--json",
        "pull", "main:app.unit",
    ]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "pull"
    assert payload["mode"] == "execute"
    unit, = payload["sync_units"]
    assert unit["identity"] == "main:app.unit"
    assert unit["result"] == "applied"
    assert unit["primary_source_change"]["kind"] == change
    assert all(step["stage"] == "repository-apply" for step in payload["stages"])
    assert [step["action"] for step in payload["stages"]] == ["pre_pull", "update" if change == "write" else "delete", "post_pull"]
    assert all(step["status"] == "ok" for step in payload["stages"])
    repo_path = tmp_path / "repo/packages/app/unit"
    live_path = tmp_path / "live/unit"
    assert (repo_path.read_bytes() if repo_path.exists() else None) == live
    assert (live_path.read_bytes() if live_path.exists() else None) == live
    assert tracked.read_bytes() == tracked_before


def test_pull_cli_uses_capture_without_launching_configured_editor(tmp_path, monkeypatch, capsys):
    make_engine(tmp_path, monkeypatch, [
        ("unit", "both", b"repo", b"live",
         'capture = "printf captured"\ncompare = {repo = "raw", live = "raw"}\n'
         'editor = {run = "exit 9", io = "pipe"}'),
    ])

    assert main([
        "--config", str(tmp_path / "config.toml"), "--unattended", "--json",
        "pull", "main:app.unit",
    ]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["sync_units"][0]["result"] == "applied"
    assert (tmp_path / "repo/packages/app/unit").read_bytes() == b"captured"
    assert (tmp_path / "live/unit").read_bytes() == b"live"


@pytest.mark.parametrize("capture_fails", [False, True])
def test_pull_cli_editor_is_explicit_deck_action_with_approval(
    tmp_path, monkeypatch, capsys, capture_fails,
):
    make_engine(tmp_path, monkeypatch, [
        ("unit", "both", b"repo", b"live",
         ('capture = "false"\ncompare = {repo = "raw", live = "raw"}\n' if capture_fails else "")
         + 'editor = {run = "printf edited > \\"$DOTMAN_SOURCE\\"", io = "pipe"}'),
    ])
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    def edit_and_approve(session, *, use_color):
        row, = session.view.rows
        assert row.approved is not capture_fails
        assert bool(row.diagnostics) is capture_fails
        view = session.view
        edited = session.dispatch(EditProposal(view.session_id, view.revision, row.row_id))
        assert edited.result.status == "saved"
        assert (tmp_path / "repo/packages/app/unit").read_bytes() == b"repo"
        view = session.view
        session.dispatch(SetApproval(view.session_id, view.revision, row.row_id, True))
        return True

    monkeypatch.setattr("dotman.sync_deck.run_command_deck", edit_and_approve)

    assert main(["--config", str(tmp_path / "config.toml"), "pull", "main:app.unit"]) == 0
    assert (tmp_path / "repo/packages/app/unit").read_bytes() == b"edited"
    assert (tmp_path / "live/unit").read_bytes() == b"live"
    output = capsys.readouterr().out
    assert "[approved] main:app.unit" in output
    assert "applied" in output


def test_pull_cli_run_noop_executes_only_pull_hooks(tmp_path, monkeypatch, capsys):
    make_engine(tmp_path, monkeypatch, [("unit", "both", b"same", b"same", "")])
    package = tmp_path / "repo/packages/app/package.toml"
    with package.open("a") as stream:
        stream.write(
            '\n[hooks]\npre_pull = "true"\npost_pull = "true"\npre_push = "exit 9"\n'
        )

    assert main([
        "--config", str(tmp_path / "config.toml"), "--unattended", "--json",
        "pull", "--run-noop", "main:app",
    ]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert [step["action"] for step in payload["stages"]] == ["pre_pull", "post_pull"], payload
    assert all(step["status"] == "ok" for step in payload["stages"])
    assert (tmp_path / "repo/packages/app/unit").read_bytes() == b"same"
