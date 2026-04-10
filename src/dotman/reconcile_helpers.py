from __future__ import annotations

from pathlib import Path

from dotman.reconcile import run_basic_reconcile
from dotman.templates import discover_template_file_dependencies


BUILTIN_JINJA_RECONCILE = "jinja"


def run_jinja_reconcile(
    *,
    repo_path: str,
    live_path: str,
    review_repo_path: str | None = None,
    review_live_path: str | None = None,
    editor: str | None = None,
) -> int:
    resolved_repo_path = Path(repo_path).expanduser().resolve()
    if not resolved_repo_path.exists():
        raise ValueError(f"repo path does not exist: {resolved_repo_path}")
    if not resolved_repo_path.is_file():
        raise ValueError(f"jinja reconcile requires a file repo path: {resolved_repo_path}")

    additional_sources = [str(path) for path in discover_template_file_dependencies(resolved_repo_path)]
    return run_basic_reconcile(
        repo_path=str(resolved_repo_path),
        live_path=live_path,
        additional_sources=additional_sources,
        review_repo_path=review_repo_path,
        review_live_path=review_live_path,
        editor=editor,
    )


__all__ = ["BUILTIN_JINJA_RECONCILE", "run_jinja_reconcile"]
