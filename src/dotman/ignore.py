from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Iterable
import json
import subprocess
import sys

from pathspec import PathSpec
from pathspec.gitignore import GitIgnoreSpec



def _normalize_relative_path(relative_path: str) -> str:
    normalized = relative_path.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/")


@dataclass(frozen=True, slots=True)
class IgnoreMatcher:
    spec: PathSpec | None = None

    @classmethod
    def from_patterns(cls, patterns: Iterable[str]) -> "IgnoreMatcher":
        normalized_patterns = tuple(pattern for pattern in patterns if pattern)
        if not normalized_patterns:
            return cls()
        return cls(GitIgnoreSpec.from_lines(normalized_patterns))

    def matches(self, relative_path: str) -> bool:
        if self.spec is None:
            return False
        return self.spec.match_file(_normalize_relative_path(relative_path))

    def matches_directory(self, relative_path: str) -> bool:
        return self.matches(f"{_normalize_relative_path(relative_path).rstrip('/')}/")



def matches_ignore_pattern(relative_path: str, pattern: str) -> bool:
    return IgnoreMatcher.from_patterns((pattern,)).matches(relative_path)



def _symlink_target_text(path: Path) -> str:
    try:
        return os.readlink(path)
    except OSError:
        return "<unknown>"



def _directory_identity(path: Path, *, relative_path: str) -> tuple[int, int]:
    try:
        stat_result = path.stat()
    except OSError as exc:
        display_path = relative_path or "."
        raise ValueError(
            "directory symlink cannot be resolved while scanning directory: "
            f"{display_path} -> {_symlink_target_text(path)}"
        ) from exc
    return (stat_result.st_dev, stat_result.st_ino)



def _list_directory_files_without_sudo(
    root: Path,
    ignore_patterns: tuple[str, ...],
    *,
    follow_dir_symlinks: bool = False,
) -> dict[str, Path]:
    files: dict[str, Path] = {}
    if not root.exists():
        return files

    matcher = IgnoreMatcher.from_patterns(ignore_patterns)
    active_dirs: set[tuple[int, int]] = set()

    def scan_directory(directory: Path, relative_directory: str) -> None:
        directory_identity = _directory_identity(directory, relative_path=relative_directory)
        if directory_identity in active_dirs:
            display_path = relative_directory or "."
            raise ValueError(
                "directory symlink loop encountered while scanning directory: "
                f"{display_path} -> {_symlink_target_text(directory)}"
            )
        active_dirs.add(directory_identity)
        try:
            for child in sorted(directory.iterdir(), key=lambda path: path.name):
                relative = f"{relative_directory}/{child.name}" if relative_directory else child.name
                if child.is_symlink() and child.is_dir():
                    if matcher.matches_directory(relative):
                        continue
                    if not follow_dir_symlinks:
                        raise ValueError(
                            "directory symlink encountered while scanning directory: "
                            f"{relative} -> {_symlink_target_text(child)}; "
                            'set symlinks.dir_symlink_mode = "follow" to descend'
                        )
                    scan_directory(child, relative)
                    continue
                if child.is_dir():
                    scan_directory(child, relative)
                    continue
                if matcher.matches(relative):
                    continue
                files[relative] = child
        finally:
            active_dirs.remove(directory_identity)

    scan_directory(root, "")
    return files



def _list_directory_files_via_sudo(
    root: Path,
    ignore_patterns: tuple[str, ...],
    *,
    follow_dir_symlinks: bool = False,
) -> dict[str, Path]:
    from dotman.file_access import request_sudo

    request_sudo(f"list protected directory: {root}")
    completed = subprocess.run(
        ["sudo", "-n", sys.executable, "-m", "dotman.privileged_ops", "list-directory-files", str(root)],
        input=json.dumps(
            {"ignore_patterns": ignore_patterns, "follow_dir_symlinks": follow_dir_symlinks}
        ).encode("utf-8"),
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise PermissionError(stderr or f"permission denied for {root}")
    payload = json.loads(completed.stdout.decode("utf-8"))
    return {relative: Path(path_text) for relative, path_text in payload.items()}



def list_directory_files(
    root: Path,
    ignore_patterns: tuple[str, ...],
    *,
    follow_dir_symlinks: bool = False,
) -> dict[str, Path]:
    try:
        return _list_directory_files_without_sudo(root, ignore_patterns, follow_dir_symlinks=follow_dir_symlinks)
    except PermissionError:
        return _list_directory_files_via_sudo(root, ignore_patterns, follow_dir_symlinks=follow_dir_symlinks)
