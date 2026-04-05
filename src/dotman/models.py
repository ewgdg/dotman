from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RepoConfig:
    name: str
    path: Path
    order: int
    state_path: Path


@dataclass(frozen=True)
class ManagerConfig:
    config_path: Path
    repos: dict[str, RepoConfig]

    @property
    def ordered_repos(self) -> list[RepoConfig]:
        return sorted(self.repos.values(), key=lambda repo: repo.order)


@dataclass(frozen=True)
class TargetSpec:
    name: str
    declared_in: Path
    source: str | None = None
    path: str | None = None
    chmod: str | None = None
    render: str | None = None
    capture: str | None = None
    reconcile: str | None = None
    pull_view_repo: str | None = None
    pull_view_live: str | None = None
    push_ignore: tuple[str, ...] | None = None
    pull_ignore: tuple[str, ...] | None = None


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
    pull_view_repo: str = "raw"
    pull_view_live: str = "raw"
    push_ignore: tuple[str, ...] = ()
    pull_ignore: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_name": self.target_name,
            "repo_path": str(self.repo_path),
            "live_path": str(self.live_path),
            "target_kind": self.target_kind,
            "render_command": self.render_command,
            "capture_command": self.capture_command,
            "reconcile_command": self.reconcile_command,
            "pull_view_repo": self.pull_view_repo,
            "pull_view_live": self.pull_view_live,
            "push_ignore": list(self.push_ignore),
            "pull_ignore": list(self.pull_ignore),
        }


@dataclass(frozen=True)
class InstalledPackageSummary:
    repo: str
    package_id: str
    description: str | None
    bindings: list[InstalledBindingSummary]

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "package_id": self.package_id,
            "description": self.description,
            "bindings": [binding.to_dict() for binding in self.bindings],
        }


@dataclass(frozen=True)
class InstalledPackageBindingDetail:
    binding: InstalledBindingSummary
    targets: list[InstalledTargetSummary]
    hooks: dict[str, list[HookPlan]]

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.binding.to_dict(),
            "targets": [target.to_dict() for target in self.targets],
            "hooks": {name: [item.to_dict() for item in items] for name, items in self.hooks.items()},
        }


@dataclass(frozen=True)
class InstalledPackageDetail:
    repo: str
    package_id: str
    description: str | None
    bindings: list[InstalledPackageBindingDetail]

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "package_id": self.package_id,
            "description": self.description,
            "bindings": [binding.to_dict() for binding in self.bindings],
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
    projection_error: str | None = None
    pull_view_repo: str = "raw"
    pull_view_live: str = "raw"
    push_ignore: tuple[str, ...] = ()
    pull_ignore: tuple[str, ...] = ()
    desired_bytes: bytes | None = field(default=None, repr=False)

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
            "projection_error": self.projection_error,
            "pull_view_repo": self.pull_view_repo,
            "pull_view_live": self.pull_view_live,
            "push_ignore": list(self.push_ignore),
            "pull_ignore": list(self.pull_ignore),
        }


@dataclass(frozen=True)
class BindingPlan:
    operation: str
    binding: Binding
    selector_kind: str
    package_ids: list[str]
    variables: dict[str, Any]
    hooks: dict[str, list[HookPlan]]
    target_plans: list[TargetPlan]

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
