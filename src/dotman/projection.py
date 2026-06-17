from __future__ import annotations

import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from dotman.capture import BUILTIN_PATCH_CAPTURE
from dotman.collisions import validate_reserved_path_conflicts, validate_target_collisions
from dotman.config import expand_path
from dotman.file_access import needs_sudo_for_read, read_bytes, sudo_prefix_command
from dotman.ignore import list_directory_files
from dotman.manifest import (
    flatten_vars,
    merge_ignore_patterns,
    resolve_sync_policy,
    sync_policy_allows_operation,
    sync_policy_deletes_on_push,
)
from dotman.models import DirectoryPlanItem, HookCommandSpec, PackageSpec, ResolvedPackageSelection, TargetPathRule, TargetPlan, TargetSpec
from dotman.repository import Repository
from dotman.templates import render_template_file, render_template_string


@dataclass(frozen=True)
class TargetMetadata:
    repo_name: str
    package_id: str
    bound_profile: str | None
    requested_profile: str
    target_name: str
    repo_path: Path
    live_path: Path
    probe_command: str | None
    render_command: str | None
    capture_command: str | None
    reconcile: HookCommandSpec | None
    pull_view_repo: str
    pull_view_live: str
    push_ignore: tuple[str, ...]
    pull_ignore: tuple[str, ...]
    skip_markers: tuple[str, ...]
    chmod: str | None
    path_rules: tuple[TargetPathRule, ...]
    command_cwd: Path
    command_env: dict[str, str]
    package: PackageSpec
    target: TargetSpec
    live_path_is_symlink: bool = False
    live_path_symlink_target: str | None = None


def _metadata_collision_tuple(metadata: TargetMetadata):
    return (
        metadata.package,
        metadata.target,
        metadata.repo_path,
        metadata.live_path,
        metadata.push_ignore,
        metadata.pull_ignore,
        metadata.live_path_is_symlink,
        metadata.live_path_symlink_target,
    )


def target_claims_path(target: TargetSpec) -> bool:
    return target.probe is None


def validate_probe_target_config(*, package: PackageSpec, target: TargetSpec) -> None:
    if target.probe is None:
        return
    forbidden_probe_fields = {
        "source": target.source,
        "path": target.path,
        "type": target.target_type,
        "chmod": target.chmod,
        "render": target.render,
        "capture": target.capture,
        "reconcile": target.reconcile,
        "pull_view_repo": target.pull_view_repo,
        "pull_view_live": target.pull_view_live,
        "push_ignore": target.push_ignore,
        "pull_ignore": target.pull_ignore,
        "path_rules": target.path_rules or None,
    }
    forbidden = sorted(name for name, value in forbidden_probe_fields.items() if value is not None)
    if forbidden:
        raise ValueError(
            f"target '{package.id}:{target.name}' uses probe and must not define: "
            + ", ".join(forbidden)
        )


def build_target_metadata(
    engine: Any,
    *,
    repo: Repository,
    packages: list[PackageSpec],
    context: dict[str, Any],
    selection: ResolvedPackageSelection,
    operation: str,
    inferred_os: str,
    declaration_package_ids: set[str],
    inspect_live_symlinks: bool = True,
    validate_declaration_conflicts: bool = True,
) -> list[TargetMetadata]:
    metadata_targets: list[TargetMetadata] = []

    for package in packages:
        if package.id not in declaration_package_ids:
            continue
        for target in (package.targets or {}).values():
            if target.disabled:
                continue
            sync_policy = resolve_sync_policy(package=package, target=target)
            if not sync_policy_allows_operation(sync_policy, operation=operation):
                continue
            if target.probe is not None:
                validate_probe_target_config(package=package, target=target)
                probe_command = render_template_string(
                    target.probe,
                    context,
                    base_dir=target.declared_in,
                    source_path=target.declared_in,
                )
                placeholder_path = target.declared_in.resolve()
                metadata_targets.append(
                    TargetMetadata(
                        repo_name=repo.config.name,
                        package_id=package.id,
                        bound_profile=selection.bound_profile,
                        requested_profile=selection.requested_profile,
                        target_name=target.name,
                        repo_path=placeholder_path,
                        live_path=placeholder_path,
                        probe_command=probe_command,
                        render_command=None,
                        capture_command=None,
                        reconcile=None,
                        pull_view_repo="raw",
                        pull_view_live="raw",
                        push_ignore=(),
                        pull_ignore=(),
                        skip_markers=(),
                        chmod=None,
                        path_rules=(),
                        command_cwd=target.declared_in,
                        command_env=build_target_command_env(
                            repo=repo,
                            package=package,
                            target=target,
                            repo_path=placeholder_path,
                            live_path=placeholder_path,
                            selection=selection,
                            operation=operation,
                            inferred_os=inferred_os,
                            context=context,
                        ),
                        package=package,
                        target=target,
                    )
                )
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
            live_path_is_symlink = inspect_live_symlinks and operation == "push" and live_path.is_symlink()
            live_path_symlink_target = os.readlink(live_path) if live_path_is_symlink else None
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
                    elevation=target.reconcile.elevation,
                )
                if target.reconcile is not None
                else None
            )
            push_ignore = merge_ignore_patterns(repo.ignore_defaults.push, target.push_ignore or ())
            pull_ignore = merge_ignore_patterns(repo.ignore_defaults.pull, target.pull_ignore or ())
            skip_markers = repo.ignore_defaults.skip_markers
            path_rules = render_target_path_rules(target.path_rules, context=context, base_dir=target.declared_in)
            metadata_targets.append(
                TargetMetadata(
                    repo_name=repo.config.name,
                    package_id=package.id,
                    bound_profile=selection.bound_profile,
                    requested_profile=selection.requested_profile,
                    target_name=target.name,
                    repo_path=repo_path,
                    live_path=live_path,
                    probe_command=None,
                    render_command=render_command,
                    capture_command=capture_command,
                    reconcile=reconcile,
                    pull_view_repo=target.pull_view_repo or "raw",
                    pull_view_live=target.pull_view_live or default_pull_view_live(capture_command),
                    push_ignore=push_ignore,
                    pull_ignore=pull_ignore,
                    skip_markers=skip_markers,
                    chmod=target.chmod,
                    path_rules=path_rules,
                    command_cwd=target.declared_in,
                    command_env=build_target_command_env(
                        repo=repo,
                        package=package,
                        target=target,
                        repo_path=repo_path,
                        live_path=live_path,
                        selection=selection,
                        operation=operation,
                        inferred_os=inferred_os,
                        context=context,
                    ),
                    package=package,
                    target=target,
                    live_path_is_symlink=live_path_is_symlink,
                    live_path_symlink_target=live_path_symlink_target,
                )
            )

    if validate_declaration_conflicts:
        rendered_targets = [_metadata_collision_tuple(metadata) for metadata in metadata_targets if target_claims_path(metadata.target)]
        validate_target_collisions(rendered_targets, operation=operation)
        if operation == "push":
            validate_reserved_path_conflicts(engine, packages, rendered_targets, context)
    return metadata_targets


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
    metadata_targets = build_target_metadata(
        engine,
        repo=repo,
        packages=packages,
        context=context,
        selection=selection,
        operation=operation,
        inferred_os=inferred_os,
        declaration_package_ids=declaration_package_ids,
    )

    plans: list[TargetPlan] = []
    for metadata in metadata_targets:
        package = metadata.package
        target = metadata.target
        repo_path = metadata.repo_path
        live_path = metadata.live_path
        if target.probe is not None:
            probe_active = run_probe_command(metadata)
            plans.append(
                TargetPlan(
                    package_id=package.id,
                    target_name=target.name,
                    repo_path=repo_path,
                    live_path=live_path,
                    action="probe" if probe_active else "noop",
                    target_kind="probe",
                    projection_kind="probe",
                    probe_command=metadata.probe_command,
                    command_cwd=metadata.command_cwd,
                    command_env=metadata.command_env,
                )
            )
            continue
        sync_policy = resolve_sync_policy(package=package, target=target)
        target_kind = resolve_target_kind(
            target_type=target.target_type,
            repo_path=repo_path,
            live_path=live_path,
            target_label=f"{package.id}:{target.name}",
            file_symlink_mode=engine.config.file_symlink_mode,
            dir_symlink_mode=engine.config.dir_symlink_mode,
        )
        if metadata.path_rules and target_kind == "file":
            raise ValueError(
                f"target '{package.id}:{target.name}' defines path_rules but is not a directory target"
            )
        render_command = metadata.render_command
        capture_command = metadata.capture_command
        reconcile = metadata.reconcile
        if operation == "push" and sync_policy_deletes_on_push(sync_policy):
            target_kind = resolve_push_only_delete_target_kind(
                target_type=target.target_type,
                repo_path=repo_path,
                live_path=live_path,
                target_label=f"{package.id}:{target.name}",
                file_symlink_mode=engine.config.file_symlink_mode,
                dir_symlink_mode=engine.config.dir_symlink_mode,
            )
            if target_kind == "directory":
                action, directory_items = plan_live_delete_directory_action(
                    repo_path=repo_path,
                    live_path=live_path,
                    push_ignore=metadata.push_ignore,
                    skip_markers=metadata.skip_markers,
                    follow_dir_symlinks=engine.config.dir_symlink_mode == "follow",
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
                        live_path_is_symlink=metadata.live_path_is_symlink,
                        live_path_symlink_target=metadata.live_path_symlink_target,
                        file_symlink_mode=engine.config.file_symlink_mode,
                        dir_symlink_mode=engine.config.dir_symlink_mode,
                        pull_view_repo=metadata.pull_view_repo,
                        pull_view_live=metadata.pull_view_live,
                        push_ignore=metadata.push_ignore,
                        pull_ignore=metadata.pull_ignore,
                        chmod=metadata.chmod,
                        path_rules=metadata.path_rules,
                        command_cwd=metadata.command_cwd,
                        command_env=metadata.command_env,
                        directory_items=directory_items,
                    )
                )
                continue

            action = "delete" if target_kind == "file" and (live_path.exists() or live_path.is_symlink()) else "noop"
            review_before_bytes, review_after_bytes = build_file_review_bytes(
                engine,
                repo=repo,
                package=package,
                target=target,
                repo_path=repo_path,
                live_path=live_path,
                desired_bytes=b"",
                render_command=render_command,
                capture_command=capture_command,
                context=context,
                selection=selection,
                operation=operation,
                inferred_os=inferred_os,
                pull_view_repo=metadata.pull_view_repo,
                pull_view_live=metadata.pull_view_live,
            )
            plans.append(
                TargetPlan(
                    package_id=package.id,
                    target_name=target.name,
                    repo_path=repo_path,
                    live_path=live_path,
                    action=action,
                    target_kind=target_kind,
                    projection_kind="raw" if target_kind != "unknown" else "unknown",
                    render_command=render_command,
                    capture_command=capture_command,
                    reconcile=reconcile,
                    live_path_is_symlink=metadata.live_path_is_symlink,
                    live_path_symlink_target=metadata.live_path_symlink_target,
                    file_symlink_mode=engine.config.file_symlink_mode,
                    dir_symlink_mode=engine.config.dir_symlink_mode,
                    pull_view_repo=metadata.pull_view_repo,
                    pull_view_live=metadata.pull_view_live,
                    push_ignore=metadata.push_ignore,
                    pull_ignore=metadata.pull_ignore,
                    chmod=metadata.chmod,
                    path_rules=metadata.path_rules,
                    command_cwd=metadata.command_cwd,
                    command_env=metadata.command_env,
                    review_before_bytes=review_before_bytes,
                    review_after_bytes=review_after_bytes,
                )
            )
            continue
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
                    live_path_is_symlink=metadata.live_path_is_symlink,
                    live_path_symlink_target=metadata.live_path_symlink_target,
                    file_symlink_mode=engine.config.file_symlink_mode,
                    dir_symlink_mode=engine.config.dir_symlink_mode,
                    pull_view_repo=metadata.pull_view_repo,
                    pull_view_live=metadata.pull_view_live,
                    push_ignore=metadata.push_ignore,
                    pull_ignore=metadata.pull_ignore,
                    chmod=metadata.chmod,
                    path_rules=metadata.path_rules,
                    command_cwd=metadata.command_cwd,
                    command_env=metadata.command_env,
                )
            )
            continue

        validate_patch_capture_target(
            package=package,
            target=target,
            target_kind=target_kind,
            render_command=render_command,
            capture_command=capture_command,
            pull_view_repo=metadata.pull_view_repo,
            pull_view_live=metadata.pull_view_live,
            repo_path=repo_path,
        )
        if target_kind == "directory":
            action, directory_items = plan_directory_action(
                engine,
                repo=repo,
                package=package,
                target=target,
                repo_path=repo_path,
                live_path=live_path,
                push_ignore=metadata.push_ignore,
                pull_ignore=metadata.pull_ignore,
                skip_markers=metadata.skip_markers,
                operation=operation,
                render_command=render_command,
                capture_command=capture_command,
                context=context,
                selection=selection,
                inferred_os=inferred_os,
                pull_view_repo=metadata.pull_view_repo,
                pull_view_live=metadata.pull_view_live,
                path_rules=metadata.path_rules,
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
                    live_path_is_symlink=metadata.live_path_is_symlink,
                    live_path_symlink_target=metadata.live_path_symlink_target,
                    file_symlink_mode=engine.config.file_symlink_mode,
                    dir_symlink_mode=engine.config.dir_symlink_mode,
                    pull_view_repo=metadata.pull_view_repo,
                    pull_view_live=metadata.pull_view_live,
                    push_ignore=metadata.push_ignore,
                    pull_ignore=metadata.pull_ignore,
                    chmod=metadata.chmod,
                    path_rules=metadata.path_rules,
                    command_cwd=metadata.command_cwd,
                    command_env=metadata.command_env,
                    directory_items=directory_items,
                )
            )
            continue

        projection_error: str | None = None
        desired_bytes: bytes | None = None
        projection_kind = projection_kind_for_render_command(render_command)
        try:
            if operation == "push":
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
        pull_view_repo = metadata.pull_view_repo
        pull_view_live = metadata.pull_view_live
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
        action = plan_file_action_from_review_bytes(
            repo_path=repo_path,
            live_path=live_path,
            desired_bytes=desired_bytes,
            review_before_bytes=review_before_bytes,
            review_after_bytes=review_after_bytes,
            operation=operation,
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
                live_path_is_symlink=metadata.live_path_is_symlink,
                live_path_symlink_target=metadata.live_path_symlink_target,
                file_symlink_mode=engine.config.file_symlink_mode,
                dir_symlink_mode=engine.config.dir_symlink_mode,
                pull_view_repo=pull_view_repo,
                pull_view_live=pull_view_live,
                push_ignore=metadata.push_ignore,
                pull_ignore=metadata.pull_ignore,
                chmod=metadata.chmod,
                command_cwd=metadata.command_cwd,
                command_env=metadata.command_env,
                desired_bytes=desired_bytes,
                review_before_bytes=review_before_bytes,
                review_after_bytes=review_after_bytes,
            )
        )
    return plans


def resolve_target_kind(
    *,
    target_type: str | None,
    repo_path: Path,
    live_path: Path,
    target_label: str = "target",
    file_symlink_mode: str = "prompt",
    dir_symlink_mode: str = "fail",
) -> str:
    if target_type is None:
        return infer_target_kind(repo_path=repo_path, live_path=live_path)
    validate_explicit_target_type(
        target_type=target_type,
        repo_path=repo_path,
        live_path=live_path,
        target_label=target_label,
        file_symlink_mode=file_symlink_mode,
        dir_symlink_mode=dir_symlink_mode,
    )
    return target_type


def validate_explicit_target_type(
    *,
    target_type: str,
    repo_path: Path,
    live_path: Path,
    target_label: str,
    file_symlink_mode: str,
    dir_symlink_mode: str,
) -> None:
    live_follow_symlink = target_type == "file" or (
        target_type == "directory" and dir_symlink_mode == "follow"
    )
    path_roles = (
        ("repo source", repo_path, True),
        ("live", live_path, live_follow_symlink),
    )
    for role, path, follow_symlink in path_roles:
        existing_kind = existing_target_path_kind(path, follow_symlink=follow_symlink)
        if existing_kind is not None and existing_kind != target_type:
            raise ValueError(
                f"target '{target_label}' declares type = \"{target_type}\" but {role} path is {existing_kind}: {path}"
            )


def existing_target_path_kind(path: Path, *, follow_symlink: bool = False) -> str | None:
    if path.is_symlink():
        if not follow_symlink:
            return "file"
        resolved_path = path.resolve(strict=False)
        if resolved_path.is_dir():
            return "directory"
        if resolved_path.exists():
            return "file"
        return None
    if path.is_dir():
        return "directory"
    if path.exists():
        return "file"
    return None


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


def resolve_push_only_delete_target_kind(
    *,
    target_type: str | None,
    repo_path: Path,
    live_path: Path,
    target_label: str = "target",
    file_symlink_mode: str = "prompt",
    dir_symlink_mode: str = "fail",
) -> str:
    if target_type is None:
        return infer_push_only_delete_target_kind(
            repo_path=repo_path,
            live_path=live_path,
            dir_symlink_mode=dir_symlink_mode,
        )
    validate_explicit_target_type(
        target_type=target_type,
        repo_path=repo_path,
        live_path=live_path,
        target_label=target_label,
        file_symlink_mode=file_symlink_mode,
        dir_symlink_mode=dir_symlink_mode,
    )
    return target_type


def infer_push_only_delete_target_kind(*, repo_path: Path, live_path: Path, dir_symlink_mode: str = "fail") -> str:
    if live_path.is_symlink():
        if dir_symlink_mode == "follow" and live_path.is_dir():
            return "directory"
        return "file"
    if live_path.is_dir():
        return "directory"
    if live_path.exists():
        return "file"
    if repo_path.is_dir():
        return "directory"
    if repo_path.exists():
        return "file"
    return "unknown"


def default_pull_view_live(capture_command: str | None) -> str:
    if capture_command == BUILTIN_PATCH_CAPTURE:
        return "raw"
    if capture_command is not None:
        return "capture"
    return "raw"


def render_target_path_rules(
    path_rules: tuple[TargetPathRule, ...],
    *,
    context: dict[str, Any],
    base_dir: Path,
) -> tuple[TargetPathRule, ...]:
    rendered_rules: list[TargetPathRule] = []
    for rule in path_rules:
        rendered_rules.append(
            TargetPathRule(
                pattern=rule.pattern,
                chmod=rule.chmod,
                render=render_template_string(rule.render, context, base_dir=base_dir, source_path=base_dir)
                if rule.render is not None
                else None,
                capture=render_template_string(rule.capture, context, base_dir=base_dir, source_path=base_dir)
                if rule.capture is not None
                else None,
                pull_view_repo=rule.pull_view_repo,
                pull_view_live=rule.pull_view_live,
            )
        )
    return tuple(rendered_rules)


def validate_patch_capture_target(
    *,
    package: PackageSpec,
    target: TargetSpec,
    target_kind: str,
    render_command: str | None,
    capture_command: str | None,
    pull_view_repo: str,
    pull_view_live: str,
    repo_path: Path,
) -> None:
    if capture_command != BUILTIN_PATCH_CAPTURE:
        return
    if target_kind == "directory":
        return
    if target_kind != "file":
        raise ValueError(f'capture = "patch" requires a file-like sync unit for {package.id}:{target.name}')
    validate_patch_capture_unit(
        label=f"{package.id}:{target.name}",
        render_command=render_command,
        capture_command=capture_command,
        pull_view_repo=pull_view_repo,
        pull_view_live=pull_view_live,
        repo_path=repo_path,
    )


def validate_patch_capture_unit(
    *,
    label: str,
    render_command: str | None,
    capture_command: str | None,
    pull_view_repo: str,
    pull_view_live: str,
    repo_path: Path | None = None,
) -> None:
    if capture_command != BUILTIN_PATCH_CAPTURE:
        return
    if render_command is None:
        raise ValueError(f'capture = "patch" requires render for {label}')
    if pull_view_repo != "render" or pull_view_live != "raw":
        raise ValueError(
            f'capture = "patch" requires pull_view_repo = "render" and pull_view_live = "raw" for {label}'
        )
    if repo_path is not None and not repo_path.exists():
        raise ValueError(f'capture = "patch" requires existing repo source for {label}')


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
    engine: Any,
    *,
    repo: Repository,
    package: PackageSpec,
    target: TargetSpec,
    repo_path: Path,
    live_path: Path,
    push_ignore: tuple[str, ...],
    pull_ignore: tuple[str, ...],
    skip_markers: tuple[str, ...],
    operation: str,
    render_command: str | None,
    capture_command: str | None,
    context: dict[str, Any],
    selection: ResolvedPackageSelection,
    inferred_os: str,
    pull_view_repo: str,
    pull_view_live: str,
    path_rules: tuple[TargetPathRule, ...] = (),
) -> tuple[str, tuple[DirectoryPlanItem, ...]]:
    # Ignore lists are operation-scoped: an ignored child should disappear from
    # both repo and live scans so planning does not create, update, or delete it.
    operation_ignore = push_ignore if operation == "push" else pull_ignore
    follow_dir_symlinks = engine.config.dir_symlink_mode == "follow"
    desired_files = list_directory_files(
        repo_path,
        operation_ignore,
        skip_markers=skip_markers,
        follow_dir_symlinks=follow_dir_symlinks,
    )
    live_exists = live_path.exists()
    live_files = (
        list_directory_files(
            live_path,
            operation_ignore,
            skip_markers=skip_markers,
            follow_dir_symlinks=follow_dir_symlinks,
        )
        if live_exists
        else {}
    )
    desired_rel_paths = set(desired_files)
    live_rel_paths = set(live_files)
    directory_items: list[DirectoryPlanItem] = []

    if operation == "push":
        for relative_path in sorted(desired_rel_paths - live_rel_paths):
            source_path = desired_files[relative_path]
            child_policy = directory_child_policy(
                relative_path,
                path_rules,
                default_render=render_command,
                default_capture=capture_command,
            )
            child_pull_view_repo, child_pull_view_live = directory_child_pull_views(
                target=target,
                capture_command=child_policy[2],
                target_pull_view_repo=pull_view_repo,
                target_pull_view_live=pull_view_live,
                rule_pull_view_repo=child_policy[3],
                rule_pull_view_live=child_policy[4],
            )
            validate_directory_child_patch_capture(
                package=package,
                target=target,
                relative_path=relative_path,
                render_command=child_policy[1],
                capture_command=child_policy[2],
                pull_view_repo=child_pull_view_repo,
                pull_view_live=child_pull_view_live,
                repo_path=source_path,
            )
            desired_bytes, _projection_kind = project_repo_file(
                engine,
                repo=repo,
                package=package,
                target=target,
                repo_path=source_path,
                live_path=live_path / relative_path,
                render_command=child_policy[1],
                context=context,
                selection=selection,
                operation=operation,
                inferred_os=inferred_os,
            )
            directory_items.append(
                DirectoryPlanItem(
                    relative_path=relative_path,
                    action="create",
                    repo_path=source_path,
                    live_path=live_path / relative_path,
                    chmod=child_policy[0],
                    render_command=child_policy[1],
                    capture_command=child_policy[2],
                    pull_view_repo=child_pull_view_repo,
                    pull_view_live=child_pull_view_live,
                    desired_bytes=desired_bytes,
                    review_before_bytes=b"",
                    review_after_bytes=desired_bytes,
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
            child_policy = directory_child_policy(
                relative_path,
                path_rules,
                default_render=render_command,
                default_capture=capture_command,
            )
            child_pull_view_repo, child_pull_view_live = directory_child_pull_views(
                target=target,
                capture_command=child_policy[2],
                target_pull_view_repo=pull_view_repo,
                target_pull_view_live=pull_view_live,
                rule_pull_view_repo=child_policy[3],
                rule_pull_view_live=child_policy[4],
            )
            validate_directory_child_patch_capture(
                package=package,
                target=target,
                relative_path=relative_path,
                render_command=child_policy[1],
                capture_command=child_policy[2],
                pull_view_repo=child_pull_view_repo,
                pull_view_live=child_pull_view_live,
                repo_path=source_path,
            )
            desired_bytes, _projection_kind = project_repo_file(
                engine,
                repo=repo,
                package=package,
                target=target,
                repo_path=source_path,
                live_path=live_file,
                render_command=child_policy[1],
                context=context,
                selection=selection,
                operation=operation,
                inferred_os=inferred_os,
            )
            live_bytes = read_bytes(live_file)
            desired_chmod = child_policy[0]
            child_chmod_differs = directory_child_chmod_differs(live_file, desired_chmod)
            executable_bit_differs = desired_chmod is None and directory_executable_bit_differs(source_path, live_file)
            if desired_bytes != live_bytes or executable_bit_differs or child_chmod_differs:
                action = (
                    "chmod"
                    if child_chmod_differs and desired_bytes == live_bytes and not executable_bit_differs
                    else "update"
                )
                directory_items.append(
                    DirectoryPlanItem(
                        relative_path=relative_path,
                        action=action,
                        repo_path=source_path,
                        live_path=live_file,
                        chmod=desired_chmod,
                        render_command=child_policy[1],
                        capture_command=child_policy[2],
                        pull_view_repo=child_pull_view_repo,
                        pull_view_live=child_pull_view_live,
                        desired_bytes=desired_bytes,
                        review_before_bytes=live_bytes,
                        review_after_bytes=desired_bytes,
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
        child_policy = directory_child_policy(
            relative_path,
            path_rules,
            default_render=render_command,
            default_capture=capture_command,
        )
        child_pull_view_repo, child_pull_view_live = directory_child_pull_views(
            target=target,
            capture_command=child_policy[2],
            target_pull_view_repo=pull_view_repo,
            target_pull_view_live=pull_view_live,
            rule_pull_view_repo=child_policy[3],
            rule_pull_view_live=child_policy[4],
        )
        validate_directory_child_patch_capture(
            package=package,
            target=target,
            relative_path=relative_path,
            render_command=child_policy[1],
            capture_command=child_policy[2],
            pull_view_repo=child_pull_view_repo,
            pull_view_live=child_pull_view_live,
            repo_path=desired_files[relative_path],
        )
        directory_items.append(
            DirectoryPlanItem(
                relative_path=relative_path,
                action="delete",
                repo_path=desired_files[relative_path],
                live_path=live_path / relative_path,
                render_command=child_policy[1],
                capture_command=child_policy[2],
                pull_view_repo=child_pull_view_repo,
                pull_view_live=child_pull_view_live,
            )
        )
    for relative_path in sorted(live_rel_paths - desired_rel_paths):
        child_policy = directory_child_policy(
            relative_path,
            path_rules,
            default_render=render_command,
            default_capture=capture_command,
        )
        child_pull_view_repo, child_pull_view_live = directory_child_pull_views(
            target=target,
            capture_command=child_policy[2],
            target_pull_view_repo=pull_view_repo,
            target_pull_view_live=pull_view_live,
            rule_pull_view_repo=child_policy[3],
            rule_pull_view_live=child_policy[4],
        )
        validate_directory_child_patch_capture(
            package=package,
            target=target,
            relative_path=relative_path,
            render_command=child_policy[1],
            capture_command=child_policy[2],
            pull_view_repo=child_pull_view_repo,
            pull_view_live=child_pull_view_live,
            repo_path=repo_path / relative_path,
        )
        directory_items.append(
            DirectoryPlanItem(
                relative_path=relative_path,
                action="create",
                repo_path=repo_path / relative_path,
                live_path=live_files[relative_path],
                render_command=child_policy[1],
                capture_command=child_policy[2],
                pull_view_repo=child_pull_view_repo,
                pull_view_live=child_pull_view_live,
            )
        )
    for relative_path in sorted(desired_rel_paths & live_rel_paths):
        source_path = desired_files[relative_path]
        live_file = live_files[relative_path]
        child_policy = directory_child_policy(
            relative_path,
            path_rules,
            default_render=render_command,
            default_capture=capture_command,
        )
        child_pull_view_repo, child_pull_view_live = directory_child_pull_views(
            target=target,
            capture_command=child_policy[2],
            target_pull_view_repo=pull_view_repo,
            target_pull_view_live=pull_view_live,
            rule_pull_view_repo=child_policy[3],
            rule_pull_view_live=child_policy[4],
        )
        validate_directory_child_patch_capture(
            package=package,
            target=target,
            relative_path=relative_path,
            render_command=child_policy[1],
            capture_command=child_policy[2],
            pull_view_repo=child_pull_view_repo,
            pull_view_live=child_pull_view_live,
            repo_path=source_path,
        )
        repo_bytes = pull_view_bytes(
            engine,
            repo=repo,
            package=package,
            target=target,
            repo_path=source_path,
            live_path=live_file,
            view=child_pull_view_repo,
            repo_side=True,
            render_command=child_policy[1],
            capture_command=child_policy[2],
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
            repo_path=source_path,
            live_path=live_file,
            view=child_pull_view_live,
            repo_side=False,
            render_command=child_policy[1],
            capture_command=child_policy[2],
            context=context,
            selection=selection,
            operation=operation,
            inferred_os=inferred_os,
        )
        if repo_bytes != live_bytes or directory_executable_bit_differs(source_path, live_file):
            directory_items.append(
                DirectoryPlanItem(
                    relative_path=relative_path,
                    action="update",
                    repo_path=source_path,
                    live_path=live_file,
                    render_command=child_policy[1],
                    capture_command=child_policy[2],
                    pull_view_repo=child_pull_view_repo,
                    pull_view_live=child_pull_view_live,
                    review_before_bytes=repo_bytes,
                    review_after_bytes=live_bytes,
                )
            )

    if not directory_items:
        return "noop", ()
    ordered_items = tuple(sorted(directory_items, key=lambda item: item.relative_path))
    return ("delete" if not live_exists else "update"), ordered_items


def validate_directory_child_patch_capture(
    *,
    package: PackageSpec,
    target: TargetSpec,
    relative_path: str,
    render_command: str | None,
    capture_command: str | None,
    pull_view_repo: str,
    pull_view_live: str,
    repo_path: Path | None = None,
) -> None:
    label = f"{package.id}:{target.name}:{relative_path}"
    validate_patch_capture_unit(
        label=label,
        render_command=render_command,
        capture_command=capture_command,
        pull_view_repo=pull_view_repo,
        pull_view_live=pull_view_live,
        repo_path=repo_path,
    )


def directory_executable_bit_differs(repo_file: Path, live_file: Path) -> bool:
    repo_mode = file_permission_mode(repo_file)
    live_mode = file_permission_mode(live_file)
    return repo_mode is not None and live_mode is not None and file_is_executable(repo_mode) != file_is_executable(live_mode)


def directory_child_pull_views(
    *,
    target: TargetSpec,
    capture_command: str | None,
    target_pull_view_repo: str,
    target_pull_view_live: str,
    rule_pull_view_repo: str | None,
    rule_pull_view_live: str | None,
) -> tuple[str, str]:
    pull_view_repo = rule_pull_view_repo or (target_pull_view_repo if target.pull_view_repo is not None else "raw")
    pull_view_live = rule_pull_view_live or (
        target_pull_view_live if target.pull_view_live is not None else default_pull_view_live(capture_command)
    )
    return pull_view_repo, pull_view_live


def directory_child_policy(
    relative_path: str,
    path_rules: tuple[TargetPathRule, ...],
    *,
    default_render: str | None,
    default_capture: str | None,
) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    desired_chmod = None
    render_command = default_render
    capture_command = default_capture
    pull_view_repo = None
    pull_view_live = None
    path = PurePosixPath(relative_path)
    for rule in path_rules:
        if not path.match(rule.pattern):
            continue
        if rule.chmod is not None:
            desired_chmod = rule.chmod
        if rule.render is not None:
            render_command = rule.render
        if rule.capture is not None:
            capture_command = rule.capture
        if rule.pull_view_repo is not None:
            pull_view_repo = rule.pull_view_repo
        if rule.pull_view_live is not None:
            pull_view_live = rule.pull_view_live
    return desired_chmod, render_command, capture_command, pull_view_repo, pull_view_live


def directory_child_chmod_differs(live_file: Path, desired_chmod: str | None) -> bool:
    if desired_chmod is None:
        return False
    live_mode = file_permission_mode(live_file)
    return live_mode is not None and live_mode != int(desired_chmod, 8)


def file_is_executable(mode: int) -> bool:
    return bool(mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))


def file_permission_mode(path: Path) -> int | None:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except (FileNotFoundError, PermissionError):
        return None


def plan_live_delete_directory_action(
    *,
    repo_path: Path,
    live_path: Path,
    push_ignore: tuple[str, ...],
    skip_markers: tuple[str, ...],
    follow_dir_symlinks: bool = False,
) -> tuple[str, tuple[DirectoryPlanItem, ...]]:
    live_files = (
        list_directory_files(
            live_path,
            push_ignore,
            skip_markers=skip_markers,
            follow_dir_symlinks=follow_dir_symlinks,
        )
        if live_path.exists()
        else {}
    )
    directory_items = tuple(
        DirectoryPlanItem(
            relative_path=relative_path,
            action="delete",
            repo_path=repo_path / relative_path,
            live_path=live_file,
        )
        for relative_path, live_file in sorted(live_files.items())
    )
    return ("delete", directory_items) if directory_items else ("noop", ())


def projection_kind_for_render_command(render_command: str | None) -> str:
    if render_command == "jinja":
        return "template"
    if render_command is not None:
        return "command"
    return "raw"


def plan_file_action_from_review_bytes(
    *,
    repo_path: Path,
    live_path: Path,
    desired_bytes: bytes | None,
    review_before_bytes: bytes | None,
    review_after_bytes: bytes | None,
    operation: str,
) -> str:
    if operation == "push":
        if not live_path.exists():
            return "create"
        if desired_bytes is None:
            return "unknown"
        return "noop" if desired_bytes == review_before_bytes else "update"

    repo_exists = repo_path.exists()
    live_exists = live_path.exists()
    if not repo_exists and not live_exists:
        return "noop"
    if not live_exists:
        return "delete"
    if not repo_exists:
        return "create"
    return "noop" if review_before_bytes == review_after_bytes else "update"


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


def run_probe_command(metadata: TargetMetadata) -> bool:
    if metadata.probe_command is None:
        raise ValueError(f"missing probe command for {metadata.package_id}:{metadata.target_name}")
    env = os.environ.copy()
    env.update(metadata.command_env)
    completed = subprocess.run(
        metadata.probe_command,
        cwd=str(metadata.command_cwd),
        env=env,
        shell=True,
        executable="/bin/sh",
        capture_output=True,
        check=False,
    )
    if completed.returncode == 0:
        return True
    if completed.returncode == 100:
        return False
    stderr = completed.stderr.decode("utf-8", errors="replace").strip()
    stdout = completed.stdout.decode("utf-8", errors="replace").strip()
    detail = stderr or stdout or f"exit status {completed.returncode}"
    raise ValueError(
        f"probe failed for {metadata.package_id}:{metadata.target_name} "
        f"with status {completed.returncode}: {detail}"
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
