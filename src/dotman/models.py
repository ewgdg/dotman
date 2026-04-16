from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def package_ref_text(*, package_id: str, bound_profile: str | None = None) -> str:
    if bound_profile is None:
        return package_id
    return f"{package_id}<{bound_profile}>"


@dataclass(frozen=True)
class RepoConfig:
    name: str
    path: Path
    order: int
    state_key: str
    state_path: Path
    local_override_path: Path


@dataclass(frozen=True)
class SnapshotConfig:
    enabled: bool
    path: Path
    max_generations: int


@dataclass(frozen=True)
class SelectionMenuConfig:
    full_paths: bool = False
    bottom_up: bool = True


@dataclass(frozen=True)
class ManagerConfig:
    config_path: Path
    repos: dict[str, RepoConfig]
    snapshots: SnapshotConfig
    selection_menu: SelectionMenuConfig = field(default_factory=SelectionMenuConfig)
    file_symlink_mode: str = "prompt"
    dir_symlink_mode: str = "fail"

    @property
    def ordered_repos(self) -> list[RepoConfig]:
        return sorted(self.repos.values(), key=lambda repo: repo.order)


@dataclass(frozen=True)
class TargetSpec:
    name: str
    declared_in: Path
    source: str | None = None
    path: str | None = None
    sync_policy: str | None = None
    chmod: str | None = None
    render: str | None = None
    capture: str | None = None
    reconcile: str | None = None
    reconcile_io: str | None = None
    pull_view_repo: str | None = None
    pull_view_live: str | None = None
    push_ignore: tuple[str, ...] | None = None
    pull_ignore: tuple[str, ...] | None = None
    disabled: bool = False


@dataclass(frozen=True)
class HookSpec:
    name: str
    commands: tuple[str, ...]
    declared_in: Path


@dataclass(frozen=True)
class PackageSpec:
    id: str
    package_root: Path
    description: str | None = None
    binding_mode: str = "singleton"
    sync_policy: str | None = None
    depends: tuple[str, ...] | None = None
    extends: tuple[str, ...] | None = None
    reserved_paths: tuple[str, ...] | None = None
    vars: dict[str, Any] | None = None
    targets: dict[str, TargetSpec] | None = None
    hooks: dict[str, HookSpec] | None = None
    remove: tuple[str, ...] | None = None
    append: dict[str, Any] | None = None


@dataclass(frozen=True)
class GroupSpec:
    id: str
    members: tuple[str, ...]
    path: Path


@dataclass(frozen=True)
class ProfileSpec:
    id: str
    includes: tuple[str, ...]
    vars: dict[str, Any]
    path: Path


@dataclass(frozen=True)
class RepoIgnoreDefaults:
    push: tuple[str, ...] = ()
    pull: tuple[str, ...] = ()


@dataclass(frozen=True)
class Binding:
    repo: str
    selector: str
    profile: str


@dataclass(frozen=True)
class InstalledBindingSummary:
    repo: str
    selector: str
    profile: str
    selector_kind: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "selector": self.selector,
            "profile": self.profile,
            "selector_kind": self.selector_kind,
        }


@dataclass(frozen=True)
class HookPlan:
    package_id: str
    hook_name: str
    command: str
    cwd: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "package_id": self.package_id,
            "hook_name": self.hook_name,
            "command": self.command,
            "cwd": str(self.cwd),
        }


@dataclass(frozen=True)
class InstalledTargetSummary:
    target_name: str
    repo_path: Path
    live_path: Path
    target_kind: str
    render_command: str | None = None
    capture_command: str | None = None
    reconcile_command: str | None = None
    reconcile_io: str | None = None
    pull_view_repo: str = "raw"
    pull_view_live: str = "raw"
    push_ignore: tuple[str, ...] = ()
    pull_ignore: tuple[str, ...] = ()
    chmod: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_name": self.target_name,
            "repo_path": str(self.repo_path),
            "live_path": str(self.live_path),
            "target_kind": self.target_kind,
            "render_command": self.render_command,
            "capture_command": self.capture_command,
            "reconcile_command": self.reconcile_command,
            "reconcile_io": self.reconcile_io,
            "pull_view_repo": self.pull_view_repo,
            "pull_view_live": self.pull_view_live,
            "push_ignore": list(self.push_ignore),
            "pull_ignore": list(self.pull_ignore),
            "chmod": self.chmod,
        }


@dataclass(frozen=True)
class InstalledPackageSummary:
    repo: str
    package_id: str
    description: str | None
    bindings: list[InstalledBindingSummary]
    state: str
    bound_profile: str | None = None

    @property
    def package_ref(self) -> str:
        return package_ref_text(package_id=self.package_id, bound_profile=self.bound_profile)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "package_id": self.package_id,
            "package_ref": self.package_ref,
            "bound_profile": self.bound_profile,
            "description": self.description,
            "state": self.state,
            "bindings": [binding.to_dict() for binding in self.bindings],
        }


@dataclass(frozen=True)
class TrackedBindingIssue:
    state_key: str
    repo: str
    selector: str
    profile: str
    state: str
    reason: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_key": self.state_key,
            "repo": self.repo,
            "selector": self.selector,
            "profile": self.profile,
            "state": self.state,
            "reason": self.reason,
            "message": self.message,
        }


@dataclass(frozen=True)
class InstalledPackageBindingDetail:
    binding: InstalledBindingSummary
    tracked_reason: str
    targets: list[InstalledTargetSummary]
    hooks: dict[str, list[HookPlan]]

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.binding.to_dict(),
            "tracked_reason": self.tracked_reason,
            "targets": [target.to_dict() for target in self.targets],
            "hooks": {name: [item.to_dict() for item in items] for name, items in self.hooks.items()},
        }


@dataclass(frozen=True)
class InstalledOwnedTargetDetail:
    binding: InstalledBindingSummary
    target: InstalledTargetSummary

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.binding.to_dict(),
            **self.target.to_dict(),
        }


@dataclass(frozen=True)
class InstalledPackageDetail:
    repo: str
    package_id: str
    description: str | None
    bindings: list[InstalledPackageBindingDetail]
    owned_targets: list[InstalledOwnedTargetDetail]
    bound_profile: str | None = None

    @property
    def package_ref(self) -> str:
        return package_ref_text(package_id=self.package_id, bound_profile=self.bound_profile)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "package_id": self.package_id,
            "package_ref": self.package_ref,
            "bound_profile": self.bound_profile,
            "description": self.description,
            "bindings": [binding.to_dict() for binding in self.bindings],
            "owned_targets": [target.to_dict() for target in self.owned_targets],
        }


@dataclass(frozen=True)
class TargetPlan:
    package_id: str
    target_name: str
    repo_path: Path
    live_path: Path
    action: str
    target_kind: str
    projection_kind: str
    desired_text: str | None = None
    render_command: str | None = None
    capture_command: str | None = None
    reconcile_command: str | None = None
    reconcile_io: str | None = None
    projection_error: str | None = None
    live_path_is_symlink: bool = field(default=False, repr=False)
    live_path_symlink_target: str | None = field(default=None, repr=False)
    allow_live_path_symlink_replace: bool = field(default=False, repr=False)
    file_symlink_mode: str = field(default="prompt", repr=False)
    dir_symlink_mode: str = field(default="fail", repr=False)
    pull_view_repo: str = "raw"
    pull_view_live: str = "raw"
    push_ignore: tuple[str, ...] = ()
    pull_ignore: tuple[str, ...] = ()
    chmod: str | None = None
    command_cwd: Path | None = None
    command_env: dict[str, str] | None = field(default=None, repr=False)
    desired_bytes: bytes | None = field(default=None, repr=False)
    review_before_bytes: bytes | None = field(default=None, repr=False)
    review_after_bytes: bytes | None = field(default=None, repr=False)
    directory_items: tuple["DirectoryPlanItem", ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "package_id": self.package_id,
            "target_name": self.target_name,
            "repo_path": str(self.repo_path),
            "live_path": str(self.live_path),
            "action": self.action,
            "target_kind": self.target_kind,
            "projection_kind": self.projection_kind,
            "render_command": self.render_command,
            "capture_command": self.capture_command,
            "reconcile_command": self.reconcile_command,
            "reconcile_io": self.reconcile_io,
            "projection_error": self.projection_error,
            "pull_view_repo": self.pull_view_repo,
            "pull_view_live": self.pull_view_live,
            "push_ignore": list(self.push_ignore),
            "pull_ignore": list(self.pull_ignore),
            "chmod": self.chmod,
            "directory_items": [item.to_dict() for item in self.directory_items],
        }


def filter_hook_plans_for_targets(
    hooks: dict[str, list[HookPlan]],
    target_plans: list[TargetPlan],
) -> dict[str, list[HookPlan]]:
    executable_package_ids = {
        target.package_id
        for target in target_plans
        if target.action != "noop"
    }
    filtered_hooks: dict[str, list[HookPlan]] = {}
    for hook_name, hook_plans in hooks.items():
        matching_hooks = [
            hook_plan
            for hook_plan in hook_plans
            if hook_plan.package_id in executable_package_ids
        ]
        if matching_hooks:
            filtered_hooks[hook_name] = matching_hooks
    return filtered_hooks


@dataclass(frozen=True)
class BindingPlan:
    operation: str
    binding: Binding
    selector_kind: str
    package_ids: list[str]
    variables: dict[str, Any]
    hooks: dict[str, list[HookPlan]]
    target_plans: list[TargetPlan]
    hook_plans: dict[str, list[HookPlan]] | None = field(default=None, repr=False)
    repo_root: Path | None = None
    state_path: Path | None = None
    inferred_os: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.binding.repo,
            "selector": self.binding.selector,
            "profile": self.binding.profile,
            "selector_kind": self.selector_kind,
            "packages": self.package_ids,
            "targets": [target.to_dict() for target in self.target_plans],
            "hooks": {name: [item.to_dict() for item in items] for name, items in self.hooks.items()},
        }


@dataclass(frozen=True)
class DirectoryPlanItem:
    relative_path: str
    action: str
    repo_path: Path
    live_path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "action": self.action,
            "repo_path": str(self.repo_path),
            "live_path": str(self.live_path),
        }
