from __future__ import annotations

import fnmatch
import os
import subprocess
import sys
import tomllib
from collections import defaultdict
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any

from dotman.config import expand_path, load_manager_config
from dotman.models import (
    Binding,
    BindingPlan,
    GroupSpec,
    HookPlan,
    HookSpec,
    InstalledBindingSummary,
    InstalledPackageBindingDetail,
    InstalledPackageDetail,
    InstalledPackageSummary,
    InstalledTargetSummary,
    ManagerConfig,
    PackageSpec,
    ProfileSpec,
    RepoConfig,
    RepoIgnoreDefaults,
    TargetPlan,
    TargetSpec,
)
from dotman.profiles import compute_profile_heights, rank_profiles
from dotman.templates import build_template_context, render_template_file, render_template_string


def _copy_map(value: dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    result: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, dict):
            result[key] = _copy_map(item)
        elif isinstance(item, list):
            result[key] = list(item)
        else:
            result[key] = item
    return result


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = _copy_map(base)
    for key, override_value in override.items():
        base_value = merged.get(key)
        if isinstance(base_value, dict) and isinstance(override_value, dict):
            merged[key] = deep_merge(base_value, override_value)
        elif isinstance(override_value, list):
            merged[key] = list(override_value)
        elif isinstance(override_value, dict):
            merged[key] = _copy_map(override_value)
        else:
            merged[key] = override_value
    return merged


def dotted_get(data: dict[str, Any], dotted_path: str) -> Any:
    current: Any = data
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def dotted_delete(data: dict[str, Any], dotted_path: str) -> None:
    parts = dotted_path.split(".")
    current: Any = data
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            return
        current = current[part]
    if isinstance(current, dict):
        current.pop(parts[-1], None)


def dotted_append(data: dict[str, Any], dotted_path: str, values: list[Any]) -> None:
    parts = dotted_path.split(".")
    current = data
    for part in parts[:-1]:
        next_value = current.get(part)
        if not isinstance(next_value, dict):
            next_value = {}
            current[part] = next_value
        current = next_value
    existing = current.get(parts[-1])
    if existing is None:
        current[parts[-1]] = list(values)
        return
    if not isinstance(existing, list):
        raise ValueError(f"append target '{dotted_path}' is not a list")
    current[parts[-1]] = [*existing, *values]


def normalize_string_list(value: Any) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise ValueError(f"expected string or list[str], got {type(value).__name__}")


def merge_ignore_patterns(*pattern_sets: tuple[str, ...]) -> tuple[str, ...]:
    merged: list[str] = []
    for pattern_set in pattern_sets:
        for pattern in pattern_set:
            if pattern not in merged:
                merged.append(pattern)
    return tuple(merged)


def flatten_vars(data: dict[str, Any], prefix: str = "") -> dict[str, str]:
    flattened: dict[str, str] = {}
    for key, value in data.items():
        flat_key = f"{prefix}__{key}" if prefix else key
        if isinstance(value, dict):
            flattened.update(flatten_vars(value, flat_key))
        else:
            flattened[flat_key] = str(value)
    return flattened


def infer_profile_os(profile_id: str, lineage: list[str], variables: dict[str, Any]) -> str:
    explicit = variables.get("os")
    if isinstance(explicit, str):
        return explicit
    names = [profile_id, *lineage]
    joined = " ".join(names)
    if "mac" in joined:
        return "darwin"
    if "linux" in joined or "arch" in joined:
        return "linux"
    return sys.platform


def matches_ignore_pattern(relative_path: str, pattern: str) -> bool:
    normalized = relative_path.strip("/")
    cleaned = pattern.strip("/")
    if not cleaned:
        return False
    if "/" in cleaned:
        return fnmatch.fnmatchcase(normalized, cleaned)
    path_parts = PurePosixPath(normalized).parts
    return fnmatch.fnmatchcase(normalized, cleaned) or any(
        fnmatch.fnmatchcase(part, cleaned) for part in path_parts
    )


def list_directory_files(root: Path, ignore_patterns: tuple[str, ...]) -> dict[str, Path]:
    files: dict[str, Path] = {}
    if not root.exists():
        return files
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        relative = path.relative_to(root).as_posix()
        if any(matches_ignore_pattern(relative, pattern) for pattern in ignore_patterns):
            continue
        files[relative] = path
    return files


class Repository:
    def __init__(self, config: RepoConfig) -> None:
        self.config = config
        self.root = config.path
        self.ignore_defaults = self._load_repo_ignore_defaults()
        self.packages = self._load_packages()
        self.groups = self._load_groups()
        self.profiles = self._load_profiles()
        self.local_vars = self._load_local_vars()
        self._resolved_packages: dict[str, PackageSpec] = {}

    def _load_repo_ignore_defaults(self) -> RepoIgnoreDefaults:
        repo_config_path = self.root / "repo.toml"
        if not repo_config_path.exists():
            return RepoIgnoreDefaults()
        payload = tomllib.loads(repo_config_path.read_text(encoding="utf-8"))
        ignore_payload = payload.get("ignore")
        if ignore_payload is None:
            return RepoIgnoreDefaults()
        if not isinstance(ignore_payload, dict):
            raise ValueError(f"repo config {repo_config_path} [ignore] must be a table")
        return RepoIgnoreDefaults(
            apply=normalize_string_list(ignore_payload.get("apply")) or (),
            import_=normalize_string_list(ignore_payload.get("import")) or (),
        )

    def _load_packages(self) -> dict[str, PackageSpec]:
        packages: dict[str, PackageSpec] = {}
        for manifest_path in sorted((self.root / "packages").glob("**/package.toml")):
            payload = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
            package_id = payload.get("id")
            if not isinstance(package_id, str):
                raise ValueError(f"package manifest {manifest_path} must define string id")
            targets_payload = payload.get("targets")
            hooks_payload = payload.get("hooks")
            append_payload = payload.get("append")
            targets = (
                {
                    target_name: TargetSpec(
                        name=target_name,
                        declared_in=manifest_path.parent,
                        source=target_payload.get("source"),
                        path=target_payload.get("path"),
                        chmod=target_payload.get("chmod"),
                        render=target_payload.get("render"),
                        capture=target_payload.get("capture"),
                        reconcile=target_payload.get("reconcile"),
                        import_view_repo=target_payload.get("import_view_repo"),
                        import_view_live=target_payload.get("import_view_live"),
                        apply_ignore=normalize_string_list(target_payload.get("apply_ignore")),
                        import_ignore=normalize_string_list(target_payload.get("import_ignore")),
                    )
                    for target_name, target_payload in targets_payload.items()
                }
                if isinstance(targets_payload, dict)
                else None
            )
            hooks = (
                {
                    hook_name: HookSpec(
                        name=hook_name,
                        commands=normalize_string_list(hook_value) or (),
                        declared_in=manifest_path.parent,
                    )
                    for hook_name, hook_value in hooks_payload.items()
                }
                if isinstance(hooks_payload, dict)
                else None
            )
            packages[package_id] = PackageSpec(
                id=package_id,
                package_root=manifest_path.parent,
                description=payload.get("description"),
                depends=normalize_string_list(payload.get("depends")),
                extends=normalize_string_list(payload.get("extends")),
                reserved_paths=normalize_string_list(payload.get("reserved_paths")),
                vars=_copy_map(payload.get("vars")) if isinstance(payload.get("vars"), dict) else None,
                targets=targets,
                hooks=hooks,
                remove=normalize_string_list(payload.get("remove")),
                append=_copy_map(append_payload) if isinstance(append_payload, dict) else None,
            )
        return packages

    def _load_groups(self) -> dict[str, GroupSpec]:
        groups: dict[str, GroupSpec] = {}
        groups_root = self.root / "groups"
        if not groups_root.exists():
            return groups
        for group_path in sorted(groups_root.glob("**/*.toml")):
            group_id = group_path.relative_to(groups_root).with_suffix("").as_posix()
            payload = tomllib.loads(group_path.read_text(encoding="utf-8"))
            groups[group_id] = GroupSpec(
                id=group_id,
                members=normalize_string_list(payload.get("members")) or (),
                path=group_path,
            )
        return groups

    def _load_profiles(self) -> dict[str, ProfileSpec]:
        profiles: dict[str, ProfileSpec] = {}
        profiles_root = self.root / "profiles"
        if not profiles_root.exists():
            return profiles
        for profile_path in sorted(profiles_root.glob("**/*.toml")):
            profile_id = profile_path.relative_to(profiles_root).with_suffix("").as_posix()
            payload = tomllib.loads(profile_path.read_text(encoding="utf-8"))
            profiles[profile_id] = ProfileSpec(
                id=profile_id,
                includes=normalize_string_list(payload.get("includes")) or (),
                vars=_copy_map(payload.get("vars")) if isinstance(payload.get("vars"), dict) else {},
                path=profile_path,
            )
        return profiles

    def _load_local_vars(self) -> dict[str, Any]:
        local_path = self.root / "local.toml"
        if not local_path.exists():
            return {}
        payload = tomllib.loads(local_path.read_text(encoding="utf-8"))
        vars_payload = payload.get("vars")
        return _copy_map(vars_payload) if isinstance(vars_payload, dict) else {}

    def compose_profile(self, profile_id: str) -> tuple[dict[str, Any], list[str]]:
        if profile_id not in self.profiles:
            raise ValueError(f"unknown profile '{profile_id}' in repo '{self.config.name}'")

        lineage: list[str] = []

        def visit(current_id: str, stack: tuple[str, ...]) -> dict[str, Any]:
            if current_id in stack:
                cycle = " -> ".join([*stack, current_id])
                raise ValueError(f"profile include cycle detected in repo '{self.config.name}': {cycle}")
            profile = self.profiles[current_id]
            merged: dict[str, Any] = {}
            for include_id in profile.includes:
                merged = deep_merge(merged, visit(include_id, (*stack, current_id)))
            lineage.append(current_id)
            return deep_merge(merged, profile.vars)

        return visit(profile_id, ()), lineage

    def resolve_package(self, package_id: str) -> PackageSpec:
        cached = self._resolved_packages.get(package_id)
        if cached is not None:
            return cached
        if package_id not in self.packages:
            raise ValueError(f"unknown package '{package_id}' in repo '{self.config.name}'")
        loaded = self.packages[package_id]
        merged: PackageSpec | None = None
        for parent_id in loaded.extends or ():
            parent_spec = self.resolve_package(parent_id)
            merged = parent_spec if merged is None else merge_package_specs(merged, parent_spec)
        current = strip_package_extensions(loaded)
        merged = current if merged is None else merge_package_specs(merged, current)
        merged = apply_remove_and_append(merged, loaded.remove or (), loaded.append or {})
        self._resolved_packages[package_id] = merged
        return merged

    def expand_group(self, group_id: str) -> list[str]:
        if group_id not in self.groups:
            raise ValueError(f"unknown group '{group_id}' in repo '{self.config.name}'")
        ordered: list[str] = []
        seen: set[str] = set()

        def visit(current_group_id: str, stack: tuple[str, ...]) -> None:
            if current_group_id in stack:
                cycle = " -> ".join([*stack, current_group_id])
                raise ValueError(f"group membership cycle detected in repo '{self.config.name}': {cycle}")
            current_group = self.groups[current_group_id]
            for member in current_group.members:
                package_exists = member in self.packages
                group_exists = member in self.groups
                if package_exists and group_exists:
                    raise ValueError(f"selector '{member}' is ambiguous between package and group in repo '{self.config.name}'")
                if group_exists:
                    visit(member, (*stack, current_group_id))
                    continue
                if not package_exists:
                    raise ValueError(f"group member '{member}' does not resolve in repo '{self.config.name}'")
                if member not in seen:
                    seen.add(member)
                    ordered.append(member)

        visit(group_id, ())
        return ordered


def strip_package_extensions(package: PackageSpec) -> PackageSpec:
    return replace(package, extends=None)


def merge_target_specs(base: TargetSpec, override: TargetSpec) -> TargetSpec:
    return TargetSpec(
        name=override.name,
        declared_in=override.declared_in,
        source=override.source if override.source is not None else base.source,
        path=override.path if override.path is not None else base.path,
        chmod=override.chmod if override.chmod is not None else base.chmod,
        render=override.render if override.render is not None else base.render,
        capture=override.capture if override.capture is not None else base.capture,
        reconcile=override.reconcile if override.reconcile is not None else base.reconcile,
        import_view_repo=override.import_view_repo if override.import_view_repo is not None else base.import_view_repo,
        import_view_live=override.import_view_live if override.import_view_live is not None else base.import_view_live,
        apply_ignore=override.apply_ignore if override.apply_ignore is not None else base.apply_ignore,
        import_ignore=override.import_ignore if override.import_ignore is not None else base.import_ignore,
    )


def merge_package_specs(base: PackageSpec, override: PackageSpec) -> PackageSpec:
    targets = dict(base.targets or {})
    for name, target in (override.targets or {}).items():
        targets[name] = merge_target_specs(targets[name], target) if name in targets else target

    hooks = dict(base.hooks or {})
    hooks.update(override.hooks or {})

    return PackageSpec(
        id=override.id,
        package_root=override.package_root,
        description=override.description if override.description is not None else base.description,
        depends=override.depends if override.depends is not None else base.depends,
        extends=None,
        reserved_paths=override.reserved_paths if override.reserved_paths is not None else base.reserved_paths,
        vars=deep_merge(base.vars or {}, override.vars or {}),
        targets=targets,
        hooks=hooks,
        remove=override.remove if override.remove is not None else base.remove,
        append=deep_merge(base.append or {}, override.append or {}),
    )


def apply_remove_and_append(package: PackageSpec, remove_paths: tuple[str, ...], append_payload: dict[str, Any]) -> PackageSpec:
    vars_payload = _copy_map(package.vars or {})
    for dotted_path in remove_paths:
        if dotted_path.startswith("vars."):
            dotted_delete(vars_payload, dotted_path.removeprefix("vars."))

    if append_payload:
        for top_key, value in append_payload.items():
            if isinstance(value, dict):
                for nested_key, nested_value in value.items():
                    dotted_append(vars_payload, f"{top_key}.{nested_key}", list(nested_value))
            else:
                dotted_append(vars_payload, top_key, list(value))

    return replace(package, vars=vars_payload)


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


class DotmanEngine:
    def __init__(self, config: ManagerConfig) -> None:
        self.config = config
        self.repos = {repo.name: Repository(repo) for repo in config.ordered_repos}

    @classmethod
    def from_config_path(cls, config_path: str | Path | None = None) -> "DotmanEngine":
        return cls(load_manager_config(config_path))

    def get_repo(self, repo_name: str) -> Repository:
        try:
            return self.repos[repo_name]
        except KeyError as exc:
            raise ValueError(f"unknown repo '{repo_name}'") from exc

    def resolve_selector(self, selector: str, repo_name: str | None = None) -> tuple[Repository, str, str]:
        candidate_repos = [self.get_repo(repo_name)] if repo_name else [self.repos[repo.name] for repo in self.config.ordered_repos]
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

        if len(exact_matches) == 1:
            return exact_matches[0]
        if len(exact_matches) > 1:
            candidates = ", ".join(f"{repo.config.name}:{match}" for repo, match, _ in exact_matches)
            raise ValueError(f"selector '{selector}' is defined in multiple repos: {candidates}")
        unique_partials = {(repo.config.name, match, kind): (repo, match, kind) for repo, match, kind in partial_matches}
        if len(unique_partials) == 1:
            return next(iter(unique_partials.values()))
        if len(unique_partials) > 1:
            candidates = ", ".join(f"{repo.config.name}:{match}" for repo, match, _ in unique_partials.values())
            raise ValueError(f"selector '{selector}' is ambiguous: {candidates}")
        raise ValueError(f"selector '{selector}' did not match any package or group")

    def resolve_binding(self, binding_text: str, *, profile: str | None = None) -> tuple[Repository, Binding, str]:
        explicit_repo, selector, selector_profile = parse_binding_text(binding_text)
        repo, resolved_selector, selector_kind = self.resolve_selector(selector, explicit_repo)
        resolved_profile = profile or selector_profile
        if not resolved_profile:
            raise ValueError("profile is required in non-interactive mode")
        return repo, Binding(repo=repo.config.name, selector=resolved_selector, profile=resolved_profile), selector_kind

    def plan_apply(self, binding_text: str, *, profile: str | None = None) -> BindingPlan:
        repo, binding, selector_kind = self.resolve_binding(binding_text, profile=profile)
        return self._build_plan(repo, binding, selector_kind, operation="apply")

    def plan_import(self, binding_text: str, *, profile: str | None = None) -> BindingPlan:
        repo, binding, selector_kind = self.resolve_binding(binding_text, profile=profile)
        return self._build_plan(repo, binding, selector_kind, operation="import")

    def resolve_tracked_binding(self, binding_text: str) -> tuple[Repository, Binding]:
        explicit_repo, selector, profile = parse_binding_text(binding_text)
        candidate_repos = [self.get_repo(explicit_repo)] if explicit_repo else [self.repos[repo.name] for repo in self.config.ordered_repos]
        tracked = [
            (repo, binding)
            for repo in candidate_repos
            for binding in self.read_bindings(repo)
            if profile is None or binding.profile == profile
        ]

        binding_label = selector if profile is None else f"{selector}@{profile}"
        exact_matches = [(repo, binding) for repo, binding in tracked if binding.selector == selector]
        if len(exact_matches) == 1:
            return exact_matches[0]
        if len(exact_matches) > 1:
            candidates = ", ".join(
                f"{repo.config.name}:{binding.selector}@{binding.profile}"
                for repo, binding in exact_matches
            )
            raise ValueError(f"binding '{binding_label}' is ambiguous: {candidates}")

        partial_matches = [(repo, binding) for repo, binding in tracked if selector in binding.selector]
        if len(partial_matches) == 1:
            return partial_matches[0]
        if len(partial_matches) > 1:
            candidates = ", ".join(
                f"{repo.config.name}:{binding.selector}@{binding.profile}"
                for repo, binding in partial_matches
            )
            raise ValueError(f"binding '{binding_label}' is ambiguous: {candidates}")

        owner_bindings = self._find_tracked_package_owners(candidate_repos, selector, profile)
        if owner_bindings:
            owners = ", ".join(
                f"{repo.config.name}:{binding.selector}@{binding.profile}"
                for repo, binding in owner_bindings
            )
            required_repo = explicit_repo or owner_bindings[0][0].config.name
            required_ref = f"{required_repo}:{selector}"
            raise ValueError(
                f"cannot remove '{required_ref}': required by tracked bindings: {owners}"
            )

        raise ValueError(f"binding '{binding_label}' is not currently tracked")

    def plan_upgrade(self) -> list[BindingPlan]:
        plans: list[BindingPlan] = []
        for repo_config in self.config.ordered_repos:
            repo = self.get_repo(repo_config.name)
            for binding in self.read_bindings(repo):
                selector_kind = "group" if binding.selector in repo.groups else "package"
                plans.append(self._build_plan(repo, binding, selector_kind, operation="upgrade"))
        return plans

    def list_installed_packages(self) -> list[InstalledPackageSummary]:
        installed: dict[tuple[str, str], InstalledPackageSummary] = {}
        for repo, binding, selector_kind, package_ids in self._iter_installed_bindings():
            binding_summary = InstalledBindingSummary(
                repo=repo.config.name,
                selector=binding.selector,
                profile=binding.profile,
                selector_kind=selector_kind,
            )
            for package_id in package_ids:
                package = repo.resolve_package(package_id)
                key = (repo.config.name, package_id)
                existing = installed.get(key)
                if existing is None:
                    installed[key] = InstalledPackageSummary(
                        repo=repo.config.name,
                        package_id=package_id,
                        description=package.description,
                        bindings=[binding_summary],
                    )
                    continue
                if binding_summary not in existing.bindings:
                    existing.bindings.append(binding_summary)

        return [
            InstalledPackageSummary(
                repo=summary.repo,
                package_id=summary.package_id,
                description=summary.description,
                bindings=sorted(summary.bindings, key=lambda item: (item.selector, item.profile, item.repo)),
            )
            for _key, summary in sorted(installed.items(), key=lambda item: item[0])
        ]

    def describe_installed_package(self, package_text: str) -> InstalledPackageDetail:
        repo, package_id = self._resolve_installed_package(package_text)
        details: list[InstalledPackageBindingDetail] = []
        description = repo.resolve_package(package_id).description

        for candidate_repo, binding, selector_kind, package_ids in self._iter_installed_bindings():
            if candidate_repo.config.name != repo.config.name or package_id not in package_ids:
                continue
            details.append(self._describe_package_binding(candidate_repo, binding, selector_kind, package_id, package_ids))

        if not details:
            raise ValueError(f"package '{repo.config.name}:{package_id}' is not currently installed")

        return InstalledPackageDetail(
            repo=repo.config.name,
            package_id=package_id,
            description=description,
            bindings=sorted(details, key=lambda item: (item.binding.selector, item.binding.profile, item.binding.repo)),
        )

    def read_bindings(self, repo: Repository) -> list[Binding]:
        state_path = repo.config.state_path / "bindings.toml"
        if not state_path.exists():
            return []
        payload = tomllib.loads(state_path.read_text(encoding="utf-8"))
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

    def record_binding(self, binding: Binding) -> None:
        repo = self.get_repo(binding.repo)
        bindings = self.read_bindings(repo)
        updated = False
        normalized: list[Binding] = []
        for existing in bindings:
            if existing.repo == binding.repo and existing.selector == binding.selector:
                if not updated:
                    normalized.append(binding)
                    updated = True
                continue
            normalized.append(existing)
        if not updated:
            normalized.append(binding)
        self.write_bindings(repo, normalized)

    def remove_binding(self, binding_text: str) -> Binding:
        repo, binding = self.resolve_tracked_binding(binding_text)
        remaining = [
            existing
            for existing in self.read_bindings(repo)
            if not (
                existing.repo == binding.repo
                and existing.selector == binding.selector
                and existing.profile == binding.profile
            )
        ]
        self.write_bindings(repo, remaining)
        return binding

    def _find_tracked_package_owners(
        self,
        candidate_repos: list[Repository],
        selector: str,
        profile: str | None,
    ) -> list[tuple[Repository, Binding]]:
        owners: list[tuple[Repository, Binding]] = []
        candidate_repo_names = {repo.config.name for repo in candidate_repos}
        for repo, binding, _selector_kind, package_ids in self._iter_installed_bindings():
            if repo.config.name not in candidate_repo_names:
                continue
            if profile is not None and binding.profile != profile:
                continue
            if selector in package_ids and (repo, binding) not in owners:
                owners.append((repo, binding))
        return owners

    def write_bindings(self, repo: Repository, bindings: list[Binding]) -> None:
        state_dir = repo.config.state_path
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

    def _iter_installed_bindings(self) -> list[tuple[Repository, Binding, str, list[str]]]:
        installed_bindings: list[tuple[Repository, Binding, str, list[str]]] = []
        for repo_config in self.config.ordered_repos:
            repo = self.get_repo(repo_config.name)
            for binding in self.read_bindings(repo):
                selector_kind = "group" if binding.selector in repo.groups else "package"
                installed_bindings.append((repo, binding, selector_kind, self._resolve_package_ids(repo, binding.selector, selector_kind)))
        return installed_bindings

    def _resolve_installed_package(self, package_text: str) -> tuple[Repository, str]:
        explicit_repo, selector, profile = parse_binding_text(package_text)
        if profile is not None:
            raise ValueError("installed show expects a package selector, not a binding")

        candidate_repos = [self.get_repo(explicit_repo)] if explicit_repo else [self.repos[repo.name] for repo in self.config.ordered_repos]
        installed_ids = {
            (repo.config.name, package_id): repo
            for repo, _binding, _selector_kind, package_ids in self._iter_installed_bindings()
            if repo in candidate_repos
            for package_id in package_ids
        }

        exact_matches = [(repo, package_id) for (repo_name, package_id), repo in installed_ids.items() if package_id == selector and repo_name == repo.config.name]
        if len(exact_matches) == 1:
            return exact_matches[0]
        if len(exact_matches) > 1:
            candidates = ", ".join(f"{repo.config.name}:{package_id}" for repo, package_id in exact_matches)
            raise ValueError(f"installed package '{selector}' is defined in multiple repos: {candidates}")

        partial_matches = [(repo, package_id) for (_repo_name, package_id), repo in installed_ids.items() if selector in package_id]
        unique_partials = {(repo.config.name, package_id): (repo, package_id) for repo, package_id in partial_matches}
        if len(unique_partials) == 1:
            return next(iter(unique_partials.values()))
        if len(unique_partials) > 1:
            candidates = ", ".join(f"{repo.config.name}:{package_id}" for repo, package_id in unique_partials.values())
            raise ValueError(f"installed package '{selector}' is ambiguous: {candidates}")
        raise ValueError(f"installed package '{selector}' did not match any tracked package")

    def _describe_package_binding(
        self,
        repo: Repository,
        binding: Binding,
        selector_kind: str,
        package_id: str,
        package_ids: list[str],
    ) -> InstalledPackageBindingDetail:
        resolved_packages = [repo.resolve_package(candidate_id) for candidate_id in package_ids]
        profile_vars, lineage = repo.compose_profile(binding.profile)
        package_vars: dict[str, Any] = {}
        for package in resolved_packages:
            package_vars = deep_merge(package_vars, package.vars or {})
        variables = deep_merge(deep_merge(package_vars, profile_vars), repo.local_vars)
        inferred_os = infer_profile_os(binding.profile, lineage, variables)
        context = build_template_context(variables, profile=binding.profile, inferred_os=inferred_os)
        package = repo.resolve_package(package_id)
        hooks = self._plan_hooks(repo, [package], context)
        targets = self._summarize_targets(repo, package, context)

        return InstalledPackageBindingDetail(
            binding=InstalledBindingSummary(
                repo=repo.config.name,
                selector=binding.selector,
                profile=binding.profile,
                selector_kind=selector_kind,
            ),
            targets=targets,
            hooks=hooks,
        )

    def _summarize_targets(
        self,
        repo: Repository,
        package: PackageSpec,
        context: dict[str, Any],
    ) -> list[InstalledTargetSummary]:
        target_summaries: list[InstalledTargetSummary] = []
        for target in (package.targets or {}).values():
            if target.source is None or target.path is None:
                raise ValueError(f"target '{package.id}:{target.name}' must define source and path")
            rendered_source = render_template_string(target.source, context, base_dir=target.declared_in)
            rendered_path = render_template_string(target.path, context, base_dir=target.declared_in)
            repo_path = (target.declared_in / rendered_source).resolve()
            live_path = expand_path(rendered_path)
            render_command = (
                render_template_string(target.render, context, base_dir=target.declared_in)
                if target.render is not None
                else None
            )
            capture_command = (
                render_template_string(target.capture, context, base_dir=target.declared_in)
                if target.capture is not None
                else None
            )
            reconcile_command = (
                render_template_string(target.reconcile, context, base_dir=target.declared_in)
                if target.reconcile is not None
                else None
            )
            target_summaries.append(
                InstalledTargetSummary(
                    target_name=target.name,
                    repo_path=repo_path,
                    live_path=live_path,
                    target_kind="directory" if repo_path.is_dir() else "file",
                    render_command=render_command,
                    capture_command=capture_command,
                    reconcile_command=reconcile_command,
                    import_view_repo=target.import_view_repo or "raw",
                    import_view_live=target.import_view_live or ("capture" if capture_command else "raw"),
                    apply_ignore=merge_ignore_patterns(repo.ignore_defaults.apply, target.apply_ignore or ()),
                    import_ignore=merge_ignore_patterns(repo.ignore_defaults.import_, target.import_ignore or ()),
                )
            )
        return target_summaries

    def _build_plan(self, repo: Repository, binding: Binding, selector_kind: str, *, operation: str) -> BindingPlan:
        package_ids = self._resolve_package_ids(repo, binding.selector, selector_kind)
        resolved_packages = [repo.resolve_package(package_id) for package_id in package_ids]
        profile_vars, lineage = repo.compose_profile(binding.profile)
        package_vars: dict[str, Any] = {}
        for package in resolved_packages:
            package_vars = deep_merge(package_vars, package.vars or {})
        variables = deep_merge(deep_merge(package_vars, profile_vars), repo.local_vars)
        inferred_os = infer_profile_os(binding.profile, lineage, variables)
        context = build_template_context(variables, profile=binding.profile, inferred_os=inferred_os)
        hooks = self._plan_hooks(repo, resolved_packages, context)
        target_plans = self._plan_targets(
            repo=repo,
            packages=resolved_packages,
            context=context,
            binding=binding,
            operation=operation,
            inferred_os=inferred_os,
        )
        return BindingPlan(
            operation=operation,
            binding=binding,
            selector_kind=selector_kind,
            package_ids=package_ids,
            variables=variables,
            hooks=hooks,
            target_plans=target_plans,
        )

    def _resolve_package_ids(self, repo: Repository, selector: str, selector_kind: str) -> list[str]:
        roots = [selector] if selector_kind == "package" else repo.expand_group(selector)
        ordered: list[str] = []
        seen: set[str] = set()

        def visit(package_id: str) -> None:
            if package_id in seen:
                return
            seen.add(package_id)
            ordered.append(package_id)
            for dependency in repo.resolve_package(package_id).depends or ():
                visit(dependency)

        for root_package in roots:
            visit(root_package)
        return ordered

    def _plan_hooks(
        self,
        repo: Repository,
        packages: list[PackageSpec],
        context: dict[str, Any],
    ) -> dict[str, list[HookPlan]]:
        hooks: dict[str, list[HookPlan]] = defaultdict(list)
        for package in packages:
            for hook_name, hook_spec in (package.hooks or {}).items():
                for command in hook_spec.commands:
                    hooks[hook_name].append(
                        HookPlan(
                            package_id=package.id,
                            hook_name=hook_name,
                            command=render_template_string(command, context, base_dir=hook_spec.declared_in).strip(),
                            cwd=hook_spec.declared_in,
                        )
                    )
        return dict(hooks)

    def _plan_targets(
        self,
        *,
        repo: Repository,
        packages: list[PackageSpec],
        context: dict[str, Any],
        binding: Binding,
        operation: str,
        inferred_os: str,
    ) -> list[TargetPlan]:
        rendered_targets: list[tuple[PackageSpec, TargetSpec, Path, Path, tuple[str, ...], tuple[str, ...]]] = []
        for package in packages:
            for target in (package.targets or {}).values():
                if target.source is None or target.path is None:
                    raise ValueError(f"target '{package.id}:{target.name}' must define source and path")
                rendered_source = render_template_string(target.source, context, base_dir=target.declared_in)
                rendered_path = render_template_string(target.path, context, base_dir=target.declared_in)
                repo_path = (target.declared_in / rendered_source).resolve()
                live_path = expand_path(rendered_path)
                rendered_targets.append(
                    (
                        package,
                        target,
                        repo_path,
                        live_path,
                        merge_ignore_patterns(repo.ignore_defaults.apply, target.apply_ignore or ()),
                        merge_ignore_patterns(repo.ignore_defaults.import_, target.import_ignore or ()),
                    )
                )

        self._validate_target_collisions(rendered_targets)
        self._validate_reserved_path_conflicts(packages, rendered_targets, context)

        plans: list[TargetPlan] = []
        for package, target, repo_path, live_path, apply_ignore, import_ignore in rendered_targets:
            render_command = (
                render_template_string(target.render, context, base_dir=target.declared_in)
                if target.render is not None
                else None
            )
            capture_command = (
                render_template_string(target.capture, context, base_dir=target.declared_in)
                if target.capture is not None
                else None
            )
            reconcile_command = (
                render_template_string(target.reconcile, context, base_dir=target.declared_in)
                if target.reconcile is not None
                else None
            )
            if repo_path.is_dir():
                action = self._plan_directory_action(repo_path, live_path, apply_ignore, import_ignore)
                plans.append(
                    TargetPlan(
                        package_id=package.id,
                        target_name=target.name,
                        repo_path=repo_path,
                        live_path=live_path,
                        action=action,
                        target_kind="directory",
                        projection_kind="directory",
                        render_command=render_command,
                        capture_command=capture_command,
                        reconcile_command=reconcile_command,
                        import_view_repo=target.import_view_repo or "raw",
                        import_view_live=target.import_view_live or ("capture" if capture_command else "raw"),
                        apply_ignore=apply_ignore,
                        import_ignore=import_ignore,
                    )
                )
                continue

            projection_error: str | None = None
            desired_bytes: bytes | None = None
            projection_kind = "raw"
            try:
                desired_bytes, projection_kind = self._project_repo_file(
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
            except ValueError as exc:
                if operation in {"apply", "upgrade"} and not live_path.exists():
                    projection_error = str(exc)
                    projection_kind = "command"
                else:
                    raise
            import_view_repo = target.import_view_repo or "raw"
            import_view_live = target.import_view_live or ("capture" if capture_command else "raw")
            action = self._plan_file_action(
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
                import_view_repo=import_view_repo,
                import_view_live=import_view_live,
            )
            desired_text = None
            if desired_bytes is not None:
                try:
                    desired_text = desired_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    desired_text = None
            plans.append(
                TargetPlan(
                    package_id=package.id,
                    target_name=target.name,
                    repo_path=repo_path,
                    live_path=live_path,
                    action=action,
                    target_kind="file",
                    projection_kind=projection_kind,
                    desired_text=desired_text,
                    render_command=render_command,
                    capture_command=capture_command,
                    reconcile_command=reconcile_command,
                    projection_error=projection_error,
                    import_view_repo=import_view_repo,
                    import_view_live=import_view_live,
                    apply_ignore=apply_ignore,
                    import_ignore=import_ignore,
                    desired_bytes=desired_bytes,
                )
            )
        return plans

    def _validate_target_collisions(
        self,
        rendered_targets: list[tuple[PackageSpec, TargetSpec, Path, Path, tuple[str, ...], tuple[str, ...]]],
    ) -> None:
        for index, (package, target, _repo_path, live_path, apply_ignore, import_ignore) in enumerate(rendered_targets):
            for (
                other_package,
                other_target,
                _other_repo_path,
                other_live_path,
                other_apply_ignore,
                other_import_ignore,
            ) in rendered_targets[index + 1 :]:
                if live_path == other_live_path:
                    raise ValueError(
                        f"conflicting target ownership: {package.id}:{target.name} and {other_package.id}:{other_target.name} both map to {live_path}"
                    )
                if live_path in other_live_path.parents:
                    relative = other_live_path.relative_to(live_path).as_posix()
                    parent_ignore = set(apply_ignore) | set(import_ignore)
                    if not any(matches_ignore_pattern(relative, pattern) for pattern in parent_ignore):
                        raise ValueError(
                            f"incompatible nested targets: {package.id}:{target.name} contains {other_package.id}:{other_target.name}"
                        )
                elif other_live_path in live_path.parents:
                    relative = live_path.relative_to(other_live_path).as_posix()
                    parent_ignore = set(other_apply_ignore) | set(other_import_ignore)
                    if not any(matches_ignore_pattern(relative, pattern) for pattern in parent_ignore):
                        raise ValueError(
                            f"incompatible nested targets: {other_package.id}:{other_target.name} contains {package.id}:{target.name}"
                        )

    def _validate_reserved_path_conflicts(
        self,
        packages: list[PackageSpec],
        rendered_targets: list[tuple[PackageSpec, TargetSpec, Path, Path, tuple[str, ...], tuple[str, ...]]],
        context: dict[str, Any],
    ) -> None:
        target_claims = [
            (package.id, f"{package.id}:{target.name}", live_path)
            for package, target, _repo_path, live_path, _apply_ignore, _import_ignore in rendered_targets
        ]
        reserved_claims: list[tuple[str, Path]] = []
        for package in packages:
            for reserved_path in package.reserved_paths or ():
                rendered_path = render_template_string(reserved_path, context, base_dir=package.package_root)
                reserved_claims.append((package.id, expand_path(rendered_path)))

        for package_id, reserved_path in reserved_claims:
            for target_package_id, target_label, target_path in target_claims:
                if package_id == target_package_id:
                    continue
                if self._paths_conflict(reserved_path, target_path):
                    raise ValueError(
                        f"reserved path conflict: {package_id} reserves {reserved_path} and {target_label} maps to {target_path}"
                    )

        for index, (package_id, reserved_path) in enumerate(reserved_claims):
            for other_package_id, other_reserved_path in reserved_claims[index + 1 :]:
                if package_id == other_package_id:
                    continue
                if self._paths_conflict(reserved_path, other_reserved_path):
                    raise ValueError(
                        f"reserved path conflict: {package_id} reserves {reserved_path} and {other_package_id} reserves {other_reserved_path}"
                    )

    def _paths_conflict(self, left: Path, right: Path) -> bool:
        return left == right or left in right.parents or right in left.parents

    def _project_repo_file(
        self,
        *,
        repo: Repository,
        package: PackageSpec,
        target: TargetSpec,
        repo_path: Path,
        live_path: Path,
        render_command: str | None,
        context: dict[str, Any],
        binding: Binding,
        operation: str,
        inferred_os: str,
    ) -> tuple[bytes, str]:
        if render_command:
            return (
                self._run_command_projection(
                    repo=repo,
                    package=package,
                    target=target,
                    repo_path=repo_path,
                    live_path=live_path,
                    command=render_command,
                    binding=binding,
                    operation=operation,
                    inferred_os=inferred_os,
                    context=context,
                ),
                "command",
            )
        return render_template_file(repo_path, context)

    def _plan_directory_action(
        self,
        repo_path: Path,
        live_path: Path,
        apply_ignore: tuple[str, ...],
        import_ignore: tuple[str, ...],
    ) -> str:
        desired_files = list_directory_files(repo_path, apply_ignore)
        if not live_path.exists():
            return "install"
        live_files = list_directory_files(live_path, import_ignore)
        desired_rel_paths = set(desired_files)
        live_rel_paths = set(live_files)
        if desired_rel_paths != live_rel_paths:
            return "update"
        for relative_path, source_path in desired_files.items():
            live_file = live_files[relative_path]
            desired_bytes, _projection_kind = render_template_file(source_path, {})
            if desired_bytes != live_file.read_bytes():
                return "update"
        return "noop"

    def _plan_file_action(
        self,
        *,
        repo: Repository,
        package: PackageSpec,
        target: TargetSpec,
        repo_path: Path,
        live_path: Path,
        desired_bytes: bytes | None,
        render_command: str | None,
        capture_command: str | None,
        context: dict[str, Any],
        binding: Binding,
        operation: str,
        inferred_os: str,
        import_view_repo: str,
        import_view_live: str,
    ) -> str:
        if operation in {"apply", "upgrade"}:
            if not live_path.exists():
                return "install"
            if desired_bytes is None:
                return "unknown"
            return "noop" if desired_bytes == live_path.read_bytes() else "update"

        if not live_path.exists():
            return "missing"
        repo_bytes = self._import_view_bytes(
            repo=repo,
            package=package,
            target=target,
            repo_path=repo_path,
            live_path=live_path,
            view=import_view_repo,
            repo_side=True,
            render_command=render_command,
            capture_command=capture_command,
            context=context,
            binding=binding,
            operation=operation,
            inferred_os=inferred_os,
        )
        live_bytes = self._import_view_bytes(
            repo=repo,
            package=package,
            target=target,
            repo_path=repo_path,
            live_path=live_path,
            view=import_view_live,
            repo_side=False,
            render_command=render_command,
            capture_command=capture_command,
            context=context,
            binding=binding,
            operation=operation,
            inferred_os=inferred_os,
        )
        return "noop" if repo_bytes == live_bytes else "update"

    def _import_view_bytes(
        self,
        *,
        repo: Repository,
        package: PackageSpec,
        target: TargetSpec,
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
        if view == "raw":
            return repo_path.read_bytes() if repo_side else live_path.read_bytes()
        if view == "render":
            desired_bytes, _projection = self._project_repo_file(
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
            return desired_bytes
        if view == "capture":
            if capture_command is None:
                raise ValueError(f"target '{package.id}:{target.name}' does not define capture")
            return self._run_command_projection(
                repo=repo,
                package=package,
                target=target,
                repo_path=repo_path,
                live_path=live_path,
                command=capture_command,
                binding=binding,
                operation=operation,
                inferred_os=inferred_os,
                context=context,
            )
        command = render_template_string(view, context, base_dir=target.declared_in)
        return self._run_command_projection(
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

    def _run_command_projection(
        self,
        *,
        repo: Repository,
        package: PackageSpec,
        target: TargetSpec,
        repo_path: Path,
        live_path: Path,
        command: str,
        binding: Binding,
        operation: str,
        inferred_os: str,
        context: dict[str, Any],
    ) -> bytes:
        env = os.environ.copy()
        env.update(
            {
                "DOTMAN_REPO_NAME": repo.config.name,
                "DOTMAN_REPO_ROOT": str(repo.root),
                "DOTMAN_STATE_PATH": str(repo.config.state_path),
                "DOTMAN_PACKAGE_ID": package.id,
                "DOTMAN_PACKAGE_ROOT": str(package.package_root),
                "DOTMAN_TARGET_NAME": target.name,
                "DOTMAN_REPO_PATH": str(repo_path),
                "DOTMAN_SOURCE": str(repo_path),
                "DOTMAN_LIVE_PATH": str(live_path),
                "DOTMAN_PROFILE": binding.profile,
                "DOTMAN_OPERATION": operation,
                "DOTMAN_OS": inferred_os,
            }
        )
        for flat_key, value in flatten_vars(context["vars"]).items():
            env[f"DOTMAN_VAR_{flat_key}"] = value
        completed = subprocess.run(
            command,
            cwd=str(target.declared_in),
            env=env,
            shell=True,
            executable="/bin/sh",
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace")
            raise ValueError(f"command projection failed for {package.id}:{target.name}: {stderr.strip()}")
        return completed.stdout


__all__ = [
    "DotmanEngine",
    "compute_profile_heights",
    "rank_profiles",
]
