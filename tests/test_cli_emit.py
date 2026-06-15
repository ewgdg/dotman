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
from dotman.cli_emit import emit_error, emit_payload
from dotman.models import ResolvedPackageIdentity, TargetPlan
from dotman.planning import TrackedPackageProfileConflictError
from dotman.templates import JinjaRenderError
from tests.helpers import make_package_plan


def test_emit_payload_renders_probe_targets_without_fake_paths(capsys) -> None:
    plan = make_package_plan(
        operation="push",
        repo_name="sandbox",
        package_id="app",
        requested_profile="default",
        target_plans=[
            TargetPlan(
                package_id="app",
                target_name="version",
                repo_path=Path("/repo/app"),
                live_path=Path("/repo/app"),
                action="probe",
                target_kind="probe",
                projection_kind="probe",
                probe_command="exit 0",
            )
        ],
    )

    exit_code = emit_payload(
        operation="push",
        plans=[plan],
        json_output=False,
        mode="dry-run",
        use_color=False,
        collect_pending_selection_items_for_operation=lambda _plans, *, operation: [
            types.SimpleNamespace(
                selection_label="sandbox:app@default",
                package_id="app",
                target_name="version",
                bound_profile=None,
                action="probe",
            )
        ],
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "sandbox:app.version -> probe" in output
    assert "None" not in output


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


def test_emit_error_styles_profile_conflict_selectors_and_package_identity(capsys) -> None:
    error = TrackedPackageProfileConflictError(
        package_identity=ResolvedPackageIdentity(repo="fixture", package_id="shared", bound_profile=None),
        conflict_kind="ambiguous_implicit",
        contenders=(
            "fixture:shared@basic required by fixture:meta-a@basic",
            "fixture:shared@work required by fixture:meta-b@work",
        ),
    )

    emit_error(error, use_color=True)

    error_output = capsys.readouterr().err
    assert "ambiguous implicit profile contexts for \033[2;34mfixture\033[0m\033[2m:\033[0m\033[1mshared\033[0m:" in error_output
    assert "\033[2;34mfixture\033[0m\033[2m:\033[0m\033[1mshared\033[0m\033[2m@basic\033[0m" in error_output
    assert " required by " in error_output
    assert "\033[2mrequired by\033[0m" not in error_output
    assert "\033[2;34mfixture\033[0m\033[2m:\033[0m\033[1mmeta-b\033[0m\033[2m@work\033[0m" in error_output
