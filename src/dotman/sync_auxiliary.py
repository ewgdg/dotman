"""Freeze non-payload work and retain only Guard-admitted hook families."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal
import stat

from dotman.command_runtime import CommandRuntime
from dotman.models import ResolvedSyncTarget, package_ref_text
from dotman.planning import PackagePlanningInput
from dotman.projection import run_probe_command
from dotman.sync_observation import Diagnostic, _ResolvedInputs, _identity
from dotman.sync_publication import PublicationMetadata


@dataclass(frozen=True)
class AuxiliaryRow:
    row_id: str
    kind: Literal["probe", "hook", "directory-root"]
    included: bool
    scope: str
    directions: tuple[str, ...]
    allowed_commands: tuple[Literal["set-included"], ...] = ("set-included",)
    diagnostics: tuple[Diagnostic, ...] = ()


def _package_scope(repo: str, package_id: str, profile: str | None) -> str:
    return f"{repo}:{package_ref_text(package_id=package_id, bound_profile=profile)}"


def retain_directional_hooks(
    metadata: PublicationMetadata, scopes: frozenset[str],
) -> PublicationMetadata:
    """Keep payload metadata for completion, but never restore a removed hook route."""
    return replace(
        metadata,
        repo_hooks=tuple((repo, hooks if repo in scopes else ()) for repo, hooks in metadata.repo_hooks),
        packages=tuple(replace(
            package,
            hooks={
                name: [hook for hook in hooks if
                    _package_scope(package.repo_name, package.package_id, package.bound_profile) in scopes
                    and (hook.scope_kind != "target" or ResolvedSyncTarget(
                        package.repo_name, package.package_id, hook.target_name,
                        bound_profile=package.bound_profile,
                    ).canonical in scopes)
                ]
                for name, hooks in package.hooks.items()
            },
        ) for package in metadata.packages),
    )


def plan_auxiliary(
    inputs: _ResolvedInputs,
    directional: dict[str, list[PackagePlanningInput]],
    metadata: dict[str, PublicationMetadata],
    *,
    command_runtime: CommandRuntime,
    run_noop: bool,
) -> tuple[AuxiliaryRow, ...]:
    admitted = {
        direction: {_identity(target) for item in survivors for target in item.target_metadata}
        for direction, survivors in directional.items()
    }
    rows = []
    for identity, (_item, target) in inputs.items():
        directions = tuple(direction for direction in ("push", "pull") if identity in admitted[direction])
        if (target.target.target_type == "directory" and "push" in directions and target.chmod is not None
                and target.live_path.is_dir() and not target.live_path.is_symlink()
                and stat.S_IMODE(target.live_path.stat().st_mode) != int(target.chmod, 8)):
            rows.append(AuxiliaryRow(identity.canonical, "directory-root", False, identity.canonical, ("push",)))
        if target.probe_command is not None and directions and run_probe_command(command_runtime, target):
            rows.append(AuxiliaryRow(identity.canonical, "probe", False, identity.canonical, directions))

    for direction, frozen in metadata.items():
        scopes = {}
        for repo, hooks in frozen.repo_hooks:
            if any(run_noop or hook.run_noop for hook in hooks):
                scopes[repo] = None
        for package in frozen.packages:
            package_scope = _package_scope(package.repo_name, package.package_id, package.bound_profile)
            for hooks in package.hooks.values():
                for hook in hooks:
                    if not (run_noop or hook.run_noop):
                        continue
                    scope = (
                        ResolvedSyncTarget(package.repo_name, package.package_id, hook.target_name,
                                           bound_profile=package.bound_profile).canonical
                        if hook.scope_kind == "target" else package_scope
                    )
                    scopes[scope] = None
        rows.extend(
            AuxiliaryRow(f"{scope} ({direction}-hooks)", "hook", False, scope, (direction,))
            for scope in scopes
        )
    return tuple(rows)
