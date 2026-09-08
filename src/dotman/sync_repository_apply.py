"""Apply frozen Primary Source outcomes, completing units before pull post-hooks."""

from __future__ import annotations

import stat
from dataclasses import dataclass, replace
from typing import Callable, Sequence

from dotman import file_access
from dotman.command_runtime import CommandRuntime, command_runtime_session, current_command_runtime
from dotman.elevation import elevation_broker_session
from dotman.execution import ExecutionStep, ExecutionStepResult, _execute_step
from dotman.models import PackagePlan, ResolvedSyncTarget, TargetPlan
from dotman.planning import PackagePlanningInput, plan_hooks, plan_repo_hooks
from dotman.sync_base_store import FilePresent, Missing
from dotman.sync_publication import (
    HookActivation, PublicationMetadata, PublicationResult, PublicationUnitResult,
    _hook_scopes, _package_scope, _target_identity,
    _PublicationStopped, _failed_step,
)


@dataclass(frozen=True)
class RepositoryApplyUnit:
    row_id: str
    identity: ResolvedSyncTarget
    outcome: FilePresent | Missing | None


def prepare_repository_apply(
    inputs: Sequence[PackagePlanningInput],
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
                action="noop", target_kind="probe" if target.probe_command is not None else "file", projection_kind="raw",
                command_cwd=target.command_cwd,
                # One static target may supply metadata to both Sync stages.
                command_env={**target.command_env, "DOTMAN_OPERATION": "pull"},
            )
            for target in item.target_metadata
        ]
        hooks = plan_hooks(
            item.repo, context.resolved_packages, context.context,
            selection=item.selection, operation="pull", inferred_os=context.inferred_os,
            variables=context.variables, target_plans=targets,
            declaration_package_ids={item.selection.identity.package_id},
        )
        # Guards already narrowed capability during Observation and never rerun.
        hooks = {name: values for name, values in hooks.items() if not name.startswith("guard_")}
        packages.append(PackagePlan(
            operation="pull", selection=item.selection, variables=dict(context.variables),
            hooks=hooks, target_plans=targets, repo_root=item.repo.root,
            state_path=item.repo.config.state_path, inferred_os=context.inferred_os,
        ))
        if item.repo.config.name not in repos:
            repos[item.repo.config.name] = tuple(
                hook for hooks in plan_repo_hooks(item.repo, operation="pull").values()
                for hook in hooks
            )
    return PublicationMetadata(tuple(packages), tuple(repos.items()))



def execute_repository_apply(
    metadata: PublicationMetadata,
    units: Sequence[RepositoryApplyUnit],
    *,
    command_runtime: CommandRuntime | None = None,
    complete: Callable[[RepositoryApplyUnit], None],
    stream_output: bool = False,
    assume_yes: bool = False,
    auxiliary: Sequence[HookActivation] = (),
    run_noop: bool = False,
) -> PublicationResult:
    """Complete at the ordered unit boundary; callback failure stops execution.

    The caller decides whether a successfully applied unit can acknowledge now
    or must wait for its frozen live effects. No live endpoint is accessed here.
    """
    units = tuple(units)
    by_identity = {unit.identity: unit for unit in units}
    if len(by_identity) != len(units) or len({unit.row_id for unit in units}) != len(units):
        raise ValueError("Duplicate repository apply unit")
    if any(unit.outcome is not None and not isinstance(unit.outcome, (FilePresent, Missing)) for unit in units):
        raise ValueError("Invalid frozen repository outcome")

    normal_scopes, noop_scopes = _hook_scopes(metadata, auxiliary, [unit.identity for unit in units if unit.outcome is not None])
    selected = []
    matched = set()
    for package in metadata.packages:
        targets = []
        for target in package.target_plans:
            identity = ResolvedSyncTarget(
                repo=package.repo_name, package_id=target.package_id,
                bound_profile=package.bound_profile, target_name=target.target_name,
            )
            if identity in by_identity:
                matched.add(identity)
                targets.append((target, by_identity[identity]))
            elif identity.canonical in normal_scopes | noop_scopes:
                targets.append((target, None))
        if targets or _package_scope(package) in normal_scopes | noop_scopes:
            selected.append((package, targets))
    if matched != set(by_identity):
        raise ValueError("Repository apply unit has no captured metadata")
    if {package.repo_name for package, _ in selected} - dict(metadata.repo_hooks).keys():
        raise ValueError("Repository apply package has no captured repository scope")

    results = {unit.row_id: PublicationUnitResult(unit.row_id, "skipped") for unit in units}
    steps = []
    current_unit = None
    error = None
    interrupted = False

    def run_hooks(hooks, name, package=None, target=None):
        scope = (_target_identity(package, target).canonical if target is not None else
                 _package_scope(package) if package is not None else hooks[0].repo_name if hooks else None)
        for hook in hooks:
            if hook.hook_name != name or not (
                scope in normal_scopes or (scope in noop_scopes and (hook.run_noop or run_noop))
            ):
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
                packages = [(package, targets) for package, targets in selected if package.repo_name == repo_name]
                repo_active = repo_name in normal_scopes | noop_scopes
                if repo_active:
                    run_hooks(repo_hooks, "pre_pull")
                for package, targets in packages:
                    hooks = [hook for values in package.hooks.values() for hook in values]
                    package_hooks = [hook for hook in hooks if hook.scope_kind == "package"]
                    package_active = _package_scope(package) in normal_scopes | noop_scopes
                    if package_active:
                        run_hooks(package_hooks, "pre_pull", package)
                    for target, unit in targets:
                        target_hooks = [hook for hook in hooks if hook.scope_kind == "target" and hook.target_name == target.target_name]
                        run_hooks(target_hooks, "pre_pull", package, target)
                        if unit is None:
                            run_hooks(target_hooks, "post_pull", package, target)
                            continue
                        current_unit = unit
                        step = ExecutionStep(
                            repo_name=repo_name, package_id=package.package_id,
                            package_plan=package, target_plan=target, kind="target",
                            action="delete" if isinstance(unit.outcome, Missing) else "update",
                            scope_kind="target",
                        )
                        try:
                            if unit.outcome is not None:
                                path = target.repo_path
                                # Atomic replacement protects the leaf, not a
                                # retargeted parent link into unrelated storage.
                                path.relative_to(package.repo_root)
                                for parent in path.parents:
                                    if parent.is_symlink():
                                        raise ValueError(f"Repository source parent is a symlink: {parent}")
                                    if parent == package.repo_root:
                                        break
                                try:
                                    shape = path.lstat()
                                except FileNotFoundError:
                                    shape = None
                                if shape is not None and not stat.S_ISREG(shape.st_mode):
                                    raise ValueError(f"Repository apply expects a regular file: {path}")
                                if isinstance(unit.outcome, FilePresent):
                                    file_access.write_bytes_atomic(path, unit.outcome.content)
                                else:
                                    file_access.delete_path_and_prune_empty_parents(path, root=path.parent)
                                steps.append(ExecutionStepResult(step, "ok"))
                            # Acknowledgment is part of unit completion, not hook
                            # success. Earlier completions survive post-hook failure.
                            step = replace(step, kind="unit-completion", action="complete")
                            complete(unit)
                        except (OSError, ValueError, RuntimeError, KeyboardInterrupt) as exc:
                            steps.append(_failed_step(step, exc))
                            raise
                        results[unit.row_id] = PublicationUnitResult(unit.row_id, "ok")
                        current_unit = None
                        run_hooks(target_hooks, "post_pull", package, target)
                    if package_active:
                        run_hooks(package_hooks, "post_pull", package)
                if repo_active:
                    run_hooks(repo_hooks, "post_pull")
        except (_PublicationStopped, OSError, ValueError, RuntimeError, KeyboardInterrupt) as exc:
            interrupted = isinstance(exc, (InterruptedError, KeyboardInterrupt)) or (
                isinstance(exc, _PublicationStopped) and exc.interrupted
            )
            error = str(exc) or "Repository apply interrupted"
            if current_unit is not None:
                results[current_unit.row_id] = PublicationUnitResult(current_unit.row_id, "failed", error)
    return PublicationResult(tuple(results[unit.row_id] for unit in units), error, tuple(steps), interrupted=interrupted)
