from __future__ import annotations

from pathlib import Path

import pytest

from dotman.capture import CaptureError, capture_patch
from dotman.templates import build_template_context, render_template_string


def _build_jinja_projector(*, base_dir: Path, context: dict[str, object]):
    def project(candidate_bytes: bytes) -> bytes:
        candidate_text = candidate_bytes.decode("utf-8")
        return render_template_string(candidate_text, context, base_dir=base_dir).encode("utf-8")

    return project


def test_capture_patch_keeps_raw_repo_unchanged_when_review_diff_is_empty(tmp_path: Path) -> None:
    repo_path = tmp_path / "config.txt"
    review_repo_path = tmp_path / "review-repo.txt"
    review_live_path = tmp_path / "review-live.txt"

    repo_path.write_text("greeting = {{ vars.greeting }}\n", encoding="utf-8")
    review_repo_path.write_text("greeting = hello\n", encoding="utf-8")
    review_live_path.write_text("greeting = hello\n", encoding="utf-8")
    context = build_template_context({"greeting": "hello"}, profile="default", inferred_os="linux")

    result = capture_patch(
        repo_path=repo_path,
        review_repo_path=review_repo_path,
        review_live_path=review_live_path,
        project_repo_bytes=_build_jinja_projector(base_dir=repo_path.parent, context=context),
    )

    assert result == repo_path.read_bytes()


def test_capture_patch_applies_simple_value_change_and_verifies_projection(tmp_path: Path) -> None:
    repo_path = tmp_path / "config.txt"
    review_repo_path = tmp_path / "review-repo.txt"
    review_live_path = tmp_path / "review-live.txt"

    repo_path.write_text("greeting = {{ vars.greeting }}\n", encoding="utf-8")
    review_repo_path.write_text("greeting = hello\n", encoding="utf-8")
    review_live_path.write_text("greeting = world\n", encoding="utf-8")
    context = build_template_context({"greeting": "hello"}, profile="default", inferred_os="linux")

    result = capture_patch(
        repo_path=repo_path,
        review_repo_path=review_repo_path,
        review_live_path=review_live_path,
        project_repo_bytes=_build_jinja_projector(base_dir=repo_path.parent, context=context),
    )

    assert result == b"greeting = world\n"


def test_capture_patch_rejects_review_repo_line_count_mismatches(tmp_path: Path) -> None:
    repo_path = tmp_path / "config.txt"
    review_repo_path = tmp_path / "review-repo.txt"
    review_live_path = tmp_path / "review-live.txt"

    repo_path.write_text("greeting = {{ vars.greeting }}\nextra\n", encoding="utf-8")
    review_repo_path.write_text("greeting = hello\n", encoding="utf-8")
    review_live_path.write_text("greeting = world\n", encoding="utf-8")
    context = build_template_context({"greeting": "hello"}, profile="default", inferred_os="linux")

    with pytest.raises(CaptureError, match="same line count"):
        capture_patch(
            repo_path=repo_path,
            review_repo_path=review_repo_path,
            review_live_path=review_live_path,
            project_repo_bytes=_build_jinja_projector(base_dir=repo_path.parent, context=context),
        )


def test_capture_patch_requires_review_paths_from_env_when_not_provided(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo_path = tmp_path / "config.txt"
    repo_path.write_text("greeting = {{ vars.greeting }}\n", encoding="utf-8")
    monkeypatch.delenv("DOTMAN_REVIEW_REPO_PATH", raising=False)
    monkeypatch.delenv("DOTMAN_REVIEW_LIVE_PATH", raising=False)

    with pytest.raises(CaptureError, match="DOTMAN_REVIEW_REPO_PATH"):
        capture_patch(
            repo_path=repo_path,
            project_repo_bytes=lambda candidate_bytes: candidate_bytes,
        )


def test_capture_patch_reports_projection_mismatch(tmp_path: Path) -> None:
    repo_path = tmp_path / "config.txt"
    review_repo_path = tmp_path / "review-repo.txt"
    review_live_path = tmp_path / "review-live.txt"

    repo_path.write_text("greeting = {{ vars.greeting }}\n", encoding="utf-8")
    review_repo_path.write_text("greeting = hello\n", encoding="utf-8")
    review_live_path.write_text("greeting = world\n", encoding="utf-8")
    context = build_template_context({"greeting": "hello"}, profile="default", inferred_os="linux")

    with pytest.raises(CaptureError, match="captured bytes do not match"):
        capture_patch(
            repo_path=repo_path,
            review_repo_path=review_repo_path,
            review_live_path=review_live_path,
            project_repo_bytes=lambda candidate_bytes: _build_jinja_projector(
                base_dir=repo_path.parent,
                context=context,
            )(candidate_bytes).replace(b"world", b"mismatch"),
        )


def test_capture_patch_wraps_jinja_projection_errors_structurally(tmp_path: Path) -> None:
    repo_path = tmp_path / "config.txt"
    review_repo_path = tmp_path / "review-repo.txt"
    review_live_path = tmp_path / "review-live.txt"

    repo_path.write_text("greeting = {{ vars.greeting }}\n", encoding="utf-8")
    review_repo_path.write_text("greeting = hello\n", encoding="utf-8")
    review_live_path.write_text("greeting = world\n", encoding="utf-8")

    with pytest.raises(CaptureError, match="capture failed") as exc_info:
        capture_patch(
            repo_path=repo_path,
            review_repo_path=review_repo_path,
            review_live_path=review_live_path,
            project_repo_bytes=lambda _candidate_bytes: render_template_string(
                "{{ missing.value }}",
                {},
                base_dir=repo_path.parent,
                source_path=repo_path,
            ).encode("utf-8"),
        )

    assert exc_info.value.path == repo_path
    assert "missing" in exc_info.value.detail
