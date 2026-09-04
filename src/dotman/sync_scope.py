from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Sequence

from dotman import planning, tracking
from dotman.models import (
    ResolvedPackageSelection,
    ResolvedSyncScope,
    ResolvedSyncTarget,
)
from dotman.package_resolution import parse_package_ref_text
from dotman.collisions import resolve_tracked_target_winners, validate_target_collisions


@dataclass(frozen=True)
class _ScopeSelector:
    text: str
    repo: str
    package_id: str
    bound_profile: str | None
    target_name: str | None
    child_path: str | None


def _parse_scope_selector(text: str) -> _ScopeSelector:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("sync scope selector must not be empty")
    if text != text.strip():
        raise ValueError(f"sync scope '{text}' is not canonical")
    if ":" not in text:
        raise ValueError(f"sync scope '{text}' is not canonical; expected repo:package")
    repo, remainder = text.split(":", 1)
    if not repo or not remainder or ":" in repo:
        raise ValueError(f"sync scope '{text}' is not canonical; expected repo:package")
    target_name: str | None = None
    child_path: str | None = None
    package_ref = remainder
    if "." in remainder:
        package_ref, target_name = remainder.split(".", 1)
        if not package_ref or not target_name:
            raise ValueError(f"sync scope '{text}' is not canonical")
        if "/" in target_name:
            target_name, child_path = target_name.split("/", 1)
            if not target_name or not child_path or "." in target_name:
                raise ValueError(f"sync scope '{text}' is not canonical")
            path = PurePosixPath(child_path)
            if (
                path.is_absolute()
                or path.as_posix() != child_path
                or any(part in {"", ".", ".."} for part in path.parts)
                or "\\" in child_path
            ):
                raise ValueError(f"sync scope '{text}' child path must be normalized POSIX")
        elif "." in target_name:
            raise ValueError(f"sync scope '{text}' is not canonical")
    try:
        parsed_repo, package_id, bound_profile = parse_package_ref_text(f"{repo}:{package_ref}")
    except ValueError as exc:
        raise ValueError(f"sync scope '{text}' is not canonical") from exc
    if parsed_repo != repo:
        raise ValueError(f"sync scope '{text}' is not canonical")
    return _ScopeSelector(text, repo, package_id, bound_profile, target_name, child_path)


def _tracked_selections(context) -> list[ResolvedPackageSelection]:
    entries = tracking.effective_tracked_package_entries_by_repo(context.tracked_state)
    tracked_entries = tracking.tracked_entries_by_repo_from_bindings(entries)
    selections = planning.resolve_tracked_package_selections(
        context,
        entries_by_repo=tracked_entries,
    )
    return selections


def _matching_selection(
    selector: _ScopeSelector,
    *,
    repo,
    selections: list[ResolvedPackageSelection],
) -> ResolvedPackageSelection:
    if selector.package_id in repo.groups and selector.package_id not in repo.packages:
        raise ValueError(f"sync scope '{selector.text}' names group '{selector.package_id}'; groups are not tracked identities")
    candidates = [
        selection
        for selection in selections
        if selection.identity.repo == selector.repo
        and selection.identity.package_id == selector.package_id
        and selection.identity.bound_profile == selector.bound_profile
    ]
    if selector.bound_profile is None:
        candidates = [
            selection
            for selection in candidates
            if repo.package_binding_mode(selection.package_id) != "multi_instance"
        ]
    if not candidates:
        raise ValueError(f"sync scope '{selector.text}' did not match an exact tracked package identity")
    if len(candidates) > 1:
        labels = ", ".join(selection.selection_label for selection in candidates)
        raise ValueError(f"sync scope '{selector.text}' is ambiguous: {labels}")
    return candidates[0]


def _scope_closure(
    context,
    selections: list[ResolvedPackageSelection],
    root: ResolvedPackageSelection,
) -> list[ResolvedPackageSelection]:
    """Return root's dependency closure from the already resolved graph.

    Profile resolution has already selected the winning identity for every
    package before this function runs. Traversing manifests here only tells
    us which graph nodes are related; candidates always come from selections
    so an overridden implicit profile can never be resurrected.
    """
    selections_by_package: dict[tuple[str, str], list[ResolvedPackageSelection]] = {}
    for selection in selections:
        selections_by_package.setdefault(
            (selection.identity.repo, selection.identity.package_id), []
        ).append(selection)

    selected_keys: set[tuple[str, str, str | None, str]] = set()
    queue = [root]
    while queue:
        current = queue.pop(0)
        current_key = (
            current.identity.repo,
            current.identity.package_id,
            current.identity.bound_profile,
            current.requested_profile,
        )
        if current_key in selected_keys:
            continue
        selected_keys.add(current_key)
        repo = context.repositories[current.identity.repo]
        related_ids = planning.resolve_package_ids(
            repo, current.identity.package_id, "package"
        )
        for package_id in related_ids:
            if package_id == current.identity.package_id:
                continue
            candidates = selections_by_package.get((current.identity.repo, package_id), ())
            if not candidates:
                continue
            owner_matches = [
                candidate
                for candidate in candidates
                if candidate.owner_identity == current.identity
            ]
            profile_matches = [
                candidate
                for candidate in candidates
                if candidate.requested_profile == current.requested_profile
                or candidate.identity.bound_profile == current.identity.bound_profile
            ]
            choices = owner_matches or profile_matches or list(candidates)
            # A resolved graph has one winner for singleton identities. For
            # multi-instance dependencies, profile/owner matching keeps the
            # closure on the requesting profile rather than broadening it.
            queue.append(next(iter(choices)))

    return [
        selection
        for selection in selections
        if (
            selection.identity.repo,
            selection.identity.package_id,
            selection.identity.bound_profile,
            selection.requested_profile,
        ) in selected_keys
    ]

def _target_exists(context, selection: ResolvedPackageSelection, target_name: str) -> bool:
    repo = context.repositories[selection.identity.repo]
    package = repo.resolve_package(selection.identity.package_id)
    return target_name in (package.targets or {})


def _target_key(target: ResolvedSyncTarget) -> tuple[str, str, str | None, str]:
    return target.repo, target.package_id, target.bound_profile, target.target_name


def _target_is_directory(context, selection: ResolvedPackageSelection, target_name: str) -> bool:
    repo = context.repositories[selection.identity.repo]
    package = repo.resolve_package(selection.identity.package_id)
    target = (package.targets or {}).get(target_name)
    if target is None:
        return False
    if target.target_type is not None:
        return target.target_type == "directory"
    if target.source is None:
        return False
    return (target.declared_in / target.source).is_dir()


def resolve_sync_scope(
    context,
    selectors: Sequence[str] | None = None,
) -> ResolvedSyncScope:
    raw_selectors = () if selectors is None else ((selectors,) if isinstance(selectors, str) else tuple(selectors))
    parsed = tuple(_parse_scope_selector(text) for text in raw_selectors)
    all_selections = _tracked_selections(context)

    if not parsed:
        selected_selections = list(all_selections)
    else:
        selected_selections = []
        selected_keys: set[tuple[str, str, str | None, str]] = set()
        selected_closures: list[list[ResolvedPackageSelection]] = []
        for item in parsed:
            repo = context.repositories.get(item.repo)
            if repo is None:
                raise ValueError(f"unknown repo '{item.repo}'")
            root = _matching_selection(item, repo=repo, selections=all_selections)
            closure = _scope_closure(context, all_selections, root)
            selected_closures.append(closure)
            for selection in closure:
                key = (
                    selection.identity.repo,
                    selection.identity.package_id,
                    selection.identity.bound_profile,
                    selection.requested_profile,
                )
                if key not in selected_keys:
                    selected_keys.add(key)
                    selected_selections.append(selection)

    # Static ownership and collision resolution runs against the full tracked graph
    # before narrowing to the requested identities.
    winner_keys_by_operation: dict[str, set[tuple[str, str, str | None, str]]] = {}
    for operation in ("push", "pull"):
        candidates = planning.collect_tracked_ownership_candidates(context, operation=operation)
        winners = resolve_tracked_target_winners(candidates)
        winner_keys_by_operation[operation] = {
            (
                candidate.selection.identity.repo,
                candidate.selection.identity.package_id,
                candidate.selection.identity.bound_profile,
                candidate.target_name,
            )
            for candidate_list in candidates.values()
            for candidate in candidate_list
            if (candidate.plan_index, candidate.target_index) in winners
        }

        # Validate path nesting after ownership has selected the winning target
        # at each live/repository write path.
        planning_inputs, _static_candidates = planning.collect_static_target_candidates(
            context, all_selections, operation=operation
        )
        winning_target_keys = winner_keys_by_operation[operation]
        rendered_targets = []
        for planning_input in planning_inputs:
            for metadata in planning_input.target_metadata:
                if not planning.target_claims_path(metadata.target):
                    continue
                metadata_key = (
                    planning_input.selection.identity.repo,
                    planning_input.selection.identity.package_id,
                    planning_input.selection.identity.bound_profile,
                    metadata.target_name,
                )
                if metadata_key not in winning_target_keys:
                    continue
                rendered_targets.append(
                    (
                        metadata.package,
                        metadata.target,
                        metadata.repo_path,
                        metadata.live_path,
                        metadata.ignore_patterns,
                        metadata.live_path_is_symlink,
                        metadata.live_path_symlink_target,
                    )
                )
        validate_target_collisions(rendered_targets, operation=operation)

    selected_targets: list[ResolvedSyncTarget] = []
    selected_target_keys: set[tuple[str, str, str | None, str, str | None]] = set()
    for item, closure in zip(parsed, selected_closures if parsed else ()):
        repo = context.repositories[item.repo]
        root = _matching_selection(item, repo=repo, selections=all_selections)
        if item.target_name is not None:
            if not _target_exists(context, root, item.target_name):
                raise ValueError(f"sync scope '{item.text}' did not match an exact target")
            if item.child_path is not None and not _target_is_directory(context, root, item.target_name):
                raise ValueError(f"sync scope '{item.text}' child identity requires a resolved directory target")
            target = ResolvedSyncTarget(
                repo=item.repo,
                package_id=root.identity.package_id,
                target_name=item.target_name,
                bound_profile=root.identity.bound_profile,
                child_path=item.child_path,
            )
            if not any(_target_key(target) in keys for keys in winner_keys_by_operation.values()):
                raise ValueError(f"sync scope '{item.text}' is not an owning tracked target")
            key = (*_target_key(target), target.child_path)
            if key not in selected_target_keys:
                selected_target_keys.add(key)
                selected_targets.append(target)
        else:
            for selection in closure:
                package = context.repositories[selection.identity.repo].resolve_package(selection.identity.package_id)
                for target_name in (package.targets or {}):
                    target = ResolvedSyncTarget(
                        repo=item.repo,
                        package_id=selection.identity.package_id,
                        target_name=target_name,
                        bound_profile=selection.bound_profile,
                    )
                    if not any(_target_key(target) in keys for keys in winner_keys_by_operation.values()):
                        continue
                    key = (*_target_key(target), None)
                    if key not in selected_target_keys:
                        selected_target_keys.add(key)
                        selected_targets.append(target)

    if not parsed:
        for selection in selected_selections:
            repo = context.repositories[selection.identity.repo]
            package = repo.resolve_package(selection.identity.package_id)
            for target_name in (package.targets or {}):
                target = ResolvedSyncTarget(
                    repo=selection.identity.repo,
                    package_id=selection.identity.package_id,
                    target_name=target_name,
                    bound_profile=selection.identity.bound_profile,
                )
                if not any(_target_key(target) in keys for keys in winner_keys_by_operation.values()):
                    continue
                key = (*_target_key(target), None)
                if key not in selected_target_keys:
                    selected_target_keys.add(key)
                    selected_targets.append(target)

    normalized_selectors = tuple(dict.fromkeys(raw_selectors))
    public_selections = tuple(
        replace(selection, owner_identity=None, owner_selection_label=None)
        for selection in selected_selections
    )
    return ResolvedSyncScope(
        selectors=normalized_selectors,
        package_selections=public_selections,
        targets=tuple(selected_targets),
    )
