"""Frozen Push evidence and optional, unit-local checkpoint persistence."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotman.file_access import read_bytes
from dotman.models import (
    DirectoryPlanItem,
    ResolvedPackageSelection,
    ResolvedSyncTarget,
    TargetPlan,
    target_path_rule_matches,
)
from dotman.repository import Repository
from dotman.sync_base_lifecycle import (
    BaseInputs,
    BaseLifecycleResult,
    BaseProfileContext,
    BaseUnit,
    FrozenBaseUnit,
    ProposalCompletion,
    SyncBaseLifecycle,
)
from dotman.sync_base_store import (
    DirectoryChildPresent,
    FilePresent,
    Missing,
    SyncBasePayload,
    SyncBaseStore,
    SyncBaseStoreError,
)


def freeze_payload(path: Path, *, child: bool = False) -> SyncBasePayload:
    """Freeze repository-space evidence alongside the required planning Render."""
    if not path.exists():
        return Missing()
    content = read_bytes(path)
    return DirectoryChildPresent(content, bool(path.stat().st_mode & 0o111)) if child else FilePresent(content)


@dataclass(frozen=True)
class PushCheckpoint:
    frozen: FrozenBaseUnit
    manager_root: Path
    state_key: str
    action: str

    def acknowledge(self) -> BaseLifecycleResult:
        try:
            with SyncBaseStore.open(self.manager_root, self.state_key) as store:
                lifecycle = SyncBaseLifecycle(store, operation="push")
                if self.action == "noop":
                    return lifecycle.direct_agreement(self.frozen)
                return lifecycle.complete(
                    self.frozen,
                    ProposalCompletion("use-repository", approved=True, publication_effects="succeeded"),
                )
        except SyncBaseStoreError as exc:
            return BaseLifecycleResult(converged=self.action != "noop", failure=exc)


def checkpoints_for_target(
    target: TargetPlan,
    *,
    repo: Repository,
    selection: ResolvedPackageSelection,
    context: dict[str, Any],
    manager_root: Path,
) -> tuple[PushCheckpoint, ...]:
    candidates = (
        [*target.directory_items, *target.checkpoint_agreements]
        if target.target_kind == "directory" else [target]
    )
    result = []
    for candidate in candidates:
        policy = candidate.sync_policy or target.sync_policy
        if candidate.checkpoint_payload is None or policy not in ("both", "pull-only"):
            continue
        child = candidate.relative_path if isinstance(candidate, DirectoryPlanItem) else None
        identity = ResolvedSyncTarget(
            repo.config.name, target.package_id, target.target_name, selection.bound_profile, child,
        )
        unit = BaseUnit(
            identity,
            candidate.repo_path.relative_to(repo.root).as_posix(),
            policy,
            BaseInputs(
                render=candidate.render_command or "raw",
                capture=candidate.capture_command or "raw",
                profile_context=BaseProfileContext(context),
                path_rules=tuple(
                    rule.name for rule in target.path_rules
                    if child is not None and target_path_rule_matches(child, rule.pattern)
                ),
                file_symlink_mode=target.file_symlink_mode,
                dir_symlink_mode=target.dir_symlink_mode,
            ),
        )
        result.append(PushCheckpoint(
            FrozenBaseUnit(unit, candidate.checkpoint_payload), manager_root, repo.config.state_key, candidate.action,
        ))
    return tuple(result)
