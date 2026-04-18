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
from dotman.snapshot import execute_rollback


@dataclass
class PayloadPackageSection:
    repo_name: str
    package_id: str
    profile: str
    hooks: dict[str, list[Any]]
    targets: list[Any]


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
    for plan in plans:
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
    for plan in plans:
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
    return sum(len(hook_plans) for plan in plans for hook_plans in plan.hooks.values())


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

    for plan in plans:
        targets_by_package: dict[str, list[Any]] = {}
        for item in collect_pending_selection_items_for_operation([plan], operation=operation):
            targets_by_package.setdefault(item.package_id, []).append(item)

        hooks_by_package: dict[str, dict[str, list[Any]]] = {}
        for hook_name, hook_plans in plan.hooks.items():
            for hook_plan in hook_plans:
                hooks_by_package.setdefault(hook_plan.package_id, {}).setdefault(hook_name, []).append(hook_plan)

        for package_id in plan.package_ids:
            package_targets = targets_by_package.get(package_id, [])
            package_hooks = hooks_by_package.get(package_id, {})
            if not package_targets and not package_hooks:
                continue
            section_key = (plan.binding.repo, package_id, plan.binding.profile)
            section = package_sections.get(section_key)
            if section is None:
                section = PayloadPackageSection(
                    repo_name=plan.binding.repo,
                    package_id=package_id,
                    profile=plan.binding.profile,
                    hooks={},
                    targets=[],
                )
                package_sections[section_key] = section
            for hook_name, hook_plans in package_hooks.items():
                section.hooks.setdefault(hook_name, []).extend(hook_plans)
            section.targets.extend(package_targets)

    return list(package_sections.values())


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
    for plan in plans:
        visible_targets = [target for target in plan.target_plans if target.action != "noop"]
        visible_hooks = {name: items for name, items in plan.hooks.items() if items}
        if not visible_targets and not visible_hooks:
            continue
        visible_plans.append(replace(plan, target_plans=visible_targets, hooks=visible_hooks))
    warnings = collect_push_live_symlink_hazards(visible_plans) if operation == "push" else []
    payload = {
        "mode": mode,
        "operation": operation,
        "bindings": [plan.to_dict() for plan in visible_plans],
    }
    if warnings:
        payload["warnings"] = [warning.to_dict() for warning in warnings]
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    package_sections = collect_payload_package_sections(
        visible_plans,
        operation=operation,
        collect_pending_selection_items_for_operation=collect_pending_selection_items_for_operation,
    )
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
                    value=count_hook_commands(visible_plans),
                    use_color=use_color,
                ),
            ]
        )
    )

    if not package_sections:
        print()
        print(f"  {cli_style.render_payload_section_label('no pending target actions', use_color=use_color)}")
        return 0

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

        if section.hooks:
            print(f"    {cli_style.render_payload_section_label('hooks:', use_color=use_color)}")
            for hook_name, hook_plans in section.hooks.items():
                print(f"      {_render_payload_hook_label(hook_name, use_color=use_color)}")
                for index, hook_plan in enumerate(hook_plans, start=1):
                    for line in render_hook_command_lines(
                        hook_plan.command,
                        command_count=len(hook_plans),
                        index=index,
                    ):
                        print(f"  {line}")
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
    step_count = sum(len(package.steps) for package in session.packages)
    _print_payload_header(f"executing {session.operation}", use_color=use_color)
    print(
        "  "
        + " · ".join(
            [
                cli_style.render_summary_stat(label="packages", value=len(session.packages), use_color=use_color),
                cli_style.render_summary_stat(label="steps", value=step_count, use_color=use_color),
            ]
        )
    )
    if not session.packages:
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
    print(f"      {cli_style.render_execution_status(step_result.status, use_color=use_color)}")


def _print_execution_package_finish(package_result: Any, *, use_color: bool) -> None:
    if package_result.status == "skipped":
        print(f"    {cli_style.render_execution_status('skipped', use_color=use_color)}")


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
):
    session = build_execution_session(plans, operation=operation, run_noop=run_noop)
    with sudo_session():
        _preflight_execution_session_sudo(session)
        if json_output:
            return execute_session(session, stream_output=False, assume_yes=assume_yes)
        _print_execution_header(session=session, use_color=use_color)
        if not session.packages:
            return execute_session(session, stream_output=True, assume_yes=assume_yes)
        return execute_session(
            session,
            stream_output=True,
            assume_yes=assume_yes,
            on_package_start=lambda package: _print_execution_package_start(package, use_color=use_color),
            on_step_start=lambda package, step, index, total: _print_execution_step_start(
                package,
                step,
                index,
                total,
                full_paths=full_paths,
                use_color=use_color,
            ),
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

    print(f"kept existing tracked binding {cli_style.render_binding_reference(binding, use_color=use_color)}")
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
