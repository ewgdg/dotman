from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path


def _resolve_editor_command(editor: str | None) -> list[str]:
    editor_value = editor or os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not editor_value:
        raise ValueError("reconcile editor requires --editor or $VISUAL/$EDITOR")
    return shlex.split(editor_value)


def _resolve_existing_path(path_value: str, *, label: str) -> Path:
    resolved = Path(path_value).expanduser().resolve()
    if not resolved.exists():
        raise ValueError(f"{label} does not exist: {resolved}")
    return resolved


def run_basic_reconcile(
    *,
    repo_path: str,
    live_path: str,
    additional_sources: list[str],
    editor: str | None = None,
) -> int:
    resolved_repo_path = _resolve_existing_path(repo_path, label="repo path")
    resolved_live_path = _resolve_existing_path(live_path, label="live path")
    resolved_additional_sources = [
        _resolve_existing_path(path_value, label="additional source")
        for path_value in additional_sources
    ]

    ordered_paths: list[Path] = []
    for candidate_path in [resolved_repo_path, resolved_live_path, *resolved_additional_sources]:
        if candidate_path not in ordered_paths:
            ordered_paths.append(candidate_path)

    completed = subprocess.run(
        [*_resolve_editor_command(editor), *(str(path) for path in ordered_paths)],
        check=False,
    )
    return completed.returncode
