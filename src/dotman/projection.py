from __future__ import annotations

import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotman.capture import BUILTIN_PATCH_CAPTURE
from dotman.command_runtime import (
    CommandRequest,
    CommandRuntime,
    ShellCommand,
    raise_for_command_interruption,
)
from dotman.collisions import validate_reserved_path_conflicts, validate_target_collisions
from dotman.config import expand_path
from dotman.file_access import needs_sudo_for_read, read_bytes
from dotman.ignore import GITIGNORE_CONTROL_FILE_PATTERNS, collect_gitignore_patterns, list_directory_files
from dotman.manifest import (
    FORCED_COMMAND_PREFIX,
    flatten_vars,
    merge_ignore_patterns,
    resolve_sync_policy,
    sync_policy_allows_operation,
    sync_policy_deletes_on_push,
)
from dotman.models import (
    AdditionalSource,
    DirectoryPlanItem,
    EditorSpec,
    GuardSkip,
    HookCommandSpec,
    ManagerConfig,
    PackageSpec,
    ResolvedPackageSelection,
    TargetPathRule,
    TargetPlan,
    TargetSpec,
    target_path_rule_matches,
)
from dotman.repository import Repository
from dotman.templates import render_template_file, render_template_string


@dataclass(frozen=True)
class ProjectionContext:
    config: ManagerConfig
    command_runtime: CommandRuntime


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
    compare_repo: str
    compare_live: str
    ignore_patterns: tuple[str, ...]
    gitignore_control_ops: tuple[str, ...]
    skip_markers: tuple[str, ...]
    chmod: str | None
    path_rules: tuple[TargetPathRule, ...]
    command_cwd: Path
    command_env: dict[str, str]
    package: PackageSpec
    target: TargetSpec
    editor: Any = None
    additional_sources: tuple[str, ...] = ()
    additional_source_entries: tuple[AdditionalSource, ...] = ()
    additional_sources_root: Path | None = None
    live_path_is_symlink: bool = False
    live_path_symlink_target: str | None = None


def _metadata_collision_tuple(metadata: TargetMetadata):
    return (
        metadata.package,
        metadata.target,
        metadata.repo_path,
        metadata.live_path,
        metadata.ignore_patterns,
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
        "render": target.render if target.render_explicit else None,
        "capture": target.capture if target.capture_explicit else None,
        "editor": target.editor if target.editor_explicit else None,
        "compare": (
            {"repo": target.compare_repo, "live": target.compare_live}
            if target.compare_repo_explicit or target.compare_live_explicit
            else None
        ),
        "ignore": target.ignore_patterns,
        "gitignore": target.gitignore_ops,
        "path_rules": target.path_rules or None,
    }
    forbidden = sorted(name for name, value in forbidden_probe_fields.items() if value is not None)
    if forbidden:
        raise ValueError(
            f"target '{package.id}:{target.name}' uses probe and must not define: "
            + ", ".join(forbidden)
        )


def _projection_command(value: str) -> str:
    return value[len(FORCED_COMMAND_PREFIX):] if value.startswith(FORCED_COMMAND_PREFIX) else value


def build_target_metadata(
    *,
    repo: Repository,
    packages: list[PackageSpec],
    context: dict[str, Any],
    selection: ResolvedPackageSelection,
    operation: str,
    inferred_os: str,
    declaration_package_ids: set[str],
    target_names: set[str] | None = None,
    inspect_live_symlinks: bool = True,
    inspect_gitignore_patterns: bool = True,
    validate_declaration_conflicts: bool = True,
) -> list[TargetMetadata]:
    metadata_targets: list[TargetMetadata] = []

    for package in packages:
        if package.id not in declaration_package_ids:
            continue
        for target in (package.targets or {}).values():
            if target_names is not None and target.name not in target_names:
                continue
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
                placeholder_path = expand_path(str(target.declared_in), dereference=False)
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
                        editor=target.editor,
                        additional_sources=target.additional_sources,
                        additional_source_entries=target.additional_source_entries,
                        additional_sources_root=target.additional_sources_root,
                        compare_repo="raw",
                        compare_live="raw",
                        ignore_patterns=(),
                        gitignore_control_ops=(),
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
            # Target identity must stay configuration-derived; following a source
            # symlink here would make ownership depend on volatile host state.
            repo_path = expand_path(str(target.declared_in / rendered_source), dereference=False)
            live_path = expand_path(rendered_path, dereference=False)
            live_path_is_symlink = inspect_live_symlinks and operation == "push" and live_path.is_symlink()
            live_path_symlink_target = os.readlink(live_path) if live_path_is_symlink else None
            render_command = (
                render_template_string(target.render, context, base_dir=target.declared_in, source_path=target.declared_in)
                if target.render != "raw"
                else None
            )
            capture_command = (
                render_template_string(target.capture, context, base_dir=target.declared_in, source_path=target.declared_in)
                if target.capture != "raw"
                else None
            )
            gitignore_ops = package.gitignore_ops if package.gitignore_ops is not None else repo.ignore_defaults.gitignore
            pattern_layers: list[tuple[str, ...]] = [repo.ignore_defaults.patterns]
            if gitignore_ops and operation in gitignore_ops and inspect_gitignore_patterns:
                pattern_layers.append(collect_gitignore_patterns(repo_path))
            if package.ignore_patterns is not None:
                pattern_layers.append(package.ignore_patterns)
            if target.ignore_patterns is not None:
                pattern_layers.append(target.ignore_patterns)
            ignore_patterns = merge_ignore_patterns(*pattern_layers)
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
                    compare_repo=target.compare_repo if target.compare_repo != "raw" else "raw",
                    compare_live=target.compare_live,
                    ignore_patterns=ignore_patterns,
                    gitignore_control_ops=gitignore_ops,
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
                    editor=target.editor,
                    additional_sources=target.additional_sources,
                    additional_source_entries=target.additional_source_entries,
                    additional_sources_root=target.additional_sources_root,
                    live_path_is_symlink=live_path_is_symlink,
                    live_path_symlink_target=live_path_symlink_target,
                )
            )

    if validate_declaration_conflicts:
        rendered_targets = [_metadata_collision_tuple(metadata) for metadata in metadata_targets if target_claims_path(metadata.target)]
        validate_target_collisions(rendered_targets, operation=operation)
        if operation == "push":
            validate_reserved_path_conflicts(packages, rendered_targets, context)
    return metadata_targets


def plan_targets(
    projection_context: ProjectionContext,
    *,
    repo: Repository,
    packages: list[PackageSpec],
    context: dict[str, Any],
    selection: ResolvedPackageSelection,
    operation: str,
    inferred_os: str,
    declaration_package_ids: set[str],
    target_names: set[str] | None = None,
    metadata_targets: list[TargetMetadata] | None = None,
    guard_skips: list[GuardSkip] | None = None,
) -> list[TargetPlan]:
    if metadata_targets is None:
        metadata_targets = build_target_metadata(
            repo=repo,
            packages=packages,
            context=context,
            selection=selection,
            operation=operation,
            inferred_os=inferred_os,
            declaration_package_ids=declaration_package_ids,
            target_names=target_names,
        )

    plans: list[TargetPlan] = []
    for metadata in metadata_targets:
        package = metadata.package
        target = metadata.target
        repo_path = metadata.repo_path
        live_path = metadata.live_path
        effective_ignore_patterns = metadata.ignore_patterns
        sync_policy = resolve_sync_policy(package=package, target=target)
        if target.probe is not None:
            probe_active = run_probe_command(projection_context.command_runtime, metadata)
            plans.append(
                TargetPlan(
                    package_id=package.id,
                    target_name=target.name,
                    render=target.render,
                    capture=target.capture,
                    compare_repo=metadata.compare_repo,
                    compare_live=metadata.compare_live,
                    sync_policy=sync_policy,
                        editor=target.editor,
                    editor_explicit=target.editor_explicit,
                    additional_sources=target.additional_sources,
                    additional_source_entries=target.additional_source_entries,
                    additional_sources_root=target.additional_sources_root,
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
        target_kind = resolve_target_kind(
            target_type=target.target_type,
            repo_path=repo_path,
            live_path=live_path,
            target_label=f"{package.id}:{target.name}",
            file_symlink_mode=projection_context.config.file_symlink_mode,
            dir_symlink_mode=projection_context.config.dir_symlink_mode,
        )
        if metadata.path_rules and target_kind == "file":
            raise ValueError(
                f"target '{package.id}:{target.name}' defines path_rules but is not a directory target"
            )
        render_command = metadata.render_command
        capture_command = metadata.capture_command
        if operation == "push" and sync_policy_deletes_on_push(sync_policy):
            target_kind = resolve_push_only_delete_target_kind(
                target_type=target.target_type,
                repo_path=repo_path,
                live_path=live_path,
                target_label=f"{package.id}:{target.name}",
                file_symlink_mode=projection_context.config.file_symlink_mode,
                dir_symlink_mode=projection_context.config.dir_symlink_mode,
            )
            if target_kind == "directory":
                action, directory_items = plan_live_delete_directory_action(
                    repo_path=repo_path,
                    live_path=live_path,
                    skip_markers=metadata.skip_markers,
                    force_ignore_patterns=GITIGNORE_CONTROL_FILE_PATTERNS if operation in metadata.gitignore_control_ops else (),
                    follow_dir_symlinks=projection_context.config.dir_symlink_mode == "follow",
                    command_runtime=projection_context.command_runtime,
                    path_rules=metadata.path_rules,
                    ignore_patterns=effective_ignore_patterns,
                    compare_repo=metadata.compare_repo,
                    compare_live=metadata.compare_live,
                    package=package,
                    target=target,
                    context=context,
                    target_env=metadata.command_env,
                    repo_name=repo.config.name,
                    package_id=package.id,
                    bound_profile=selection.bound_profile,
                    target_name=target.name,
                    guard_skips=guard_skips,
                )
                plans.append(
                    TargetPlan(
                        package_id=package.id,
                        target_name=target.name,
                        render=target.render,
                        capture=target.capture,
                        compare_repo=metadata.compare_repo,
                        compare_live=metadata.compare_live,
                        sync_policy=sync_policy,
                        editor=target.editor,
                        editor_explicit=target.editor_explicit,
                        additional_sources=target.additional_sources,
                        additional_source_entries=target.additional_source_entries,
                        additional_sources_root=target.additional_sources_root,
                        repo_path=repo_path,
                        live_path=live_path,
                        action=action,
                        target_kind="directory",
                        projection_kind="directory",
                        render_command=render_command,
                        capture_command=capture_command,
                        live_path_is_symlink=metadata.live_path_is_symlink,
                        live_path_symlink_target=metadata.live_path_symlink_target,
                        file_symlink_mode=projection_context.config.file_symlink_mode,
                        dir_symlink_mode=projection_context.config.dir_symlink_mode,
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
                projection_context.command_runtime,
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
                compare_repo=metadata.compare_repo,
                compare_live=metadata.compare_live,
            )
            plans.append(
                TargetPlan(
                    package_id=package.id,
                    target_name=target.name,
                    render=target.render,
                    capture=target.capture,
                    compare_repo=metadata.compare_repo,
                    compare_live=metadata.compare_live,
                    sync_policy=sync_policy,
                    editor=target.editor,
                    editor_explicit=target.editor_explicit,
                    additional_sources=target.additional_sources,
                    additional_source_entries=target.additional_source_entries,
                    additional_sources_root=target.additional_sources_root,
                    repo_path=repo_path,
                    live_path=live_path,
                    action=action,
                    target_kind=target_kind,
                    projection_kind="raw" if target_kind != "unknown" else "unknown",
                    render_command=render_command,
                    capture_command=capture_command,
                    live_path_is_symlink=metadata.live_path_is_symlink,
                    live_path_symlink_target=metadata.live_path_symlink_target,
                    file_symlink_mode=projection_context.config.file_symlink_mode,
                    dir_symlink_mode=projection_context.config.dir_symlink_mode,
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
                    render=target.render,
                    capture=target.capture,
                    compare_repo=metadata.compare_repo,
                    compare_live=metadata.compare_live,
                    sync_policy=sync_policy,
                    editor=target.editor,
                    editor_explicit=target.editor_explicit,
                    additional_sources=target.additional_sources,
                    additional_source_entries=target.additional_source_entries,
                    additional_sources_root=target.additional_sources_root,
                    repo_path=repo_path,
                    live_path=live_path,
                    action="noop",
                    target_kind="unknown",
                    projection_kind="unknown",
                    render_command=render_command,
                    capture_command=capture_command,
                    live_path_is_symlink=metadata.live_path_is_symlink,
                    live_path_symlink_target=metadata.live_path_symlink_target,
                    file_symlink_mode=projection_context.config.file_symlink_mode,
                    dir_symlink_mode=projection_context.config.dir_symlink_mode,
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
            compare_repo=metadata.compare_repo,
            compare_live=metadata.compare_live,
            repo_path=repo_path,
        )
        if target_kind == "directory":
            action, directory_items = plan_directory_action(
                projection_context,
                repo=repo,
                package=package,
                target=target,
                repo_path=repo_path,
                live_path=live_path,
                skip_markers=metadata.skip_markers,
                force_ignore_patterns=GITIGNORE_CONTROL_FILE_PATTERNS if operation in metadata.gitignore_control_ops else (),
                operation=operation,
                ignore_patterns=effective_ignore_patterns,
                render_command=render_command,
                capture_command=capture_command,
                context=context,
                selection=selection,
                inferred_os=inferred_os,
                compare_repo=metadata.compare_repo,
                compare_live=metadata.compare_live,
                target_env=metadata.command_env,
                path_rules=metadata.path_rules,
                guard_skips=guard_skips,
            )
            plans.append(
                TargetPlan(
                    package_id=package.id,
                    target_name=target.name,
                    render=target.render,
                    capture=target.capture,
                    compare_repo=metadata.compare_repo,
                    compare_live=metadata.compare_live,
                    sync_policy=sync_policy,
                    editor=target.editor,
                    editor_explicit=target.editor_explicit,
                    additional_sources=target.additional_sources,
                    additional_source_entries=target.additional_source_entries,
                    additional_sources_root=target.additional_sources_root,
                            repo_path=repo_path,
                    live_path=live_path,
                    action=action,
                    target_kind="directory",
                    projection_kind="directory",
                    render_command=render_command,
                    capture_command=capture_command,
                    live_path_is_symlink=metadata.live_path_is_symlink,
                    live_path_symlink_target=metadata.live_path_symlink_target,
                    file_symlink_mode=projection_context.config.file_symlink_mode,
                    dir_symlink_mode=projection_context.config.dir_symlink_mode,
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
                    projection_context.command_runtime,
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
        compare_repo = metadata.compare_repo
        compare_live = metadata.compare_live
        review_before_bytes, review_after_bytes = build_file_review_bytes(
            projection_context.command_runtime,
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
            compare_repo=compare_repo,
            compare_live=compare_live,
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
                render=target.render,
                capture=target.capture,
                compare_repo=metadata.compare_repo,
                compare_live=metadata.compare_live,
                sync_policy=sync_policy,
                editor=target.editor,
                editor_explicit=target.editor_explicit,
                additional_sources=target.additional_sources,
                additional_source_entries=target.additional_source_entries,
                additional_sources_root=target.additional_sources_root,
                repo_path=repo_path,
                live_path=live_path,
                action=action,
                target_kind="file",
                projection_kind=projection_kind,
                desired_text=desired_text,
                render_command=render_command,
                capture_command=capture_command,
                projection_error=projection_error,
                live_path_is_symlink=metadata.live_path_is_symlink,
                live_path_symlink_target=metadata.live_path_symlink_target,
                file_symlink_mode=projection_context.config.file_symlink_mode,
                dir_symlink_mode=projection_context.config.dir_symlink_mode,
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


def default_compare_live(capture_command: str | None) -> str:
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
    return tuple(
        TargetPathRule(
            name=rule.name,
            pattern=rule.pattern,
            priority=rule.priority,
            chmod=rule.chmod,
            render=render_template_string(rule.render, context, base_dir=base_dir, source_path=base_dir) if rule.render != "raw" else "raw",
            capture=render_template_string(rule.capture, context, base_dir=base_dir, source_path=base_dir) if rule.capture != "raw" else "raw",
            compare_repo=render_template_string(rule.compare_repo, context, base_dir=base_dir, source_path=base_dir) if rule.compare_repo not in {"raw", "render"} else rule.compare_repo,
            compare_live=render_template_string(rule.compare_live, context, base_dir=base_dir, source_path=base_dir) if rule.compare_live not in {"raw", "capture"} else rule.compare_live,
            editor=rule.editor,
            sync_policy=rule.sync_policy,
            additional_sources=rule.additional_sources,
            additional_source_entries=rule.additional_source_entries,
            render_explicit=rule.render_explicit,
            capture_explicit=rule.capture_explicit,
            compare_repo_explicit=rule.compare_repo_explicit,
            compare_live_explicit=rule.compare_live_explicit,
            editor_explicit=rule.editor_explicit,
            priority_explicit=rule.priority_explicit,
            pattern_explicit=rule.pattern_explicit,
            sync_policy_explicit=rule.sync_policy_explicit,
            hooks=rule.hooks,
        )
        for rule in path_rules
    )



def validate_patch_capture_target(
    *,
    package: PackageSpec,
    target: TargetSpec,
    target_kind: str,
    render_command: str | None,
    capture_command: str | None,
    compare_repo: str,
    compare_live: str,
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
        compare_repo=compare_repo,
        compare_live=compare_live,
        repo_path=repo_path,
    )


def validate_patch_capture_unit(
    *,
    label: str,
    render_command: str | None,
    capture_command: str | None,
    compare_repo: str,
    compare_live: str,
    repo_path: Path | None = None,
) -> None:
    if capture_command != BUILTIN_PATCH_CAPTURE:
        return
    if render_command is None:
        raise ValueError(f'capture = "patch" requires render for {label}')
    if compare_repo != "render" or compare_live != "raw":
        raise ValueError(
            f'capture = "patch" requires compare_repo = "render" and compare_live = "raw" for {label}'
        )
    if repo_path is not None and not repo_path.exists():
        raise ValueError(f'capture = "patch" requires existing repo source for {label}')


def project_repo_file(
    command_runtime: CommandRuntime,
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
                    command_runtime,
                    repo=repo,
                    package=package,
                    target=target,
                    repo_path=repo_path,
                    live_path=live_path,
                    command=_projection_command(render_command),
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


def filter_directory_candidates_by_path_rule_guards(
    *,
    command_runtime: CommandRuntime,
    desired_files: dict[str, Path],
    live_files: dict[str, Path],
    path_rules: tuple[TargetPathRule, ...],
    operation: str,
    context: dict[str, Any],
    target_env: dict[str, str],
    repo_name: str,
    package_id: str,
    bound_profile: str | None,
    target_name: str,
    guard_skips: list[GuardSkip] | None,
) -> tuple[dict[str, Path], dict[str, Path]]:
    from dotman.planning_guards import evaluate_directory_path_rule_guards

    candidate_paths = set(desired_files) | set(live_files)
    if not candidate_paths or not path_rules:
        return desired_files, live_files
    remaining_paths, path_rule_skips = evaluate_directory_path_rule_guards(
        command_runtime=command_runtime,
        path_rules=path_rules,
        candidate_paths=candidate_paths,
        operation=operation,
        context=context,
        target_env=target_env,
        repo_name=repo_name,
        package_id=package_id,
        bound_profile=bound_profile,
        target_name=target_name,
    )
    if guard_skips is not None:
        guard_skips.extend(path_rule_skips)
    return (
        {path: source for path, source in desired_files.items() if path in remaining_paths},
        {path: source for path, source in live_files.items() if path in remaining_paths},
    )


def plan_directory_action(
    projection_context: ProjectionContext,
    *,
    repo: Repository,
    package: PackageSpec,
    target: TargetSpec,
    repo_path: Path,
    live_path: Path,
    ignore_patterns: tuple[str, ...],
    skip_markers: tuple[str, ...],
    operation: str,
    render_command: str | None,
    capture_command: str | None,
    context: dict[str, Any],
    selection: ResolvedPackageSelection,
    inferred_os: str,
    compare_repo: str,
    compare_live: str,
    target_env: dict[str, str],
    path_rules: tuple[TargetPathRule, ...] = (),
    force_ignore_patterns: tuple[str, ...] = (),
    guard_skips: list[GuardSkip] | None = None,
) -> tuple[str, tuple[DirectoryPlanItem, ...]]:
    # The operation-specific metadata already includes any selected .gitignore
    # controls; apply the same patterns to repository and live census inputs.
    operation_ignore = ignore_patterns
    follow_dir_symlinks = projection_context.config.dir_symlink_mode == "follow"
    desired_files = list_directory_files(
        repo_path,
        operation_ignore,
        skip_markers=skip_markers,
        follow_dir_symlinks=follow_dir_symlinks,
        force_ignore_patterns=force_ignore_patterns,
    )
    live_exists = live_path.exists()
    live_files = (
        list_directory_files(
            live_path,
            operation_ignore,
            skip_markers=skip_markers,
            follow_dir_symlinks=follow_dir_symlinks,
            force_ignore_patterns=force_ignore_patterns,
        )
        if live_exists
        else {}
    )
    desired_files, live_files = filter_directory_candidates_by_path_rule_guards(
        command_runtime=projection_context.command_runtime,
        desired_files=desired_files,
        live_files=live_files,
        path_rules=path_rules,
        operation=operation,
        context=context,
        target_env=target_env,
        repo_name=repo.config.name,
        package_id=package.id,
        bound_profile=selection.bound_profile,
        target_name=target.name,
        guard_skips=guard_skips,
    )
    desired_rel_paths = set(desired_files)
    live_rel_paths = set(live_files)
    directory_items: list[DirectoryPlanItem] = []

    if operation == "push":
        # A path rule's push-only-delete policy applies to each matching child,
        # so classify those children before constructing create/update actions.
        child_delete_paths: set[str] = set()
        for relative_path in sorted(desired_rel_paths | live_rel_paths):
            child_policy = directory_child_policy(
                relative_path,
                path_rules,
                default_render=render_command,
                default_capture=capture_command,
                default_editor=target.editor,
                default_sync_policy=resolve_sync_policy(package=package, target=target),
            )
            if not sync_policy_deletes_on_push(child_policy[7] or ""):
                continue
            child_delete_paths.add(relative_path)
            if relative_path not in live_rel_paths:
                continue
            child_compare_repo, child_compare_live = directory_child_pull_views(
                target=target,
                capture_command=child_policy[2],
                target_compare_repo=compare_repo,
                target_compare_live=compare_live,
                rule_compare_repo=child_policy[3],
                rule_compare_live=child_policy[4],
            )
            directory_items.append(
                DirectoryPlanItem(
                    relative_path=relative_path,
                    action="delete",
                    repo_path=repo_path / relative_path,
                    live_path=live_files[relative_path],
                    render_command=child_policy[1],
                    capture_command=child_policy[2],
                    compare_repo=child_compare_repo,
                    compare_live=child_compare_live,
                    editor=child_policy[5],
                    editor_explicit=directory_child_editor_explicit(
                        relative_path, path_rules, default_explicit=target.editor_explicit
                    ),
                    additional_sources=child_policy[6],
                    additional_source_entries=child_policy[5].source_entries(),
                    sync_policy=child_policy[7],
                )
            )
        desired_rel_paths -= child_delete_paths
        live_rel_paths -= child_delete_paths

        for relative_path in sorted(desired_rel_paths - live_rel_paths):
            source_path = desired_files[relative_path]
            child_policy = directory_child_policy(
                relative_path,
                path_rules,
                default_render=render_command,
                default_capture=capture_command,
                default_editor=target.editor,
                default_sync_policy=resolve_sync_policy(package=package, target=target),
            )
            if not sync_policy_allows_operation(child_policy[7] or "both", operation=operation):
                continue
            child_compare_repo, child_compare_live = directory_child_pull_views(
                target=target,
                capture_command=child_policy[2],
                target_compare_repo=compare_repo,
                target_compare_live=compare_live,
                rule_compare_repo=child_policy[3],
                rule_compare_live=child_policy[4],
            )
            validate_directory_child_patch_capture(
                package=package,
                target=target,
                relative_path=relative_path,
                render_command=child_policy[1],
                capture_command=child_policy[2],
                compare_repo=child_compare_repo,
                compare_live=child_compare_live,
                repo_path=source_path,
            )
            desired_bytes, _projection_kind = project_repo_file(
                projection_context.command_runtime,
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
                    compare_repo=child_compare_repo,
                    compare_live=child_compare_live,
                    editor=child_policy[5],
                    editor_explicit=directory_child_editor_explicit(relative_path, path_rules, default_explicit=target.editor_explicit),
                    additional_sources=child_policy[6],
                    additional_source_entries=child_policy[5].source_entries(),
                    sync_policy=child_policy[7],
                    desired_bytes=desired_bytes,
                    review_before_bytes=b"",
                    review_after_bytes=desired_bytes,
                )
            )
        for relative_path in sorted(live_rel_paths - desired_rel_paths):
            child_policy = directory_child_policy(
                relative_path,
                path_rules,
                default_render=render_command,
                default_capture=capture_command,
                default_editor=target.editor,
                default_sync_policy=resolve_sync_policy(package=package, target=target),
            )
            if not sync_policy_allows_operation(child_policy[7] or "both", operation=operation):
                continue
            directory_items.append(
                DirectoryPlanItem(
                    relative_path=relative_path,
                    action="delete",
                    repo_path=repo_path / relative_path,
                    live_path=live_files[relative_path],
                    render_command=child_policy[1],
                    capture_command=child_policy[2],
                    editor=child_policy[5],
                    editor_explicit=directory_child_editor_explicit(relative_path, path_rules, default_explicit=target.editor_explicit),
                    additional_sources=child_policy[6],
                    additional_source_entries=child_policy[5].source_entries(),
                    sync_policy=child_policy[7],
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
                default_editor=target.editor,
                default_sync_policy=resolve_sync_policy(package=package, target=target),
            )
            if not sync_policy_allows_operation(child_policy[7] or "both", operation=operation):
                continue
            child_compare_repo, child_compare_live = directory_child_pull_views(
                target=target,
                capture_command=child_policy[2],
                target_compare_repo=compare_repo,
                target_compare_live=compare_live,
                rule_compare_repo=child_policy[3],
                rule_compare_live=child_policy[4],
            )
            validate_directory_child_patch_capture(
                package=package,
                target=target,
                relative_path=relative_path,
                render_command=child_policy[1],
                capture_command=child_policy[2],
                compare_repo=child_compare_repo,
                compare_live=child_compare_live,
                repo_path=source_path,
            )
            desired_bytes, _projection_kind = project_repo_file(
                projection_context.command_runtime,
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
                        compare_repo=child_compare_repo,
                        compare_live=child_compare_live,
                        editor=child_policy[5],
                        editor_explicit=directory_child_editor_explicit(relative_path, path_rules, default_explicit=target.editor_explicit),
                        additional_sources=child_policy[6],
                        additional_source_entries=child_policy[5].source_entries(),
                        sync_policy=child_policy[7],
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
            default_editor=target.editor,
            default_sync_policy=resolve_sync_policy(package=package, target=target),
        )
        if not sync_policy_allows_operation(child_policy[7] or "both", operation=operation):
            continue
        child_compare_repo, child_compare_live = directory_child_pull_views(
            target=target,
            capture_command=child_policy[2],
            target_compare_repo=compare_repo,
            target_compare_live=compare_live,
            rule_compare_repo=child_policy[3],
            rule_compare_live=child_policy[4],
        )
        validate_directory_child_patch_capture(
            package=package,
            target=target,
            relative_path=relative_path,
            render_command=child_policy[1],
            capture_command=child_policy[2],
            compare_repo=child_compare_repo,
            compare_live=child_compare_live,
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
                compare_repo=child_compare_repo,
                compare_live=child_compare_live,
                editor=child_policy[5],
                editor_explicit=directory_child_editor_explicit(relative_path, path_rules, default_explicit=target.editor_explicit),
                additional_sources=child_policy[6],
                additional_source_entries=child_policy[5].source_entries(),
                sync_policy=child_policy[7],
            )
        )
    for relative_path in sorted(live_rel_paths - desired_rel_paths):
        child_policy = directory_child_policy(
            relative_path,
            path_rules,
            default_render=render_command,
            default_capture=capture_command,
            default_editor=target.editor,
            default_sync_policy=resolve_sync_policy(package=package, target=target),
        )
        if not sync_policy_allows_operation(child_policy[7] or "both", operation=operation):
            continue
        child_compare_repo, child_compare_live = directory_child_pull_views(
            target=target,
            capture_command=child_policy[2],
            target_compare_repo=compare_repo,
            target_compare_live=compare_live,
            rule_compare_repo=child_policy[3],
            rule_compare_live=child_policy[4],
        )
        validate_directory_child_patch_capture(
            package=package,
            target=target,
            relative_path=relative_path,
            render_command=child_policy[1],
            capture_command=child_policy[2],
            compare_repo=child_compare_repo,
            compare_live=child_compare_live,
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
                compare_repo=child_compare_repo,
                compare_live=child_compare_live,
                editor=child_policy[5],
                editor_explicit=directory_child_editor_explicit(relative_path, path_rules, default_explicit=target.editor_explicit),
                additional_sources=child_policy[6],
                additional_source_entries=child_policy[5].source_entries(),
                sync_policy=child_policy[7],
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
            default_editor=target.editor,
            default_sync_policy=resolve_sync_policy(package=package, target=target),
        )
        if not sync_policy_allows_operation(child_policy[7] or "both", operation=operation):
            continue
        child_compare_repo, child_compare_live = directory_child_pull_views(
            target=target,
            capture_command=child_policy[2],
            target_compare_repo=compare_repo,
            target_compare_live=compare_live,
            rule_compare_repo=child_policy[3],
            rule_compare_live=child_policy[4],
        )
        validate_directory_child_patch_capture(
            package=package,
            target=target,
            relative_path=relative_path,
            render_command=child_policy[1],
            capture_command=child_policy[2],
            compare_repo=child_compare_repo,
            compare_live=child_compare_live,
            repo_path=source_path,
        )
        repo_bytes = pull_view_bytes(
            projection_context.command_runtime,
            repo=repo,
            package=package,
            target=target,
            repo_path=source_path,
            live_path=live_file,
            view=child_compare_repo,
            repo_side=True,
            render_command=child_policy[1],
            capture_command=child_policy[2],
            context=context,
            selection=selection,
            operation=operation,
            inferred_os=inferred_os,
        )
        live_bytes = pull_view_bytes(
            projection_context.command_runtime,
            repo=repo,
            package=package,
            target=target,
            repo_path=source_path,
            live_path=live_file,
            view=child_compare_live,
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
                    compare_repo=child_compare_repo,
                    compare_live=child_compare_live,
                    editor=child_policy[5],
                    editor_explicit=directory_child_editor_explicit(relative_path, path_rules, default_explicit=target.editor_explicit),
                    additional_sources=child_policy[6],
                    additional_source_entries=child_policy[5].source_entries(),
                    sync_policy=child_policy[7],
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
    compare_repo: str,
    compare_live: str,
    repo_path: Path | None = None,
) -> None:
    label = f"{package.id}:{target.name}:{relative_path}"
    validate_patch_capture_unit(
        label=label,
        render_command=render_command,
        capture_command=capture_command,
        compare_repo=compare_repo,
        compare_live=compare_live,
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
    target_compare_repo: str,
    target_compare_live: str,
    rule_compare_repo: str | None,
    rule_compare_live: str | None,
) -> tuple[str, str]:
    compare_repo = rule_compare_repo or target_compare_repo or "raw"
    compare_live = rule_compare_live or target_compare_live or default_compare_live(capture_command)
    return compare_repo, compare_live


def directory_child_policy(
    relative_path: str,
    path_rules: tuple[TargetPathRule, ...],
    *,
    default_render: str | None,
    default_capture: str | None,
    default_editor: EditorSpec | None = None,
    default_sync_policy: str | None = None,
) -> tuple[str | None, str | None, str | None, str | None, str | None, EditorSpec, tuple[str, ...], str | None]:
    desired_chmod = None
    render_command = default_render
    capture_command = default_capture
    compare_repo = None
    compare_live = None
    editor = default_editor or EditorSpec()
    sync_policy = default_sync_policy
    for rule in path_rules:
        if not target_path_rule_matches(relative_path, rule.pattern):
            continue
        if rule.chmod is not None:
            desired_chmod = rule.chmod
        if rule.render_explicit:
            render_command = None if rule.render == "raw" else rule.render
        if rule.capture_explicit:
            capture_command = None if rule.capture == "raw" else rule.capture
        if rule.compare_repo_explicit:
            compare_repo = rule.compare_repo
        if rule.compare_live_explicit:
            compare_live = rule.compare_live
        if rule.editor_explicit:
            editor = rule.editor
        if rule.sync_policy_explicit:
            sync_policy = rule.sync_policy
        if compare_live == "capture" and capture_command is None:
            compare_live = "raw"
    return desired_chmod, render_command, capture_command, compare_repo, compare_live, editor, editor.additional_sources, sync_policy


def directory_child_editor_explicit(
    relative_path: str,
    path_rules: tuple[TargetPathRule, ...],
    *,
    default_explicit: bool,
) -> bool:
    """Report whether a child receives an explicitly configured editor."""
    if default_explicit:
        return True
    return any(
        rule.editor_explicit and target_path_rule_matches(relative_path, rule.pattern)
        for rule in path_rules
    )


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
    command_runtime: CommandRuntime,
    repo_path: Path,
    live_path: Path,
    ignore_patterns: tuple[str, ...],
    skip_markers: tuple[str, ...],
    path_rules: tuple[TargetPathRule, ...],
    compare_repo: str,
    compare_live: str,
    package: PackageSpec,
    target: TargetSpec,
    context: dict[str, Any],
    target_env: dict[str, str],
    repo_name: str,
    package_id: str,
    bound_profile: str | None,
    target_name: str,
    guard_skips: list[GuardSkip] | None,
    force_ignore_patterns: tuple[str, ...] = (),
    follow_dir_symlinks: bool = False,
) -> tuple[str, tuple[DirectoryPlanItem, ...]]:
    live_files = (
        list_directory_files(
            live_path,
            ignore_patterns,
            skip_markers=skip_markers,
            follow_dir_symlinks=follow_dir_symlinks,
            force_ignore_patterns=force_ignore_patterns,
        )
        if live_path.exists()
        else {}
    )
    _, live_files = filter_directory_candidates_by_path_rule_guards(
        command_runtime=command_runtime,
        desired_files={},
        live_files=live_files,
        path_rules=path_rules,
        operation="push",
        context=context,
        target_env=target_env,
        repo_name=repo_name,
        package_id=package_id,
        bound_profile=bound_profile,
        target_name=target_name,
        guard_skips=guard_skips,
    )
    directory_items: list[DirectoryPlanItem] = []
    for relative_path, live_file in sorted(live_files.items()):
        child_policy = directory_child_policy(
            relative_path,
            path_rules,
            default_render=None,
            default_capture=None,
            default_editor=target.editor,
            default_sync_policy=resolve_sync_policy(package=package, target=target),
        )
        if not sync_policy_allows_operation(child_policy[7] or "both", operation="push"):
            continue
        child_compare_repo, child_compare_live = directory_child_pull_views(
            target=target,
            capture_command=child_policy[2],
            target_compare_repo=compare_repo,
            target_compare_live=compare_live,
            rule_compare_repo=child_policy[3],
            rule_compare_live=child_policy[4],
        )
        directory_items.append(
            DirectoryPlanItem(
                relative_path=relative_path,
                action="delete",
                repo_path=repo_path / relative_path,
                live_path=live_file,
                compare_repo=child_compare_repo,
                compare_live=child_compare_live,
                editor=child_policy[5],
                editor_explicit=directory_child_editor_explicit(relative_path, path_rules, default_explicit=target.editor_explicit),
                additional_sources=child_policy[6],
                additional_source_entries=child_policy[5].source_entries(),
                sync_policy=child_policy[7],
            )
        )
    directory_items = tuple(directory_items)
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
    command_runtime: CommandRuntime,
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
    compare_repo: str,
    compare_live: str,
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
        command_runtime,
        repo=repo,
        package=package,
        target=target,
        repo_path=repo_path,
        live_path=live_path,
        view=compare_repo,
        repo_side=True,
        render_command=render_command,
        capture_command=capture_command,
        context=context,
        selection=selection,
        operation=operation,
        inferred_os=inferred_os,
    )
    live_bytes = pull_view_bytes(
        command_runtime,
        repo=repo,
        package=package,
        target=target,
        repo_path=repo_path,
        live_path=live_path,
        view=compare_live,
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
    command_runtime: CommandRuntime,
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
    compare_repo: str,
    compare_live: str,
) -> tuple[bytes | None, bytes | None]:
    if operation == "push":
        try:
            live_bytes = read_bytes(live_path)
        except FileNotFoundError:
            live_bytes = b""
        return live_bytes, desired_bytes

    repo_bytes = pull_view_bytes(
        command_runtime,
        repo=repo,
        package=package,
        target=target,
        repo_path=repo_path,
        live_path=live_path,
        view=compare_repo,
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
        command_runtime,
        repo=repo,
        package=package,
        target=target,
        repo_path=repo_path,
        live_path=live_path,
        view=compare_live,
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
    command_runtime: CommandRuntime,
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
            command_runtime,
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
            return read_bytes(live_path)
        return run_command_projection(
            command_runtime,
            repo=repo,
            package=package,
            target=target,
            repo_path=repo_path,
            live_path=live_path,
            command=_projection_command(capture_command),
            selection=selection,
            operation=operation,
            inferred_os=inferred_os,
            context=context,
        )
    command = render_template_string(_projection_command(view), context, base_dir=target.declared_in, source_path=target.declared_in)
    return run_command_projection(
        command_runtime,
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


def run_probe_command(command_runtime: CommandRuntime, metadata: TargetMetadata) -> bool:
    if metadata.probe_command is None:
        raise ValueError(f"missing probe command for {metadata.package_id}:{metadata.target_name}")
    result = command_runtime.run(
        CommandRequest(
            command=ShellCommand(metadata.probe_command),
            cwd=metadata.command_cwd,
            env=metadata.command_env,
        )
    )
    raise_for_command_interruption(result)
    if result.exit_code == 0:
        return True
    if result.exit_code == 100:
        return False
    stderr = result.stderr.decode("utf-8", errors="replace").strip()
    stdout = result.stdout.decode("utf-8", errors="replace").strip()
    detail = stderr or stdout or f"exit status {result.exit_code}"
    raise ValueError(
        f"probe failed for {metadata.package_id}:{metadata.target_name} "
        f"with status {result.exit_code}: {detail}"
    )


def run_command_projection(
    command_runtime: CommandRuntime,
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
    env = build_target_command_env(
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
    # Projection providers are never elevated. If either managed input is not
    # readable by the provider, Dotman reads it through the access layer and
    # gives the command a private, readable staging copy instead.
    stage_repo = repo_path.exists() and needs_sudo_for_read(repo_path)
    stage_live = live_path.exists() and needs_sudo_for_read(live_path)
    with tempfile.TemporaryDirectory(prefix="dotman-projection-") as stage_dir:
        staged_env = dict(env)
        if stage_repo:
            staged_source = Path(stage_dir) / f"repo-{repo_path.name}"
            staged_source.write_bytes(read_bytes(repo_path))
            staged_source.chmod(0o444)
            staged_env.update({
                "DOTMAN_TARGET_REPO_PATH": str(staged_source),
                "DOTMAN_REPO_PATH": str(staged_source),
                "DOTMAN_SOURCE": str(staged_source),
            })
        if stage_live:
            staged_live = Path(stage_dir) / f"live-{live_path.name}"
            staged_live.write_bytes(read_bytes(live_path))
            staged_live.chmod(0o444)
            staged_env.update({
                "DOTMAN_TARGET_LIVE_PATH": str(staged_live),
                "DOTMAN_LIVE_PATH": str(staged_live),
            })
        result = command_runtime.run(
            CommandRequest(
                command=ShellCommand(_projection_command(command)),
                cwd=target.declared_in,
                env=staged_env,
                # Projection commands are pure stdout producers; managed reads are performed by Dotman.
                elevation="none",
            )
        )
    raise_for_command_interruption(result)
    if result.exit_code != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        raise ValueError(f"command projection failed for {package.id}:{target.name}: {stderr.strip()}")
    return result.stdout


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
