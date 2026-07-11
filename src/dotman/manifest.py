from __future__ import annotations

import sys
from dataclasses import MISSING, fields, is_dataclass, replace
from pathlib import Path
from typing import Any

from dotman.models import DefaultCommandElevationMode, HookCommandSpec, HookSpec, PackageSpec, TargetPathRule, TargetSpec
from dotman.presets import BUILTIN_TARGET_PRESETS, get_builtin_target_preset


VALID_COMMAND_IO_VALUES = ("pipe", "tty")
VALID_HOOK_IO_VALUES = VALID_COMMAND_IO_VALUES
VALID_ELEVATION_VALUES = ("none", "root", "lease", "broker", "intercept")
VALID_DEFAULT_COMMAND_ELEVATION_VALUES = ("none", "broker", "intercept")
VALID_SYNC_POLICY_VALUES = ("push-only", "pull-only", "both", "push-only-delete")
VALID_TARGET_TYPE_VALUES = ("file", "directory")


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
    default_command_elevation: DefaultCommandElevationMode = "none",
) -> tuple[HookCommandSpec, ...]:
    if isinstance(value, str):
        return (HookCommandSpec(run=value, elevation=default_command_elevation),)
    if not isinstance(value, list):
        raise ValueError(
            f"{manifest_kind} {manifest_path} {owner_label} hook '{hook_name}' commands must be a string or list"
        )

    commands: list[HookCommandSpec] = []
    for item in value:
        if isinstance(item, str):
            commands.append(HookCommandSpec(run=item, elevation=default_command_elevation))
            continue
        if isinstance(item, dict):
            commands.append(
                _build_hook_command_spec(
                    command_payload=item,
                    manifest_kind=manifest_kind,
                    manifest_path=manifest_path,
                    owner_label=owner_label,
                    hook_name=hook_name,
                    default_command_elevation=default_command_elevation,
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
    default_command_elevation: DefaultCommandElevationMode = "none",
) -> HookCommandSpec:
    planning_guard = hook_name.startswith("guard_")
    command_spec = _build_command_spec(
        command_payload=command_payload,
        manifest_kind=manifest_kind,
        manifest_path=manifest_path,
        owner_label=owner_label,
        command_label=f"hook '{hook_name}' command object",
        default_command_elevation=default_command_elevation,
        allow_run_noop=not planning_guard,
    )
    if planning_guard and command_spec.io != "pipe":
        raise ValueError(
            f"{manifest_kind} {manifest_path} {owner_label} hook '{hook_name}' command io must be 'pipe'"
        )
    return command_spec


def _build_command_spec(
    *,
    command_payload: dict[str, Any],
    manifest_kind: str,
    manifest_path: Path,
    owner_label: str,
    command_label: str,
    default_command_elevation: DefaultCommandElevationMode = "none",
    allow_run_noop: bool = False,
) -> HookCommandSpec:
    if "privileged" in command_payload:
        raise ValueError(
            f"{manifest_kind} {manifest_path} {owner_label} {command_label} uses deprecated 'privileged'; "
            "use elevation = \"root\" instead"
        )
    supported_keys = {"run", "io", "elevation"}
    if allow_run_noop:
        supported_keys.add("run_noop")
    unknown_keys = sorted(key for key in command_payload if key not in supported_keys)
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
    ) or default_command_elevation
    run_noop_value = command_payload.get("run_noop", False) if allow_run_noop else False
    if allow_run_noop and not isinstance(run_noop_value, bool):
        raise ValueError(
            f"{manifest_kind} {manifest_path} {owner_label} {command_label} run_noop must be a boolean"
        )
    return HookCommandSpec(run=run_value, io=io_value, elevation=elevation_value, run_noop=run_noop_value)


def _build_reconcile_spec(
    *,
    reconcile_payload: Any,
    manifest_kind: str,
    manifest_path: Path,
    owner_label: str,
    default_command_elevation: DefaultCommandElevationMode = "none",
) -> HookCommandSpec | None:
    if reconcile_payload is None:
        return None
    if isinstance(reconcile_payload, str):
        if reconcile_payload == "jinja":
            return HookCommandSpec(run=reconcile_payload, io="tty")
        return HookCommandSpec(run=reconcile_payload, elevation=default_command_elevation)
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
        default_command_elevation=default_command_elevation,
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


def normalize_default_command_elevation(value: Any, *, manifest_path: Path) -> DefaultCommandElevationMode:
    normalized = normalize_optional_string_enum(
        value,
        key="default_command_elevation",
        allowed=VALID_DEFAULT_COMMAND_ELEVATION_VALUES,
    )
    return normalized or "none"


def normalize_sync_policy(value: Any) -> str | None:
    return normalize_optional_string_enum(value, key="sync_policy", allowed=VALID_SYNC_POLICY_VALUES)


def normalize_target_type(value: Any) -> str | None:
    return normalize_optional_string_enum(value, key="target type", allowed=VALID_TARGET_TYPE_VALUES)


def normalize_probe_command(value: Any, *, manifest_path: Path, target_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(
            f"package manifest {manifest_path} target '{target_name}' probe must be a string"
        )
    if not value.strip():
        raise ValueError(
            f"package manifest {manifest_path} target '{target_name}' probe must not be empty"
        )
    return value


def resolve_sync_policy(*, package: PackageSpec, target: TargetSpec) -> str:
    return target.sync_policy or package.sync_policy or "both"


def sync_policy_allows_operation(sync_policy: str, *, operation: str) -> bool:
    if sync_policy == "both":
        return True
    if sync_policy in {"push-only", "push-only-delete"}:
        return operation == "push"
    if sync_policy == "pull-only":
        return operation == "pull"
    raise ValueError(f"unsupported sync policy '{sync_policy}'")


def sync_policy_deletes_on_push(sync_policy: str) -> bool:
    return sync_policy == "push-only-delete"


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


def normalize_path_rule_hooks(
    value: Any,
    *,
    manifest_path: Path,
    target_name: str,
    rule_index: int,
    default_command_elevation: DefaultCommandElevationMode,
) -> dict[str, HookSpec] | None:
    if value is None:
        return None
    owner_label = f"target '{target_name}' path_rules[{rule_index}]"
    if not isinstance(value, dict):
        raise ValueError(f"package manifest {manifest_path} {owner_label}.hooks must be a table")
    unknown_hook_names = sorted(key for key in value if key not in {"guard_push", "guard_pull"})
    if unknown_hook_names:
        unknown_text = ", ".join(unknown_hook_names)
        raise ValueError(
            f"package manifest {manifest_path} {owner_label} uses unsupported hook names: {unknown_text}"
        )
    return {
        hook_name: build_hook_spec(
            hook_name=hook_name,
            hook_payload=hook_value,
            manifest_path=manifest_path,
            owner_label=owner_label,
            default_command_elevation=default_command_elevation,
        )
        for hook_name, hook_value in value.items()
    }


def normalize_target_path_rules(
    value: Any,
    *,
    manifest_path: Path,
    target_name: str,
    default_command_elevation: DefaultCommandElevationMode = "none",
) -> tuple[TargetPathRule, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(
            f"package manifest {manifest_path} target '{target_name}' path_rules must be a list"
        )
    rules: list[TargetPathRule] = []
    for index, rule_payload in enumerate(value, start=1):
        if not isinstance(rule_payload, dict):
            raise ValueError(
                f"package manifest {manifest_path} target '{target_name}' path_rules[{index}] must be a table"
            )
        pattern = rule_payload.get("pattern")
        if not isinstance(pattern, str) or not pattern.strip():
            raise ValueError(
                f"package manifest {manifest_path} target '{target_name}' path_rules[{index}].pattern must be a non-empty string"
            )
        normalized_pattern = pattern.replace("\\", "/")
        pattern_parts = normalized_pattern.split("/")
        if normalized_pattern.startswith("/") or any(part == ".." for part in pattern_parts):
            raise ValueError(
                f"package manifest {manifest_path} target '{target_name}' path_rules[{index}].pattern must be relative to the target root"
            )
        preset_payload: dict[str, Any] = {}
        preset_name = rule_payload.get("preset")
        if preset_name is not None:
            if not isinstance(preset_name, str):
                raise ValueError(
                    f"package manifest {manifest_path} target '{target_name}' path_rules[{index}].preset must be a string"
                )
            preset = get_builtin_target_preset(preset_name)
            if preset is None:
                available = ", ".join(sorted(BUILTIN_TARGET_PRESETS))
                raise ValueError(
                    f"package manifest {manifest_path} target '{target_name}' path_rules[{index}] uses unknown preset '{preset_name}'; "
                    f"available presets: {available}"
                )
            preset_payload = preset
        chmod = get_target_value(target_payload=rule_payload, preset_payload=preset_payload, key="chmod")
        if chmod is not None:
            if not isinstance(chmod, str):
                raise ValueError(
                    f"package manifest {manifest_path} target '{target_name}' path_rules[{index}].chmod must be a string"
                )
            try:
                int(chmod, 8)
            except ValueError:
                raise ValueError(
                    f"package manifest {manifest_path} target '{target_name}' path_rules[{index}].chmod must be an octal string"
                ) from None
        render = get_target_value(target_payload=rule_payload, preset_payload=preset_payload, key="render")
        if render is not None and not isinstance(render, str):
            raise ValueError(
                f"package manifest {manifest_path} target '{target_name}' path_rules[{index}].render must be a string"
            )
        capture = get_target_value(target_payload=rule_payload, preset_payload=preset_payload, key="capture")
        if capture is not None and not isinstance(capture, str):
            raise ValueError(
                f"package manifest {manifest_path} target '{target_name}' path_rules[{index}].capture must be a string"
            )
        pull_view_repo = read_target_schema_alias(
            target_payload=rule_payload,
            preset_payload=preset_payload,
            primary_key="pull_view_repo",
            legacy_key="import_view_repo",
        )
        if pull_view_repo is not None and not isinstance(pull_view_repo, str):
            raise ValueError(
                f"package manifest {manifest_path} target '{target_name}' path_rules[{index}].pull_view_repo must be a string"
            )
        pull_view_live = read_target_schema_alias(
            target_payload=rule_payload,
            preset_payload=preset_payload,
            primary_key="pull_view_live",
            legacy_key="import_view_live",
        )
        if pull_view_live is not None and not isinstance(pull_view_live, str):
            raise ValueError(
                f"package manifest {manifest_path} target '{target_name}' path_rules[{index}].pull_view_live must be a string"
            )
        hooks = normalize_path_rule_hooks(
            rule_payload.get("hooks"),
            manifest_path=manifest_path,
            target_name=target_name,
            rule_index=index,
            default_command_elevation=default_command_elevation,
        )
        rules.append(
            TargetPathRule(
                pattern=normalized_pattern,
                chmod=chmod,
                render=render,
                capture=capture,
                pull_view_repo=pull_view_repo,
                pull_view_live=pull_view_live,
                hooks=hooks,
            )
        )
    return tuple(rules)


def read_target_ignore_table(
    *,
    target_payload: dict[str, Any],
    preset_payload: dict[str, Any],
    manifest_path: Path,
    target_name: str,
) -> dict[str, Any] | None:
    ignore_payload = get_target_value(target_payload=target_payload, preset_payload=preset_payload, key="ignore")
    if ignore_payload is None:
        return None
    if not isinstance(ignore_payload, dict):
        raise ValueError(f"package manifest {manifest_path} target '{target_name}' ignore must be a table")
    return ignore_payload


def build_target_operation_ignore(
    *,
    target_payload: dict[str, Any],
    preset_payload: dict[str, Any],
    manifest_path: Path,
    target_name: str,
    primary_key: str,
    legacy_key: str,
    table_key: str,
    table_legacy_key: str,
) -> tuple[str, ...] | None:
    operation_ignore = normalize_string_list(
        read_target_schema_alias(
            target_payload=target_payload,
            preset_payload=preset_payload,
            primary_key=primary_key,
            legacy_key=legacy_key,
        )
    )
    ignore_payload = read_target_ignore_table(
        target_payload=target_payload,
        preset_payload=preset_payload,
        manifest_path=manifest_path,
        target_name=target_name,
    )
    table_operation_ignore = (
        normalize_string_list(read_schema_alias(ignore_payload, table_key, table_legacy_key))
        if ignore_payload is not None
        else None
    )
    shared_ignore = normalize_string_list(
        get_target_value(target_payload=target_payload, preset_payload=preset_payload, key="shared_ignore")
    )
    table_shared_ignore = (
        normalize_string_list(ignore_payload.get("shared")) if ignore_payload is not None else None
    )
    if operation_ignore is None and table_operation_ignore is None and shared_ignore is None and table_shared_ignore is None:
        return None
    return merge_ignore_patterns(
        operation_ignore or (),
        table_operation_ignore or (),
        shared_ignore or (),
        table_shared_ignore or (),
    )


def build_target_spec(
    *,
    target_name: str,
    target_payload: dict[str, Any],
    manifest_path: Path,
    default_command_elevation: DefaultCommandElevationMode = "none",
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
                default_command_elevation=default_command_elevation,
            )
            for hook_name, hook_value in hooks_payload.items()
        }
    source = get_target_value(target_payload=target_payload, preset_payload=preset_payload, key="source")
    path = get_target_value(target_payload=target_payload, preset_payload=preset_payload, key="path")
    probe = normalize_probe_command(
        get_target_value(target_payload=target_payload, preset_payload=preset_payload, key="probe"),
        manifest_path=manifest_path,
        target_name=target_name,
    )
    target_type = normalize_target_type(
        get_target_value(target_payload=target_payload, preset_payload=preset_payload, key="type")
    )
    sync_policy = normalize_sync_policy(
        get_target_value(target_payload=target_payload, preset_payload=preset_payload, key="sync_policy")
    )
    chmod = get_target_value(target_payload=target_payload, preset_payload=preset_payload, key="chmod")
    render = get_target_value(target_payload=target_payload, preset_payload=preset_payload, key="render")
    capture = get_target_value(target_payload=target_payload, preset_payload=preset_payload, key="capture")
    reconcile = _build_reconcile_spec(
        reconcile_payload=get_target_value(target_payload=target_payload, preset_payload=preset_payload, key="reconcile"),
        manifest_kind="package manifest",
        manifest_path=manifest_path,
        owner_label=f"target '{target_name}'",
        default_command_elevation=default_command_elevation,
    )
    pull_view_repo = read_target_schema_alias(
        target_payload=target_payload,
        preset_payload=preset_payload,
        primary_key="pull_view_repo",
        legacy_key="import_view_repo",
    )
    pull_view_live = read_target_schema_alias(
        target_payload=target_payload,
        preset_payload=preset_payload,
        primary_key="pull_view_live",
        legacy_key="import_view_live",
    )
    push_ignore = build_target_operation_ignore(
        target_payload=target_payload,
        preset_payload=preset_payload,
        manifest_path=manifest_path,
        target_name=target_name,
        primary_key="push_ignore",
        legacy_key="apply_ignore",
        table_key="push",
        table_legacy_key="apply",
    )
    pull_ignore = build_target_operation_ignore(
        target_payload=target_payload,
        preset_payload=preset_payload,
        manifest_path=manifest_path,
        target_name=target_name,
        primary_key="pull_ignore",
        legacy_key="import_ignore",
        table_key="pull",
        table_legacy_key="import",
    )
    ignore_payload = read_target_ignore_table(
        target_payload=target_payload,
        preset_payload=preset_payload,
        manifest_path=manifest_path,
        target_name=target_name,
    )
    gitignore = (
        normalize_gitignore_list(ignore_payload.get("gitignore"))
        if ignore_payload is not None
        else None
    )
    path_rules = normalize_target_path_rules(
        get_target_value(target_payload=target_payload, preset_payload=preset_payload, key="path_rules"),
        manifest_path=manifest_path,
        target_name=target_name,
        default_command_elevation=default_command_elevation,
    )
    if probe is not None:
        forbidden_probe_fields = {
            "source": source,
            "path": path,
            "type": target_type,
            "chmod": chmod,
            "render": render,
            "capture": capture,
            "reconcile": reconcile,
            "pull_view_repo": pull_view_repo,
            "pull_view_live": pull_view_live,
            "push_ignore": push_ignore,
            "pull_ignore": pull_ignore,
            "gitignore": gitignore,
            "path_rules": path_rules or None,
        }
        forbidden = sorted(name for name, value in forbidden_probe_fields.items() if value is not None)
        if forbidden:
            raise ValueError(
                f"package manifest {manifest_path} target '{target_name}' uses probe and must not define: "
                + ", ".join(forbidden)
            )
    return TargetSpec(
        name=target_name,
        declared_in=manifest_path.parent,
        source=source,
        path=path,
        probe=probe,
        target_type=target_type,
        sync_policy=sync_policy,
        chmod=chmod,
        render=render,
        capture=capture,
        reconcile=reconcile,
        pull_view_repo=pull_view_repo,
        pull_view_live=pull_view_live,
        push_ignore=push_ignore,
        pull_ignore=pull_ignore,
        gitignore=gitignore,
        path_rules=path_rules,
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
    default_command_elevation: DefaultCommandElevationMode = "none",
) -> HookSpec:
    commands_payload = hook_payload
    run_noop = False
    if isinstance(hook_payload, dict) and "run" in hook_payload:
        commands_payload = [hook_payload]
    elif isinstance(hook_payload, dict):
        planning_guard = hook_name.startswith("guard_")
        supported_keys = {"commands"} if planning_guard else {"commands", "run_noop"}
        unknown_keys = sorted(key for key in hook_payload if key not in supported_keys)
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
        default_command_elevation=default_command_elevation,
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


VALID_GITIGNORE_OPS = frozenset({"push", "pull"})


def normalize_gitignore_list(value: Any) -> tuple[str, ...] | None:
    """Normalize and validate a gitignore ops list.

    Accepts a list of operation names ("push", "pull"). Returns None
    when absent (inherit repo default), or tuple of ops (possibly empty
    to explicitly disable).
    """
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"gitignore must be a list[str], got {type(value).__name__}")
    for op in value:
        if op not in VALID_GITIGNORE_OPS:
            raise ValueError(f"gitignore only supports 'push' and 'pull', got '{op}'")
    return tuple(value)


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
        probe=override.probe if override.probe is not None else base.probe,
        target_type=override.target_type if override.target_type is not None else base.target_type,
        sync_policy=override.sync_policy if override.sync_policy is not None else base.sync_policy,
        chmod=override.chmod if override.chmod is not None else base.chmod,
        render=override.render if override.render is not None else base.render,
        capture=override.capture if override.capture is not None else base.capture,
        reconcile=override.reconcile if override.reconcile is not None else base.reconcile,
        pull_view_repo=override.pull_view_repo if override.pull_view_repo is not None else base.pull_view_repo,
        pull_view_live=override.pull_view_live if override.pull_view_live is not None else base.pull_view_live,
        push_ignore=override.push_ignore if override.push_ignore is not None else base.push_ignore,
        pull_ignore=override.pull_ignore if override.pull_ignore is not None else base.pull_ignore,
        gitignore=override.gitignore if override.gitignore is not None else base.gitignore,
        path_rules=override.path_rules or base.path_rules,
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


def _split_structured_path(path: str) -> tuple[str, ...]:
    parts = tuple(path.split("."))
    if not parts or any(not part for part in parts):
        raise ValueError(f"invalid package inheritance path '{path}'")
    return parts


def _replace_dataclass_field(value: Any, field_name: str, replacement: Any, *, path: str) -> Any:
    if not is_dataclass(value) or not hasattr(value, field_name):
        raise ValueError(f"package inheritance path '{path}' does not resolve to a structured field")
    return replace(value, **{field_name: replacement})


def _field_default(value: Any, field_name: str, *, path: str) -> Any:
    for field_info in fields(value):
        if field_info.name != field_name:
            continue
        if field_info.default is not MISSING:
            return field_info.default
        if field_info.default_factory is not MISSING:
            return field_info.default_factory()
        raise ValueError(f"cannot remove required package inheritance field '{path}'")
    raise ValueError(f"package inheritance path '{path}' does not resolve to a structured field")


def _remove_structured_path(value: Any, parts: tuple[str, ...], *, path: str) -> Any:
    field_name = parts[0]
    if isinstance(value, dict):
        if field_name not in value:
            return value
        updated = dict(value)
        if len(parts) == 1:
            del updated[field_name]
        else:
            updated[field_name] = _remove_structured_path(value[field_name], parts[1:], path=path)
        return updated

    if not is_dataclass(value) or not hasattr(value, field_name):
        raise ValueError(f"package inheritance path '{path}' does not resolve to a structured field")
    if len(parts) == 1:
        return _replace_dataclass_field(value, field_name, _field_default(value, field_name, path=path), path=path)

    child = getattr(value, field_name)
    if child is None:
        return value
    replacement = _remove_structured_path(child, parts[1:], path=path)
    return _replace_dataclass_field(value, field_name, replacement, path=path)


def _append_hook_commands(
    hook: HookSpec,
    values: Any,
    *,
    package: PackageSpec,
    default_command_elevation: DefaultCommandElevationMode,
) -> HookSpec:
    # Manifest syntax treats a hook value as its command list, while the
    # normalized model stores that list inside HookSpec.
    manifest_path = package.package_root / "package.toml"
    commands = normalize_hook_command_specs(
        values,
        manifest_kind="package manifest",
        manifest_path=manifest_path,
        owner_label=f"package hook '{hook.name}'",
        hook_name=hook.name,
        default_command_elevation=default_command_elevation,
    )
    return replace(
        hook,
        commands=(*hook.commands, *commands),
        declared_in=package.package_root,
    )


def _normalize_append_values(
    current: Any,
    values: Any,
    *,
    package: PackageSpec,
    default_command_elevation: DefaultCommandElevationMode,
    path: str,
) -> Any:
    if isinstance(current, HookSpec):
        return _append_hook_commands(
            current,
            values,
            package=package,
            default_command_elevation=default_command_elevation,
        )
    if not isinstance(values, list):
        raise ValueError(f"append target '{path}' must receive a list")
    if "hooks" in path.split(".") and path.endswith(".commands"):
        hook_name = path.split(".")[-2]
        commands = normalize_hook_command_specs(
            values,
            manifest_kind="package manifest",
            manifest_path=package.package_root / "package.toml",
            owner_label=f"package hook '{hook_name}'",
            hook_name=hook_name,
            default_command_elevation=default_command_elevation,
        )
        return (*current, *commands) if isinstance(current, tuple) else [*current, *commands]
    if isinstance(current, tuple):
        # TOML lists normalize to tuples in immutable domain models.
        if path.endswith(".path_rules"):
            target_name = path.split(".")[-2]
            normalized = normalize_target_path_rules(
                values,
                manifest_path=package.package_root / "package.toml",
                target_name=target_name,
                default_command_elevation=default_command_elevation,
            )
            return (*current, *normalized)
        if (current and isinstance(current[0], str)) or path.endswith(
            (".depends", ".reserved_paths", ".push_ignore", ".pull_ignore", ".gitignore")
        ):
            if not all(isinstance(item, str) for item in values):
                raise ValueError(f"append target '{path}' must contain only strings")
            return (*current, *values)
        raise ValueError(f"append target '{path}' has unsupported list element type")
    if isinstance(current, list):
        return [*current, *values]
    raise ValueError(f"append target '{path}' is not a list")


def _append_structured_path(
    value: Any,
    parts: tuple[str, ...],
    values: Any,
    *,
    package: PackageSpec,
    default_command_elevation: DefaultCommandElevationMode,
    path: str,
) -> Any:
    if not parts:
        return _normalize_append_values(
            value,
            values,
            package=package,
            default_command_elevation=default_command_elevation,
            path=path,
        )

    field_name = parts[0]
    if isinstance(value, dict):
        if field_name not in value:
            raise ValueError(f"append target '{path}' does not exist")
        updated = dict(value)
        updated[field_name] = _append_structured_path(
            value[field_name],
            parts[1:],
            values,
            package=package,
            default_command_elevation=default_command_elevation,
            path=path,
        )
        return updated

    if not is_dataclass(value) or not hasattr(value, field_name):
        raise ValueError(f"append target '{path}' does not resolve to a structured field")
    child = getattr(value, field_name)
    replacement = _append_structured_path(
        child,
        parts[1:],
        values,
        package=package,
        default_command_elevation=default_command_elevation,
        path=path,
    )
    return _replace_dataclass_field(value, field_name, replacement, path=path)


def _iter_append_paths(payload: dict[str, Any], prefix: tuple[str, ...] = ()):
    for key, value in payload.items():
        current_path = (*prefix, key)
        if isinstance(value, dict):
            yield from _iter_append_paths(value, current_path)
        else:
            yield ".".join(current_path), value


def patch_remove_and_append(
    package: PackageSpec,
    remove_paths: tuple[str, ...],
    append_payload: dict[str, Any],
    *,
    default_command_elevation: DefaultCommandElevationMode = "none",
) -> PackageSpec:
    patched = package
    for dotted_path in remove_paths:
        path = _split_structured_path(dotted_path)
        patched = _remove_structured_path(patched, path, path=dotted_path)

    for dotted_path, values in _iter_append_paths(append_payload):
        patched = _append_structured_path(
            patched,
            _split_structured_path(dotted_path),
            values,
            package=patched,
            default_command_elevation=default_command_elevation,
            path=dotted_path,
        )
    return patched
