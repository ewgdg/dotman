from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from dotman.command_runtime import (
    CommandResult,
    MemoryCommandRuntime,
    command_runtime_session,
)
from dotman.diff_review import (
    DEFAULT_REVIEW_PAGER,
    ReviewItem,
    _load_item_bytes,
    _load_item_mode,
    _review_display_path,
    _select_review_pager_command,
    build_review_items,
    display_review_path,
    run_review_item_diff,
    run_review_item_edit,
)
from dotman.models import DirectoryPlanItem, EditorSpec, HookPlan, UiConfig, TargetPlan
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

    def fake_run(request):
        recorded["request"] = request
        assert request.cwd is not None
        assert Path(request.cwd, "live", "~", "...", "share", "live-file").read_text(encoding="utf-8") == "before\n"
        assert Path(request.cwd, "repo", "~", ".config", "repo-file").read_text(encoding="utf-8") == "after\n"
        return CommandResult(exit_code=1)

    runtime = MemoryCommandRuntime([fake_run])
    with command_runtime_session(runtime):
        run_review_item_diff(review_item)

    request = recorded["request"]
    assert request.env == {}
    assert request.command.arguments[:5] == ("git", "diff", "--no-index", "--color=auto", "--")
    assert request.command.arguments[5:] == ("live/~/.../share/live-file", "repo/~/.config/repo-file")
    assert request.io == "tty"


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

    def fake_run(request):
        assert request.cwd is not None
        assert stat.S_IMODE(Path(request.cwd, "live", "~", "...", "share", "live-file").stat().st_mode) == 0o644
        assert stat.S_IMODE(Path(request.cwd, "repo", "~", ".config", "repo-file").stat().st_mode) == 0o755
        return CommandResult(exit_code=1)

    with command_runtime_session(MemoryCommandRuntime([fake_run])):
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

    def fake_run(request):
        recorded["request"] = request
        assert request.cwd is not None
        assert Path(request.cwd, "live", "var", "...", "sddm.conf.d", "kde_settings.conf").read_text(encoding="utf-8") == "before\n"
        assert Path(request.cwd, "repo", "etc", "sddm.conf.d", "kde_settings.conf").read_text(encoding="utf-8") == "after\n"
        return CommandResult(exit_code=1)

    with command_runtime_session(MemoryCommandRuntime([fake_run])):
        run_review_item_diff(review_item)

    assert recorded["request"].command.arguments[5:] == ("live/var/.../sddm.conf.d/kde_settings.conf", "repo/etc/sddm.conf.d/kde_settings.conf")




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

    runtime = MemoryCommandRuntime([CommandResult(exit_code=1)])
    with command_runtime_session(runtime):
        run_review_item_diff(review_item)

    request = runtime.requests[0]
    assert request.command.arguments[:6] == ("git", "--paginate", "diff", "--no-index", "--color=auto", "--")
    assert request.command.arguments[6:] == ("live/~/.../share/live-file", "repo/~/.config/repo-file")
    assert request.env["GIT_PAGER"] == DEFAULT_REVIEW_PAGER


def test_run_review_item_diff_preserves_interruption(monkeypatch) -> None:
    review_item = ReviewItem(
        selection_label="example:git@basic",
        package_id="git",
        target_name="gitconfig",
        action="update",
        operation="push",
        repo_path=Path("/repo-file"),
        live_path=Path("/live-file"),
        source_path="/repo-file",
        destination_path="/live-file",
        before_bytes=b"before\n",
        after_bytes=b"after\n",
    )
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)

    with command_runtime_session(MemoryCommandRuntime([CommandResult(exit_code=130)])):
        with pytest.raises(KeyboardInterrupt):
            run_review_item_diff(review_item)


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











@pytest.mark.parametrize("directory", [False, True])
@pytest.mark.parametrize("provenance", [False, True])
def test_review_editor_preserves_effective_child_sources_and_declaring_roots(
    tmp_path: Path, directory: bool, provenance: bool,
) -> None:
    from dotman.models import AdditionalSource

    parent_root = tmp_path / "parent"
    child_root = tmp_path / "child"
    parent_root.mkdir()
    child_root.mkdir()
    (parent_root / "shared").write_bytes(b"parent source")
    (child_root / "shared").write_bytes(b"child source")
    repo_path = child_root / "primary"
    live_path = tmp_path / "live"
    repo_path.write_bytes(b"repo")
    live_path.write_bytes(b"live")
    editor = EditorSpec(type=None, run="sh hooks/editor.sh", io="pipe")
    entries = (
        (AdditionalSource("shared", parent_root), AdditionalSource("shared", child_root))
        if provenance else ()
    )
    policy = dict(
        editor=editor, editor_explicit=True,
        additional_sources=("shared", "shared") if provenance else ("shared",),
        additional_source_entries=entries,
    )
    target = TargetPlan(
        package_id="app", target_name="config", action="update",
        target_kind="directory" if directory else "file", projection_kind="raw",
        repo_path=child_root if directory else repo_path,
        live_path=tmp_path if directory else live_path,
        command_cwd=child_root,
        command_env={"DOTMAN_PACKAGE_ROOT": str(child_root)},
        additional_sources_root=parent_root,
        directory_items=(DirectoryPlanItem(
            relative_path="primary", action="update", repo_path=repo_path,
            live_path=live_path, review_before_bytes=b"repo", review_after_bytes=b"live",
            **policy,
        ),) if directory else (),
        **({} if directory else policy),
    )
    plan = make_package_plan(
        operation="push", repo_name="example", package_id="app",
        requested_profile="default", variables={}, hooks={}, target_plans=[target],
    )
    item, = build_review_items([plan], operation="push")
    assert item.editor == editor
    assert item.editor_explicit

    def check_editor(request):
        assert request.cwd == child_root
        assert request.command.arguments[:2] == ("sh", "hooks/editor.sh")
        paths = request.env["DOTMAN_EDITOR_ADDITIONAL_SOURCE_PATHS"].split(os.pathsep)
        assert [Path(path).read_bytes() for path in paths] == (
            [b"parent source", b"child source"] if provenance else [b"parent source"]
        )
        assert all(Path(path).parent != parent_root for path in paths)
        return CommandResult(exit_code=0)

    with command_runtime_session(MemoryCommandRuntime([check_editor])):
        assert run_review_item_edit(item) == 0
