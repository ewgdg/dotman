from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from dotman.models import BindingPlan
from dotman.reconcile import run_basic_reconcile


DEFAULT_REVIEW_PAGER = "less -FRX -R"
MAX_UNCOMPACTED_REVIEW_PATH_PARTS = 3
REVIEW_PATH_TAIL_PARTS = 2


@dataclass(frozen=True)
class ReviewItem:
    binding_label: str
    package_id: str
    target_name: str
    action: str
    operation: str
    repo_path: Path
    live_path: Path
    source_path: str
    destination_path: str
    before_bytes: bytes | None = field(default=None, repr=False)
    after_bytes: bytes | None = field(default=None, repr=False)
    reconcile_command: str | None = None
    command_cwd: Path | None = None
    command_env: dict[str, str] | None = field(default=None, repr=False)
    diff_unavailable_reason: str | None = None


def build_review_items(plans: Sequence[BindingPlan], *, operation: str) -> list[ReviewItem]:
    review_items: list[ReviewItem] = []
    for plan in plans:
        binding_label = f"{plan.binding.repo}:{plan.binding.selector}@{plan.binding.profile}"
        for target in plan.target_plans:
            if target.directory_items:
                for item in target.directory_items:
                    source_path, destination_path = _selection_item_paths(
                        operation=operation,
                        repo_path=item.repo_path,
                        live_path=item.live_path,
                    )
                    review_items.append(
                        ReviewItem(
                            binding_label=binding_label,
                            package_id=target.package_id,
                            target_name=target.target_name,
                            action=item.action,
                            operation=operation,
                            repo_path=item.repo_path,
                            live_path=item.live_path,
                            source_path=source_path,
                            destination_path=destination_path,
                            before_bytes=_load_item_bytes(repo_path=item.repo_path, live_path=item.live_path, operation=operation, before=True),
                            after_bytes=_load_item_bytes(repo_path=item.repo_path, live_path=item.live_path, operation=operation, before=False),
                        )
                    )
                continue

            if target.action == "noop":
                continue

            source_path, destination_path = _selection_item_paths(
                operation=operation,
                repo_path=target.repo_path,
                live_path=target.live_path,
            )
            diff_unavailable_reason = None
            if target.review_after_bytes is None:
                diff_unavailable_reason = target.projection_error or "diff preview is unavailable"
            review_items.append(
                ReviewItem(
                    binding_label=binding_label,
                    package_id=target.package_id,
                    target_name=target.target_name,
                    action=target.action,
                    operation=operation,
                    repo_path=target.repo_path,
                    live_path=target.live_path,
                    source_path=source_path,
                    destination_path=destination_path,
                    before_bytes=target.review_before_bytes,
                    after_bytes=target.review_after_bytes,
                    reconcile_command=target.reconcile_command,
                    command_cwd=target.command_cwd,
                    command_env=target.command_env,
                    diff_unavailable_reason=diff_unavailable_reason,
                )
            )
    return review_items


def diff_status(review_item: ReviewItem) -> str:
    if review_item.diff_unavailable_reason is not None:
        return "diff unavailable"
    return "diff"


def edit_status(review_item: ReviewItem) -> str:
    if review_item.operation == "pull" and review_item.reconcile_command is not None:
        return "reconcile"
    if review_item.repo_path.exists() and review_item.live_path.exists():
        return "editor"
    return "edit unavailable"


def run_review_item_diff(review_item: ReviewItem) -> None:
    if review_item.diff_unavailable_reason is not None:
        raise ValueError(review_item.diff_unavailable_reason)
    if review_item.before_bytes is None or review_item.after_bytes is None:
        raise ValueError("diff preview is unavailable")

    with tempfile.TemporaryDirectory(prefix="dotman-diff-") as temp_dir:
        temp_root = Path(temp_dir)
        left_side, right_side = _review_diff_side_names(operation=review_item.operation)
        left_path = _write_review_file(
            root=temp_root,
            side=left_side,
            reference_path=review_item.repo_path if review_item.operation == "pull" else review_item.live_path,
            content=review_item.before_bytes,
        )
        right_path = _write_review_file(
            root=temp_root,
            side=right_side,
            reference_path=review_item.live_path if review_item.operation == "pull" else review_item.repo_path,
            content=review_item.after_bytes,
        )
        try:
            pager_command = _select_review_pager_command() if sys.stdout.isatty() else None
            diff_command = _build_review_diff_command(
                root=temp_root,
                left_path=left_path,
                right_path=right_path,
                paginate=pager_command is not None,
            )
            diff_env = None
            if pager_command is not None:
                diff_env = {**os.environ, "GIT_PAGER": pager_command}
            completed = subprocess.run(
                diff_command,
                check=False,
                env=diff_env,
                cwd=temp_root,
            )
        except FileNotFoundError as exc:
            raise ValueError("git is required for diff review") from exc
    if completed.returncode not in {0, 1}:
        raise ValueError("git diff failed during review")


def run_review_item_edit(review_item: ReviewItem) -> int:
    if review_item.operation == "pull" and review_item.reconcile_command is not None:
        with tempfile.TemporaryDirectory(prefix="dotman-reconcile-review-") as temp_dir:
            review_env = dict(review_item.command_env or {})
            review_paths = _materialize_review_edit_paths(review_item=review_item, root=Path(temp_dir))
            if review_paths is not None:
                review_repo_path, review_live_path = review_paths
                review_env.update(
                    {
                        "DOTMAN_REVIEW_REPO_PATH": str(review_repo_path),
                        "DOTMAN_REVIEW_LIVE_PATH": str(review_live_path),
                    }
                )
            completed = subprocess.run(
                review_item.reconcile_command,
                check=False,
                shell=True,
                cwd=review_item.command_cwd,
                env={**os.environ, **review_env},
            )
            return completed.returncode

    if not review_item.repo_path.exists() or not review_item.live_path.exists():
        raise ValueError("edit requires both repo and live paths to exist")

    with tempfile.TemporaryDirectory(prefix="dotman-editor-review-") as temp_dir:
        review_paths = _materialize_review_edit_paths(review_item=review_item, root=Path(temp_dir))
        review_repo_path = None
        review_live_path = None
        if review_paths is not None:
            review_repo_path, review_live_path = review_paths

        try:
            return run_basic_reconcile(
                repo_path=str(review_item.repo_path),
                live_path=str(review_item.live_path),
                additional_sources=[],
                review_repo_path=str(review_repo_path) if review_repo_path is not None else None,
                review_live_path=str(review_live_path) if review_live_path is not None else None,
            )
        except FileNotFoundError as exc:
            raise ValueError("editor command was not found") from exc


def _load_item_bytes(*, repo_path: Path, live_path: Path, operation: str, before: bool) -> bytes:
    if operation == "pull":
        target_path = repo_path if before else live_path
    else:
        target_path = live_path if before else repo_path
    if not target_path.exists():
        return b""
    return target_path.read_bytes()


def _selection_item_paths(*, operation: str, repo_path: Path | str, live_path: Path | str) -> tuple[str, str]:
    repo_text = str(repo_path)
    live_text = str(live_path)
    if operation == "pull":
        return live_text, repo_text
    return repo_text, live_text


def _review_diff_side_names(*, operation: str) -> tuple[str, str]:
    if operation == "pull":
        return "repo", "live"
    return "live", "repo"


def _review_edit_side_names(*, operation: str) -> tuple[str, str]:
    return "review-repo", "review-live"


def _materialize_review_edit_paths(*, review_item: ReviewItem, root: Path) -> tuple[Path, Path] | None:
    if review_item.before_bytes is None or review_item.after_bytes is None:
        return None

    review_repo_bytes, review_live_bytes = _review_edit_bytes(review_item=review_item)
    repo_path, live_path = _review_edit_reference_paths(review_item=review_item)
    review_repo_path = _write_review_file(
        root=root,
        side=_review_edit_side_names(operation=review_item.operation)[0],
        reference_path=repo_path,
        content=review_repo_bytes,
    )
    review_live_path = _write_review_file(
        root=root,
        side=_review_edit_side_names(operation=review_item.operation)[1],
        reference_path=live_path,
        content=review_live_bytes,
    )
    return review_repo_path, review_live_path


def _review_edit_bytes(*, review_item: ReviewItem) -> tuple[bytes, bytes]:
    if review_item.operation == "pull":
        return review_item.before_bytes, review_item.after_bytes
    return review_item.after_bytes, review_item.before_bytes


def _review_edit_reference_paths(*, review_item: ReviewItem) -> tuple[Path, Path]:
    return review_item.repo_path, review_item.live_path


def _write_review_file(*, root: Path, side: str, reference_path: Path, content: bytes) -> Path:
    output_path = root / side / _review_display_path(reference_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(content)
    output_path.chmod(0o444)
    return output_path


def display_review_path(reference_path: Path | str) -> str:
    return str(_review_display_path(Path(reference_path)))


def _review_display_path(reference_path: Path) -> Path:
    normalized_path = _normalize_review_display_path(reference_path)
    return _compact_review_display_path(normalized_path)


def _normalize_review_display_path(reference_path: Path) -> Path:
    # Keep diff labels readable and machine-independent by collapsing the
    # current home directory to `~` instead of embedding an absolute prefix.
    try:
        home_relative_path = reference_path.relative_to(Path.home())
    except ValueError:
        if reference_path.is_absolute():
            relative_parts = reference_path.parts[1:]
            return Path(*relative_parts) if relative_parts else Path("content")
        return reference_path if reference_path.parts else Path("content")
    return Path("~") / home_relative_path if home_relative_path.parts else Path("~")


def _compact_review_display_path(display_path: Path) -> Path:
    parts = display_path.parts
    if len(parts) <= MAX_UNCOMPACTED_REVIEW_PATH_PARTS:
        return display_path
    # Keep the anchor and tail so long review labels stay short without
    # dropping the most useful disambiguating path context.
    return Path(parts[0], "...", *parts[-REVIEW_PATH_TAIL_PARTS:])


def _build_review_diff_command(*, root: Path, left_path: Path, right_path: Path, paginate: bool) -> list[str]:
    command = ["git"]
    if paginate:
        command.append("--paginate")
    command.extend(
        [
            "diff",
            "--no-index",
            "--color=auto",
            "--",
            str(left_path.relative_to(root)),
            str(right_path.relative_to(root)),
        ]
    )
    return command


def _select_review_pager_command() -> str | None:
    git_pager = os.environ.get("GIT_PAGER")
    if git_pager is not None:
        if not _pager_command_is_disabled(git_pager):
            return git_pager
        return DEFAULT_REVIEW_PAGER if shutil.which("less") is not None else None

    pager = os.environ.get("PAGER")
    if pager is not None:
        if not _pager_command_is_disabled(pager):
            return pager
        return DEFAULT_REVIEW_PAGER if shutil.which("less") is not None else None

    configured_pager = _git_configured_pager_command()
    if configured_pager is not None:
        if not _pager_command_is_disabled(configured_pager):
            return configured_pager
        return DEFAULT_REVIEW_PAGER if shutil.which("less") is not None else None

    return DEFAULT_REVIEW_PAGER if shutil.which("less") is not None else None


def _git_configured_pager_command() -> str | None:
    for key in ("pager.diff", "core.pager"):
        try:
            completed = subprocess.run(
                ["git", "config", "--get", key],
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return None
        if completed.returncode == 0:
            pager = completed.stdout.strip()
            if pager:
                return pager
    return None


def _pager_command_is_disabled(command: str) -> bool:
    try:
        command_parts = shlex.split(command)
    except ValueError:
        return False
    if not command_parts:
        return False
    return Path(command_parts[0]).name == "cat"
