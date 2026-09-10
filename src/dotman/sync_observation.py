"""Frozen independent Sync Unit Observation; no review state, Approval or execution plans."""

from __future__ import annotations

import os
import stat
from contextlib import ExitStack
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from dotman.sync_path_policy import SyncPathError
from dotman import planning, projection
from dotman.file_access import read_bytes
from dotman.manifest import resolve_sync_policy, sync_policy_allows_operation
from dotman.models import ResolvedSyncScope, ResolvedSyncTarget, target_path_rule_matches
from dotman.planning_guards import evaluate_directional_guards, evaluate_directory_path_rule_guards
from dotman.sync_directory import census_directory, child_metadata
from dotman.sync_base_lifecycle import (
    BaseInputs,
    BaseProfileContext,
    BaseUnit,
    FrozenGitHead,
    SyncBaseGit,
    SyncBaseGitError,
    SyncBaseLifecycle,
)
from dotman.sync_base_store import (
    DATABASE_FILE_NAME,
    FilePresent,
    DirectoryChildPresent,
    SyncBasePayload,
    Missing,
    SyncBaseRecord,
    SyncBaseStore,
)

FileState = SyncBasePayload
ObservationState = Literal["directly-in-sync", "drifted", "observation-failed"]


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str


@dataclass(frozen=True)
class GitEvidence:
    head: FrozenGitHead | None = None
    primary_clean: bool | None = None
    committed: FileState | None = None


@dataclass(frozen=True)
class BaseEvidence:
    status: Literal["usable", "unavailable", "not-applicable"]
    reason: str | None = None
    record: SyncBaseRecord | None = None
    acknowledged: bool = False
    deleted: bool = False


@dataclass(frozen=True)
class Observation:
    identity: ResolvedSyncTarget
    state: ObservationState
    configured_policy: str
    effective_policy: str
    inputs: BaseInputs
    compare_repo: str
    compare_live: str
    git: GitEvidence
    base: BaseEvidence
    repository: FileState | None = None
    live: FileState | None = None
    comparison_repository: FileState | None = None
    comparison_live: FileState | None = None
    chmod: str | None = None
    live_is_symlink: bool = False
    live_mode: int | None = None
    diagnostics: tuple[Diagnostic, ...] = ()
    repository_path: Path | None = None
    live_path: Path | None = None
    repository_blockers: tuple[str, ...] = ()
    live_blockers: tuple[str, ...] = ()
    managed_children: tuple[str, ...] = ()


def _identity(metadata: projection.TargetMetadata) -> ResolvedSyncTarget:
    return ResolvedSyncTarget(
        repo=metadata.repo_name,
        package_id=metadata.package_id,
        target_name=metadata.target_name,
        bound_profile=metadata.bound_profile,
    )


def _read_endpoint(
    path: Path, *, repository: bool, follow_missing: bool = False, directory_child: bool = False, repository_root: Path | None = None
) -> tuple[FileState, bool, int | None]:
    if repository and repository_root is not None:
        if not path.is_relative_to(repository_root):
            raise SyncPathError("repository-confinement", "repository endpoint is outside its repository")
        for parent in (path, *path.parents):
            if parent.is_symlink():
                raise SyncPathError("repository-symlink", f"repository endpoint must not traverse a symlink: {parent}")
            if parent == repository_root:
                break
    try:
        shape = path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return Missing(), False, None
    is_symlink = stat.S_ISLNK(shape.st_mode)
    if is_symlink:
        if repository:
            raise SyncPathError("repository-symlink", "repository endpoint must not be a symlink")
        try:
            shape = path.stat()
        except FileNotFoundError as exc:
            if follow_missing:
                return Missing(), True, None
            raise SyncPathError(
                "symlink-referent", "prompt-mode live symlink requires a regular-file referent"
            ) from exc
    if directory_child and stat.S_ISDIR(shape.st_mode) and not is_symlink:
        return Missing(), False, None
    if not stat.S_ISREG(shape.st_mode):
        raise SyncPathError("unsupported-entry", "endpoint must be a regular file")
    return FilePresent(read_bytes(path)), is_symlink, stat.S_IMODE(shape.st_mode)


_ResolvedInputs = dict[
    ResolvedSyncTarget, tuple[planning.PackagePlanningInput, projection.TargetMetadata]
]


def _resolve_inputs(
    context: planning.PlanningContext,
    scope: ResolvedSyncScope,
) -> tuple[_ResolvedInputs, dict[str, list[planning.PackagePlanningInput]]]:
    selected = {replace(target, child_path=None) for target in scope.targets}
    inputs = {}
    directional = {}
    for direction in ("push", "pull"):
        candidates, _ = planning.collect_static_target_candidates(
            context,
            list(scope.package_selections),
            operation=direction,
        )
        narrowed = []
        for item in candidates:
            metadata = [
                entry for entry in item.target_metadata if _identity(entry) in selected
            ]
            for entry in metadata:
                if entry.target.target_type is None and (entry.repo_path.is_dir() or entry.live_path.is_dir()):
                    entry = replace(entry, target=replace(entry.target, target_type="directory"))
                inputs.setdefault(_identity(entry), (item, entry))
            # Empty selected scopes may retain independently noop-eligible hooks.
            narrowed.append(replace(item, target_metadata=metadata))
        directional[direction] = narrowed
    if inputs.keys() != selected:
        raise ValueError("resolved Sync scope no longer matches selected configuration")
    # Directional metadata collection must not move pull-only files to the end.
    ordered = {replace(identity, child_path=None): inputs[replace(identity, child_path=None)] for identity in scope.targets}
    # Full targets need every potentially configured child direction. Exact
    # child scopes already know their policies, so unrelated rules must not
    # activate ancestor or target Guards for an unselected direction.
    for identity, (item, metadata) in ordered.items():
        if metadata.target.target_type != "directory":
            continue
        selected_paths = {target.child_path for target in scope.targets if replace(target, child_path=None) == identity}
        if None in selected_paths:
            policies = {resolve_sync_policy(package=metadata.package, target=metadata.target)}
            policies.update(rule.sync_policy for rule in metadata.path_rules if rule.sync_policy is not None)
        else:
            policies = {
                resolve_sync_policy(package=metadata.package, target=child_metadata(metadata, path).target)
                for path in selected_paths
            }
        for direction, candidates in directional.items():
            index = next(index for index, candidate in enumerate(candidates) if candidate.selection == item.selection)
            candidate = candidates[index]
            allowed = any(sync_policy_allows_operation(policy, operation=direction) for policy in policies)
            targets = [entry for entry in candidate.target_metadata if allowed or _identity(entry) != identity]
            if allowed and not any(_identity(entry) == identity for entry in targets):
                targets.append(replace(metadata, command_env={**metadata.command_env, "DOTMAN_OPERATION": direction}))
            candidates[index] = replace(candidate, target_metadata=targets)
    return ordered, directional


def _base_unit(
    context: planning.PlanningContext,
    identity: ResolvedSyncTarget,
    item: planning.PackagePlanningInput,
    metadata: projection.TargetMetadata,
) -> BaseUnit:
    return BaseUnit(
        identity,
        metadata.repo_path.relative_to(item.repo.root).as_posix(),
        resolve_sync_policy(package=metadata.package, target=metadata.target),
        BaseInputs(
            render=metadata.render_command or "raw",
            capture=metadata.capture_command or "raw",
            path_rules=tuple(rule.name for rule in metadata.path_rules
                             if identity.child_path is not None and target_path_rule_matches(identity.child_path, rule.pattern)),
            profile_context=BaseProfileContext(item.package_context.context),
            file_symlink_mode=context.config.file_symlink_mode,
            dir_symlink_mode=context.config.dir_symlink_mode,
        ),
    )


def _observe_file(
    context: planning.PlanningContext,
    identity: ResolvedSyncTarget,
    item: planning.PackagePlanningInput,
    metadata: projection.TargetMetadata,
    unit: BaseUnit,
    effective: str,
    git: GitEvidence,
    base: BaseEvidence,
) -> Observation:
    observation = Observation(
        identity,
        "observation-failed",
        unit.configured_policy,
        effective,
        unit.inputs,
        metadata.compare_repo,
        metadata.compare_live,
        git,
        base,
        chmod=metadata.chmod,
        repository_path=metadata.repo_path,
        live_path=metadata.live_path,
    )
    if effective == "no-route":
        return replace(
            observation,
            diagnostics=(
                Diagnostic(
                    "no-route", "directional Guards removed every convergence route"
                ),
            ),
        )
    try:
        repository, _link, repository_mode = _read_endpoint(metadata.repo_path, repository=True, directory_child=identity.child_path is not None, repository_root=item.repo.root)
        if identity.child_path is not None and isinstance(repository, FilePresent):
            repository = DirectoryChildPresent(repository.content, projection.file_is_executable(repository_mode))
        observation = replace(observation, repository=repository)
        live, live_is_symlink, live_mode = _read_endpoint(
            metadata.live_path,
            repository=False, directory_child=identity.child_path is not None,
            follow_missing=context.config.file_symlink_mode == "follow",
        )
        if identity.child_path is not None and isinstance(live, FilePresent):
            live = DirectoryChildPresent(live.content, projection.file_is_executable(live_mode))
        observation = replace(
            observation, live=live, live_is_symlink=live_is_symlink, live_mode=live_mode
        )
        if effective == "push-only-delete":
            compared_repo, compared_live = Missing(), live
        else:

            def projected(view: str, *, repo_side: bool) -> FileState:
                content = projection.project_frozen_file(
                    context.projection.command_runtime,
                    metadata=metadata,
                    context=item.package_context.context,
                    repository=repository.content
                    if isinstance(repository, (FilePresent, DirectoryChildPresent))
                    else None,
                    live=live.content if isinstance(live, (FilePresent, DirectoryChildPresent)) else None,
                    view=view,
                    repo_side=repo_side,
                )
                return Missing() if content is None else (
                    DirectoryChildPresent(content, (repository if repo_side else live).executable)
                    if identity.child_path is not None else FilePresent(content)
                )

            compared_repo = projected(
                "render" if effective == "push-only" else metadata.compare_repo,
                repo_side=True,
            )
            compared_live = (
                live
                if effective == "push-only"
                else projected(
                    metadata.compare_live,
                    repo_side=False,
                )
            )
        if effective == "push-only" and metadata.chmod is not None and isinstance(compared_repo, DirectoryChildPresent):
            compared_repo = replace(compared_repo, executable=projection.file_is_executable(int(metadata.chmod, 8)))
        exact_mode_active = effective == "push-only" or (identity.child_path is not None and effective == "both")
        mode_agrees = (
            not exact_mode_active
            or isinstance(compared_repo, Missing)
            or metadata.chmod is None
            or live_mode == int(metadata.chmod, 8)
        )
        return replace(
            observation,
            state="directly-in-sync"
            if compared_repo == compared_live and mode_agrees
            else "drifted",
            comparison_repository=compared_repo,
            comparison_live=compared_live,
        )
    except (OSError, ValueError) as exc:
        return replace(
            observation, diagnostics=(Diagnostic(exc.code if isinstance(exc, SyncPathError) else "observation-failed", str(exc)),)
        )


def _effective_policy(configured: str, push: bool, pull: bool) -> str:
    if push and pull:
        return "both"
    if push:
        return "push-only-delete" if configured == "push-only-delete" else "push-only"
    return "pull-only" if pull else "no-route"


@dataclass(frozen=True)
class ObservedScope:
    observations: tuple[Observation, ...]
    directional: dict[str, list[planning.PackagePlanningInput]]
    hook_scopes: dict[str, frozenset[str]]
    inputs: _ResolvedInputs
    directory_censuses: tuple = ()


def _discard_ineligible_bases(
    context: planning.PlanningContext,
    inputs: _ResolvedInputs,
    *,
    preview: bool,
) -> dict[ResolvedSyncTarget, bool]:
    """Resolved configured policy is authoritative before volatile Guards."""
    deleted = {}
    if preview:
        return deleted
    with ExitStack() as resources:
        stores = {}
        for identity, (item, metadata) in inputs.items():
            unit = _base_unit(context, identity, item, metadata)
            if unit.eligible:
                continue
            if identity.repo not in stores:
                stores[identity.repo] = resources.enter_context(SyncBaseStore.open(
                    context.tracked_state.state_root, item.repo.config.state_key,
                ))
            deleted[identity] = stores[identity.repo].delete(unit.identity_bytes)
    return deleted


def observe_scope(
    context: planning.PlanningContext,
    scope: ResolvedSyncScope,
    *,
    preview: bool,
    run_noop: bool = False,
    resolved_inputs: tuple[_ResolvedInputs, dict[str, list[planning.PackagePlanningInput]]] | None = None,
    directions: tuple[str, ...] = ("push", "pull"),
    read_bases: bool = True,
    base_operation: str = "sync",
    omit_no_route: bool = False,
) -> ObservedScope:
    inputs, directional = resolved_inputs if resolved_inputs is not None else _resolve_inputs(context, scope)
    directional = {direction: candidates if direction in directions else [] for direction, candidates in directional.items()}
    inputs = {
        identity: value for identity, value in inputs.items()
        if value[1].target.target_type == "directory" or any(
            sync_policy_allows_operation(resolve_sync_policy(package=value[1].package, target=value[1].target), operation=direction)
            for direction in directions
        )
    }
    probe_inputs = {identity: value for identity, value in inputs.items() if value[1].probe_command is not None}
    inputs = {identity: value for identity, value in inputs.items() if identity not in probe_inputs}
    directory_inputs = {identity: value for identity, value in inputs.items() if value[1].target.target_type == "directory"}
    ordered_inputs = inputs
    inputs = {identity: value for identity, value in inputs.items() if identity not in directory_inputs}
    maintenance = _discard_ineligible_bases(context, inputs, preview=preview) if read_bases else {}
    # Resolve the control-aware child workset before volatile ancestor Guards;
    # configured ineligibility must survive a later Guard failure. Reuse this
    # census for Observation rather than discovering children a second time.
    resolved_directories = {}
    for identity, (item, metadata) in directory_inputs.items():
        selected_paths = {target.child_path for target in scope.targets if replace(target, child_path=None) == identity}
        census = census_directory(
            metadata, follow_live_directories=context.config.dir_symlink_mode == "follow",
            selected_paths=tuple(sorted(path for path in selected_paths if path is not None)),
        )
        children = {
            relative: (replace(identity, child_path=relative or None), child_metadata(metadata, relative), failures)
            for relative, failures in census.entries
            if (None in selected_paths or relative in selected_paths)
            and any(sync_policy_allows_operation(resolve_sync_policy(package=child_metadata(metadata, relative).package, target=child_metadata(metadata, relative).target), operation=direction) for direction in directions)
        }
        maintenance.update(_discard_ineligible_bases(context, {
            child_identity: (item, child) for child_identity, child, failures in children.values()
            if child_identity.child_path is not None and not failures
        }, preview=preview) if read_bases else {})
        resolved_directories[identity] = (selected_paths, census, children)
    configured_directional = directional
    eligibility = evaluate_directional_guards(
        directional, command_runtime=context.projection.command_runtime, run_noop=run_noop,
    )
    directional = {direction: value.inputs for direction, value in eligibility.items()}
    admitted = {
        direction: {_identity(metadata) for item in survivors for metadata in item.target_metadata}
        for direction, survivors in directional.items()
    }

    child_policies, child_failures, child_topology = {}, {}, {}
    directory_censuses = []
    expanded_inputs = {**inputs, **probe_inputs}
    for identity, (item, metadata) in directory_inputs.items():
        selected_paths, census, children = resolved_directories[identity]
        # Any Guard narrowing invalidates a complete participation proof.
        configured_directions = {direction for direction in ("push", "pull")
                                 if any(_identity(target) == identity for candidate in configured_directional[direction]
                                        for target in candidate.target_metadata)}
        complete = (None in selected_paths and census.unrestricted
                    and all(identity in admitted[direction] for direction in configured_directions)
                    and not any((rule.hooks or {}).get("guard_push") or (rule.hooks or {}).get("guard_pull") for rule in metadata.path_rules))
        directory_censuses.append((identity, item, census, complete))
        child_admitted = {}
        for direction in ("push", "pull"):
            candidates = {
                relative for relative, (_child, child, _failures) in children.items()
                if identity in admitted[direction] and sync_policy_allows_operation(
                    resolve_sync_policy(package=child.package, target=child.target), operation=direction)
            }
            child_admitted[direction], _skips = evaluate_directory_path_rule_guards(
                command_runtime=context.projection.command_runtime, path_rules=metadata.path_rules,
                candidate_paths=candidates, operation=direction, context=item.package_context.context,
                target_env={**metadata.command_env, "DOTMAN_OPERATION": direction},
                repo_name=identity.repo, package_id=identity.package_id,
                bound_profile=identity.bound_profile, target_name=identity.target_name,
            )
        for relative, (child_identity, child, failures) in children.items():
            child_topology[child_identity] = (census.blockers(relative, repository=True), census.blockers(relative, repository=False), tuple(path for path, failures in census.entries if not failures))
            expanded_inputs[child_identity] = (item, child)
            unit = _base_unit(context, child_identity, item, child)
            child_policies[child_identity] = _effective_policy(unit.configured_policy, relative in child_admitted["push"], relative in child_admitted["pull"])
            child_failures[child_identity] = failures

    inputs = {identity: value for identity, value in expanded_inputs.items() if identity not in probe_inputs}
    if omit_no_route:
        inputs = {
            identity: value for identity, value in inputs.items()
            if child_policies.get(identity, _effective_policy(
                resolve_sync_policy(package=value[1].package, target=value[1].target),
                identity in admitted["push"], identity in admitted["pull"],
            )) != "no-route"
        }
    units = {
        identity: _base_unit(context, identity, item, metadata)
        for identity, (item, metadata) in inputs.items()
    }
    with ExitStack() as resources:
        lifecycles, gits = {}, {}
        for identity, (item, _metadata) in inputs.items():
            if identity.repo not in gits:
                git = SyncBaseGit(item.repo.root, context.projection.command_runtime)
                gits[identity.repo] = git
                # Absence is ordinary Base evidence, not permission to create
                # storage during preview. Existing store artifacts go through
                # the store's security validation rather than being hidden.
                directory = (
                    context.tracked_state.state_root
                    / "repos"
                    / item.repo.config.state_key
                )
                try:
                    store_exists = any(
                        name.startswith(DATABASE_FILE_NAME)
                        for name in os.listdir(directory)
                    )
                except FileNotFoundError:
                    store_exists = False
                if not preview or store_exists:
                    store = resources.enter_context(
                        SyncBaseStore.open(
                            context.tracked_state.state_root,
                            item.repo.config.state_key,
                            read_only=preview,
                        )
                    )
                    lifecycles[identity.repo] = SyncBaseLifecycle(
                        store,
                        git,
                        operation=base_operation,
                        preview=preview,
                    )
            lifecycle = lifecycles.get(identity.repo)
            if lifecycle is not None:
                maintenance[identity] = lifecycle.selected_policy_resolved(
                    units[identity]
                ).deleted or maintenance.get(identity, False)

        frozen, git_failures = {}, {}
        for repo_name, git in gits.items():
            repo_units = tuple(
                unit for identity, unit in units.items() if identity.repo == repo_name
            )
            try:
                head = git.freeze_head()
                for fact in git.freeze_units(head, repo_units):
                    frozen[fact.unit.identity] = fact
            except SyncBaseGitError as exc:
                git_failures[repo_name] = Diagnostic("git-failed", str(exc))

        observations = []
        for identity, (item, metadata) in inputs.items():
            unit = units[identity]
            push, pull = identity in admitted["push"], identity in admitted["pull"]
            effective = child_policies.get(identity, _effective_policy(unit.configured_policy, push, pull))
            lifecycle = lifecycles.get(identity.repo)
            fact = frozen.get(identity)
            git_evidence = (
                GitEvidence()
                if fact is None
                else GitEvidence(
                    fact.head,
                    fact.primary_clean,
                    fact.payload,
                )
            )
            base = BaseEvidence(
                "unavailable" if unit.eligible else "not-applicable",
                "absent" if unit.eligible else "ineligible",
                deleted=maintenance.get(identity, False),
            )
            git_failure = git_failures.get(identity.repo)
            if read_bases and lifecycle is not None and fact is not None:
                try:
                    inspection = lifecycle.maintain(unit, fact.head)
                    base = replace(
                        base,
                        status=inspection.status,
                        reason=inspection.reason,
                        record=inspection.record,
                    )
                except SyncBaseGitError as exc:
                    git_failure = Diagnostic("git-failed", str(exc))
            if child_failures.get(identity):
                observation = Observation(
                    identity, "observation-failed", unit.configured_policy, effective,
                    unit.inputs, metadata.compare_repo, metadata.compare_live, git_evidence, base,
                    chmod=metadata.chmod, repository_path=metadata.repo_path, live_path=metadata.live_path,
                    diagnostics=tuple(Diagnostic(failure.code, failure.message) for failure in child_failures[identity]),
                )
            else:
                observation = _observe_file(
                    context, identity, item, metadata, unit, effective, git_evidence, base
                )
            if fact is not None and fact.failure is not None:
                git_failure = Diagnostic("git-failed", str(fact.failure))
            if git_failure is not None:
                observation = replace(
                    observation,
                    state="observation-failed",
                    diagnostics=(*observation.diagnostics, git_failure),
                )
            elif observation.state == "directly-in-sync" and lifecycle is not None:
                result = lifecycle.direct_agreement(fact)
                observation = replace(
                    observation, base=replace(base, acknowledged=result.acknowledged)
                )
                if result.failure is not None:
                    observation = replace(
                        observation,
                        diagnostics=(
                            Diagnostic(
                                "base-acknowledgment-failed", str(result.failure)
                            ),
                        ),
                    )
            if identity in child_topology:
                repository_blockers, live_blockers, managed_children = child_topology[identity]
                observation = replace(observation, repository_blockers=repository_blockers, live_blockers=live_blockers, managed_children=managed_children)
            observations.append(observation)
        order = {identity: index for index, identity in enumerate(ordered_inputs)}
        observations.sort(key=lambda unit: (order[replace(unit.identity, child_path=None)], unit.identity.child_path or ""))
        return ObservedScope(tuple(observations), directional, {
            direction: value.hook_scopes for direction, value in eligibility.items()
        }, expanded_inputs, tuple(directory_censuses))
