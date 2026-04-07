from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from dotman.diff_review import (
    DEFAULT_REVIEW_PAGER,
    ReviewItem,
    _select_review_pager_command,
    build_review_items,
    edit_status,
    run_review_item_diff,
    run_review_item_edit,
)
from dotman.models import Binding, BindingPlan, TargetPlan


def test_build_review_items_for_pull_uses_planning_view_bytes(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo-file"
    live_path = tmp_path / "live-file"
    repo_path.write_text("raw repo\n", encoding="utf-8")
    live_path.write_text("raw live\n", encoding="utf-8")

    plan = BindingPlan(
        operation="pull",
        binding=Binding(repo="example", selector="git", profile="basic"),
        selector_kind="package",
        package_ids=["git"],
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


def test_run_review_item_diff_invokes_git_diff(monkeypatch, tmp_path: Path) -> None:
    review_item = ReviewItem(
        binding_label="example:git@basic",
        package_id="git",
        target_name="gitconfig",
        action="update",
        operation="push",
        repo_path=tmp_path / "repo-file",
        live_path=tmp_path / "live-file",
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
        assert Path(cwd, "live", "live-file").read_text(encoding="utf-8") == "before\n"
        assert Path(cwd, "repo", "repo-file").read_text(encoding="utf-8") == "after\n"
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr("dotman.diff_review.subprocess.run", fake_run)

    run_review_item_diff(review_item)

    assert recorded["check"] is False
    assert recorded["env"] is None
    assert recorded["command"][:5] == ["git", "diff", "--no-index", "--color=auto", "--"]
    assert recorded["command"][5:] == ["live/live-file", "repo/repo-file"]


def test_run_review_item_diff_uses_repo_and_live_labels_for_pull(monkeypatch, tmp_path: Path) -> None:
    review_item = ReviewItem(
        binding_label="example:git@basic",
        package_id="git",
        target_name="gitconfig",
        action="update",
        operation="pull",
        repo_path=tmp_path / ".gitconfig",
        live_path=tmp_path / ".configrc",
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
        assert Path(cwd, "repo", ".gitconfig").read_text(encoding="utf-8") == "repo\n"
        assert Path(cwd, "live", ".configrc").read_text(encoding="utf-8") == "live\n"
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr("dotman.diff_review.subprocess.run", fake_run)

    run_review_item_diff(review_item)

    assert recorded["command"][5:] == ["repo/.gitconfig", "live/.configrc"]


def test_run_review_item_diff_uses_explicit_pager_when_stdout_is_tty(monkeypatch, tmp_path: Path) -> None:
    review_item = ReviewItem(
        binding_label="example:git@basic",
        package_id="git",
        target_name="gitconfig",
        action="update",
        operation="push",
        repo_path=tmp_path / "repo-file",
        live_path=tmp_path / "live-file",
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
    assert recorded["command"][6:] == ["live/live-file", "repo/repo-file"]
    assert recorded["env"] is not None
    assert recorded["env"]["GIT_PAGER"] == DEFAULT_REVIEW_PAGER


def test_select_review_pager_command_prefers_less_when_pager_env_is_cat(monkeypatch) -> None:
    monkeypatch.delenv("GIT_PAGER", raising=False)
    monkeypatch.setenv("PAGER", "cat")
    monkeypatch.setattr("dotman.diff_review._git_configured_pager_command", lambda: None)
    monkeypatch.setattr("dotman.diff_review.shutil.which", lambda name: "/usr/bin/less" if name == "less" else None)

    assert _select_review_pager_command() == DEFAULT_REVIEW_PAGER


def test_select_review_pager_command_replaces_explicit_git_pager_cat_with_less(monkeypatch) -> None:
    monkeypatch.setenv("GIT_PAGER", "cat")
    monkeypatch.setattr("dotman.diff_review.shutil.which", lambda name: "/usr/bin/less" if name == "less" else None)

    assert _select_review_pager_command() == DEFAULT_REVIEW_PAGER


def test_run_review_item_edit_prefers_pull_reconcile_command(monkeypatch, tmp_path: Path) -> None:
    review_item = ReviewItem(
        binding_label="example:nvim@basic",
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
        reconcile_command="sh hooks/reconcile.sh",
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


def test_run_review_item_edit_uses_planning_views_for_plain_pull_editor(monkeypatch, tmp_path: Path) -> None:
    repo_path = tmp_path / "repo-file"
    live_path = tmp_path / "live-file"
    repo_path.write_text("raw repo\n", encoding="utf-8")
    live_path.write_text("raw live\n", encoding="utf-8")
    review_item = ReviewItem(
        binding_label="example:nvim@basic",
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
        binding_label="example:nvim@basic",
        package_id="nvim",
        target_name="init_lua",
        action="update",
        operation="push",
        repo_path=repo_path,
        live_path=live_path,
        source_path=str(repo_path),
        destination_path=str(live_path),
        reconcile_command="sh hooks/reconcile.sh",
    )
    pull_item = ReviewItem(
        binding_label="example:nvim@basic",
        package_id="nvim",
        target_name="init_lua",
        action="update",
        operation="pull",
        repo_path=repo_path,
        live_path=live_path,
        source_path=str(live_path),
        destination_path=str(repo_path),
        reconcile_command="sh hooks/reconcile.sh",
    )

    assert edit_status(push_item) == "editor"
    assert edit_status(pull_item) == "reconcile"
