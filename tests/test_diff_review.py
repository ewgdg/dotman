from __future__ import annotations

import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from dotman.diff_review import (
    DEFAULT_REVIEW_PAGER,
    ReviewItem,
    _load_item_bytes,
    _load_item_mode,
    _review_display_path,
    _review_item_bytes,
    _select_review_pager_command,
    build_review_items,
    display_review_path,
    edit_status,
    run_review_item_diff,
    run_review_item_edit,
)
from dotman.models import DirectoryPlanItem, HookCommandSpec, HookPlan, UiConfig, TargetPlan
from dotman.ui_context import ui_config_scope
from tests.helpers import make_package_plan


def test_build_review_items_adds_probe_targets_with_related_hooks() -> None:
    plan = make_package_plan(
        operation="push",
        repo_name="sandbox",
        package_id="app",
        requested_profile="default",
        variables={},
        hooks={
            "pre_push": [
                HookPlan(
                    package_id="app",
                    hook_name="pre_push",
                    command="echo package pre",
                    cwd=Path("/repo/app"),
                ),
                HookPlan(
                    package_id="app",
                    target_name="version",
                    scope_kind="target",
                    hook_name="pre_push",
                    command="echo target pre",
                    cwd=Path("/repo/app"),
                ),
            ]
        },
        target_plans=[
            TargetPlan(
                package_id="app",
                target_name="version",
                repo_path=Path("/repo/app"),
                live_path=Path("/repo/app"),
                action="probe",
                target_kind="probe",
                projection_kind="probe",
                probe_command="exit 0",
            )
        ],
    )

    review_items = build_review_items([plan], operation="push")

    assert len(review_items) == 1
    assert review_items[0].action == "install"
    assert review_items[0].is_probe is True
    assert review_items[0].source_path == ""
    assert review_items[0].destination_path == ""
    assert review_items[0].probe_command == "exit 0"
    assert review_items[0].hook_command_summaries == ("pre_push: echo target pre",)


def test_build_review_items_for_pull_uses_planning_view_bytes(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo-file"
    live_path = tmp_path / "live-file"
    repo_path.write_text("raw repo\n", encoding="utf-8")
    live_path.write_text("raw live\n", encoding="utf-8")

    plan = make_package_plan(
        operation="pull",
        repo_name="example",
        package_id="git",
        requested_profile="basic",
        variables={},
        hooks={},
        target_plans=[
            TargetPlan(
                package_id="git",
                target_name="gitconfig",
                repo_path=repo_path,
                live_path=live_path,
                action="update",
                target_kind="file",
                projection_kind="raw",
                review_before_bytes=b"repo planning view\n",
                review_after_bytes=b"live planning view\n",
            )
        ],
    )

    review_items = build_review_items([plan], operation="pull")

    assert len(review_items) == 1
    assert review_items[0].before_bytes == b"repo planning view\n"
    assert review_items[0].after_bytes == b"live planning view\n"
    assert review_items[0].source_path == str(live_path)
    assert review_items[0].destination_path == str(repo_path)


def test_build_review_items_for_pull_directory_uses_planning_view_bytes(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo-file"
    live_path = tmp_path / "live-file"
    repo_path.write_text("raw repo\n", encoding="utf-8")
    live_path.write_text("raw live with secret\n", encoding="utf-8")

    plan = make_package_plan(
        operation="pull",
        repo_name="example",
        package_id="config",
        requested_profile="basic",
        variables={},
        hooks={},
        target_plans=[
            TargetPlan(
                package_id="config",
                target_name="app",
                repo_path=repo_path.parent,
                live_path=live_path.parent,
                action="update",
                target_kind="directory",
                projection_kind="directory",
                directory_items=(
                    DirectoryPlanItem(
                        relative_path="data.json",
                        action="update",
                        repo_path=repo_path,
                        live_path=live_path,
                        review_before_bytes=b"repo planning view\n",
                        review_after_bytes=b"live planning view without secret\n",
                    ),
                ),
            )
        ],
    )

    review_items = build_review_items([plan], operation="pull")

    assert len(review_items) == 1
    assert review_items[0].before_bytes == b"repo planning view\n"
    assert review_items[0].after_bytes == b"live planning view without secret\n"



def test_build_review_items_for_push_directory_reuses_planned_desired_bytes(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo-template"
    live_path = tmp_path / "live-file"
    repo_path.write_text("raw template {{ value }}\n", encoding="utf-8")
    live_path.write_text("old rendered value\n", encoding="utf-8")

    plan = make_package_plan(
        operation="push",
        repo_name="example",
        package_id="scripts",
        requested_profile="basic",
        variables={},
        hooks={},
        target_plans=[
            TargetPlan(
                package_id="scripts",
                target_name="bin",
                repo_path=repo_path.parent,
                live_path=live_path.parent,
                action="update",
                target_kind="directory",
                projection_kind="raw",
                directory_items=(
                    DirectoryPlanItem(
                        relative_path="tool.sh",
                        action="update",
                        repo_path=repo_path,
                        live_path=live_path,
                        desired_bytes=b"new rendered value\n",
                    ),
                ),
            )
        ],
    )

    review_items = build_review_items([plan], operation="push")

    assert len(review_items) == 1
    assert review_items[0].before_bytes == b"old rendered value\n"
    assert review_items[0].after_bytes == b"new rendered value\n"


def test_build_review_items_for_pull_directory_create_lazily_loads_capture_view(tmp_path: Path) -> None:
    live_path = tmp_path / "live-file"
    live_path.write_text("raw live\n", encoding="utf-8")
    repo_path = tmp_path / "repo-file"
    plan = make_package_plan(
        operation="pull",
        repo_name="example",
        package_id="scripts",
        requested_profile="basic",
        variables={},
        hooks={},
        target_plans=[
            TargetPlan(
                package_id="scripts",
                target_name="bin",
                repo_path=repo_path.parent,
                live_path=live_path.parent,
                action="update",
                target_kind="directory",
                projection_kind="raw",
                command_cwd=tmp_path,
                command_env={},
                directory_items=(
                    DirectoryPlanItem(
                        relative_path="tool.sh",
                        action="create",
                        repo_path=repo_path,
                        live_path=live_path,
                        capture_command="printf 'captured live\\n'",
                        pull_view_live="capture",
                    ),
                ),
            )
        ],
    )

    review_item = build_review_items([plan], operation="pull")[0]

    assert review_item.before_bytes == b""
    assert review_item.after_bytes is None
    assert review_item.after_bytes_loader is not None
    assert _review_item_bytes(review_item, before=False) == b"captured live\n"


def test_build_review_items_for_pull_directory_delete_lazily_loads_render_view(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo-file"
    repo_path.write_text("raw repo\n", encoding="utf-8")
    live_path = tmp_path / "live-file"
    plan = make_package_plan(
        operation="pull",
        repo_name="example",
        package_id="scripts",
        requested_profile="basic",
        variables={},
        hooks={},
        target_plans=[
            TargetPlan(
                package_id="scripts",
                target_name="bin",
                repo_path=repo_path.parent,
                live_path=live_path.parent,
                action="update",
                target_kind="directory",
                projection_kind="raw",
                command_cwd=tmp_path,
                command_env={},
                directory_items=(
                    DirectoryPlanItem(
                        relative_path="tool.sh",
                        action="delete",
                        repo_path=repo_path,
                        live_path=live_path,
                        render_command="printf 'rendered repo\\n'",
                        pull_view_repo="render",
                    ),
                ),
            )
        ],
    )

    review_item = build_review_items([plan], operation="pull")[0]

    assert review_item.before_bytes is None
    assert review_item.before_bytes_loader is not None
    assert review_item.after_bytes == b""
    assert _review_item_bytes(review_item, before=True) == b"rendered repo\n"


def test_pull_directory_lazy_capture_view_uses_sudo_when_live_read_needs_it(monkeypatch, tmp_path: Path) -> None:
    live_path = tmp_path / "live-file"
    live_path.write_text("raw live\n", encoding="utf-8")
    repo_path = tmp_path / "repo-file"
    plan = make_package_plan(
        operation="pull",
        repo_name="example",
        package_id="scripts",
        requested_profile="basic",
        variables={},
        hooks={},
        target_plans=[
            TargetPlan(
                package_id="scripts",
                target_name="bin",
                repo_path=repo_path.parent,
                live_path=live_path.parent,
                action="update",
                target_kind="directory",
                projection_kind="raw",
                command_cwd=tmp_path,
                command_env={},
                directory_items=(
                    DirectoryPlanItem(
                        relative_path="tool.sh",
                        action="create",
                        repo_path=repo_path,
                        live_path=live_path,
                        capture_command="capture-cmd",
                        pull_view_live="capture",
                    ),
                ),
            )
        ],
    )
    recorded: dict[str, object] = {}

    monkeypatch.setattr("dotman.diff_review.needs_sudo_for_read", lambda path: path == live_path)

    def fake_sudo_prefix(command: str) -> str:
        recorded["unwrapped_command"] = command
        return f"sudo-wrapper {command}"

    def fake_run(command: str, **kwargs):
        recorded["command"] = command
        recorded["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout=b"captured live\n", stderr=b"")

    monkeypatch.setattr("dotman.diff_review.sudo_prefix_command", fake_sudo_prefix)
    monkeypatch.setattr("dotman.diff_review.subprocess.run", fake_run)

    review_item = build_review_items([plan], operation="pull")[0]

    assert _review_item_bytes(review_item, before=False) == b"captured live\n"
    assert recorded["unwrapped_command"] == "capture-cmd"
    assert recorded["command"] == "sudo-wrapper capture-cmd"


def test_push_directory_raw_live_review_bytes_use_privileged_file_access(monkeypatch, tmp_path: Path) -> None:
    live_path = tmp_path / "live-file"
    live_path.write_text("raw live\n", encoding="utf-8")
    repo_path = tmp_path / "missing-repo-file"
    plan = make_package_plan(
        operation="push",
        repo_name="example",
        package_id="scripts",
        requested_profile="basic",
        variables={},
        hooks={},
        target_plans=[
            TargetPlan(
                package_id="scripts",
                target_name="bin",
                repo_path=repo_path.parent,
                live_path=live_path.parent,
                action="update",
                target_kind="directory",
                projection_kind="raw",
                directory_items=(
                    DirectoryPlanItem(
                        relative_path="tool.sh",
                        action="delete",
                        repo_path=repo_path,
                        live_path=live_path,
                    ),
                ),
            )
        ],
    )
    calls: list[Path] = []

    def fake_read_bytes(path: Path) -> bytes:
        calls.append(path)
        if path == repo_path:
            raise FileNotFoundError(path)
        return b"privileged live\n"

    monkeypatch.setattr("dotman.diff_review.read_bytes", fake_read_bytes)

    review_item = build_review_items([plan], operation="push")[0]

    assert review_item.before_bytes == b"privileged live\n"
    assert calls == [live_path, repo_path]


def test_load_item_bytes_attempts_privileged_read_when_exists_is_false(monkeypatch, tmp_path: Path) -> None:
    live_path = tmp_path / "protected-live"
    repo_path = tmp_path / "missing-repo"
    original_exists = Path.exists
    calls: list[Path] = []

    def fake_exists(path: Path, *args, **kwargs) -> bool:
        if path == live_path:
            return False
        return original_exists(path, *args, **kwargs)

    def fake_read_bytes(path: Path) -> bytes:
        calls.append(path)
        return b"privileged live\n"

    monkeypatch.setattr(Path, "exists", fake_exists)
    monkeypatch.setattr("dotman.diff_review.read_bytes", fake_read_bytes)

    assert _load_item_bytes(repo_path=repo_path, live_path=live_path, operation="push", before=True) == b"privileged live\n"
    assert calls == [live_path]


def test_load_item_bytes_returns_empty_for_missing_side(monkeypatch, tmp_path: Path) -> None:
    live_path = tmp_path / "missing-live"
    repo_path = tmp_path / "repo"

    def fake_read_bytes(path: Path) -> bytes:
        raise FileNotFoundError(path)

    monkeypatch.setattr("dotman.diff_review.read_bytes", fake_read_bytes)

    assert _load_item_bytes(repo_path=repo_path, live_path=live_path, operation="push", before=True) == b""


def test_load_item_mode_ignores_permission_denied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    live_path = tmp_path / "protected-live"
    repo_path = tmp_path / "repo"

    def fake_stat(path: Path, *args, **kwargs):
        if path == live_path:
            raise PermissionError("permission denied")
        return original_stat(path, *args, **kwargs)

    original_stat = Path.stat
    monkeypatch.setattr(Path, "stat", fake_stat)

    assert _load_item_mode(repo_path=repo_path, live_path=live_path, operation="push", before=True) is None


def test_build_review_items_for_push_directory_includes_mode_metadata(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo-file"
    live_path = tmp_path / "live-file"
    repo_path.write_text("same\n", encoding="utf-8")
    live_path.write_text("same\n", encoding="utf-8")
    repo_path.chmod(0o755)
    live_path.chmod(0o644)

    plan = make_package_plan(
        operation="push",
        repo_name="example",
        package_id="scripts",
        requested_profile="basic",
        variables={},
        hooks={},
        target_plans=[
            TargetPlan(
                package_id="scripts",
                target_name="bin",
                repo_path=repo_path.parent,
                live_path=live_path.parent,
                action="update",
                target_kind="directory",
                projection_kind="raw",
                directory_items=(
                    DirectoryPlanItem(
                        relative_path="tool.sh",
                        action="update",
                        repo_path=repo_path,
                        live_path=live_path,
                    ),
                ),
            )
        ],
    )

    review_items = build_review_items([plan], operation="push")

    assert len(review_items) == 1
    assert review_items[0].before_bytes == b"same\n"
    assert review_items[0].after_bytes == b"same\n"
    assert review_items[0].before_mode == 0o644
    assert review_items[0].after_mode == 0o755


def test_build_review_items_for_pull_directory_includes_mode_metadata(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo-file"
    live_path = tmp_path / "live-file"
    repo_path.write_text("same\n", encoding="utf-8")
    live_path.write_text("same\n", encoding="utf-8")
    repo_path.chmod(0o644)
    live_path.chmod(0o755)

    plan = make_package_plan(
        operation="pull",
        repo_name="example",
        package_id="scripts",
        requested_profile="basic",
        variables={},
        hooks={},
        target_plans=[
            TargetPlan(
                package_id="scripts",
                target_name="bin",
                repo_path=repo_path.parent,
                live_path=live_path.parent,
                action="update",
                target_kind="directory",
                projection_kind="raw",
                directory_items=(
                    DirectoryPlanItem(
                        relative_path="tool.sh",
                        action="update",
                        repo_path=repo_path,
                        live_path=live_path,
                    ),
                ),
            )
        ],
    )

    review_items = build_review_items([plan], operation="pull")

    assert len(review_items) == 1
    assert review_items[0].before_bytes == b"same\n"
    assert review_items[0].after_bytes == b"same\n"
    assert review_items[0].before_mode == 0o644
    assert review_items[0].after_mode == 0o755


def test_run_review_item_diff_prints_probe_summary(capsys) -> None:
    review_item = ReviewItem(
        selection_label="example:app@basic",
        package_id="app",
        target_name="version",
        action="install",
        operation="push",
        repo_path=Path("/repo/app"),
        live_path=Path("/repo/app"),
        source_path="",
        destination_path="",
        is_probe=True,
        hook_command_summaries=("pre_push: echo pre", "post_push: echo post"),
    )

    run_review_item_diff(review_item)

    assert capsys.readouterr().out == (
        "[pre_push] echo pre\n"
        "[post_push] echo post\n"
    )


def test_run_review_item_diff_invokes_git_diff(monkeypatch) -> None:
    repo_path = Path.home() / ".config" / "repo-file"
    live_path = Path.home() / ".local" / "share" / "live-file"
    review_item = ReviewItem(
        selection_label="example:git@basic",
        package_id="git",
        target_name="gitconfig",
        action="update",
        operation="push",
        repo_path=repo_path,
        live_path=live_path,
        source_path="/repo-file",
        destination_path="/live-file",
        before_bytes=b"before\n",
        after_bytes=b"after\n",
    )
    recorded: dict[str, object] = {}

    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    monkeypatch.setattr("dotman.diff_review._select_review_pager_command", lambda: None)

    def fake_run(command: list[str], check: bool, env=None, cwd=None):
        recorded["command"] = command
        recorded["check"] = check
        recorded["env"] = env
        recorded["cwd"] = cwd
        assert cwd is not None
        assert Path(cwd, "live", "~", "...", "share", "live-file").read_text(encoding="utf-8") == "before\n"
        assert Path(cwd, "repo", "~", ".config", "repo-file").read_text(encoding="utf-8") == "after\n"
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr("dotman.diff_review.subprocess.run", fake_run)

    run_review_item_diff(review_item)

    assert recorded["check"] is False
    assert recorded["env"] is None
    assert recorded["command"][:5] == ["git", "diff", "--no-index", "--color=auto", "--"]
    assert recorded["command"][5:] == ["live/~/.../share/live-file", "repo/~/.config/repo-file"]


def test_run_review_item_diff_materializes_executable_bit_change(monkeypatch, capsys) -> None:
    repo_path = Path.home() / ".config" / "repo-file"
    live_path = Path.home() / ".local" / "share" / "live-file"
    review_item = ReviewItem(
        selection_label="example:scripts@basic",
        package_id="scripts",
        target_name="bin",
        action="update",
        operation="push",
        repo_path=repo_path,
        live_path=live_path,
        source_path="/repo-file",
        destination_path="/live-file",
        before_bytes=b"same\n",
        after_bytes=b"same\n",
        before_mode=0o644,
        after_mode=0o755,
    )

    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    monkeypatch.setattr("dotman.diff_review._select_review_pager_command", lambda: None)

    def fake_run(command: list[str], check: bool, env=None, cwd=None):
        assert cwd is not None
        assert stat.S_IMODE(Path(cwd, "live", "~", "...", "share", "live-file").stat().st_mode) == 0o644
        assert stat.S_IMODE(Path(cwd, "repo", "~", ".config", "repo-file").stat().st_mode) == 0o755
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr("dotman.diff_review.subprocess.run", fake_run)

    run_review_item_diff(review_item)

    assert "file mode:" not in capsys.readouterr().out


def test_run_review_item_diff_materializes_absolute_paths_under_temp_root(monkeypatch) -> None:
    repo_path = Path("/etc/sddm.conf.d/kde_settings.conf")
    live_path = Path("/var/lib/sddm.conf.d/kde_settings.conf")
    review_item = ReviewItem(
        selection_label="main:sddm@basic",
        package_id="sddm",
        target_name="kde_settings.conf",
        action="update",
        operation="push",
        repo_path=repo_path,
        live_path=live_path,
        source_path=str(repo_path),
        destination_path=str(live_path),
        before_bytes=b"before\n",
        after_bytes=b"after\n",
    )
    recorded: dict[str, object] = {}

    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    monkeypatch.setattr("dotman.diff_review._select_review_pager_command", lambda: None)

    def fake_run(command: list[str], check: bool, env=None, cwd=None):
        recorded["command"] = command
        recorded["cwd"] = cwd
        assert cwd is not None
        assert Path(cwd, "live", "var", "...", "sddm.conf.d", "kde_settings.conf").read_text(encoding="utf-8") == "before\n"
        assert Path(cwd, "repo", "etc", "sddm.conf.d", "kde_settings.conf").read_text(encoding="utf-8") == "after\n"
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr("dotman.diff_review.subprocess.run", fake_run)

    run_review_item_diff(review_item)

    assert recorded["command"][5:] == ["live/var/.../sddm.conf.d/kde_settings.conf", "repo/etc/sddm.conf.d/kde_settings.conf"]


def test_run_review_item_diff_uses_repo_and_live_labels_for_pull(monkeypatch) -> None:
    repo_path = Path.home() / ".gitconfig"
    live_path = Path.home() / ".config" / "git" / "config"
    review_item = ReviewItem(
        selection_label="example:git@basic",
        package_id="git",
        target_name="gitconfig",
        action="update",
        operation="pull",
        repo_path=repo_path,
        live_path=live_path,
        source_path="/live-file",
        destination_path="/repo-file",
        before_bytes=b"repo\n",
        after_bytes=b"live\n",
    )
    recorded: dict[str, object] = {}

    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    monkeypatch.setattr("dotman.diff_review._select_review_pager_command", lambda: None)

    def fake_run(command: list[str], check: bool, env=None, cwd=None):
        recorded["command"] = command
        recorded["cwd"] = cwd
        assert cwd is not None
        assert Path(cwd, "repo", "~", ".gitconfig").read_text(encoding="utf-8") == "repo\n"
        assert Path(cwd, "live", "~", "...", "git", "config").read_text(encoding="utf-8") == "live\n"
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr("dotman.diff_review.subprocess.run", fake_run)

    run_review_item_diff(review_item)

    assert recorded["command"][5:] == ["repo/~/.gitconfig", "live/~/.../git/config"]


def test_run_review_item_diff_uses_explicit_pager_when_stdout_is_tty(monkeypatch) -> None:
    repo_path = Path.home() / ".config" / "repo-file"
    live_path = Path.home() / ".local" / "share" / "live-file"
    review_item = ReviewItem(
        selection_label="example:git@basic",
        package_id="git",
        target_name="gitconfig",
        action="update",
        operation="push",
        repo_path=repo_path,
        live_path=live_path,
        source_path="/repo-file",
        destination_path="/live-file",
        before_bytes=b"before\n",
        after_bytes=b"after\n",
    )
    recorded: dict[str, object] = {}

    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("dotman.diff_review._select_review_pager_command", lambda: DEFAULT_REVIEW_PAGER)

    def fake_run(command: list[str], check: bool, env=None, cwd=None):
        recorded["command"] = command
        recorded["check"] = check
        recorded["env"] = env
        recorded["cwd"] = cwd
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr("dotman.diff_review.subprocess.run", fake_run)

    run_review_item_diff(review_item)

    assert recorded["check"] is False
    assert recorded["command"][:6] == ["git", "--paginate", "diff", "--no-index", "--color=auto", "--"]
    assert recorded["command"][6:] == ["live/~/.../share/live-file", "repo/~/.config/repo-file"]
    assert recorded["env"] is not None
    assert recorded["env"]["GIT_PAGER"] == DEFAULT_REVIEW_PAGER


def test_review_display_path_uses_tilde_for_home_prefix() -> None:
    assert _review_display_path(Path.home() / ".config" / "nvim" / "init.lua") == Path("~/.../nvim/init.lua")


def test_review_display_path_compacts_long_home_relative_path() -> None:
    assert _review_display_path(Path.home() / ".local" / "share" / "nvim" / "init.lua") == Path("~/.../nvim/init.lua")


def test_review_display_path_uses_configured_tail_segments() -> None:
    assert _review_display_path(Path.home() / ".local" / "share" / "nvim" / "init.lua", tail_segments=3) == Path("~/.../share/nvim/init.lua")


def test_review_display_path_uses_ui_context_tail_segments() -> None:
    with ui_config_scope(UiConfig(compact_path_tail_segments=3)):
        assert _review_display_path(Path.home() / ".local" / "share" / "nvim" / "init.lua") == Path("~/.../share/nvim/init.lua")


def test_review_display_path_rejects_invalid_tail_segments() -> None:
    with pytest.raises(ValueError, match="tail segments"):
        _review_display_path(Path.home() / ".local" / "share" / "nvim" / "init.lua", tail_segments=0)


def test_review_display_path_keeps_absolute_path_with_root_prefix() -> None:
    assert _review_display_path(Path("/etc/gitconfig")) == Path("/etc/gitconfig")


def test_review_display_path_keeps_short_absolute_system_path() -> None:
    assert _review_display_path(Path("/etc/sddm.conf.d/kde_settings.conf")) == Path("/etc/sddm.conf.d/kde_settings.conf")


def test_review_display_path_compacts_long_absolute_path() -> None:
    assert _review_display_path(Path("/etc/xdg/nvim/init.lua")) == Path("/etc/.../nvim/init.lua")


def test_display_review_path_can_disable_compaction_and_home_collapse() -> None:
    full_path = Path.home() / ".config" / "nvim" / "init.lua"

    assert display_review_path(full_path, compact=False) == str(full_path)


def test_select_review_pager_command_uses_git_pager_before_git_config_and_pager(monkeypatch) -> None:
    monkeypatch.setenv("GIT_PAGER", "delta")
    monkeypatch.setenv("PAGER", "less")
    monkeypatch.setattr("dotman.diff_review._git_configured_pager_command", lambda: "diff-so-fancy")

    assert _select_review_pager_command() == "delta"


def test_select_review_pager_command_uses_git_config_before_pager_env(monkeypatch) -> None:
    monkeypatch.delenv("GIT_PAGER", raising=False)
    monkeypatch.setenv("PAGER", "less")
    monkeypatch.setattr("dotman.diff_review._git_configured_pager_command", lambda: "delta")

    assert _select_review_pager_command() == "delta"


def test_select_review_pager_command_uses_pager_env_after_git_config(monkeypatch) -> None:
    monkeypatch.delenv("GIT_PAGER", raising=False)
    monkeypatch.setenv("PAGER", "less")
    monkeypatch.setattr("dotman.diff_review._git_configured_pager_command", lambda: None)

    assert _select_review_pager_command() == "less"


def test_select_review_pager_command_treats_pager_cat_as_disabled(monkeypatch) -> None:
    monkeypatch.delenv("GIT_PAGER", raising=False)
    monkeypatch.setenv("PAGER", "cat")
    monkeypatch.setattr("dotman.diff_review._git_configured_pager_command", lambda: None)
    monkeypatch.setattr("dotman.diff_review.shutil.which", lambda name: "/usr/bin/less" if name == "less" else None)

    assert _select_review_pager_command() is None


def test_select_review_pager_command_treats_git_pager_cat_as_disabled(monkeypatch) -> None:
    monkeypatch.setenv("GIT_PAGER", "cat")
    monkeypatch.setattr("dotman.diff_review.shutil.which", lambda name: "/usr/bin/less" if name == "less" else None)

    assert _select_review_pager_command() is None


def test_run_review_item_edit_prefers_pull_reconcile(monkeypatch, tmp_path: Path) -> None:
    review_item = ReviewItem(
        selection_label="example:nvim@basic",
        package_id="nvim",
        target_name="init_lua",
        action="update",
        operation="pull",
        repo_path=tmp_path / "repo-file",
        live_path=tmp_path / "live-file",
        source_path="/live-file",
        destination_path="/repo-file",
        before_bytes=b"repo planning view\n",
        after_bytes=b"live planning view\n",
        reconcile=HookCommandSpec(run="sh hooks/reconcile.sh"),
        command_cwd=tmp_path,
        command_env={
            "DOTMAN_REPO_PATH": str(tmp_path / "repo-file"),
            "DOTMAN_LIVE_PATH": str(tmp_path / "live-file"),
            "DOTMAN_TARGET_NAME": "init_lua",
        },
    )
    recorded: dict[str, object] = {}

    def fake_run(command: str, check: bool, shell: bool, cwd: Path | None, env: dict[str, str] | None):
        recorded["command"] = command
        recorded["check"] = check
        recorded["shell"] = shell
        recorded["cwd"] = cwd
        recorded["env"] = env
        assert env is not None
        assert Path(env["DOTMAN_REVIEW_REPO_PATH"]).read_text(encoding="utf-8") == "repo planning view\n"
        assert Path(env["DOTMAN_REVIEW_LIVE_PATH"]).read_text(encoding="utf-8") == "live planning view\n"
        assert Path(env["DOTMAN_REVIEW_REPO_PATH"]).stat().st_mode & 0o222 == 0
        assert Path(env["DOTMAN_REVIEW_LIVE_PATH"]).stat().st_mode & 0o222 == 0
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("dotman.diff_review.subprocess.run", fake_run)

    exit_code = run_review_item_edit(review_item)

    assert exit_code == 0
    assert recorded["command"] == "sh hooks/reconcile.sh"
    assert recorded["check"] is False
    assert recorded["shell"] is True
    assert recorded["cwd"] == tmp_path
    assert recorded["env"] is not None
    assert recorded["env"]["DOTMAN_REPO_PATH"] == str(tmp_path / "repo-file")
    assert recorded["env"]["DOTMAN_LIVE_PATH"] == str(tmp_path / "live-file")
    assert recorded["env"]["DOTMAN_TARGET_NAME"] == "init_lua"
    assert recorded["env"]["PATH"] == os.environ["PATH"]


def test_run_review_item_edit_runs_builtin_jinja_reconcile(monkeypatch, tmp_path: Path) -> None:
    repo_path = tmp_path / "repo-file"
    live_path = tmp_path / "live-file"
    repo_path.write_text("{% include 'shared.txt' %}\n", encoding="utf-8")
    live_path.write_text("raw live\n", encoding="utf-8")
    (tmp_path / "shared.txt").write_text("shared\n", encoding="utf-8")
    review_item = ReviewItem(
        selection_label="example:nvim@basic",
        package_id="nvim",
        target_name="init_lua",
        action="update",
        operation="pull",
        repo_path=repo_path,
        live_path=live_path,
        source_path="/live-file",
        destination_path="/repo-file",
        before_bytes=b"repo planning view\n",
        after_bytes=b"capture live planning view\n",
        reconcile=HookCommandSpec(run="jinja", io="tty"),
        command_env={
            "DOTMAN_REPO_PATH": str(repo_path),
            "DOTMAN_LIVE_PATH": str(live_path),
        },
    )
    recorded: dict[str, object] = {}

    def fake_run_jinja_reconcile(
        *,
        repo_path: str,
        live_path: str,
        review_repo_path: str | None = None,
        review_live_path: str | None = None,
        editor: str | None = None,
    ) -> int:
        recorded["repo_path"] = repo_path
        recorded["live_path"] = live_path
        recorded["review_repo_path"] = review_repo_path
        recorded["review_live_path"] = review_live_path
        recorded["editor"] = editor
        assert review_repo_path is not None
        assert review_live_path is not None
        assert Path(review_repo_path).read_text(encoding="utf-8") == "repo planning view\n"
        assert Path(review_live_path).read_text(encoding="utf-8") == "capture live planning view\n"
        return 0

    monkeypatch.setattr("dotman.diff_review.run_jinja_reconcile", fake_run_jinja_reconcile)

    exit_code = run_review_item_edit(review_item)

    assert exit_code == 0
    assert recorded["repo_path"] == str(repo_path)
    assert recorded["live_path"] == str(live_path)
    assert recorded["editor"] is None



def test_run_review_item_edit_uses_planning_views_for_plain_pull_editor(monkeypatch, tmp_path: Path) -> None:
    repo_path = tmp_path / "repo-file"
    live_path = tmp_path / "live-file"
    repo_path.write_text("raw repo\n", encoding="utf-8")
    live_path.write_text("raw live\n", encoding="utf-8")
    review_item = ReviewItem(
        selection_label="example:nvim@basic",
        package_id="nvim",
        target_name="init_lua",
        action="update",
        operation="pull",
        repo_path=repo_path,
        live_path=live_path,
        source_path="/live-file",
        destination_path="/repo-file",
        before_bytes=b"repo planning view\n",
        after_bytes=b"capture live planning view\n",
    )
    recorded: dict[str, object] = {}

    def fake_run_basic_reconcile(
        *,
        repo_path: str,
        live_path: str,
        additional_sources: list[str],
        review_repo_path: str | None = None,
        review_live_path: str | None = None,
        editor: str | None = None,
        assume_yes: bool = False,
    ) -> int:
        recorded["repo_path"] = repo_path
        recorded["live_path"] = live_path
        recorded["additional_sources"] = additional_sources
        recorded["review_repo_path"] = review_repo_path
        recorded["review_live_path"] = review_live_path
        recorded["editor"] = editor
        assert review_repo_path is not None
        assert review_live_path is not None
        assert Path(review_repo_path).read_text(encoding="utf-8") == "repo planning view\n"
        assert Path(review_live_path).read_text(encoding="utf-8") == "capture live planning view\n"
        return 0

    monkeypatch.setattr("dotman.diff_review.run_basic_reconcile", fake_run_basic_reconcile)

    exit_code = run_review_item_edit(review_item)

    assert exit_code == 0
    assert recorded["repo_path"] == str(repo_path)
    assert recorded["live_path"] == str(live_path)
    assert recorded["additional_sources"] == []
    assert recorded["editor"] is None


def test_edit_status_keeps_reconcile_pull_only(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo-file"
    live_path = tmp_path / "live-file"
    repo_path.write_text("repo\n", encoding="utf-8")
    live_path.write_text("live\n", encoding="utf-8")

    push_item = ReviewItem(
        selection_label="example:nvim@basic",
        package_id="nvim",
        target_name="init_lua",
        action="update",
        operation="push",
        repo_path=repo_path,
        live_path=live_path,
        source_path=str(repo_path),
        destination_path=str(live_path),
        reconcile=HookCommandSpec(run="sh hooks/reconcile.sh"),
    )
    pull_item = ReviewItem(
        selection_label="example:nvim@basic",
        package_id="nvim",
        target_name="init_lua",
        action="update",
        operation="pull",
        repo_path=repo_path,
        live_path=live_path,
        source_path=str(live_path),
        destination_path=str(repo_path),
        reconcile=HookCommandSpec(run="sh hooks/reconcile.sh"),
    )

    assert edit_status(push_item) == "editor"
    assert edit_status(pull_item) == "reconcile"
