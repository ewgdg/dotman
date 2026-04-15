from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
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
from dotman.models import BindingPlan, DirectoryPlanItem, HookPlan, TargetPlan
from dotman.repo_access import restore_repo_path_access_for_invoking_user
from dotman.reconcile_helpers import BUILTIN_JINJA_RECONCILE, run_jinja_reconcile
from dotman.templates import build_template_context, render_template_string
from dotman.terminal import preserve_terminal_state


@dataclass(frozen=True)
class ExecutionStep:
    package_id: str
    binding_plan: BindingPlan
    kind: str
    action: str
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
    binding_selector: str
    profile: str
    package_id: str
    steps: tuple[ExecutionStep, ...]


@dataclass(frozen=True)
class ExecutionSession:
    operation: str
    packages: tuple[PackageExecutionUnit, ...]
    requires_privilege: bool = False


@dataclass(frozen=True)
class ExecutionStepResult:
    step: ExecutionStep
    status: str
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        step = self.step
        return {
            "kind": step.kind,
            "action": step.action,
            "package_id": step.package_id,
            "binding": {
                "repo": step.binding_plan.binding.repo,
                "selector": step.binding_plan.binding.selector,
                "profile": step.binding_plan.binding.profile,
            },
            "status": self.status,
            "privileged": step.privileged,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error": self.error,
            "repo_path": str(_step_repo_path(step)) if _step_repo_path(step) is not None else None,
            "live_path": str(_step_live_path(step)) if _step_live_path(step) is not None else None,
            "command": step.command,
        }


@dataclass(frozen=True)
class PackageExecutionResult:
    unit: PackageExecutionUnit
    status: str
    steps: tuple[ExecutionStepResult, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "repo": self.unit.repo_name,
            "binding": {
                "selector": self.unit.binding_selector,
                "profile": self.unit.profile,
            },
            "package_id": self.unit.package_id,
            "status": self.status,
            "steps": [step.to_dict() for step in self.steps],
        }


@dataclass(frozen=True)
class ExecutionResult:
    session: ExecutionSession
    status: str
    packages: tuple[PackageExecutionResult, ...]

    @property
    def exit_code(self) -> int:
        return 0 if self.status == "ok" else 1

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": "execute",
            "operation": self.session.operation,
            "status": self.status,
            "requires_privilege": self.session.requires_privilege,
            "packages": [package.to_dict() for package in self.packages],
        }


_STEP_LABELS_BY_OPERATION = {
    "push": ("guard_push", "pre_push", "post_push"),
    "pull": ("guard_pull", "pre_pull", "post_pull"),
}


def build_execution_session(
    plans: Sequence[BindingPlan],
    *,
    operation: str,
    run_noop: bool = False,
) -> ExecutionSession:
    _ensure_no_unapproved_live_symlink_targets(plans, operation=operation)
    package_units: list[PackageExecutionUnit] = []
    hook_names = _STEP_LABELS_BY_OPERATION[operation]
    for plan in plans:
        targets_by_package: dict[str, list[TargetPlan]] = {}
        for target in plan.target_plans:
            if target.action == "noop":
                continue
            targets_by_package.setdefault(target.package_id, []).append(target)

        filtered_hooks_by_package: dict[str, dict[str, list[HookPlan]]] = {}
        for hook_name in hook_names:
            for hook_plan in plan.hooks.get(hook_name, []):
                filtered_hooks_by_package.setdefault(hook_plan.package_id, {}).setdefault(hook_name, []).append(hook_plan)

        raw_hook_plans = getattr(plan, "hook_plans", None) or plan.hooks
        raw_hooks_by_package: dict[str, dict[str, list[HookPlan]]] = {}
        if run_noop:
            for hook_name in hook_names:
                for hook_plan in raw_hook_plans.get(hook_name, []):
                    raw_hooks_by_package.setdefault(hook_plan.package_id, {}).setdefault(hook_name, []).append(hook_plan)

        for package_id in plan.package_ids:
            target_plans = targets_by_package.get(package_id, [])
            package_hooks = filtered_hooks_by_package.get(package_id, {})
            if not target_plans and run_noop:
                package_hooks = raw_hooks_by_package.get(package_id, {}) or package_hooks

            target_steps: list[ExecutionStep] = []
            for target_plan in target_plans:
                target_steps.extend(_build_target_steps(plan=plan, target_plan=target_plan, operation=operation))

            package_requires_privilege = any(step.privileged for step in target_steps)
            package_steps: list[ExecutionStep] = []
            for hook_name in hook_names[:2]:
                package_steps.extend(
                    ExecutionStep(
                        package_id=package_id,
                        binding_plan=plan,
                        kind="hook",
                        action=hook_name,
                        hook_plan=hook_plan,
                        privileged=package_requires_privilege,
                    )
                    for hook_plan in package_hooks.get(hook_name, [])
                )

            package_steps.extend(target_steps)

            if target_plans or (run_noop and package_hooks):
                package_steps.extend(
                    ExecutionStep(
                        package_id=package_id,
                        binding_plan=plan,
                        kind="hook",
                        action=hook_names[2],
                        hook_plan=hook_plan,
                        privileged=package_requires_privilege,
                    )
                    for hook_plan in package_hooks.get(hook_names[2], [])
                )

            if not package_steps:
                continue
            package_units.append(
                PackageExecutionUnit(
                    repo_name=plan.binding.repo,
                    binding_selector=plan.binding.selector,
                    profile=plan.binding.profile,
                    package_id=package_id,
                    steps=tuple(package_steps),
                )
            )

    return ExecutionSession(
        operation=operation,
        packages=tuple(package_units),
        requires_privilege=any(step.privileged for package in package_units for step in package.steps),
    )



def _ensure_no_unapproved_live_symlink_targets(plans: Sequence[BindingPlan], *, operation: str) -> None:
    if operation not in {"push", "upgrade"}:
        return

    hazards: list[str] = []
    for plan in plans:
        binding_label = f"{plan.binding.repo}:{plan.binding.selector}@{plan.binding.profile}"
        for target in plan.target_plans:
            if target.action == "noop" or not target.live_path_is_symlink:
                continue
            if target.target_kind == "directory":
                if target.dir_symlink_mode == "follow":
                    continue
                symlink_target = target.live_path_symlink_target or "<unknown>"
                hazards.append(
                    f"{binding_label} {target.package_id}:{target.target_name} ({target.live_path} -> {symlink_target})"
                )
                continue
            if target.file_symlink_mode == "follow" or target.allow_live_path_symlink_replace:
                continue
            symlink_target = target.live_path_symlink_target or "<unknown>"
            hazards.append(
                f"{binding_label} {target.package_id}:{target.target_name} ({target.live_path} -> {symlink_target})"
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
    package_results: list[PackageExecutionResult] = []
    failed = False
    for package_index, package in enumerate(session.packages):
        if on_package_start is not None:
            on_package_start(package)
        step_results: list[ExecutionStepResult] = []
        failed_in_package = False
        failed_step_index: int | None = None
        total_steps = len(package.steps)
        for step_index, step in enumerate(package.steps, start=1):
            if failed:
                step_results.append(ExecutionStepResult(step=step, status="skipped"))
                continue
            if on_step_start is not None:
                on_step_start(package, step, step_index, total_steps)
            result = _execute_step(step, stream_output=stream_output, assume_yes=assume_yes)
            step_results.append(result)
            if on_step_finish is not None:
                on_step_finish(package, result, step_index, total_steps)
            if result.status != "ok":
                failed = True
                failed_in_package = True
                failed_step_index = step_index - 1
                break
        if failed_in_package and failed_step_index is not None:
            for remaining_step in package.steps[failed_step_index + 1 :]:
                step_results.append(ExecutionStepResult(step=remaining_step, status="skipped"))
        package_result = PackageExecutionResult(
            unit=package,
            status="failed" if failed_in_package else "ok",
            steps=tuple(step_results),
        )
        package_results.append(package_result)
        if on_package_finish is not None:
            on_package_finish(package_result)
        if failed:
            for remaining_package in session.packages[package_index + 1 :]:
                skipped_result = PackageExecutionResult(
                    unit=remaining_package,
                    status="skipped",
                    steps=tuple(
                        ExecutionStepResult(step=step, status="skipped") for step in remaining_package.steps
                    ),
                )
                package_results.append(skipped_result)
                if on_package_start is not None:
                    on_package_start(remaining_package)
                if on_package_finish is not None:
                    on_package_finish(skipped_result)
            break
    return ExecutionResult(
        session=session,
        status="failed" if failed else "ok",
        packages=tuple(package_results),
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
    for package in session.packages:
        for step in package.steps:
            if step.privileged and step.kind != "hook":
                return _sudo_reason_for_step(step)
    for package in session.packages:
        for step in package.steps:
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
        return f"execute privileged hook for {step.package_id}"
    if live_path is not None:
        return f"access protected path: {live_path}"
    if repo_path is not None:
        return f"access protected path: {repo_path}"
    return "planned execution includes privileged operations"



def _build_target_steps(*, plan: BindingPlan, target_plan: TargetPlan, operation: str) -> list[ExecutionStep]:
    steps: list[ExecutionStep] = []
    if operation == "push":
        if target_plan.target_kind == "directory":
            steps.extend(
                ExecutionStep(
                    package_id=target_plan.package_id,
                    binding_plan=plan,
                    kind="target",
                    action=item.action,
                    target_plan=target_plan,
                    directory_item=item,
                    privileged=_target_step_needs_sudo(operation=operation, target_plan=target_plan, action=item.action, directory_item=item),
                )
                for item in target_plan.directory_items
            )
            if target_plan.directory_items and target_plan.chmod is not None:
                steps.append(
                    ExecutionStep(
                        package_id=target_plan.package_id,
                        binding_plan=plan,
                        kind="chmod",
                        action="chmod",
                        target_plan=target_plan,
                        privileged=needs_sudo_for_chmod(target_plan.live_path),
                    )
                )
            return steps
        steps.append(
            ExecutionStep(
                package_id=target_plan.package_id,
                binding_plan=plan,
                kind="target",
                action=target_plan.action,
                target_plan=target_plan,
                privileged=_target_step_needs_sudo(operation=operation, target_plan=target_plan, action=target_plan.action),
            )
        )
        if target_plan.action in {"create", "update"} and target_plan.chmod is not None:
            steps.append(
                ExecutionStep(
                    package_id=target_plan.package_id,
                    binding_plan=plan,
                    kind="chmod",
                    action="chmod",
                    target_plan=target_plan,
                    privileged=needs_sudo_for_chmod(target_plan.live_path),
                )
            )
        return steps

    if target_plan.reconcile_command is not None and target_plan.action == "update":
        steps.append(
            ExecutionStep(
                package_id=target_plan.package_id,
                binding_plan=plan,
                kind="reconcile",
                action="reconcile",
                target_plan=target_plan,
                privileged=_reconcile_step_needs_sudo(target_plan),
            )
        )
        return steps

    if target_plan.target_kind == "directory":
        action_map = {"create": "create_repo", "update": "update_repo", "delete": "delete_repo"}
        steps.extend(
            ExecutionStep(
                package_id=target_plan.package_id,
                binding_plan=plan,
                kind="target",
                action=action_map[item.action],
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
            package_id=target_plan.package_id,
            binding_plan=plan,
            kind="target",
            action=direct_action,
            target_plan=target_plan,
            privileged=_target_step_needs_sudo(operation=operation, target_plan=target_plan, action=direct_action),
        )
    )
    return steps


def _execute_step(step: ExecutionStep, *, stream_output: bool, assume_yes: bool) -> ExecutionStepResult:
    try:
        if step.kind == "hook":
            exit_code, stdout, stderr = _run_command(
                command=step.hook_plan.command,
                cwd=step.hook_plan.cwd,
                env=_build_hook_env(step),
                stream_output=stream_output,
                interactive=False,
                privileged=step.privileged,
            )
            return ExecutionStepResult(
                step=step,
                status="ok" if exit_code == 0 else "failed",
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                error=None if exit_code == 0 else f"command exited with status {exit_code}",
            )

        target_plan = _require_target_plan(step)
        if step.binding_plan.operation in {"push", "upgrade"} and target_plan.target_kind == "directory":
            if target_plan.live_path.is_symlink() and target_plan.dir_symlink_mode != "follow":
                raise ValueError(
                    f"live target path is a symlink for target '{target_plan.package_id}:{target_plan.target_name}': "
                    f"{target_plan.live_path} -> {target_plan.live_path.resolve(strict=False)}"
                )

        if step.kind == "reconcile":
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
                    stdout = ""
                    stderr = ""
                elif target_plan.reconcile_io == "tty":
                    exit_code, stdout, stderr = _run_command_with_terminal(
                        command=target_plan.reconcile_command or "",
                        cwd=target_plan.command_cwd,
                        env=command_env,
                        privileged=step.privileged,
                    )
                else:
                    exit_code, stdout, stderr = _run_command(
                        command=target_plan.reconcile_command or "",
                        cwd=target_plan.command_cwd,
                        env=command_env,
                        stream_output=stream_output,
                        interactive=False,
                        privileged=step.privileged,
                    )
            return ExecutionStepResult(
                step=step,
                status="ok" if exit_code == 0 else "failed",
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                error=None if exit_code == 0 else f"command exited with status {exit_code}",
            )
        if step.kind == "chmod":
            _execute_chmod_step(step)
            return ExecutionStepResult(step=step, status="ok")
        _execute_target_step(step)
        return ExecutionStepResult(step=step, status="ok")
    except Exception as exc:  # noqa: BLE001 - fail-fast execution should surface the original error text.
        return ExecutionStepResult(step=step, status="failed", error=str(exc))


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
        repo_path = step.directory_item.repo_path if step.directory_item is not None else target_plan.repo_path
        if step.directory_item is not None:
            repo_bytes = read_bytes(step.directory_item.live_path)
        elif target_plan.capture_command == BUILTIN_PATCH_CAPTURE:
            repo_bytes = _pull_patch_capture_bytes(target_plan=target_plan, binding_plan=step.binding_plan)
        else:
            repo_bytes = _pull_desired_bytes(target_plan)
        if needs_sudo_for_write(repo_path):
            sudo_write_bytes_atomic(repo_path, repo_bytes, restore_root=step.binding_plan.repo_root)
        else:
            _write_bytes(repo_path, repo_bytes)
            _restore_repo_path_access_for_invoking_user(repo_path, repo_root=step.binding_plan.repo_root)
        return
    if step.action == "delete_repo":
        delete_path = step.directory_item.repo_path if step.directory_item is not None else target_plan.repo_path
        if needs_sudo_for_write(delete_path):
            sudo_delete_path_and_prune_empty_parents(delete_path, root=target_plan.repo_path)
        else:
            _delete_file(delete_path, root=target_plan.repo_path)
        return
    raise ValueError(f"unsupported execution action '{step.action}'")


def _execute_chmod_step(step: ExecutionStep) -> None:
    target_plan = _require_target_plan(step)
    if target_plan.chmod is None:
        return
    chmod_mode = int(target_plan.chmod, 8)
    if step.binding_plan.operation == "push":
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
        raise ValueError(stderr.strip() or f"render command exited with status {exit_code}")
    return stdout.encode("utf-8")


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


def _pull_patch_capture_bytes(*, target_plan: TargetPlan, binding_plan: BindingPlan) -> bytes:
    projector = _build_patch_capture_projector(target_plan=target_plan, binding_plan=binding_plan)
    with _materialize_patch_capture_review_env(target_plan):
        return capture_patch(repo_path=str(target_plan.repo_path), project_repo_bytes=projector)


def _build_patch_capture_projector(*, target_plan: TargetPlan, binding_plan: BindingPlan):
    context = build_template_context(
        binding_plan.variables,
        profile=binding_plan.binding.profile,
        inferred_os=binding_plan.inferred_os or sys.platform,
    )
    base_dir = target_plan.repo_path.parent

    def project(candidate_bytes: bytes) -> bytes:
        candidate_text = candidate_bytes.decode("utf-8")
        return render_template_string(candidate_text, context, base_dir=base_dir, source_path=target_plan.repo_path).encode("utf-8")

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
    if interactive and stream_output:
        return _run_command_with_terminal(command=command, cwd=cwd, env=env, privileged=False)

    process = subprocess.Popen(
        command,
        cwd=str(cwd) if cwd is not None else None,
        env={**os.environ, **env},
        shell=True,
        executable="/bin/sh",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
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

    stdout_thread = Thread(target=pump, args=(process.stdout, stdout_buffer, sys.stdout), daemon=True)
    stderr_thread = Thread(target=pump, args=(process.stderr, stderr_buffer, sys.stderr), daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    return_code = process.wait()
    stdout_thread.join()
    stderr_thread.join()
    return return_code, "".join(stdout_buffer), "".join(stderr_buffer)


def _run_command_with_terminal(*, command: str, cwd: Path | None, env: dict[str, str], privileged: bool = False) -> tuple[int, str, str]:
    # TTY reconcile commands are allowed to launch full-screen editors. Piping
    # and prefixing their output corrupts terminal control sequences and leaves
    # the shell looking broken after exit, so dotman must hand them the tty.
    if privileged and os.geteuid() != 0:
        request_sudo("run privileged command")
        command = sudo_prefix_command(command)
    with preserve_terminal_state():
        completed = subprocess.run(
            command,
            cwd=str(cwd) if cwd is not None else None,
            env={**os.environ, **env},
            shell=True,
            executable="/bin/sh",
            check=False,
        )
    return completed.returncode, "", ""


def _require_interactive_terminal_for_reconcile() -> None:
    if sys.stdin.isatty() and sys.stdout.isatty() and sys.stderr.isatty():
        return
    raise ValueError("reconcile_io 'tty' requires an interactive terminal")


def _build_hook_env(step: ExecutionStep) -> dict[str, str]:
    plan = step.binding_plan
    hook_plan = step.hook_plan
    env = {
        "DOTMAN_REPO_NAME": plan.binding.repo,
        "DOTMAN_PACKAGE_ID": step.package_id,
        "DOTMAN_PROFILE": plan.binding.profile,
        "DOTMAN_OPERATION": plan.operation,
    }
    if plan.repo_root is not None:
        env["DOTMAN_REPO_ROOT"] = str(plan.repo_root)
    if plan.state_path is not None:
        env["DOTMAN_STATE_PATH"] = str(plan.state_path)
    if hook_plan is not None:
        env["DOTMAN_PACKAGE_ROOT"] = str(hook_plan.cwd)
    if plan.inferred_os is not None:
        env["DOTMAN_OS"] = plan.inferred_os
    for key, value in plan.variables.items():
        _flatten_vars(env, prefix=f"DOTMAN_VAR_{key}", value=value)
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
