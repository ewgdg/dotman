from __future__ import annotations

import difflib
import os
from pathlib import Path
from typing import Callable


BUILTIN_PATCH_CAPTURE = "patch"


ProjectRepoBytes = Callable[[bytes], bytes]


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

    raw_bytes = resolved_repo_path.read_bytes()
    review_repo_bytes = resolved_review_repo_path.read_bytes()
    review_live_bytes = resolved_review_live_path.read_bytes()
    candidate_bytes = apply_review_patch(raw_bytes, review_repo_bytes, review_live_bytes)

    try:
        projected_bytes = project_repo_bytes(candidate_bytes)
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 - the caller needs the original projection error text.
        raise ValueError(f"patch capture verification projection failed: {exc}") from exc

    if projected_bytes != review_live_bytes:
        raise ValueError("patch capture verification mismatch: projected bytes do not match the review live bytes")
    return candidate_bytes


def apply_review_patch(raw_bytes: bytes, review_repo_bytes: bytes, review_live_bytes: bytes) -> bytes:
    raw_text = _decode_utf8(raw_bytes, label="repo source")
    review_repo_text = _decode_utf8(review_repo_bytes, label="review repo view")
    review_live_text = _decode_utf8(review_live_bytes, label="review live view")

    raw_lines = raw_text.splitlines(keepends=True)
    review_repo_lines = review_repo_text.splitlines(keepends=True)
    review_live_lines = review_live_text.splitlines(keepends=True)

    if len(raw_lines) != len(review_repo_lines):
        raise ValueError("patch capture requires the repo source and review repo view to have the same line count")

    patched_lines = list(raw_lines)
    offset = 0
    matcher = difflib.SequenceMatcher(None, review_repo_lines, review_live_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        start = i1 + offset
        end = i2 + offset
        if start < 0 or end < start or end > len(patched_lines):
            raise ValueError("patch capture could not be applied to the repo source")
        if tag == "equal":
            continue
        replacement = review_live_lines[j1:j2]
        patched_lines[start:end] = replacement
        offset += len(replacement) - (i2 - i1)

    return "".join(patched_lines).encode("utf-8")


def _decode_utf8(content: bytes, *, label: str) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"patch capture requires UTF-8 text for {label}") from exc


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
            raise ValueError(f"patch capture requires {label}")
        resolved_value = os.environ.get(env_name)
        if resolved_value is None:
            if option_name is None:
                raise ValueError(f"patch capture requires {label} via {env_name}")
            raise ValueError(f"patch capture requires {label} via {env_name} or {option_name}")

    resolved_path = Path(resolved_value).expanduser().resolve()
    if not resolved_path.exists():
        raise ValueError(f"patch capture requires an existing {label}: {resolved_path}")
    if not resolved_path.is_file():
        raise ValueError(f"patch capture requires a file {label}: {resolved_path}")
    return resolved_path


__all__ = ["BUILTIN_PATCH_CAPTURE", "ProjectRepoBytes", "apply_review_patch", "capture_patch"]
