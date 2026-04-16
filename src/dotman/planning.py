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
    filter_hook_plans_for_targets,
    HookPlan,
    PackageSpec,
)
from dotman.projection import (
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
    package_ids = engine._resolve_package_ids(repo, binding.selector, selector_kind)
    resolved_packages = [repo.resolve_package(package_id) for package_id in package_ids]
    profile_vars, lineage = repo.compose_profile(binding.profile)
    package_vars: dict[str, Any] = {}
    for package in resolved_packages:
        package_vars = deep_merge(package_vars, package.vars or {})
    variables = deep_merge(deep_merge(package_vars, profile_vars), repo.local_vars)
    inferred_os = infer_profile_os(binding.profile, lineage, variables)
    context = build_template_context(variables, profile=binding.profile, inferred_os=inferred_os)
    hook_plans = engine._plan_hooks(repo, resolved_packages, context, operation=operation)
    target_plans = engine._plan_targets(
        repo=repo,
        packages=resolved_packages,
        context=context,
        binding=binding,
        operation=operation,
        inferred_os=inferred_os,
    )
    hooks = filter_hook_plans_for_targets(hook_plans, target_plans)
    return BindingPlan(
        operation=operation,
        binding=binding,
        selector_kind=selector_kind,
        package_ids=package_ids,
        variables=variables,
        hooks=hooks,
        target_plans=target_plans,
        hook_plans=hook_plans,
        repo_root=repo.root,
        state_path=repo.config.state_path,
        inferred_os=inferred_os,
    )



def build_tracked_plans(
    engine: Any,
    *,
    operation: str,
    bindings_by_repo: dict[str, list[Binding]] | None = None,
) -> list[BindingPlan]:
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
    return filtered_plans



def collect_tracked_candidates(
    engine: Any,
    *,
    operation: str,
    bindings_by_repo: dict[str, list[Binding]] | None = None,
) -> tuple[list[BindingPlan], dict[Path, list[TrackedTargetCandidate]]]:
    plans: list[BindingPlan] = []
    candidates_by_live_path: dict[Path, list[TrackedTargetCandidate]] = defaultdict(list)
    current_bindings = bindings_by_repo or engine._effective_bindings_by_repo()

    for repo_config in engine.config.ordered_repos:
        repo = engine.get_repo(repo_config.name)
        for binding in current_bindings.get(repo_config.name, []):
            selector_kind = "package"
            selected_packages = set(engine._selected_package_ids(repo, binding.selector, selector_kind))
            plan = engine._build_plan(repo, binding, selector_kind, operation=operation)
            plan_index = len(plans)
            plans.append(plan)
            for target_index, target in enumerate(plan.target_plans):
                precedence_name = "explicit" if target.package_id in selected_packages else "implicit"
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
                        target_label=f"{target.package_id}:{target.target_name}",
                        signature=engine._tracked_target_signature(target),
                    )
                )
    return plans, candidates_by_live_path



def preview_binding_implicit_overrides(engine: Any, binding: Binding) -> list[TrackedTargetOverride]:
    repo = engine.get_repo(binding.repo)
    raw_bindings_by_repo = engine._raw_bindings_by_repo()
    raw_bindings_by_repo[repo.config.name] = engine._normalize_recorded_binding_set(
        engine._effective_bindings_for_repo(repo, raw_bindings_by_repo.get(repo.config.name, [])),
        engine._expand_binding_for_tracking(repo, binding),
    )
    _plans, candidates_by_live_path = engine._collect_tracked_candidates(
        operation="push",
        bindings_by_repo=engine._effective_bindings_by_repo(raw_bindings_by_repo),
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
    operation: str | None = None,
) -> dict[str, list[HookPlan]]:
    hook_names = HOOK_NAMES_BY_OPERATION.get(operation, VALID_HOOK_NAMES)
    hooks: dict[str, list[HookPlan]] = defaultdict(list)
    for package in packages:
        package_hooks = package.hooks or {}
        for hook_name in hook_names:
            hook_spec = package_hooks.get(hook_name)
            if hook_spec is None:
                continue
            for command in hook_spec.commands:
                hooks[hook_name].append(
                    HookPlan(
                        package_id=package.id,
                        hook_name=hook_name,
                        command=render_template_string(command, context, base_dir=hook_spec.declared_in, source_path=hook_spec.declared_in).strip(),
                        cwd=hook_spec.declared_in,
                    )
                )
    return dict(hooks)
