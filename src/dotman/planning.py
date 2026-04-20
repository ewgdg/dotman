from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any

from dotman.collisions import (
    TrackedTargetCandidate,
    TrackedTargetConflictError,
    TrackedTargetOverride,
    paths_conflict,
    resolve_tracked_target_winners,
    tracked_target_signature,
    validate_reserved_path_conflicts,
    validate_target_collisions,
)
from dotman.manifest import deep_merge, infer_profile_os
from dotman.models import (
    Binding,
    BindingPlan,
    binding_plans_for_operation_plan,
    filter_hook_plans_for_targets,
    HookPlan,
    OperationPlan,
    PackageSpec,
    repo_qualified_target_text,
)
from dotman.projection import (
    build_package_hook_env,
    build_repo_hook_env,
    build_file_review_bytes,
    build_target_command_env,
    plan_directory_action,
    plan_file_action,
    plan_targets,
    project_repo_file,
    pull_view_bytes,
    run_command_projection,
)
from dotman.repository import Repository, VALID_HOOK_NAMES
from dotman.templates import build_template_context, render_template_string

HOOK_NAMES_BY_OPERATION = {
    "push": ("guard_push", "pre_push", "post_push"),
    "pull": ("guard_pull", "pre_pull", "post_pull"),
}



def build_plan(
    engine: Any,
    repo: Repository,
    binding: Binding,
    selector_kind: str,
    *,
    operation: str,
) -> BindingPlan:
    declaration_package_ids = engine._resolve_package_ids(repo, binding.selector, selector_kind)
    package_ids = repo.expand_target_ref_package_ids(declaration_package_ids)
    resolved_packages = [repo.resolve_package(package_id) for package_id in package_ids]
    profile_vars, lineage = repo.compose_profile(binding.profile)
    package_vars: dict[str, Any] = {}
    for package in resolved_packages:
        package_vars = deep_merge(package_vars, package.vars or {})
    variables = deep_merge(deep_merge(package_vars, profile_vars), repo.local_vars)
    inferred_os = infer_profile_os(binding.profile, lineage, variables)
    context = build_template_context(variables, profile=binding.profile, inferred_os=inferred_os)
    target_plans = engine._plan_targets(
        repo=repo,
        packages=resolved_packages,
        context=context,
        binding=binding,
        operation=operation,
        inferred_os=inferred_os,
        declaration_package_ids=set(declaration_package_ids),
    )
    hook_plans = engine._plan_hooks(
        repo,
        resolved_packages,
        context,
        binding=binding,
        operation=operation,
        inferred_os=inferred_os,
        variables=variables,
        target_plans=target_plans,
    )
    hooks = filter_hook_plans_for_targets(hook_plans, target_plans)
    package_bound_profiles = {
        package_id: (binding.profile if repo.package_binding_mode(package_id) == "multi_instance" else None)
        for package_id in package_ids
    }
    return BindingPlan(
        operation=operation,
        binding=binding,
        selector_kind=selector_kind,
        package_ids=package_ids,
        variables=variables,
        hooks=hooks,
        target_plans=target_plans,
        hook_plans=hook_plans,
        package_bound_profiles=package_bound_profiles,
        repo_root=repo.root,
        state_path=repo.config.state_path,
        inferred_os=inferred_os,
    )



def build_tracked_plans(
    engine: Any,
    *,
    operation: str,
    bindings_by_repo: dict[str, list[Binding]] | None = None,
) -> OperationPlan:
    plans, candidates_by_live_path = engine._collect_tracked_candidates(
        operation=operation,
        bindings_by_repo=bindings_by_repo,
    )
    winner_indexes = engine._resolve_tracked_target_winners(candidates_by_live_path)
    filtered_plans: list[BindingPlan] = []
    for plan_index, plan in enumerate(plans):
        filtered_targets = [
            target
            for target_index, target in enumerate(plan.target_plans)
            if (plan_index, target_index) in winner_indexes
        ]
        filtered_plans.append(
            replace(
                plan,
                hooks=filter_hook_plans_for_targets(plan.hooks, filtered_targets),
                target_plans=filtered_targets,
            )
        )
    repo_by_name = {repo_config.name: engine.get_repo(repo_config.name) for repo_config in engine.config.ordered_repos}
    return build_operation_plan(
        filtered_plans,
        repo_by_name=repo_by_name,
        operation=operation,
    )



def collect_tracked_candidates(
    engine: Any,
    *,
    operation: str,
    bindings_by_repo: dict[str, list[Binding]] | None = None,
) -> tuple[list[BindingPlan], dict[Path, list[TrackedTargetCandidate]]]:
    plans: list[BindingPlan] = []
    candidates_by_live_path: dict[Path, list[TrackedTargetCandidate]] = defaultdict(list)
    current_bindings = bindings_by_repo or engine._effective_tracked_package_entries_by_repo()

    for repo_config in engine.config.ordered_repos:
        repo = engine.get_repo(repo_config.name)
        for binding in current_bindings.get(repo_config.name, []):
            selector_kind = "package"
            selected_packages = set(engine._selected_package_ids(repo, binding.selector, selector_kind))
            plan = engine._build_plan(repo, binding, selector_kind, operation=operation)
            plan_index = len(plans)
            plans.append(plan)
            for target_index, target in enumerate(plan.target_plans):
                contributor_package_ids = set(target.contributor_package_ids or (target.package_id,))
                precedence_name = "explicit" if contributor_package_ids & selected_packages else "implicit"
                candidates_by_live_path[target.live_path].append(
                    TrackedTargetCandidate(
                        plan_index=plan_index,
                        target_index=target_index,
                        live_path=target.live_path,
                        precedence=1 if precedence_name == "explicit" else 0,
                        precedence_name=precedence_name,
                        binding=binding,
                        binding_label=f"{binding.repo}:{binding.selector}@{binding.profile}",
                        package_id=target.package_id,
                        target_name=target.target_name,
                        target_label=repo_qualified_target_text(
                            repo_name=binding.repo,
                            package_id=target.package_id,
                            target_name=target.target_name,
                        ),
                        signature=engine._tracked_target_signature(target),
                    )
                )
    return plans, candidates_by_live_path



def preview_binding_implicit_overrides(engine: Any, binding: Binding) -> list[TrackedTargetOverride]:
    repo = engine.get_repo(binding.repo)
    raw_tracked_package_entries_by_repo = engine._raw_tracked_package_entries_by_repo()
    raw_tracked_package_entries_by_repo[repo.config.name] = engine._normalize_tracked_package_entry_set(
        engine._effective_tracked_package_entries_for_repo(repo, raw_tracked_package_entries_by_repo.get(repo.config.name, [])),
        engine._expand_tracked_package_entry(repo, binding),
    )
    _plans, candidates_by_live_path = engine._collect_tracked_candidates(
        operation="push",
        bindings_by_repo=engine._effective_tracked_package_entries_by_repo(raw_tracked_package_entries_by_repo),
    )

    overrides_by_package: dict[
        tuple[str, str, str, str],
        dict[tuple[str, str, str, str], TrackedTargetCandidate],
    ] = {}
    winners_by_package: dict[tuple[str, str, str, str], TrackedTargetCandidate] = {}
    for _live_path, candidates in candidates_by_live_path.items():
        highest_precedence = max(candidate.precedence for candidate in candidates)
        winning_candidates = [candidate for candidate in candidates if candidate.precedence == highest_precedence]
        winner = next(
            (
                candidate
                for candidate in winning_candidates
                if candidate.binding == binding and candidate.precedence_name == "explicit"
            ),
            None,
        )
        if winner is None:
            continue
        overridden = [
            candidate
            for candidate in candidates
            if candidate.binding != binding and candidate.precedence_name == "implicit"
        ]
        if not overridden:
            continue

        # Collapse override previews to package ownership because target-level collisions
        # are already rejected elsewhere; the user only needs to see which packages lose ownership.
        winner_key = (
            winner.binding.repo,
            winner.binding.selector,
            winner.binding.profile,
            winner.package_id,
        )
        winners_by_package[winner_key] = winner
        package_overrides = overrides_by_package.setdefault(winner_key, {})
        for candidate in overridden:
            contender_key = (
                candidate.binding.repo,
                candidate.binding.selector,
                candidate.binding.profile,
                candidate.package_id,
            )
            package_overrides.setdefault(contender_key, candidate)
    return sorted(
        [
            TrackedTargetOverride(
                winner=winners_by_package[winner_key],
                overridden=tuple(
                    sorted(
                        contenders.values(),
                        key=lambda item: (
                            item.binding.repo,
                            item.binding.selector,
                            item.binding.profile,
                            item.package_id,
                        ),
                    )
                ),
            )
            for winner_key, contenders in overrides_by_package.items()
        ],
        key=lambda item: (
            item.winner.package_id,
            item.winner.binding.repo,
            item.winner.binding.selector,
            item.winner.binding.profile,
        ),
    )



def plan_hooks(
    repo: Repository,
    packages: list[PackageSpec],
    context: dict[str, Any],
    *,
    binding: Binding,
    operation: str | None = None,
    inferred_os: str,
    variables: dict[str, Any],
    target_plans: list[Any],
) -> dict[str, list[HookPlan]]:
    hook_names = HOOK_NAMES_BY_OPERATION.get(operation, VALID_HOOK_NAMES)
    hooks: dict[str, list[HookPlan]] = defaultdict(list)
    targets_by_owner = {
        (target.package_id, target.target_name): target
        for target in target_plans
    }
    for package in packages:
        package_env = build_package_hook_env(
            repo=repo,
            package=package,
            binding=binding,
            operation=operation or "push",
            inferred_os=inferred_os,
            context=context,
        )
        package_hooks = package.hooks or {}
        for hook_name in hook_names:
            hook_spec = package_hooks.get(hook_name)
            if hook_spec is None:
                continue
            for command in hook_spec.commands:
                hooks[hook_name].append(
                    HookPlan(
                        hook_name=hook_name,
                        command=render_template_string(command, context, base_dir=hook_spec.declared_in, source_path=hook_spec.declared_in).strip(),
                        cwd=hook_spec.declared_in,
                        repo_name=repo.config.name,
                        package_id=package.id,
                        scope_kind="package",
                        env=dict(package_env),
                        run_noop=hook_spec.run_noop,
                    )
                )
        for target_name, target_spec in (package.targets or {}).items():
            target_plan = targets_by_owner.get((package.id, target_name))
            if target_plan is None:
                continue
            target_hooks = target_spec.hooks or {}
            for hook_name in hook_names:
                hook_spec = target_hooks.get(hook_name)
                if hook_spec is None:
                    continue
                target_env = dict(target_plan.command_env or {})
                for command in hook_spec.commands:
                    hooks[hook_name].append(
                        HookPlan(
                            hook_name=hook_name,
                            command=render_template_string(command, context, base_dir=hook_spec.declared_in, source_path=hook_spec.declared_in).strip(),
                            cwd=hook_spec.declared_in,
                            repo_name=repo.config.name,
                            package_id=package.id,
                            target_name=target_name,
                            scope_kind="target",
                            env=target_env,
                            run_noop=hook_spec.run_noop,
                        )
                    )
    return dict(hooks)


def plan_repo_hooks(
    repo: Repository,
    *,
    operation: str,
) -> dict[str, list[HookPlan]]:
    hook_names = HOOK_NAMES_BY_OPERATION.get(operation, VALID_HOOK_NAMES)
    context = {"vars": repo.local_vars, "repo_name": repo.config.name, "operation": operation}
    env = build_repo_hook_env(repo=repo, operation=operation, context=context)
    hooks: dict[str, list[HookPlan]] = defaultdict(list)
    for hook_name in hook_names:
        hook_spec = (repo.hooks or {}).get(hook_name)
        if hook_spec is None:
            continue
        for command in hook_spec.commands:
            hooks[hook_name].append(
                HookPlan(
                    hook_name=hook_name,
                    command=render_template_string(command, context, base_dir=hook_spec.declared_in, source_path=hook_spec.declared_in).strip(),
                    cwd=hook_spec.declared_in,
                    repo_name=repo.config.name,
                    scope_kind="repo",
                    env=dict(env),
                    run_noop=hook_spec.run_noop,
                )
            )
    return dict(hooks)


def finalize_repo_hook_plans(
    hooks: dict[str, list[HookPlan]],
    binding_plans: list[BindingPlan],
    *,
    allow_standalone_noop_hooks: bool = False,
    excluded_repo_names: set[str] | None = None,
) -> dict[str, list[HookPlan]]:
    if not hooks:
        return {}
    repo_name = next((hook.repo_name for hook_plans in hooks.values() for hook in hook_plans if hook.repo_name), None)
    repo_has_lower_scope_work = any(
        any(target.action != "noop" for target in plan.target_plans) or any(plan.hooks.values())
        for plan in binding_plans
    )
    if repo_has_lower_scope_work:
        return hooks
    if repo_name is not None and repo_name in (excluded_repo_names or set()):
        return {}
    finalized: dict[str, list[HookPlan]] = {}
    for hook_name, hook_plans in hooks.items():
        retained = [hook for hook in hook_plans if allow_standalone_noop_hooks or hook.run_noop]
        if retained:
            finalized[hook_name] = retained
    return finalized


def standalone_repo_hook_summary(
    hooks: dict[str, list[HookPlan]],
    binding_plans: list[BindingPlan],
) -> tuple[str, ...] | None:
    if any(any(target.action != "noop" for target in plan.target_plans) or any(plan.hooks.values()) for plan in binding_plans):
        return None
    hook_names: list[str] = []
    for hook_name, hook_plans in hooks.items():
        if not hook_plans:
            continue
        hook_names.append(hook_name)
    return tuple(hook_names) if hook_names else None


def build_operation_plan(
    plans: list[BindingPlan],
    *,
    repo_by_name: dict[str, Repository],
    operation: str,
    allow_standalone_noop_hooks: bool = False,
    excluded_repo_names: set[str] | None = None,
) -> OperationPlan:
    repo_order = tuple(
        repo_name
        for repo_name in repo_by_name
        if any(plan.binding.repo == repo_name for plan in plans)
    )
    repo_hook_plans = {
        repo_name: plan_repo_hooks(repo_by_name[repo_name], operation=operation)
        for repo_name in repo_order
    }
    repo_hooks = {
        repo_name: finalize_repo_hook_plans(
            repo_hook_plans.get(repo_name, {}),
            [plan for plan in plans if plan.binding.repo == repo_name],
            allow_standalone_noop_hooks=allow_standalone_noop_hooks,
            excluded_repo_names=excluded_repo_names,
        )
        for repo_name in repo_order
    }
    repo_hooks = {repo_name: hooks for repo_name, hooks in repo_hooks.items() if hooks}
    return OperationPlan(
        operation=operation,
        binding_plans=tuple(plans),
        repo_hooks=repo_hooks,
        repo_hook_plans=repo_hook_plans,
        repo_order=repo_order,
    )
