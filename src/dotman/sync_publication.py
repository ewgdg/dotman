"""Publish approved, frozen file effects without projecting or observing sources."""

from __future__ import annotations

import stat
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Literal, Protocol, Sequence

from dotman import file_access
from dotman.command_runtime import INTERRUPTED_EXIT_CODE, CommandRuntime, command_runtime_session, current_command_runtime
from dotman.elevation import elevation_broker_session
from dotman.execution import ExecutionStep, ExecutionStepResult, _execute_step
from dotman.models import HookPlan, PackagePlan, ResolvedSyncTarget, SnapshotConfig, TargetPlan
from dotman.planning import PackagePlanningInput, plan_hooks, plan_repo_hooks
from dotman.snapshot import SnapshotRecord, create_push_snapshot, mark_snapshot_status


class Effect(Protocol):
    @property
    def kind(self) -> Literal["write", "delete", "chmod"]: ...
    @property
    def path(self) -> Path: ...
    @property
    def content(self) -> bytes | None: ...
    @property
    def mode(self) -> int | None: ...


@dataclass(frozen=True)
class PublicationUnit:
    row_id: str
    identity: ResolvedSyncTarget
    effects: tuple[Effect, ...]


@dataclass(frozen=True)
class PublicationMetadata:
    packages: tuple[PackagePlan, ...]
    repo_hooks: tuple[tuple[str, tuple[HookPlan, ...]], ...]


@dataclass(frozen=True)
class PublicationUnitResult:
    row_id: str
    status: Literal["ok", "failed", "skipped"]
    error: str | None = None


@dataclass(frozen=True)
class PublicationResult:
    units: tuple[PublicationUnitResult, ...]
    error: str | None = None
    steps: tuple[ExecutionStepResult, ...] = ()
    snapshot: SnapshotRecord | None = None
    interrupted: bool = False


def prepare_publication(
    inputs: Sequence[PackagePlanningInput], *, file_symlink_mode: str = "prompt",
) -> PublicationMetadata:
    """Freeze only execution metadata and hooks at session open, never payloads."""
    packages = []
    repos = {}
    for item in inputs:
        context = item.package_context
        targets = [
            TargetPlan(
                package_id=target.package_id, target_name=target.target_name,
                repo_path=target.repo_path, live_path=target.live_path,
                action="noop", target_kind="file", projection_kind="raw",
                command_cwd=target.command_cwd, command_env=dict(target.command_env),
                file_symlink_mode=file_symlink_mode,
            )
            for target in item.target_metadata
        ]
        hooks = plan_hooks(
            item.repo, context.resolved_packages, context.context,
            selection=item.selection, operation="push", inferred_os=context.inferred_os,
            variables=context.variables, target_plans=targets,
            declaration_package_ids={item.selection.identity.package_id},
        )
        # Guards already narrowed capability during Observation and never rerun.
        hooks = {name: values for name, values in hooks.items() if not name.startswith("guard_")}
        packages.append(PackagePlan(
            operation="push", selection=item.selection, variables=dict(context.variables),
            hooks=hooks, target_plans=targets, repo_root=item.repo.root,
            state_path=item.repo.config.state_path, inferred_os=context.inferred_os,
        ))
        if item.repo.config.name not in repos:
            repos[item.repo.config.name] = tuple(
                hook for hooks in plan_repo_hooks(item.repo, operation="push").values()
                for hook in hooks
            )
    return PublicationMetadata(tuple(packages), tuple(repos.items()))


class _PublicationStopped(Exception):
    def __init__(self, message: str, *, interrupted: bool = False):
        super().__init__(message)
        self.interrupted = interrupted


def _failed_step(step: ExecutionStep, error: BaseException) -> ExecutionStepResult:
    interrupted = isinstance(error, (InterruptedError, KeyboardInterrupt))
    return ExecutionStepResult(
        step, "interrupted" if interrupted else "failed",
        exit_code=INTERRUPTED_EXIT_CODE if interrupted else None,
        error=str(error) or ("Publication interrupted" if interrupted else None),
    )


def _effect_path(effect: Effect, target: TargetPlan) -> Path:
    if effect.path != target.live_path:
        raise ValueError("Publication effect does not match its frozen live endpoint")
    path = effect.path
    if path.is_symlink():
        if target.file_symlink_mode == "follow":
            # A followed link may retarget after Observation; policy follows its
            # current referent without adopting any newly observed content.
            path = path.resolve(strict=False)
        elif effect.kind != "delete":
            raise ValueError(f"Live symlink replacement is not authorized: {path}")
    try:
        shape = path.lstat()
    except FileNotFoundError:
        if effect.kind == "chmod":
            raise ValueError(f"Cannot chmod missing live endpoint: {path}") from None
        return path
    if not stat.S_ISREG(shape.st_mode) and not (effect.kind == "delete" and stat.S_ISLNK(shape.st_mode)):
        raise ValueError(f"Publication expects a regular file: {path}")
    return path


def execute_publication(
    metadata: PublicationMetadata,
    units: Sequence[PublicationUnit],
    *,
    snapshot_config: SnapshotConfig,
    complete: Callable[[PublicationUnit], None] | None = None,
    command_runtime: CommandRuntime | None = None,
    stream_output: bool = False,
    assume_yes: bool = False,
) -> PublicationResult:
    """Apply exact effects, retaining completion even when enclosing hooks fail."""
    units = tuple(units)
    by_identity = {unit.identity: unit for unit in units}
    if len(by_identity) != len(units) or len({unit.row_id for unit in units}) != len(units):
        raise ValueError("Duplicate publication unit")
    selected = []
    snapshot_endpoints = []
    matched = set()
    for package in metadata.packages:
        targets = []
        for target in package.target_plans:
            identity = ResolvedSyncTarget(
                repo=package.repo_name, package_id=target.package_id,
                bound_profile=package.bound_profile, target_name=target.target_name,
            )
            unit = by_identity.get(identity)
            if unit is None:
                continue
            matched.add(identity)
            if not unit.effects:
                continue
            for effect in unit.effects:
                if effect.kind not in {"write", "delete", "chmod"}:
                    raise ValueError(f"Unknown publication effect: {effect.kind}")
                if effect.kind == "write" and effect.content is None:
                    raise ValueError("Frozen write requires content")
                if effect.kind == "chmod" and effect.mode is None:
                    raise ValueError("Frozen chmod requires mode")
            snapshot_endpoints.append((unit.effects[0], target))
            targets.append(replace(target, action="delete" if unit.effects[0].kind == "delete" else "update"))
        if targets:
            selected.append(replace(package, target_plans=targets))
    if matched != set(by_identity):
        raise ValueError("Publication unit has no captured metadata")

    results = {
        unit.row_id: PublicationUnitResult(unit.row_id, "ok" if not unit.effects else "skipped")
        for unit in units
    }
    steps = []
    snapshot = None
    error = None
    current_unit = None
    interrupted = False
    snapshot_started = False

    def run_hooks(hooks, name, package=None, target=None):
        for hook in hooks:
            if hook.hook_name != name:
                continue
            step = ExecutionStep(
                repo_name=hook.repo_name or "", package_id=hook.package_id,
                package_plan=package, target_plan=target, kind="hook",
                action=name, scope_kind=hook.scope_kind, hook_plan=hook,
                privileged=hook.elevation == "root",
            )
            result = _execute_step(step, stream_output=stream_output, assume_yes=assume_yes)
            steps.append(result)
            if result.status != "ok":
                raise _PublicationStopped(
                    result.error or result.stderr or f"{name} exited {result.exit_code}",
                    interrupted=result.status == "interrupted",
                )

    with elevation_broker_session(), command_runtime_session(command_runtime or current_command_runtime()):
        try:
            for repo_name, repo_hooks in metadata.repo_hooks:
                repo_packages = [package for package in selected if package.repo_name == repo_name]
                if not repo_packages:
                    continue
                run_hooks(repo_hooks, "pre_push")
                for package in repo_packages:
                    hooks = [hook for values in package.hooks.values() for hook in values]
                    package_hooks = [hook for hook in hooks if hook.scope_kind == "package"]
                    run_hooks(package_hooks, "pre_push", package)
                    for target in package.target_plans:
                        identity = ResolvedSyncTarget(
                            repo=repo_name, package_id=target.package_id,
                            bound_profile=package.bound_profile, target_name=target.target_name,
                        )
                        target_hooks = [hook for hook in hooks if hook.scope_kind == "target" and hook.target_name == target.target_name]
                        run_hooks(target_hooks, "pre_push", package, target)
                        current_unit = by_identity[identity]
                        for effect in current_unit.effects:
                            step = ExecutionStep(
                                repo_name=repo_name, package_id=package.package_id,
                                package_plan=package, target_plan=target,
                                kind="chmod" if effect.kind == "chmod" else "target",
                                action=effect.kind, scope_kind="target",
                            )
                            try:
                                path = _effect_path(effect, target)
                                # Snapshot starts only after pre-hooks and safety,
                                # immediately before the first actual live effect.
                                if not snapshot_started:
                                    try:
                                        if snapshot_config.enabled:
                                            # Snapshot reads the whole selected set, not just
                                            # the next writer. Recheck each initial endpoint
                                            # after pre-hooks so a later FIFO cannot block it.
                                            for initial_effect, snapshot_target in snapshot_endpoints:
                                                _effect_path(initial_effect, snapshot_target)
                                        snapshot = create_push_snapshot(selected, snapshot_config)
                                    except (OSError, ValueError, RuntimeError, KeyboardInterrupt) as exc:
                                        failure = _failed_step(
                                            replace(step, kind="snapshot", action="create"), exc,
                                        )
                                        steps.append(failure)
                                        raise _PublicationStopped(
                                            failure.error, interrupted=failure.status == "interrupted",
                                        ) from exc
                                    snapshot_started = True
                                if effect.kind == "write":
                                    file_access.write_bytes_atomic(path, effect.content)
                                elif effect.kind == "delete":
                                    file_access.delete_path_and_prune_empty_parents(path, root=path.parent)
                                else:
                                    file_access.chmod(path, effect.mode)
                            except (OSError, ValueError, RuntimeError, KeyboardInterrupt) as exc:
                                failure = _failed_step(step, exc)
                                steps.append(failure)
                                raise _PublicationStopped(
                                    failure.error, interrupted=failure.status == "interrupted",
                                ) from exc
                            steps.append(ExecutionStepResult(step, "ok"))
                        # Complete at this unit's own effect boundary, before hooks
                        # or a later unit can fail and conceal successful ancestry.
                        if complete is not None:
                            complete(current_unit)
                        results[current_unit.row_id] = PublicationUnitResult(current_unit.row_id, "ok")
                        current_unit = None
                        run_hooks(target_hooks, "post_push", package, target)
                    run_hooks(package_hooks, "post_push", package)
                run_hooks(repo_hooks, "post_push")
        except (_PublicationStopped, OSError, ValueError, RuntimeError, KeyboardInterrupt) as exc:
            interrupted = isinstance(exc, (InterruptedError, KeyboardInterrupt)) or (
                isinstance(exc, _PublicationStopped) and exc.interrupted
            )
            error = str(exc) or "Publication interrupted"
            if current_unit is not None:
                results[current_unit.row_id] = PublicationUnitResult(current_unit.row_id, "failed", error)
        if snapshot is not None:
            try:
                snapshot = mark_snapshot_status(snapshot, "failed" if error else "applied")
            except (OSError, ValueError, RuntimeError, KeyboardInterrupt) as exc:
                failure = _failed_step(ExecutionStep(kind="snapshot", action="finalize"), exc)
                steps.append(failure)
                interrupted = interrupted or failure.status == "interrupted"
                error = error or failure.error
    return PublicationResult(tuple(results[unit.row_id] for unit in units), error, tuple(steps), snapshot, interrupted)

