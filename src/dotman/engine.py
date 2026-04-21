from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from dotman.config import load_manager_config
from dotman.ignore import list_directory_files, matches_ignore_pattern
from dotman.models import (
    FullSpecSelector,
    PackagePlan,
    ResolvedPackageIdentity,
    ResolvedSelector,
    ResolvedPackageSelection,
    SelectorKind,
    TrackableGroupDetail,
    TrackablePackageDetail,
    TrackableCatalogEntry,
    SearchMatch,
    TrackedPackageEntry,
    TrackedPackageEntrySummary,
    TrackedPackageEntryDetail,
    TrackedPackageDetail,
    TrackedPackageSummary,
    TrackedOwnedTargetDetail,
    TrackedTargetSummary,
    ManagerConfig,
    OperationPlan,
    PackageSpec,
    TrackedPackageEntryIssue,
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
    PersistedTrackedPackageEntryRecord,
    TrackedStateSummary,
)
from dotman import tracked_packages, tracking, variable_inspection

def parse_full_spec_selector_text(binding_text: str) -> tuple[str | None, str, str | None]:
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
    repo_name, selector, profile = parse_full_spec_selector_text(package_text)
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


def _search_match_reason(
    *,
    query_lower: str,
    selector_lower: str,
    qualified_selector_lower: str,
    slash_qualified_selector_lower: str,
    description_lower: str | None,
) -> tuple[str, int] | None:
    if query_lower == qualified_selector_lower or query_lower == slash_qualified_selector_lower:
        return "exact_repo_qualified_selector", 0
    if query_lower == selector_lower:
        return "exact_selector", 1
    if selector_lower.startswith(query_lower):
        return "prefix_selector", 2
    if query_lower in selector_lower:
        return "substring_selector", 3
    if description_lower is not None and query_lower in description_lower:
        return "substring_description", 4
    return None


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

    def resolve_selector_text(self, query_text: str) -> tuple[Repository, ResolvedSelector]:
        explicit_repo, selector, selector_profile = parse_full_spec_selector_text(query_text)
        del selector_profile
        repo, resolved_selector, selector_kind = self.resolve_selector(selector, explicit_repo)
        return repo, ResolvedSelector(
            repo=repo.config.name,
            selector=resolved_selector,
            selector_kind=selector_kind,
        )

    def resolve_full_spec_selector_text(self, query_text: str, *, profile: str | None = None) -> tuple[Repository, FullSpecSelector]:
        explicit_repo, selector, selector_profile = parse_full_spec_selector_text(query_text)
        repo, resolved_selector, selector_kind = self.resolve_selector(selector, explicit_repo)
        resolved_profile = profile or selector_profile
        if not resolved_profile:
            raise ValueError("profile is required in non-interactive mode")
        return repo, FullSpecSelector(
            repo=repo.config.name,
            selector=resolved_selector,
            selector_kind=selector_kind,
            profile=resolved_profile,
        )

    def search_selectors(self, query_text: str) -> list[SearchMatch]:
        query = query_text.strip()
        if not query:
            raise ValueError("search query must not be empty")

        query_lower = query.lower()
        ranked_matches: list[tuple[tuple[int, int, int, int, str], SearchMatch]] = []

        for repo, trackable in tracking.iter_trackable_catalog_entries(self):
            selector = trackable.selector
            selector_lower = selector.lower()
            qualified_selector = trackable.qualified_selector
            qualified_selector_lower = qualified_selector.lower()
            slash_qualified_selector_lower = f"{repo.config.name}/{selector}".lower()
            description = trackable.description
            description_lower = description.lower() if isinstance(description, str) else None

            match = _search_match_reason(
                query_lower=query_lower,
                selector_lower=selector_lower,
                qualified_selector_lower=qualified_selector_lower,
                slash_qualified_selector_lower=slash_qualified_selector_lower,
                description_lower=description_lower,
            )
            if match is None:
                continue

            match_reason, tier = match
            ranked_matches.append(
                (
                    (tier, repo.config.order, 0 if trackable.kind == "package" else 1, len(selector), qualified_selector_lower),
                    SearchMatch(
                        kind=trackable.kind,
                        repo=repo.config.name,
                        selector=selector,
                        qualified_selector=qualified_selector,
                        description=description,
                        binding_mode=trackable.binding_mode,
                        member_count=trackable.member_count,
                        match_reason=match_reason,
                        rank=0,
                    ),
                )
            )

        ranked_matches.sort(key=lambda item: item[0])
        return [replace(match, rank=index + 1) for index, (_sort_key, match) in enumerate(ranked_matches)]

    def list_trackables(self) -> list[TrackableCatalogEntry]:
        return tracking.list_trackables(self)

    def _resolved_package_identity(self, repo: Repository, package_id: str, requested_profile: str) -> ResolvedPackageIdentity:
        return ResolvedPackageIdentity(
            repo=repo.config.name,
            package_id=package_id,
            bound_profile=self._bound_profile_for_package(repo, package_id, requested_profile),
        )

    def _resolved_package_selection(
        self,
        *,
        repo: Repository,
        package_id: str,
        requested_profile: str,
        explicit: bool,
        source_kind: str,
        source_selector: str | None = None,
        owner_identity: ResolvedPackageIdentity | None = None,
    ) -> ResolvedPackageSelection:
        return ResolvedPackageSelection(
            identity=self._resolved_package_identity(repo, package_id, requested_profile),
            requested_profile=requested_profile,
            explicit=explicit,
            source_kind=source_kind,
            source_selector=source_selector,
            owner_identity=owner_identity,
        )

    def _tracked_entry_from_package_entry(self, package_entry: FullSpecSelector) -> TrackedPackageEntry:
        return TrackedPackageEntry(repo=package_entry.repo, package_id=package_entry.selector, profile=package_entry.profile)

    def _tracked_entries_by_repo_from_bindings(
        self,
        bindings_by_repo: dict[str, list[Any]] | None,
    ) -> dict[str, list[TrackedPackageEntry]] | None:
        if bindings_by_repo is None:
            return None
        return {
            repo_name: [
                entry if isinstance(entry, TrackedPackageEntry) else self._tracked_entry_from_package_entry(entry)
                for entry in entries
            ]
            for repo_name, entries in bindings_by_repo.items()
        }

    def plan_push_query(self, query_text: str, *, profile: str | None = None) -> OperationPlan:
        _repo, query = self.resolve_full_spec_selector_text(query_text, profile=profile)
        selections = self._planning_helpers().resolve_full_spec_selector(self, query, operation="push")
        plans = [
            self._build_package_plan(self.get_repo(selection.identity.repo), selection, operation="push")
            for selection in selections
        ]
        return self._build_operation_plan(plans, operation="push")

    def plan_pull_query(self, query_text: str, *, profile: str | None = None) -> OperationPlan:
        _repo, query = self.resolve_full_spec_selector_text(query_text, profile=profile)
        selections = self._planning_helpers().resolve_full_spec_selector(self, query, operation="pull")
        plans = [
            self._build_package_plan(self.get_repo(selection.identity.repo), selection, operation="pull")
            for selection in selections
        ]
        return self._build_operation_plan(plans, operation="pull")

    def resolve_tracked_binding(
        self,
        binding_text: str,
        *,
        operation: str = "untrack",
        allow_package_owners: bool = False,
    ) -> tuple[Repository, FullSpecSelector]:
        explicit_repo, _parsed_selector, _parsed_profile = parse_full_spec_selector_text(binding_text)
        selector, profile, exact_matches, partial_matches, owner_package_entries = self.find_tracked_package_entry_matches(binding_text)
        binding_label = selector if profile is None else f"{selector}@{profile}"
        if len(exact_matches) == 1:
            return exact_matches[0]
        if len(exact_matches) > 1:
            candidates = ", ".join(
                f"{repo.config.name}:{binding.selector}@{binding.profile}"
                for repo, binding in exact_matches
            )
            raise ValueError(f"tracked package entry '{binding_label}' is ambiguous: {candidates}")

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
            raise ValueError(f"tracked package entry '{binding_label}' is ambiguous: {candidates}")

        if owner_package_entries:
            if allow_package_owners:
                if len(owner_package_entries) == 1:
                    owner_repo, owner_binding = owner_package_entries[0]
                    return owner_repo, FullSpecSelector(
                        repo=owner_repo.config.name,
                        selector=selector,
                        selector_kind="package",
                        profile=owner_binding.profile,
                    )
                candidates = ", ".join(
                    f"{repo.config.name}:{binding.selector}@{binding.profile}"
                    for repo, binding in owner_package_entries
                )
                raise ValueError(f"{operation} target '{binding_label}' is ambiguous across tracked package entries: {candidates}")
            owners = ", ".join(
                f"{repo.config.name}:{binding.selector}@{binding.profile}"
                for repo, binding in owner_package_entries
            )
            required_repo = explicit_repo or owner_package_entries[0][0].config.name
            required_ref = f"{required_repo}:{selector}"
            raise ValueError(
                f"cannot {operation} '{required_ref}': required by tracked package entries: {owners}"
            )

        raise ValueError(f"tracked package entry '{binding_label}' is not currently tracked")

    def find_tracked_package_entry_matches(
        self,
        binding_text: str,
    ) -> tuple[str, str | None, list[tuple[Repository, FullSpecSelector]], list[tuple[Repository, FullSpecSelector]], list[tuple[Repository, FullSpecSelector]]]:
        explicit_repo, selector, profile = parse_full_spec_selector_text(binding_text)
        candidate_repos = self.candidate_repos(explicit_repo)
        tracked = [
            (repo, binding)
            for repo in candidate_repos
            for binding in self.read_effective_tracked_package_entries(repo)
            if profile is None or binding.profile == profile
        ]

        exact_matches = [(repo, binding) for repo, binding in tracked if binding.selector == selector]
        partial_matches = [(repo, binding) for repo, binding in tracked if selector in binding.selector]
        unique_partials = {
            (repo.config.name, binding.selector, binding.profile): (repo, binding)
            for repo, binding in partial_matches
        }
        owner_package_entries = self._find_tracked_package_owners(candidate_repos, selector, profile)
        unique_owners = {
            (repo.config.name, binding.selector, binding.profile): (repo, binding)
            for repo, binding in owner_package_entries
        }
        return selector, profile, exact_matches, list(unique_partials.values()), list(unique_owners.values())

    def plan_push(self) -> OperationPlan:
        return self._build_tracked_plans(operation="push")

    def plan_pull(self) -> OperationPlan:
        return self._build_tracked_plans(operation="pull")

    def _tracking_helpers(self):
        return tracking

    def list_tracked_state(self) -> TrackedStateSummary:
        return self._tracking_helpers().list_tracked_state(self)

    def list_invalid_explicit_package_entries(
        self,
        *,
        bindings_by_repo: dict[str, list[FullSpecSelector]] | None = None,
    ) -> list[TrackedPackageEntryIssue]:
        return self._tracking_helpers().list_invalid_explicit_package_entries(
            self,
            bindings_by_repo=bindings_by_repo,
        )

    def list_orphan_explicit_package_entries(self) -> list[TrackedPackageEntryIssue]:
        return self._tracking_helpers().list_orphan_explicit_package_entries(self)

    def list_tracked_packages(self) -> list[TrackedPackageSummary]:
        return self._tracking_helpers().list_tracked_packages(self)

    def describe_trackable(self, *, repo_name: str, selector: str, selector_kind: SelectorKind) -> TrackablePackageDetail | TrackableGroupDetail:
        return self._tracking_helpers().describe_trackable(
            self,
            repo_name=repo_name,
            selector=selector,
            selector_kind=selector_kind,
        )

    def describe_tracked_package(self, package_text: str) -> TrackedPackageDetail:
        return self._tracking_helpers().describe_tracked_package(self, package_text)

    def list_variables(self) -> list[Any]:
        return variable_inspection.list_winning_variables(self)

    def describe_variable(self, variable_text: str) -> Any:
        return variable_inspection.describe_resolved_variable(self, variable_text)

    def find_variable_matches(self, variable_text: str) -> tuple[list[str], list[str]]:
        return variable_inspection.find_variable_matches(self, variable_text)

    def _read_tracked_package_entries_file(self, state_path: Path) -> list[FullSpecSelector]:
        return self._tracking_helpers().read_tracked_package_entries_file(state_path)

    def read_tracked_package_entries(self, repo: Repository) -> list[FullSpecSelector]:
        return self._tracking_helpers().read_tracked_package_entries(self, repo)

    def read_effective_tracked_package_entries(self, repo: Repository) -> list[FullSpecSelector]:
        return self._tracking_helpers().read_effective_tracked_package_entries(self, repo)

    def expand_tracked_package_entry(self, binding: FullSpecSelector) -> list[FullSpecSelector]:
        return self._tracking_helpers().expand_tracked_package_entry(self, binding)

    def _raw_tracked_package_entries_by_repo(self) -> dict[str, list[FullSpecSelector]]:
        return self._tracking_helpers().raw_tracked_package_entries_by_repo(self)

    def _effective_tracked_package_entries_by_repo(
        self,
        raw_tracked_package_entries_by_repo: dict[str, list[FullSpecSelector]] | None = None,
    ) -> dict[str, list[FullSpecSelector]]:
        return self._tracking_helpers().effective_tracked_package_entries_by_repo(
            self,
            raw_tracked_package_entries_by_repo=raw_tracked_package_entries_by_repo,
        )

    def _tracked_package_entry_scope_key(self, repo: Repository, binding: FullSpecSelector) -> tuple[str, str, str | None]:
        return self._tracking_helpers().tracked_package_entry_scope_key(self, repo, binding)

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

    def _normalize_tracked_package_entries(self, bindings: list[FullSpecSelector], package_entry: FullSpecSelector) -> list[FullSpecSelector]:
        return self._tracking_helpers().normalize_tracked_package_entries(self, bindings, package_entry)

    def _normalize_tracked_package_entry_set(self, bindings: list[FullSpecSelector], additions: list[FullSpecSelector]) -> list[FullSpecSelector]:
        return self._tracking_helpers().normalize_tracked_package_entry_set(self, bindings, additions)

    def _expand_tracked_package_entry(self, repo: Repository, binding: FullSpecSelector) -> list[FullSpecSelector]:
        return self._tracking_helpers().expand_tracked_package_entry_in_repo(repo, binding)

    def _effective_tracked_package_entries_for_repo(self, repo: Repository, raw_bindings: list[FullSpecSelector]) -> list[FullSpecSelector]:
        return self._tracking_helpers().effective_tracked_package_entries_for_repo(self, repo, raw_bindings)

    def _validate_tracked_package_entries(self, bindings_by_repo: dict[str, list[FullSpecSelector]]) -> None:
        self._tracking_helpers().validate_tracked_package_entries(self, bindings_by_repo)

    def record_tracked_package_entry(self, binding: FullSpecSelector) -> None:
        self._tracking_helpers().record_tracked_package_entry(self, binding)

    def validate_tracked_package_entry(self, binding: FullSpecSelector) -> None:
        self._tracking_helpers().validate_tracked_package_entry(self, binding)

    def find_persisted_tracked_package_entry_matches(
        self,
        binding_text: str,
    ) -> tuple[str, str | None, list[PersistedTrackedPackageEntryRecord], list[PersistedTrackedPackageEntryRecord]]:
        return self._tracking_helpers().find_persisted_tracked_package_entry_matches(
            self,
            binding_text,
            parse_full_spec_selector_text=parse_full_spec_selector_text,
        )

    def remove_tracked_package_entry(self, binding_text: str, *, operation: str = "untrack") -> FullSpecSelector:
        return self._tracking_helpers().remove_tracked_package_entry(
            self,
            binding_text,
            operation=operation,
            parse_full_spec_selector_text=parse_full_spec_selector_text,
        )

    def _find_tracked_package_owners(
        self,
        candidate_repos: list[Repository],
        selector: str,
        profile: str | None,
    ) -> list[tuple[Repository, FullSpecSelector]]:
        return self._tracking_helpers().find_tracked_package_owners(
            self,
            candidate_repos,
            selector,
            profile,
        )

    def write_tracked_package_entries(self, repo: Repository, bindings: list[FullSpecSelector]) -> None:
        self._tracking_helpers().write_tracked_package_entries(self, repo, bindings)

    def _write_tracked_package_entries_file(self, state_dir: Path, bindings: list[FullSpecSelector]) -> None:
        self._tracking_helpers().write_tracked_package_entries_file(state_dir, bindings)

    def remove_persisted_tracked_package_entry(self, record: PersistedTrackedPackageEntryRecord, *, operation: str = "untrack") -> FullSpecSelector:
        return self._tracking_helpers().remove_persisted_tracked_package_entry(
            self,
            record,
            operation=operation,
            tracked_target_conflict_error=TrackedTargetConflictError,
        )

    def _remove_tracked_package_entry_record(self, bindings: list[FullSpecSelector], target: FullSpecSelector) -> list[FullSpecSelector]:
        return self._tracking_helpers().remove_tracked_package_entry_record(bindings, target)

    def _iter_tracked_package_entries(self) -> list[tuple[Repository, FullSpecSelector, str, list[str]]]:
        return self._tracking_helpers().iter_tracked_package_entries(self)

    def _configured_persisted_tracked_package_entry_records(
        self,
        *,
        bindings_by_repo: dict[str, list[FullSpecSelector]] | None = None,
    ) -> tuple[list[PersistedTrackedPackageEntryRecord], list[PersistedTrackedPackageEntryRecord]]:
        return self._tracking_helpers().configured_persisted_tracked_package_entry_records(
            self,
            bindings_by_repo=bindings_by_repo,
        )

    def _orphan_persisted_tracked_package_entry_records(self) -> list[PersistedTrackedPackageEntryRecord]:
        return self._tracking_helpers().orphan_persisted_tracked_package_entry_records(self)

    def _all_persisted_tracked_package_entry_records(self) -> list[PersistedTrackedPackageEntryRecord]:
        return self._tracking_helpers().all_persisted_tracked_package_entry_records(self)

    def _resolve_persisted_tracked_package_entry(self, repo: Repository, binding: FullSpecSelector) -> list[FullSpecSelector]:
        return self._tracking_helpers().resolve_persisted_tracked_package_entry(self, repo, binding)

    def _tracked_package_matches_for_untrack(
        self,
        *,
        selector: str,
        profile: str | None,
        repo_name: str | None,
    ) -> tuple[list[TrackedPackageSummary], list[TrackedPackageEntrySummary]]:
        return self._tracking_helpers().tracked_package_matches_for_untrack(
            self,
            selector=selector,
            profile=profile,
            repo_name=repo_name,
        )

    def _sorted_tracked_package_entry_issues(self, issues: list[TrackedPackageEntryIssue]) -> list[TrackedPackageEntryIssue]:
        return self._tracking_helpers().sorted_tracked_package_entry_issues(issues)

    def _format_persisted_tracked_package_entry_candidates(self, records: list[PersistedTrackedPackageEntryRecord]) -> str:
        return self._tracking_helpers().format_persisted_tracked_package_entry_candidates(records)

    def _format_tracked_package_candidates(self, packages: list[TrackedPackageSummary]) -> str:
        return self._tracking_helpers().format_tracked_package_candidates(packages)

    def _format_owner_package_entries(self, package_entries: list[TrackedPackageEntrySummary]) -> str:
        return self._tracking_helpers().format_owner_package_entries(package_entries)

    def _selected_package_ids(self, repo: Repository, selector: str, selector_kind: SelectorKind) -> list[str]:
        return [selector] if selector_kind == "package" else repo.expand_group(selector)

    def _tracked_package_helpers(self):
        return tracked_packages

    def _resolve_tracked_package(self, package_text: str) -> tuple[Repository, str, str | None]:
        return self._tracked_package_helpers().resolve_tracked_package(self, package_text)

    def find_tracked_package_matches(
        self,
        package_text: str,
    ) -> tuple[str, str | None, list[tuple[Repository, str, str | None]], list[tuple[Repository, str, str | None]]]:
        return self._tracked_package_helpers().find_tracked_package_matches(
            self,
            package_text,
            parse_package_ref_text=parse_package_ref_text,
        )

    def find_tracked_target_matches(self, target_text: str) -> tuple[str, list[Any], list[Any]]:
        return self._tracked_package_helpers().find_tracked_target_matches(
            self,
            target_text,
            parse_full_spec_selector_text=parse_full_spec_selector_text,
            parse_package_ref_text=parse_package_ref_text,
        )

    def _describe_tracked_package_entry(
        self,
        repo: Repository,
        package_entry: FullSpecSelector,
        selector_kind: SelectorKind,
        package_id: str,
        package_ids: list[str],
        *,
        executable: bool,
    ) -> TrackedPackageEntryDetail:
        return self._tracked_package_helpers().describe_tracked_package_entry(
            self,
            repo,
            package_entry,
            selector_kind,
            package_id,
            package_ids,
            executable=executable,
        )

    def _resolve_package_ids(self, repo: Repository, selector: str, selector_kind: SelectorKind) -> list[str]:
        roots = self._selected_package_ids(repo, selector, selector_kind)
        ordered: list[str] = []
        seen_packages: set[str] = set()
        completed_nodes: set[tuple[str, str]] = set()

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
                # Dependency graphs may be cyclic. Treat active-node revisits as back-edges
                # and stop descending so resolution stays finite while still collecting each
                # reachable package exactly once.
                return
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
    ) -> list[TrackedTargetSummary]:
        return self._tracked_package_helpers().summarize_targets(repo, package, context)

    def _tracked_target_summary_from_plan(self, target: Any) -> TrackedTargetSummary:
        return self._tracked_package_helpers().tracked_target_summary_from_plan(target)

    def _describe_owned_package_targets(
        self,
        repo_name: str,
        package_id: str,
        bound_profile: str | None,
    ) -> list[TrackedOwnedTargetDetail]:
        return self._tracked_package_helpers().describe_owned_package_targets(
            self,
            repo_name,
            package_id,
            bound_profile,
        )

    def _effective_tracked_package_entry_keys(
        self,
        repo_name: str,
        package_id: str,
        bound_profile: str | None,
    ) -> set[tuple[str, str, str]]:
        return self._tracked_package_helpers().effective_tracked_package_entry_keys(
            self,
            repo_name,
            package_id,
            bound_profile,
        )

    def _planning_helpers(self):
        from dotman import planning

        return planning

    def _build_package_plan(self, repo: Repository, selection: ResolvedPackageSelection, *, operation: str) -> PackagePlan:
        return self._planning_helpers().build_package_plan(
            self,
            repo,
            selection,
            operation=operation,
        )

    def _build_tracked_plans(
        self,
        *,
        operation: str,
        bindings_by_repo: dict[str, list[FullSpecSelector]] | None = None,
    ) -> OperationPlan:
        return self._planning_helpers().build_tracked_plans(
            self,
            operation=operation,
            entries_by_repo=self._tracked_entries_by_repo_from_bindings(bindings_by_repo),
        )

    def _build_operation_plan(self, plans: list[PackagePlan], *, operation: str, allow_standalone_noop_hooks: bool = False, excluded_repo_names: set[str] | None = None) -> OperationPlan:
        return self._planning_helpers().build_operation_plan(
            plans,
            repo_by_name={repo_config.name: self.get_repo(repo_config.name) for repo_config in self.config.ordered_repos},
            operation=operation,
            allow_standalone_noop_hooks=allow_standalone_noop_hooks,
            excluded_repo_names=excluded_repo_names,
        )

    def _collect_tracked_candidates(
        self,
        *,
        operation: str,
        bindings_by_repo: dict[str, list[FullSpecSelector]] | None = None,
        entries_by_repo: dict[str, list[TrackedPackageEntry]] | None = None,
    ) -> tuple[list[PackagePlan], dict[Path, list[TrackedTargetCandidate]]]:
        return self._planning_helpers().collect_tracked_candidates(
            self,
            operation=operation,
            entries_by_repo=entries_by_repo or self._tracked_entries_by_repo_from_bindings(bindings_by_repo),
        )

    def preview_package_selection_implicit_overrides(self, selection: ResolvedPackageSelection) -> list[TrackedTargetOverride]:
        return self._planning_helpers().preview_package_selection_implicit_overrides(self, selection)

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
        *,
        selection: ResolvedPackageSelection,
        operation: str | None = None,
        inferred_os: str,
        variables: dict[str, Any],
        target_plans: list[Any],
    ) -> dict[str, list[Any]]:
        return self._planning_helpers().plan_hooks(
            repo,
            packages,
            context,
            selection=selection,
            operation=operation,
            inferred_os=inferred_os,
            variables=variables,
            target_plans=target_plans,
        )

    def _plan_targets(
        self,
        *,
        repo: Repository,
        packages: list[PackageSpec],
        context: dict[str, Any],
        selection: ResolvedPackageSelection,
        operation: str,
        inferred_os: str,
        declaration_package_ids: set[str],
    ) -> list[Any]:
        return self._planning_helpers().plan_targets(
            self,
            repo=repo,
            packages=packages,
            context=context,
            selection=selection,
            operation=operation,
            inferred_os=inferred_os,
            declaration_package_ids=declaration_package_ids,
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
        selection: ResolvedPackageSelection,
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
            selection=selection,
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
        selection: ResolvedPackageSelection,
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
            selection=selection,
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
        selection: ResolvedPackageSelection,
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
            selection=selection,
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
        selection: ResolvedPackageSelection,
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
            selection=selection,
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
        selection: ResolvedPackageSelection,
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
            selection=selection,
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
        selection: ResolvedPackageSelection,
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
            selection=selection,
            operation=operation,
            inferred_os=inferred_os,
            context=context,
        )


__all__ = [
    "DotmanEngine",
    "compute_profile_heights",
    "rank_profiles",
]
