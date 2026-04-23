from __future__ import annotations

import difflib
import os
from pathlib import Path
from typing import Callable

from dotman.file_access import read_bytes
from dotman.templates import JinjaRenderError


BUILTIN_PATCH_CAPTURE = "patch"


class CaptureError(ValueError):
    def __init__(self, path: Path | None, detail: str) -> None:
        self.path = path
        self.detail = detail
        super().__init__(detail)

    def __str__(self) -> str:
        return format_capture_error(self)


ProjectRepoBytes = Callable[[bytes], bytes]


def format_capture_error(error: CaptureError) -> str:
    location = f" for {error.path}" if error.path is not None else ""
    return f"capture failed{location}: {error.detail}"


def capture_patch(
    *,
    repo_path: str | Path,
    project_repo_bytes: ProjectRepoBytes,
    review_repo_path: str | Path | None = None,
    review_live_path: str | Path | None = None,
) -> bytes:
    resolved_repo_path = _resolve_existing_file(
        repo_path,
        label="repo path",
    )
    resolved_review_repo_path = _resolve_existing_file(
        review_repo_path,
        label="review repo path",
        env_name="DOTMAN_REVIEW_REPO_PATH",
        option_name="--review-repo-path",
    )
    resolved_review_live_path = _resolve_existing_file(
        review_live_path,
        label="review live path",
        env_name="DOTMAN_REVIEW_LIVE_PATH",
        option_name="--review-live-path",
    )

    raw_bytes = read_bytes(resolved_repo_path)
    review_repo_bytes = resolved_review_repo_path.read_bytes()
    review_live_bytes = resolved_review_live_path.read_bytes()
    candidate_bytes = apply_review_patch(
        raw_bytes,
        review_repo_bytes,
        review_live_bytes,
        repo_path=resolved_repo_path,
        review_repo_path=resolved_review_repo_path,
        review_live_path=resolved_review_live_path,
    )

    try:
        projected_bytes = project_repo_bytes(candidate_bytes)
    except CaptureError:
        raise
    except JinjaRenderError as exc:
        raise CaptureError(path=exc.path, detail=exc.detail) from exc
    except Exception as exc:  # noqa: BLE001 - the caller needs the original projection error text.
        raise CaptureError(path=resolved_repo_path, detail=f"capture projection failed: {exc}") from exc

    if projected_bytes != review_live_bytes:
        raise CaptureError(
            path=resolved_review_live_path,
            detail="captured bytes do not match the review live bytes",
        )
    return candidate_bytes


def apply_review_patch(
    raw_bytes: bytes,
    review_repo_bytes: bytes,
    review_live_bytes: bytes,
    *,
    repo_path: Path | None = None,
    review_repo_path: Path | None = None,
    review_live_path: Path | None = None,
) -> bytes:
    raw_text = _decode_utf8(raw_bytes, label="repo source", path=repo_path)
    review_repo_text = _decode_utf8(review_repo_bytes, label="review source", path=review_repo_path)
    review_live_text = _decode_utf8(review_live_bytes, label="review live content", path=review_live_path)

    raw_lines = raw_text.splitlines(keepends=True)
    review_repo_lines = review_repo_text.splitlines(keepends=True)
    review_live_lines = review_live_text.splitlines(keepends=True)

    if len(raw_lines) != len(review_repo_lines):
        raise CaptureError(
            path=repo_path,
            detail="repo source and review source must have the same line count",
        )

    patched_lines = list(raw_lines)
    offset = 0
    matcher = difflib.SequenceMatcher(None, review_repo_lines, review_live_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        start = i1 + offset
        end = i2 + offset
        if start < 0 or end < start or end > len(patched_lines):
            raise CaptureError(path=repo_path, detail="review changes could not be applied to the repo source")
        if tag == "equal":
            continue
        replacement = review_live_lines[j1:j2]
        patched_lines[start:end] = replacement
        offset += len(replacement) - (i2 - i1)

    return "".join(patched_lines).encode("utf-8")


def _decode_utf8(content: bytes, *, label: str, path: Path | None = None) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CaptureError(path=path, detail=f"requires UTF-8 text for {label}") from exc


def _resolve_existing_file(
    path_value: str | Path | None,
    *,
    label: str,
    env_name: str | None = None,
    option_name: str | None = None,
) -> Path:
    resolved_value = path_value
    if resolved_value is None:
        if env_name is None:
            raise CaptureError(path=None, detail=f"requires {label}")
        resolved_value = os.environ.get(env_name)
        if resolved_value is None:
            if option_name is None:
                raise CaptureError(path=None, detail=f"requires {label} via {env_name}")
            raise CaptureError(path=None, detail=f"requires {label} via {env_name} or {option_name}")

    resolved_path = Path(resolved_value).expanduser().resolve()
    if not resolved_path.exists():
        raise CaptureError(path=resolved_path, detail=f"requires an existing {label}")
    if not resolved_path.is_file():
        raise CaptureError(path=resolved_path, detail=f"requires a file {label}")
    return resolved_path


__all__ = ["BUILTIN_PATCH_CAPTURE", "CaptureError", "ProjectRepoBytes", "apply_review_patch", "capture_patch"]
