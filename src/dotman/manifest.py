from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from dotman.models import HookCommandSpec, HookSpec, PackageSpec, TargetSpec
from dotman.presets import BUILTIN_TARGET_PRESETS, get_builtin_target_preset


VALID_COMMAND_IO_VALUES = ("pipe", "tty")
VALID_HOOK_IO_VALUES = VALID_COMMAND_IO_VALUES
VALID_ELEVATION_VALUES = ("none", "root", "lease", "broker", "intercept")
VALID_SYNC_POLICY_VALUES = ("push-only", "pull-only", "both")


def validate_package_id(package_id: str) -> None:
    if not package_id.strip():
        raise ValueError("package id must not be empty")
    if package_id.startswith("/") or package_id.endswith("/"):
        raise ValueError(f"invalid package id '{package_id}'")
    if any(character in package_id for character in ("\\", ":", "@", "<", ">", ".")):
        raise ValueError(f"invalid package id '{package_id}'")
    parts = package_id.split("/")
    if any(not part or part in {".", ".."} or any(character.isspace() for character in part) for part in parts):
        raise ValueError(f"invalid package id '{package_id}'")


def validate_target_name(target_name: str) -> None:
    if not target_name.strip() or "." in target_name or any(character.isspace() for character in target_name):
        raise ValueError(f"invalid target name '{target_name}'")


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


def normalize_hook_command_specs(
    value: Any,
    *,
    manifest_kind: str,
    manifest_path: Path,
    owner_label: str,
    hook_name: str,
) -> tuple[HookCommandSpec, ...]:
    if isinstance(value, str):
        return (HookCommandSpec(run=value),)
    if not isinstance(value, list):
        raise ValueError(
            f"{manifest_kind} {manifest_path} {owner_label} hook '{hook_name}' commands must be a string or list"
        )

    commands: list[HookCommandSpec] = []
    for item in value:
        if isinstance(item, str):
            commands.append(HookCommandSpec(run=item))
            continue
        if isinstance(item, dict):
            commands.append(
                _build_hook_command_spec(
                    command_payload=item,
                    manifest_kind=manifest_kind,
                    manifest_path=manifest_path,
                    owner_label=owner_label,
                    hook_name=hook_name,
                )
            )
            continue
        raise ValueError(
            f"{manifest_kind} {manifest_path} {owner_label} hook '{hook_name}' commands must contain only strings or command objects"
        )
    return tuple(commands)


def _build_hook_command_spec(
    *,
    command_payload: dict[str, Any],
    manifest_kind: str,
    manifest_path: Path,
    owner_label: str,
    hook_name: str,
) -> HookCommandSpec:
    return _build_command_spec(
        command_payload=command_payload,
        manifest_kind=manifest_kind,
        manifest_path=manifest_path,
        owner_label=owner_label,
        command_label=f"hook '{hook_name}' command object",
    )


def _build_command_spec(
    *,
    command_payload: dict[str, Any],
    manifest_kind: str,
    manifest_path: Path,
    owner_label: str,
    command_label: str,
) -> HookCommandSpec:
    if "privileged" in command_payload:
        raise ValueError(
            f"{manifest_kind} {manifest_path} {owner_label} {command_label} uses deprecated 'privileged'; "
            "use elevation = \"root\" instead"
        )
    unknown_keys = sorted(key for key in command_payload if key not in {"run", "io", "elevation"})
    if unknown_keys:
        unknown_text = ", ".join(unknown_keys)
        raise ValueError(
            f"{manifest_kind} {manifest_path} {owner_label} {command_label} has unsupported keys: {unknown_text}"
        )
    if "run" not in command_payload:
        raise ValueError(
            f"{manifest_kind} {manifest_path} {owner_label} {command_label} must define 'run'"
        )
    run_value = command_payload.get("run")
    if not isinstance(run_value, str):
        raise ValueError(
            f"{manifest_kind} {manifest_path} {owner_label} {command_label} 'run' must be a string"
        )
    if not run_value.strip():
        raise ValueError(
            f"{manifest_kind} {manifest_path} {owner_label} {command_label} 'run' must not be empty"
        )
    io_value = normalize_optional_string_enum(command_payload.get("io"), key="io", allowed=VALID_HOOK_IO_VALUES) or "pipe"
    elevation_value = normalize_optional_string_enum(
        command_payload.get("elevation"),
        key="elevation",
        allowed=VALID_ELEVATION_VALUES,
    ) or "none"
    return HookCommandSpec(run=run_value, io=io_value, elevation=elevation_value)


def _build_reconcile_spec(
    *,
    reconcile_payload: Any,
    manifest_kind: str,
    manifest_path: Path,
    owner_label: str,
) -> HookCommandSpec | None:
    if reconcile_payload is None:
        return None
    if isinstance(reconcile_payload, str):
        if reconcile_payload == "jinja":
            return HookCommandSpec(run=reconcile_payload, io="tty")
        return HookCommandSpec(run=reconcile_payload)
    if not isinstance(reconcile_payload, dict):
        raise ValueError(
            f"{manifest_kind} {manifest_path} {owner_label} reconcile must be a string or table"
        )
    return _build_command_spec(
        command_payload=reconcile_payload,
        manifest_kind=manifest_kind,
        manifest_path=manifest_path,
        owner_label=owner_label,
        command_label="reconcile object",
    )


def normalize_optional_string_enum(value: Any, *, key: str, allowed: tuple[str, ...]) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"expected string for '{key}', got {type(value).__name__}")
    if value not in allowed:
        allowed_text = ", ".join(allowed)
        raise ValueError(f"unsupported {key} '{value}'; expected one of: {allowed_text}")
    return value


def normalize_sync_policy(value: Any) -> str | None:
    return normalize_optional_string_enum(value, key="sync_policy", allowed=VALID_SYNC_POLICY_VALUES)


def resolve_sync_policy(*, package: PackageSpec, target: TargetSpec) -> str:
    return target.sync_policy or package.sync_policy or "both"


def sync_policy_allows_operation(sync_policy: str, *, operation: str) -> bool:
    if sync_policy == "both":
        return True
    if sync_policy == "push-only":
        return operation == "push"
    if sync_policy == "pull-only":
        return operation == "pull"
    raise ValueError(f"unsupported sync policy '{sync_policy}'")


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
    # Presets are default layer. Resolve explicit aliases first so user can
    # override preset with current key or legacy schema alias.
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
    try:
        validate_target_name(target_name)
    except ValueError as exc:
        raise ValueError(f"package manifest {manifest_path}: {exc}") from None
    preset_payload = resolve_target_preset(
        target_payload=target_payload,
        manifest_path=manifest_path,
        target_name=target_name,
    )
    hooks_payload = target_payload.get("hooks")
    hooks = None
    if isinstance(hooks_payload, dict):
        unknown_hook_names = sorted(key for key in hooks_payload if key not in {"guard_push", "pre_push", "post_push", "guard_pull", "pre_pull", "post_pull"})
        if unknown_hook_names:
            unknown_text = ", ".join(unknown_hook_names)
            raise ValueError(
                f"package manifest {manifest_path} target '{target_name}' uses unsupported hook names: {unknown_text}"
            )
        hooks = {
            hook_name: build_hook_spec(
                hook_name=hook_name,
                hook_payload=hook_value,
                manifest_path=manifest_path,
                owner_label=f"target '{target_name}'",
            )
            for hook_name, hook_value in hooks_payload.items()
        }
    return TargetSpec(
        name=target_name,
        declared_in=manifest_path.parent,
        source=get_target_value(target_payload=target_payload, preset_payload=preset_payload, key="source"),
        path=get_target_value(target_payload=target_payload, preset_payload=preset_payload, key="path"),
        sync_policy=normalize_sync_policy(
            get_target_value(target_payload=target_payload, preset_payload=preset_payload, key="sync_policy")
        ),
        chmod=get_target_value(target_payload=target_payload, preset_payload=preset_payload, key="chmod"),
        render=get_target_value(target_payload=target_payload, preset_payload=preset_payload, key="render"),
        capture=get_target_value(target_payload=target_payload, preset_payload=preset_payload, key="capture"),
        reconcile=_build_reconcile_spec(
            reconcile_payload=get_target_value(target_payload=target_payload, preset_payload=preset_payload, key="reconcile"),
            manifest_kind="package manifest",
            manifest_path=manifest_path,
            owner_label=f"target '{target_name}'",
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
        hooks=hooks,
        disabled=bool(get_target_value(target_payload=target_payload, preset_payload=preset_payload, key="disabled") or False),
    )


def build_hook_spec(
    *,
    hook_name: str,
    hook_payload: Any,
    manifest_path: Path,
    owner_label: str = "package",
    manifest_kind: str = "package manifest",
) -> HookSpec:
    commands_payload = hook_payload
    run_noop = False
    if isinstance(hook_payload, dict):
        unknown_keys = sorted(key for key in hook_payload if key not in {"commands", "run_noop"})
        if unknown_keys:
            unknown_text = ", ".join(unknown_keys)
            raise ValueError(
                f"{manifest_kind} {manifest_path} {owner_label} hook '{hook_name}' has unsupported keys: {unknown_text}"
            )
        if "commands" not in hook_payload:
            raise ValueError(
                f"{manifest_kind} {manifest_path} {owner_label} hook '{hook_name}' must define 'commands'"
            )
        commands_payload = hook_payload.get("commands")
        run_noop_value = hook_payload.get("run_noop", False)
        if not isinstance(run_noop_value, bool):
            raise ValueError(
                f"{manifest_kind} {manifest_path} {owner_label} hook '{hook_name}' run_noop must be a boolean"
            )
        run_noop = run_noop_value
    commands = normalize_hook_command_specs(
        commands_payload,
        manifest_kind=manifest_kind,
        manifest_path=manifest_path,
        owner_label=owner_label,
        hook_name=hook_name,
    )
    return HookSpec(
        name=hook_name,
        commands=commands,
        declared_in=manifest_path.parent,
        run_noop=run_noop,
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


def strip_package_extensions(package: PackageSpec) -> PackageSpec:
    return replace(package, extends=None)


def merge_target_specs(base: TargetSpec, override: TargetSpec) -> TargetSpec:
    hooks = dict(base.hooks or {})
    hooks.update(override.hooks or {})
    return TargetSpec(
        name=override.name,
        declared_in=override.declared_in,
        source=override.source if override.source is not None else base.source,
        path=override.path if override.path is not None else base.path,
        sync_policy=override.sync_policy if override.sync_policy is not None else base.sync_policy,
        chmod=override.chmod if override.chmod is not None else base.chmod,
        render=override.render if override.render is not None else base.render,
        capture=override.capture if override.capture is not None else base.capture,
        reconcile=override.reconcile if override.reconcile is not None else base.reconcile,
        pull_view_repo=override.pull_view_repo if override.pull_view_repo is not None else base.pull_view_repo,
        pull_view_live=override.pull_view_live if override.pull_view_live is not None else base.pull_view_live,
        push_ignore=override.push_ignore if override.push_ignore is not None else base.push_ignore,
        pull_ignore=override.pull_ignore if override.pull_ignore is not None else base.pull_ignore,
        hooks=hooks,
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
        sync_policy=override.sync_policy if override.sync_policy is not None else base.sync_policy,
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
