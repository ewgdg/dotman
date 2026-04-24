from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import InitVar, dataclass, field
from pathlib import Path
from threading import Thread
from typing import Iterator, Sequence

from dotman.atomic_files import write_bytes_atomic as atomic_write_bytes_atomic
from dotman.atomic_files import write_symlink_atomic as atomic_write_symlink_atomic
from dotman.capture import BUILTIN_PATCH_CAPTURE, capture_patch
from dotman.engine import HOOK_NAMES_BY_OPERATION
from dotman.file_access import (
    chmod as sudo_chmod,
    delete_path_and_prune_empty_parents as sudo_delete_path_and_prune_empty_parents,
    needs_sudo_for_chmod,
    needs_sudo_for_read,
    needs_sudo_for_write,
    read_bytes,
    request_sudo,
    sudo_prefix_command,
    write_bytes_atomic as sudo_write_bytes_atomic,
)
from dotman.models import DirectoryPlanItem, HookPlan, OperationPlan, PackagePlan, TargetPlan, package_plans_for_operation_plan, repo_qualified_target_text
from dotman.repo_access import restore_repo_path_access_for_invoking_user
from dotman.reconcile_helpers import BUILTIN_JINJA_RECONCILE, run_jinja_reconcile
from dotman.templates import build_template_context, render_template_string
from dotman.terminal import preserve_terminal_state


INTERRUPTED_EXIT_CODE = 130
_COMMAND_INTERRUPT_GRACE_SECONDS = 0.5


@dataclass(frozen=True)
class ExecutionStep:
    repo_name: str = ""
    package_id: str | None = None
    package_plan: PackagePlan | None = None
    kind: str = ""
    action: str = ""
    scope_kind: str = "package"
    hook_plan: HookPlan | None = None
    target_plan: TargetPlan | None = None
    directory_item: DirectoryPlanItem | None = None
    privileged: bool = False

    @property
    def command(self) -> str | None:
        if self.hook_plan is not None:
            return self.hook_plan.command
        if self.action == "reconcile" and self.target_plan is not None:
            return self.target_plan.reconcile_command
        return None


@dataclass(frozen=True)
class PackageExecutionUnit:
    repo_name: str
    selection_label: str
    requested_profile: str
    package_id: str
    steps: tuple[ExecutionStep, ...]


@dataclass(frozen=True)
class RepoExecutionUnit:
    repo_name: str
    pre_steps: tuple[ExecutionStep, ...]
    packages: tuple[PackageExecutionUnit, ...]
    post_steps: tuple[ExecutionStep, ...]

    @property
    def steps(self) -> tuple[ExecutionStep, ...]:
        package_steps = tuple(step for package in self.packages for step in package.steps)
        return (*self.pre_steps, *package_steps, *self.post_steps)


@dataclass(frozen=True)
class ExecutionSession:
    operation: str
    repos: tuple[RepoExecutionUnit, ...] = ()
    package_units: InitVar[tuple[PackageExecutionUnit, ...] | None] = None
    requires_privilege: bool = False

    def __post_init__(self, package_units: tuple[PackageExecutionUnit, ...] | None) -> None:
        if package_units is None or self.repos:
            return
        object.__setattr__(self, "repos", _build_repo_units_from_packages(package_units))

    @property
    def packages(self) -> tuple[PackageExecutionUnit, ...]:
        return tuple(package for repo in self.repos for package in repo.packages)


def _build_repo_units_from_packages(packages: tuple[PackageExecutionUnit, ...] | None) -> tuple[RepoExecutionUnit, ...]:
    if packages is None:
        return ()
    repo_units: list[RepoExecutionUnit] = []
    packages_by_repo: dict[str, list[PackageExecutionUnit]] = {}
    for package in packages:
        packages_by_repo.setdefault(package.repo_name, []).append(package)
    for repo_name, repo_packages in packages_by_repo.items():
        repo_units.append(
            RepoExecutionUnit(
                repo_name=repo_name,
                pre_steps=(),
                packages=tuple(repo_packages),
                post_steps=(),
            )
        )
    return tuple(repo_units)


@dataclass(frozen=True)
class ExecutionStepResult:
    step: ExecutionStep
    status: str
    skip_reason: str | None = None
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        step = self.step
        repo_path = _step_repo_path(step)
        live_path = _step_live_path(step)
        return {
            "kind": step.kind,
            "action": step.action,
            "package_id": step.package_id,
            "repo": step.repo_name,
            "selection": None if step.package_plan is None else step.package_plan.selection.to_dict(),
            "status": self.status,
            "skip_reason": self.skip_reason,
            "privileged": step.privileged,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error": self.error,
            "repo_path": str(repo_path) if repo_path is not None else None,
            "live_path": str(live_path) if live_path is not None else None,
            "command": step.command,
        }


@dataclass(frozen=True)
class PackageExecutionResult:
    unit: PackageExecutionUnit
    status: str
    steps: tuple[ExecutionStepResult, ...]
    skip_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "repo": self.unit.repo_name,
            "selection_label": self.unit.selection_label,
            "requested_profile": self.unit.requested_profile,
            "package_id": self.unit.package_id,
            "status": self.status,
            "skip_reason": self.skip_reason,
            "steps": [step.to_dict() for step in self.steps],
        }


@dataclass(frozen=True)
class RepoExecutionResult:
    unit: RepoExecutionUnit
    status: str
    steps: tuple[ExecutionStepResult, ...]
    packages: tuple[PackageExecutionResult, ...]
    skip_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "repo": self.unit.repo_name,
            "status": self.status,
            "skip_reason": self.skip_reason,
            "steps": [step.to_dict() for step in self.steps],
            "packages": [package.to_dict() for package in self.packages],
        }


@dataclass(frozen=True)
class ExecutionResult:
    session: ExecutionSession
    status: str
    repos: tuple[RepoExecutionResult, ...]

    @property
    def packages(self) -> tuple[PackageExecutionResult, ...]:
        return tuple(package for repo in self.repos for package in repo.packages)

    @property
    def exit_code(self) -> int:
        if self.status == "interrupted":
            return INTERRUPTED_EXIT_CODE
        return 0 if self.status == "ok" else 1

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": "execute",
            "operation": self.session.operation,
            "status": self.status,
            "requires_privilege": self.session.requires_privilege,
            "repos": [repo.to_dict() for repo in self.repos],
            "packages": [package.to_dict() for package in self.packages],
        }


_STEP_LABELS_BY_OPERATION = {
    "push": ("guard_push", "pre_push", "post_push"),
    "pull": ("guard_pull", "pre_pull", "post_pull"),
}


def build_execution_session(
    plans: Sequence[PackagePlan] | OperationPlan,
    *,
    operation: str,
    run_noop: bool = False,
) -> ExecutionSession:
    del run_noop
    package_plans = package_plans_for_operation_plan(plans)
    repo_hooks = plans.repo_hooks if isinstance(plans, OperationPlan) else {}
    repo_order = plans.repo_order if isinstance(plans, OperationPlan) and plans.repo_order else tuple(
        dict.fromkeys(plan.repo_name for plan in package_plans)
    )
    _ensure_no_unapproved_live_symlink_targets(package_plans, operation=operation)
    repo_units: list[RepoExecutionUnit] = []
    hook_names = _STEP_LABELS_BY_OPERATION[operation]
    for repo_name in repo_order:
        repo_package_plans = [plan for plan in package_plans if plan.repo_name == repo_name]
        package_units: list[PackageExecutionUnit] = []
        for plan in repo_package_plans:
            package_hooks_by_package: dict[str, dict[str, list[HookPlan]]] = {}
            target_hooks_by_target: dict[tuple[str, str], dict[str, list[HookPlan]]] = {}
            for hook_name in hook_names:
                for hook_plan in plan.hooks.get(hook_name, []):
                    if hook_plan.scope_kind == "target" and hook_plan.package_id is not None and hook_plan.target_name is not None:
                        target_hooks_by_target.setdefault((hook_plan.package_id, hook_plan.target_name), {}).setdefault(hook_name, []).append(hook_plan)
                        continue
                    if hook_plan.package_id is not None:
                        package_hooks_by_package.setdefault(hook_plan.package_id, {}).setdefault(hook_name, []).append(hook_plan)

            targets_by_package: dict[str, list[TargetPlan]] = {}
            for target in plan.target_plans:
                targets_by_package.setdefault(target.package_id, []).append(target)

            package_id = plan.package_id
            package_targets = targets_by_package.get(package_id, [])
            package_hooks = package_hooks_by_package.get(package_id, {})
            target_steps_by_owner = {
                (target.package_id, target.target_name): (
                    [] if target.action == "noop" else _build_target_steps(plan=plan, target_plan=target, operation=operation)
                )
                for target in package_targets
            }
            package_steps: list[ExecutionStep] = []
            for hook_name in hook_names[:2]:
                package_steps.extend(
                    ExecutionStep(
                        repo_name=plan.repo_name,
                        package_id=package_id,
                        package_plan=plan,
                        kind="hook",
                        action=hook_name,
                        scope_kind="package",
                        hook_plan=hook_plan,
                        # Hooks are arbitrary user shell. Never auto-promote them to
                        # root just because adjacent file operations need sudo.
                        privileged=False,
                    )
                    for hook_plan in package_hooks.get(hook_name, [])
                )

            for target in package_targets:
                target_id = (target.package_id, target.target_name)
                target_steps = target_steps_by_owner[target_id]
                target_hooks = target_hooks_by_target.get(target_id, {})
                for hook_name in hook_names[:2]:
                    package_steps.extend(
                        ExecutionStep(
                            repo_name=plan.repo_name,
                            package_id=package_id,
                            package_plan=plan,
                            kind="hook",
                            action=hook_name,
                            scope_kind="target",
                            hook_plan=hook_plan,
                            target_plan=target,
                            privileged=False,
                        )
                        for hook_plan in target_hooks.get(hook_name, [])
                    )
                package_steps.extend(target_steps)
                if target_steps or target_hooks:
                    package_steps.extend(
                        ExecutionStep(
                            repo_name=plan.repo_name,
                            package_id=package_id,
                            package_plan=plan,
                            kind="hook",
                            action=hook_names[2],
                            scope_kind="target",
                            hook_plan=hook_plan,
                            target_plan=target,
                            privileged=False,
                        )
                        for hook_plan in target_hooks.get(hook_names[2], [])
                    )

            if package_steps or package_hooks:
                package_steps.extend(
                    ExecutionStep(
                        repo_name=plan.repo_name,
                        package_id=package_id,
                        package_plan=plan,
                        kind="hook",
                        action=hook_names[2],
                        scope_kind="package",
                        hook_plan=hook_plan,
                        privileged=False,
                    )
                    for hook_plan in package_hooks.get(hook_names[2], [])
                )

            if not package_steps:
                continue
            package_units.append(
                PackageExecutionUnit(
                    repo_name=plan.repo_name,
                    selection_label=plan.selection_label,
                    requested_profile=plan.requested_profile,
                    package_id=package_id,
                    steps=tuple(package_steps),
                )
            )

        repo_pre_steps = tuple(
            ExecutionStep(
                repo_name=repo_name,
                package_id=None,
                package_plan=None,
                kind="hook",
                action=hook_name,
                scope_kind="repo",
                hook_plan=hook_plan,
                privileged=False,
            )
            for hook_name in hook_names[:2]
            for hook_plan in repo_hooks.get(repo_name, {}).get(hook_name, [])
        )
        repo_post_steps = tuple(
            ExecutionStep(
                repo_name=repo_name,
                package_id=None,
                package_plan=None,
                kind="hook",
                action=hook_names[2],
                scope_kind="repo",
                hook_plan=hook_plan,
                privileged=False,
            )
            for hook_plan in repo_hooks.get(repo_name, {}).get(hook_names[2], [])
        )
        if not package_units and not repo_pre_steps and not repo_post_steps:
            continue
        repo_units.append(
            RepoExecutionUnit(
                repo_name=repo_name,
                pre_steps=repo_pre_steps,
                packages=tuple(package_units),
                post_steps=repo_post_steps,
            )
        )

    return ExecutionSession(
        operation=operation,
        repos=tuple(repo_units),
        requires_privilege=any(step.privileged for repo in repo_units for step in repo.steps),
    )



def _ensure_no_unapproved_live_symlink_targets(plans: Sequence[PackagePlan], *, operation: str) -> None:
    if operation != "push":
        return

    hazards: list[str] = []
    for plan in plans:
        selection_label = plan.selection_label
        for target in plan.target_plans:
            if target.action == "noop" or not target.live_path_is_symlink:
                continue
            if target.target_kind == "directory":
                if target.dir_symlink_mode == "follow":
                    continue
                symlink_target = target.live_path_symlink_target or "<unknown>"
                hazards.append(
                    f"{selection_label} {repo_qualified_target_text(repo_name=plan.repo_name, package_id=target.package_id, target_name=target.target_name)} ({target.live_path} -> {symlink_target})"
                )
                continue
            if target.file_symlink_mode == "follow" or target.allow_live_path_symlink_replace:
                continue
            symlink_target = target.live_path_symlink_target or "<unknown>"
            hazards.append(
                f"{selection_label} {repo_qualified_target_text(repo_name=plan.repo_name, package_id=target.package_id, target_name=target.target_name)} ({target.live_path} -> {symlink_target})"
            )

    if hazards:
        raise ValueError("refusing to execute through unresolved symlinked live target(s): " + ", ".join(hazards))


def _push_live_path(target_plan: TargetPlan) -> Path:
    live_path = target_plan.live_path
    live_path_is_symlink = live_path.is_symlink()
    if not live_path_is_symlink:
        return live_path
    if target_plan.target_kind == "directory":
        if target_plan.dir_symlink_mode == "follow":
            return live_path
        raise ValueError(
            f"live target path is a symlink for target '{target_plan.package_id}:{target_plan.target_name}': "
            f"{live_path} -> {live_path.resolve(strict=False)}"
        )
    if target_plan.file_symlink_mode == "follow":
        return live_path.resolve(strict=False)
    if target_plan.allow_live_path_symlink_replace:
        return live_path
    raise ValueError(
        f"live target path is a symlink for target '{target_plan.package_id}:{target_plan.target_name}': "
        f"{live_path} -> {live_path.resolve(strict=False)}"
    )


def execute_session(
    session: ExecutionSession,
    *,
    stream_output: bool,
    assume_yes: bool = False,
    on_package_start=None,
    on_step_start=None,
    on_step_finish=None,
    on_package_finish=None,
) -> ExecutionResult:
    _preflight_execution_session_sudo(session)
    repo_results: list[RepoExecutionResult] = []
    failed = False
    interrupted = False
    for repo in session.repos:
        repo_step_results: list[ExecutionStepResult] = []
        repo_package_results: list[PackageExecutionResult] = []
        repo_status = "ok"
        repo_skip_reason: str | None = None

        if failed or interrupted:
            repo_status = "skipped"
            repo_skip_reason = "interrupted" if interrupted else "failure"
            repo_step_results.extend(_build_skipped_step_result(step, skip_reason=repo_skip_reason) for step in (*repo.pre_steps, *repo.post_steps))
            for package in repo.packages:
                if on_package_start is not None:
                    on_package_start(package)
                skipped_result = _build_skipped_package_result(package, skip_reason=repo_skip_reason)
                repo_package_results.append(skipped_result)
                if on_package_finish is not None:
                    on_package_finish(skipped_result)
            repo_results.append(
                RepoExecutionResult(
                    unit=repo,
                    status=repo_status,
                    skip_reason=repo_skip_reason,
                    steps=tuple(repo_step_results),
                    packages=tuple(repo_package_results),
                )
            )
            continue

        for step_index, step in enumerate(repo.pre_steps, start=1):
            if on_step_start is not None:
                on_step_start(repo, step, step_index, len(repo.pre_steps))
            result = _execute_step(step, stream_output=stream_output, assume_yes=assume_yes)
            repo_step_results.append(result)
            if on_step_finish is not None:
                on_step_finish(repo, result, step_index, len(repo.pre_steps))
            if result.status == "skipped":
                repo_status = "skipped"
                repo_skip_reason = result.skip_reason or "guard"
                break
            if result.status == "interrupted":
                repo_status = "interrupted"
                repo_skip_reason = "interrupted"
                interrupted = True
                break
            if result.status != "ok":
                repo_status = "failed"
                repo_skip_reason = "failure"
                failed = True
                break

        for package in repo.packages:
            if repo_skip_reason is not None:
                if on_package_start is not None:
                    on_package_start(package)
                skipped_result = _build_skipped_package_result(package, skip_reason=repo_skip_reason)
                repo_package_results.append(skipped_result)
                if on_package_finish is not None:
                    on_package_finish(skipped_result)
                continue
            package_result = _execute_package_unit(
                package,
                stream_output=stream_output,
                assume_yes=assume_yes,
                on_package_start=on_package_start,
                on_step_start=on_step_start,
                on_step_finish=on_step_finish,
                on_package_finish=on_package_finish,
            )
            repo_package_results.append(package_result)
            if package_result.status == "interrupted":
                repo_status = "interrupted"
                repo_skip_reason = "interrupted"
                interrupted = True
            elif package_result.status == "failed":
                repo_status = "failed"
                repo_skip_reason = "failure"
                failed = True
            elif package_result.status == "skipped" and package_result.skip_reason == "guard":
                # package guard skip is local to package; repo remains ok.
                pass

        if repo_skip_reason is None and repo_status == "ok":
            for step_index, step in enumerate(repo.post_steps, start=1):
                if on_step_start is not None:
                    on_step_start(repo, step, step_index, len(repo.post_steps))
                result = _execute_step(step, stream_output=stream_output, assume_yes=assume_yes)
                repo_step_results.append(result)
                if on_step_finish is not None:
                    on_step_finish(repo, result, step_index, len(repo.post_steps))
                if result.status != "ok":
                    if result.status == "interrupted":
                        repo_status = "interrupted"
                        repo_skip_reason = "interrupted"
                        interrupted = True
                    else:
                        repo_status = "failed"
                        repo_skip_reason = "failure"
                        failed = True
                    break
        else:
            repo_step_results.extend(_build_skipped_step_result(step, skip_reason=repo_skip_reason or "failure") for step in repo.post_steps)

        repo_results.append(
            RepoExecutionResult(
                unit=repo,
                status=repo_status,
                skip_reason=repo_skip_reason if repo_status == "skipped" else None,
                steps=tuple(repo_step_results),
                packages=tuple(repo_package_results),
            )
        )
    return ExecutionResult(
        session=session,
        status="interrupted" if interrupted else "failed" if failed else "ok",
        repos=tuple(repo_results),
    )


def _execute_package_unit(
    package: PackageExecutionUnit,
    *,
    stream_output: bool,
    assume_yes: bool,
    on_package_start=None,
    on_step_start=None,
    on_step_finish=None,
    on_package_finish=None,
) -> PackageExecutionResult:
    if on_package_start is not None:
        on_package_start(package)

    step_results: list[ExecutionStepResult] = []
    package_status = "ok"
    package_skip_reason: str | None = None
    skipped_target_id: tuple[str, str] | None = None
    total_steps = len(package.steps)
    for step_index, step in enumerate(package.steps, start=1):
        current_target_id = _step_target_id(step)
        if skipped_target_id is not None and current_target_id != skipped_target_id:
            skipped_target_id = None
        if package_skip_reason is not None:
            step_results.append(_build_skipped_step_result(step, skip_reason=package_skip_reason))
            continue
        if skipped_target_id is not None and current_target_id == skipped_target_id:
            step_results.append(_build_skipped_step_result(step, skip_reason="guard"))
            continue
        if on_step_start is not None:
            on_step_start(package, step, step_index, total_steps)
        result = _execute_step(step, stream_output=stream_output, assume_yes=assume_yes)
        step_results.append(result)
        if on_step_finish is not None:
            on_step_finish(package, result, step_index, total_steps)
        if result.status == "skipped" and result.skip_reason == "guard":
            if step.scope_kind == "target" and current_target_id is not None:
                skipped_target_id = current_target_id
                continue
            package_skip_reason = "guard"
            package_status = "skipped"
            continue
        if result.status == "skipped":
            package_skip_reason = result.skip_reason or "skipped"
            package_status = "skipped"
            continue
        if result.status == "interrupted":
            package_skip_reason = "interrupted"
            package_status = "interrupted"
            continue
        if result.status != "ok":
            package_skip_reason = "failure"
            package_status = "failed"
            continue
    package_result = PackageExecutionResult(
        unit=package,
        status=package_status,
        skip_reason=package_skip_reason if package_status == "skipped" else None,
        steps=tuple(step_results),
    )
    if on_package_finish is not None:
        on_package_finish(package_result)
    return package_result


def _build_skipped_step_result(step: ExecutionStep, *, skip_reason: str) -> ExecutionStepResult:
    return ExecutionStepResult(step=step, status="skipped", skip_reason=skip_reason)


def _build_skipped_package_result(
    package: PackageExecutionUnit,
    *,
    skip_reason: str,
) -> PackageExecutionResult:
    return PackageExecutionResult(
        unit=package,
        status="skipped",
        skip_reason=skip_reason,
        steps=tuple(_build_skipped_step_result(step, skip_reason=skip_reason) for step in package.steps),
    )


def _reconcile_step_needs_sudo(target_plan: TargetPlan) -> bool:
    # Custom reconcile commands are arbitrary user shell. Dotman should not
    # silently run them as root; users can opt in by writing sudo into the
    # reconcile command itself.
    return target_plan.reconcile_command == BUILTIN_JINJA_RECONCILE and needs_sudo_for_read(target_plan.live_path)



def _target_step_needs_sudo(
    *,
    operation: str,
    target_plan: TargetPlan,
    action: str,
    directory_item: DirectoryPlanItem | None = None,
) -> bool:
    if operation == "push":
        live_path = directory_item.live_path if directory_item is not None else target_plan.live_path
        return action in {"create", "update", "delete"} and needs_sudo_for_write(live_path)

    if action in {"create_repo", "update_repo"}:
        source_path = directory_item.live_path if directory_item is not None else target_plan.live_path
        return needs_sudo_for_read(source_path)
    if action == "delete_repo":
        repo_path = directory_item.repo_path if directory_item is not None else target_plan.repo_path
        return needs_sudo_for_write(repo_path)
    return False


def _preflight_execution_session_sudo(session: ExecutionSession) -> None:
    if session.requires_privilege:
        request_sudo(_execution_session_sudo_reason(session))



def _execution_session_sudo_reason(session: ExecutionSession) -> str:
    for repo in session.repos:
        for step in repo.steps:
            if step.privileged and step.kind != "hook":
                return _sudo_reason_for_step(step)
    for repo in session.repos:
        for step in repo.steps:
            if step.privileged:
                return _sudo_reason_for_step(step)
    return "planned execution includes privileged operations"



def _sudo_reason_for_step(step: ExecutionStep) -> str:
    live_path = _step_live_path(step)
    repo_path = _step_repo_path(step)
    if step.action in {"create", "update", "delete"} and live_path is not None:
        return f"write protected path: {live_path}"
    if step.action == "chmod" and live_path is not None:
        return f"change mode on protected path: {live_path}"
    if step.action in {"create_repo", "update_repo", "reconcile"} and live_path is not None:
        return f"read protected path: {live_path}"
    if step.action == "delete_repo" and repo_path is not None:
        return f"delete protected path: {repo_path}"
    if step.kind == "hook":
        if step.package_id is None:
            return f"execute privileged repo hook for {step.repo_name}"
        return f"execute privileged hook for {step.package_id}"
    if live_path is not None:
        return f"access protected path: {live_path}"
    if repo_path is not None:
        return f"access protected path: {repo_path}"
    return "planned execution includes privileged operations"



def _build_target_steps(*, plan: PackagePlan, target_plan: TargetPlan, operation: str) -> list[ExecutionStep]:
    steps: list[ExecutionStep] = []
    if operation == "push":
        if target_plan.target_kind == "directory":
            steps.extend(
                ExecutionStep(
                    repo_name=plan.repo_name,
                    package_id=target_plan.package_id,
                    package_plan=plan,
                    kind="target",
                    action=item.action,
                    scope_kind="target",
                    target_plan=target_plan,
                    directory_item=item,
                    privileged=_target_step_needs_sudo(operation=operation, target_plan=target_plan, action=item.action, directory_item=item),
                )
                for item in target_plan.directory_items
            )
            if target_plan.directory_items and target_plan.chmod is not None:
                steps.append(
                    ExecutionStep(
                        repo_name=plan.repo_name,
                        package_id=target_plan.package_id,
                        package_plan=plan,
                        kind="chmod",
                        action="chmod",
                        scope_kind="target",
                        target_plan=target_plan,
                        privileged=needs_sudo_for_chmod(target_plan.live_path),
                    )
                )
            return steps
        steps.append(
            ExecutionStep(
                repo_name=plan.repo_name,
                package_id=target_plan.package_id,
                package_plan=plan,
                kind="target",
                action=target_plan.action,
                scope_kind="target",
                target_plan=target_plan,
                privileged=_target_step_needs_sudo(operation=operation, target_plan=target_plan, action=target_plan.action),
            )
        )
        if target_plan.action in {"create", "update"} and target_plan.chmod is not None:
            steps.append(
                ExecutionStep(
                    repo_name=plan.repo_name,
                    package_id=target_plan.package_id,
                    package_plan=plan,
                    kind="chmod",
                    action="chmod",
                    scope_kind="target",
                    target_plan=target_plan,
                    privileged=needs_sudo_for_chmod(target_plan.live_path),
                )
            )
        return steps

    if target_plan.reconcile_command is not None and target_plan.capture_command is None and target_plan.action == "update":
        steps.append(
            ExecutionStep(
                repo_name=plan.repo_name,
                package_id=target_plan.package_id,
                package_plan=plan,
                kind="reconcile",
                action="reconcile",
                scope_kind="target",
                target_plan=target_plan,
                privileged=_reconcile_step_needs_sudo(target_plan),
            )
        )
        return steps

    if target_plan.target_kind == "directory":
        action_map = {"create": "create_repo", "update": "update_repo", "delete": "delete_repo"}
        steps.extend(
            ExecutionStep(
                repo_name=plan.repo_name,
                package_id=target_plan.package_id,
                package_plan=plan,
                kind="target",
                action=action_map[item.action],
                scope_kind="target",
                target_plan=target_plan,
                directory_item=item,
                privileged=_target_step_needs_sudo(operation=operation, target_plan=target_plan, action=action_map[item.action], directory_item=item),
            )
            for item in target_plan.directory_items
        )
        return steps

    direct_action = {
        "create": "create_repo",
        "update": "update_repo",
        "delete": "delete_repo",
    }[target_plan.action]
    steps.append(
        ExecutionStep(
            repo_name=plan.repo_name,
            package_id=target_plan.package_id,
            package_plan=plan,
            kind="target",
            action=direct_action,
            scope_kind="target",
            target_plan=target_plan,
            privileged=_target_step_needs_sudo(operation=operation, target_plan=target_plan, action=direct_action),
        )
    )
    return steps


def _execute_step(step: ExecutionStep, *, stream_output: bool, assume_yes: bool) -> ExecutionStepResult:
    try:
        if step.kind == "hook":
            if step.hook_plan.io == "tty":
                _require_interactive_terminal_for_hook()
            exit_code, stdout, stderr = _run_command(
                command=step.hook_plan.command,
                cwd=step.hook_plan.cwd,
                env=_build_hook_env(step, assume_yes=assume_yes),
                stream_output=stream_output,
                interactive=step.hook_plan.io == "tty",
                privileged=step.privileged,
            )
            if exit_code == 0:
                return ExecutionStepResult(step=step, status="ok", exit_code=exit_code, stdout=stdout, stderr=stderr)
            if _is_interrupt_exit_code(exit_code):
                return ExecutionStepResult(
                    step=step,
                    status="interrupted",
                    exit_code=INTERRUPTED_EXIT_CODE,
                    stdout=stdout,
                    stderr=stderr,
                )
            if exit_code == 100 and _is_guard_step(step):
                return ExecutionStepResult(
                    step=step,
                    status="skipped",
                    skip_reason="guard",
                    exit_code=exit_code,
                    stdout=stdout,
                    stderr=stderr,
                )
            return ExecutionStepResult(
                step=step,
                status="failed",
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                error=f"command exited with status {exit_code}",
            )

        target_plan = _require_target_plan(step)
        if step.package_plan.operation == "push" and target_plan.target_kind == "directory":
            if target_plan.live_path.is_symlink() and target_plan.dir_symlink_mode != "follow":
                raise ValueError(
                    f"live target path is a symlink for target '{target_plan.package_id}:{target_plan.target_name}': "
                    f"{target_plan.live_path} -> {target_plan.live_path.resolve(strict=False)}"
                )

        if step.kind == "reconcile":
            exit_code, stdout, stderr = _run_reconcile_target_plan(
                target_plan=target_plan,
                stream_output=stream_output,
                assume_yes=assume_yes,
                privileged=step.privileged,
            )
            return ExecutionStepResult(
                step=step,
                status=_command_step_status(exit_code),
                exit_code=INTERRUPTED_EXIT_CODE if _is_interrupt_exit_code(exit_code) else exit_code,
                stdout=stdout,
                stderr=stderr,
                error=None if exit_code == 0 or _is_interrupt_exit_code(exit_code) else f"command exited with status {exit_code}",
            )
        if _should_fallback_to_reconcile_after_capture(step):
            return _execute_target_step_with_capture_fallback(
                step,
                stream_output=stream_output,
                assume_yes=assume_yes,
            )
        if step.kind == "chmod":
            _execute_chmod_step(step)
            return ExecutionStepResult(step=step, status="ok")
        _execute_target_step(step)
        return ExecutionStepResult(step=step, status="ok")
    except Exception as exc:  # noqa: BLE001 - fail-fast execution should surface the original error text.
        return ExecutionStepResult(step=step, status="failed", error=str(exc))


def _is_guard_step(step: ExecutionStep) -> bool:
    return step.kind == "hook" and step.action.startswith("guard_")


def _command_step_status(exit_code: int) -> str:
    if exit_code == 0:
        return "ok"
    if _is_interrupt_exit_code(exit_code):
        return "interrupted"
    return "failed"


def _should_fallback_to_reconcile_after_capture(step: ExecutionStep) -> bool:
    target_plan = step.target_plan
    return (
        step.kind == "target"
        and step.action == "update_repo"
        and step.directory_item is None
        and target_plan is not None
        and target_plan.capture_command is not None
        and target_plan.reconcile_command is not None
    )


def _execute_target_step_with_capture_fallback(
    step: ExecutionStep,
    *,
    stream_output: bool,
    assume_yes: bool,
) -> ExecutionStepResult:
    target_plan = _require_target_plan(step)
    try:
        repo_bytes = _pull_repo_bytes(step)
    except Exception as capture_exc:  # noqa: BLE001 - fallback should trigger on any capture failure.
        try:
            exit_code, stdout, stderr = _run_reconcile_target_plan(
                target_plan=target_plan,
                stream_output=stream_output,
                assume_yes=assume_yes,
                privileged=_reconcile_step_needs_sudo(target_plan),
            )
        except Exception as reconcile_exc:  # noqa: BLE001 - surface both failures together.
            raise ValueError(f"capture failed ({capture_exc}); reconcile failed ({reconcile_exc})") from reconcile_exc
        fallback_note = f"capture failed; falling back to reconcile: {capture_exc}"
        combined_stderr = fallback_note if not stderr else f"{fallback_note}\n{stderr}"
        return ExecutionStepResult(
            step=step,
            status=_command_step_status(exit_code),
            exit_code=INTERRUPTED_EXIT_CODE if _is_interrupt_exit_code(exit_code) else exit_code,
            stdout=stdout,
            stderr=combined_stderr,
            error=None if exit_code == 0 or _is_interrupt_exit_code(exit_code) else f"command exited with status {exit_code}",
        )
    _write_pull_repo_bytes(step, repo_bytes)
    return ExecutionStepResult(step=step, status="ok")


def _execute_target_step(step: ExecutionStep) -> None:
    target_plan = _require_target_plan(step)
    if step.action in {"create", "update"}:
        if step.directory_item is not None:
            source_bytes = read_bytes(step.directory_item.repo_path)
            live_path = step.directory_item.live_path
        else:
            source_bytes = _push_desired_bytes(target_plan)
            live_path = _push_live_path(target_plan)
        if needs_sudo_for_write(live_path):
            sudo_write_bytes_atomic(live_path, source_bytes)
        else:
            _write_bytes(live_path, source_bytes)
        return
    if step.action == "delete":
        delete_path = step.directory_item.live_path if step.directory_item is not None else _push_live_path(target_plan)
        delete_root = target_plan.live_path if step.directory_item is not None else delete_path
        if needs_sudo_for_write(delete_path):
            sudo_delete_path_and_prune_empty_parents(delete_path, root=delete_root)
        else:
            _delete_file(delete_path, root=delete_root)
        return
    if step.action in {"create_repo", "update_repo"}:
        repo_bytes = _pull_repo_bytes(step)
        _write_pull_repo_bytes(step, repo_bytes)
        return
    if step.action == "delete_repo":
        delete_path = step.directory_item.repo_path if step.directory_item is not None else target_plan.repo_path
        if needs_sudo_for_write(delete_path):
            sudo_delete_path_and_prune_empty_parents(delete_path, root=target_plan.repo_path)
        else:
            _delete_file(delete_path, root=target_plan.repo_path)
        return
    raise ValueError(f"unsupported execution action '{step.action}'")


def _pull_repo_bytes(step: ExecutionStep) -> bytes:
    target_plan = _require_target_plan(step)
    if step.directory_item is not None:
        return read_bytes(step.directory_item.live_path)
    if target_plan.capture_command == BUILTIN_PATCH_CAPTURE:
        return _pull_patch_capture_bytes(target_plan=target_plan, package_plan=step.package_plan)
    return _pull_desired_bytes(target_plan)


def _write_pull_repo_bytes(step: ExecutionStep, repo_bytes: bytes) -> None:
    target_plan = _require_target_plan(step)
    repo_path = step.directory_item.repo_path if step.directory_item is not None else target_plan.repo_path
    if needs_sudo_for_write(repo_path):
        sudo_write_bytes_atomic(repo_path, repo_bytes, restore_root=step.package_plan.repo_root)
        return
    _write_bytes(repo_path, repo_bytes)
    _restore_repo_path_access_for_invoking_user(repo_path, repo_root=step.package_plan.repo_root)


def _execute_chmod_step(step: ExecutionStep) -> None:
    target_plan = _require_target_plan(step)
    if target_plan.chmod is None:
        return
    chmod_mode = int(target_plan.chmod, 8)
    if step.package_plan.operation == "push":
        chmod_path = _push_live_path(target_plan)
    else:
        chmod_path = target_plan.repo_path
    if chmod_path.exists():
        if needs_sudo_for_chmod(chmod_path):
            sudo_chmod(chmod_path, chmod_mode)
        else:
            os.chmod(chmod_path, chmod_mode)


def _push_desired_bytes(target_plan: TargetPlan) -> bytes:
    if target_plan.desired_bytes is not None:
        return target_plan.desired_bytes
    if target_plan.render_command is None:
        raise ValueError(
            f"missing desired bytes for {target_plan.package_id}:{target_plan.target_name}"
        )
    exit_code, stdout, stderr = _run_command(
        command=target_plan.render_command,
        cwd=target_plan.command_cwd,
        env=_build_target_env(target_plan),
        stream_output=False,
        interactive=False,
    )
    if exit_code != 0:
        _raise_for_interrupt_exit_code(exit_code)
        raise ValueError(stderr.strip() or f"render command exited with status {exit_code}")
    return stdout.encode("utf-8")


def _run_reconcile_target_plan(
    *,
    target_plan: TargetPlan,
    stream_output: bool,
    assume_yes: bool,
    privileged: bool,
) -> tuple[int, str, str]:
    with _materialize_reconcile_review_env(target_plan) as review_env:
        command_env = {**_build_target_env(target_plan), **review_env}
        if target_plan.reconcile_io == "tty":
            _require_interactive_terminal_for_reconcile()
        if target_plan.reconcile_command == BUILTIN_JINJA_RECONCILE:
            # Keep built-in reconcile values declarative in plans/info
            # while still reusing the same helper as the CLI subcommand.
            exit_code = run_jinja_reconcile(
                repo_path=str(target_plan.repo_path),
                live_path=str(target_plan.live_path),
                review_repo_path=command_env.get("DOTMAN_REVIEW_REPO_PATH"),
                review_live_path=command_env.get("DOTMAN_REVIEW_LIVE_PATH"),
                assume_yes=assume_yes,
            )
            return exit_code, "", ""
        if target_plan.reconcile_io == "tty":
            return _run_command_with_terminal(
                command=target_plan.reconcile_command or "",
                cwd=target_plan.command_cwd,
                env=command_env,
                privileged=privileged,
            )
        return _run_command(
            command=target_plan.reconcile_command or "",
            cwd=target_plan.command_cwd,
            env=command_env,
            stream_output=stream_output,
            interactive=False,
            privileged=privileged,
        )


def _pull_desired_bytes(target_plan: TargetPlan) -> bytes:
    if target_plan.capture_command is None:
        return read_bytes(target_plan.live_path)
    exit_code, stdout, stderr = _run_command(
        command=target_plan.capture_command,
        cwd=target_plan.command_cwd,
        env=_build_target_env(target_plan),
        stream_output=False,
        interactive=False,
        privileged=needs_sudo_for_read(target_plan.live_path),
    )
    if exit_code != 0:
        _raise_for_interrupt_exit_code(exit_code)
        raise ValueError(stderr.strip() or f"capture command exited with status {exit_code}")
    return stdout.encode("utf-8")


@contextmanager
def _materialize_patch_capture_review_env(target_plan: TargetPlan) -> Iterator[None]:
    # Reverse capture needs the same review-side projection bytes the reviewer saw,
    # not the raw repo and live files.
    with _materialize_reconcile_review_env(target_plan) as review_env:
        previous_env = {key: os.environ.get(key) for key in review_env}
        os.environ.update(review_env)
        try:
            yield
        finally:
            for key, previous_value in previous_env.items():
                if previous_value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = previous_value


def _pull_patch_capture_bytes(*, target_plan: TargetPlan, package_plan: PackagePlan) -> bytes:
    projector = _build_patch_capture_projector(target_plan=target_plan, package_plan=package_plan)
    with _materialize_patch_capture_review_env(target_plan):
        return capture_patch(repo_path=str(target_plan.repo_path), project_repo_bytes=projector)


def _build_patch_capture_projector(*, target_plan: TargetPlan, package_plan: PackagePlan):
    if target_plan.render_command is None:
        raise ValueError(f'capture = "patch" requires render for {target_plan.package_id}:{target_plan.target_name}')

    if target_plan.render_command == "jinja":
        context = build_template_context(
            package_plan.variables,
            profile=package_plan.requested_profile,
            inferred_os=package_plan.inferred_os or sys.platform,
        )
        base_dir = target_plan.repo_path.parent

        def project(candidate_bytes: bytes) -> bytes:
            candidate_text = candidate_bytes.decode("utf-8")
            return render_template_string(candidate_text, context, base_dir=base_dir, source_path=target_plan.repo_path).encode("utf-8")

        return project

    def project(candidate_bytes: bytes) -> bytes:
        command_env = {
            **_build_target_env(target_plan),
        }
        # Keep temp source beside real source so command renderers that resolve
        # sibling files relative to $DOTMAN_SOURCE still see same local layout.
        with tempfile.NamedTemporaryFile(
            prefix=f".dotman-patch-{target_plan.repo_path.stem}-",
            suffix=target_plan.repo_path.suffix,
            dir=target_plan.repo_path.parent,
            delete=False,
        ) as temp_source:
            temp_source.write(candidate_bytes)
            temp_source_path = Path(temp_source.name)
        try:
            temp_source_text = str(temp_source_path)
            command_env.update(
                {
                    "DOTMAN_TARGET_REPO_PATH": temp_source_text,
                    "DOTMAN_REPO_PATH": temp_source_text,
                    "DOTMAN_SOURCE": temp_source_text,
                }
            )
            exit_code, stdout, stderr = _run_command(
                command=target_plan.render_command or "",
                cwd=target_plan.command_cwd,
                env=command_env,
                stream_output=False,
                interactive=False,
                privileged=needs_sudo_for_read(target_plan.live_path),
            )
            if exit_code != 0:
                _raise_for_interrupt_exit_code(exit_code)
                raise ValueError(stderr.strip() or f"render command exited with status {exit_code}")
            return stdout.encode("utf-8")
        finally:
            temp_source_path.unlink(missing_ok=True)

    return project


def write_bytes_atomic(path: Path, content: bytes) -> None:
    atomic_write_bytes_atomic(path, content)


def write_symlink_atomic(path: Path, target: str | Path) -> None:
    atomic_write_symlink_atomic(path, target)


def delete_path_and_prune_empty_parents(path: Path, *, root: Path) -> None:
    if path.exists() or path.is_symlink():
        path.unlink()
    prune_root = root if root.is_dir() else root.parent
    current = path.parent
    while current.exists() and current != prune_root and current != current.parent:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _write_bytes(path: Path, content: bytes) -> None:
    write_bytes_atomic(path, content)


def _delete_file(path: Path, *, root: Path) -> None:
    delete_path_and_prune_empty_parents(path, root=root)


def _restore_repo_path_access_for_invoking_user(path: Path, *, repo_root: Path | None) -> None:
    restore_repo_path_access_for_invoking_user(path, repo_root=repo_root)


def _run_command(
    *,
    command: str,
    cwd: Path | None,
    env: dict[str, str],
    stream_output: bool,
    interactive: bool,
    privileged: bool = False,
) -> tuple[int, str, str]:
    if privileged and os.geteuid() != 0:
        request_sudo("run privileged command")
        command = sudo_prefix_command(command)
    if interactive:
        return _run_command_with_terminal(command=command, cwd=cwd, env=env, privileged=False)

    stdout_buffer: list[str] = []
    stderr_buffer: list[str] = []

    def pump(stream, buffer: list[str], sink) -> None:
        try:
            for chunk in iter(stream.readline, ""):
                buffer.append(chunk)
                if stream_output:
                    for line in chunk.splitlines(keepends=True):
                        sink.write(f"      {line}")
                        sink.flush()
        finally:
            stream.close()

    with preserve_terminal_state():
        process = subprocess.Popen(
            command,
            cwd=str(cwd) if cwd is not None else None,
            env={**os.environ, **env},
            shell=True,
            executable="/bin/sh",
            # Pipe mode owns stdout/stderr but must not read dotman's stdin;
            # accidental reads can steal selection input or hang hidden prompts.
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            # Parent handles Ctrl-C cleanup for pipe commands, so isolate the
            # shell tree and signal the whole group on interruption.
            start_new_session=True,
        )
        stdout_thread = Thread(target=pump, args=(process.stdout, stdout_buffer, sys.stdout), daemon=True)
        stderr_thread = Thread(target=pump, args=(process.stderr, stderr_buffer, sys.stderr), daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        try:
            return_code = process.wait()
        except KeyboardInterrupt:
            _interrupt_process_group(process)
            _wait_for_process_exit(process)
            raise
        finally:
            stdout_thread.join()
            stderr_thread.join()
    return _normalize_command_return_code(return_code), "".join(stdout_buffer), "".join(stderr_buffer)


def _run_command_with_terminal(*, command: str, cwd: Path | None, env: dict[str, str], privileged: bool = False) -> tuple[int, str, str]:
    # TTY-backed commands may launch full-screen editors or other terminal-native
    # tools. Piping and prefixing their output corrupts control sequences and can
    # leave the shell looking broken after exit, so dotman must hand them tty.
    if privileged and os.geteuid() != 0:
        request_sudo("run privileged command")
        command = sudo_prefix_command(command)
    with preserve_terminal_state():
        process = subprocess.Popen(
            command,
            cwd=str(cwd) if cwd is not None else None,
            env={**os.environ, **env},
            shell=True,
            executable="/bin/sh",
        )
        previous_sigint_handler = signal.getsignal(signal.SIGINT)
        # TTY commands own the foreground terminal. Ignore Ctrl-C in dotman
        # while waiting so the child handles interruption and avoids duplicate
        # parent notices.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            return_code = process.wait()
        finally:
            signal.signal(signal.SIGINT, previous_sigint_handler)
    return _normalize_command_return_code(return_code), "", ""


def _interrupt_process_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGINT)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            process.terminate()
        except OSError:
            pass


def _wait_for_process_exit(process: subprocess.Popen) -> None:
    try:
        process.wait(timeout=_COMMAND_INTERRUPT_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                process.kill()
            except OSError:
                pass
        process.wait()


def _normalize_command_return_code(return_code: int) -> int:
    if _is_interrupt_exit_code(return_code):
        return INTERRUPTED_EXIT_CODE
    return return_code


def _is_interrupt_exit_code(exit_code: int) -> bool:
    return exit_code == INTERRUPTED_EXIT_CODE or exit_code == -signal.SIGINT


def _raise_for_interrupt_exit_code(exit_code: int) -> None:
    if _is_interrupt_exit_code(exit_code):
        raise KeyboardInterrupt


def _require_interactive_terminal_for_reconcile() -> None:
    _require_interactive_terminal(setting_name="reconcile_io")


def _require_interactive_terminal_for_hook() -> None:
    _require_interactive_terminal(setting_name="hook command io")


def _require_interactive_terminal(*, setting_name: str) -> None:
    if sys.stdin.isatty() and sys.stdout.isatty() and sys.stderr.isatty():
        return
    raise ValueError(f"{setting_name} 'tty' requires an interactive terminal")


def _build_hook_env(step: ExecutionStep, *, assume_yes: bool) -> dict[str, str]:
    hook_plan = step.hook_plan
    if hook_plan is not None and hook_plan.env is not None:
        env = dict(hook_plan.env)
    else:
        env = {}
        plan = step.package_plan
        if plan is not None:
            env.setdefault("DOTMAN_REPO_NAME", plan.repo_name)
            if step.package_id is not None:
                env.setdefault("DOTMAN_PACKAGE_ID", step.package_id)
            env.setdefault("DOTMAN_PROFILE", plan.requested_profile)
            env.setdefault("DOTMAN_OPERATION", plan.operation)
            if plan.repo_root is not None:
                env.setdefault("DOTMAN_REPO_ROOT", str(plan.repo_root))
            if plan.state_path is not None:
                env.setdefault("DOTMAN_STATE_PATH", str(plan.state_path))
            if hook_plan is not None:
                env.setdefault("DOTMAN_PACKAGE_ROOT", str(hook_plan.cwd))
            if plan.inferred_os is not None:
                env.setdefault("DOTMAN_OS", plan.inferred_os)
            for key, value in plan.variables.items():
                _flatten_vars(env, prefix=f"DOTMAN_VAR_{key}", value=value)
    env["DOTMAN_ASSUME_YES"] = "1" if assume_yes else "0"
    return env


def _build_target_env(target_plan: TargetPlan) -> dict[str, str]:
    return target_plan.command_env or {}


@contextmanager
def _materialize_reconcile_review_env(target_plan: TargetPlan) -> Iterator[dict[str, str]]:
    if target_plan.review_before_bytes is None or target_plan.review_after_bytes is None:
        yield {}
        return

    # Review helpers, especially `dotman reconcile editor` and reverse capture,
    # should review the same projected pull views the user selected from, not the raw repo/live files.
    with tempfile.TemporaryDirectory(prefix="dotman-reconcile-review-") as temp_dir:
        temp_root = Path(temp_dir)
        review_repo_path = temp_root / f"review-repo-{target_plan.repo_path.name}"
        review_live_path = temp_root / f"review-live-{target_plan.live_path.name}"
        _write_readonly_review_file(review_repo_path, target_plan.review_before_bytes)
        _write_readonly_review_file(review_live_path, target_plan.review_after_bytes)
        yield {
            "DOTMAN_REVIEW_REPO_PATH": str(review_repo_path),
            "DOTMAN_REVIEW_LIVE_PATH": str(review_live_path),
        }


def _write_readonly_review_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(0o444)


def _flatten_vars(output: dict[str, str], *, prefix: str, value: object) -> None:
    if isinstance(value, dict):
        for nested_key, nested_value in value.items():
            _flatten_vars(output, prefix=f"{prefix}__{nested_key}", value=nested_value)
        return
    output[prefix] = str(value)


def _step_target_id(step: ExecutionStep) -> tuple[str, str] | None:
    if step.target_plan is not None:
        return (step.target_plan.package_id, step.target_plan.target_name)
    if step.hook_plan is not None and step.hook_plan.package_id is not None and step.hook_plan.target_name is not None:
        return (step.hook_plan.package_id, step.hook_plan.target_name)
    return None


def _require_target_plan(step: ExecutionStep) -> TargetPlan:
    if step.target_plan is None:
        raise ValueError(f"step '{step.action}' is missing a target plan")
    return step.target_plan


def _step_repo_path(step: ExecutionStep) -> Path | None:
    if step.directory_item is not None:
        return step.directory_item.repo_path
    if step.target_plan is not None:
        return step.target_plan.repo_path
    return None


def _step_live_path(step: ExecutionStep) -> Path | None:
    if step.directory_item is not None:
        return step.directory_item.live_path
    if step.target_plan is not None:
        return step.target_plan.live_path
    return None


__all__ = [
    "ExecutionResult",
    "ExecutionSession",
    "ExecutionStep",
    "ExecutionStepResult",
    "PackageExecutionResult",
    "PackageExecutionUnit",
    "build_execution_session",
    "execute_session",
]
