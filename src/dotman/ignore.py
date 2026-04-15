from __future__ import annotations

from dataclasses import dataclass
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



def matches_ignore_pattern(relative_path: str, pattern: str) -> bool:
    return IgnoreMatcher.from_patterns((pattern,)).matches(relative_path)



def _list_directory_files_without_sudo(root: Path, ignore_patterns: tuple[str, ...]) -> dict[str, Path]:
    files: dict[str, Path] = {}
    if not root.exists():
        return files

    matcher = IgnoreMatcher.from_patterns(ignore_patterns)
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        relative = path.relative_to(root).as_posix()
        if matcher.matches(relative):
            continue
        files[relative] = path
    return files



def _list_directory_files_via_sudo(root: Path, ignore_patterns: tuple[str, ...]) -> dict[str, Path]:
    from dotman.file_access import request_sudo

    request_sudo()
    completed = subprocess.run(
        ["sudo", "-n", sys.executable, "-m", "dotman.privileged_ops", "list-directory-files", str(root)],
        input=json.dumps(ignore_patterns).encode("utf-8"),
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise PermissionError(stderr or f"permission denied for {root}")
    payload = json.loads(completed.stdout.decode("utf-8"))
    return {relative: Path(path_text) for relative, path_text in payload.items()}



def list_directory_files(root: Path, ignore_patterns: tuple[str, ...]) -> dict[str, Path]:
    try:
        return _list_directory_files_without_sudo(root, ignore_patterns)
    except PermissionError:
        return _list_directory_files_via_sudo(root, ignore_patterns)
