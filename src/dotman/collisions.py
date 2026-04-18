from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotman.config import expand_path
from dotman.ignore import IgnoreMatcher
from dotman.manifest import merge_ignore_patterns
from dotman.models import Binding, PackageSpec, TargetPlan, TargetSpec
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
    binding: Binding
    binding_label: str
    package_id: str
    target_name: str
    target_label: str
    signature: tuple[Any, ...]


@dataclass(frozen=True)
class TrackedTargetOverride:
    winner: TrackedTargetCandidate
    overridden: tuple[TrackedTargetCandidate, ...]



def tracked_target_signature(target: TargetPlan) -> tuple[Any, ...]:
    if target.target_kind == "directory":
        return (
            "directory",
            tuple(
                (
                    item.relative_path,
                    item.action,
                    str(item.repo_path),
                )
                for item in target.directory_items
            ),
            target.render_command,
            target.capture_command,
            target.reconcile_command,
            target.push_ignore,
            target.pull_ignore,
        )
    return (
        "file",
        target.desired_bytes,
        target.projection_kind,
        target.projection_error,
        target.render_command,
        target.capture_command,
        target.reconcile_command,
        target.push_ignore,
        target.pull_ignore,
        None if target.desired_bytes is not None else str(target.repo_path),
    )



def resolve_tracked_target_winners(
    candidates_by_live_path: dict[Path, list[TrackedTargetCandidate]],
) -> set[tuple[int, int]]:
    winner_indexes: set[tuple[int, int]] = set()
    for live_path, candidates in candidates_by_live_path.items():
        highest_precedence = max(candidate.precedence for candidate in candidates)
        contenders = [candidate for candidate in candidates if candidate.precedence == highest_precedence]
        first = contenders[0]
        if any(candidate.signature != first.signature for candidate in contenders[1:]):
            raise TrackedTargetConflictError(
                live_path=live_path,
                precedence=first.precedence_name,
                contenders=[
                    f"{candidate.binding_label} -> {candidate.target_label}"
                    for candidate in sorted(contenders, key=lambda item: (item.binding_label, item.target_label))
                ],
                candidates=sorted(
                    contenders,
                    key=lambda item: (
                        item.binding_label,
                        item.target_label,
                    ),
                ),
            )
        winner_indexes.add((first.plan_index, first.target_index))
    return winner_indexes



def validate_target_collisions(
    rendered_targets: list[tuple[PackageSpec, TargetSpec, Path, Path, tuple[str, ...], tuple[str, ...], bool, str | None]],
) -> None:
    for index, (package, target, _repo_path, live_path, push_ignore, pull_ignore, _live_path_is_symlink, _live_path_symlink_target) in enumerate(rendered_targets):
        for (
            other_package,
            other_target,
            _other_repo_path,
            other_live_path,
            other_push_ignore,
            other_pull_ignore,
            _other_live_path_is_symlink,
            _other_live_path_symlink_target,
        ) in rendered_targets[index + 1 :]:
            if live_path == other_live_path:
                raise ValueError(
                    f"conflicting target ownership: {package.id}:{target.name} and {other_package.id}:{other_target.name} both map to {live_path}"
                )
            if live_path in other_live_path.parents:
                relative = other_live_path.relative_to(live_path).as_posix()
                parent_ignore = IgnoreMatcher.from_patterns(
                    merge_ignore_patterns(push_ignore, pull_ignore)
                )
                if not parent_ignore.matches(relative):
                    raise ValueError(
                        f"incompatible nested targets: {package.id}:{target.name} contains {other_package.id}:{other_target.name}"
                    )
            elif other_live_path in live_path.parents:
                relative = live_path.relative_to(other_live_path).as_posix()
                parent_ignore = IgnoreMatcher.from_patterns(
                    merge_ignore_patterns(other_push_ignore, other_pull_ignore)
                )
                if not parent_ignore.matches(relative):
                    raise ValueError(
                        f"incompatible nested targets: {other_package.id}:{other_target.name} contains {package.id}:{target.name}"
                    )



def validate_reserved_path_conflicts(
    engine: Any,
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

    for package_id, reserved_path in reserved_claims:
        for target_package_id, target_label, target_path in target_claims:
            if package_id == target_package_id:
                continue
            if engine._paths_conflict(reserved_path, target_path):
                raise ValueError(
                    f"reserved path conflict: {package_id} reserves {reserved_path} and {target_label} maps to {target_path}"
                )

    for index, (package_id, reserved_path) in enumerate(reserved_claims):
        for other_package_id, other_reserved_path in reserved_claims[index + 1 :]:
            if package_id == other_package_id:
                continue
            if engine._paths_conflict(reserved_path, other_reserved_path):
                raise ValueError(
                    f"reserved path conflict: {package_id} reserves {reserved_path} and {other_package_id} reserves {other_reserved_path}"
                )



def paths_conflict(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents
