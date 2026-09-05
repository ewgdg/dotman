"""Frozen file Observation; no review state, Approval or execution plans."""

from __future__ import annotations

import os
import stat
from contextlib import ExitStack
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from dotman import planning, projection
from dotman.file_access import read_bytes
from dotman.manifest import resolve_sync_policy
from dotman.models import ResolvedSyncScope, ResolvedSyncTarget
from dotman.planning_guards import evaluate_hierarchical_guards
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
    Missing,
    SyncBaseRecord,
    SyncBaseStore,
)

FileState = FilePresent | Missing
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


def _identity(metadata: projection.TargetMetadata) -> ResolvedSyncTarget:
    return ResolvedSyncTarget(
        repo=metadata.repo_name,
        package_id=metadata.package_id,
        target_name=metadata.target_name,
        bound_profile=metadata.bound_profile,
    )


def _read_endpoint(
    path: Path, *, repository: bool, follow_missing: bool = False
) -> tuple[FileState, bool, int | None]:
    try:
        shape = path.lstat()
    except FileNotFoundError:
        return Missing(), False, None
    is_symlink = stat.S_ISLNK(shape.st_mode)
    if is_symlink:
        if repository:
            raise ValueError("repository endpoint must not be a symlink")
        try:
            shape = path.stat()
        except FileNotFoundError as exc:
            if follow_missing:
                return Missing(), True, None
            raise ValueError(
                "prompt-mode live symlink requires a regular-file referent"
            ) from exc
    if not stat.S_ISREG(shape.st_mode):
        raise ValueError("endpoint must be a regular file")
    return FilePresent(read_bytes(path)), is_symlink, stat.S_IMODE(shape.st_mode)


_ResolvedInputs = dict[
    ResolvedSyncTarget, tuple[planning.PackagePlanningInput, projection.TargetMetadata]
]


def _resolve_inputs(
    context: planning.PlanningContext,
    scope: ResolvedSyncScope,
) -> tuple[_ResolvedInputs, dict[str, list[planning.PackagePlanningInput]]]:
    if any(target.child_path is not None for target in scope.targets):
        raise ValueError("file SyncSession requires file-target scopes")
    selected = set(scope.targets)
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
                if (
                    entry.probe_command is not None
                    or entry.target.target_type == "directory"
                ):
                    raise ValueError("file SyncSession requires file targets")
                inputs.setdefault(_identity(entry), (item, entry))
            if metadata:
                narrowed.append(replace(item, target_metadata=metadata))
        directional[direction] = narrowed
    if inputs.keys() != selected:
        raise ValueError("resolved Sync scope no longer matches selected configuration")
    # Directional metadata collection must not move pull-only files to the end.
    ordered = {identity: inputs[identity] for identity in scope.targets}
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
        repository, _link, _mode = _read_endpoint(metadata.repo_path, repository=True)
        observation = replace(observation, repository=repository)
        live, live_is_symlink, live_mode = _read_endpoint(
            metadata.live_path,
            repository=False,
            follow_missing=context.config.file_symlink_mode == "follow",
        )
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
                    if isinstance(repository, FilePresent)
                    else None,
                    live=live.content if isinstance(live, FilePresent) else None,
                    view=view,
                    repo_side=repo_side,
                )
                return Missing() if content is None else FilePresent(content)

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
        mode_agrees = (
            effective != "push-only"
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
            observation, diagnostics=(Diagnostic("observation-failed", str(exc)),)
        )


def observe_scope(
    context: planning.PlanningContext,
    scope: ResolvedSyncScope,
    *,
    preview: bool,
) -> tuple[Observation, ...]:
    inputs, directional = _resolve_inputs(context, scope)
    units = {
        identity: _base_unit(context, identity, item, metadata)
        for identity, (item, metadata) in inputs.items()
    }
    with ExitStack() as resources:
        lifecycles, gits, maintenance = {}, {}, {}
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
                        operation="sync",
                        preview=preview,
                    )
            lifecycle = lifecycles.get(identity.repo)
            if lifecycle is not None:
                maintenance[identity] = lifecycle.selected_policy_resolved(
                    units[identity]
                ).deleted

        admitted = {}
        for direction, candidates in directional.items():
            survivors, _skips = evaluate_hierarchical_guards(
                candidates,
                command_runtime=context.projection.command_runtime,
                operation=direction,
                run_noop=False,
            )
            admitted[direction] = {
                _identity(metadata)
                for item in survivors
                for metadata in item.target_metadata
            }

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
            effective = (
                "both"
                if push and pull
                else (
                    "push-only-delete"
                    if unit.configured_policy == "push-only-delete"
                    else "push-only"
                )
                if push
                else "pull-only"
                if pull
                else "no-route"
            )
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
            if lifecycle is not None and fact is not None:
                try:
                    inspection = lifecycle.inspect(unit, fact.head)
                    base = replace(
                        base,
                        status=inspection.status,
                        reason=inspection.reason,
                        record=inspection.record,
                    )
                except SyncBaseGitError as exc:
                    git_failure = Diagnostic("git-failed", str(exc))
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
            observations.append(observation)
        return tuple(observations)
