from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from dotman.config import load_manager_config
from dotman.ignore import list_directory_files, matches_ignore_pattern
from dotman.models import (
    Binding,
    BindingPlan,
    InstalledBindingSummary,
    InstalledPackageBindingDetail,
    InstalledPackageDetail,
    InstalledPackageSummary,
    InstalledOwnedTargetDetail,
    InstalledTargetSummary,
    ManagerConfig,
    PackageSpec,
    TrackedBindingIssue,
)
from dotman.planning import (
    HOOK_NAMES_BY_OPERATION,
    TrackedTargetCandidate,
    TrackedTargetConflictError,
    TrackedTargetOverride,
)
from dotman.profiles import rank_profiles
from dotman.repository import Repository
from dotman.tracking import (
    PersistedBindingRecord,
    TrackedStateSummary,
)
from dotman import installed, tracking

def parse_binding_text(binding_text: str) -> tuple[str | None, str, str | None]:
    repo_name: str | None = None
    selector_and_profile = binding_text
    if ":" in binding_text:
        potential_repo, remainder = binding_text.split(":", 1)
        if "/" not in potential_repo:
            repo_name = potential_repo
            selector_and_profile = remainder
    selector, _, profile = selector_and_profile.partition("@")
    if not selector:
        raise ValueError("selector must not be empty")
    return repo_name, selector, profile or None


def parse_package_ref_text(package_text: str) -> tuple[str | None, str, str | None]:
    repo_name, selector, profile = parse_binding_text(package_text)
    if profile is not None:
        raise ValueError("tracked package lookup expects a package selector, not a binding")
    bound_profile: str | None = None
    if selector.endswith(">"):
        open_index = selector.rfind("<")
        if open_index == -1:
            raise ValueError(f"invalid tracked package selector '{selector}'")
        bound_profile = selector[open_index + 1 : -1]
        selector = selector[:open_index]
        if not selector or not bound_profile:
            raise ValueError(f"invalid tracked package selector '{package_text}'")
    return repo_name, selector, bound_profile


class DotmanEngine:
    def __init__(self, config: ManagerConfig) -> None:
        self.config = config
        self.repos = {repo.name: Repository(repo) for repo in config.ordered_repos}

    @classmethod
    def from_config_path(
        cls,
        config_path: str | Path | None = None,
        *,
        file_symlink_mode: str | None = None,
        dir_symlink_mode: str | None = None,
    ) -> "DotmanEngine":
        config = load_manager_config(config_path)
        if file_symlink_mode is not None or dir_symlink_mode is not None:
            config = replace(
                config,
                file_symlink_mode=file_symlink_mode or config.file_symlink_mode,
                dir_symlink_mode=dir_symlink_mode or config.dir_symlink_mode,
            )
        return cls(config)

    def get_repo(self, repo_name: str) -> Repository:
        try:
            return self.repos[repo_name]
        except KeyError as exc:
            raise ValueError(f"unknown repo '{repo_name}'") from exc

    def candidate_repos(self, repo_name: str | None = None) -> list[Repository]:
        if repo_name:
            return [self.get_repo(repo_name)]
        return [self.repos[repo.name] for repo in self.config.ordered_repos]

    def find_selector_matches(
        self,
        selector: str,
        repo_name: str | None = None,
    ) -> tuple[list[tuple[Repository, str, str]], list[tuple[Repository, str, str]]]:
        candidate_repos = self.candidate_repos(repo_name)
        exact_matches: list[tuple[Repository, str, str]] = []
        partial_matches: list[tuple[Repository, str, str]] = []
        for repo in candidate_repos:
            package_match = selector in repo.packages
            group_match = selector in repo.groups
            if package_match and group_match:
                raise ValueError(f"selector '{selector}' is ambiguous between package and group in repo '{repo.config.name}'")
            if package_match:
                exact_matches.append((repo, selector, "package"))
                continue
            if group_match:
                exact_matches.append((repo, selector, "group"))
                continue
            for package_id in repo.packages:
                if selector in package_id:
                    partial_matches.append((repo, package_id, "package"))
            for group_id in repo.groups:
                if selector in group_id:
                    partial_matches.append((repo, group_id, "group"))
        unique_partials = {(repo.config.name, match, kind): (repo, match, kind) for repo, match, kind in partial_matches}
        return exact_matches, list(unique_partials.values())

    def list_profiles(self, repo_name: str) -> list[str]:
        repo = self.get_repo(repo_name)
        return rank_profiles({profile_id: profile.includes for profile_id, profile in repo.profiles.items()})

    def resolve_selector(self, selector: str, repo_name: str | None = None) -> tuple[Repository, str, str]:
        exact_matches, partial_matches = self.find_selector_matches(selector, repo_name)

        if len(exact_matches) == 1:
            return exact_matches[0]
        if len(exact_matches) > 1:
            candidates = ", ".join(f"{repo.config.name}:{match}" for repo, match, _ in exact_matches)
            raise ValueError(f"selector '{selector}' is defined in multiple repos: {candidates}")
        if len(partial_matches) == 1:
            repo, match, _selector_kind = partial_matches[0]
            raise ValueError(
                f"no exact match for '{selector}'; use exact name '{repo.config.name}:{match}'"
            )
        if len(partial_matches) > 1:
            candidates = ", ".join(f"{repo.config.name}:{match}" for repo, match, _ in partial_matches)
            raise ValueError(f"selector '{selector}' is ambiguous: {candidates}")
        raise ValueError(f"selector '{selector}' did not match any package or group")

    def resolve_binding(self, binding_text: str, *, profile: str | None = None) -> tuple[Repository, Binding, str]:
        explicit_repo, selector, selector_profile = parse_binding_text(binding_text)
        repo, resolved_selector, selector_kind = self.resolve_selector(selector, explicit_repo)
        resolved_profile = profile or selector_profile
        if not resolved_profile:
            raise ValueError("profile is required in non-interactive mode")
        return repo, Binding(repo=repo.config.name, selector=resolved_selector, profile=resolved_profile), selector_kind

    def plan_push_binding(self, binding_text: str, *, profile: str | None = None) -> BindingPlan:
        repo, binding, selector_kind = self.resolve_binding(binding_text, profile=profile)
        return self._build_plan(repo, binding, selector_kind, operation="push")

    def plan_pull_binding(self, binding_text: str, *, profile: str | None = None) -> BindingPlan:
        repo, binding, selector_kind = self.resolve_binding(binding_text, profile=profile)
        return self._build_plan(repo, binding, selector_kind, operation="pull")

    def resolve_tracked_binding(
        self,
        binding_text: str,
        *,
        operation: str = "untrack",
        allow_package_owners: bool = False,
    ) -> tuple[Repository, Binding]:
        explicit_repo, _parsed_selector, _parsed_profile = parse_binding_text(binding_text)
        selector, profile, exact_matches, partial_matches, owner_bindings = self.find_tracked_binding_matches(binding_text)
        binding_label = selector if profile is None else f"{selector}@{profile}"
        if len(exact_matches) == 1:
            return exact_matches[0]
        if len(exact_matches) > 1:
            candidates = ", ".join(
                f"{repo.config.name}:{binding.selector}@{binding.profile}"
                for repo, binding in exact_matches
            )
            raise ValueError(f"binding '{binding_label}' is ambiguous: {candidates}")

        if len(partial_matches) == 1:
            repo, binding = partial_matches[0]
            raise ValueError(
                f"no exact match for '{binding_label}'; use exact name '{repo.config.name}:{binding.selector}@{binding.profile}'"
            )
        if len(partial_matches) > 1:
            candidates = ", ".join(
                f"{repo.config.name}:{binding.selector}@{binding.profile}"
                for repo, binding in partial_matches
            )
            raise ValueError(f"binding '{binding_label}' is ambiguous: {candidates}")

        if owner_bindings:
            if allow_package_owners:
                if len(owner_bindings) == 1:
                    owner_repo, owner_binding = owner_bindings[0]
                    return owner_repo, Binding(
                        repo=owner_repo.config.name,
                        selector=selector,
                        profile=owner_binding.profile,
                    )
                candidates = ", ".join(
                    f"{repo.config.name}:{binding.selector}@{binding.profile}"
                    for repo, binding in owner_bindings
                )
                raise ValueError(f"{operation} target '{binding_label}' is ambiguous across tracked bindings: {candidates}")
            owners = ", ".join(
                f"{repo.config.name}:{binding.selector}@{binding.profile}"
                for repo, binding in owner_bindings
            )
            required_repo = explicit_repo or owner_bindings[0][0].config.name
            required_ref = f"{required_repo}:{selector}"
            raise ValueError(
                f"cannot {operation} '{required_ref}': required by tracked bindings: {owners}"
            )

        raise ValueError(f"binding '{binding_label}' is not currently tracked")

    def find_tracked_binding_matches(
        self,
        binding_text: str,
    ) -> tuple[str, str | None, list[tuple[Repository, Binding]], list[tuple[Repository, Binding]], list[tuple[Repository, Binding]]]:
        explicit_repo, selector, profile = parse_binding_text(binding_text)
        candidate_repos = self.candidate_repos(explicit_repo)
        tracked = [
            (repo, binding)
            for repo in candidate_repos
            for binding in self.read_effective_bindings(repo)
            if profile is None or binding.profile == profile
        ]

        exact_matches = [(repo, binding) for repo, binding in tracked if binding.selector == selector]
        partial_matches = [(repo, binding) for repo, binding in tracked if selector in binding.selector]
        unique_partials = {
            (repo.config.name, binding.selector, binding.profile): (repo, binding)
            for repo, binding in partial_matches
        }
        owner_bindings = self._find_tracked_package_owners(candidate_repos, selector, profile)
        unique_owners = {
            (repo.config.name, binding.selector, binding.profile): (repo, binding)
            for repo, binding in owner_bindings
        }
        return selector, profile, exact_matches, list(unique_partials.values()), list(unique_owners.values())

    def plan_upgrade(self) -> list[BindingPlan]:
        return self._build_tracked_plans(operation="upgrade")

    def plan_push(self) -> list[BindingPlan]:
        return self._build_tracked_plans(operation="push")

    def plan_upgrade_binding(self, binding_text: str, *, profile: str | None = None) -> BindingPlan:
        repo, binding, selector_kind = self.resolve_binding(binding_text, profile=profile)
        return self._build_plan(repo, binding, selector_kind, operation="upgrade")

    def plan_pull(self) -> list[BindingPlan]:
        return self._build_tracked_plans(operation="pull")

    def _tracking_helpers(self):
        return tracking

    def list_tracked_state(self) -> TrackedStateSummary:
        return self._tracking_helpers().list_tracked_state(self)

    def list_invalid_explicit_bindings(
        self,
        *,
        bindings_by_repo: dict[str, list[Binding]] | None = None,
    ) -> list[TrackedBindingIssue]:
        return self._tracking_helpers().list_invalid_explicit_bindings(
            self,
            bindings_by_repo=bindings_by_repo,
        )

    def list_orphan_explicit_bindings(self) -> list[TrackedBindingIssue]:
        return self._tracking_helpers().list_orphan_explicit_bindings(self)

    def list_tracked_packages(self) -> list[InstalledPackageSummary]:
        return self._tracking_helpers().list_tracked_packages(self)

    def list_installed_packages(self) -> list[InstalledPackageSummary]:
        return self._tracking_helpers().list_installed_packages(self)

    def describe_tracked_package(self, package_text: str) -> InstalledPackageDetail:
        return self._tracking_helpers().describe_tracked_package(self, package_text)

    def describe_installed_package(self, package_text: str) -> InstalledPackageDetail:
        return self._tracking_helpers().describe_installed_package(self, package_text)

    def _read_bindings_file(self, state_path: Path) -> list[Binding]:
        return self._tracking_helpers().read_bindings_file(state_path)

    def read_bindings(self, repo: Repository) -> list[Binding]:
        return self._tracking_helpers().read_bindings(self, repo)

    def read_effective_bindings(self, repo: Repository) -> list[Binding]:
        return self._tracking_helpers().read_effective_bindings(self, repo)

    def expand_binding_for_tracking(self, binding: Binding) -> list[Binding]:
        return self._tracking_helpers().expand_binding_for_tracking(self, binding)

    def _raw_bindings_by_repo(self) -> dict[str, list[Binding]]:
        return self._tracking_helpers().raw_bindings_by_repo(self)

    def _effective_bindings_by_repo(
        self,
        raw_bindings_by_repo: dict[str, list[Binding]] | None = None,
    ) -> dict[str, list[Binding]]:
        return self._tracking_helpers().effective_bindings_by_repo(
            self,
            raw_bindings_by_repo=raw_bindings_by_repo,
        )

    def _binding_scope_key(self, repo: Repository, binding: Binding) -> tuple[str, str, str | None]:
        return self._tracking_helpers().binding_scope_key(self, repo, binding)

    def _bound_profile_for_package(
        self,
        repo: Repository,
        package_id: str,
        binding_profile: str,
    ) -> str | None:
        return self._tracking_helpers().bound_profile_for_package(
            self,
            repo,
            package_id,
            binding_profile,
        )

    def _normalize_recorded_bindings(self, bindings: list[Binding], binding: Binding) -> list[Binding]:
        return self._tracking_helpers().normalize_recorded_bindings(self, bindings, binding)

    def _normalize_recorded_binding_set(self, bindings: list[Binding], additions: list[Binding]) -> list[Binding]:
        return self._tracking_helpers().normalize_recorded_binding_set(self, bindings, additions)

    def _expand_binding_for_tracking(self, repo: Repository, binding: Binding) -> list[Binding]:
        return self._tracking_helpers().expand_binding_for_tracking_in_repo(repo, binding)

    def _effective_bindings_for_repo(self, repo: Repository, raw_bindings: list[Binding]) -> list[Binding]:
        return self._tracking_helpers().effective_bindings_for_repo(self, repo, raw_bindings)

    def _validate_tracked_bindings(self, bindings_by_repo: dict[str, list[Binding]]) -> None:
        self._tracking_helpers().validate_tracked_bindings(self, bindings_by_repo)

    def record_binding(self, binding: Binding) -> None:
        self._tracking_helpers().record_binding(self, binding)

    def validate_recorded_binding(self, binding: Binding) -> None:
        self._tracking_helpers().validate_recorded_binding(self, binding)

    def find_persisted_binding_matches(
        self,
        binding_text: str,
    ) -> tuple[str, str | None, list[PersistedBindingRecord], list[PersistedBindingRecord]]:
        return self._tracking_helpers().find_persisted_binding_matches(
            self,
            binding_text,
            parse_binding_text=parse_binding_text,
        )

    def remove_binding(self, binding_text: str, *, operation: str = "untrack") -> Binding:
        return self._tracking_helpers().remove_binding(
            self,
            binding_text,
            operation=operation,
            parse_binding_text=parse_binding_text,
        )

    def _find_tracked_package_owners(
        self,
        candidate_repos: list[Repository],
        selector: str,
        profile: str | None,
    ) -> list[tuple[Repository, Binding]]:
        return self._tracking_helpers().find_tracked_package_owners(
            self,
            candidate_repos,
            selector,
            profile,
        )

    def write_bindings(self, repo: Repository, bindings: list[Binding]) -> None:
        self._tracking_helpers().write_bindings(self, repo, bindings)

    def _write_bindings_file(self, state_dir: Path, bindings: list[Binding]) -> None:
        self._tracking_helpers().write_bindings_file(state_dir, bindings)

    def remove_persisted_binding(self, record: PersistedBindingRecord, *, operation: str = "untrack") -> Binding:
        return self._tracking_helpers().remove_persisted_binding(
            self,
            record,
            operation=operation,
            tracked_target_conflict_error=TrackedTargetConflictError,
        )

    def _remove_binding_record(self, bindings: list[Binding], target: Binding) -> list[Binding]:
        return self._tracking_helpers().remove_binding_record(bindings, target)

    def _iter_tracked_bindings(self) -> list[tuple[Repository, Binding, str, list[str]]]:
        return self._tracking_helpers().iter_tracked_bindings(self)

    def _iter_installed_bindings(self) -> list[tuple[Repository, Binding, str, list[str]]]:
        return self._tracking_helpers().iter_installed_bindings(self)

    def _configured_persisted_binding_records(
        self,
        *,
        bindings_by_repo: dict[str, list[Binding]] | None = None,
    ) -> tuple[list[PersistedBindingRecord], list[PersistedBindingRecord]]:
        return self._tracking_helpers().configured_persisted_binding_records(
            self,
            bindings_by_repo=bindings_by_repo,
        )

    def _orphan_persisted_binding_records(self) -> list[PersistedBindingRecord]:
        return self._tracking_helpers().orphan_persisted_binding_records(self)

    def _all_persisted_binding_records(self) -> list[PersistedBindingRecord]:
        return self._tracking_helpers().all_persisted_binding_records(self)

    def _resolve_persisted_binding(self, repo: Repository, binding: Binding) -> list[Binding]:
        return self._tracking_helpers().resolve_persisted_binding(self, repo, binding)

    def _tracked_package_matches_for_untrack(
        self,
        *,
        selector: str,
        profile: str | None,
        repo_name: str | None,
    ) -> tuple[list[InstalledPackageSummary], list[InstalledBindingSummary]]:
        return self._tracking_helpers().tracked_package_matches_for_untrack(
            self,
            selector=selector,
            profile=profile,
            repo_name=repo_name,
        )

    def _sorted_binding_issues(self, issues: list[TrackedBindingIssue]) -> list[TrackedBindingIssue]:
        return self._tracking_helpers().sorted_binding_issues(issues)

    def _format_persisted_binding_candidates(self, records: list[PersistedBindingRecord]) -> str:
        return self._tracking_helpers().format_persisted_binding_candidates(records)

    def _format_tracked_package_candidates(self, packages: list[InstalledPackageSummary]) -> str:
        return self._tracking_helpers().format_tracked_package_candidates(packages)

    def _format_owner_bindings(self, bindings: list[InstalledBindingSummary]) -> str:
        return self._tracking_helpers().format_owner_bindings(bindings)

    def _selected_package_ids(self, repo: Repository, selector: str, selector_kind: str) -> list[str]:
        return [selector] if selector_kind == "package" else repo.expand_group(selector)

    def _installed_helpers(self):
        return installed

    def _resolve_installed_package(self, package_text: str) -> tuple[Repository, str, str | None]:
        return self._installed_helpers().resolve_installed_package(self, package_text)

    def find_installed_package_matches(
        self,
        package_text: str,
    ) -> tuple[str, str | None, list[tuple[Repository, str, str | None]], list[tuple[Repository, str, str | None]]]:
        return self._installed_helpers().find_installed_package_matches(
            self,
            package_text,
            parse_package_ref_text=parse_package_ref_text,
        )

    def _describe_package_binding(
        self,
        repo: Repository,
        binding: Binding,
        selector_kind: str,
        package_id: str,
        package_ids: list[str],
        *,
        executable: bool,
    ) -> InstalledPackageBindingDetail:
        return self._installed_helpers().describe_package_binding(
            self,
            repo,
            binding,
            selector_kind,
            package_id,
            package_ids,
            executable=executable,
        )

    def _resolve_package_ids(self, repo: Repository, selector: str, selector_kind: str) -> list[str]:
        roots = self._selected_package_ids(repo, selector, selector_kind)
        ordered: list[str] = []
        seen_packages: set[str] = set()
        completed_nodes: set[tuple[str, str]] = set()

        def format_cycle(stack: tuple[tuple[str, str], ...], node: tuple[str, str]) -> str:
            cycle_start = stack.index(node)
            cycle_nodes = [*stack[cycle_start:], node]
            return " -> ".join(node_id for _node_kind, node_id in cycle_nodes)

        def visit_selector(current_selector: str, stack: tuple[tuple[str, str], ...], *, source: str) -> None:
            package_exists = current_selector in repo.packages
            group_exists = current_selector in repo.groups
            if package_exists and group_exists:
                raise ValueError(
                    f"selector '{current_selector}' is ambiguous between package and group in repo '{repo.config.name}'"
                )
            if not package_exists and not group_exists:
                raise ValueError(f"{source} '{current_selector}' does not resolve in repo '{repo.config.name}'")

            node_kind = "package" if package_exists else "group"
            node = (node_kind, current_selector)
            if node in stack:
                raise ValueError(f"dependency cycle detected in repo '{repo.config.name}': {format_cycle(stack, node)}")
            if node in completed_nodes:
                return

            next_stack = (*stack, node)
            if group_exists:
                for member in repo.groups[current_selector].members:
                    visit_selector(member, next_stack, source="group member")
                completed_nodes.add(node)
                return

            if current_selector not in seen_packages:
                seen_packages.add(current_selector)
                ordered.append(current_selector)
            for dependency in repo.resolve_package(current_selector).depends or ():
                visit_selector(dependency, next_stack, source="dependency")
            completed_nodes.add(node)

        for root_package in roots:
            visit_selector(root_package, (), source="package")
        return ordered

    def _summarize_targets(
        self,
        repo: Repository,
        package: PackageSpec,
        context: dict[str, Any],
    ) -> list[InstalledTargetSummary]:
        return self._installed_helpers().summarize_targets(repo, package, context)

    def _installed_target_summary_from_plan(self, target: Any) -> InstalledTargetSummary:
        return self._installed_helpers().installed_target_summary_from_plan(target)

    def _describe_owned_package_targets(
        self,
        repo_name: str,
        package_id: str,
        bound_profile: str | None,
    ) -> list[InstalledOwnedTargetDetail]:
        return self._installed_helpers().describe_owned_package_targets(
            self,
            repo_name,
            package_id,
            bound_profile,
        )

    def _effective_package_binding_keys(
        self,
        repo_name: str,
        package_id: str,
        bound_profile: str | None,
    ) -> set[tuple[str, str, str]]:
        return self._installed_helpers().effective_package_binding_keys(
            self,
            repo_name,
            package_id,
            bound_profile,
        )

    def _planning_helpers(self):
        from dotman import planning

        return planning

    def _build_plan(self, repo: Repository, binding: Binding, selector_kind: str, *, operation: str) -> BindingPlan:
        return self._planning_helpers().build_plan(
            self,
            repo,
            binding,
            selector_kind,
            operation=operation,
        )

    def _build_tracked_plans(
        self,
        *,
        operation: str,
        bindings_by_repo: dict[str, list[Binding]] | None = None,
    ) -> list[BindingPlan]:
        return self._planning_helpers().build_tracked_plans(
            self,
            operation=operation,
            bindings_by_repo=bindings_by_repo,
        )

    def _collect_tracked_candidates(
        self,
        *,
        operation: str,
        bindings_by_repo: dict[str, list[Binding]] | None = None,
    ) -> tuple[list[BindingPlan], dict[Path, list[TrackedTargetCandidate]]]:
        return self._planning_helpers().collect_tracked_candidates(
            self,
            operation=operation,
            bindings_by_repo=bindings_by_repo,
        )

    def preview_binding_implicit_overrides(self, binding: Binding) -> list[TrackedTargetOverride]:
        return self._planning_helpers().preview_binding_implicit_overrides(self, binding)

    def _tracked_target_signature(self, target: Any) -> tuple[Any, ...]:
        return self._planning_helpers().tracked_target_signature(target)

    def _resolve_tracked_target_winners(
        self,
        candidates_by_live_path: dict[Path, list[TrackedTargetCandidate]],
    ) -> set[tuple[int, int]]:
        return self._planning_helpers().resolve_tracked_target_winners(candidates_by_live_path)

    def _plan_hooks(
        self,
        repo: Repository,
        packages: list[PackageSpec],
        context: dict[str, Any],
        operation: str | None = None,
    ) -> dict[str, list[Any]]:
        return self._planning_helpers().plan_hooks(repo, packages, context, operation=operation)

    def _plan_targets(
        self,
        *,
        repo: Repository,
        packages: list[PackageSpec],
        context: dict[str, Any],
        binding: Binding,
        operation: str,
        inferred_os: str,
    ) -> list[Any]:
        return self._planning_helpers().plan_targets(
            self,
            repo=repo,
            packages=packages,
            context=context,
            binding=binding,
            operation=operation,
            inferred_os=inferred_os,
        )

    def _validate_target_collisions(self, rendered_targets: list[Any]) -> None:
        self._planning_helpers().validate_target_collisions(rendered_targets)

    def _validate_reserved_path_conflicts(
        self,
        packages: list[PackageSpec],
        rendered_targets: list[Any],
        context: dict[str, Any],
    ) -> None:
        self._planning_helpers().validate_reserved_path_conflicts(
            self,
            packages,
            rendered_targets,
            context,
        )

    def _paths_conflict(self, left: Path, right: Path) -> bool:
        return self._planning_helpers().paths_conflict(left, right)

    def _project_repo_file(
        self,
        *,
        repo: Repository,
        package: PackageSpec,
        target: Any,
        repo_path: Path,
        live_path: Path,
        render_command: str | None,
        context: dict[str, Any],
        binding: Binding,
        operation: str,
        inferred_os: str,
    ) -> tuple[bytes, str]:
        return self._planning_helpers().project_repo_file(
            self,
            repo=repo,
            package=package,
            target=target,
            repo_path=repo_path,
            live_path=live_path,
            render_command=render_command,
            context=context,
            binding=binding,
            operation=operation,
            inferred_os=inferred_os,
        )

    def _plan_directory_action(
        self,
        repo_path: Path,
        live_path: Path,
        push_ignore: tuple[str, ...],
        pull_ignore: tuple[str, ...],
        *,
        operation: str,
    ) -> tuple[str, tuple[Any, ...]]:
        return self._planning_helpers().plan_directory_action(
            repo_path,
            live_path,
            push_ignore,
            pull_ignore,
            operation=operation,
        )

    def _plan_file_action(
        self,
        *,
        repo: Repository,
        package: PackageSpec,
        target: Any,
        repo_path: Path,
        live_path: Path,
        desired_bytes: bytes | None,
        render_command: str | None,
        capture_command: str | None,
        context: dict[str, Any],
        binding: Binding,
        operation: str,
        inferred_os: str,
        pull_view_repo: str,
        pull_view_live: str,
    ) -> str:
        return self._planning_helpers().plan_file_action(
            self,
            repo=repo,
            package=package,
            target=target,
            repo_path=repo_path,
            live_path=live_path,
            desired_bytes=desired_bytes,
            render_command=render_command,
            capture_command=capture_command,
            context=context,
            binding=binding,
            operation=operation,
            inferred_os=inferred_os,
            pull_view_repo=pull_view_repo,
            pull_view_live=pull_view_live,
        )

    def _build_file_review_bytes(
        self,
        *,
        repo: Repository,
        package: PackageSpec,
        target: Any,
        repo_path: Path,
        live_path: Path,
        desired_bytes: bytes | None,
        render_command: str | None,
        capture_command: str | None,
        context: dict[str, Any],
        binding: Binding,
        operation: str,
        inferred_os: str,
        pull_view_repo: str,
        pull_view_live: str,
    ) -> tuple[bytes | None, bytes | None]:
        return self._planning_helpers().build_file_review_bytes(
            self,
            repo=repo,
            package=package,
            target=target,
            repo_path=repo_path,
            live_path=live_path,
            desired_bytes=desired_bytes,
            render_command=render_command,
            capture_command=capture_command,
            context=context,
            binding=binding,
            operation=operation,
            inferred_os=inferred_os,
            pull_view_repo=pull_view_repo,
            pull_view_live=pull_view_live,
        )

    def _pull_view_bytes(
        self,
        *,
        repo: Repository,
        package: PackageSpec,
        target: Any,
        repo_path: Path,
        live_path: Path,
        view: str,
        repo_side: bool,
        render_command: str | None,
        capture_command: str | None,
        context: dict[str, Any],
        binding: Binding,
        operation: str,
        inferred_os: str,
    ) -> bytes:
        return self._planning_helpers().pull_view_bytes(
            self,
            repo=repo,
            package=package,
            target=target,
            repo_path=repo_path,
            live_path=live_path,
            view=view,
            repo_side=repo_side,
            render_command=render_command,
            capture_command=capture_command,
            context=context,
            binding=binding,
            operation=operation,
            inferred_os=inferred_os,
        )

    def _run_command_projection(
        self,
        *,
        repo: Repository,
        package: PackageSpec,
        target: Any,
        repo_path: Path,
        live_path: Path,
        command: str,
        binding: Binding,
        operation: str,
        inferred_os: str,
        context: dict[str, Any],
    ) -> bytes:
        return self._planning_helpers().run_command_projection(
            self,
            repo=repo,
            package=package,
            target=target,
            repo_path=repo_path,
            live_path=live_path,
            command=command,
            binding=binding,
            operation=operation,
            inferred_os=inferred_os,
            context=context,
        )

    def _build_target_command_env(
        self,
        *,
        repo: Repository,
        package: PackageSpec,
        target: Any,
        repo_path: Path,
        live_path: Path,
        binding: Binding,
        operation: str,
        inferred_os: str,
        context: dict[str, Any],
    ) -> dict[str, str]:
        return self._planning_helpers().build_target_command_env(
            repo=repo,
            package=package,
            target=target,
            repo_path=repo_path,
            live_path=live_path,
            binding=binding,
            operation=operation,
            inferred_os=inferred_os,
            context=context,
        )


__all__ = [
    "DotmanEngine",
    "compute_profile_heights",
    "rank_profiles",
]
