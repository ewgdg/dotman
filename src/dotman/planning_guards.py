from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from dotman.models import GuardSkip, HookSpec, PackageSpec, resolved_package_identity_key
from dotman.projection import TargetMetadata, build_package_hook_env, build_repo_hook_env
from dotman.templates import render_template_string

if TYPE_CHECKING:
    from dotman.planning import PackagePlanningInput
    from dotman.progress import ProgressSink


PLANNING_GUARD_EXCLUDED_ENV_KEYS = frozenset({"DOTMAN_ASSUME_YES"})


class GuardPlanningError(ValueError):
    def __init__(
        self,
        *,
        scope_kind: str,
        repo_name: str,
        package_id: str | None,
        bound_profile: str | None,
        target_name: str | None,
        hook_name: str,
        exit_code: int,
        detail: str | None,
    ) -> None:
        self.scope_kind = scope_kind
        self.repo_name = repo_name
        self.package_id = package_id
        self.bound_profile = bound_profile
        self.target_name = target_name
        self.hook_name = hook_name
        self.exit_code = exit_code
        message = f"{hook_name} failed with exit {exit_code}"
        if detail:
            message = f"{message}: {detail}"
        self.detail = message
        super().__init__(message)


def _selected_package(planning_input: "PackagePlanningInput") -> PackageSpec:
    package_id = planning_input.selection.identity.package_id
    return next(
        package
        for package in planning_input.package_context.resolved_packages
        if package.id == package_id
    )


def _package_has_potential_work(
    planning_input: "PackagePlanningInput",
    *,
    operation: str,
    run_noop: bool,
) -> bool:
    if planning_input.target_metadata:
        return True
    package = _selected_package(planning_input)
    for hook_name in (f"pre_{operation}", f"post_{operation}"):
        hook_spec = (package.hooks or {}).get(hook_name)
        if hook_spec is None or not hook_spec.commands:
            continue
        if run_noop or hook_spec.run_noop or any(command.run_noop for command in hook_spec.commands):
            return True
    return False


def _first_nonempty_output_line(stderr: str, stdout: str) -> str | None:
    for output in (stderr, stdout):
        for line in output.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
    return None


def _run_planning_guard(
    *,
    guard_spec: HookSpec,
    guard_name: str,
    context: dict[str, Any],
    env: dict[str, str],
    scope_kind: str,
    repo_name: str,
    package_id: str | None = None,
    bound_profile: str | None = None,
    target_name: str | None = None,
) -> GuardSkip | None:
    from dotman.execution import INTERRUPTED_EXIT_CODE, run_command

    for command_spec in guard_spec.commands:
        command = render_template_string(
            command_spec.run,
            context,
            base_dir=guard_spec.declared_in,
            source_path=guard_spec.declared_in,
        ).strip()
        exit_code, stdout, stderr = run_command(
            command=command,
            cwd=guard_spec.declared_in,
            env=dict(env),
            stream_output=False,
            interactive=False,
            elevation=command_spec.elevation,
            excluded_env_keys=PLANNING_GUARD_EXCLUDED_ENV_KEYS,
        )
        if exit_code == 0:
            continue
        if exit_code == INTERRUPTED_EXIT_CODE:
            raise KeyboardInterrupt
        detail = _first_nonempty_output_line(stderr, stdout)
        if exit_code == 100:
            return GuardSkip(
                scope_kind=scope_kind,
                repo_name=repo_name,
                package_id=package_id,
                bound_profile=bound_profile,
                target_name=target_name,
                reason=detail,
            )
        raise GuardPlanningError(
            scope_kind=scope_kind,
            repo_name=repo_name,
            package_id=package_id,
            bound_profile=bound_profile,
            target_name=target_name,
            hook_name=guard_name,
            exit_code=exit_code,
            detail=detail,
        )
    return None


def _repo_has_potential_work(
    repo_inputs: list["PackagePlanningInput"],
    *,
    operation: str,
    run_noop: bool,
) -> bool:
    if any(
        _package_has_potential_work(
            planning_input,
            operation=operation,
            run_noop=run_noop,
        )
        for planning_input in repo_inputs
    ):
        return True
    repo = repo_inputs[0].repo
    for hook_name in (f"pre_{operation}", f"post_{operation}"):
        hook_spec = (repo.hooks or {}).get(hook_name)
        if hook_spec is None or not hook_spec.commands:
            continue
        if run_noop or hook_spec.run_noop or any(command.run_noop for command in hook_spec.commands):
            return True
    return False


def _evaluate_repo_guards(
    planning_inputs: list["PackagePlanningInput"],
    *,
    operation: str,
    run_noop: bool,
    sink: "ProgressSink | None" = None,
) -> tuple[list["PackagePlanningInput"], tuple[GuardSkip, ...]]:
    inputs_by_repo: dict[str, list["PackagePlanningInput"]] = defaultdict(list)
    for planning_input in planning_inputs:
        inputs_by_repo[planning_input.repo.config.name].append(planning_input)

    admitted_inputs: list["PackagePlanningInput"] = []
    guard_skips: list[GuardSkip] = []
    guard_name = f"guard_{operation}"
    for repo_name, repo_inputs in inputs_by_repo.items():
        if not _repo_has_potential_work(repo_inputs, operation=operation, run_noop=run_noop):
            admitted_inputs.extend(repo_inputs)
            continue
        repo = repo_inputs[0].repo
        guard_spec = (repo.hooks or {}).get(guard_name)
        if guard_spec is None or not guard_spec.commands:
            admitted_inputs.extend(repo_inputs)
            continue
        context = {"vars": repo.local_vars, "repo_name": repo_name, "operation": operation}
        skip = _run_planning_guard(
            guard_spec=guard_spec,
            guard_name=guard_name,
            context=context,
            env=build_repo_hook_env(repo=repo, operation=operation, context=context),
            scope_kind="repo",
            repo_name=repo_name,
        )
        if skip is None:
            admitted_inputs.extend(repo_inputs)
            continue
        guard_skips.append(skip)
        if sink is not None:
            sink.update(len(repo_inputs))
    return admitted_inputs, tuple(guard_skips)


def _evaluate_package_guards(
    planning_inputs: list["PackagePlanningInput"],
    *,
    operation: str,
    run_noop: bool,
    sink: "ProgressSink | None" = None,
) -> tuple[list["PackagePlanningInput"], tuple[GuardSkip, ...]]:
    admitted_inputs: list["PackagePlanningInput"] = []
    guard_skips: list[GuardSkip] = []
    outcomes_by_identity: dict[tuple[str, str, str | None], GuardSkip | None] = {}
    guard_name = f"guard_{operation}"

    for planning_input in planning_inputs:
        if not _package_has_potential_work(planning_input, operation=operation, run_noop=run_noop):
            admitted_inputs.append(planning_input)
            continue

        identity_key = resolved_package_identity_key(planning_input.selection.identity)
        if identity_key in outcomes_by_identity:
            if outcomes_by_identity[identity_key] is None:
                admitted_inputs.append(planning_input)
            elif sink is not None:
                sink.update(1)
            continue

        package = _selected_package(planning_input)
        guard_spec = (package.hooks or {}).get(guard_name)
        if guard_spec is None or not guard_spec.commands:
            outcomes_by_identity[identity_key] = None
            admitted_inputs.append(planning_input)
            continue

        package_env = build_package_hook_env(
            repo=planning_input.repo,
            package=package,
            selection=planning_input.selection,
            operation=operation,
            inferred_os=planning_input.package_context.inferred_os,
            context=planning_input.package_context.context,
        )
        skip = _run_planning_guard(
            guard_spec=guard_spec,
            guard_name=guard_name,
            context=planning_input.package_context.context,
            env=package_env,
            scope_kind="package",
            repo_name=planning_input.repo.config.name,
            package_id=package.id,
            bound_profile=planning_input.selection.identity.bound_profile,
        )
        outcomes_by_identity[identity_key] = skip
        if skip is None:
            admitted_inputs.append(planning_input)
        else:
            guard_skips.append(skip)
            if sink is not None:
                sink.update(1)

    return admitted_inputs, tuple(guard_skips)


def _evaluate_target_guards(
    planning_inputs: list["PackagePlanningInput"],
    *,
    operation: str,
) -> tuple[list["PackagePlanningInput"], tuple[GuardSkip, ...]]:
    admitted_inputs: list["PackagePlanningInput"] = []
    guard_skips: list[GuardSkip] = []
    outcomes_by_identity: dict[tuple[str, str, str | None, str], GuardSkip | None] = {}
    guard_name = f"guard_{operation}"
    for planning_input in planning_inputs:
        admitted_metadata: list[TargetMetadata] = []
        for metadata in planning_input.target_metadata:
            identity_key = (
                planning_input.repo.config.name,
                metadata.package_id,
                planning_input.selection.identity.bound_profile,
                metadata.target_name,
            )
            if identity_key in outcomes_by_identity:
                if outcomes_by_identity[identity_key] is None:
                    admitted_metadata.append(metadata)
                continue
            guard_spec = (metadata.target.hooks or {}).get(guard_name)
            if guard_spec is None or not guard_spec.commands:
                outcomes_by_identity[identity_key] = None
                admitted_metadata.append(metadata)
                continue
            skip = _run_planning_guard(
                guard_spec=guard_spec,
                guard_name=guard_name,
                context=planning_input.package_context.context,
                env=metadata.command_env,
                scope_kind="target",
                repo_name=planning_input.repo.config.name,
                package_id=metadata.package_id,
                bound_profile=planning_input.selection.identity.bound_profile,
                target_name=metadata.target_name,
            )
            outcomes_by_identity[identity_key] = skip
            if skip is None:
                admitted_metadata.append(metadata)
            else:
                guard_skips.append(skip)
        admitted_inputs.append(replace(planning_input, target_metadata=admitted_metadata))
    return admitted_inputs, tuple(guard_skips)


def evaluate_hierarchical_guards(
    planning_inputs: list["PackagePlanningInput"],
    *,
    operation: str,
    run_noop: bool,
    sink: "ProgressSink | None" = None,
) -> tuple[list["PackagePlanningInput"], tuple[GuardSkip, ...]]:
    from dotman.elevation import elevation_broker_session

    with elevation_broker_session():
        repo_inputs, repo_skips = _evaluate_repo_guards(
            planning_inputs,
            operation=operation,
            run_noop=run_noop,
            sink=sink,
        )
        package_inputs, package_skips = _evaluate_package_guards(
            repo_inputs,
            operation=operation,
            run_noop=run_noop,
            sink=sink,
        )
        target_inputs, target_skips = _evaluate_target_guards(package_inputs, operation=operation)
    return target_inputs, (*repo_skips, *package_skips, *target_skips)
