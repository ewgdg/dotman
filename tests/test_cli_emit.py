from __future__ import annotations

import sys
import types
from pathlib import Path

pathspec = types.ModuleType("pathspec")
pathspec.__path__ = []
pathspec.PathSpec = type("PathSpec", (), {})
sys.modules.setdefault("pathspec", pathspec)

pathspec_gitignore = types.ModuleType("pathspec.gitignore")
pathspec_gitignore.GitIgnoreSpec = type("GitIgnoreSpec", (), {})
sys.modules.setdefault("pathspec.gitignore", pathspec_gitignore)

from dotman.capture import CaptureError
from dotman.cli_emit import emit_error
from dotman.templates import JinjaRenderError


class StructuredGenericError(ValueError):
    def __init__(self, path: Path, detail: str) -> None:
        self.path = path
        self.detail = detail
        super().__init__(detail)


def test_emit_error_includes_path_for_structured_generic_errors(capsys) -> None:
    error = StructuredGenericError(path=Path("/tmp/example.txt"), detail="boom")

    emit_error(error, use_color=False)

    error_output = capsys.readouterr().err
    assert ":: StructuredGenericError" in error_output
    assert "path:" in error_output
    assert "detail:" in error_output
    assert "/tmp/example.txt" in error_output
    assert "boom" in error_output


def test_emit_error_styles_capture_errors_with_capture_header(capsys) -> None:
    error = CaptureError(path=Path("/tmp/review.txt"), detail="broken projection")

    emit_error(error, use_color=False)

    error_output = capsys.readouterr().err
    assert ":: CaptureError" in error_output
    assert "path:" in error_output
    assert "detail:" in error_output
    assert "/tmp/review.txt" in error_output
    assert "broken projection" in error_output


def test_emit_error_uses_error_type_name_for_jinja_errors(capsys) -> None:
    error = JinjaRenderError(path=Path("/tmp/template.txt"), detail="missing value")

    emit_error(error, use_color=False)

    error_output = capsys.readouterr().err
    assert ":: JinjaRenderError" in error_output
    assert "path:" in error_output
    assert "detail:" in error_output
    assert "/tmp/template.txt" in error_output
    assert "missing value" in error_output
