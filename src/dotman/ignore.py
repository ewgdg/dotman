from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

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


def list_directory_files(root: Path, ignore_patterns: tuple[str, ...]) -> dict[str, Path]:
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
