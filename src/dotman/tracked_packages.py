from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotman.config import expand_path
from dotman.manifest import deep_merge, infer_profile_os, merge_ignore_patterns
from dotman.projection import default_pull_view_live, infer_target_kind
from dotman.models import (
    FullSpecSelector,
    HookCommandSpec,
    SelectorKind,
    TrackedPackageEntrySummary,
    TrackedOwnedTargetDetail,
    TrackedPackageEntryDetail,
    TrackedTargetSummary,
    PackageSpec,
    TargetPlan,
    package_ref_text,
)
from dotman.repository import Repository
from dotman.resolver import build_target_match_fields
from dotman.templates import build_template_context, render_template_string


@dataclass(frozen=True)
class TrackedTargetMatch:
    repo_name: str
    package_id: str
    target_name: str
    repo_path: Path
    target_kind: str
    bound_profile: str | None = None


def resolve_tracked_package(
    engine: Any,
    package_text: str,
) -> tuple[Repository, str, str | None]:
    selector, bound_profile, exact_matches, partial_matches = engine.find_tracked_package_matches(package_text)
    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        candidates = ", ".join(
            f"{repo.config.name}:{package_ref_text(package_id=package_id, bound_profile=match_bound_profile)}"
            for repo, package_id, match_bound_profile in exact_matches
        )
        if len({repo.config.name for repo, _package_id, _match_bound_profile in exact_matches}) > 1:
            raise ValueError(f"tracked package '{selector}' is defined in multiple repos: {candidates}")
        raise ValueError(f"tracked package '{selector}' is ambiguous: {candidates}")

    if len(partial_matches) == 1:
        repo, package_id, match_bound_profile = partial_matches[0]
        raise ValueError(
            f"no exact match for '{selector}'; use exact name '"
            f"{repo.config.name}:{package_ref_text(package_id=package_id, bound_profile=match_bound_profile)}'"
        )
    if len(partial_matches) > 1:
        candidates = ", ".join(
            f"{repo.config.name}:{package_ref_text(package_id=package_id, bound_profile=match_bound_profile)}"
            for repo, package_id, match_bound_profile in partial_matches
        )
        raise ValueError(f"tracked package '{selector}' is ambiguous: {candidates}")
    raise ValueError(f"tracked package '{selector}' did not match any tracked package")


def find_tracked_target_matches(
    engine: Any,
    target_text: str,
    *,
    parse_full_spec_selector_text: Any,
    parse_package_ref_text: Any,
) -> tuple[str, list[TrackedTargetMatch], list[TrackedTargetMatch]]:
    _explicit_repo, selector, profile = parse_full_spec_selector_text(target_text)
    if profile is not None:
        raise ValueError("tracked target lookup expects a target selector, not a binding")
    if "." in selector:
        package_query, separator, target_name = selector.partition(".")
        if not separator or not package_query or not target_name:
            raise ValueError(
                f"invalid tracked target selector '{target_text}'; expected [<repo>:]<package>.<target>"
            )
        parse_package_ref_text(package_query)

    tracked_targets = list_tracked_targets(engine)
    exact_matches: list[TrackedTargetMatch] = []
    partial_matches: list[TrackedTargetMatch] = []
    for candidate in tracked_targets:
        match_fields = build_target_match_fields(
            repo_name=candidate.repo_name,
            package_id=candidate.package_id,
            target_name=candidate.target_name,
            bound_profile=candidate.bound_profile,
        )
        if any(field == target_text for field in match_fields):
            exact_matches.append(candidate)
            continue
        if any(target_text in field for field in match_fields):
            partial_matches.append(candidate)
    return target_text, exact_matches, partial_matches


def list_tracked_targets(engine: Any) -> list[TrackedTargetMatch]:
    tracked_targets: dict[tuple[str, str, str | None, str], TrackedTargetMatch] = {}
    for plan in engine.plan_push():
        repo = engine.get_repo(plan.repo_name)
        for target in plan.target_plans:
            bound_profile = engine._bound_profile_for_package(repo, target.package_id, plan.requested_profile)
            key = (plan.repo_name, target.package_id, bound_profile, target.target_name)
            tracked_targets.setdefault(
                key,
                TrackedTargetMatch(
                    repo_name=plan.repo_name,
                    package_id=target.package_id,
                    target_name=target.target_name,
                    repo_path=target.repo_path,
                    target_kind=target.target_kind,
                    bound_profile=bound_profile,
                ),
            )
    return sorted(
        tracked_targets.values(),
        key=lambda item: (
            item.target_name,
            item.repo_name,
            item.package_id,
            "" if item.bound_profile is None else item.bound_profile,
        ),
    )


def find_tracked_package_matches(
    engine: Any,
    package_text: str,
    *,
    parse_package_ref_text: Any,
) -> tuple[str, str | None, list[tuple[Repository, str, str | None]], list[tuple[Repository, str, str | None]]]:
    explicit_repo, selector, bound_profile = parse_package_ref_text(package_text)
    candidate_repos = engine.candidate_repos(explicit_repo)
    tracked_package_ids = {
        (
            repo.config.name,
            package_id,
            engine._bound_profile_for_package(repo, package_id, binding.profile),
        ): repo
        for repo, binding, _selector_kind, package_ids in engine._iter_tracked_package_entries()
        if repo in candidate_repos
        for package_id in package_ids
    }
    exact_matches = [
        (repo, package_id, match_bound_profile)
        for (repo_name, package_id, match_bound_profile), repo in tracked_package_ids.items()
        if package_id == selector and repo_name == repo.config.name
        and (bound_profile is None or match_bound_profile == bound_profile)
    ]
    partial_matches = [
        (repo, package_id, match_bound_profile)
        for (_repo_name, package_id, match_bound_profile), repo in tracked_package_ids.items()
        if selector in package_ref_text(package_id=package_id, bound_profile=match_bound_profile)
        and (bound_profile is None or match_bound_profile == bound_profile)
    ]
    unique_partials = {
        (repo.config.name, package_id, match_bound_profile): (repo, package_id, match_bound_profile)
        for repo, package_id, match_bound_profile in partial_matches
    }
    return selector, bound_profile, exact_matches, list(unique_partials.values())



def describe_tracked_package_entry(
    engine: Any,
    repo: Repository,
    binding: FullSpecSelector,
    selector_kind: SelectorKind,
    package_id: str,
    package_ids: list[str],
    *,
    executable: bool,
) -> TrackedPackageEntryDetail:
    resolved_packages = [repo.resolve_package(candidate_id) for candidate_id in package_ids]
    profile_vars, lineage = repo.compose_profile(binding.profile)
    package_vars: dict[str, Any] = {}
    for package in resolved_packages:
        package_vars = deep_merge(package_vars, package.vars or {})
    variables = deep_merge(deep_merge(package_vars, profile_vars), repo.local_vars)
    inferred_os = infer_profile_os(binding.profile, lineage, variables)
    context = build_template_context(variables, profile=binding.profile, inferred_os=inferred_os)
    package = repo.resolve_package(package_id)
    selection = engine._resolved_package_selection(
        repo=repo,
        package_id=package_id,
        requested_profile=binding.profile,
        explicit=package_id in engine._selected_package_ids(repo, binding.selector, selector_kind),
        source_kind="tracked_entry",
        source_selector=binding.selector,
    )
    hooks = (
        engine._plan_hooks(
            repo,
            [package],
            context,
            selection=selection,
            operation="push",
            inferred_os=inferred_os,
            variables=variables,
            target_plans=[],
        )
        if executable
        else {}
    )
    targets = summarize_targets(repo, package, context)
    tracked_reason = "explicit" if selection.explicit else "implicit"

    return TrackedPackageEntryDetail(
        package_entry=TrackedPackageEntrySummary(
            repo=repo.config.name,
            selector=binding.selector,
            profile=binding.profile,
            selector_kind=selector_kind,
        ),
        tracked_reason=tracked_reason,
        targets=targets,
        hooks=hooks,
    )



def summarize_targets(
    repo: Repository,
    package: PackageSpec,
    context: dict[str, Any],
) -> list[TrackedTargetSummary]:
    target_summaries: list[TrackedTargetSummary] = []
    for target in (package.targets or {}).values():
        if target.disabled:
            continue
        if target.source is None or target.path is None:
            raise ValueError(f"target '{package.id}:{target.name}' must define source and path")
        rendered_source = render_template_string(target.source, context, base_dir=target.declared_in, source_path=target.declared_in)
        rendered_path = render_template_string(target.path, context, base_dir=target.declared_in, source_path=target.declared_in)
        repo_path = (target.declared_in / rendered_source).resolve()
        live_path = expand_path(rendered_path, dereference=False)
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
        target_summaries.append(
            TrackedTargetSummary(
                target_name=target.name,
                repo_path=repo_path,
                live_path=live_path,
                target_kind=infer_target_kind(repo_path=repo_path, live_path=live_path),
                render_command=render_command,
                capture_command=capture_command,
                reconcile=reconcile,
                pull_view_repo=target.pull_view_repo or "raw",
                pull_view_live=target.pull_view_live or default_pull_view_live(capture_command),
                push_ignore=merge_ignore_patterns(repo.ignore_defaults.push, target.push_ignore or ()),
                pull_ignore=merge_ignore_patterns(repo.ignore_defaults.pull, target.pull_ignore or ()),
                chmod=target.chmod,
            )
        )
    return target_summaries



def tracked_target_summary_from_plan(target: TargetPlan) -> TrackedTargetSummary:
    return TrackedTargetSummary(
        target_name=target.target_name,
        repo_path=target.repo_path,
        live_path=target.live_path,
        target_kind=target.target_kind,
        render_command=target.render_command,
        capture_command=target.capture_command,
        reconcile=target.reconcile,
        pull_view_repo=target.pull_view_repo,
        pull_view_live=target.pull_view_live,
        push_ignore=target.push_ignore,
        pull_ignore=target.pull_ignore,
        chmod=target.chmod,
    )



def describe_owned_package_targets(
    engine: Any,
    repo_name: str,
    package_id: str,
    bound_profile: str | None,
) -> list[TrackedOwnedTargetDetail]:
    owned_targets: list[TrackedOwnedTargetDetail] = []
    for plan in engine.plan_push():
        if plan.repo_name != repo_name:
            continue
        if bound_profile is not None and plan.requested_profile != bound_profile:
            continue
        for target in plan.target_plans:
            if target.package_id != package_id:
                continue
            owned_targets.append(
                TrackedOwnedTargetDetail(
                    package_entry=TrackedPackageEntrySummary(
                        repo=plan.repo_name,
                        selector=plan.selection.source_selector or plan.package_id,
                        profile=plan.requested_profile,
                        selector_kind="package",
                    ),
                    target=tracked_target_summary_from_plan(target),
                )
            )
    return sorted(
        owned_targets,
        key=lambda item: (
            item.target.target_name,
            item.package_entry.profile,
            item.package_entry.selector,
            item.package_entry.repo,
        ),
    )



def effective_tracked_package_entry_keys(
    engine: Any,
    repo_name: str,
    package_id: str,
    bound_profile: str | None,
) -> set[tuple[str, str, str]]:
    effective_package_entries: set[tuple[str, str, str]] = set()
    for plan in engine.plan_push():
        if plan.repo_name != repo_name:
            continue
        if bound_profile is not None and plan.requested_profile != bound_profile:
            continue
        # `info tracked` should report hooks for the package entry that currently owns the package's
        # winning targets, even when the live files already match and push would be all-noop.
        if not any(target.package_id == package_id for target in plan.target_plans):
            continue
        effective_package_entries.add((plan.repo_name, plan.selection.source_selector or plan.package_id, plan.requested_profile))
    return effective_package_entries
