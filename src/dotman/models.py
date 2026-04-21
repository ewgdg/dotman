from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


def package_ref_text(*, package_id: str, bound_profile: str | None = None) -> str:
    if bound_profile is None:
        return package_id
    return f"{package_id}<{bound_profile}>"


def target_ref_text(*, package_id: str, target_name: str, bound_profile: str | None = None) -> str:
    return f"{package_ref_text(package_id=package_id, bound_profile=bound_profile)}.{target_name}"


def repo_qualified_target_text(*, repo_name: str, package_id: str, target_name: str, bound_profile: str | None = None) -> str:
    return f"{repo_name}:{target_ref_text(package_id=package_id, target_name=target_name, bound_profile=bound_profile)}"


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
    hooks: dict[str, "HookSpec"] | None = None
    disabled: bool = False


@dataclass(frozen=True)
class HookSpec:
    name: str
    commands: tuple[str, ...]
    declared_in: Path
    run_noop: bool = False


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
    description: str | None = None


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


SelectorKind = Literal["package", "group"]


@dataclass(frozen=True)
class ResolvedSelector:
    repo: str
    selector: str
    selector_kind: SelectorKind

    def with_profile(self, profile: str) -> FullSpecSelector:
        return FullSpecSelector(
            repo=self.repo,
            selector=self.selector,
            selector_kind=self.selector_kind,
            profile=profile,
        )


@dataclass(frozen=True)
class FullSpecSelector(ResolvedSelector):
    profile: str


@dataclass(frozen=True)
class TrackedPackageEntry:
    repo: str
    package_id: str
    profile: str


@dataclass(frozen=True)
class ResolvedPackageIdentity:
    repo: str
    package_id: str
    bound_profile: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "package_id": self.package_id,
            "bound_profile": self.bound_profile,
        }


PackageSelectionSourceKind = Literal["selector_query", "tracked_entry", "dependency"]


@dataclass(frozen=True)
class ResolvedPackageSelection:
    identity: ResolvedPackageIdentity
    requested_profile: str
    explicit: bool
    source_kind: PackageSelectionSourceKind
    source_selector: str | None = None
    owner_identity: ResolvedPackageIdentity | None = None

    @property
    def repo_name(self) -> str:
        return self.identity.repo

    @property
    def package_id(self) -> str:
        return self.identity.package_id

    @property
    def bound_profile(self) -> str | None:
        return self.identity.bound_profile

    @property
    def selection_label(self) -> str:
        selector = self.source_selector or self.identity.package_id
        return f"{self.identity.repo}:{selector}@{self.requested_profile}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity.to_dict(),
            "requested_profile": self.requested_profile,
            "explicit": self.explicit,
            "source_kind": self.source_kind,
            "source_selector": self.source_selector,
            "owner_identity": None if self.owner_identity is None else self.owner_identity.to_dict(),
            "selection_label": self.selection_label,
        }


def resolved_package_identity_key(identity: ResolvedPackageIdentity) -> tuple[str, str, str | None]:
    return (identity.repo, identity.package_id, identity.bound_profile)


def resolved_package_selection_key(selection: ResolvedPackageSelection) -> tuple[str, str, str | None, str]:
    return (*resolved_package_identity_key(selection.identity), selection.requested_profile)


@dataclass(frozen=True)
class TrackedPackageEntrySummary:
    repo: str
    selector: str
    profile: str
    selector_kind: SelectorKind

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "package_id": self.selector,
            "profile": self.profile,
        }


@dataclass(frozen=True)
class HookPlan:
    hook_name: str
    command: str
    cwd: Path
    repo_name: str | None = None
    package_id: str | None = None
    target_name: str | None = None
    scope_kind: str = "package"
    env: dict[str, str] | None = field(default=None, repr=False)
    run_noop: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope_kind": self.scope_kind,
            "repo_name": self.repo_name,
            "package_id": self.package_id,
            "target_name": self.target_name,
            "hook_name": self.hook_name,
            "command": self.command,
            "cwd": str(self.cwd),
        }


@dataclass(frozen=True)
class TrackedTargetSummary:
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
class TrackedPackageSummary:
    repo: str
    package_id: str
    description: str | None
    package_entries: list[TrackedPackageEntrySummary]
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
            "package_entries": [package_entry.to_dict() for package_entry in self.package_entries],
        }


@dataclass(frozen=True)
class TrackableTargetDetail:
    target_name: str
    source: str | None
    path: str | None
    render_command: str | None = None
    capture_command: str | None = None
    reconcile_command: str | None = None
    reconcile_io: str | None = None
    pull_view_repo: str | None = None
    pull_view_live: str | None = None
    push_ignore: tuple[str, ...] = ()
    pull_ignore: tuple[str, ...] = ()
    chmod: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_name": self.target_name,
            "source": self.source,
            "path": self.path,
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
class TrackablePackageDetail:
    repo: str
    selector: str
    description: str | None
    binding_mode: str
    tracked_instances: list[TrackedPackageSummary]
    targets: list[TrackableTargetDetail]
    kind: str = field(init=False, default="package")

    @property
    def tracked(self) -> bool:
        return bool(self.tracked_instances)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "repo": self.repo,
            "selector": self.selector,
            "description": self.description,
            "binding_mode": self.binding_mode,
            "tracked": self.tracked,
            "tracked_instances": [instance.to_dict() for instance in self.tracked_instances],
            "targets": [target.to_dict() for target in self.targets],
        }


@dataclass(frozen=True)
class SearchMatch:
    kind: SelectorKind
    repo: str
    selector: str
    qualified_selector: str
    description: str | None
    match_reason: str
    rank: int
    binding_mode: str | None = None
    member_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "repo": self.repo,
            "selector": self.selector,
            "qualified_selector": self.qualified_selector,
            "description": self.description,
            "binding_mode": self.binding_mode,
            "member_count": self.member_count,
            "match_reason": self.match_reason,
            "rank": self.rank,
        }


@dataclass(frozen=True)
class TrackableGroupMemberDetail:
    package_id: str
    tracked_instances: list[TrackedPackageSummary]

    @property
    def tracked(self) -> bool:
        return bool(self.tracked_instances)

    def to_dict(self) -> dict[str, Any]:
        return {
            "package_id": self.package_id,
            "tracked": self.tracked,
            "tracked_instances": [instance.to_dict() for instance in self.tracked_instances],
        }


@dataclass(frozen=True)
class TrackableGroupDetail:
    repo: str
    selector: str
    members: list[TrackableGroupMemberDetail]
    kind: str = field(init=False, default="group")

    @property
    def tracked(self) -> bool:
        return any(member.tracked for member in self.members)

    @property
    def tracked_member_count(self) -> int:
        return sum(1 for member in self.members if member.tracked)

    @property
    def tracked_state(self) -> str:
        if not self.members or self.tracked_member_count == 0:
            return "untracked"
        if self.tracked_member_count == len(self.members):
            return "fully_tracked"
        return "partially_tracked"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "repo": self.repo,
            "selector": self.selector,
            "tracked": self.tracked,
            "tracked_state": self.tracked_state,
            "tracked_member_count": self.tracked_member_count,
            "member_count": len(self.members),
            "members": [member.to_dict() for member in self.members],
        }


@dataclass(frozen=True)
class TrackableCatalogEntry:
    kind: str
    repo: str
    selector: str
    description: str | None
    binding_mode: str | None = None
    member_count: int | None = None

    @property
    def qualified_selector(self) -> str:
        return f"{self.repo}:{self.selector}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "repo": self.repo,
            "selector": self.selector,
            "qualified_selector": self.qualified_selector,
            "description": self.description,
            "binding_mode": self.binding_mode,
            "member_count": self.member_count,
        }


@dataclass(frozen=True)
class TrackedPackageEntryIssue:
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
            "package_id": self.selector,
            "profile": self.profile,
            "state": self.state,
            "reason": self.reason,
            "message": self.message,
        }


@dataclass(frozen=True)
class TrackedPackageEntryDetail:
    package_entry: TrackedPackageEntrySummary
    tracked_reason: str
    targets: list[TrackedTargetSummary]
    hooks: dict[str, list[HookPlan]]

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.package_entry.to_dict(),
            "tracked_reason": self.tracked_reason,
            "targets": [target.to_dict() for target in self.targets],
            "hooks": {name: [item.to_dict() for item in items] for name, items in self.hooks.items()},
        }


@dataclass(frozen=True)
class TrackedOwnedTargetDetail:
    package_entry: TrackedPackageEntrySummary
    target: TrackedTargetSummary

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.package_entry.to_dict(),
            **self.target.to_dict(),
        }


@dataclass(frozen=True)
class TrackedPackageDetail:
    repo: str
    package_id: str
    description: str | None
    package_entries: list[TrackedPackageEntryDetail]
    owned_targets: list[TrackedOwnedTargetDetail]
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
            "package_entries": [package_entry.to_dict() for package_entry in self.package_entries],
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
    return finalize_hook_plans_for_targets(hooks, target_plans)


def executable_package_ids_for_targets(target_plans: list[TargetPlan]) -> set[str]:
    executable_package_ids: set[str] = set()
    for target in target_plans:
        if target.action == "noop":
            continue
        executable_package_ids.add(target.package_id)
    return executable_package_ids


def finalize_hook_plans_for_targets(
    hooks: dict[str, list[HookPlan]],
    target_plans: list[TargetPlan],
    *,
    allow_standalone_noop_hooks: bool = False,
    excluded_standalone_package_ids: set[str] | None = None,
    excluded_standalone_target_ids: set[tuple[str, str]] | None = None,
) -> dict[str, list[HookPlan]]:
    executable_package_ids = executable_package_ids_for_targets(target_plans)
    executable_target_ids = {
        (target.package_id, target.target_name)
        for target in target_plans
        if target.action != "noop"
    }
    excluded_package_ids = excluded_standalone_package_ids or set()
    excluded_target_ids = excluded_standalone_target_ids or set()
    filtered_hooks: dict[str, list[HookPlan]] = {}
    for hook_name, hook_plans in hooks.items():
        matching_hooks = [
            hook_plan
            for hook_plan in hook_plans
            if (
                hook_plan.scope_kind == "package"
                and hook_plan.package_id in executable_package_ids
            )
            or (
                hook_plan.scope_kind == "target"
                and hook_plan.package_id is not None
                and hook_plan.target_name is not None
                and (hook_plan.package_id, hook_plan.target_name) in executable_target_ids
            )
            or (
                hook_plan.scope_kind == "package"
                and hook_plan.package_id not in excluded_package_ids
                and (allow_standalone_noop_hooks or hook_plan.run_noop)
            )
            or (
                hook_plan.scope_kind == "target"
                and hook_plan.package_id is not None
                and hook_plan.target_name is not None
                and (hook_plan.package_id, hook_plan.target_name) not in excluded_target_ids
                and (allow_standalone_noop_hooks or hook_plan.run_noop)
            )
        ]
        if matching_hooks:
            filtered_hooks[hook_name] = matching_hooks
    return filtered_hooks


def standalone_hook_package_summaries(
    hooks: dict[str, list[HookPlan]],
    target_plans: list[TargetPlan],
) -> dict[str, tuple[str, ...]]:
    executable_package_ids = executable_package_ids_for_targets(target_plans)
    hook_names_by_package: dict[str, list[str]] = {}
    for hook_name, hook_plans in hooks.items():
        for hook_plan in hook_plans:
            if hook_plan.scope_kind != "package":
                continue
            if hook_plan.package_id in executable_package_ids:
                continue
            package_hook_names = hook_names_by_package.setdefault(hook_plan.package_id, [])
            if hook_name not in package_hook_names:
                package_hook_names.append(hook_name)
    return {
        package_id: tuple(hook_names)
        for package_id, hook_names in hook_names_by_package.items()
    }


def standalone_hook_target_summaries(
    hooks: dict[str, list[HookPlan]],
    target_plans: list[TargetPlan],
) -> dict[tuple[str, str], tuple[str, ...]]:
    executable_target_ids = {
        (target.package_id, target.target_name)
        for target in target_plans
        if target.action != "noop"
    }
    hook_names_by_target: dict[tuple[str, str], list[str]] = {}
    for hook_name, hook_plans in hooks.items():
        for hook_plan in hook_plans:
            if hook_plan.scope_kind != "target" or hook_plan.package_id is None or hook_plan.target_name is None:
                continue
            target_id = (hook_plan.package_id, hook_plan.target_name)
            if target_id in executable_target_ids:
                continue
            target_hook_names = hook_names_by_target.setdefault(target_id, [])
            if hook_name not in target_hook_names:
                target_hook_names.append(hook_name)
    return {
        target_id: tuple(hook_names)
        for target_id, hook_names in hook_names_by_target.items()
    }


@dataclass(frozen=True)
class PackagePlan:
    operation: str
    selection: ResolvedPackageSelection
    variables: dict[str, Any]
    hooks: dict[str, list[HookPlan]]
    target_plans: list[TargetPlan]
    hook_plans: dict[str, list[HookPlan]] | None = field(default=None, repr=False)
    repo_root: Path | None = None
    state_path: Path | None = None
    inferred_os: str | None = None

    @property
    def repo_name(self) -> str:
        return self.selection.identity.repo

    @property
    def package_id(self) -> str:
        return self.selection.identity.package_id

    @property
    def bound_profile(self) -> str | None:
        return self.selection.identity.bound_profile

    @property
    def requested_profile(self) -> str:
        return self.selection.requested_profile

    @property
    def selection_label(self) -> str:
        return self.selection.selection_label

    def to_dict(self) -> dict[str, Any]:
        return {
            "selection": self.selection.to_dict(),
            "selection_label": self.selection.selection_label,
            "repo": self.selection.identity.repo,
            "package_id": self.selection.identity.package_id,
            "bound_profile": self.selection.identity.bound_profile,
            "profile": self.selection.requested_profile,
            "requested_profile": self.selection.requested_profile,
            "explicit": self.selection.explicit,
            "source_kind": self.selection.source_kind,
            "source_selector": self.selection.source_selector,
            "targets": [target.to_dict() for target in self.target_plans],
            "hooks": {name: [item.to_dict() for item in items] for name, items in self.hooks.items()},
        }


@dataclass(frozen=True)
class OperationPlan:
    operation: str
    package_plans: tuple[PackagePlan, ...]
    repo_hooks: dict[str, dict[str, list[HookPlan]]] = field(default_factory=dict)
    repo_hook_plans: dict[str, dict[str, list[HookPlan]]] | None = field(default=None, repr=False)
    repo_order: tuple[str, ...] = ()

    def __iter__(self):
        return iter(self.package_plans)

    def __len__(self) -> int:
        return len(self.package_plans)

    def __getitem__(self, index: int) -> PackagePlan:
        return self.package_plans[index]

    def to_dict(self) -> dict[str, Any]:
        return {
            "packages": [plan.to_dict() for plan in self.package_plans],
            "repo_hooks": {
                repo_name: {hook_name: [item.to_dict() for item in items] for hook_name, items in hooks.items()}
                for repo_name, hooks in self.repo_hooks.items()
            },
        }


def package_plans_for_operation_plan(plans: OperationPlan | list[PackagePlan] | tuple[PackagePlan, ...]) -> list[PackagePlan]:
    if isinstance(plans, OperationPlan):
        return list(plans.package_plans)
    return list(plans)


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
