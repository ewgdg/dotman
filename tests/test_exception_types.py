from __future__ import annotations

from pathlib import Path

import pytest

from dotman.capture import CaptureError
from dotman.config import ManagerConfigLoadError
from dotman.templates import JinjaRenderError
from dotman.toml_utils import TomlLoadError


@pytest.mark.parametrize(
    "error",
    [
        JinjaRenderError(path=Path("/tmp/template.txt"), detail="missing value"),
        CaptureError(path=Path("/tmp/review.txt"), detail="broken projection"),
        TomlLoadError(context="package manifest", path=Path("/tmp/package.toml"), detail="bad toml"),
        ManagerConfigLoadError(path=Path("/tmp/config.toml"), detail="missing config", hint="create one"),
    ],
)
def test_custom_exceptions_allow_runtime_exception_metadata_updates(error: Exception) -> None:
    cause = ValueError("root cause")

    error.__traceback__ = None
    error.__cause__ = cause
    error.__context__ = cause
    error.add_note("note")

    assert error.__cause__ is cause
    assert error.__context__ is cause
    assert error.__notes__ == ["note"]
