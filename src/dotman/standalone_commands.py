from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from dotman.capture import capture_patch
from dotman.command_runtime import (
    CommandRequest,
    ShellCommand,
    current_command_runtime,
    raise_for_command_interruption,
)
from dotman.elevation import request_elevation_from_env
from dotman.manifest import flatten_vars
from dotman.reconcile import run_basic_reconcile
from dotman.reconcile_helpers import run_jinja_reconcile
from dotman.templates import (
    JinjaRenderError,
    build_template_context,
    render_template_file,
    render_template_string,
)


class StandaloneCommandRunner:
    """Run commands that do not need manager configuration or an engine."""

    command_names = frozenset({"rewrite", "transform", "elevation", "capture", "reconcile", "render"})

    def run(self, args: Any) -> int:
        if args.command == "rewrite" and args.rewrite_name == "home":
            from dotman.rewrites.cli import run_home_rewrite

            return run_home_rewrite(action=args.rewrite_action, input_path=args.input_path)
        if args.command == "transform":
            from dotman.transforms.cli import run_parsed_engine
            from dotman.transforms.json import JsonTransformEngine
            from dotman.transforms.plist import PlistTransformEngine
            from dotman.transforms.toml import TomlTransformEngine
            from dotman.transforms.xml import XmlTransformEngine

            engines = {
                "json": JsonTransformEngine,
                "plist": PlistTransformEngine,
                "toml": TomlTransformEngine,
                "xml": XmlTransformEngine,
            }
            return run_parsed_engine(engines[args.transform_format](), args.transform_parser, args)
        if args.command == "elevation" and args.elevation_command == "request":
            return request_elevation_from_env(args.reason)
        if args.command == "capture" and args.capture_command == "patch":
            return run_patch_capture(
                repo_path=args.repo_path,
                render_command=args.render,
                review_repo_path=args.review_repo_path,
                review_live_path=args.review_live_path,
                profile=args.profile,
                inferred_os=args.template_os,
                var_assignments=args.var,
            )
        if args.command == "reconcile" and args.reconcile_helper == "editor":
            return run_basic_reconcile(
                repo_path=args.repo_path,
                live_path=args.live_path,
                additional_sources=args.additional_source,
                review_repo_path=args.review_repo_path,
                review_live_path=args.review_live_path,
                editor=args.editor,
                assume_yes=getattr(args, "assume_yes", False),
            )
        if args.command == "reconcile" and args.reconcile_helper == "jinja":
            return run_jinja_reconcile(
                repo_path=args.repo_path,
                live_path=args.live_path,
                review_repo_path=args.review_repo_path,
                review_live_path=args.review_live_path,
                editor=args.editor,
                assume_yes=getattr(args, "assume_yes", False),
            )
        if args.command == "render" and args.render_command == "jinja":
            return run_jinja_render(
                source_path=args.source_path,
                profile=args.profile,
                inferred_os=args.template_os,
                var_assignments=args.var,
            )
        raise ValueError(f"unsupported standalone command '{args.command}'")


def _assign_nested_value(target: dict[str, object], key_parts: Sequence[str], value: str) -> None:
    current = target
    for key in key_parts[:-1]:
        nested = current.get(key)
        if not isinstance(nested, dict):
            nested = {}
            current[key] = nested
        current = nested
    current[key_parts[-1]] = value


def _template_vars_from_dotman_env(environ: dict[str, str]) -> dict[str, object]:
    variables: dict[str, object] = {}
    for key, value in environ.items():
        if not key.startswith("DOTMAN_VAR_"):
            continue
        path_parts = [part for part in key.removeprefix("DOTMAN_VAR_").split("__") if part]
        if path_parts:
            _assign_nested_value(variables, path_parts, value)
    return variables


def _apply_template_var_assignments(
    variables: dict[str, object],
    assignments: Sequence[str],
) -> dict[str, object]:
    for assignment in assignments:
        if "=" not in assignment:
            raise ValueError(f"invalid --var assignment '{assignment}'; expected <key=value>")
        dotted_key, value = assignment.split("=", 1)
        key_parts = [part for part in dotted_key.split(".") if part]
        if not key_parts:
            raise ValueError(f"invalid --var assignment '{assignment}'; expected <key=value>")
        _assign_nested_value(variables, key_parts, value)
    return variables


def run_jinja_render(
    *,
    source_path: str,
    profile: str | None,
    inferred_os: str | None,
    var_assignments: Sequence[str],
) -> int:
    path = Path(source_path)
    variables = _template_vars_from_dotman_env(dict(os.environ))
    _apply_template_var_assignments(variables, var_assignments)
    if not path.exists():
        raise JinjaRenderError(path=path, detail="source path does not exist")
    context = build_template_context(
        variables,
        profile=profile or os.environ.get("DOTMAN_PROFILE") or "default",
        inferred_os=inferred_os or os.environ.get("DOTMAN_OS") or sys.platform,
    )
    rendered, _projection_kind = render_template_file(path, context)
    sys.stdout.write(rendered.decode("utf-8"))
    return 0


def _build_patch_capture_cli_env(
    *,
    repo_path: Path,
    variables: dict[str, object],
    profile: str,
    inferred_os: str,
) -> dict[str, str]:
    env = {
        "DOTMAN_REPO_PATH": str(repo_path),
        "DOTMAN_SOURCE": str(repo_path),
        "DOTMAN_PROFILE": profile,
        "DOTMAN_OS": inferred_os,
    }
    for flat_key, value in flatten_vars(variables).items():
        env[f"DOTMAN_VAR_{flat_key}"] = value
    return env


def _build_cli_patch_capture_projector(
    *,
    repo_path: Path,
    render_command: str,
    variables: dict[str, object],
    profile: str,
    inferred_os: str,
):
    if render_command == "jinja":
        context = build_template_context(
            variables,
            profile=profile,
            inferred_os=inferred_os,
        )

        def project(candidate_bytes: bytes) -> bytes:
            return render_template_string(
                candidate_bytes.decode("utf-8"),
                context,
                base_dir=repo_path.parent,
                source_path=repo_path,
            ).encode("utf-8")

        return project

    base_env = _build_patch_capture_cli_env(
        repo_path=repo_path,
        variables=variables,
        profile=profile,
        inferred_os=inferred_os,
    )

    def project(candidate_bytes: bytes) -> bytes:
        # The renderer may resolve sibling files relative to $DOTMAN_SOURCE, so
        # the transient candidate must stay beside the real repository source.
        with tempfile.NamedTemporaryFile(
            prefix=f".dotman-patch-{repo_path.stem}-",
            suffix=repo_path.suffix,
            dir=repo_path.parent,
            delete=False,
        ) as temp_source:
            temp_source.write(candidate_bytes)
            temp_source_path = Path(temp_source.name)
        try:
            temp_source_text = str(temp_source_path)
            result = current_command_runtime().run(
                CommandRequest(
                    command=ShellCommand(render_command),
                    cwd=repo_path.parent,
                    env={
                        **base_env,
                        "DOTMAN_REPO_PATH": temp_source_text,
                        "DOTMAN_SOURCE": temp_source_text,
                    },
                )
            )
            raise_for_command_interruption(result)
            if result.exit_code != 0:
                stderr = result.stderr.decode("utf-8", errors="replace")
                raise ValueError(stderr.strip() or f"render command exited with status {result.exit_code}")
            return result.stdout
        finally:
            temp_source_path.unlink(missing_ok=True)

    return project


def run_patch_capture(
    *,
    repo_path: str,
    render_command: str,
    review_repo_path: str | None,
    review_live_path: str | None,
    profile: str | None,
    inferred_os: str | None,
    var_assignments: Sequence[str],
) -> int:
    resolved_repo_path = Path(repo_path).expanduser().resolve()
    variables = _template_vars_from_dotman_env(dict(os.environ))
    _apply_template_var_assignments(variables, var_assignments)
    resolved_profile = profile or os.environ.get("DOTMAN_PROFILE") or "default"
    resolved_os = inferred_os or os.environ.get("DOTMAN_OS") or sys.platform
    captured = capture_patch(
        repo_path=resolved_repo_path,
        review_repo_path=review_repo_path,
        review_live_path=review_live_path,
        project_repo_bytes=_build_cli_patch_capture_projector(
            repo_path=resolved_repo_path,
            render_command=render_command,
            variables=variables,
            profile=resolved_profile,
            inferred_os=resolved_os,
        ),
    )
    sys.stdout.buffer.write(captured)
    return 0
