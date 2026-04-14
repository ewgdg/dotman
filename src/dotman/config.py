from __future__ import annotations

import os
from pathlib import Path

from dotman.models import ManagerConfig, RepoConfig, SnapshotConfig
from dotman.toml_utils import load_toml_file


def default_repo_state_dir(state_key: str) -> Path:
    return default_state_root() / "repos" / state_key


def default_config_root() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "dotman"


def default_config_path() -> Path:
    return default_config_root() / "config.toml"


def default_state_root() -> Path:
    state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return state_home / "dotman"


def default_snapshot_root() -> Path:
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return data_home / "dotman" / "snapshots"


def expand_path(value: str, *, base_dir: Path | None = None, dereference: bool = True) -> Path:
    expanded = Path(os.path.expandvars(os.path.expanduser(value)))
    if not expanded.is_absolute() and base_dir is not None:
        expanded = base_dir / expanded
    if dereference:
        return expanded.resolve()
    # Managed target paths should keep the user-declared pathname as identity
    # instead of silently following a live symlink to a different file.
    return Path(os.path.abspath(expanded))


def default_local_override_path(repo_name: str) -> Path:
    return default_config_root() / "repos" / repo_name / "local.toml"


def validate_state_key(state_key: object, *, repo_name: str) -> str:
    if not isinstance(state_key, str):
        raise ValueError(f"repo '{repo_name}' state_key must be a string")
    normalized = state_key.strip()
    if not normalized:
        raise ValueError(f"repo '{repo_name}' state_key must not be empty")
    if normalized in {".", ".."}:
        raise ValueError(f"repo '{repo_name}' state_key must not be '.' or '..'")
    if "/" in normalized or "\\" in normalized:
        raise ValueError(f"repo '{repo_name}' state_key must not contain path separators")
    return normalized


def load_manager_config(config_path: str | Path | None = None) -> ManagerConfig:
    resolved_path = Path(config_path) if config_path is not None else default_config_path()
    resolved_path = resolved_path.expanduser().resolve()
    payload = load_toml_file(resolved_path, context="manager config")
    repos_payload = payload.get("repos")
    if not isinstance(repos_payload, dict) or not repos_payload:
        raise ValueError("config must define at least one [repos.<name>] entry")

    repos: dict[str, RepoConfig] = {}
    seen_orders: dict[int, str] = {}
    seen_state_keys: dict[str, str] = {}
    for repo_name, repo_payload in repos_payload.items():
        if not isinstance(repo_payload, dict):
            raise ValueError(f"repo entry '{repo_name}' must be a table")
        if "state_path" in repo_payload:
            # Orphan-state discovery only works when every repo state dir is derived from one
            # predictable manager root, so custom per-repo state paths are no longer supported.
            raise ValueError(f"repo '{repo_name}' uses unsupported key 'state_path'; use state_key and migrate bindings under the dotman state root")
        repo_path_value = repo_payload.get("path")
        order_value = repo_payload.get("order")
        if not isinstance(repo_path_value, str):
            raise ValueError(f"repo '{repo_name}' must define string path")
        if not isinstance(order_value, int):
            raise ValueError(f"repo '{repo_name}' must define integer order")
        if order_value in seen_orders:
            raise ValueError(
                f"repo order values must be unique: {repo_name} and {seen_orders[order_value]} both use {order_value}"
            )
        seen_orders[order_value] = repo_name
        state_key_value = repo_payload.get("state_key", repo_name)
        state_key = validate_state_key(state_key_value, repo_name=repo_name)
        if state_key in seen_state_keys:
            raise ValueError(
                f"repo state_key values must be unique: {repo_name} and {seen_state_keys[state_key]} both use '{state_key}'"
            )
        seen_state_keys[state_key] = repo_name
        state_path = default_repo_state_dir(state_key).resolve()
        repos[repo_name] = RepoConfig(
            name=repo_name,
            path=expand_path(repo_path_value, base_dir=resolved_path.parent),
            order=order_value,
            state_key=state_key,
            state_path=state_path,
            local_override_path=default_local_override_path(repo_name).resolve(),
        )

    snapshots_payload = payload.get("snapshots", {})
    if not isinstance(snapshots_payload, dict):
        raise ValueError("config [snapshots] must be a table")
    enabled_value = snapshots_payload.get("enabled", True)
    if not isinstance(enabled_value, bool):
        raise ValueError("config snapshots.enabled must be a boolean")
    snapshot_path_value = snapshots_payload.get("path")
    snapshot_path = (
        expand_path(snapshot_path_value, base_dir=resolved_path.parent)
        if isinstance(snapshot_path_value, str)
        else default_snapshot_root().resolve()
    )
    max_generations_value = snapshots_payload.get("max_generations", 10)
    if not isinstance(max_generations_value, int) or max_generations_value <= 0:
        raise ValueError("config snapshots.max_generations must be a positive integer")

    return ManagerConfig(
        config_path=resolved_path,
        repos=repos,
        snapshots=SnapshotConfig(
            enabled=enabled_value,
            path=snapshot_path,
            max_generations=max_generations_value,
        ),
    )
