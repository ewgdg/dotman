from __future__ import annotations

import fnmatch
import os
import subprocess
import sys
import tomllib
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any

from dotman.config import default_state_root, expand_path, load_manager_config
from dotman.models import (
    Binding,
    BindingPlan,
    DirectoryPlanItem,
    filter_hook_plans_for_targets,
    GroupSpec,
    HookPlan,
    HookSpec,
    InstalledBindingSummary,
    InstalledPackageBindingDetail,
    InstalledPackageDetail,
    InstalledPackageSummary,
    InstalledOwnedTargetDetail,
    InstalledTargetSummary,
    ManagerConfig,
    PackageSpec,
    ProfileSpec,
    RepoConfig,
    RepoIgnoreDefaults,
    TrackedBindingIssue,
    package_ref_text,
    TargetPlan,
    TargetSpec,
)
from dotman.presets import BUILTIN_TARGET_PRESETS, get_builtin_target_preset
from dotman.profiles import compute_profile_heights, rank_profiles
from dotman.templates import build_template_context, render_template_file, render_template_string


VALID_HOOK_NAMES = (
    "guard_push",
    "pre_push",
    "post_push",
    "guard_pull",
    "pre_pull",
    "post_pull",
)
VALID_RECONCILE_IO_VALUES = ("pipe", "tty")
HOOK_NAMES_BY_OPERATION = {
    "push": ("guard_push", "pre_push", "post_push"),
    "pull": ("guard_pull", "pre_pull", "post_pull"),
    "upgrade": ("guard_push", "pre_push", "post_push"),
}


class TrackedTargetConflictError(ValueError):
    def __init__(
        self,
        *,
        live_path: Path,
        precedence: str,
        contenders: list[str],
        candidates: list["TrackedTargetCandidate"],
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
    packages: list[InstalledPackageSummary]
    invalid_bindings: list[TrackedBindingIssue]


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


def normalize_optional_string_enum(value: Any, *, key: str, allowed: tuple[str, ...]) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"expected string for '{key}', got {type(value).__name__}")
    if value not in allowed:
        allowed_text = ", ".join(allowed)
        raise ValueError(f"unsupported {key} '{value}'; expected one of: {allowed_text}")
    return value


def read_schema_alias(payload: dict[str, Any], primary_key: str, legacy_key: str) -> Any:
    primary_value = payload.get(primary_key)
    legacy_value = payload.get(legacy_key)
    if primary_value is not None and legacy_value is not None and primary_value != legacy_value:
        raise ValueError(f"conflicting schema keys '{primary_key}' and legacy '{legacy_key}'")
    if primary_value is not None:
        return primary_value
    return legacy_value



def resolve_target_preset(
    *,
    target_payload: dict[str, Any],
    manifest_path: Path,
    target_name: str,
) -> dict[str, Any]:
    preset_name = target_payload.get("preset")
    if preset_name is None:
        return {}
    if not isinstance(preset_name, str):
        raise ValueError(
            f"package manifest {manifest_path} target '{target_name}' preset must be a string"
        )
    preset = get_builtin_target_preset(preset_name)
    if preset is None:
        available = ", ".join(sorted(BUILTIN_TARGET_PRESETS))
        raise ValueError(
            f"package manifest {manifest_path} target '{target_name}' uses unknown preset '{preset_name}'; "
            f"available presets: {available}"
        )
    return preset



def get_target_value(
    *,
    target_payload: dict[str, Any],
    preset_payload: dict[str, Any],
    key: str,
) -> Any:
    if key in target_payload:
        return target_payload[key]
    return preset_payload.get(key)



def read_target_schema_alias(
    *,
    target_payload: dict[str, Any],
    preset_payload: dict[str, Any],
    primary_key: str,
    legacy_key: str,
) -> Any:
    # Presets are a default layer. Resolve explicit aliases first so a user can
    # override a preset with either the current key or its legacy schema alias.
    explicit_value = read_schema_alias(target_payload, primary_key, legacy_key)
    if explicit_value is not None:
        return explicit_value
    return preset_payload.get(primary_key)



def build_target_spec(
    *,
    target_name: str,
    target_payload: dict[str, Any],
    manifest_path: Path,
) -> TargetSpec:
    preset_payload = resolve_target_preset(
        target_payload=target_payload,
        manifest_path=manifest_path,
        target_name=target_name,
    )
    return TargetSpec(
        name=target_name,
        declared_in=manifest_path.parent,
        source=get_target_value(target_payload=target_payload, preset_payload=preset_payload, key="source"),
        path=get_target_value(target_payload=target_payload, preset_payload=preset_payload, key="path"),
        chmod=get_target_value(target_payload=target_payload, preset_payload=preset_payload, key="chmod"),
        render=get_target_value(target_payload=target_payload, preset_payload=preset_payload, key="render"),
        capture=get_target_value(target_payload=target_payload, preset_payload=preset_payload, key="capture"),
        reconcile=get_target_value(target_payload=target_payload, preset_payload=preset_payload, key="reconcile"),
        reconcile_io=normalize_optional_string_enum(
            get_target_value(target_payload=target_payload, preset_payload=preset_payload, key="reconcile_io"),
            key="reconcile_io",
            allowed=VALID_RECONCILE_IO_VALUES,
        ),
        pull_view_repo=read_target_schema_alias(
            target_payload=target_payload,
            preset_payload=preset_payload,
            primary_key="pull_view_repo",
            legacy_key="import_view_repo",
        ),
        pull_view_live=read_target_schema_alias(
            target_payload=target_payload,
            preset_payload=preset_payload,
            primary_key="pull_view_live",
            legacy_key="import_view_live",
        ),
        push_ignore=normalize_string_list(
            read_target_schema_alias(
                target_payload=target_payload,
                preset_payload=preset_payload,
                primary_key="push_ignore",
                legacy_key="apply_ignore",
            )
        ),
        pull_ignore=normalize_string_list(
            read_target_schema_alias(
                target_payload=target_payload,
                preset_payload=preset_payload,
                primary_key="pull_ignore",
                legacy_key="import_ignore",
            )
        ),
        disabled=bool(get_target_value(target_payload=target_payload, preset_payload=preset_payload, key="disabled") or False),
    )



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
            push=normalize_string_list(read_schema_alias(ignore_payload, "push", "apply")) or (),
            pull=normalize_string_list(read_schema_alias(ignore_payload, "pull", "import")) or (),
        )

    def _load_packages(self) -> dict[str, PackageSpec]:
        packages: dict[str, PackageSpec] = {}
        for manifest_path in sorted((self.root / "packages").glob("**/package.toml")):
            payload = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
            package_id = payload.get("id")
            if not isinstance(package_id, str):
                raise ValueError(f"package manifest {manifest_path} must define string id")
            binding_mode = str(payload.get("binding_mode", "singleton"))
            if binding_mode not in {"singleton", "multi_instance"}:
                raise ValueError(
                    f"package manifest {manifest_path} has unsupported binding_mode '{binding_mode}'"
                )
            targets_payload = payload.get("targets")
            hooks_payload = payload.get("hooks")
            append_payload = payload.get("append")
            targets = (
                {
                    target_name: build_target_spec(
                        target_name=target_name,
                        target_payload=target_payload,
                        manifest_path=manifest_path,
                    )
                    for target_name, target_payload in targets_payload.items()
                }
                if isinstance(targets_payload, dict)
                else None
            )
            hooks = None
            if isinstance(hooks_payload, dict):
                unknown_hook_names = [hook_name for hook_name in hooks_payload if hook_name not in VALID_HOOK_NAMES]
                if unknown_hook_names:
                    unknown_text = ", ".join(sorted(unknown_hook_names))
                    raise ValueError(
                        f"package manifest {manifest_path} uses unsupported hook names: {unknown_text}"
                    )
                hooks = {
                    hook_name: HookSpec(
                        name=hook_name,
                        commands=normalize_string_list(hook_value) or (),
                        declared_in=manifest_path.parent,
                    )
                    for hook_name, hook_value in hooks_payload.items()
                }
            packages[package_id] = PackageSpec(
                id=package_id,
                package_root=manifest_path.parent,
                description=payload.get("description"),
                binding_mode=binding_mode,
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
        local_path = self.config.local_override_path
        if not local_path.exists():
            return {}
        payload = tomllib.loads(local_path.read_text(encoding="utf-8"))
        unknown_top_level_keys = sorted(key for key in payload if key != "vars")
        if unknown_top_level_keys:
            unknown_text = ", ".join(unknown_top_level_keys)
            raise ValueError(f"local override {local_path} has unknown top-level keys: {unknown_text}")
        vars_payload = payload.get("vars", {})
        if not isinstance(vars_payload, dict):
            raise ValueError(f"local override {local_path} [vars] must be a table")
        return _copy_map(vars_payload)

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
        merged = patch_remove_and_append(merged, loaded.remove or (), loaded.append or {})
        self._resolved_packages[package_id] = merged
        return merged

    def package_binding_mode(self, package_id: str) -> str:
        return self.resolve_package(package_id).binding_mode

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
        reconcile_io=override.reconcile_io if override.reconcile_io is not None else base.reconcile_io,
        pull_view_repo=override.pull_view_repo if override.pull_view_repo is not None else base.pull_view_repo,
        pull_view_live=override.pull_view_live if override.pull_view_live is not None else base.pull_view_live,
        push_ignore=override.push_ignore if override.push_ignore is not None else base.push_ignore,
        pull_ignore=override.pull_ignore if override.pull_ignore is not None else base.pull_ignore,
        disabled=override.disabled or base.disabled,
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
        binding_mode=override.binding_mode,
        depends=override.depends if override.depends is not None else base.depends,
        extends=None,
        reserved_paths=override.reserved_paths if override.reserved_paths is not None else base.reserved_paths,
        vars=deep_merge(base.vars or {}, override.vars or {}),
        targets=targets,
        hooks=hooks,
        remove=override.remove if override.remove is not None else base.remove,
        append=deep_merge(base.append or {}, override.append or {}),
    )


def patch_remove_and_append(package: PackageSpec, remove_paths: tuple[str, ...], append_payload: dict[str, Any]) -> PackageSpec:
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
    def from_config_path(cls, config_path: str | Path | None = None) -> "DotmanEngine":
        return cls(load_manager_config(config_path))

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

    def list_tracked_state(self) -> TrackedStateSummary:
        return TrackedStateSummary(
            packages=self.list_tracked_packages(),
            invalid_bindings=self._sorted_binding_issues(
                [
                    *self.list_invalid_explicit_bindings(),
                    *self.list_orphan_explicit_bindings(),
                ]
            ),
        )

    def list_invalid_explicit_bindings(
        self,
        *,
        bindings_by_repo: dict[str, list[Binding]] | None = None,
    ) -> list[TrackedBindingIssue]:
        _valid_records, invalid_records = self._configured_persisted_binding_records(bindings_by_repo=bindings_by_repo)
        return self._sorted_binding_issues([record.issue for record in invalid_records if record.issue is not None])

    def list_orphan_explicit_bindings(self) -> list[TrackedBindingIssue]:
        return self._sorted_binding_issues([record.issue for record in self._orphan_persisted_binding_records() if record.issue is not None])

    def list_tracked_packages(self) -> list[InstalledPackageSummary]:
        installed: dict[tuple[str, str, str | None], InstalledPackageSummary] = {}
        package_states: dict[tuple[str, str, str | None], str] = {}
        for repo, binding, selector_kind, package_ids in self._iter_tracked_bindings():
            binding_summary = InstalledBindingSummary(
                repo=repo.config.name,
                selector=binding.selector,
                profile=binding.profile,
                selector_kind=selector_kind,
            )
            for package_id in package_ids:
                package = repo.resolve_package(package_id)
                bound_profile = self._bound_profile_for_package(repo, package_id, binding.profile)
                key = (repo.config.name, package_id, bound_profile)
                package_state = "explicit" if selector_kind == "package" and binding.selector == package_id else "implicit"
                existing = installed.get(key)
                if existing is None:
                    installed[key] = InstalledPackageSummary(
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
            InstalledPackageSummary(
                repo=summary.repo,
                package_id=summary.package_id,
                description=summary.description,
                bindings=sorted(summary.bindings, key=lambda item: (item.selector, item.profile, item.repo)),
                state=package_states[key],
                bound_profile=summary.bound_profile,
            )
            for key, summary in sorted(
                installed.items(),
                key=lambda item: (
                    0 if package_states[item[0]] == "explicit" else 1,
                    item[0][0],
                    item[0][1],
                    "" if item[0][2] is None else item[0][2],
                ),
            )
        ]

    def list_installed_packages(self) -> list[InstalledPackageSummary]:
        return self.list_tracked_packages()

    def describe_tracked_package(self, package_text: str) -> InstalledPackageDetail:
        repo, package_id, bound_profile = self._resolve_installed_package(package_text)
        effective_binding_keys = self._effective_package_binding_keys(
            repo.config.name,
            package_id,
            bound_profile,
        )
        details: list[InstalledPackageBindingDetail] = []
        description = repo.resolve_package(package_id).description

        for candidate_repo, binding, selector_kind, package_ids in self._iter_tracked_bindings():
            if candidate_repo.config.name != repo.config.name or package_id not in package_ids:
                continue
            if self._bound_profile_for_package(candidate_repo, package_id, binding.profile) != bound_profile:
                continue
            details.append(
                self._describe_package_binding(
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

        return InstalledPackageDetail(
            repo=repo.config.name,
            package_id=package_id,
            description=description,
            bindings=sorted(details, key=lambda item: (item.binding.selector, item.binding.profile, item.binding.repo)),
            owned_targets=self._describe_owned_package_targets(
                repo.config.name,
                package_id,
                bound_profile,
            ),
            bound_profile=bound_profile,
        )

    def describe_installed_package(self, package_text: str) -> InstalledPackageDetail:
        return self.describe_tracked_package(package_text)

    def _read_bindings_file(self, state_path: Path) -> list[Binding]:
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

    def read_bindings(self, repo: Repository) -> list[Binding]:
        return self._read_bindings_file(repo.config.state_path / "bindings.toml")

    def read_effective_bindings(self, repo: Repository) -> list[Binding]:
        return self._effective_bindings_for_repo(repo, self.read_bindings(repo))

    def expand_binding_for_tracking(self, binding: Binding) -> list[Binding]:
        repo = self.get_repo(binding.repo)
        return self._expand_binding_for_tracking(repo, binding)

    def _raw_bindings_by_repo(self) -> dict[str, list[Binding]]:
        return {
            repo_config.name: self.read_bindings(self.get_repo(repo_config.name))
            for repo_config in self.config.ordered_repos
        }

    def _effective_bindings_by_repo(
        self,
        raw_bindings_by_repo: dict[str, list[Binding]] | None = None,
    ) -> dict[str, list[Binding]]:
        current_raw_bindings = raw_bindings_by_repo or self._raw_bindings_by_repo()
        return {
            repo_config.name: self._effective_bindings_for_repo(
                self.get_repo(repo_config.name),
                current_raw_bindings.get(repo_config.name, []),
            )
            for repo_config in self.config.ordered_repos
        }

    def _binding_scope_key(self, repo: Repository, binding: Binding) -> tuple[str, str, str | None]:
        if binding.selector in repo.packages and repo.package_binding_mode(binding.selector) == "multi_instance":
            return (binding.repo, binding.selector, binding.profile)
        return (binding.repo, binding.selector, None)

    def _bound_profile_for_package(
        self,
        repo: Repository,
        package_id: str,
        binding_profile: str,
    ) -> str | None:
        if repo.package_binding_mode(package_id) == "multi_instance":
            return binding_profile
        return None

    def _normalize_recorded_bindings(self, bindings: list[Binding], binding: Binding) -> list[Binding]:
        repo = self.get_repo(binding.repo)
        target_scope = self._binding_scope_key(repo, binding)
        updated = False
        normalized: list[Binding] = []
        for existing in bindings:
            if self._binding_scope_key(repo, existing) == target_scope:
                if not updated:
                    normalized.append(binding)
                    updated = True
                continue
            normalized.append(existing)
        if not updated:
            normalized.append(binding)
        return normalized

    def _normalize_recorded_binding_set(self, bindings: list[Binding], additions: list[Binding]) -> list[Binding]:
        normalized = list(bindings)
        for binding in additions:
            normalized = self._normalize_recorded_bindings(normalized, binding)
        return normalized

    def _expand_binding_for_tracking(self, repo: Repository, binding: Binding) -> list[Binding]:
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

    def _effective_bindings_for_repo(self, repo: Repository, raw_bindings: list[Binding]) -> list[Binding]:
        effective_bindings: list[Binding] = []
        for binding in raw_bindings:
            try:
                expanded_bindings = self._expand_binding_for_tracking(repo, binding)
            except PersistedBindingResolutionError:
                continue
            effective_bindings = self._normalize_recorded_binding_set(effective_bindings, expanded_bindings)
        return effective_bindings

    def _validate_tracked_bindings(self, bindings_by_repo: dict[str, list[Binding]]) -> None:
        # Tracked-state validity is defined by the resolved push winner set for live targets.
        self._build_tracked_plans(operation="push", bindings_by_repo=bindings_by_repo)

    def record_binding(self, binding: Binding) -> None:
        repo = self.get_repo(binding.repo)
        raw_bindings_by_repo = self._raw_bindings_by_repo()
        normalized = self._normalize_recorded_binding_set(
            self._effective_bindings_for_repo(repo, raw_bindings_by_repo.get(repo.config.name, [])),
            self._expand_binding_for_tracking(repo, binding),
        )
        raw_bindings_by_repo[repo.config.name] = normalized
        if not self.list_invalid_explicit_bindings(bindings_by_repo=raw_bindings_by_repo):
            self._validate_tracked_bindings(self._effective_bindings_by_repo(raw_bindings_by_repo))
        self.write_bindings(repo, normalized)

    def validate_recorded_binding(self, binding: Binding) -> None:
        repo = self.get_repo(binding.repo)
        raw_bindings_by_repo = self._raw_bindings_by_repo()
        raw_bindings_by_repo[repo.config.name] = self._normalize_recorded_binding_set(
            self._effective_bindings_for_repo(repo, raw_bindings_by_repo.get(repo.config.name, [])),
            self._expand_binding_for_tracking(repo, binding),
        )
        if not self.list_invalid_explicit_bindings(bindings_by_repo=raw_bindings_by_repo):
            self._validate_tracked_bindings(self._effective_bindings_by_repo(raw_bindings_by_repo))

    def find_persisted_binding_matches(
        self,
        binding_text: str,
    ) -> tuple[str, str | None, list[PersistedBindingRecord], list[PersistedBindingRecord]]:
        explicit_repo, selector, profile = parse_binding_text(binding_text)
        tracked_records = [
            *self._all_persisted_binding_records(),
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

    def remove_binding(self, binding_text: str, *, operation: str = "untrack") -> Binding:
        selector, profile, exact_matches, partial_matches = self.find_persisted_binding_matches(binding_text)
        binding_label = selector if profile is None else f"{selector}@{profile}"
        if len(exact_matches) == 1:
            return self.remove_persisted_binding(exact_matches[0], operation=operation)
        if len(exact_matches) > 1:
            raise ValueError(
                f"binding '{binding_label}' is ambiguous: {self._format_persisted_binding_candidates(exact_matches)}"
            )

        package_matches, owner_bindings = self._tracked_package_matches_for_untrack(
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
                binding_candidates = self._format_persisted_binding_candidates(partial_matches)
                package_candidates = self._format_tracked_package_candidates(package_matches)
                raise ValueError(
                    f"binding '{binding_label}' is ambiguous: tracked bindings: {binding_candidates}; tracked packages: {package_candidates}"
                )
            if len(partial_matches) == 1:
                record = partial_matches[0]
                raise ValueError(
                    f"no exact match for '{binding_label}'; use exact name '{record.binding.repo}:{record.binding.selector}@{record.binding.profile}'"
                )
            raise ValueError(
                f"binding '{binding_label}' is ambiguous: {self._format_persisted_binding_candidates(partial_matches)}"
            )

        if package_matches:
            if len(package_matches) > 1:
                raise ValueError(
                    f"binding '{binding_label}' is ambiguous: tracked packages: {self._format_tracked_package_candidates(package_matches)}"
                )
            owners = self._format_owner_bindings(owner_bindings)
            required_repo = parse_binding_text(binding_text)[0] or package_matches[0].repo
            required_ref = f"{required_repo}:{selector}"
            raise ValueError(f"cannot {operation} '{required_ref}': required by tracked bindings: {owners}")

        raise ValueError(f"binding '{binding_label}' is not currently tracked")

    def _find_tracked_package_owners(
        self,
        candidate_repos: list[Repository],
        selector: str,
        profile: str | None,
    ) -> list[tuple[Repository, Binding]]:
        owners: list[tuple[Repository, Binding]] = []
        candidate_repo_names = {repo.config.name for repo in candidate_repos}
        for repo, binding, _selector_kind, package_ids in self._iter_tracked_bindings():
            if repo.config.name not in candidate_repo_names:
                continue
            if profile is not None and binding.profile != profile:
                continue
            if selector in package_ids and (repo, binding) not in owners:
                owners.append((repo, binding))
        return owners

    def write_bindings(self, repo: Repository, bindings: list[Binding]) -> None:
        self._write_bindings_file(repo.config.state_path, bindings)

    def _write_bindings_file(self, state_dir: Path, bindings: list[Binding]) -> None:
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

    def remove_persisted_binding(self, record: PersistedBindingRecord, *, operation: str = "untrack") -> Binding:
        state_path = record.state_dir / "bindings.toml"
        if record.repo is not None and record.issue is None:
            raw_bindings_by_repo = self._raw_bindings_by_repo()
            remaining = self._remove_binding_record(self.read_effective_bindings(record.repo), record.binding)
            raw_bindings_by_repo[record.repo.config.name] = remaining
            if not self.list_invalid_explicit_bindings(bindings_by_repo=raw_bindings_by_repo):
                try:
                    self._validate_tracked_bindings(self._effective_bindings_by_repo(raw_bindings_by_repo))
                except TrackedTargetConflictError as exc:
                    binding_label = f"{record.binding.repo}:{record.binding.selector}@{record.binding.profile}"
                    raise ValueError(
                        f"cannot {operation} '{binding_label}': removing this binding would expose {exc}"
                    ) from None
            self._write_bindings_file(record.state_dir, remaining)
            return record.binding

        remaining = self._remove_binding_record(self._read_bindings_file(state_path), record.binding)
        if record.repo is not None:
            raw_bindings_by_repo = self._raw_bindings_by_repo()
            raw_bindings_by_repo[record.repo.config.name] = remaining
            if not self.list_invalid_explicit_bindings(bindings_by_repo=raw_bindings_by_repo):
                try:
                    self._validate_tracked_bindings(self._effective_bindings_by_repo(raw_bindings_by_repo))
                except TrackedTargetConflictError as exc:
                    binding_label = f"{record.binding.repo}:{record.binding.selector}@{record.binding.profile}"
                    raise ValueError(
                        f"cannot {operation} '{binding_label}': removing this binding would expose {exc}"
                    ) from None
        self._write_bindings_file(record.state_dir, remaining)
        return record.binding

    def _remove_binding_record(self, bindings: list[Binding], target: Binding) -> list[Binding]:
        removed = False
        remaining: list[Binding] = []
        for binding in bindings:
            if not removed and binding == target:
                removed = True
                continue
            remaining.append(binding)
        return remaining

    def _iter_tracked_bindings(self) -> list[tuple[Repository, Binding, str, list[str]]]:
        valid_records, _invalid_records = self._configured_persisted_binding_records()
        return [
            (record.repo, record.binding, record.selector_kind or "package", list(record.package_ids))
            for record in valid_records
            if record.repo is not None
        ]

    def _iter_installed_bindings(self) -> list[tuple[Repository, Binding, str, list[str]]]:
        return self._iter_tracked_bindings()

    def _configured_persisted_binding_records(
        self,
        *,
        bindings_by_repo: dict[str, list[Binding]] | None = None,
    ) -> tuple[list[PersistedBindingRecord], list[PersistedBindingRecord]]:
        valid_records: list[PersistedBindingRecord] = []
        invalid_records: list[PersistedBindingRecord] = []
        current_bindings = bindings_by_repo or self._raw_bindings_by_repo()
        for repo_config in self.config.ordered_repos:
            repo = self.get_repo(repo_config.name)
            for binding in current_bindings.get(repo_config.name, []):
                try:
                    resolved_bindings = self._resolve_persisted_binding(repo, binding)
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
                            package_ids=tuple(self._resolve_package_ids(repo, resolved_binding.selector, "package")),
                        )
                    )
        return valid_records, invalid_records

    def _orphan_persisted_binding_records(self) -> list[PersistedBindingRecord]:
        state_root = default_state_root() / "repos"
        if not state_root.exists():
            return []
        configured_state_keys = {repo_config.state_key for repo_config in self.config.ordered_repos}
        orphan_records: list[PersistedBindingRecord] = []
        for state_dir in sorted(path for path in state_root.iterdir() if path.is_dir()):
            if state_dir.name in configured_state_keys:
                continue
            state_path = state_dir / "bindings.toml"
            if not state_path.exists():
                continue
            for binding in self._read_bindings_file(state_path):
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

    def _all_persisted_binding_records(self) -> list[PersistedBindingRecord]:
        valid_records, invalid_records = self._configured_persisted_binding_records()
        return [*valid_records, *invalid_records, *self._orphan_persisted_binding_records()]

    def _resolve_persisted_binding(self, repo: Repository, binding: Binding) -> list[Binding]:
        resolved_bindings = self._expand_binding_for_tracking(repo, binding)
        try:
            for resolved_binding in resolved_bindings:
                self._resolve_package_ids(repo, resolved_binding.selector, "package")
        except ValueError as exc:
            raise PersistedBindingResolutionError(
                reason="dependency_resolution_failed",
                message="dependency resolution failed",
            ) from exc
        return resolved_bindings

    def _tracked_package_matches_for_untrack(
        self,
        *,
        selector: str,
        profile: str | None,
        repo_name: str | None,
    ) -> tuple[list[InstalledPackageSummary], list[InstalledBindingSummary]]:
        package_matches: list[InstalledPackageSummary] = []
        owner_bindings: dict[tuple[str, str, str], InstalledBindingSummary] = {}
        if repo_name is not None and repo_name not in self.repos:
            return package_matches, []
        candidate_repo_names = set(self.repos) if repo_name is None else {repo_name}
        for package in self.list_tracked_packages():
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

    def _sorted_binding_issues(self, issues: list[TrackedBindingIssue]) -> list[TrackedBindingIssue]:
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

    def _format_persisted_binding_candidates(self, records: list[PersistedBindingRecord]) -> str:
        return ", ".join(
            f"{record.binding.repo}:{record.binding.selector}@{record.binding.profile}"
            for record in records
        )

    def _format_tracked_package_candidates(self, packages: list[InstalledPackageSummary]) -> str:
        return ", ".join(
            f"{package.repo}:{package.package_ref}"
            for package in packages
        )

    def _format_owner_bindings(self, bindings: list[InstalledBindingSummary]) -> str:
        return ", ".join(
            f"{binding.repo}:{binding.selector}@{binding.profile}"
            for binding in bindings
        )

    def _selected_package_ids(self, repo: Repository, selector: str, selector_kind: str) -> list[str]:
        return [selector] if selector_kind == "package" else repo.expand_group(selector)

    def _resolve_installed_package(self, package_text: str) -> tuple[Repository, str, str | None]:
        selector, bound_profile, exact_matches, partial_matches = self.find_installed_package_matches(package_text)
        if len(exact_matches) == 1:
            return exact_matches[0]
        if len(exact_matches) > 1:
            candidates = ", ".join(
                f"{repo.config.name}:{package_ref_text(package_id=package_id, bound_profile=match_bound_profile)}"
                for repo, package_id, match_bound_profile in exact_matches
            )
            if len({repo.config.name for repo, _package_id, _match_bound_profile in exact_matches}) > 1:
                raise ValueError(f"tracked package '{selector}' is defined in multiple repos: {candidates}")
            raise ValueError(f"tracked package '{selector}' is ambiguous: {candidates}")

        if len(partial_matches) == 1:
            repo, package_id, match_bound_profile = partial_matches[0]
            raise ValueError(
                f"no exact match for '{selector}'; use exact name '"
                f"{repo.config.name}:{package_ref_text(package_id=package_id, bound_profile=match_bound_profile)}'"
            )
        if len(partial_matches) > 1:
            candidates = ", ".join(
                f"{repo.config.name}:{package_ref_text(package_id=package_id, bound_profile=match_bound_profile)}"
                for repo, package_id, match_bound_profile in partial_matches
            )
            raise ValueError(f"tracked package '{selector}' is ambiguous: {candidates}")
        raise ValueError(f"tracked package '{selector}' did not match any tracked package")

    def find_installed_package_matches(
        self,
        package_text: str,
    ) -> tuple[str, str | None, list[tuple[Repository, str, str | None]], list[tuple[Repository, str, str | None]]]:
        explicit_repo, selector, bound_profile = parse_package_ref_text(package_text)
        candidate_repos = self.candidate_repos(explicit_repo)
        installed_ids = {
            (
                repo.config.name,
                package_id,
                self._bound_profile_for_package(repo, package_id, binding.profile),
            ): repo
            for repo, binding, _selector_kind, package_ids in self._iter_installed_bindings()
            if repo in candidate_repos
            for package_id in package_ids
        }
        exact_matches = [
            (repo, package_id, match_bound_profile)
            for (repo_name, package_id, match_bound_profile), repo in installed_ids.items()
            if package_id == selector and repo_name == repo.config.name
            and (bound_profile is None or match_bound_profile == bound_profile)
        ]
        partial_matches = [
            (repo, package_id, match_bound_profile)
            for (_repo_name, package_id, match_bound_profile), repo in installed_ids.items()
            if selector in package_ref_text(package_id=package_id, bound_profile=match_bound_profile)
            and (bound_profile is None or match_bound_profile == bound_profile)
        ]
        unique_partials = {
            (repo.config.name, package_id, match_bound_profile): (repo, package_id, match_bound_profile)
            for repo, package_id, match_bound_profile in partial_matches
        }
        return selector, bound_profile, exact_matches, list(unique_partials.values())

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
        resolved_packages = [repo.resolve_package(candidate_id) for candidate_id in package_ids]
        profile_vars, lineage = repo.compose_profile(binding.profile)
        package_vars: dict[str, Any] = {}
        for package in resolved_packages:
            package_vars = deep_merge(package_vars, package.vars or {})
        variables = deep_merge(deep_merge(package_vars, profile_vars), repo.local_vars)
        inferred_os = infer_profile_os(binding.profile, lineage, variables)
        context = build_template_context(variables, profile=binding.profile, inferred_os=inferred_os)
        package = repo.resolve_package(package_id)
        hooks = self._plan_hooks(repo, [package], context) if executable else {}
        targets = self._summarize_targets(repo, package, context)
        tracked_reason = "explicit" if package_id in self._selected_package_ids(repo, binding.selector, selector_kind) else "implicit"

        return InstalledPackageBindingDetail(
            binding=InstalledBindingSummary(
                repo=repo.config.name,
                selector=binding.selector,
                profile=binding.profile,
                selector_kind=selector_kind,
            ),
            tracked_reason=tracked_reason,
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
            if target.disabled:
                continue
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
                    reconcile_io=target.reconcile_io,
                    pull_view_repo=target.pull_view_repo or "raw",
                    pull_view_live=target.pull_view_live or ("capture" if capture_command else "raw"),
                    push_ignore=merge_ignore_patterns(repo.ignore_defaults.push, target.push_ignore or ()),
                    pull_ignore=merge_ignore_patterns(repo.ignore_defaults.pull, target.pull_ignore or ()),
                    chmod=target.chmod,
                )
            )
        return target_summaries

    def _installed_target_summary_from_plan(self, target: TargetPlan) -> InstalledTargetSummary:
        return InstalledTargetSummary(
            target_name=target.target_name,
            repo_path=target.repo_path,
            live_path=target.live_path,
            target_kind=target.target_kind,
            render_command=target.render_command,
            capture_command=target.capture_command,
            reconcile_command=target.reconcile_command,
            reconcile_io=target.reconcile_io,
            pull_view_repo=target.pull_view_repo,
            pull_view_live=target.pull_view_live,
            push_ignore=target.push_ignore,
            pull_ignore=target.pull_ignore,
            chmod=target.chmod,
        )

    def _describe_owned_package_targets(
        self,
        repo_name: str,
        package_id: str,
        bound_profile: str | None,
    ) -> list[InstalledOwnedTargetDetail]:
        owned_targets: list[InstalledOwnedTargetDetail] = []
        for plan in self.plan_push():
            if plan.binding.repo != repo_name:
                continue
            if bound_profile is not None and plan.binding.profile != bound_profile:
                continue
            for target in plan.target_plans:
                if target.package_id != package_id:
                    continue
                owned_targets.append(
                    InstalledOwnedTargetDetail(
                        binding=InstalledBindingSummary(
                            repo=plan.binding.repo,
                            selector=plan.binding.selector,
                            profile=plan.binding.profile,
                            selector_kind=plan.selector_kind,
                        ),
                        target=self._installed_target_summary_from_plan(target),
                    )
                )
        return sorted(
            owned_targets,
            key=lambda item: (
                item.target.target_name,
                item.binding.profile,
                item.binding.selector,
                item.binding.repo,
            ),
        )

    def _effective_package_binding_keys(
        self,
        repo_name: str,
        package_id: str,
        bound_profile: str | None,
    ) -> set[tuple[str, str, str]]:
        effective_bindings: set[tuple[str, str, str]] = set()
        for plan in self.plan_push():
            if plan.binding.repo != repo_name:
                continue
            if bound_profile is not None and plan.binding.profile != bound_profile:
                continue
            # `info tracked` should report hooks for the binding that currently owns the package's
            # winning targets, even when the live files already match and push would be all-noop.
            if not any(target.package_id == package_id for target in plan.target_plans):
                continue
            effective_bindings.add((plan.binding.repo, plan.binding.selector, plan.binding.profile))
        return effective_bindings

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
        hooks = self._plan_hooks(repo, resolved_packages, context, operation=operation)
        target_plans = self._plan_targets(
            repo=repo,
            packages=resolved_packages,
            context=context,
            binding=binding,
            operation=operation,
            inferred_os=inferred_os,
        )
        hooks = filter_hook_plans_for_targets(hooks, target_plans)
        return BindingPlan(
            operation=operation,
            binding=binding,
            selector_kind=selector_kind,
            package_ids=package_ids,
            variables=variables,
            hooks=hooks,
            target_plans=target_plans,
            repo_root=repo.root,
            state_path=repo.config.state_path,
            inferred_os=inferred_os,
        )

    def _build_tracked_plans(
        self,
        *,
        operation: str,
        bindings_by_repo: dict[str, list[Binding]] | None = None,
    ) -> list[BindingPlan]:
        plans, candidates_by_live_path = self._collect_tracked_candidates(
            operation=operation,
            bindings_by_repo=bindings_by_repo,
        )
        winner_indexes = self._resolve_tracked_target_winners(candidates_by_live_path)
        filtered_plans: list[BindingPlan] = []
        for plan_index, plan in enumerate(plans):
            filtered_targets = [
                target
                for target_index, target in enumerate(plan.target_plans)
                if (plan_index, target_index) in winner_indexes
            ]
            filtered_plans.append(
                replace(
                    plan,
                    hooks=filter_hook_plans_for_targets(plan.hooks, filtered_targets),
                    target_plans=filtered_targets,
                )
            )
        return filtered_plans

    def _collect_tracked_candidates(
        self,
        *,
        operation: str,
        bindings_by_repo: dict[str, list[Binding]] | None = None,
    ) -> tuple[list[BindingPlan], dict[Path, list[TrackedTargetCandidate]]]:
        plans: list[BindingPlan] = []
        candidates_by_live_path: dict[Path, list[TrackedTargetCandidate]] = defaultdict(list)
        current_bindings = bindings_by_repo or self._effective_bindings_by_repo()

        for repo_config in self.config.ordered_repos:
            repo = self.get_repo(repo_config.name)
            for binding in current_bindings.get(repo_config.name, []):
                selector_kind = "package"
                selected_packages = set(self._selected_package_ids(repo, binding.selector, selector_kind))
                plan = self._build_plan(repo, binding, selector_kind, operation=operation)
                plan_index = len(plans)
                plans.append(plan)
                for target_index, target in enumerate(plan.target_plans):
                    precedence_name = "explicit" if target.package_id in selected_packages else "implicit"
                    candidates_by_live_path[target.live_path].append(
                        TrackedTargetCandidate(
                            plan_index=plan_index,
                            target_index=target_index,
                            live_path=target.live_path,
                            precedence=1 if precedence_name == "explicit" else 0,
                            precedence_name=precedence_name,
                            binding=binding,
                            binding_label=f"{binding.repo}:{binding.selector}@{binding.profile}",
                            package_id=target.package_id,
                            target_name=target.target_name,
                            target_label=f"{target.package_id}:{target.target_name}",
                            signature=self._tracked_target_signature(target),
                        )
                    )
        return plans, candidates_by_live_path

    def preview_binding_implicit_overrides(self, binding: Binding) -> list[TrackedTargetOverride]:
        repo = self.get_repo(binding.repo)
        raw_bindings_by_repo = self._raw_bindings_by_repo()
        raw_bindings_by_repo[repo.config.name] = self._normalize_recorded_binding_set(
            self._effective_bindings_for_repo(repo, raw_bindings_by_repo.get(repo.config.name, [])),
            self._expand_binding_for_tracking(repo, binding),
        )
        _plans, candidates_by_live_path = self._collect_tracked_candidates(
            operation="push",
            bindings_by_repo=self._effective_bindings_by_repo(raw_bindings_by_repo),
        )

        overrides_by_package: dict[
            tuple[str, str, str, str],
            dict[tuple[str, str, str, str], TrackedTargetCandidate],
        ] = {}
        winners_by_package: dict[tuple[str, str, str, str], TrackedTargetCandidate] = {}
        for live_path, candidates in candidates_by_live_path.items():
            highest_precedence = max(candidate.precedence for candidate in candidates)
            winning_candidates = [candidate for candidate in candidates if candidate.precedence == highest_precedence]
            winner = next(
                (
                    candidate
                    for candidate in winning_candidates
                    if candidate.binding == binding and candidate.precedence_name == "explicit"
                ),
                None,
            )
            if winner is None:
                continue
            overridden = [
                candidate
                for candidate in candidates
                if candidate.binding != binding and candidate.precedence_name == "implicit"
            ]
            if not overridden:
                continue

            # Collapse override previews to package ownership because target-level collisions
            # are already rejected elsewhere; the user only needs to see which packages lose ownership.
            winner_key = (
                winner.binding.repo,
                winner.binding.selector,
                winner.binding.profile,
                winner.package_id,
            )
            winners_by_package[winner_key] = winner
            package_overrides = overrides_by_package.setdefault(winner_key, {})
            for candidate in overridden:
                contender_key = (
                    candidate.binding.repo,
                    candidate.binding.selector,
                    candidate.binding.profile,
                    candidate.package_id,
                )
                package_overrides.setdefault(contender_key, candidate)
        return sorted(
            [
                TrackedTargetOverride(
                    winner=winners_by_package[winner_key],
                    overridden=tuple(
                        sorted(
                            contenders.values(),
                            key=lambda item: (
                                item.binding.repo,
                                item.binding.selector,
                                item.binding.profile,
                                item.package_id,
                            ),
                        )
                    ),
                )
                for winner_key, contenders in overrides_by_package.items()
            ],
            key=lambda item: (
                item.winner.package_id,
                item.winner.binding.repo,
                item.winner.binding.selector,
                item.winner.binding.profile,
            ),
        )

    def _tracked_target_signature(self, target: TargetPlan) -> tuple[Any, ...]:
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

    def _resolve_tracked_target_winners(
        self,
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
                        f"{candidate.binding_label} ({candidate.target_label})"
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

    def _resolve_package_ids(self, repo: Repository, selector: str, selector_kind: str) -> list[str]:
        roots = self._selected_package_ids(repo, selector, selector_kind)
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
        operation: str | None = None,
    ) -> dict[str, list[HookPlan]]:
        hook_names = HOOK_NAMES_BY_OPERATION.get(operation, VALID_HOOK_NAMES)
        hooks: dict[str, list[HookPlan]] = defaultdict(list)
        for package in packages:
            package_hooks = package.hooks or {}
            for hook_name in hook_names:
                hook_spec = package_hooks.get(hook_name)
                if hook_spec is None:
                    continue
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
                if target.disabled:
                    continue
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
                        merge_ignore_patterns(repo.ignore_defaults.push, target.push_ignore or ()),
                        merge_ignore_patterns(repo.ignore_defaults.pull, target.pull_ignore or ()),
                    )
                )

        self._validate_target_collisions(rendered_targets)
        self._validate_reserved_path_conflicts(packages, rendered_targets, context)

        plans: list[TargetPlan] = []
        for package, target, repo_path, live_path, push_ignore, pull_ignore in rendered_targets:
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
            command_env = self._build_target_command_env(
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
            if repo_path.is_dir():
                action, directory_items = self._plan_directory_action(
                    repo_path,
                    live_path,
                    push_ignore,
                    pull_ignore,
                    operation=operation,
                )
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
                        reconcile_io=target.reconcile_io,
                        pull_view_repo=target.pull_view_repo or "raw",
                        pull_view_live=target.pull_view_live or ("capture" if capture_command else "raw"),
                        push_ignore=push_ignore,
                        pull_ignore=pull_ignore,
                        chmod=target.chmod,
                        command_cwd=target.declared_in,
                        command_env=command_env,
                        directory_items=directory_items,
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
                if render_command == "jinja":
                    raise
                if operation in {"upgrade", "push"} and not live_path.exists():
                    projection_error = str(exc)
                    projection_kind = "command"
                else:
                    raise
            pull_view_repo = target.pull_view_repo or "raw"
            pull_view_live = target.pull_view_live or ("capture" if capture_command else "raw")
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
                pull_view_repo=pull_view_repo,
                pull_view_live=pull_view_live,
            )
            review_before_bytes, review_after_bytes = self._build_file_review_bytes(
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
                    reconcile_io=target.reconcile_io,
                    projection_error=projection_error,
                    pull_view_repo=pull_view_repo,
                    pull_view_live=pull_view_live,
                    push_ignore=push_ignore,
                    pull_ignore=pull_ignore,
                    chmod=target.chmod,
                    command_cwd=target.declared_in,
                    command_env=command_env,
                    desired_bytes=desired_bytes,
                    review_before_bytes=review_before_bytes,
                    review_after_bytes=review_after_bytes,
                )
            )
        return plans

    def _validate_target_collisions(
        self,
        rendered_targets: list[tuple[PackageSpec, TargetSpec, Path, Path, tuple[str, ...], tuple[str, ...]]],
    ) -> None:
        for index, (package, target, _repo_path, live_path, push_ignore, pull_ignore) in enumerate(rendered_targets):
            for (
                other_package,
                other_target,
                _other_repo_path,
                other_live_path,
                other_push_ignore,
                other_pull_ignore,
            ) in rendered_targets[index + 1 :]:
                if live_path == other_live_path:
                    raise ValueError(
                        f"conflicting target ownership: {package.id}:{target.name} and {other_package.id}:{other_target.name} both map to {live_path}"
                    )
                if live_path in other_live_path.parents:
                    relative = other_live_path.relative_to(live_path).as_posix()
                    parent_ignore = set(push_ignore) | set(pull_ignore)
                    if not any(matches_ignore_pattern(relative, pattern) for pattern in parent_ignore):
                        raise ValueError(
                            f"incompatible nested targets: {package.id}:{target.name} contains {other_package.id}:{other_target.name}"
                        )
                elif other_live_path in live_path.parents:
                    relative = live_path.relative_to(other_live_path).as_posix()
                    parent_ignore = set(other_push_ignore) | set(other_pull_ignore)
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
            for package, target, _repo_path, live_path, _push_ignore, _pull_ignore in rendered_targets
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
        if render_command == "jinja":
            return render_template_file(repo_path, context)
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
        return repo_path.read_bytes(), "raw"

    def _plan_directory_action(
        self,
        repo_path: Path,
        live_path: Path,
        push_ignore: tuple[str, ...],
        pull_ignore: tuple[str, ...],
        *,
        operation: str,
    ) -> tuple[str, tuple[DirectoryPlanItem, ...]]:
        desired_files = list_directory_files(repo_path, push_ignore)
        live_exists = live_path.exists()
        live_files = list_directory_files(live_path, pull_ignore) if live_exists else {}
        desired_rel_paths = set(desired_files)
        live_rel_paths = set(live_files)
        directory_items: list[DirectoryPlanItem] = []

        if operation in {"upgrade", "push"}:
            for relative_path in sorted(desired_rel_paths - live_rel_paths):
                directory_items.append(
                    DirectoryPlanItem(
                        relative_path=relative_path,
                        action="create",
                        repo_path=desired_files[relative_path],
                        live_path=live_path / relative_path,
                    )
                )
            for relative_path in sorted(live_rel_paths - desired_rel_paths):
                directory_items.append(
                    DirectoryPlanItem(
                        relative_path=relative_path,
                        action="delete",
                        repo_path=repo_path / relative_path,
                        live_path=live_files[relative_path],
                    )
                )
            for relative_path in sorted(desired_rel_paths & live_rel_paths):
                source_path = desired_files[relative_path]
                live_file = live_files[relative_path]
                desired_bytes = source_path.read_bytes()
                if desired_bytes != live_file.read_bytes():
                    directory_items.append(
                        DirectoryPlanItem(
                            relative_path=relative_path,
                            action="update",
                            repo_path=source_path,
                            live_path=live_file,
                        )
                    )
            if not directory_items:
                return "noop", ()
            ordered_items = tuple(sorted(directory_items, key=lambda item: item.relative_path))
            return ("create" if not live_exists else "update"), ordered_items

        for relative_path in sorted(desired_rel_paths - live_rel_paths):
            directory_items.append(
                DirectoryPlanItem(
                    relative_path=relative_path,
                    action="delete",
                    repo_path=desired_files[relative_path],
                    live_path=live_path / relative_path,
                )
            )
        for relative_path in sorted(live_rel_paths - desired_rel_paths):
            directory_items.append(
                DirectoryPlanItem(
                    relative_path=relative_path,
                    action="create",
                    repo_path=repo_path / relative_path,
                    live_path=live_files[relative_path],
                )
            )
        for relative_path in sorted(desired_rel_paths & live_rel_paths):
            source_path = desired_files[relative_path]
            live_file = live_files[relative_path]
            desired_bytes = source_path.read_bytes()
            if desired_bytes != live_file.read_bytes():
                directory_items.append(
                    DirectoryPlanItem(
                        relative_path=relative_path,
                        action="update",
                        repo_path=source_path,
                        live_path=live_file,
                    )
                )

        if not directory_items:
            return "noop", ()
        ordered_items = tuple(sorted(directory_items, key=lambda item: item.relative_path))
        return ("delete" if not live_exists else "update"), ordered_items

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
        pull_view_repo: str,
        pull_view_live: str,
    ) -> str:
        if operation in {"upgrade", "push"}:
            if not live_path.exists():
                return "create"
            if desired_bytes is None:
                return "unknown"
            return "noop" if desired_bytes == live_path.read_bytes() else "update"

        if not live_path.exists():
            return "delete"
        repo_bytes = self._pull_view_bytes(
            repo=repo,
            package=package,
            target=target,
            repo_path=repo_path,
            live_path=live_path,
            view=pull_view_repo,
            repo_side=True,
            render_command=render_command,
            capture_command=capture_command,
            context=context,
            binding=binding,
            operation=operation,
            inferred_os=inferred_os,
        )
        live_bytes = self._pull_view_bytes(
            repo=repo,
            package=package,
            target=target,
            repo_path=repo_path,
            live_path=live_path,
            view=pull_view_live,
            repo_side=False,
            render_command=render_command,
            capture_command=capture_command,
            context=context,
            binding=binding,
            operation=operation,
            inferred_os=inferred_os,
        )
        return "noop" if repo_bytes == live_bytes else "update"

    def _build_file_review_bytes(
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
        pull_view_repo: str,
        pull_view_live: str,
    ) -> tuple[bytes | None, bytes | None]:
        if operation in {"upgrade", "push"}:
            live_bytes = live_path.read_bytes() if live_path.exists() else b""
            return live_bytes, desired_bytes

        repo_bytes = self._pull_view_bytes(
            repo=repo,
            package=package,
            target=target,
            repo_path=repo_path,
            live_path=live_path,
            view=pull_view_repo,
            repo_side=True,
            render_command=render_command,
            capture_command=capture_command,
            context=context,
            binding=binding,
            operation=operation,
            inferred_os=inferred_os,
        )
        if not live_path.exists():
            return repo_bytes, b""
        live_bytes = self._pull_view_bytes(
            repo=repo,
            package=package,
            target=target,
            repo_path=repo_path,
            live_path=live_path,
            view=pull_view_live,
            repo_side=False,
            render_command=render_command,
            capture_command=capture_command,
            context=context,
            binding=binding,
            operation=operation,
            inferred_os=inferred_os,
        )
        return repo_bytes, live_bytes

    def _pull_view_bytes(
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
            self._build_target_command_env(
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
        )
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

    def _build_target_command_env(
        self,
        *,
        repo: Repository,
        package: PackageSpec,
        target: TargetSpec,
        repo_path: Path,
        live_path: Path,
        binding: Binding,
        operation: str,
        inferred_os: str,
        context: dict[str, Any],
    ) -> dict[str, str]:
        env = {
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
        for flat_key, value in flatten_vars(context["vars"]).items():
            env[f"DOTMAN_VAR_{flat_key}"] = value
        return env


__all__ = [
    "DotmanEngine",
    "compute_profile_heights",
    "rank_profiles",
]
