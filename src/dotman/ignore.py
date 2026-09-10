from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Iterable
import json
import sys

from pathspec import PathSpec
from pathspec.gitignore import GitIgnoreSpec

from dotman.command_runtime import (
    ArgvCommand,
    CommandRequest,
    current_command_runtime,
    raise_for_command_interruption,
)



GITIGNORE_CONTROL_FILE_PATTERNS = (".gitignore", "**/.gitignore")


@dataclass(frozen=True)
class GitIgnoreChain:
    """Control files retain their own coordinate system; Git globs are never rebased."""
    target: str
    controls: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def matcher(self) -> "GitIgnoreMatcher":
        return GitIgnoreMatcher(self.target, tuple(
            (directory, GitIgnoreSpec.from_lines(lines)) for directory, lines in self.controls
        ))


@dataclass(frozen=True)
class GitIgnoreMatcher:
    target: str
    controls: tuple[tuple[str, GitIgnoreSpec], ...]

    def matches(self, relative: str) -> bool:
        path = "/".join(part for part in (self.target, relative.rstrip("/")) if part)
        parts = path.split("/")
        # An excluded directory cannot be reopened by a child's control file.
        for index in range(1, len(parts) + 1):
            candidate = "/".join(parts[:index])
            directory = index < len(parts) or relative.endswith("/")
            ignored = False
            for scope, spec in self.controls:
                prefix = scope + "/" if scope else ""
                if not candidate.startswith(prefix):
                    continue
                local = candidate[len(prefix):] + ("/" if directory else "")
                result = spec.check_file(local)
                if result.include is not None:
                    ignored = result.include
            if ignored:
                return True
        return False

    def matches_directory(self, relative: str) -> bool:
        return self.matches(relative.rstrip("/") + "/")


def collect_gitignore_chain(root: Path, repository_root: Path, *, nested: bool = True) -> GitIgnoreChain:
    from dotman.file_access import read_bytes
    import stat

    relative = root.relative_to(repository_root)
    directories = [repository_root]
    for part in relative.parts:
        directories.append(directories[-1] / part)
    if not nested:
        # Sync reads target and nested controls inside its diagnostic-aware census.
        directories.pop()
    if nested and root.is_dir() and not root.is_symlink():
        for directory, children, _files in os.walk(root, followlinks=False):
            children.sort()
            if Path(directory) != root:
                directories.append(Path(directory))
    controls = []
    for directory in directories:
        control = directory / ".gitignore"
        try:
            shape = control.lstat()
        except (FileNotFoundError, NotADirectoryError):
            continue
        # Ignore controls are data, not symlink or special-file payloads.
        if stat.S_ISREG(shape.st_mode):
            scope = directory.relative_to(repository_root).as_posix()
            controls.append(("" if scope == "." else scope,
                             tuple(read_bytes(control).decode("utf-8", errors="replace").splitlines())))
    return GitIgnoreChain("" if relative == Path(".") else relative.as_posix(), tuple(controls))


def _normalize_relative_path(relative_path: str) -> str:
    normalized = relative_path.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/")


@dataclass(frozen=True, slots=True)
class IgnoreMatcher:
    spec: PathSpec | None = None
    gitignore: GitIgnoreMatcher | None = None

    @classmethod
    def from_patterns(cls, patterns: Iterable[str], *, gitignore: GitIgnoreChain | None = None) -> "IgnoreMatcher":
        normalized_patterns = tuple(pattern for pattern in patterns if pattern)
        if not normalized_patterns:
            return cls(gitignore=gitignore.matcher() if gitignore else None)
        return cls(GitIgnoreSpec.from_lines(normalized_patterns), gitignore.matcher() if gitignore else None)

    def matches(self, relative_path: str) -> bool:
        relative = _normalize_relative_path(relative_path)
        result = self.spec.check_file(relative) if self.spec else None
        # Dotman patterns are target-relative overrides, including explicit negations.
        if result is not None and result.include is not None:
            return bool(result.include)
        return bool(self.gitignore and self.gitignore.matches(relative))

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



def _directory_contains_skip_marker(directory: Path, skip_markers: tuple[str, ...]) -> bool:
    for marker in skip_markers:
        try:
            (directory / marker).lstat()
        except FileNotFoundError:
            continue
        return True
    return False



def _list_directory_files_without_sudo(
    root: Path,
    ignore_patterns: tuple[str, ...],
    *,
    skip_markers: tuple[str, ...] = (),
    follow_dir_symlinks: bool = False,
    force_ignore_patterns: tuple[str, ...] = (),
    gitignore: GitIgnoreChain | None = None,
) -> dict[str, Path]:
    files: dict[str, Path] = {}
    if not root.exists():
        return files

    matcher = IgnoreMatcher.from_patterns(ignore_patterns, gitignore=gitignore)
    force_matcher = IgnoreMatcher.from_patterns(force_ignore_patterns)
    active_dirs: set[tuple[int, int]] = set()

    def scan_directory(directory: Path, relative_directory: str) -> None:
        if _directory_contains_skip_marker(directory, skip_markers):
            return
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
                    if force_matcher.matches_directory(relative) or matcher.matches_directory(relative):
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
                    if force_matcher.matches_directory(relative) or matcher.matches_directory(relative):
                        continue
                    scan_directory(child, relative)
                    continue
                if child.name in skip_markers or force_matcher.matches(relative) or matcher.matches(relative):
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
    skip_markers: tuple[str, ...] = (),
    follow_dir_symlinks: bool = False,
    force_ignore_patterns: tuple[str, ...] = (),
    gitignore: GitIgnoreChain | None = None,
) -> dict[str, Path]:
    from dotman.file_access import request_sudo

    request_sudo(f"list protected directory: {root}")
    result = current_command_runtime().run(
        CommandRequest(
            command=ArgvCommand(
                (
                    "sudo",
                    "-n",
                    sys.executable,
                    "-m",
                    "dotman.privileged_ops",
                    "list-directory-files",
                    str(root),
                )
            ),
            input=json.dumps(
                {
                    "ignore_patterns": ignore_patterns,
                    "skip_markers": skip_markers,
                    "follow_dir_symlinks": follow_dir_symlinks,
                    "force_ignore_patterns": force_ignore_patterns,
                    "gitignore": {"target": gitignore.target, "controls": gitignore.controls} if gitignore else None,
                }
            ).encode("utf-8"),
        )
    )
    raise_for_command_interruption(result)
    if result.exit_code != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise PermissionError(stderr or f"permission denied for {root}")
    payload = json.loads(result.stdout.decode("utf-8"))
    return {relative: Path(path_text) for relative, path_text in payload.items()}



def list_directory_files(
    root: Path,
    ignore_patterns: tuple[str, ...],
    *,
    skip_markers: tuple[str, ...] = (),
    follow_dir_symlinks: bool = False,
    force_ignore_patterns: tuple[str, ...] = (),
    gitignore: GitIgnoreChain | None = None,
) -> dict[str, Path]:
    try:
        return _list_directory_files_without_sudo(
            root,
            ignore_patterns,
            skip_markers=skip_markers,
            follow_dir_symlinks=follow_dir_symlinks,
            force_ignore_patterns=force_ignore_patterns,
            gitignore=gitignore,
        )
    except PermissionError:
        return _list_directory_files_via_sudo(
            root,
            ignore_patterns,
            skip_markers=skip_markers,
            follow_dir_symlinks=follow_dir_symlinks,
            force_ignore_patterns=force_ignore_patterns,
            gitignore=gitignore,
        )
