from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotman.config import default_state_root
from dotman.toml_utils import load_toml_file
from dotman.models import (
    Binding,
    TrackedBindingSummary,
    TrackedPackageBindingDetail,
    TrackedPackageDetail,
    TrackedPackageSummary,
    TrackedTargetRefDetail,
    TrackedBindingIssue,
    package_ref_text,
)
from dotman.repository import Repository


class PersistedBindingResolutionError(ValueError):
    def __init__(self, *, reason: str, message: str) -> None:
        self.reason = reason
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class PersistedBindingRecord:
    state_key: str
    state_dir: Path
    binding: Binding
    repo: Repository | None = None
    selector_kind: str | None = None
    package_ids: tuple[str, ...] = ()
    issue: TrackedBindingIssue | None = None


@dataclass(frozen=True)
class TrackedStateSummary:
    packages: list[TrackedPackageSummary]
    invalid_bindings: list[TrackedBindingIssue]



def list_tracked_state(engine: Any) -> TrackedStateSummary:
    return TrackedStateSummary(
        packages=engine.list_tracked_packages(),
        invalid_bindings=engine._sorted_binding_issues(
            [
                *engine.list_invalid_explicit_bindings(),
                *engine.list_orphan_explicit_bindings(),
            ]
        ),
    )



def list_invalid_explicit_bindings(
    engine: Any,
    *,
    bindings_by_repo: dict[str, list[Binding]] | None = None,
) -> list[TrackedBindingIssue]:
    _valid_records, invalid_records = engine._configured_persisted_binding_records(bindings_by_repo=bindings_by_repo)
    return engine._sorted_binding_issues([record.issue for record in invalid_records if record.issue is not None])



def list_orphan_explicit_bindings(engine: Any) -> list[TrackedBindingIssue]:
    return engine._sorted_binding_issues(
        [record.issue for record in engine._orphan_persisted_binding_records() if record.issue is not None]
    )



def list_tracked_packages(engine: Any) -> list[TrackedPackageSummary]:
    tracked_packages: dict[tuple[str, str, str | None], TrackedPackageSummary] = {}
    package_states: dict[tuple[str, str, str | None], str] = {}
    for repo, binding, selector_kind, package_ids in engine._iter_tracked_bindings():
        binding_summary = TrackedBindingSummary(
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
                    bindings=[binding_summary],
                    state=package_state,
                    bound_profile=bound_profile,
                )
                package_states[key] = package_state
                continue
            if binding_summary not in existing.bindings:
                existing.bindings.append(binding_summary)
            if package_state == "explicit":
                package_states[key] = "explicit"

    return [
        TrackedPackageSummary(
            repo=summary.repo,
            package_id=summary.package_id,
            description=summary.description,
            bindings=sorted(summary.bindings, key=lambda item: (item.selector, item.profile, item.repo)),
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
def describe_tracked_package(engine: Any, package_text: str) -> TrackedPackageDetail:
    repo, package_id, bound_profile = engine._resolve_tracked_package(package_text)
    effective_binding_keys = engine._effective_package_binding_keys(
        repo.config.name,
        package_id,
        bound_profile,
    )
    details: list[TrackedPackageBindingDetail] = []
    description = repo.resolve_package(package_id).description

    for candidate_repo, binding, selector_kind, package_ids in engine._iter_tracked_bindings():
        if candidate_repo.config.name != repo.config.name or package_id not in package_ids:
            continue
        if engine._bound_profile_for_package(candidate_repo, package_id, binding.profile) != bound_profile:
            continue
        details.append(
            engine._describe_package_binding(
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
    target_refs = [
        TrackedTargetRefDetail(
            target_name=target_name,
            chain=repo.resolve_target_reference(package_id, target_name).chain,
        )
        for target_name in sorted(resolved_package.target_refs or {})
    ]

    return TrackedPackageDetail(
        repo=repo.config.name,
        package_id=package_id,
        description=description,
        bindings=sorted(details, key=lambda item: (item.binding.selector, item.binding.profile, item.binding.repo)),
        owned_targets=engine._describe_owned_package_targets(
            repo.config.name,
            package_id,
            bound_profile,
        ),
        target_refs=target_refs,
        bound_profile=bound_profile,
    )
def read_bindings_file(state_path: Path) -> list[Binding]:
    if not state_path.exists():
        return []
    payload = load_toml_file(state_path, context="bindings file")
    bindings_payload = payload.get("bindings", [])
    bindings: list[Binding] = []
    for binding_payload in bindings_payload:
        bindings.append(
            Binding(
                repo=str(binding_payload["repo"]),
                selector=str(binding_payload["selector"]),
                profile=str(binding_payload["profile"]),
            )
        )
    return bindings



def read_bindings(engine: Any, repo: Repository) -> list[Binding]:
    return read_bindings_file(repo.config.state_path / "bindings.toml")



def read_effective_bindings(engine: Any, repo: Repository) -> list[Binding]:
    return engine._effective_bindings_for_repo(repo, engine.read_bindings(repo))



def expand_binding_for_tracking(engine: Any, binding: Binding) -> list[Binding]:
    repo = engine.get_repo(binding.repo)
    return engine._expand_binding_for_tracking(repo, binding)



def raw_bindings_by_repo(engine: Any) -> dict[str, list[Binding]]:
    return {
        repo_config.name: engine.read_bindings(engine.get_repo(repo_config.name))
        for repo_config in engine.config.ordered_repos
    }



def effective_bindings_by_repo(
    engine: Any,
    raw_bindings_by_repo: dict[str, list[Binding]] | None = None,
) -> dict[str, list[Binding]]:
    current_raw_bindings = raw_bindings_by_repo or engine._raw_bindings_by_repo()
    return {
        repo_config.name: engine._effective_bindings_for_repo(
            engine.get_repo(repo_config.name),
            current_raw_bindings.get(repo_config.name, []),
        )
        for repo_config in engine.config.ordered_repos
    }



def binding_scope_key(engine: Any, repo: Repository, binding: Binding) -> tuple[str, str, str | None]:
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



def normalize_recorded_bindings(engine: Any, bindings: list[Binding], binding: Binding) -> list[Binding]:
    repo = engine.get_repo(binding.repo)
    target_scope = engine._binding_scope_key(repo, binding)
    updated = False
    normalized: list[Binding] = []
    for existing in bindings:
        if engine._binding_scope_key(repo, existing) == target_scope:
            if not updated:
                normalized.append(binding)
                updated = True
            continue
        normalized.append(existing)
    if not updated:
        normalized.append(binding)
    return normalized



def normalize_recorded_binding_set(engine: Any, bindings: list[Binding], additions: list[Binding]) -> list[Binding]:
    normalized = list(bindings)
    for binding in additions:
        normalized = engine._normalize_recorded_bindings(normalized, binding)
    return normalized



def expand_binding_for_tracking_in_repo(repo: Repository, binding: Binding) -> list[Binding]:
    if binding.profile not in repo.profiles:
        raise PersistedBindingResolutionError(reason="unknown_profile", message="unknown profile")
    package_match = binding.selector in repo.packages
    group_match = binding.selector in repo.groups
    if package_match and group_match:
        raise PersistedBindingResolutionError(reason="selector_kind_invalid", message="selector kind invalid")
    if not package_match and not group_match:
        raise PersistedBindingResolutionError(reason="unknown_selector", message="unknown selector")
    if package_match:
        return [binding]
    try:
        package_ids = repo.expand_group(binding.selector)
    except ValueError as exc:
        raise PersistedBindingResolutionError(
            reason="dependency_resolution_failed",
            message="dependency resolution failed",
        ) from exc
    return [Binding(repo=binding.repo, selector=package_id, profile=binding.profile) for package_id in package_ids]



def effective_bindings_for_repo(engine: Any, repo: Repository, raw_bindings: list[Binding]) -> list[Binding]:
    effective_bindings: list[Binding] = []
    for binding in raw_bindings:
        try:
            expanded_bindings = engine._expand_binding_for_tracking(repo, binding)
        except PersistedBindingResolutionError:
            continue
        effective_bindings = engine._normalize_recorded_binding_set(effective_bindings, expanded_bindings)
    return effective_bindings



def validate_tracked_bindings(engine: Any, bindings_by_repo: dict[str, list[Binding]]) -> None:
    # Tracked-state validity is defined by the resolved push winner set for live targets.
    engine._build_tracked_plans(operation="push", bindings_by_repo=bindings_by_repo)



def record_binding(engine: Any, binding: Binding) -> None:
    repo = engine.get_repo(binding.repo)
    raw_bindings_by_repo = engine._raw_bindings_by_repo()
    normalized = engine._normalize_recorded_binding_set(
        engine._effective_bindings_for_repo(repo, raw_bindings_by_repo.get(repo.config.name, [])),
        engine._expand_binding_for_tracking(repo, binding),
    )
    raw_bindings_by_repo[repo.config.name] = normalized
    if not engine.list_invalid_explicit_bindings(bindings_by_repo=raw_bindings_by_repo):
        engine._validate_tracked_bindings(engine._effective_bindings_by_repo(raw_bindings_by_repo))
    engine.write_bindings(repo, normalized)



def validate_recorded_binding(engine: Any, binding: Binding) -> None:
    repo = engine.get_repo(binding.repo)
    raw_bindings_by_repo = engine._raw_bindings_by_repo()
    raw_bindings_by_repo[repo.config.name] = engine._normalize_recorded_binding_set(
        engine._effective_bindings_for_repo(repo, raw_bindings_by_repo.get(repo.config.name, [])),
        engine._expand_binding_for_tracking(repo, binding),
    )
    if not engine.list_invalid_explicit_bindings(bindings_by_repo=raw_bindings_by_repo):
        engine._validate_tracked_bindings(engine._effective_bindings_by_repo(raw_bindings_by_repo))



def find_persisted_binding_matches(
    engine: Any,
    binding_text: str,
    *,
    parse_binding_text: Any,
) -> tuple[str, str | None, list[PersistedBindingRecord], list[PersistedBindingRecord]]:
    explicit_repo, selector, profile = parse_binding_text(binding_text)
    tracked_records = [
        record for record in engine._all_persisted_binding_records()
        if record.issue is None or record.issue.state in {"invalid", "orphan"}
    ]
    if explicit_repo is not None:
        tracked_records = [record for record in tracked_records if record.binding.repo == explicit_repo]
    if profile is not None:
        tracked_records = [record for record in tracked_records if record.binding.profile == profile]
    exact_matches = [record for record in tracked_records if record.binding.selector == selector]
    partial_matches = [record for record in tracked_records if selector in record.binding.selector]
    unique_partials = {
        (
            record.state_key,
            record.binding.repo,
            record.binding.selector,
            record.binding.profile,
        ): record
        for record in partial_matches
    }
    return selector, profile, exact_matches, list(unique_partials.values())



def remove_binding(
    engine: Any,
    binding_text: str,
    *,
    operation: str = "untrack",
    parse_binding_text: Any,
) -> Binding:
    selector, profile, exact_matches, partial_matches = engine.find_persisted_binding_matches(binding_text)
    binding_label = selector if profile is None else f"{selector}@{profile}"
    if len(exact_matches) == 1:
        return engine.remove_persisted_binding(exact_matches[0], operation=operation)
    if len(exact_matches) > 1:
        raise ValueError(
            f"tracked package entry '{binding_label}' is ambiguous: {engine._format_persisted_binding_candidates(exact_matches)}"
        )

    package_matches, owner_bindings = engine._tracked_package_matches_for_untrack(
        selector=selector,
        profile=profile,
        repo_name=parse_binding_text(binding_text)[0],
    )
    if partial_matches:
        package_matches = [
            package
            for package in package_matches
            if not any(
                record.binding.repo == package.repo and record.binding.selector == package.package_id
                for record in partial_matches
            )
        ]
        if package_matches:
            binding_candidates = engine._format_persisted_binding_candidates(partial_matches)
            package_candidates = engine._format_tracked_package_candidates(package_matches)
            raise ValueError(
                f"tracked package entry '{binding_label}' is ambiguous: tracked package entries: {binding_candidates}; tracked packages: {package_candidates}"
            )
        if len(partial_matches) == 1:
            record = partial_matches[0]
            raise ValueError(
                f"no exact match for '{binding_label}'; use exact name '{record.binding.repo}:{record.binding.selector}@{record.binding.profile}'"
            )
        raise ValueError(
            f"tracked package entry '{binding_label}' is ambiguous: {engine._format_persisted_binding_candidates(partial_matches)}"
        )

    if package_matches:
        if len(package_matches) > 1:
            raise ValueError(
                f"tracked package entry '{binding_label}' is ambiguous: tracked packages: {engine._format_tracked_package_candidates(package_matches)}"
            )
        owners = engine._format_owner_bindings(owner_bindings)
        required_repo = parse_binding_text(binding_text)[0] or package_matches[0].repo
        required_ref = f"{required_repo}:{selector}"
        raise ValueError(f"cannot {operation} '{required_ref}': required by tracked package entries: {owners}")

    raise ValueError(f"tracked package entry '{binding_label}' is not currently tracked")



def find_tracked_package_owners(
    engine: Any,
    candidate_repos: list[Repository],
    selector: str,
    profile: str | None,
) -> list[tuple[Repository, Binding]]:
    owners: list[tuple[Repository, Binding]] = []
    candidate_repo_names = {repo.config.name for repo in candidate_repos}
    for repo, binding, _selector_kind, package_ids in engine._iter_tracked_bindings():
        if repo.config.name not in candidate_repo_names:
            continue
        if profile is not None and binding.profile != profile:
            continue
        if selector in package_ids and (repo, binding) not in owners:
            owners.append((repo, binding))
    return owners



def write_bindings(engine: Any, repo: Repository, bindings: list[Binding]) -> None:
    write_bindings_file(repo.config.state_path, bindings)



def write_bindings_file(state_dir: Path, bindings: list[Binding]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "bindings.toml"
    temp_path = state_path.with_suffix(".tmp")
    lines = ["version = 1", ""]
    for binding in bindings:
        lines.extend(
            [
                "[[bindings]]",
                f'repo = "{binding.repo}"',
                f'selector = "{binding.selector}"',
                f'profile = "{binding.profile}"',
                "",
            ]
        )
    temp_path.write_text("\n".join(lines), encoding="utf-8")
    temp_path.replace(state_path)



def remove_persisted_binding(
    engine: Any,
    record: PersistedBindingRecord,
    *,
    operation: str = "untrack",
    tracked_target_conflict_error: type[Exception],
) -> Binding:
    state_path = record.state_dir / "bindings.toml"
    if record.repo is not None and record.issue is None:
        raw_bindings_by_repo = engine._raw_bindings_by_repo()
        remaining = engine._remove_binding_record(engine.read_effective_bindings(record.repo), record.binding)
        raw_bindings_by_repo[record.repo.config.name] = remaining
        if not engine.list_invalid_explicit_bindings(bindings_by_repo=raw_bindings_by_repo):
            try:
                engine._validate_tracked_bindings(engine._effective_bindings_by_repo(raw_bindings_by_repo))
            except tracked_target_conflict_error as exc:
                binding_label = f"{record.binding.repo}:{record.binding.selector}@{record.binding.profile}"
                raise ValueError(
                    f"cannot {operation} '{binding_label}': removing this binding would expose {exc}"
                ) from None
        write_bindings_file(record.state_dir, remaining)
        return record.binding

    remaining = engine._remove_binding_record(engine._read_bindings_file(state_path), record.binding)
    if record.repo is not None:
        raw_bindings_by_repo = engine._raw_bindings_by_repo()
        raw_bindings_by_repo[record.repo.config.name] = remaining
        if not engine.list_invalid_explicit_bindings(bindings_by_repo=raw_bindings_by_repo):
            try:
                engine._validate_tracked_bindings(engine._effective_bindings_by_repo(raw_bindings_by_repo))
            except tracked_target_conflict_error as exc:
                binding_label = f"{record.binding.repo}:{record.binding.selector}@{record.binding.profile}"
                raise ValueError(
                    f"cannot {operation} '{binding_label}': removing this binding would expose {exc}"
                ) from None
    write_bindings_file(record.state_dir, remaining)
    return record.binding



def remove_binding_record(bindings: list[Binding], target: Binding) -> list[Binding]:
    removed = False
    remaining: list[Binding] = []
    for binding in bindings:
        if not removed and binding == target:
            removed = True
            continue
        remaining.append(binding)
    return remaining



def iter_tracked_bindings(engine: Any) -> list[tuple[Repository, Binding, str, list[str]]]:
    valid_records, _invalid_records = engine._configured_persisted_binding_records()
    return [
        (record.repo, record.binding, record.selector_kind or "package", list(record.package_ids))
        for record in valid_records
        if record.repo is not None
    ]
def configured_persisted_binding_records(
    engine: Any,
    *,
    bindings_by_repo: dict[str, list[Binding]] | None = None,
) -> tuple[list[PersistedBindingRecord], list[PersistedBindingRecord]]:
    valid_records: list[PersistedBindingRecord] = []
    invalid_records: list[PersistedBindingRecord] = []
    current_bindings = bindings_by_repo or engine._raw_bindings_by_repo()
    for repo_config in engine.config.ordered_repos:
        repo = engine.get_repo(repo_config.name)
        for binding in current_bindings.get(repo_config.name, []):
            try:
                resolved_bindings = engine._resolve_persisted_binding(repo, binding)
            except PersistedBindingResolutionError as exc:
                invalid_records.append(
                    PersistedBindingRecord(
                        state_key=repo.config.state_key,
                        state_dir=repo.config.state_path,
                        binding=binding,
                        repo=repo,
                        issue=TrackedBindingIssue(
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
                    PersistedBindingRecord(
                        state_key=repo.config.state_key,
                        state_dir=repo.config.state_path,
                        binding=resolved_binding,
                        repo=repo,
                        selector_kind="package",
                        package_ids=tuple(engine._resolve_package_ids(repo, resolved_binding.selector, "package")),
                    )
                )
    return valid_records, invalid_records



def orphan_persisted_binding_records(engine: Any) -> list[PersistedBindingRecord]:
    state_root = default_state_root() / "repos"
    if not state_root.exists():
        return []
    configured_state_keys = {repo_config.state_key for repo_config in engine.config.ordered_repos}
    orphan_records: list[PersistedBindingRecord] = []
    for state_dir in sorted(path for path in state_root.iterdir() if path.is_dir()):
        if state_dir.name in configured_state_keys:
            continue
        state_path = state_dir / "bindings.toml"
        if not state_path.exists():
            continue
        for binding in engine._read_bindings_file(state_path):
            orphan_records.append(
                PersistedBindingRecord(
                    state_key=state_dir.name,
                    state_dir=state_dir,
                    binding=binding,
                    issue=TrackedBindingIssue(
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



def all_persisted_binding_records(engine: Any) -> list[PersistedBindingRecord]:
    valid_records, invalid_records = engine._configured_persisted_binding_records()
    return [*valid_records, *invalid_records, *engine._orphan_persisted_binding_records()]



def resolve_persisted_binding(engine: Any, repo: Repository, binding: Binding) -> list[Binding]:
    resolved_bindings = engine._expand_binding_for_tracking(repo, binding)
    try:
        for resolved_binding in resolved_bindings:
            engine._resolve_package_ids(repo, resolved_binding.selector, "package")
    except ValueError as exc:
        raise PersistedBindingResolutionError(
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
) -> tuple[list[TrackedPackageSummary], list[TrackedBindingSummary]]:
    package_matches: list[TrackedPackageSummary] = []
    owner_bindings: dict[tuple[str, str, str], TrackedBindingSummary] = {}
    if repo_name is not None and repo_name not in engine.repos:
        return package_matches, []
    candidate_repo_names = set(engine.repos) if repo_name is None else {repo_name}
    for package in engine.list_tracked_packages():
        if package.repo not in candidate_repo_names:
            continue
        matching_bindings = [binding for binding in package.bindings if profile is None or binding.profile == profile]
        if not matching_bindings:
            continue
        package_ref = package.package_ref
        if package.package_id == selector:
            package_matches.append(package)
        elif selector in package_ref:
            package_matches.append(package)
        else:
            continue
        for binding in matching_bindings:
            owner_bindings[(binding.repo, binding.selector, binding.profile)] = binding
    sorted_package_matches = sorted(
        package_matches,
        key=lambda item: (item.repo, item.package_id, "" if item.bound_profile is None else item.bound_profile),
    )
    sorted_owner_bindings = sorted(
        owner_bindings.values(),
        key=lambda item: (item.repo, item.selector, item.profile),
    )
    return sorted_package_matches, sorted_owner_bindings



def sorted_binding_issues(issues: list[TrackedBindingIssue]) -> list[TrackedBindingIssue]:
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



def format_persisted_binding_candidates(records: list[PersistedBindingRecord]) -> str:
    return ", ".join(
        f"{record.binding.repo}:{record.binding.selector}@{record.binding.profile}"
        for record in records
    )



def format_tracked_package_candidates(packages: list[TrackedPackageSummary]) -> str:
    return ", ".join(f"{package.repo}:{package.package_ref}" for package in packages)



def format_owner_bindings(bindings: list[TrackedBindingSummary]) -> str:
    return ", ".join(f"{binding.repo}:{binding.selector}@{binding.profile}" for binding in bindings)
