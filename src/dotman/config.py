from __future__ import annotations

import os
import tomllib
from pathlib import Path

from dotman.models import ManagerConfig, RepoConfig


def default_config_path() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "dotman" / "config.toml"


def default_state_root() -> Path:
    state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return state_home / "dotman"


def expand_path(value: str, *, base_dir: Path | None = None) -> Path:
    expanded = Path(os.path.expandvars(os.path.expanduser(value)))
    if not expanded.is_absolute() and base_dir is not None:
        expanded = base_dir / expanded
    return expanded.resolve()


def load_manager_config(config_path: str | Path | None = None) -> ManagerConfig:
    resolved_path = Path(config_path) if config_path is not None else default_config_path()
    resolved_path = resolved_path.expanduser().resolve()
    payload = tomllib.loads(resolved_path.read_text(encoding="utf-8"))
    repos_payload = payload.get("repos")
    if not isinstance(repos_payload, dict) or not repos_payload:
        raise ValueError("config must define at least one [repos.<name>] entry")

    repos: dict[str, RepoConfig] = {}
    seen_orders: dict[int, str] = {}
    for repo_name, repo_payload in repos_payload.items():
        if not isinstance(repo_payload, dict):
            raise ValueError(f"repo entry '{repo_name}' must be a table")
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
        state_path_value = repo_payload.get("state_path")
        state_path = (
            expand_path(state_path_value, base_dir=resolved_path.parent)
            if isinstance(state_path_value, str)
            else (default_state_root() / repo_name).resolve()
        )
        repos[repo_name] = RepoConfig(
            name=repo_name,
            path=expand_path(repo_path_value, base_dir=resolved_path.parent),
            order=order_value,
            state_path=state_path,
        )

    return ManagerConfig(config_path=resolved_path, repos=repos)
