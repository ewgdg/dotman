from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from dotman.manifest import (
    _copy_map,
    build_target_spec,
    deep_merge,
    merge_package_specs,
    normalize_string_list,
    patch_remove_and_append,
    read_schema_alias,
    strip_package_extensions,
)
from dotman.models import GroupSpec, HookSpec, PackageSpec, ProfileSpec, RepoConfig, RepoIgnoreDefaults


VALID_HOOK_NAMES = (
    "guard_push",
    "pre_push",
    "post_push",
    "guard_pull",
    "pre_pull",
    "post_pull",
)


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
                    raise ValueError(
                        f"selector '{member}' is ambiguous between package and group in repo '{self.config.name}'"
                    )
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
