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
    executable_package_ids_for_targets,
    filter_hook_plans_for_targets,
    HookPlan,
    OperationPlan,
    PackagePlan,
    PackageSpec,
    repo_qualified_target_text,
    ResolvedPackageIdentity,
    ResolvedPackageSelection,
    resolved_package_selection_key,
    SelectorQuery,
    TrackedPackageEntry,
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


def _merge_selection(
    selections: list[ResolvedPackageSelection],
    selection_indexes: dict[tuple[str, str, str | None], int],
    selection: ResolvedPackageSelection,
) -> None:
    key = resolved_package_selection_key(selection)
    existing_index = selection_indexes.get(key)
    if existing_index is None:
        selection_indexes[key] = len(selections)
        selections.append(selection)
        return
    existing = selections[existing_index]
    if selection.explicit and not existing.explicit:
        selections[existing_index] = selection


def _resolved_package_selections_from_roots(
    engine: Any,
    repo: Repository,
    *,
    root_package_ids: list[str],
    requested_profile: str,
    source_kind: str,
    source_selector: str | None,
) -> list[ResolvedPackageSelection]:
    selections: list[ResolvedPackageSelection] = []
    selection_indexes: dict[tuple[str, str, str | None], int] = {}
    for root_package_id in root_package_ids:
        root_selection = engine._resolved_package_selection(
            repo=repo,
            package_id=root_package_id,
            requested_profile=requested_profile,
            explicit=True,
            source_kind=source_kind,
            source_selector=source_selector,
        )
        _merge_selection(selections, selection_indexes, root_selection)
        related_package_ids = repo.expand_target_ref_package_ids(
            engine._resolve_package_ids(repo, root_package_id, "package")
        )
        for related_package_id in related_package_ids:
            if related_package_id == root_package_id:
                continue
            _merge_selection(
                selections,
                selection_indexes,
                engine._resolved_package_selection(
                    repo=repo,
                    package_id=related_package_id,
                    requested_profile=requested_profile,
                    explicit=False,
                    source_kind="dependency",
                    owner_identity=root_selection.identity,
                ),
            )
    return selections


def resolve_selector_query(engine: Any, query: SelectorQuery, *, operation: str) -> list[ResolvedPackageSelection]:
    del operation
    repo, resolved_selector, selector_kind = engine.resolve_selector(query.selector, query.repo)
    resolved_profile = query.profile
    if not resolved_profile:
        raise ValueError("profile is required in non-interactive mode")
    root_package_ids = engine._selected_package_ids(repo, resolved_selector, selector_kind)
    return _resolved_package_selections_from_roots(
        engine,
        repo,
        root_package_ids=root_package_ids,
        requested_profile=resolved_profile,
        source_kind="selector_query",
        source_selector=resolved_selector,
    )


def resolve_tracked_package_entry(engine: Any, entry: TrackedPackageEntry) -> list[ResolvedPackageSelection]:
    repo = engine.get_repo(entry.repo)
    if entry.package_id not in repo.packages:
        raise ValueError(f"unknown package '{entry.package_id}' in repo '{repo.config.name}'")
    return _resolved_package_selections_from_roots(
        engine,
        repo,
        root_package_ids=[entry.package_id],
        requested_profile=entry.profile,
        source_kind="tracked_entry",
        source_selector=entry.package_id,
    )


def _filter_package_hook_plans(
    hook_plans: dict[str, list[HookPlan]],
    *,
    package_id: str,
    target_plans: list[Any],
) -> dict[str, list[HookPlan]]:
    executable_package_ids = executable_package_ids_for_targets(target_plans)
    executable_target_ids = {
        (target.package_id, target.target_name)
        for target in target_plans
        if target.action != "noop"
    }
    hooks: dict[str, list[HookPlan]] = {}
    for hook_name, items in hook_plans.items():
        retained = []
        for hook in items:
            if hook.package_id != package_id:
                continue
            if hook.scope_kind == "package":
                if package_id in executable_package_ids or hook.run_noop:
                    retained.append(hook)
                continue
            if hook.target_name is None:
                continue
            if (package_id, hook.target_name) in executable_target_ids or hook.run_noop:
                retained.append(hook)
        if retained:
            hooks[hook_name] = retained
    return hooks


def build_package_plan(
    engine: Any,
    repo: Repository,
    selection: ResolvedPackageSelection,
    *,
    operation: str,
) -> PackagePlan:
    root_identity = selection.owner_identity or selection.identity
    related_package_ids = repo.expand_target_ref_package_ids(
        engine._resolve_package_ids(repo, root_identity.package_id, "package")
    )
    resolved_packages = [repo.resolve_package(package_id) for package_id in related_package_ids]
    profile_vars, lineage = repo.compose_profile(selection.requested_profile)
    package_vars: dict[str, Any] = {}
    for package in resolved_packages:
        package_vars = deep_merge(package_vars, package.vars or {})
    variables = deep_merge(deep_merge(package_vars, profile_vars), repo.local_vars)
    inferred_os = infer_profile_os(selection.requested_profile, lineage, variables)
    context = build_template_context(variables, profile=selection.requested_profile, inferred_os=inferred_os)
    target_plans = engine._plan_targets(
        repo=repo,
        packages=resolved_packages,
        context=context,
        selection=selection,
        operation=operation,
        inferred_os=inferred_os,
        declaration_package_ids={root_identity.package_id},
    )
    hook_plans = engine._plan_hooks(
        repo,
        resolved_packages,
        context,
        selection=selection,
        operation=operation,
        inferred_os=inferred_os,
        variables=variables,
        target_plans=target_plans,
    )
    package_targets = [target for target in target_plans if target.package_id == selection.identity.package_id]
    hooks = _filter_package_hook_plans(
        hook_plans,
        package_id=selection.identity.package_id,
        target_plans=package_targets,
    )
    return PackagePlan(
        operation=operation,
        selection=selection,
        variables=variables,
        hooks=hooks,
        target_plans=package_targets,
        hook_plans=hook_plans,
        repo_root=repo.root,
        state_path=repo.config.state_path,
        inferred_os=inferred_os,
    )


def _merge_package_plans(plans: list[PackagePlan]) -> list[PackagePlan]:
    merged: list[PackagePlan] = []
    plan_indexes: dict[tuple[str, str, str | None], int] = {}
    for plan in plans:
        key = resolved_package_selection_key(plan.selection)
        existing_index = plan_indexes.get(key)
        if existing_index is None:
            plan_indexes[key] = len(merged)
            merged.append(plan)
            continue
        existing = merged[existing_index]
        if plan.selection.explicit and not existing.selection.explicit:
            merged[existing_index] = plan
    return merged


def build_tracked_plans(
    engine: Any,
    *,
    operation: str,
    entries_by_repo: dict[str, list[TrackedPackageEntry]] | None = None,
) -> OperationPlan:
    plans, candidates_by_live_path = engine._collect_tracked_candidates(
        operation=operation,
        entries_by_repo=entries_by_repo,
    )
    winner_indexes = engine._resolve_tracked_target_winners(candidates_by_live_path)
    filtered_plans: list[PackagePlan] = []
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
    entries_by_repo: dict[str, list[TrackedPackageEntry]] | None = None,
) -> tuple[list[PackagePlan], dict[Path, list[TrackedTargetCandidate]]]:
    plans: list[PackagePlan] = []
    candidates_by_live_path: dict[Path, list[TrackedTargetCandidate]] = defaultdict(list)
    current_entries = entries_by_repo or engine._tracked_entries_by_repo_from_bindings(
        engine._effective_tracked_package_entries_by_repo()
    )

    for repo_config in engine.config.ordered_repos:
        repo = engine.get_repo(repo_config.name)
        repo_plans: list[PackagePlan] = []
        for entry in current_entries.get(repo_config.name, []):
            for selection in resolve_tracked_package_entry(engine, entry):
                repo_plans.append(build_package_plan(engine, repo, selection, operation=operation))
        repo_plans = _merge_package_plans(repo_plans)
        for plan in repo_plans:
            plan_index = len(plans)
            plans.append(plan)
            for target_index, target in enumerate(plan.target_plans):
                candidates_by_live_path[target.live_path].append(
                    TrackedTargetCandidate(
                        plan_index=plan_index,
                        target_index=target_index,
                        live_path=target.live_path,
                        precedence=1 if plan.selection.explicit else 0,
                        precedence_name="explicit" if plan.selection.explicit else "implicit",
                        selection=plan.selection,
                        selection_label=plan.selection.selection_label,
                        package_id=target.package_id,
                        target_name=target.target_name,
                        target_label=repo_qualified_target_text(
                            repo_name=plan.repo_name,
                            package_id=target.package_id,
                            target_name=target.target_name,
                            bound_profile=plan.bound_profile,
                        ),
                        signature=engine._tracked_target_signature(target),
                    )
                )
    return plans, candidates_by_live_path


def preview_package_selection_implicit_overrides(
    engine: Any,
    selection: ResolvedPackageSelection,
) -> list[TrackedTargetOverride]:
    entry = TrackedPackageEntry(
        repo=selection.identity.repo,
        package_id=selection.identity.package_id,
        profile=selection.requested_profile,
    )
    raw_bindings_by_repo = engine._raw_tracked_package_entries_by_repo()
    repo = engine.get_repo(entry.repo)
    raw_bindings_by_repo[repo.config.name] = engine._normalize_tracked_package_entry_set(
        engine._effective_tracked_package_entries_for_repo(
            repo,
            raw_bindings_by_repo.get(repo.config.name, []),
        ),
        [Binding(repo=entry.repo, selector=entry.package_id, profile=entry.profile)],
    )
    _plans, candidates_by_live_path = engine._collect_tracked_candidates(
        operation="push",
        bindings_by_repo=engine._effective_tracked_package_entries_by_repo(raw_bindings_by_repo),
    )

    overrides_by_package: dict[
        tuple[str, str, str | None, str],
        dict[tuple[str, str, str | None, str], TrackedTargetCandidate],
    ] = {}
    winners_by_package: dict[tuple[str, str, str | None, str], TrackedTargetCandidate] = {}
    for candidates in candidates_by_live_path.values():
        highest_precedence = max(candidate.precedence for candidate in candidates)
        winning_candidates = [candidate for candidate in candidates if candidate.precedence == highest_precedence]
        winner = next(
            (
                candidate
                for candidate in winning_candidates
                if candidate.selection.identity == selection.identity and candidate.selection.explicit
            ),
            None,
        )
        if winner is None:
            continue
        overridden = [
            candidate
            for candidate in candidates
            if candidate.selection.identity != selection.identity and candidate.precedence_name == "implicit"
        ]
        if not overridden:
            continue
        winner_key = (
            winner.selection.identity.repo,
            winner.selection.identity.package_id,
            winner.selection.identity.bound_profile,
            winner.package_id,
        )
        winners_by_package[winner_key] = winner
        package_overrides = overrides_by_package.setdefault(winner_key, {})
        for candidate in overridden:
            contender_key = (
                candidate.selection.identity.repo,
                candidate.selection.identity.package_id,
                candidate.selection.identity.bound_profile,
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
                            item.selection.identity.repo,
                            item.selection.identity.package_id,
                            "" if item.selection.identity.bound_profile is None else item.selection.identity.bound_profile,
                            item.package_id,
                        ),
                    )
                ),
            )
            for winner_key, contenders in overrides_by_package.items()
        ],
        key=lambda item: (
            item.winner.package_id,
            item.winner.selection.identity.repo,
            item.winner.selection.identity.package_id,
            "" if item.winner.selection.identity.bound_profile is None else item.winner.selection.identity.bound_profile,
        ),
    )


def plan_hooks(
    repo: Repository,
    packages: list[PackageSpec],
    context: dict[str, Any],
    *,
    selection: ResolvedPackageSelection,
    operation: str | None = None,
    inferred_os: str,
    variables: dict[str, Any],
    target_plans: list[Any],
) -> dict[str, list[HookPlan]]:
    del variables
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
            selection=selection,
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
    package_plans: list[PackagePlan],
    *,
    allow_standalone_noop_hooks: bool = False,
    excluded_repo_names: set[str] | None = None,
) -> dict[str, list[HookPlan]]:
    if not hooks:
        return {}
    repo_name = next((hook.repo_name for hook_plans in hooks.values() for hook in hook_plans if hook.repo_name), None)
    repo_has_lower_scope_work = any(
        any(target.action != "noop" for target in plan.target_plans) or any(plan.hooks.values())
        for plan in package_plans
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
    package_plans: list[PackagePlan],
) -> tuple[str, ...] | None:
    if any(any(target.action != "noop" for target in plan.target_plans) or any(plan.hooks.values()) for plan in package_plans):
        return None
    hook_names: list[str] = []
    for hook_name, hook_plans in hooks.items():
        if not hook_plans:
            continue
        hook_names.append(hook_name)
    return tuple(hook_names) if hook_names else None


def build_operation_plan(
    package_plans: list[PackagePlan],
    *,
    repo_by_name: dict[str, Repository],
    operation: str,
    allow_standalone_noop_hooks: bool = False,
    excluded_repo_names: set[str] | None = None,
) -> OperationPlan:
    repo_order = tuple(
        repo_name
        for repo_name in repo_by_name
        if any(plan.repo_name == repo_name for plan in package_plans)
    )
    repo_hook_plans = {
        repo_name: plan_repo_hooks(repo_by_name[repo_name], operation=operation)
        for repo_name in repo_order
    }
    repo_hooks = {
        repo_name: finalize_repo_hook_plans(
            repo_hook_plans.get(repo_name, {}),
            [plan for plan in package_plans if plan.repo_name == repo_name],
            allow_standalone_noop_hooks=allow_standalone_noop_hooks,
            excluded_repo_names=excluded_repo_names,
        )
        for repo_name in repo_order
    }
    repo_hooks = {repo_name: hooks for repo_name, hooks in repo_hooks.items() if hooks}
    return OperationPlan(
        operation=operation,
        package_plans=tuple(package_plans),
        repo_hooks=repo_hooks,
        repo_hook_plans=repo_hook_plans,
        repo_order=repo_order,
    )
