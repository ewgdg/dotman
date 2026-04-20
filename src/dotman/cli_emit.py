from __future__ import annotations

import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Sequence

from dotman import cli_style
from dotman.diff_review import ReviewItem, display_review_path
from dotman.execution import build_execution_session, execute_session, _preflight_execution_session_sudo
from dotman.file_access import sudo_session
from dotman.models import OperationPlan, binding_plans_for_operation_plan
from dotman.snapshot import create_push_snapshot, execute_rollback, mark_snapshot_status, prune_snapshots


@dataclass
class PayloadPackageSection:
    repo_name: str
    package_id: str
    profile: str
    package_hooks: dict[str, list[Any]]
    target_hooks: dict[str, dict[str, list[Any]]]
    targets: list[Any]


@dataclass
class PayloadRepoHookSection:
    repo_name: str
    hooks: dict[str, list[Any]]


CollectPendingSelectionItems = Callable[..., Sequence[Any]]


def _format_variable_value(value: Any) -> str:
    if isinstance(value, str):
        if not value or value.strip() != value or "\n" in value or "\t" in value:
            return json.dumps(value)
        return value
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    except TypeError:
        return str(value)


@dataclass(frozen=True)
class PushSymlinkHazard:
    binding_label: str
    package_id: str
    target_name: str
    live_path: Path
    symlink_target: str
    target_kind: str
    replaceable: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "binding_label": self.binding_label,
            "package_id": self.package_id,
            "target_name": self.target_name,
            "live_path": str(self.live_path),
            "symlink_target": self.symlink_target,
            "target_kind": self.target_kind,
            "replaceable": self.replaceable,
        }


def collect_push_live_symlink_hazards(plans: Sequence[Any]) -> list[PushSymlinkHazard]:
    hazards: list[PushSymlinkHazard] = []
    for plan in binding_plans_for_operation_plan(plans):
        binding_label = f"{plan.binding.repo}:{plan.binding.selector}@{plan.binding.profile}"
        for target in plan.target_plans:
            if target.action == "noop" or not target.live_path_is_symlink:
                continue
            if target.target_kind == "directory":
                if target.dir_symlink_mode == "follow":
                    continue
                hazards.append(
                    PushSymlinkHazard(
                        binding_label=binding_label,
                        package_id=target.package_id,
                        target_name=target.target_name,
                        live_path=target.live_path,
                        symlink_target=target.live_path_symlink_target or "<unknown>",
                        target_kind=target.target_kind,
                        replaceable=False,
                    )
                )
                continue
            if target.file_symlink_mode == "follow":
                continue
            hazards.append(
                PushSymlinkHazard(
                    binding_label=binding_label,
                    package_id=target.package_id,
                    target_name=target.target_name,
                    live_path=target.live_path,
                    symlink_target=target.live_path_symlink_target or "<unknown>",
                    target_kind=target.target_kind,
                    replaceable=True,
                )
            )
    return hazards


def allow_push_live_symlink_replacements(plans: Sequence[Any]) -> list[Any]:
    updated_plans: list[Any] = []
    for plan in binding_plans_for_operation_plan(plans):
        updated_targets = []
        for target in plan.target_plans:
            if (
                target.action != "noop"
                and target.live_path_is_symlink
                and target.target_kind != "directory"
                and target.file_symlink_mode == "prompt"
            ):
                updated_targets.append(replace(target, allow_live_path_symlink_replace=True))
            else:
                updated_targets.append(target)
        updated_plans.append(replace(plan, target_plans=updated_targets))
    if isinstance(plans, OperationPlan):
        return replace(plans, binding_plans=tuple(updated_plans))
    return updated_plans


def print_push_live_symlink_hazard_warning(
    hazards: Sequence[PushSymlinkHazard],
    *,
    use_color: bool,
    full_paths: bool = False,
) -> None:
    if not hazards:
        return

    replaceable_count = sum(1 for hazard in hazards if hazard.replaceable)
    unsupported_count = len(hazards) - replaceable_count
    print(f"  {cli_style.render_payload_section_label('warning: symlinked live targets detected', use_color=use_color)}")
    print(
        "  "
        + " · ".join(
            [
                cli_style.render_summary_stat(label="replaceable", value=replaceable_count, use_color=use_color),
                cli_style.render_summary_stat(label="unsupported", value=unsupported_count, use_color=use_color),
            ]
        )
    )
    for hazard in hazards:
        status_label = "replaceable" if hazard.replaceable else "unsupported"
        status_text = cli_style.render_menu_badge(f"[{status_label}]", use_color=use_color)
        live_path = display_cli_path(hazard.live_path, full_paths=full_paths)
        package_target_label = cli_style.render_package_target_label(
            repo_name=hazard.binding_label.split(":", 1)[0],
            package_id=hazard.package_id,
            target_name=hazard.target_name,
            use_color=use_color,
        )
        print(f"    {status_text} {package_target_label}")
        print(f"      {live_path} -> {hazard.symlink_target}")


def effective_execution_mode(*, dry_run_requested: bool) -> str:
    return "dry-run" if dry_run_requested else "execute"


def count_hook_commands(plans: Sequence[Any]) -> int:
    binding_hook_count = sum(len(hook_plans) for plan in binding_plans_for_operation_plan(plans) for hook_plans in plan.hooks.values())
    repo_hook_count = 0
    if isinstance(plans, OperationPlan):
        repo_hook_count = sum(len(hook_plans) for hooks in plans.repo_hooks.values() for hook_plans in hooks.values())
    return binding_hook_count + repo_hook_count


def display_cli_path(reference_path: Path | str, *, full_paths: bool) -> str:
    return display_review_path(reference_path, compact=not full_paths)


def _print_payload_header(header_text: str, *, use_color: bool, file=None) -> None:
    output_file = sys.stdout if file is None else file
    print(file=output_file)
    if not use_color:
        print(f"{cli_style.MENU_HEADER_MARKER} {header_text}", file=output_file)
        return
    print(
        f"{cli_style.style_text(cli_style.MENU_HEADER_MARKER, *cli_style.MENU_HEADER_MARKER_STYLE)} "
        f"{cli_style.style_text(header_text, '1')}",
        file=output_file,
    )


def _print_payload_package_header(*, repo_name: str, package_id: str, profile: str, use_color: bool) -> None:
    if not use_color:
        print(
            f"  {cli_style.MENU_HEADER_MARKER} "
            f"{cli_style.package_profile_label_text(repo_name=repo_name, package_id=package_id, profile=profile)}"
        )
        return
    print(
        f"  {cli_style.style_text(cli_style.MENU_HEADER_MARKER, *cli_style.MENU_HEADER_MARKER_STYLE)} "
        f"{cli_style.render_package_profile_label(repo_name=repo_name, package_id=package_id, profile=profile, use_color=True)}"
    )


def _render_payload_hook_label(hook_name: str, *, use_color: bool) -> str:
    return cli_style.render_menu_badge(f"[{hook_name}]", use_color=use_color)


def _render_payload_action(action: str, *, use_color: bool) -> str:
    return cli_style.render_payload_action(action, use_color=use_color)


def _print_payload_target_item(item: Any, *, full_paths: bool, use_color: bool) -> None:
    if getattr(item, "kind", "target") == "target_hook_noop":
        target_label = cli_style.render_package_target_label(
            repo_name=item.binding_label.split(":", 1)[0],
            package_id=item.package_id,
            target_name=item.target_name,
            bound_profile=getattr(item, "bound_profile", None),
            use_color=use_color,
        )
        summary = cli_style.render_annotation_parentheses(
            cli_style.hook_summary_text(getattr(item, "hook_names", ())),
            use_color=use_color,
        )
        print(f"      {target_label} -> {_render_payload_action('hooks', use_color=use_color)}{summary}")
        return
    source_path = display_cli_path(item.source_path, full_paths=full_paths)
    destination_path = display_cli_path(item.destination_path, full_paths=full_paths)
    arrow_text = cli_style.style_text("->", *cli_style.MENU_HINT_STYLE) if use_color else "->"
    repo_name = item.binding_label.split(":", 1)[0]
    target_label = cli_style.render_package_target_label(
        repo_name=repo_name,
        package_id=item.package_id,
        target_name=item.target_name,
        bound_profile=getattr(item, "bound_profile", None),
        use_color=use_color,
    )
    print(f"      {target_label} -> {_render_payload_action(item.action, use_color=use_color)}")
    print(f"        {source_path} {arrow_text} {destination_path}")


def collect_payload_package_sections(
    plans: Sequence[Any],
    *,
    operation: str,
    collect_pending_selection_items_for_operation: CollectPendingSelectionItems,
) -> list[PayloadPackageSection]:
    package_sections: dict[tuple[str, str, str], PayloadPackageSection] = {}

    for plan in binding_plans_for_operation_plan(plans):
        targets_by_package: dict[str, list[Any]] = {}
        for item in collect_pending_selection_items_for_operation([plan], operation=operation):
            if getattr(item, "kind", "target") == "package_hook_noop":
                continue
            targets_by_package.setdefault(item.package_id, []).append(item)

        package_hooks_by_package: dict[str, dict[str, list[Any]]] = {}
        target_hooks_by_package: dict[str, dict[str, dict[str, list[Any]]]] = {}
        for hook_name, hook_plans in plan.hooks.items():
            for hook_plan in hook_plans:
                if hook_plan.scope_kind == "target" and hook_plan.package_id is not None and hook_plan.target_name is not None:
                    target_hooks_by_package.setdefault(hook_plan.package_id, {}).setdefault(hook_plan.target_name, {}).setdefault(hook_name, []).append(hook_plan)
                    continue
                if hook_plan.package_id is not None:
                    package_hooks_by_package.setdefault(hook_plan.package_id, {}).setdefault(hook_name, []).append(hook_plan)

        for package_id in plan.package_ids:
            package_targets = targets_by_package.get(package_id, [])
            package_hooks = package_hooks_by_package.get(package_id, {})
            target_hooks = target_hooks_by_package.get(package_id, {})
            if not package_targets and not package_hooks and not target_hooks:
                continue
            section_key = (plan.binding.repo, package_id, plan.binding.profile)
            section = package_sections.get(section_key)
            if section is None:
                section = PayloadPackageSection(
                    repo_name=plan.binding.repo,
                    package_id=package_id,
                    profile=plan.binding.profile,
                    package_hooks={},
                    target_hooks={},
                    targets=[],
                )
                package_sections[section_key] = section
            for hook_name, hook_plans in package_hooks.items():
                section.package_hooks.setdefault(hook_name, []).extend(hook_plans)
            for target_name, target_hook_map in target_hooks.items():
                target_section = section.target_hooks.setdefault(target_name, {})
                for hook_name, hook_plans in target_hook_map.items():
                    target_section.setdefault(hook_name, []).extend(hook_plans)
            section.targets.extend(package_targets)

    return list(package_sections.values())


def collect_payload_repo_hook_sections(plans: Sequence[Any]) -> list[PayloadRepoHookSection]:
    if not isinstance(plans, OperationPlan):
        return []
    return [
        PayloadRepoHookSection(repo_name=repo_name, hooks=plans.repo_hooks.get(repo_name, {}))
        for repo_name in plans.repo_order
        if plans.repo_hooks.get(repo_name)
    ]


def emit_payload(
    *,
    operation: str,
    plans: Sequence[Any],
    json_output: bool,
    mode: str,
    full_paths: bool = False,
    use_color: bool,
    collect_pending_selection_items_for_operation: CollectPendingSelectionItems,
) -> int:
    visible_plans = []
    for plan in binding_plans_for_operation_plan(plans):
        visible_target_ids = {
            (hook.package_id, hook.target_name)
            for hook_plans in plan.hooks.values()
            for hook in hook_plans
            if getattr(hook, "scope_kind", "package") == "target" and hook.package_id is not None and hook.target_name is not None
        }
        visible_targets = [
            target
            for target in plan.target_plans
            if target.action != "noop" or (target.package_id, target.target_name) in visible_target_ids
        ]
        visible_hooks = {name: items for name, items in plan.hooks.items() if items}
        if not visible_targets and not visible_hooks:
            continue
        visible_plans.append(replace(plan, target_plans=visible_targets, hooks=visible_hooks))
    visible_operation_plans: Sequence[Any]
    if isinstance(plans, OperationPlan):
        visible_operation_plans = replace(plans, binding_plans=tuple(visible_plans))
    else:
        visible_operation_plans = visible_plans
    warnings = collect_push_live_symlink_hazards(visible_operation_plans) if operation == "push" else []
    payload = {
        "mode": mode,
        "operation": operation,
        "bindings": [plan.to_dict() for plan in visible_plans],
    }
    if isinstance(visible_operation_plans, OperationPlan) and visible_operation_plans.repo_hooks:
        payload["repo_hooks"] = {
            repo_name: {hook_name: [item.to_dict() for item in items] for hook_name, items in hooks.items()}
            for repo_name, hooks in visible_operation_plans.repo_hooks.items()
        }
    if warnings:
        payload["warnings"] = [warning.to_dict() for warning in warnings]
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    package_sections = collect_payload_package_sections(
        visible_operation_plans,
        operation=operation,
        collect_pending_selection_items_for_operation=collect_pending_selection_items_for_operation,
    )
    repo_hook_sections = collect_payload_repo_hook_sections(visible_operation_plans)
    target_items = [item for section in package_sections for item in section.targets]
    _print_payload_header(f"{mode} {operation}", use_color=use_color)
    if warnings:
        print_push_live_symlink_hazard_warning(warnings, use_color=use_color, full_paths=full_paths)
    print(
        "  "
        + cli_style.render_payload_section_label(
            "preview only; no files or hooks will be changed",
            use_color=use_color,
        )
    )
    print(
        "  "
        + " · ".join(
            [
                cli_style.render_summary_stat(label="packages", value=len(package_sections), use_color=use_color),
                cli_style.render_summary_stat(label="target actions", value=len(target_items), use_color=use_color),
                cli_style.render_summary_stat(
                    label="hook commands",
                    value=count_hook_commands(visible_operation_plans),
                    use_color=use_color,
                ),
            ]
        )
    )

    if not package_sections and not repo_hook_sections:
        print()
        print(f"  {cli_style.render_payload_section_label('no pending target actions', use_color=use_color)}")
        return 0

    for repo_section in repo_hook_sections:
        print()
        if not use_color:
            print(f"  {cli_style.MENU_HEADER_MARKER} {repo_section.repo_name}")
        else:
            print(
                f"  {cli_style.style_text(cli_style.MENU_HEADER_MARKER, *cli_style.MENU_HEADER_MARKER_STYLE)} "
                f"{cli_style.style_text(repo_section.repo_name, *cli_style.MENU_REPO_STYLE)}"
            )
        print(f"    {cli_style.render_payload_section_label('repo hooks:', use_color=use_color)}")
        for hook_name, hook_plans in repo_section.hooks.items():
            print(f"      {_render_payload_hook_label(hook_name, use_color=use_color)}")
            for index, hook_plan in enumerate(hook_plans, start=1):
                for line in render_hook_command_lines(hook_plan.command, command_count=len(hook_plans), index=index):
                    print(f"  {line}")

    for section in package_sections:
        print()
        _print_payload_package_header(
            repo_name=section.repo_name,
            package_id=section.package_id,
            profile=section.profile,
            use_color=use_color,
        )

        print(f"    {cli_style.render_payload_section_label('targets:', use_color=use_color)}")
        if section.targets:
            for item in section.targets:
                _print_payload_target_item(item, full_paths=full_paths, use_color=use_color)
        else:
            print(f"      {cli_style.render_payload_section_label('none', use_color=use_color)}")

        if section.package_hooks:
            print(f"    {cli_style.render_payload_section_label('package hooks:', use_color=use_color)}")
            for hook_name, hook_plans in section.package_hooks.items():
                print(f"      {_render_payload_hook_label(hook_name, use_color=use_color)}")
                for index, hook_plan in enumerate(hook_plans, start=1):
                    for line in render_hook_command_lines(
                        hook_plan.command,
                        command_count=len(hook_plans),
                        index=index,
                    ):
                        print(f"  {line}")
        if section.target_hooks:
            print(f"    {cli_style.render_payload_section_label('target hooks:', use_color=use_color)}")
            for target_name, hook_map in section.target_hooks.items():
                target_label = cli_style.render_package_target_label(
                    repo_name=section.repo_name,
                    package_id=section.package_id,
                    target_name=target_name,
                    use_color=use_color,
                )
                print(f"      {target_label}")
                for hook_name, hook_plans in hook_map.items():
                    print(f"        {_render_payload_hook_label(hook_name, use_color=use_color)}")
                    for index, hook_plan in enumerate(hook_plans, start=1):
                        for line in render_hook_command_lines(
                            hook_plan.command,
                            command_count=len(hook_plans),
                            index=index,
                        ):
                            print(f"    {line}")
    return 0


def execution_step_display(step: Any, *, full_paths: bool) -> str:
    if step.hook_plan is not None:
        return step.hook_plan.command
    target = step.target_plan
    if target is None:
        return ""
    if step.kind == "chmod":
        reference_path = target.live_path if step.binding_plan.operation == "push" else target.repo_path
        chmod_mode = target.chmod or "?"
        return f"{chmod_mode} {display_cli_path(reference_path, full_paths=full_paths)}"
    if step.action == "reconcile":
        return display_cli_path(target.live_path, full_paths=full_paths)
    if step.directory_item is not None:
        reference_path = (
            step.directory_item.live_path
            if step.binding_plan.operation == "push"
            else step.directory_item.repo_path
        )
        return display_cli_path(reference_path, full_paths=full_paths)
    reference_path = target.live_path if step.binding_plan.operation == "push" else target.repo_path
    return display_cli_path(reference_path, full_paths=full_paths)


def _print_execution_header(*, session: Any, use_color: bool) -> None:
    step_count = sum(len(repo.steps) for repo in session.repos)
    _print_payload_header(f"executing {session.operation}", use_color=use_color)
    print(
        "  "
        + " · ".join(
            [
                cli_style.render_summary_stat(label="repos", value=len(session.repos), use_color=use_color),
                cli_style.render_summary_stat(label="packages", value=len(session.packages), use_color=use_color),
                cli_style.render_summary_stat(label="steps", value=step_count, use_color=use_color),
            ]
        )
    )
    if not session.repos:
        print()
        print(f"  {cli_style.render_payload_section_label('no pending target actions', use_color=use_color)}")


def _print_execution_package_start(package: Any, *, use_color: bool) -> None:
    print()
    _print_payload_package_header(
        repo_name=package.repo_name,
        package_id=package.package_id,
        profile=package.profile,
        use_color=use_color,
    )


def _print_execution_step_start(
    _package: Any,
    step: Any,
    index: int,
    total: int,
    *,
    full_paths: bool,
    use_color: bool,
) -> None:
    print(
        f"    [{index}/{total}] "
        f"{cli_style.render_execution_action(step.action, use_color=use_color):<11} "
        f"{execution_step_display(step, full_paths=full_paths)}"
    )


def _print_execution_step_finish(_package: Any, step_result: Any, _index: int, _total: int, *, use_color: bool) -> None:
    if step_result.status == "ok":
        print(f"      {cli_style.render_execution_status('ok', use_color=use_color)}")
        return
    if step_result.error:
        print(f"      {step_result.error}")
    print(f"      {_render_execution_status_label(step_result.status, step_result.skip_reason, use_color=use_color)}")


def _print_execution_package_finish(package_result: Any, *, use_color: bool) -> None:
    if package_result.status == "skipped":
        print(f"    {_render_execution_status_label(package_result.status, package_result.skip_reason, use_color=use_color)}")


def _render_execution_status_label(status: str, skip_reason: str | None, *, use_color: bool) -> str:
    if status == "skipped" and skip_reason == "guard":
        return f"{cli_style.render_execution_status('skipped', use_color=use_color)}{cli_style.render_annotation_parentheses(skip_reason, use_color=use_color)}"
    return cli_style.render_execution_status(status, use_color=use_color)


def emit_execution_result(*, result: Any, json_output: bool) -> int:
    if json_output:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return result.exit_code


def execute_plans(
    *,
    operation: str,
    plans: Sequence[Any],
    json_output: bool,
    full_paths: bool = False,
    use_color: bool,
    run_noop: bool = False,
    assume_yes: bool = False,
    snapshot_config: Any | None = None,
):
    session = build_execution_session(plans, operation=operation, run_noop=run_noop)
    snapshot = None

    def ensure_push_snapshot(step: Any) -> None:
        nonlocal snapshot
        if operation != "push" or snapshot_config is None or snapshot is not None or step.kind == "hook":
            return
        snapshot = create_push_snapshot(plans, snapshot_config)

    def on_step_start(package: Any, step: Any, index: int, total: int) -> None:
        ensure_push_snapshot(step)
        if not json_output:
            _print_execution_step_start(
                package,
                step,
                index,
                total,
                full_paths=full_paths,
                use_color=use_color,
            )

    def finalize_snapshot(execution_result: Any) -> None:
        nonlocal snapshot
        if snapshot is None:
            return
        mark_snapshot_status(snapshot, "applied" if execution_result.exit_code == 0 else "failed")
        prune_snapshots(snapshot_config.path, max_generations=snapshot_config.max_generations)
        snapshot = None

    with sudo_session():
        _preflight_execution_session_sudo(session)
        if json_output:
            try:
                execution_result = execute_session(session, stream_output=False, assume_yes=assume_yes, on_step_start=on_step_start)
            except Exception:
                if snapshot is not None:
                    mark_snapshot_status(snapshot, "failed")
                    prune_snapshots(snapshot_config.path, max_generations=snapshot_config.max_generations)
                raise
            finalize_snapshot(execution_result)
            return execution_result
        _print_execution_header(session=session, use_color=use_color)
        if not session.repos:
            try:
                execution_result = execute_session(session, stream_output=True, assume_yes=assume_yes, on_step_start=on_step_start)
            except Exception:
                if snapshot is not None:
                    mark_snapshot_status(snapshot, "failed")
                    prune_snapshots(snapshot_config.path, max_generations=snapshot_config.max_generations)
                raise
            finalize_snapshot(execution_result)
            return execution_result
        try:
            execution_result = execute_session(
                session,
                stream_output=True,
                assume_yes=assume_yes,
                on_package_start=lambda package: _print_execution_package_start(package, use_color=use_color),
                on_step_start=on_step_start,
                on_step_finish=lambda package, step_result, index, total: _print_execution_step_finish(
                    package,
                    step_result,
                    index,
                    total,
                    use_color=use_color,
                ),
                on_package_finish=lambda package_result: _print_execution_package_finish(
                    package_result,
                    use_color=use_color,
                ),
            )
        except Exception:
            if snapshot is not None:
                mark_snapshot_status(snapshot, "failed")
                prune_snapshots(snapshot_config.path, max_generations=snapshot_config.max_generations)
            raise
        finalize_snapshot(execution_result)
        return execution_result


def run_execution(
    *,
    operation: str,
    plans: Sequence[Any],
    json_output: bool,
    full_paths: bool = False,
    use_color: bool,
    run_noop: bool = False,
    assume_yes: bool = False,
) -> int:
    return emit_execution_result(
        result=execute_plans(
            operation=operation,
            plans=plans,
            json_output=json_output,
            full_paths=full_paths,
            use_color=use_color,
            run_noop=run_noop,
            assume_yes=assume_yes,
        ),
        json_output=json_output,
    )


def _render_tracked_issue_label(engine: Any, issue: Any, *, use_color: bool) -> str:
    bound_profile: str | None = None
    try:
        repo = engine.get_repo(issue.repo)
    except ValueError:
        repo = None
    if repo is not None and issue.selector in repo.packages:
        package = repo.resolve_package(issue.selector)
        if package.binding_mode == "multi_instance":
            bound_profile = issue.profile
    return cli_style.render_package_label(
        repo_name=issue.repo,
        package_id=issue.selector,
        bound_profile=bound_profile,
        use_color=use_color,
    )


def emit_tracked_packages(
    *,
    engine: Any,
    packages: Sequence[Any],
    invalid_bindings: Sequence[Any],
    json_output: bool,
    use_color: bool,
) -> int:
    payload = {
        "mode": "dry-run",
        "operation": "list-tracked",
        "packages": [package.to_dict() for package in packages],
        "invalid_bindings": [binding.to_dict() for binding in invalid_bindings],
    }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    for package in packages:
        print(
            cli_style.render_package_label(
                repo_name=package.repo,
                package_id=package.package_id,
                bound_profile=package.bound_profile,
                use_color=use_color,
            )
            + f" {cli_style.render_tracked_state(package.state, use_color=use_color)}"
        )
    for binding in invalid_bindings:
        print(
            f"{_render_tracked_issue_label(engine, binding, use_color=use_color)} "
            f"{cli_style.render_tracked_state(binding.state, use_color=use_color)}"
        )
    return 0


_DOCTOR_CHECK_CATEGORY_ORDER = {
    "dependencies": 0,
    "environment": 1,
    "repository": 2,
    "state": 3,
    "other": 4,
}


def _doctor_check_category(check: Any) -> str:
    if check.key.startswith("dependency_"):
        return "dependencies"
    if check.key == "editor":
        return "environment"
    if check.key in {"repo_path", "profiles"}:
        return "repository"
    if check.key in {"state_dir", "bindings_file", "orphan_bindings_file", "snapshots"}:
        return "state"
    return "other"


def _group_doctor_checks(checks: Sequence[Any]) -> list[tuple[str, list[Any]]]:
    grouped: dict[str, list[Any]] = {}
    for check in checks:
        grouped.setdefault(_doctor_check_category(check), []).append(check)
    return [
        (category, grouped[category])
        for category in sorted(grouped, key=lambda category: (_DOCTOR_CHECK_CATEGORY_ORDER.get(category, 999), category))
    ]


def _print_doctor_check_list(*, checks: Sequence[Any], use_color: bool) -> None:
    for category, category_checks in _group_doctor_checks(checks):
        print(f"    {cli_style.render_payload_section_label(f'{category}:', use_color=use_color)}")
        for check in category_checks:
            owner_label = f"[{check.repo_name}] " if check.repo_name is not None else ""
            print(f"    - {owner_label}{check.detail}")
            if check.path is not None:
                print(f"        {cli_style.render_error_metadata_label('path:', use_color=use_color)} {check.path}")
            if check.hint is not None:
                print(f"        {cli_style.render_error_metadata_label('hint:', use_color=use_color)} {check.hint}")


def emit_doctor_summary(*, engine: Any, summary: Any, json_output: bool, use_color: bool) -> int:
    payload = summary.to_dict()
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if summary.ok else 2

    _print_payload_header("Doctor", use_color=use_color)
    print(f"  {cli_style.render_error_metadata_label('status:', use_color=use_color)} {'ok' if summary.ok else 'failed'}")
    print(f"  {cli_style.render_error_metadata_label('config:', use_color=use_color)} {summary.config_path}")
    print(f"  {cli_style.render_error_metadata_label('repos:', use_color=use_color)} {summary.repo_count}")
    print(f"  {cli_style.render_error_metadata_label('checks:', use_color=use_color)} {len(summary.checks)}")
    if summary.failed_checks:
        print(
            f"  {cli_style.render_error_metadata_label('failed checks:', use_color=use_color)} {len(summary.failed_checks)}"
        )
        _print_doctor_check_list(checks=summary.failed_checks, use_color=use_color)
    if summary.warning_checks:
        print(
            f"  {cli_style.render_error_metadata_label('warnings:', use_color=use_color)} {len(summary.warning_checks)}"
        )
        _print_doctor_check_list(checks=summary.warning_checks, use_color=use_color)
    if summary.invalid_bindings:
        print(
            f"  {cli_style.render_error_metadata_label('invalid bindings:', use_color=use_color)} {len(summary.invalid_bindings)}"
        )
        print(
            "  issues:"
        )
        for issue in summary.invalid_bindings:
            print(
                "  - "
                f"{_render_tracked_issue_label(engine, issue, use_color=use_color)} "
                f"{cli_style.render_tracked_state(issue.state, use_color=use_color)} — {issue.message}"
            )
    if summary.ok:
        print("  no issues found")
    return 0 if summary.ok else 2


def emit_variables(*, variables: Sequence[Any], json_output: bool, use_color: bool) -> int:
    payload = {
        "mode": "dry-run",
        "operation": "list-vars",
        "variables": [variable.to_dict() for variable in variables],
    }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    for variable in variables:
        binding_label = cli_style.render_binding_label(
            repo_name=variable.repo,
            selector=variable.selector,
            profile=variable.profile,
            use_color=use_color,
        )
        print(f"{cli_style.render_variable_name(variable.variable, use_color=use_color)} ({binding_label})")
    return 0


def emit_variable_detail(*, variable_detail: Any, json_output: bool, use_color: bool) -> int:
    payload = {
        "mode": "dry-run",
        "operation": "info-var",
        "variable": variable_detail.to_dict(),
    }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    header_text = variable_detail.variable
    if use_color:
        print(cli_style.style_text(header_text, "1"))
    else:
        print(header_text)

    for index, occurrence in enumerate(variable_detail.occurrences):
        if index > 0:
            print()
        binding_label = cli_style.render_binding_label(
            repo_name=occurrence.repo,
            selector=occurrence.selector,
            profile=occurrence.profile,
            use_color=use_color,
        )
        print()
        print(cli_style.render_info_section_header("reason", use_color=use_color))
        print(f"      {binding_label}")
        print()
        print(cli_style.render_info_section_header("resolved value", use_color=use_color))
        print(f"      {_format_variable_value(occurrence.value)}")
        print()
        print(cli_style.render_info_section_header("provenance", use_color=use_color))
        provenance_label = occurrence.provenance.source_label
        if occurrence.provenance.source_kind in {"package", "profile"}:
            provenance_label = f"{occurrence.provenance.source_kind} {provenance_label}"
        print(
            f"      {cli_style.render_payload_section_label(provenance_label, use_color=use_color)}: "
            f"{occurrence.provenance.source_path}"
        )
    return 0


def emit_forgotten_binding(*, binding: Any, still_tracked_package: Any, json_output: bool, use_color: bool) -> int:
    payload = {
        "mode": "state-only",
        "operation": "untrack",
        "binding": {
            "repo": binding.repo,
            "selector": binding.selector,
            "profile": binding.profile,
        },
    }
    if still_tracked_package is not None:
        payload["still_tracked_package"] = {
            "repo": still_tracked_package.repo,
            "package_id": still_tracked_package.package_id,
            "bindings": [
                {
                    **binding_detail.binding.to_dict(),
                    "tracked_reason": binding_detail.tracked_reason,
                }
                for binding_detail in still_tracked_package.bindings
            ],
        }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print(
        "untracked "
        + cli_style.render_binding_reference(binding, use_color=use_color)
    )
    if still_tracked_package is not None:
        print(
            f"{cli_style.render_package_label(repo_name=still_tracked_package.repo, package_id=still_tracked_package.package_id, bound_profile=still_tracked_package.bound_profile, use_color=use_color)} "
            "remains tracked via:"
        )
        for binding_detail in still_tracked_package.bindings:
            print(
                f"  {cli_style.render_tracked_reason(binding_detail.tracked_reason, use_color=use_color)}: "
                + cli_style.render_binding_label(
                    repo_name=binding_detail.binding.repo,
                    selector=binding_detail.binding.selector,
                    profile=binding_detail.binding.profile,
                    use_color=use_color,
                )
            )
    return 0


def emit_tracked_binding(*, binding: Any, json_output: bool, use_color: bool) -> int:
    payload = {
        "mode": "state-only",
        "operation": "track",
        "binding": {
            "repo": binding.repo,
            "selector": binding.selector,
            "profile": binding.profile,
        },
    }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print(f"tracked {cli_style.render_binding_reference(binding, use_color=use_color)}")
    return 0


def emit_add_result(*, result: Any, json_output: bool, use_color: bool) -> int:
    if json_output:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 0

    package_label = cli_style.render_package_label(
        repo_name=result.repo_name,
        package_id=result.package_id,
        package_first=True,
        include_repo_context=True,
        use_color=use_color,
    )
    action = "created" if result.created_package else "updated"
    print(f"{action} package config {package_label}")
    print(f"  manifest: {result.manifest_path}")
    print(f"  target:   {result.target_name} [{result.target_kind}]")
    print(f"  source:   {result.source_path}")
    print(f"  path:     {result.config_path}")
    if result.chmod is not None:
        print(f"  chmod:    {result.chmod}")
    manifest_only_note = "manifest only; repo source files were not copied"
    if use_color:
        manifest_only_note = cli_style.style_text(manifest_only_note, *cli_style.MENU_HINT_STYLE)
    print(f"  {manifest_only_note}")
    return 0


def emit_kept_add_result(*, repo_name: str, package_id: str, json_output: bool, use_color: bool) -> int:
    payload = {
        "mode": "config-only",
        "operation": "add",
        "repo": repo_name,
        "package_id": package_id,
        "written": False,
    }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print(
        "kept package config unchanged "
        + cli_style.render_package_label(
            repo_name=repo_name,
            package_id=package_id,
            package_first=True,
            include_repo_context=True,
            use_color=use_color,
        )
    )
    return 0


def emit_noop_add_result(*, json_output: bool) -> int:
    payload = {
        "mode": "config-only",
        "operation": "add",
        "written": False,
        "changed": False,
    }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print("No package config changes.")
    return 0


def emit_kept_binding(*, binding: Any, json_output: bool, use_color: bool) -> int:
    payload = {
        "mode": "state-only",
        "operation": "track",
        "binding": {
            "repo": binding.repo,
            "selector": binding.selector,
            "profile": binding.profile,
        },
        "recorded": False,
    }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print(f"kept existing tracked package entry {cli_style.render_binding_reference(binding, use_color=use_color)}")
    return 0


def emit_skipped_tracking(*, binding: Any, json_output: bool, use_color: bool) -> int:
    payload = {
        "mode": "state-only",
        "operation": "track",
        "binding": {
            "repo": binding.repo,
            "selector": binding.selector,
            "profile": binding.profile,
        },
        "recorded": False,
    }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print(f"skipped tracking {cli_style.render_binding_reference(binding, use_color=use_color)}")
    return 0


def render_hook_command_lines(command: str, *, command_count: int, index: int) -> list[str]:
    command_lines = command.splitlines() or [""]
    # Number multi-command hooks so users can tell distinct commands apart without cluttering single-command hooks.
    first_prefix = f"      [{index}] " if command_count > 1 else "      "
    continuation_prefix = " " * len(first_prefix)
    return [
        f"{first_prefix}{command_lines[0]}",
        *[f"{continuation_prefix}{line}" for line in command_lines[1:]],
    ]


def _render_target_ref_chain(package_detail: Any, target_ref: Any, *, use_color: bool) -> str:
    chain_labels: list[str] = [
        cli_style.style_text(target_ref.target_name, "1") if use_color else target_ref.target_name
    ]
    for step in target_ref.chain[1:]:
        chain_labels.append(
            cli_style.render_package_label(
                repo_name=package_detail.repo,
                package_id=step.package_id,
                target_name=step.target_name,
                package_first=True,
                include_repo_context=False,
                use_color=use_color,
            )
        )
    arrow_text = cli_style.style_text("->", *cli_style.MENU_HINT_STYLE) if use_color else "->"
    return f" {arrow_text} ".join(chain_labels)


def emit_tracked_package_detail(*, package_detail: Any, json_output: bool, use_color: bool) -> int:
    payload = {
        "mode": "dry-run",
        "operation": "info-tracked",
        "package": package_detail.to_dict(),
    }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print(
        cli_style.render_package_label(
            repo_name=package_detail.repo,
            package_id=package_detail.package_id,
            bound_profile=package_detail.bound_profile,
            use_color=use_color,
        )
    )
    if package_detail.description:
        print(f"  {package_detail.description}")
    if package_detail.bindings:
        print()
        print(cli_style.render_info_section_header("provenance", use_color=use_color))
    for binding in package_detail.bindings:
        binding_label = cli_style.render_binding_label(
            repo_name=binding.binding.repo,
            selector=binding.binding.selector,
            profile=binding.binding.profile,
            use_color=use_color,
        )
        print(f"    {cli_style.render_tracked_reason(binding.tracked_reason, use_color=use_color)}: {binding_label}")

    bindings_with_hooks = [binding for binding in package_detail.bindings if binding.hooks]
    if bindings_with_hooks:
        print()
        print(cli_style.render_info_section_header("hooks", use_color=use_color))
    # Hook output stays package-centric here. Under the current tracked-winner model,
    # a package instance has one effective hook-bearing binding, so repeating the
    # provenance binding under ::hooks only adds noise.
    for binding in bindings_with_hooks:
        for hook_name, hook_plans in binding.hooks.items():
            hook_label = f"[{hook_name}]"
            if use_color:
                hook_label = cli_style.style_text(hook_label, *cli_style.MENU_HINT_STYLE)
            print(f"    {hook_label}")
            for index, hook_plan in enumerate(hook_plans, start=1):
                for line in render_hook_command_lines(
                    hook_plan.command,
                    command_count=len(hook_plans),
                    index=index,
                ):
                    print(line)

    if package_detail.owned_targets:
        print()
        print(cli_style.render_info_section_header("owned targets", use_color=use_color))
    for target in package_detail.owned_targets:
        target_name = cli_style.style_text(target.target.target_name, '1') if use_color else target.target.target_name
        print(f"    {target_name} -> {target.target.live_path}")
    if package_detail.target_refs:
        print()
        print(cli_style.render_info_section_header("target refs", use_color=use_color))
    for target_ref in package_detail.target_refs:
        print(f"    {_render_target_ref_chain(package_detail, target_ref, use_color=use_color)}")
    return 0


def visible_rollback_actions(actions: Sequence[Any]) -> list[Any]:
    return [action for action in actions if action.action != "noop"]


def build_rollback_review_items(snapshot: Any, actions: Sequence[Any]) -> list[ReviewItem]:
    review_items: list[ReviewItem] = []
    for action in actions:
        if action.action == "noop":
            continue
        review_items.append(
            ReviewItem(
                binding_label=f"snapshot:{snapshot.snapshot_id}",
                package_id="snapshot",
                target_name=str(action.live_path),
                action=action.action,
                operation="rollback",
                repo_path=action.snapshot_path,
                live_path=action.live_path,
                source_path=str(action.snapshot_path),
                destination_path=str(action.live_path),
                before_bytes=action.before_bytes,
                after_bytes=action.after_bytes,
            )
        )
    return review_items


def emit_snapshot_list(
    *,
    snapshots: Sequence[Any],
    json_output: bool,
    max_generations: int | None = None,
    use_color: bool,
) -> int:
    payload = {
        "mode": "dry-run",
        "operation": "list-snapshots",
        "snapshots": [snapshot.to_dict() for snapshot in snapshots],
    }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    _print_payload_header("snapshots", use_color=use_color)
    if max_generations is not None:
        print(
            "  "
            + " · ".join(
                [
                    cli_style.render_summary_stat(label="retained", value=len(snapshots), use_color=use_color),
                    cli_style.render_summary_stat(label="limit", value=max_generations, use_color=use_color),
                ]
            )
        )
    else:
        print(f"  {cli_style.render_summary_stat(label='snapshots', value=len(snapshots), use_color=use_color)}")

    if not snapshots:
        print()
        print(f"  {cli_style.render_payload_section_label('no snapshots', use_color=use_color)}")
        return 0

    for index, snapshot in enumerate(snapshots, start=1):
        print()
        title = cli_style.format_snapshot_timestamp(snapshot.created_at)
        if use_color:
            title = cli_style.style_text(title, "1")
        index_label = cli_style.style_text(f"{index})", *cli_style.MENU_INDEX_STYLE) if use_color else f"{index})"
        print(f"  {index_label} {title}")
        print(f"     {cli_style.render_snapshot_metadata_label('ref:', use_color=use_color)}          {cli_style.render_snapshot_ref(snapshot.snapshot_id, use_color=use_color)}")
        print(f"     {cli_style.render_snapshot_metadata_label('status:', use_color=use_color)}       {cli_style.render_snapshot_status(snapshot.status, use_color=use_color)}")
        print(f"     {cli_style.render_snapshot_metadata_label('paths:', use_color=use_color)}        {snapshot.entry_count}")
        if snapshot.restore_count > 0:
            restore_summary = f"{snapshot.restore_count}x"
            if snapshot.last_restored_at is not None:
                restore_summary += f" · {cli_style.format_snapshot_timestamp(snapshot.last_restored_at)}"
            print(f"     {cli_style.render_snapshot_metadata_label('restored:', use_color=use_color)}     {restore_summary}")
    return 0


def emit_snapshot_detail(*, snapshot: Any, json_output: bool, full_paths: bool = False, use_color: bool) -> int:
    payload = {
        "mode": "dry-run",
        "operation": "info-snapshot",
        "snapshot": snapshot.to_dict(),
    }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    header_text = f"snapshot {snapshot.snapshot_id}"
    if use_color:
        print(cli_style.style_text(header_text, "1"))
    else:
        print(header_text)
    print(f"  {cli_style.render_snapshot_metadata_label('created:', use_color=use_color)}       {cli_style.format_snapshot_timestamp(snapshot.created_at)}")
    print(f"  {cli_style.render_snapshot_metadata_label('status:', use_color=use_color)}        {cli_style.render_snapshot_status(snapshot.status, use_color=use_color)}")
    print(f"  {cli_style.render_snapshot_metadata_label('paths:', use_color=use_color)}         {snapshot.entry_count}")
    print(f"  {cli_style.render_snapshot_metadata_label('restore count:', use_color=use_color)} {snapshot.restore_count}")
    if snapshot.last_restored_at is not None:
        print(
            f"  {cli_style.render_snapshot_metadata_label('last restored:', use_color=use_color)} "
            f"{cli_style.format_snapshot_timestamp(snapshot.last_restored_at)}"
        )

    if snapshot.entries:
        print()
        print(cli_style.render_info_section_header("paths", use_color=use_color))
    for entry in snapshot.entries:
        path_text = display_cli_path(entry.live_path, full_paths=full_paths)
        print(f"    {path_text}")
        print(
            f"      {cli_style.render_snapshot_metadata_label('reason:', use_color=use_color)} "
            f"{cli_style.render_snapshot_reason(entry.push_action, use_color=use_color)}"
        )
        provenance = cli_style.render_snapshot_provenance(
            repo_name=entry.repo_name,
            package_id=entry.package_id,
            target_name=entry.target_name,
            binding_label=entry.binding_label,
            use_color=use_color,
        )
        if provenance is not None:
            print(
                f"      {cli_style.render_snapshot_metadata_label('provenance:', use_color=use_color)} "
                f"{provenance}"
            )
    return 0


def emit_rollback_payload(
    *,
    snapshot: Any,
    actions: Sequence[Any],
    json_output: bool,
    mode: str,
    full_paths: bool = False,
    use_color: bool,
) -> int:
    visible_actions = visible_rollback_actions(actions)
    payload = {
        "mode": mode,
        "operation": "rollback",
        "snapshot": snapshot.to_dict(),
        "actions": [action.to_dict() for action in visible_actions],
    }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    _print_payload_header(f"{mode} rollback", use_color=use_color)
    print(f"  snapshot: {snapshot.snapshot_id}")
    print(f"  created:  {snapshot.created_at}")
    print(f"  status:   {snapshot.status}")
    print(f"  {cli_style.render_summary_stat(label='paths', value=len(visible_actions), use_color=use_color)}")
    if not visible_actions:
        print()
        print(f"  {cli_style.render_payload_section_label('no pending target actions', use_color=use_color)}")
        return 0
    for action in visible_actions:
        print(
            f"  [{_render_payload_action(action.action, use_color=use_color)}] "
            f"{display_cli_path(action.snapshot_path, full_paths=full_paths)} -> "
            f"{display_cli_path(action.live_path, full_paths=full_paths)}"
        )
    return 0


def _print_rollback_execution_header(*, snapshot: Any, action_count: int, use_color: bool) -> None:
    _print_payload_header("executing rollback", use_color=use_color)
    print(f"  snapshot: {snapshot.snapshot_id}")
    print(f"  created:  {snapshot.created_at}")
    print(f"  status:   {snapshot.status}")
    print(f"  {cli_style.render_summary_stat(label='paths', value=action_count, use_color=use_color)}")
    if action_count == 0:
        print()
        print(f"  {cli_style.render_payload_section_label('no pending target actions', use_color=use_color)}")


def _print_rollback_execution_step(index: int, total: int, action: Any, *, full_paths: bool, use_color: bool) -> None:
    print(
        f"    [{index}/{total}] "
        f"{cli_style.render_execution_action(action.action, use_color=use_color):<11} "
        f"{display_cli_path(action.live_path, full_paths=full_paths)}"
    )


def emit_rollback_result(*, result: Any, json_output: bool) -> int:
    if json_output:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return result.exit_code


def _emit_error_block(*, header_text: str, fields: Sequence[tuple[str, str]], use_color: bool) -> None:
    _print_payload_header(header_text, use_color=use_color, file=sys.stderr)
    for label, value in fields:
        print(f"  {cli_style.render_error_metadata_label(label, use_color=use_color)} {value}", file=sys.stderr)


def _structured_error_fields(error: Any, *, use_color: bool) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    package_repo = getattr(error, "package_repo", None)
    package_id = getattr(error, "package_id", None)
    if package_repo is not None and package_id is not None:
        fields.append(
            (
                "package:",
                cli_style.render_package_label(
                    repo_name=package_repo,
                    package_id=package_id,
                    use_color=use_color,
                ),
            )
        )
    path = getattr(error, "path", None)
    if path is not None:
        fields.append(("path:", str(path)))
    detail = getattr(error, "detail", None)
    if detail is None:
        detail = str(error) or error.__class__.__name__
    fields.append(("detail:", str(detail)))
    hint = getattr(error, "hint", None)
    if hint is not None:
        fields.append(("hint:", str(hint)))
    return fields


def emit_error(error: Exception, *, use_color: bool) -> None:
    _emit_error_block(
        header_text=error.__class__.__name__,
        fields=_structured_error_fields(error, use_color=use_color),
        use_color=use_color,
    )


def run_rollback_execution(
    *,
    snapshot: Any,
    actions: Sequence[Any],
    json_output: bool,
    full_paths: bool = False,
    use_color: bool,
) -> int:
    visible_actions = visible_rollback_actions(actions)
    if not json_output:
        _print_rollback_execution_header(
            snapshot=snapshot,
            action_count=len(visible_actions),
            use_color=use_color,
        )
    if not visible_actions:
        return 0
    for index, action in enumerate(visible_actions, start=1):
        if not json_output:
            _print_rollback_execution_step(
                index,
                len(visible_actions),
                action,
                full_paths=full_paths,
                use_color=use_color,
            )
    result = execute_rollback(snapshot, visible_actions)
    if not json_output:
        for action_result in result.actions:
            if action_result.status == "ok":
                print(f"      {cli_style.render_execution_status('ok', use_color=use_color)}")
                continue
            if action_result.error:
                print(f"      {action_result.error}")
            print(f"      {cli_style.render_execution_status(action_result.status, use_color=use_color)}")
    return emit_rollback_result(result=result, json_output=json_output)
