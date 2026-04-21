from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotman.config import default_state_root
from dotman.toml_utils import load_toml_file
from dotman.models import (
    FullSpecSelector,
    TrackableCatalogEntry,
    TrackableGroupDetail,
    TrackableGroupMemberDetail,
    TrackablePackageDetail,
    TrackableTargetDetail,
    TrackedPackageEntry,
    TrackedPackageEntrySummary,
    TrackedPackageEntryDetail,
    TrackedPackageDetail,
    TrackedPackageSummary,
    TrackedPackageEntryIssue,
    package_ref_text,
)
from dotman.repository import Repository


TRACKED_PACKAGES_FILE_NAME = "tracked-packages.toml"
TRACKED_PACKAGES_SCHEMA_VERSION = 1


class PersistedTrackedPackageEntryResolutionError(ValueError):
    def __init__(self, *, reason: str, message: str) -> None:
        self.reason = reason
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class PersistedTrackedPackageEntryRecord:
    state_key: str
    state_dir: Path
    package_entry: FullSpecSelector
    repo: Repository | None = None
    selector_kind: str | None = None
    package_ids: tuple[str, ...] = ()
    issue: TrackedPackageEntryIssue | None = None


@dataclass(frozen=True)
class TrackedStateSummary:
    packages: list[TrackedPackageSummary]
    invalid_package_entries: list[TrackedPackageEntryIssue]


def iter_trackable_catalog_entries(engine: Any):
    for repo in engine.candidate_repos():
        for package_id, package in sorted(repo.packages.items()):
            yield repo, TrackableCatalogEntry(
                kind="package",
                repo=repo.config.name,
                selector=package_id,
                description=package.description,
                binding_mode=package.binding_mode,
            )
        for group_id, group in sorted(repo.groups.items()):
            yield repo, TrackableCatalogEntry(
                kind="group",
                repo=repo.config.name,
                selector=group_id,
                description=group.description,
                member_count=len(group.members),
            )


def list_trackables(engine: Any) -> list[TrackableCatalogEntry]:
    return [trackable for _repo, trackable in iter_trackable_catalog_entries(engine)]



def list_tracked_state(engine: Any) -> TrackedStateSummary:
    return TrackedStateSummary(
        packages=engine.list_tracked_packages(),
        invalid_package_entries=engine._sorted_tracked_package_entry_issues(
            [
                *engine.list_invalid_explicit_package_entries(),
                *engine.list_orphan_explicit_package_entries(),
            ]
        ),
    )



def list_invalid_explicit_package_entries(
    engine: Any,
    *,
    bindings_by_repo: dict[str, list[FullSpecSelector]] | None = None,
) -> list[TrackedPackageEntryIssue]:
    _valid_records, invalid_records = engine._configured_persisted_tracked_package_entry_records(bindings_by_repo=bindings_by_repo)
    return engine._sorted_tracked_package_entry_issues([record.issue for record in invalid_records if record.issue is not None])



def list_orphan_explicit_package_entries(engine: Any) -> list[TrackedPackageEntryIssue]:
    return engine._sorted_tracked_package_entry_issues(
        [record.issue for record in engine._orphan_persisted_tracked_package_entry_records() if record.issue is not None]
    )



def list_tracked_packages(engine: Any) -> list[TrackedPackageSummary]:
    tracked_packages: dict[tuple[str, str, str | None], TrackedPackageSummary] = {}
    package_states: dict[tuple[str, str, str | None], str] = {}
    for repo, binding, selector_kind, package_ids in engine._iter_tracked_package_entries():
        binding_summary = TrackedPackageEntrySummary(
            repo=repo.config.name,
            selector=binding.selector,
            profile=binding.profile,
            selector_kind=selector_kind,
        )
        for package_id in package_ids:
            package = repo.resolve_package(package_id)
            bound_profile = engine._bound_profile_for_package(repo, package_id, binding.profile)
            key = (repo.config.name, package_id, bound_profile)
            package_state = "explicit" if selector_kind == "package" and binding.selector == package_id else "implicit"
            existing = tracked_packages.get(key)
            if existing is None:
                tracked_packages[key] = TrackedPackageSummary(
                    repo=repo.config.name,
                    package_id=package_id,
                    description=package.description,
                    package_entries=[binding_summary],
                    state=package_state,
                    bound_profile=bound_profile,
                )
                package_states[key] = package_state
                continue
            if binding_summary not in existing.package_entries:
                existing.package_entries.append(binding_summary)
            if package_state == "explicit":
                package_states[key] = "explicit"

    return [
        TrackedPackageSummary(
            repo=summary.repo,
            package_id=summary.package_id,
            description=summary.description,
            package_entries=sorted(summary.package_entries, key=lambda item: (item.selector, item.profile, item.repo)),
            state=package_states[key],
            bound_profile=summary.bound_profile,
        )
        for key, summary in sorted(
            tracked_packages.items(),
            key=lambda item: (
                0 if package_states[item[0]] == "explicit" else 1,
                item[0][0],
                item[0][1],
                "" if item[0][2] is None else item[0][2],
            ),
        )
    ]


def _tracked_instances_for_package(engine: Any, *, repo_name: str, package_id: str) -> list[TrackedPackageSummary]:
    return [
        package
        for package in engine.list_tracked_packages()
        if package.repo == repo_name and package.package_id == package_id
    ]


def describe_trackable(engine: Any, *, repo_name: str, selector: str, selector_kind: str) -> Any:
    repo = engine.get_repo(repo_name)
    if selector_kind == "package":
        return describe_trackable_package(engine, repo=repo, package_id=selector)
    return describe_trackable_group(engine, repo=repo, group_id=selector)


def describe_trackable_package(engine: Any, *, repo: Repository, package_id: str) -> TrackablePackageDetail:
    package = repo.resolve_package(package_id)
    targets = [
        TrackableTargetDetail(
            target_name=target_name,
            source=target.source,
            path=target.path,
            render_command=target.render,
            capture_command=target.capture,
            reconcile_command=target.reconcile,
            reconcile_io=target.reconcile_io,
            pull_view_repo=target.pull_view_repo,
            pull_view_live=target.pull_view_live,
            push_ignore=target.push_ignore or (),
            pull_ignore=target.pull_ignore or (),
            chmod=target.chmod,
        )
        for target_name, target in sorted((package.targets or {}).items())
        if not target.disabled
    ]
    return TrackablePackageDetail(
        repo=repo.config.name,
        selector=package_id,
        description=package.description,
        binding_mode=package.binding_mode,
        tracked_instances=_tracked_instances_for_package(
            engine,
            repo_name=repo.config.name,
            package_id=package_id,
        ),
        targets=targets,
    )


def describe_trackable_group(engine: Any, *, repo: Repository, group_id: str) -> TrackableGroupDetail:
    return TrackableGroupDetail(
        repo=repo.config.name,
        selector=group_id,
        members=[
            TrackableGroupMemberDetail(
                package_id=package_id,
                tracked_instances=_tracked_instances_for_package(
                    engine,
                    repo_name=repo.config.name,
                    package_id=package_id,
                ),
            )
            for package_id in repo.expand_group(group_id)
        ],
    )


def describe_tracked_package(engine: Any, package_text: str) -> TrackedPackageDetail:
    repo, package_id, bound_profile = engine._resolve_tracked_package(package_text)
    effective_binding_keys = engine._effective_tracked_package_entry_keys(
        repo.config.name,
        package_id,
        bound_profile,
    )
    details: list[TrackedPackageEntryDetail] = []
    description = repo.resolve_package(package_id).description

    for candidate_repo, binding, selector_kind, package_ids in engine._iter_tracked_package_entries():
        if candidate_repo.config.name != repo.config.name or package_id not in package_ids:
            continue
        if engine._bound_profile_for_package(candidate_repo, package_id, binding.profile) != bound_profile:
            continue
        details.append(
            engine._describe_tracked_package_entry(
                candidate_repo,
                binding,
                selector_kind,
                package_id,
                package_ids,
                executable=(binding.repo, binding.selector, binding.profile) in effective_binding_keys,
            )
        )

    if not details:
        package_ref = package_ref_text(package_id=package_id, bound_profile=bound_profile)
        raise ValueError(f"package '{repo.config.name}:{package_ref}' is not currently tracked")

    resolved_package = repo.resolve_package(package_id)
    return TrackedPackageDetail(
        repo=repo.config.name,
        package_id=package_id,
        description=description,
        package_entries=sorted(
            details,
            key=lambda item: (
                item.package_entry.selector,
                item.package_entry.profile,
                item.package_entry.repo,
            ),
        ),
        owned_targets=engine._describe_owned_package_targets(
            repo.config.name,
            package_id,
            bound_profile,
        ),
        bound_profile=bound_profile,
    )


def tracked_packages_file_path(state_dir: Path) -> Path:
    return state_dir / TRACKED_PACKAGES_FILE_NAME

def read_tracked_packages_file(state_path: Path) -> list[TrackedPackageEntry]:
    if not state_path.exists():
        return []
    payload = load_toml_file(state_path, context="tracked packages file")
    schema_version = payload.get("schema_version")
    if schema_version != TRACKED_PACKAGES_SCHEMA_VERSION:
        raise ValueError(
            f"tracked packages file '{state_path}' must declare schema_version = {TRACKED_PACKAGES_SCHEMA_VERSION}"
        )
    packages_payload = payload.get("packages", [])
    tracked_packages: list[TrackedPackageEntry] = []
    for package_payload in packages_payload:
        tracked_packages.append(
            TrackedPackageEntry(
                repo=str(package_payload["repo"]),
                package_id=str(package_payload["package_id"]),
                profile=str(package_payload["profile"]),
            )
        )
    return tracked_packages


def read_tracked_package_entries_file(state_path: Path) -> list[FullSpecSelector]:
    return [
        FullSpecSelector(repo=entry.repo, selector=entry.package_id, selector_kind="package", profile=entry.profile)
        for entry in read_tracked_packages_file(state_path)
    ]



def read_tracked_package_entries(engine: Any, repo: Repository) -> list[FullSpecSelector]:
    return read_tracked_package_entries_file(tracked_packages_file_path(repo.config.state_path))



def read_effective_tracked_package_entries(engine: Any, repo: Repository) -> list[FullSpecSelector]:
    return engine._effective_tracked_package_entries_for_repo(repo, engine.read_tracked_package_entries(repo))



def expand_tracked_package_entry(engine: Any, binding: FullSpecSelector) -> list[FullSpecSelector]:
    repo = engine.get_repo(binding.repo)
    return engine._expand_tracked_package_entry(repo, binding)



def raw_tracked_package_entries_by_repo(engine: Any) -> dict[str, list[FullSpecSelector]]:
    return {
        repo_config.name: engine.read_tracked_package_entries(engine.get_repo(repo_config.name))
        for repo_config in engine.config.ordered_repos
    }



def effective_tracked_package_entries_by_repo(
    engine: Any,
    raw_tracked_package_entries_by_repo: dict[str, list[FullSpecSelector]] | None = None,
) -> dict[str, list[FullSpecSelector]]:
    current_raw_bindings = raw_tracked_package_entries_by_repo or engine._raw_tracked_package_entries_by_repo()
    return {
        repo_config.name: engine._effective_tracked_package_entries_for_repo(
            engine.get_repo(repo_config.name),
            current_raw_bindings.get(repo_config.name, []),
        )
        for repo_config in engine.config.ordered_repos
    }



def tracked_package_entry_scope_key(engine: Any, repo: Repository, binding: FullSpecSelector) -> tuple[str, str, str | None]:
    if binding.selector in repo.packages and repo.package_binding_mode(binding.selector) == "multi_instance":
        return (binding.repo, binding.selector, binding.profile)
    return (binding.repo, binding.selector, None)



def bound_profile_for_package(
    engine: Any,
    repo: Repository,
    package_id: str,
    binding_profile: str,
) -> str | None:
    if repo.package_binding_mode(package_id) == "multi_instance":
        return binding_profile
    return None



def normalize_tracked_package_entries(engine: Any, bindings: list[FullSpecSelector], package_entry: FullSpecSelector) -> list[FullSpecSelector]:
    repo = engine.get_repo(package_entry.repo)
    target_scope = engine._tracked_package_entry_scope_key(repo, package_entry)
    updated = False
    normalized: list[FullSpecSelector] = []
    for existing in bindings:
        if engine._tracked_package_entry_scope_key(repo, existing) == target_scope:
            if not updated:
                normalized.append(package_entry)
                updated = True
            continue
        normalized.append(existing)
    if not updated:
        normalized.append(package_entry)
    return normalized



def normalize_tracked_package_entry_set(engine: Any, bindings: list[FullSpecSelector], additions: list[FullSpecSelector]) -> list[FullSpecSelector]:
    normalized = list(bindings)
    for binding in additions:
        normalized = engine._normalize_tracked_package_entries(normalized, binding)
    return normalized



def expand_tracked_package_entry_in_repo(repo: Repository, package_entry: FullSpecSelector) -> list[FullSpecSelector]:
    if package_entry.profile not in repo.profiles:
        raise PersistedTrackedPackageEntryResolutionError(reason="unknown_profile", message="unknown profile")
    package_match = package_entry.selector in repo.packages
    group_match = package_entry.selector in repo.groups
    if package_match and group_match:
        raise PersistedTrackedPackageEntryResolutionError(reason="selector_kind_invalid", message="selector kind invalid")
    if not package_match and not group_match:
        raise PersistedTrackedPackageEntryResolutionError(reason="unknown_selector", message="unknown selector")
    if package_match:
        return [package_entry]
    try:
        package_ids = repo.expand_group(package_entry.selector)
    except ValueError as exc:
        raise PersistedTrackedPackageEntryResolutionError(
            reason="dependency_resolution_failed",
            message="dependency resolution failed",
        ) from exc
    return [
        FullSpecSelector(
            repo=package_entry.repo,
            selector=package_id,
            selector_kind="package",
            profile=package_entry.profile,
        )
        for package_id in package_ids
    ]



def effective_tracked_package_entries_for_repo(engine: Any, repo: Repository, raw_bindings: list[FullSpecSelector]) -> list[FullSpecSelector]:
    effective_bindings: list[FullSpecSelector] = []
    for binding in raw_bindings:
        try:
            expanded_bindings = engine._expand_tracked_package_entry(repo, binding)
        except PersistedTrackedPackageEntryResolutionError:
            continue
        effective_bindings = engine._normalize_tracked_package_entry_set(effective_bindings, expanded_bindings)
    return effective_bindings



def validate_tracked_package_entries(engine: Any, bindings_by_repo: dict[str, list[FullSpecSelector]]) -> None:
    # Tracked-state validity is defined by the resolved push winner set for live targets.
    engine._build_tracked_plans(operation="push", bindings_by_repo=bindings_by_repo)



def record_tracked_package_entry(engine: Any, binding: FullSpecSelector) -> None:
    repo = engine.get_repo(binding.repo)
    raw_tracked_package_entries_by_repo = engine._raw_tracked_package_entries_by_repo()
    normalized = engine._normalize_tracked_package_entry_set(
        engine._effective_tracked_package_entries_for_repo(repo, raw_tracked_package_entries_by_repo.get(repo.config.name, [])),
        engine._expand_tracked_package_entry(repo, binding),
    )
    raw_tracked_package_entries_by_repo[repo.config.name] = normalized
    if not engine.list_invalid_explicit_package_entries(bindings_by_repo=raw_tracked_package_entries_by_repo):
        engine._validate_tracked_package_entries(engine._effective_tracked_package_entries_by_repo(raw_tracked_package_entries_by_repo))
    engine.write_tracked_package_entries(repo, normalized)



def validate_tracked_package_entry(engine: Any, binding: FullSpecSelector) -> None:
    repo = engine.get_repo(binding.repo)
    raw_tracked_package_entries_by_repo = engine._raw_tracked_package_entries_by_repo()
    raw_tracked_package_entries_by_repo[repo.config.name] = engine._normalize_tracked_package_entry_set(
        engine._effective_tracked_package_entries_for_repo(repo, raw_tracked_package_entries_by_repo.get(repo.config.name, [])),
        engine._expand_tracked_package_entry(repo, binding),
    )
    if not engine.list_invalid_explicit_package_entries(bindings_by_repo=raw_tracked_package_entries_by_repo):
        engine._validate_tracked_package_entries(engine._effective_tracked_package_entries_by_repo(raw_tracked_package_entries_by_repo))



def find_persisted_tracked_package_entry_matches(
    engine: Any,
    binding_text: str,
    *,
    parse_full_spec_selector_text: Any,
) -> tuple[str, str | None, list[PersistedTrackedPackageEntryRecord], list[PersistedTrackedPackageEntryRecord]]:
    explicit_repo, selector, profile = parse_full_spec_selector_text(binding_text)
    tracked_records = [
        record for record in engine._all_persisted_tracked_package_entry_records()
        if record.issue is None or record.issue.state in {"invalid", "orphan"}
    ]
    if explicit_repo is not None:
        tracked_records = [record for record in tracked_records if record.package_entry.repo == explicit_repo]
    if profile is not None:
        tracked_records = [record for record in tracked_records if record.package_entry.profile == profile]
    exact_matches = [record for record in tracked_records if record.package_entry.selector == selector]
    partial_matches = [record for record in tracked_records if selector in record.package_entry.selector]
    unique_partials = {
        (
            record.state_key,
            record.package_entry.repo,
            record.package_entry.selector,
            record.package_entry.profile,
        ): record
        for record in partial_matches
    }
    return selector, profile, exact_matches, list(unique_partials.values())



def remove_tracked_package_entry(
    engine: Any,
    binding_text: str,
    *,
    operation: str = "untrack",
    parse_full_spec_selector_text: Any,
) -> FullSpecSelector:
    selector, profile, exact_matches, partial_matches = engine.find_persisted_tracked_package_entry_matches(binding_text)
    binding_label = selector if profile is None else f"{selector}@{profile}"
    if len(exact_matches) == 1:
        return engine.remove_persisted_tracked_package_entry(exact_matches[0], operation=operation)
    if len(exact_matches) > 1:
        raise ValueError(
            f"tracked package entry '{binding_label}' is ambiguous: {engine._format_persisted_tracked_package_entry_candidates(exact_matches)}"
        )

    package_matches, owner_package_entries = engine._tracked_package_matches_for_untrack(
        selector=selector,
        profile=profile,
        repo_name=parse_full_spec_selector_text(binding_text)[0],
    )
    if partial_matches:
        package_matches = [
            package
            for package in package_matches
            if not any(
                record.package_entry.repo == package.repo and record.package_entry.selector == package.package_id
                for record in partial_matches
            )
        ]
        if package_matches:
            binding_candidates = engine._format_persisted_tracked_package_entry_candidates(partial_matches)
            package_candidates = engine._format_tracked_package_candidates(package_matches)
            raise ValueError(
                f"tracked package entry '{binding_label}' is ambiguous: tracked package entries: {binding_candidates}; tracked packages: {package_candidates}"
            )
        if len(partial_matches) == 1:
            record = partial_matches[0]
            raise ValueError(
                f"no exact match for '{binding_label}'; use exact name '{record.package_entry.repo}:{record.package_entry.selector}@{record.package_entry.profile}'"
            )
        raise ValueError(
            f"tracked package entry '{binding_label}' is ambiguous: {engine._format_persisted_tracked_package_entry_candidates(partial_matches)}"
        )

    if package_matches:
        if len(package_matches) > 1:
            raise ValueError(
                f"tracked package entry '{binding_label}' is ambiguous: tracked packages: {engine._format_tracked_package_candidates(package_matches)}"
            )
        owners = engine._format_owner_package_entries(owner_package_entries)
        required_repo = parse_full_spec_selector_text(binding_text)[0] or package_matches[0].repo
        required_ref = f"{required_repo}:{selector}"
        raise ValueError(f"cannot {operation} '{required_ref}': required by tracked package entries: {owners}")

    raise ValueError(f"tracked package entry '{binding_label}' is not currently tracked")



def find_tracked_package_owners(
    engine: Any,
    candidate_repos: list[Repository],
    selector: str,
    profile: str | None,
) -> list[tuple[Repository, FullSpecSelector]]:
    owners: list[tuple[Repository, FullSpecSelector]] = []
    candidate_repo_names = {repo.config.name for repo in candidate_repos}
    for repo, binding, _selector_kind, package_ids in engine._iter_tracked_package_entries():
        if repo.config.name not in candidate_repo_names:
            continue
        if profile is not None and binding.profile != profile:
            continue
        if selector in package_ids and (repo, binding) not in owners:
            owners.append((repo, binding))
    return owners



def write_tracked_package_entries(engine: Any, repo: Repository, bindings: list[FullSpecSelector]) -> None:
    write_tracked_package_entries_file(repo.config.state_path, bindings)



def write_tracked_package_entries_file(state_dir: Path, bindings: list[FullSpecSelector]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = tracked_packages_file_path(state_dir)
    temp_path = state_path.with_suffix(".tmp")
    lines = [f"schema_version = {TRACKED_PACKAGES_SCHEMA_VERSION}", ""]
    tracked_packages = sorted(
        [
            TrackedPackageEntry(repo=binding.repo, package_id=binding.selector, profile=binding.profile)
            for binding in bindings
        ],
        key=lambda entry: (entry.repo, entry.package_id, entry.profile),
    )
    for entry in tracked_packages:
        lines.extend(
            [
                "[[packages]]",
                f'repo = "{entry.repo}"',
                f'package_id = "{entry.package_id}"',
                f'profile = "{entry.profile}"',
                "",
            ]
        )
    temp_path.write_text("\n".join(lines), encoding="utf-8")
    temp_path.replace(state_path)



def remove_persisted_tracked_package_entry(
    engine: Any,
    record: PersistedTrackedPackageEntryRecord,
    *,
    operation: str = "untrack",
    tracked_target_conflict_error: type[Exception],
) -> FullSpecSelector:
    state_path = tracked_packages_file_path(record.state_dir)
    if record.repo is not None and record.issue is None:
        raw_tracked_package_entries_by_repo = engine._raw_tracked_package_entries_by_repo()
        remaining = engine._remove_tracked_package_entry_record(engine.read_effective_tracked_package_entries(record.repo), record.package_entry)
        raw_tracked_package_entries_by_repo[record.repo.config.name] = remaining
        if not engine.list_invalid_explicit_package_entries(bindings_by_repo=raw_tracked_package_entries_by_repo):
            try:
                engine._validate_tracked_package_entries(engine._effective_tracked_package_entries_by_repo(raw_tracked_package_entries_by_repo))
            except tracked_target_conflict_error as exc:
                binding_label = f"{record.package_entry.repo}:{record.package_entry.selector}@{record.package_entry.profile}"
                raise ValueError(
                    f"cannot {operation} '{binding_label}': removing this binding would expose {exc}"
                ) from None
        write_tracked_package_entries_file(record.state_dir, remaining)
        return record.package_entry

    remaining = engine._remove_tracked_package_entry_record(engine._read_tracked_package_entries_file(state_path), record.package_entry)
    if record.repo is not None:
        raw_tracked_package_entries_by_repo = engine._raw_tracked_package_entries_by_repo()
        raw_tracked_package_entries_by_repo[record.repo.config.name] = remaining
        if not engine.list_invalid_explicit_package_entries(bindings_by_repo=raw_tracked_package_entries_by_repo):
            try:
                engine._validate_tracked_package_entries(engine._effective_tracked_package_entries_by_repo(raw_tracked_package_entries_by_repo))
            except tracked_target_conflict_error as exc:
                binding_label = f"{record.package_entry.repo}:{record.package_entry.selector}@{record.package_entry.profile}"
                raise ValueError(
                    f"cannot {operation} '{binding_label}': removing this binding would expose {exc}"
                ) from None
    write_tracked_package_entries_file(record.state_dir, remaining)
    return record.package_entry



def remove_tracked_package_entry_record(bindings: list[FullSpecSelector], target: FullSpecSelector) -> list[FullSpecSelector]:
    removed = False
    remaining: list[FullSpecSelector] = []
    for binding in bindings:
        if not removed and binding == target:
            removed = True
            continue
        remaining.append(binding)
    return remaining



def iter_tracked_package_entries(engine: Any) -> list[tuple[Repository, FullSpecSelector, str, list[str]]]:
    valid_records, _invalid_records = engine._configured_persisted_tracked_package_entry_records()
    return [
        (record.repo, record.package_entry, record.selector_kind or "package", list(record.package_ids))
        for record in valid_records
        if record.repo is not None
    ]
def configured_persisted_tracked_package_entry_records(
    engine: Any,
    *,
    bindings_by_repo: dict[str, list[FullSpecSelector]] | None = None,
) -> tuple[list[PersistedTrackedPackageEntryRecord], list[PersistedTrackedPackageEntryRecord]]:
    valid_records: list[PersistedTrackedPackageEntryRecord] = []
    invalid_records: list[PersistedTrackedPackageEntryRecord] = []
    current_bindings = bindings_by_repo or engine._raw_tracked_package_entries_by_repo()
    for repo_config in engine.config.ordered_repos:
        repo = engine.get_repo(repo_config.name)
        for binding in current_bindings.get(repo_config.name, []):
            try:
                resolved_bindings = engine._resolve_persisted_tracked_package_entry(repo, binding)
            except PersistedTrackedPackageEntryResolutionError as exc:
                invalid_records.append(
                    PersistedTrackedPackageEntryRecord(
                        state_key=repo.config.state_key,
                        state_dir=repo.config.state_path,
                        package_entry=binding,
                        repo=repo,
                        issue=TrackedPackageEntryIssue(
                            state_key=repo.config.state_key,
                            repo=binding.repo,
                            selector=binding.selector,
                            profile=binding.profile,
                            state="invalid",
                            reason=exc.reason,
                            message=exc.message,
                        ),
                    )
                )
                continue
            for resolved_binding in resolved_bindings:
                valid_records.append(
                    PersistedTrackedPackageEntryRecord(
                        state_key=repo.config.state_key,
                        state_dir=repo.config.state_path,
                        package_entry=resolved_binding,
                        repo=repo,
                        selector_kind="package",
                        package_ids=tuple(engine._resolve_package_ids(repo, resolved_binding.selector, "package")),
                    )
                )
    return valid_records, invalid_records



def orphan_persisted_tracked_package_entry_records(engine: Any) -> list[PersistedTrackedPackageEntryRecord]:
    state_root = default_state_root() / "repos"
    if not state_root.exists():
        return []
    configured_state_keys = {repo_config.state_key for repo_config in engine.config.ordered_repos}
    orphan_records: list[PersistedTrackedPackageEntryRecord] = []
    for state_dir in sorted(path for path in state_root.iterdir() if path.is_dir()):
        if state_dir.name in configured_state_keys:
            continue
        state_path = tracked_packages_file_path(state_dir)
        if not state_path.exists():
            continue
        for binding in engine._read_tracked_package_entries_file(state_path):
            orphan_records.append(
                PersistedTrackedPackageEntryRecord(
                    state_key=state_dir.name,
                    state_dir=state_dir,
                    package_entry=binding,
                    issue=TrackedPackageEntryIssue(
                        state_key=state_dir.name,
                        repo=binding.repo,
                        selector=binding.selector,
                        profile=binding.profile,
                        state="orphan",
                        reason="unknown_repo",
                        message="repo not in config",
                    ),
                )
            )
    return orphan_records



def all_persisted_tracked_package_entry_records(engine: Any) -> list[PersistedTrackedPackageEntryRecord]:
    valid_records, invalid_records = engine._configured_persisted_tracked_package_entry_records()
    return [*valid_records, *invalid_records, *engine._orphan_persisted_tracked_package_entry_records()]



def resolve_persisted_tracked_package_entry(engine: Any, repo: Repository, binding: FullSpecSelector) -> list[FullSpecSelector]:
    if binding.selector not in repo.packages:
        if binding.selector in repo.groups:
            raise PersistedTrackedPackageEntryResolutionError(
                reason="selector_kind_invalid",
                message="tracked packages file rows must reference packages, not groups",
            )
        raise PersistedTrackedPackageEntryResolutionError(reason="unknown_selector", message="unknown selector")
    resolved_bindings = [binding]
    try:
        for resolved_binding in resolved_bindings:
            engine._resolve_package_ids(repo, resolved_binding.selector, "package")
    except ValueError as exc:
        raise PersistedTrackedPackageEntryResolutionError(
            reason="dependency_resolution_failed",
            message="dependency resolution failed",
        ) from exc
    return resolved_bindings



def tracked_package_matches_for_untrack(
    engine: Any,
    *,
    selector: str,
    profile: str | None,
    repo_name: str | None,
) -> tuple[list[TrackedPackageSummary], list[TrackedPackageEntrySummary]]:
    package_matches: list[TrackedPackageSummary] = []
    owner_package_entries: dict[tuple[str, str, str], TrackedPackageEntrySummary] = {}
    if repo_name is not None and repo_name not in engine.repos:
        return package_matches, []
    candidate_repo_names = set(engine.repos) if repo_name is None else {repo_name}
    for package in engine.list_tracked_packages():
        if package.repo not in candidate_repo_names:
            continue
        matching_entries = [package_entry for package_entry in package.package_entries if profile is None or package_entry.profile == profile]
        if not matching_entries:
            continue
        package_ref = package.package_ref
        if package.package_id == selector:
            package_matches.append(package)
        elif selector in package_ref:
            package_matches.append(package)
        else:
            continue
        for package_entry in matching_entries:
            owner_package_entries[(package_entry.repo, package_entry.selector, package_entry.profile)] = package_entry
    sorted_package_matches = sorted(
        package_matches,
        key=lambda item: (item.repo, item.package_id, "" if item.bound_profile is None else item.bound_profile),
    )
    sorted_owner_package_entries = sorted(
        owner_package_entries.values(),
        key=lambda item: (item.repo, item.selector, item.profile),
    )
    return sorted_package_matches, sorted_owner_package_entries



def sorted_tracked_package_entry_issues(issues: list[TrackedPackageEntryIssue]) -> list[TrackedPackageEntryIssue]:
    return sorted(
        issues,
        key=lambda item: (
            0 if item.state == "orphan" else 1,
            item.repo,
            item.selector,
            item.profile,
            item.state_key,
        ),
    )



def format_persisted_tracked_package_entry_candidates(records: list[PersistedTrackedPackageEntryRecord]) -> str:
    return ", ".join(
        f"{record.package_entry.repo}:{record.package_entry.selector}@{record.package_entry.profile}"
        for record in records
    )



def format_tracked_package_candidates(packages: list[TrackedPackageSummary]) -> str:
    return ", ".join(f"{package.repo}:{package.package_ref}" for package in packages)



def format_owner_package_entries(package_entries: list[TrackedPackageEntrySummary]) -> str:
    return ", ".join(
        f"{package_entry.repo}:{package_entry.selector}@{package_entry.profile}"
        for package_entry in package_entries
    )
