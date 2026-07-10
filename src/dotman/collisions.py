from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotman.config import expand_path
from dotman.ignore import IgnoreMatcher
from dotman.models import PackageSpec, ResolvedPackageSelection, TargetSpec, TrackedTargetSummary
from dotman.templates import render_template_string


class TrackedTargetConflictError(ValueError):
    def __init__(
        self,
        *,
        live_path: Path,
        precedence: str,
        contenders: list[str],
        candidates: list[TrackedTargetCandidate],
    ) -> None:
        self.live_path = live_path
        self.precedence = precedence
        self.contenders = tuple(contenders)
        self.candidates = tuple(candidates)
        conflict_text = ", ".join(contenders)
        super().__init__(
            f"conflicting {precedence} tracked targets for {live_path}: {conflict_text}"
        )


@dataclass(frozen=True)
class TrackedTargetCandidate:
    plan_index: int
    target_index: int
    live_path: Path
    precedence: int
    precedence_name: str
    selection: ResolvedPackageSelection
    selection_label: str
    package_id: str
    target_name: str
    target_label: str
    target_summary: TrackedTargetSummary | None = None


@dataclass(frozen=True)
class TrackedTargetOverride:
    winner: TrackedTargetCandidate
    overridden: tuple[TrackedTargetCandidate, ...]

def resolve_tracked_target_winners(
    candidates_by_live_path: dict[Path, list[TrackedTargetCandidate]],
) -> set[tuple[int, int]]:
    winner_indexes: set[tuple[int, int]] = set()
    for live_path, candidates in candidates_by_live_path.items():
        candidates_by_instance_target: dict[tuple[str, str, str | None, str], TrackedTargetCandidate] = {}
        for candidate in candidates:
            instance_target_key = (
                candidate.selection.identity.repo,
                candidate.selection.identity.package_id,
                candidate.selection.identity.bound_profile,
                candidate.target_name,
            )
            existing = candidates_by_instance_target.get(instance_target_key)
            if existing is None or (candidate.precedence, -candidate.plan_index, -candidate.target_index) > (
                existing.precedence,
                -existing.plan_index,
                -existing.target_index,
            ):
                candidates_by_instance_target[instance_target_key] = candidate

        deduped_candidates = list(candidates_by_instance_target.values())
        highest_precedence = max(candidate.precedence for candidate in deduped_candidates)
        contenders = [candidate for candidate in deduped_candidates if candidate.precedence == highest_precedence]
        first = contenders[0]
        if len(contenders) > 1:
            raise TrackedTargetConflictError(
                live_path=live_path,
                precedence=first.precedence_name,
                contenders=[
                    f"{candidate.selection_label} -> {candidate.target_label}"
                    for candidate in sorted(contenders, key=lambda item: (item.selection_label, item.target_label))
                ],
                candidates=sorted(
                    contenders,
                    key=lambda item: (
                        item.selection_label,
                        item.target_label,
                    ),
                ),
            )
        winner_indexes.add((first.plan_index, first.target_index))
    return winner_indexes



def _operation_ignore_patterns(
    *,
    push_ignore: tuple[str, ...],
    pull_ignore: tuple[str, ...],
    operation: str,
) -> tuple[str, ...]:
    if operation == "push":
        return push_ignore
    if operation == "pull":
        return pull_ignore
    raise ValueError(f"unsupported operation '{operation}'")



def validate_target_collisions(
    rendered_targets: list[tuple[PackageSpec, TargetSpec, Path, Path, tuple[str, ...], tuple[str, ...], bool, str | None]],
    *,
    operation: str,
) -> None:
    for index, (package, target, repo_path, live_path, push_ignore, pull_ignore, _live_path_is_symlink, _live_path_symlink_target) in enumerate(rendered_targets):
        path = operation_write_path(repo_path=repo_path, live_path=live_path, operation=operation)
        for (
            other_package,
            other_target,
            other_repo_path,
            other_live_path,
            other_push_ignore,
            other_pull_ignore,
            _other_live_path_is_symlink,
            _other_live_path_symlink_target,
        ) in rendered_targets[index + 1 :]:
            other_path = operation_write_path(repo_path=other_repo_path, live_path=other_live_path, operation=operation)
            if path == other_path:
                raise ValueError(
                    f"conflicting target ownership: {package.id}:{target.name} and {other_package.id}:{other_target.name} both map to {path}"
                )
            if path in other_path.parents:
                relative = other_path.relative_to(path).as_posix()
                parent_ignore = IgnoreMatcher.from_patterns(
                    _operation_ignore_patterns(
                        push_ignore=push_ignore,
                        pull_ignore=pull_ignore,
                        operation=operation,
                    )
                )
                if not parent_ignore.matches(relative):
                    raise ValueError(
                        f"incompatible nested targets: {package.id}:{target.name} contains {other_package.id}:{other_target.name}"
                    )
            elif other_path in path.parents:
                relative = path.relative_to(other_path).as_posix()
                parent_ignore = IgnoreMatcher.from_patterns(
                    _operation_ignore_patterns(
                        push_ignore=other_push_ignore,
                        pull_ignore=other_pull_ignore,
                        operation=operation,
                    )
                )
                if not parent_ignore.matches(relative):
                    raise ValueError(
                        f"incompatible nested targets: {other_package.id}:{other_target.name} contains {package.id}:{target.name}"
                    )


def operation_write_path(*, repo_path: Path, live_path: Path, operation: str) -> Path:
    if operation == "push":
        return live_path
    if operation == "pull":
        return repo_path
    raise ValueError(f"unsupported operation '{operation}'")



def validate_reserved_path_conflicts(
    packages: list[PackageSpec],
    rendered_targets: list[tuple[PackageSpec, TargetSpec, Path, Path, tuple[str, ...], tuple[str, ...], bool, str | None]],
    context: dict[str, Any],
) -> None:
    target_claims = [
        (package.id, f"{package.id}:{target.name}", live_path)
        for package, target, _repo_path, live_path, _push_ignore, _pull_ignore, _live_path_is_symlink, _live_path_symlink_target in rendered_targets
    ]
    reserved_claims: list[tuple[str, Path]] = []
    for package in packages:
        for reserved_path in package.reserved_paths or ():
            rendered_path = render_template_string(reserved_path, context, base_dir=package.package_root, source_path=package.package_root)
            reserved_claims.append((package.id, expand_path(rendered_path, dereference=False)))

    validate_reserved_path_claims(target_claims=target_claims, reserved_claims=reserved_claims)


def validate_reserved_path_claims(
    *,
    target_claims: list[tuple[str, str, Path]],
    reserved_claims: list[tuple[str, Path]],
) -> None:

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



def paths_conflict(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents
