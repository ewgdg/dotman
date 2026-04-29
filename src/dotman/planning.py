from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
import sys
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
from dotman.config import expand_path
from dotman.manifest import deep_merge, infer_profile_os
from dotman.models import (
    FullSpecSelector,
    executable_package_ids_for_targets,
    filter_hook_plans_for_targets,
    HookPlan,
    OperationPlan,
    PackagePlan,
    PackageSpec,
    package_ref_text,
    repo_qualified_target_text,
    ResolvedPackageIdentity,
    ResolvedPackageSelection,
    resolved_package_identity_key,
    resolved_package_selection_key,
    TrackedPackageEntry,
    TrackedTargetSummary,
)
from dotman.projection import (
    build_target_metadata,
    build_package_hook_env,
    build_repo_hook_env,
    build_file_review_bytes,
    build_target_command_env,
    infer_target_kind,
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


class TrackedPackageProfileConflictError(ValueError):
    def __init__(
        self,
        *,
        package_identity: ResolvedPackageIdentity,
        conflict_kind: str,
        contenders: tuple[str, ...],
    ) -> None:
        self.package_identity = package_identity
        self.conflict_kind = conflict_kind
        self.contenders = contenders
        package_label = _package_identity_label(package_identity)
        if conflict_kind == "ambiguous_implicit":
            header = f"ambiguous implicit profile contexts for {package_label}:"
        elif conflict_kind == "conflicting_explicit":
            header = f"conflicting explicit profile contexts for {package_label}:"
        else:
            header = f"conflicting profile contexts for {package_label}:"
        super().__init__("\n".join([header, *(f"  {contender}" for contender in contenders)]))


def _package_identity_label(identity: ResolvedPackageIdentity) -> str:
    return f"{identity.repo}:{package_ref_text(package_id=identity.package_id, bound_profile=identity.bound_profile)}"


def _merge_selection(
    selections: list[ResolvedPackageSelection],
    selection_indexes: dict[tuple[str, str, str | None, str], int],
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
    selection_indexes: dict[tuple[str, str, str | None, str], int] = {}
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
        related_package_ids = engine._resolve_package_ids(repo, root_package_id, "package")
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
                    owner_selection_label=root_selection.selection_label,
                ),
            )
    return selections


def resolve_tracked_package_selections(
    engine: Any,
    *,
    entries_by_repo: dict[str, list[TrackedPackageEntry]] | None = None,
) -> list[ResolvedPackageSelection]:
    current_entries = (
        entries_by_repo
        if entries_by_repo is not None
        else engine._tracked_entries_by_repo_from_bindings(engine._effective_tracked_package_entries_by_repo())
    )
    selections: list[ResolvedPackageSelection] = []
    for repo_config in engine.config.ordered_repos:
        for entry in (current_entries or {}).get(repo_config.name, []):
            selections.extend(resolve_tracked_package_entry(engine, entry))
    return _resolve_package_profile_claims(selections)


def _resolve_package_profile_claims(selections: list[ResolvedPackageSelection]) -> list[ResolvedPackageSelection]:
    selections_by_identity: dict[tuple[str, str, str | None], list[ResolvedPackageSelection]] = defaultdict(list)
    for selection in selections:
        selections_by_identity[resolved_package_identity_key(selection.identity)].append(selection)

    retained_identity_keys: set[tuple[str, str, str | None, str]] = set()
    for identity_selections in selections_by_identity.values():
        requested_profiles = {selection.requested_profile for selection in identity_selections}
        if len(requested_profiles) == 1:
            retained_identity_keys.update(resolved_package_selection_key(selection) for selection in identity_selections)
            continue

        explicit_profiles = {selection.requested_profile for selection in identity_selections if selection.explicit}
        if len(explicit_profiles) == 1:
            explicit_profile = next(iter(explicit_profiles))
            retained_identity_keys.update(
                resolved_package_selection_key(selection)
                for selection in identity_selections
                if selection.requested_profile == explicit_profile
            )
            continue

        conflict_kind = "ambiguous_implicit" if not explicit_profiles else "conflicting_explicit"
        raise TrackedPackageProfileConflictError(
            package_identity=identity_selections[0].identity,
            conflict_kind=conflict_kind,
            contenders=tuple(
                _format_profile_conflict_contender(selection)
                for selection in _sort_profile_conflict_selections(identity_selections)
            ),
        )

    resolved: list[ResolvedPackageSelection] = []
    resolved_indexes: dict[tuple[str, str, str | None, str], int] = {}
    for selection in selections:
        if resolved_package_selection_key(selection) in retained_identity_keys:
            _merge_selection(resolved, resolved_indexes, selection)
    return resolved


def _sort_profile_conflict_selections(selections: list[ResolvedPackageSelection]) -> list[ResolvedPackageSelection]:
    return sorted(selections, key=lambda selection: (selection.selection_label, selection.owner_selection_label or ""))


def _format_profile_conflict_contender(selection: ResolvedPackageSelection) -> str:
    if selection.owner_selection_label is None:
        return selection.selection_label
    return f"{selection.selection_label} required by {selection.owner_selection_label}"


@dataclass(frozen=True)
class PackagePlanningContext:
    root_identity: ResolvedPackageIdentity
    related_package_ids: list[str]
    resolved_packages: list[PackageSpec]
    variables: dict[str, Any]
    inferred_os: str
    context: dict[str, Any]


def build_package_planning_context(
    engine: Any,
    repo: Repository,
    selection: ResolvedPackageSelection,
) -> PackagePlanningContext:
    root_identity = selection.owner_identity or selection.identity
    related_package_ids = engine._resolve_package_ids(repo, root_identity.package_id, "package")
    resolved_packages = [repo.resolve_package(package_id) for package_id in related_package_ids]
    profile_vars, lineage = repo.compose_profile(selection.requested_profile)
    package_vars: dict[str, Any] = {}
    for package in resolved_packages:
        package_vars = deep_merge(package_vars, package.vars or {})
    variables = deep_merge(deep_merge(package_vars, profile_vars), repo.local_vars)
    inferred_os = infer_profile_os(selection.requested_profile, lineage, variables)
    context = build_template_context(variables, profile=selection.requested_profile, inferred_os=inferred_os)
    return PackagePlanningContext(
        root_identity=root_identity,
        related_package_ids=related_package_ids,
        resolved_packages=resolved_packages,
        variables=variables,
        inferred_os=inferred_os,
        context=context,
    )


def resolve_full_spec_selector(engine: Any, query: FullSpecSelector, *, operation: str) -> list[ResolvedPackageSelection]:
    del operation
    repo = engine.get_repo(query.repo)
    root_package_ids = engine._selected_package_ids(repo, query.selector, query.selector_kind)
    return _resolved_package_selections_from_roots(
        engine,
        repo,
        root_package_ids=root_package_ids,
        requested_profile=query.profile,
        source_kind="selector_query",
        source_selector=query.selector,
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
    package_context = build_package_planning_context(engine, repo, selection)
    target_plans = engine._plan_targets(
        repo=repo,
        packages=package_context.resolved_packages,
        context=package_context.context,
        selection=selection,
        operation=operation,
        inferred_os=package_context.inferred_os,
        declaration_package_ids={selection.identity.package_id},
    )
    hook_plans = engine._plan_hooks(
        repo,
        package_context.resolved_packages,
        package_context.context,
        selection=selection,
        operation=operation,
        inferred_os=package_context.inferred_os,
        variables=package_context.variables,
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
        variables=package_context.variables,
        hooks=hooks,
        target_plans=package_targets,
        hook_plans=hook_plans,
        repo_root=repo.root,
        state_path=repo.config.state_path,
        inferred_os=package_context.inferred_os,
    )


def _merge_package_plans(plans: list[PackagePlan]) -> list[PackagePlan]:
    merged: list[PackagePlan] = []
    plan_indexes: dict[tuple[str, str, str | None, str], int] = {}
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
    for selection in resolve_tracked_package_selections(engine, entries_by_repo=entries_by_repo):
        repo = engine.get_repo(selection.identity.repo)
        plan = build_package_plan(engine, repo, selection, operation=operation)
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


def collect_tracked_ownership_candidates(
    engine: Any,
    *,
    entries_by_repo: dict[str, list[TrackedPackageEntry]] | None = None,
    include_target_summary: bool = False,
    selections: list[ResolvedPackageSelection] | None = None,
) -> dict[Path, list[TrackedTargetCandidate]]:
    candidates_by_live_path: dict[Path, list[TrackedTargetCandidate]] = defaultdict(list)
    candidate_index = 0

    resolved_selections = (
        selections
        if selections is not None
        else resolve_tracked_package_selections(engine, entries_by_repo=entries_by_repo)
    )
    for selection in resolved_selections:
        repo = engine.get_repo(selection.identity.repo)
        package_context = build_package_planning_context(engine, repo, selection)
        metadata_targets = build_target_metadata(
            engine,
            repo=repo,
            packages=package_context.resolved_packages,
            context=package_context.context,
            selection=selection,
            operation="push",
            inferred_os=package_context.inferred_os,
            declaration_package_ids={selection.identity.package_id},
            inspect_live_symlinks=False,
        )
        for target_index, metadata in enumerate(metadata_targets):
            target_summary = None
            if include_target_summary:
                target_summary = TrackedTargetSummary(
                    target_name=metadata.target_name,
                    repo_path=metadata.repo_path,
                    live_path=metadata.live_path,
                    target_kind=infer_target_kind(repo_path=metadata.repo_path, live_path=metadata.live_path),
                    render_command=metadata.render_command,
                    capture_command=metadata.capture_command,
                    reconcile=metadata.reconcile,
                    pull_view_repo=metadata.pull_view_repo,
                    pull_view_live=metadata.pull_view_live,
                    push_ignore=metadata.push_ignore,
                    pull_ignore=metadata.pull_ignore,
                    chmod=metadata.chmod,
                )
            candidates_by_live_path[metadata.live_path].append(
                TrackedTargetCandidate(
                    plan_index=candidate_index,
                    target_index=target_index,
                    live_path=metadata.live_path,
                    precedence=1 if selection.explicit else 0,
                    precedence_name="explicit" if selection.explicit else "implicit",
                    selection=selection,
                    selection_label=selection.selection_label,
                    package_id=metadata.package_id,
                    target_name=metadata.target_name,
                    target_label=repo_qualified_target_text(
                        repo_name=repo.config.name,
                        package_id=metadata.package_id,
                        target_name=metadata.target_name,
                        bound_profile=selection.bound_profile,
                    ),
                    target_summary=target_summary,
                )
            )
            candidate_index += 1
    return candidates_by_live_path


def validate_tracked_package_ownership(
    engine: Any,
    *,
    entries_by_repo: dict[str, list[TrackedPackageEntry]] | None = None,
) -> None:
    selections = resolve_tracked_package_selections(engine, entries_by_repo=entries_by_repo)
    candidates_by_live_path = collect_tracked_ownership_candidates(
        engine,
        entries_by_repo=entries_by_repo,
        selections=selections,
    )
    engine._resolve_tracked_target_winners(candidates_by_live_path)


def preview_package_selection_implicit_overrides(
    engine: Any,
    selection: ResolvedPackageSelection,
) -> list[TrackedTargetOverride]:
    return preview_package_selections_implicit_overrides(engine, [selection])


def preview_package_selections_implicit_overrides(
    engine: Any,
    selections: list[ResolvedPackageSelection],
) -> list[TrackedTargetOverride]:
    explicit_selections = [selection for selection in selections if selection.explicit]
    if not explicit_selections:
        return []

    raw_bindings_by_repo = engine._raw_tracked_package_entries_by_repo()
    additions_by_repo: dict[str, list[FullSpecSelector]] = defaultdict(list)
    requested_selection_keys = {
        resolved_package_selection_key(selection)
        for selection in explicit_selections
    }
    for selection in explicit_selections:
        additions_by_repo[selection.identity.repo].append(
            FullSpecSelector(
                repo=selection.identity.repo,
                selector=selection.identity.package_id,
                selector_kind="package",
                profile=selection.requested_profile,
            )
        )

    for repo_name, additions in additions_by_repo.items():
        repo = engine.get_repo(repo_name)
        raw_bindings_by_repo[repo.config.name] = engine._normalize_tracked_package_entry_set(
            engine._effective_tracked_package_entries_for_repo(
                repo,
                raw_bindings_by_repo.get(repo.config.name, []),
            ),
            additions,
        )

    candidates_by_live_path = collect_tracked_ownership_candidates(
        engine,
        entries_by_repo=engine._tracked_entries_by_repo_from_bindings(
            engine._effective_tracked_package_entries_by_repo(raw_bindings_by_repo)
        ),
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
                if resolved_package_selection_key(candidate.selection) in requested_selection_keys
                and candidate.selection.explicit
            ),
            None,
        )
        if winner is None:
            continue
        overridden = [
            candidate
            for candidate in candidates
            if candidate.selection.identity != winner.selection.identity and candidate.precedence_name == "implicit"
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
            for command_spec in hook_spec.commands:
                hooks[hook_name].append(
                    HookPlan(
                        hook_name=hook_name,
                        command=render_template_string(command_spec.run, context, base_dir=hook_spec.declared_in, source_path=hook_spec.declared_in).strip(),
                        cwd=hook_spec.declared_in,
                        repo_name=repo.config.name,
                        package_id=package.id,
                        scope_kind="package",
                        io=command_spec.io,
                        elevation=command_spec.elevation,
                        env=dict(package_env),
                        run_noop=hook_spec.run_noop or command_spec.run_noop,
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
                for command_spec in hook_spec.commands:
                    hooks[hook_name].append(
                        HookPlan(
                            hook_name=hook_name,
                            command=render_template_string(command_spec.run, context, base_dir=hook_spec.declared_in, source_path=hook_spec.declared_in).strip(),
                            cwd=hook_spec.declared_in,
                            repo_name=repo.config.name,
                            package_id=package.id,
                            target_name=target_name,
                            scope_kind="target",
                            io=command_spec.io,
                            elevation=command_spec.elevation,
                            env=target_env,
                            run_noop=hook_spec.run_noop or command_spec.run_noop,
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
        for command_spec in hook_spec.commands:
            hooks[hook_name].append(
                HookPlan(
                    hook_name=hook_name,
                    command=render_template_string(command_spec.run, context, base_dir=hook_spec.declared_in, source_path=hook_spec.declared_in).strip(),
                    cwd=hook_spec.declared_in,
                    repo_name=repo.config.name,
                    scope_kind="repo",
                    io=command_spec.io,
                    elevation=command_spec.elevation,
                    env=dict(env),
                    run_noop=hook_spec.run_noop or command_spec.run_noop,
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
    if operation == "push":
        _validate_direct_package_plan_conflicts(package_plans, repo_by_name=repo_by_name)
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


def _validate_direct_package_plan_conflicts(
    package_plans: list[PackagePlan],
    *,
    repo_by_name: dict[str, Repository],
) -> None:
    for repo_name in repo_by_name:
        repo_package_plans = [plan for plan in package_plans if plan.repo_name == repo_name]
        if len(repo_package_plans) < 2:
            continue
        repo = repo_by_name[repo_name]
        rendered_targets = []
        for plan in repo_package_plans:
            for target in plan.target_plans:
                package = repo.resolve_package(target.package_id)
                target_spec = package.targets[target.target_name]
                rendered_targets.append(
                    (
                        package,
                        target_spec,
                        target.repo_path,
                        target.live_path,
                        target.push_ignore,
                        target.pull_ignore,
                        target.live_path_is_symlink,
                        target.live_path_symlink_target,
                    )
                )
        validate_target_collisions(rendered_targets)
        _validate_reserved_path_conflicts_for_package_plans(repo_package_plans, repo=repo, rendered_targets=rendered_targets)


def _validate_reserved_path_conflicts_for_package_plans(
    package_plans: list[PackagePlan],
    *,
    repo: Repository,
    rendered_targets: list[tuple[PackageSpec, Any, Path, Path, tuple[str, ...], tuple[str, ...], bool, str | None]],
) -> None:
    target_claims = [
        (package.id, f"{package.id}:{target.name}", live_path)
        for package, target, _repo_path, live_path, _push_ignore, _pull_ignore, _live_path_is_symlink, _live_path_symlink_target in rendered_targets
    ]
    reserved_claims: list[tuple[str, Path]] = []
    for plan in package_plans:
        package = repo.resolve_package(plan.package_id)
        context = build_template_context(
            plan.variables,
            profile=plan.requested_profile,
            inferred_os=plan.inferred_os or sys.platform,
        )
        for reserved_path in package.reserved_paths or ():
            rendered_path = render_template_string(
                reserved_path,
                context,
                base_dir=package.package_root,
                source_path=package.package_root,
            )
            reserved_claims.append((package.id, expand_path(rendered_path, dereference=False)))

    for package_id, reserved_path in reserved_claims:
        for target_package_id, target_label, target_path in target_claims:
            if package_id == target_package_id:
                continue
            if paths_conflict(reserved_path, target_path):
                raise ValueError(
                    f"reserved path conflict: {package_id} reserves {reserved_path} and {target_label} maps to {target_path}"
                )

    for index, (package_id, reserved_path) in enumerate(reserved_claims):
        for other_package_id, other_reserved_path in reserved_claims[index + 1 :]:
            if package_id == other_package_id:
                continue
            if paths_conflict(reserved_path, other_reserved_path):
                raise ValueError(
                    f"reserved path conflict: {package_id} reserves {reserved_path} and {other_package_id} reserves {other_reserved_path}"
                )
