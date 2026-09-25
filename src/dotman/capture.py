from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Literal

from jinja2 import TemplateSyntaxError

from dotman.command_runtime import CommandRuntime
from dotman.file_access import read_bytes
from dotman.templates import JinjaRenderError, template_syntax_tokens
from dotman.text_merge import TextMergeFailed, merge_text


BUILTIN_PATCH_CAPTURE = "patch"

LineEnding = Literal["\n", "\r\n", "mixed"]


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
    command_runtime: CommandRuntime,
    protect_template_syntax: bool,
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
        command_runtime=command_runtime,
        protect_template_syntax=protect_template_syntax,
        repo_path=resolved_repo_path,
        review_repo_path=resolved_review_repo_path,
        review_live_path=resolved_review_live_path,
    )

    try:
        projected_bytes = project_repo_bytes(candidate_bytes)
    except (CaptureError, InterruptedError):
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
    command_runtime: CommandRuntime,
    protect_template_syntax: bool,
    repo_path: Path | None = None,
    review_repo_path: Path | None = None,
    review_live_path: Path | None = None,
) -> bytes:
    """Three-way merge reviewed live edits onto raw source.

    The reviewed Render is the common ancestor, so live edits that touch
    rendered-only output conflict instead of overwriting template syntax.
    Callers still verify the candidate by rendering it forward.
    """
    raw_text = _decode_utf8(raw_bytes, label="repo source", path=repo_path)
    review_repo_text = _decode_utf8(review_repo_bytes, label="review source", path=review_repo_path)
    _decode_utf8(review_live_bytes, label="review live content", path=review_live_path)
    if review_repo_bytes == review_live_bytes:
        return raw_bytes

    source_line_ending = _line_ending(raw_text)
    review_line_ending = _line_ending(review_repo_text)
    if source_line_ending != review_line_ending and "mixed" in (source_line_ending, review_line_ending):
        raise CaptureError(
            path=repo_path,
            detail="patch Capture cannot reconcile mixed line endings between repo source and review source",
        )
    # Renderers may normalize line endings (Jinja always emits LF); merge in the
    # rendered convention so unchanged lines still match, then restore the source's.
    merge_source = _convert_line_endings(raw_text, source_line_ending, review_line_ending)
    try:
        merged = merge_text(
            merge_source.encode("utf-8"), review_repo_bytes, review_live_bytes,
            labels=("repo source", "review source", "review live"), command_runtime=command_runtime,
        )
    except InterruptedError:
        raise
    except (TextMergeFailed, OSError) as exc:
        raise CaptureError(path=repo_path, detail=f"patch merge failed: {exc}") from exc
    if merged.conflicted:
        raise CaptureError(path=repo_path, detail="live edits overlap template-generated output")
    candidate_text = _convert_line_endings(
        _decode_utf8(merged.content, label="merged source", path=repo_path), review_line_ending, source_line_ending,
    )
    if protect_template_syntax:
        _require_same_template_syntax(raw_text, candidate_text, path=repo_path)
    return candidate_text.encode("utf-8")


def _line_ending(text: str) -> LineEnding:
    crlf_count = text.count("\r\n")
    lf_count = text.count("\n") - crlf_count
    if crlf_count and lf_count:
        return "mixed"
    return "\r\n" if crlf_count else "\n"


def _convert_line_endings(text: str, current: LineEnding, target: LineEnding) -> str:
    return text if current == target else text.replace(current, target)


def _require_same_template_syntax(source: str, candidate: str, *, path: Path | None) -> None:
    # A clean merge can still delete an expression whose output equals its own
    # source text, and forward verification cannot tell the two apart.
    try:
        unchanged = template_syntax_tokens(source) == template_syntax_tokens(candidate)
    except TemplateSyntaxError as exc:
        raise CaptureError(path=path, detail=f"captured source is not valid template syntax: {exc}") from exc
    if not unchanged:
        raise CaptureError(path=path, detail="Capture would change template syntax")


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
