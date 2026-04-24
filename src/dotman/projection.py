from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from dotman.capture import BUILTIN_PATCH_CAPTURE
from dotman.collisions import validate_reserved_path_conflicts, validate_target_collisions
from dotman.config import expand_path
from dotman.file_access import needs_sudo_for_read, read_bytes, sudo_prefix_command
from dotman.ignore import list_directory_files
from dotman.manifest import flatten_vars, merge_ignore_patterns, resolve_sync_policy, sync_policy_allows_operation
from dotman.models import DirectoryPlanItem, HookCommandSpec, PackageSpec, ResolvedPackageSelection, TargetPlan, TargetSpec
from dotman.repository import Repository
from dotman.templates import render_template_file, render_template_string



def plan_targets(
    engine: Any,
    *,
    repo: Repository,
    packages: list[PackageSpec],
    context: dict[str, Any],
    selection: ResolvedPackageSelection,
    operation: str,
    inferred_os: str,
    declaration_package_ids: set[str],
) -> list[TargetPlan]:
    rendered_targets: list[tuple[PackageSpec, TargetSpec, Path, Path, tuple[str, ...], tuple[str, ...], bool, str | None]] = []

    for package in packages:
        if package.id not in declaration_package_ids:
            continue
        for target in (package.targets or {}).values():
            if target.disabled:
                continue
            sync_policy = resolve_sync_policy(package=package, target=target)
            if not sync_policy_allows_operation(sync_policy, operation=operation):
                continue
            if target.source is None or target.path is None:
                raise ValueError(
                    f"target '{package.id}:{target.name}' must define source and path"
                )
            rendered_source = render_template_string(
                target.source,
                context,
                base_dir=target.declared_in,
                source_path=target.declared_in,
            )
            rendered_path = render_template_string(
                target.path,
                context,
                base_dir=target.declared_in,
                source_path=target.declared_in,
            )
            repo_path = (target.declared_in / rendered_source).resolve()
            live_path = expand_path(rendered_path, dereference=False)
            live_path_is_symlink = operation == "push" and live_path.is_symlink()
            live_path_symlink_target = os.readlink(live_path) if live_path_is_symlink else None
            push_ignore = merge_ignore_patterns(repo.ignore_defaults.push, target.push_ignore or ())
            pull_ignore = merge_ignore_patterns(repo.ignore_defaults.pull, target.pull_ignore or ())
            rendered_targets.append(
                (
                    package,
                    target,
                    repo_path,
                    live_path,
                    push_ignore,
                    pull_ignore,
                    live_path_is_symlink,
                    live_path_symlink_target,
                )
            )

    validate_target_collisions(rendered_targets)
    validate_reserved_path_conflicts(engine, packages, rendered_targets, context)

    plans: list[TargetPlan] = []
    for package, target, repo_path, live_path, push_ignore, pull_ignore, live_path_is_symlink, live_path_symlink_target in rendered_targets:
        target_kind = infer_target_kind(repo_path=repo_path, live_path=live_path)
        render_command = (
            render_template_string(target.render, context, base_dir=target.declared_in, source_path=target.declared_in)
            if target.render is not None
            else None
        )
        capture_command = (
            render_template_string(target.capture, context, base_dir=target.declared_in, source_path=target.declared_in)
            if target.capture is not None
            else None
        )
        reconcile = (
            HookCommandSpec(
                run=render_template_string(target.reconcile.run, context, base_dir=target.declared_in, source_path=target.declared_in),
                io=target.reconcile.io,
                privileged=target.reconcile.privileged,
            )
            if target.reconcile is not None
            else None
        )
        command_env = build_target_command_env(
            repo=repo,
            package=package,
            target=target,
            repo_path=repo_path,
            live_path=live_path,
            selection=selection,
            operation=operation,
            inferred_os=inferred_os,
            context=context,
        )
        if target_kind == "unknown":
            plans.append(
                TargetPlan(
                    package_id=package.id,
                    target_name=target.name,
                    repo_path=repo_path,
                    live_path=live_path,
                    action="noop",
                    target_kind="unknown",
                    projection_kind="unknown",
                    render_command=render_command,
                    capture_command=capture_command,
                    reconcile=reconcile,
                    live_path_is_symlink=live_path_is_symlink,
                    live_path_symlink_target=live_path_symlink_target,
                    file_symlink_mode=engine.config.file_symlink_mode,
                    dir_symlink_mode=engine.config.dir_symlink_mode,
                    pull_view_repo=target.pull_view_repo or "raw",
                    pull_view_live=target.pull_view_live or default_pull_view_live(capture_command),
                    push_ignore=push_ignore,
                    pull_ignore=pull_ignore,
                    chmod=target.chmod,
                    command_cwd=target.declared_in,
                    command_env=command_env,
                )
            )
            continue

        validate_patch_capture_target(
            package=package,
            target=target,
            target_kind=target_kind,
            render_command=render_command,
        )
        if target_kind == "directory":
            action, directory_items = plan_directory_action(
                repo_path,
                live_path,
                push_ignore,
                pull_ignore,
                operation=operation,
            )
            plans.append(
                TargetPlan(
                    package_id=package.id,
                    target_name=target.name,
                    repo_path=repo_path,
                    live_path=live_path,
                    action=action,
                    target_kind="directory",
                    projection_kind="directory",
                    render_command=render_command,
                    capture_command=capture_command,
                    reconcile=reconcile,
                    live_path_is_symlink=live_path_is_symlink,
                    live_path_symlink_target=live_path_symlink_target,
                    file_symlink_mode=engine.config.file_symlink_mode,
                    dir_symlink_mode=engine.config.dir_symlink_mode,
                    pull_view_repo=target.pull_view_repo or "raw",
                    pull_view_live=target.pull_view_live or default_pull_view_live(capture_command),
                    push_ignore=push_ignore,
                    pull_ignore=pull_ignore,
                    chmod=target.chmod,
                    command_cwd=target.declared_in,
                    command_env=command_env,
                    directory_items=directory_items,
                )
            )
            continue

        projection_error: str | None = None
        desired_bytes: bytes | None = None
        projection_kind = "raw"
        try:
            if operation == "push" or repo_path.exists():
                desired_bytes, projection_kind = project_repo_file(
                    engine,
                    repo=repo,
                    package=package,
                    target=target,
                    repo_path=repo_path,
                    live_path=live_path,
                    render_command=render_command,
                    context=context,
                    selection=selection,
                    operation=operation,
                    inferred_os=inferred_os,
                )
        except ValueError as exc:
            if render_command == "jinja":
                raise
            if render_command is not None and operation == "push" and not live_path.exists():
                projection_error = str(exc)
                projection_kind = "command"
            else:
                raise
        pull_view_repo = target.pull_view_repo or "raw"
        pull_view_live = target.pull_view_live or default_pull_view_live(capture_command)
        action = plan_file_action(
            engine,
            repo=repo,
            package=package,
            target=target,
            repo_path=repo_path,
            live_path=live_path,
            desired_bytes=desired_bytes,
            render_command=render_command,
            capture_command=capture_command,
            context=context,
            selection=selection,
            operation=operation,
            inferred_os=inferred_os,
            pull_view_repo=pull_view_repo,
            pull_view_live=pull_view_live,
        )
        review_before_bytes, review_after_bytes = build_file_review_bytes(
            engine,
            repo=repo,
            package=package,
            target=target,
            repo_path=repo_path,
            live_path=live_path,
            desired_bytes=desired_bytes,
            render_command=render_command,
            capture_command=capture_command,
            context=context,
            selection=selection,
            operation=operation,
            inferred_os=inferred_os,
            pull_view_repo=pull_view_repo,
            pull_view_live=pull_view_live,
        )
        desired_text = None
        if desired_bytes is not None:
            try:
                desired_text = desired_bytes.decode("utf-8")
            except UnicodeDecodeError:
                desired_text = None
        plans.append(
            TargetPlan(
                package_id=package.id,
                target_name=target.name,
                repo_path=repo_path,
                live_path=live_path,
                action=action,
                target_kind="file",
                projection_kind=projection_kind,
                desired_text=desired_text,
                render_command=render_command,
                capture_command=capture_command,
                reconcile=reconcile,
                projection_error=projection_error,
                live_path_is_symlink=live_path_is_symlink,
                live_path_symlink_target=live_path_symlink_target,
                file_symlink_mode=engine.config.file_symlink_mode,
                dir_symlink_mode=engine.config.dir_symlink_mode,
                pull_view_repo=pull_view_repo,
                pull_view_live=pull_view_live,
                push_ignore=push_ignore,
                pull_ignore=pull_ignore,
                chmod=target.chmod,
                command_cwd=target.declared_in,
                command_env=command_env,
                desired_bytes=desired_bytes,
                review_before_bytes=review_before_bytes,
                review_after_bytes=review_after_bytes,
            )
        )
    return plans



def infer_target_kind(*, repo_path: Path, live_path: Path) -> str:
    if repo_path.is_dir():
        return "directory"
    if live_path.is_dir():
        # Directory targets should still be recognized when the repo source tree
        # does not exist yet but the live path clearly shows a directory.
        return "directory"
    if repo_path.exists() or live_path.exists():
        return "file"
    return "unknown"



def default_pull_view_live(capture_command: str | None) -> str:
    if capture_command == BUILTIN_PATCH_CAPTURE:
        return "raw"
    if capture_command is not None:
        return "capture"
    return "raw"



def validate_patch_capture_target(
    *,
    package: PackageSpec,
    target: TargetSpec,
    target_kind: str,
    render_command: str | None,
) -> None:
    if target.capture != BUILTIN_PATCH_CAPTURE:
        return
    if target_kind != "file":
        raise ValueError(f'capture = "patch" requires a file target for {package.id}:{target.name}')
    if render_command is None:
        raise ValueError(f'capture = "patch" requires render for {package.id}:{target.name}')
    if target.pull_view_repo is None or target.pull_view_live is None:
        raise ValueError(
            f'capture = "patch" requires pull_view_repo = "render" and pull_view_live = "raw" for '
            f"{package.id}:{target.name}"
        )
    if target.pull_view_repo != "render" or target.pull_view_live != "raw":
        raise ValueError(
            f'capture = "patch" requires pull_view_repo = "render" and pull_view_live = "raw" for '
            f"{package.id}:{target.name}"
        )



def project_repo_file(
    engine: Any,
    *,
    repo: Repository,
    package: PackageSpec,
    target: TargetSpec,
    repo_path: Path,
    live_path: Path,
    render_command: str | None,
    context: dict[str, Any],
    selection: ResolvedPackageSelection,
    operation: str,
    inferred_os: str,
) -> tuple[bytes, str]:
    try:
        if render_command == "jinja":
            return render_template_file(repo_path, context)
        if render_command:
            return (
                run_command_projection(
                    engine,
                    repo=repo,
                    package=package,
                    target=target,
                    repo_path=repo_path,
                    live_path=live_path,
                    command=render_command,
                    selection=selection,
                    operation=operation,
                    inferred_os=inferred_os,
                    context=context,
                ),
                "command",
            )
        return read_bytes(repo_path), "raw"
    except FileNotFoundError as exc:
        raise ValueError(
            f"repo source path does not exist for target '{package.id}:{target.name}': {repo_path}"
        ) from exc



def plan_directory_action(
    repo_path: Path,
    live_path: Path,
    push_ignore: tuple[str, ...],
    pull_ignore: tuple[str, ...],
    *,
    operation: str,
) -> tuple[str, tuple[DirectoryPlanItem, ...]]:
    desired_files = list_directory_files(repo_path, push_ignore)
    live_exists = live_path.exists()
    live_files = list_directory_files(live_path, pull_ignore) if live_exists else {}
    desired_rel_paths = set(desired_files)
    live_rel_paths = set(live_files)
    directory_items: list[DirectoryPlanItem] = []

    if operation == "push":
        for relative_path in sorted(desired_rel_paths - live_rel_paths):
            directory_items.append(
                DirectoryPlanItem(
                    relative_path=relative_path,
                    action="create",
                    repo_path=desired_files[relative_path],
                    live_path=live_path / relative_path,
                )
            )
        for relative_path in sorted(live_rel_paths - desired_rel_paths):
            directory_items.append(
                DirectoryPlanItem(
                    relative_path=relative_path,
                    action="delete",
                    repo_path=repo_path / relative_path,
                    live_path=live_files[relative_path],
                )
            )
        for relative_path in sorted(desired_rel_paths & live_rel_paths):
            source_path = desired_files[relative_path]
            live_file = live_files[relative_path]
            desired_bytes = read_bytes(source_path)
            if desired_bytes != read_bytes(live_file):
                directory_items.append(
                    DirectoryPlanItem(
                        relative_path=relative_path,
                        action="update",
                        repo_path=source_path,
                        live_path=live_file,
                    )
                )
        if not directory_items:
            return "noop", ()
        ordered_items = tuple(sorted(directory_items, key=lambda item: item.relative_path))
        if not desired_rel_paths:
            # Push has no repo-side files to keep, so any tracked live files are being removed.
            return "delete", ordered_items
        return ("create" if not live_exists else "update"), ordered_items

    for relative_path in sorted(desired_rel_paths - live_rel_paths):
        directory_items.append(
            DirectoryPlanItem(
                relative_path=relative_path,
                action="delete",
                repo_path=desired_files[relative_path],
                live_path=live_path / relative_path,
            )
        )
    for relative_path in sorted(live_rel_paths - desired_rel_paths):
        directory_items.append(
            DirectoryPlanItem(
                relative_path=relative_path,
                action="create",
                repo_path=repo_path / relative_path,
                live_path=live_files[relative_path],
            )
        )
    for relative_path in sorted(desired_rel_paths & live_rel_paths):
        source_path = desired_files[relative_path]
        live_file = live_files[relative_path]
        desired_bytes = read_bytes(source_path)
        if desired_bytes != read_bytes(live_file):
            directory_items.append(
                DirectoryPlanItem(
                    relative_path=relative_path,
                    action="update",
                    repo_path=source_path,
                    live_path=live_file,
                )
            )

    if not directory_items:
        return "noop", ()
    ordered_items = tuple(sorted(directory_items, key=lambda item: item.relative_path))
    return ("delete" if not live_exists else "update"), ordered_items



def plan_file_action(
    engine: Any,
    *,
    repo: Repository,
    package: PackageSpec,
    target: TargetSpec,
    repo_path: Path,
    live_path: Path,
    desired_bytes: bytes | None,
    render_command: str | None,
    capture_command: str | None,
    context: dict[str, Any],
    selection: ResolvedPackageSelection,
    operation: str,
    inferred_os: str,
    pull_view_repo: str,
    pull_view_live: str,
) -> str:
    if operation == "push":
        if not live_path.exists():
            return "create"
        if desired_bytes is None:
            return "unknown"
        return "noop" if desired_bytes == read_bytes(live_path) else "update"

    repo_exists = repo_path.exists()
    live_exists = live_path.exists()
    if not repo_exists and not live_exists:
        return "noop"
    if not live_exists:
        return "delete"
    if not repo_exists:
        return "create"
    repo_bytes = pull_view_bytes(
        engine,
        repo=repo,
        package=package,
        target=target,
        repo_path=repo_path,
        live_path=live_path,
        view=pull_view_repo,
        repo_side=True,
        render_command=render_command,
        capture_command=capture_command,
        context=context,
        selection=selection,
        operation=operation,
        inferred_os=inferred_os,
    )
    live_bytes = pull_view_bytes(
        engine,
        repo=repo,
        package=package,
        target=target,
        repo_path=repo_path,
        live_path=live_path,
        view=pull_view_live,
        repo_side=False,
        render_command=render_command,
        capture_command=capture_command,
        context=context,
        selection=selection,
        operation=operation,
        inferred_os=inferred_os,
    )
    return "noop" if repo_bytes == live_bytes else "update"



def build_file_review_bytes(
    engine: Any,
    *,
    repo: Repository,
    package: PackageSpec,
    target: TargetSpec,
    repo_path: Path,
    live_path: Path,
    desired_bytes: bytes | None,
    render_command: str | None,
    capture_command: str | None,
    context: dict[str, Any],
    selection: ResolvedPackageSelection,
    operation: str,
    inferred_os: str,
    pull_view_repo: str,
    pull_view_live: str,
) -> tuple[bytes | None, bytes | None]:
    if operation == "push":
        try:
            live_bytes = read_bytes(live_path)
        except FileNotFoundError:
            live_bytes = b""
        return live_bytes, desired_bytes

    repo_bytes = pull_view_bytes(
        engine,
        repo=repo,
        package=package,
        target=target,
        repo_path=repo_path,
        live_path=live_path,
        view=pull_view_repo,
        repo_side=True,
        render_command=render_command,
        capture_command=capture_command,
        context=context,
        selection=selection,
        operation=operation,
        inferred_os=inferred_os,
    )
    if not live_path.exists():
        return repo_bytes, b""
    live_bytes = pull_view_bytes(
        engine,
        repo=repo,
        package=package,
        target=target,
        repo_path=repo_path,
        live_path=live_path,
        view=pull_view_live,
        repo_side=False,
        render_command=render_command,
        capture_command=capture_command,
        context=context,
        selection=selection,
        operation=operation,
        inferred_os=inferred_os,
    )
    return repo_bytes, live_bytes



def pull_view_bytes(
    engine: Any,
    *,
    repo: Repository,
    package: PackageSpec,
    target: TargetSpec,
    repo_path: Path,
    live_path: Path,
    view: str,
    repo_side: bool,
    render_command: str | None,
    capture_command: str | None,
    context: dict[str, Any],
    selection: ResolvedPackageSelection,
    operation: str,
    inferred_os: str,
) -> bytes:
    if view == "raw":
        if repo_side and not repo_path.exists():
            # Missing repo source during pull means "nothing captured yet", not an error.
            return b""
        return read_bytes(repo_path) if repo_side else read_bytes(live_path)
    if view == "render":
        desired_bytes, _projection = project_repo_file(
            engine,
            repo=repo,
            package=package,
            target=target,
            repo_path=repo_path,
            live_path=live_path,
            render_command=render_command,
            context=context,
            selection=selection,
            operation=operation,
            inferred_os=inferred_os,
        )
        return desired_bytes
    if view == "capture":
        if capture_command == BUILTIN_PATCH_CAPTURE:
            raise ValueError(
                f"target '{package.id}:{target.name}' reserves capture = 'patch' for reverse capture and does not expose a capture view"
            )
        if capture_command is None:
            raise ValueError(f"target '{package.id}:{target.name}' does not define capture")
        return run_command_projection(
            engine,
            repo=repo,
            package=package,
            target=target,
            repo_path=repo_path,
            live_path=live_path,
            command=capture_command,
            selection=selection,
            operation=operation,
            inferred_os=inferred_os,
            context=context,
        )
    command = render_template_string(view, context, base_dir=target.declared_in, source_path=target.declared_in)
    return run_command_projection(
        engine,
        repo=repo,
        package=package,
        target=target,
        repo_path=repo_path,
        live_path=live_path,
        command=command,
        selection=selection,
        operation=operation,
        inferred_os=inferred_os,
        context=context,
    )



def run_command_projection(
    engine: Any,
    *,
    repo: Repository,
    package: PackageSpec,
    target: TargetSpec,
    repo_path: Path,
    live_path: Path,
    command: str,
    selection: ResolvedPackageSelection,
    operation: str,
    inferred_os: str,
    context: dict[str, Any],
) -> bytes:
    env = os.environ.copy()
    env.update(
        engine._build_target_command_env(
            repo=repo,
            package=package,
            target=target,
            repo_path=repo_path,
            live_path=live_path,
            selection=selection,
            operation=operation,
            inferred_os=inferred_os,
            context=context,
        )
    )
    if needs_sudo_for_read(live_path):
        command = sudo_prefix_command(command)
    completed = subprocess.run(
        command,
        cwd=str(target.declared_in),
        env=env,
        shell=True,
        executable="/bin/sh",
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace")
        raise ValueError(f"command projection failed for {package.id}:{target.name}: {stderr.strip()}")
    return completed.stdout



def build_target_command_env(
    *,
    repo: Repository,
    package: PackageSpec,
    target: TargetSpec,
    repo_path: Path,
    live_path: Path,
    selection: ResolvedPackageSelection,
    operation: str,
    inferred_os: str,
    context: dict[str, Any],
) -> dict[str, str]:
    env = {
        "DOTMAN_REPO_NAME": repo.config.name,
        "DOTMAN_REPO_ROOT": str(repo.root),
        "DOTMAN_STATE_PATH": str(repo.config.state_path),
        "DOTMAN_PACKAGE_ID": package.id,
        "DOTMAN_PACKAGE_ROOT": str(package.package_root),
        "DOTMAN_TARGET_NAME": target.name,
        "DOTMAN_TARGET_REPO_PATH": str(repo_path),
        "DOTMAN_TARGET_LIVE_PATH": str(live_path),
        "DOTMAN_REPO_PATH": str(repo_path),
        "DOTMAN_SOURCE": str(repo_path),
        "DOTMAN_LIVE_PATH": str(live_path),
        "DOTMAN_PROFILE": selection.requested_profile,
        "DOTMAN_OPERATION": operation,
        "DOTMAN_OS": inferred_os,
    }
    for flat_key, value in flatten_vars(context["vars"]).items():
        env[f"DOTMAN_VAR_{flat_key}"] = value
    return env


def build_package_hook_env(
    *,
    repo: Repository,
    package: PackageSpec,
    selection: ResolvedPackageSelection,
    operation: str,
    inferred_os: str,
    context: dict[str, Any],
) -> dict[str, str]:
    env = {
        "DOTMAN_REPO_NAME": repo.config.name,
        "DOTMAN_REPO_ROOT": str(repo.root),
        "DOTMAN_STATE_PATH": str(repo.config.state_path),
        "DOTMAN_PACKAGE_ID": package.id,
        "DOTMAN_PACKAGE_ROOT": str(package.package_root),
        "DOTMAN_PROFILE": selection.requested_profile,
        "DOTMAN_OPERATION": operation,
        "DOTMAN_OS": inferred_os,
    }
    for flat_key, value in flatten_vars(context["vars"]).items():
        env[f"DOTMAN_VAR_{flat_key}"] = value
    return env


def build_repo_hook_env(
    *,
    repo: Repository,
    operation: str,
    context: dict[str, Any],
) -> dict[str, str]:
    env = {
        "DOTMAN_REPO_NAME": repo.config.name,
        "DOTMAN_REPO_ROOT": str(repo.root),
        "DOTMAN_STATE_PATH": str(repo.config.state_path),
        "DOTMAN_OPERATION": operation,
    }
    for flat_key, value in flatten_vars(context.get("vars", {})).items():
        env[f"DOTMAN_VAR_{flat_key}"] = value
    return env
