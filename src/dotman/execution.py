from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from threading import Thread
from typing import Sequence

from dotman.engine import HOOK_NAMES_BY_OPERATION
from dotman.models import BindingPlan, DirectoryPlanItem, HookPlan, TargetPlan


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


def build_execution_session(plans: Sequence[BindingPlan], *, operation: str) -> ExecutionSession:
    package_units: list[PackageExecutionUnit] = []
    hook_names = _STEP_LABELS_BY_OPERATION[operation]
    for plan in plans:
        targets_by_package: dict[str, list[TargetPlan]] = {}
        for target in plan.target_plans:
            if target.action == "noop":
                continue
            targets_by_package.setdefault(target.package_id, []).append(target)

        hooks_by_package: dict[str, dict[str, list[HookPlan]]] = {}
        for hook_name in hook_names:
            for hook_plan in plan.hooks.get(hook_name, []):
                hooks_by_package.setdefault(hook_plan.package_id, {}).setdefault(hook_name, []).append(hook_plan)

        for package_id in plan.package_ids:
            package_steps: list[ExecutionStep] = []
            package_hooks = hooks_by_package.get(package_id, {})
            for hook_name in hook_names[:2]:
                package_steps.extend(
                    ExecutionStep(
                        package_id=package_id,
                        binding_plan=plan,
                        kind="hook",
                        action=hook_name,
                        hook_plan=hook_plan,
                    )
                    for hook_plan in package_hooks.get(hook_name, [])
                )

            target_steps: list[ExecutionStep] = []
            for target_plan in targets_by_package.get(package_id, []):
                target_steps.extend(_build_target_steps(plan=plan, target_plan=target_plan, operation=operation))
            package_steps.extend(target_steps)

            if target_steps:
                package_steps.extend(
                    ExecutionStep(
                        package_id=package_id,
                        binding_plan=plan,
                        kind="hook",
                        action=hook_names[2],
                        hook_plan=hook_plan,
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

    return ExecutionSession(operation=operation, packages=tuple(package_units))


def execute_session(
    session: ExecutionSession,
    *,
    stream_output: bool,
    on_package_start=None,
    on_step_start=None,
    on_step_finish=None,
    on_package_finish=None,
) -> ExecutionResult:
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
            result = _execute_step(step, stream_output=stream_output)
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
        )
    )
    return steps


def _execute_step(step: ExecutionStep, *, stream_output: bool) -> ExecutionStepResult:
    try:
        if step.kind == "hook":
            exit_code, stdout, stderr = _run_command(
                command=step.hook_plan.command,
                cwd=step.hook_plan.cwd,
                env=_build_hook_env(step),
                stream_output=stream_output,
                interactive=False,
            )
            return ExecutionStepResult(
                step=step,
                status="ok" if exit_code == 0 else "failed",
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                error=None if exit_code == 0 else f"command exited with status {exit_code}",
            )
        if step.kind == "reconcile":
            target_plan = _require_target_plan(step)
            if target_plan.reconcile_io == "tty":
                _require_interactive_terminal_for_reconcile()
                exit_code, stdout, stderr = _run_command_with_terminal(
                    command=target_plan.reconcile_command or "",
                    cwd=target_plan.command_cwd,
                    env=_build_target_env(target_plan),
                )
            else:
                exit_code, stdout, stderr = _run_command(
                    command=target_plan.reconcile_command or "",
                    cwd=target_plan.command_cwd,
                    env=_build_target_env(target_plan),
                    stream_output=stream_output,
                    interactive=False,
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
            _write_bytes(step.directory_item.live_path, step.directory_item.repo_path.read_bytes())
            return
        desired_bytes = _push_desired_bytes(target_plan)
        _write_bytes(target_plan.live_path, desired_bytes)
        return
    if step.action == "delete":
        delete_path = step.directory_item.live_path if step.directory_item is not None else target_plan.live_path
        _delete_file(delete_path, root=target_plan.live_path)
        return
    if step.action in {"create_repo", "update_repo"}:
        if step.directory_item is not None:
            _write_bytes(step.directory_item.repo_path, step.directory_item.live_path.read_bytes())
            return
        _write_bytes(target_plan.repo_path, _pull_desired_bytes(target_plan))
        return
    if step.action == "delete_repo":
        delete_path = step.directory_item.repo_path if step.directory_item is not None else target_plan.repo_path
        _delete_file(delete_path, root=target_plan.repo_path)
        return
    raise ValueError(f"unsupported execution action '{step.action}'")


def _execute_chmod_step(step: ExecutionStep) -> None:
    target_plan = _require_target_plan(step)
    if target_plan.chmod is None:
        return
    chmod_mode = int(target_plan.chmod, 8)
    if step.binding_plan.operation == "push":
        chmod_path = target_plan.live_path
    else:
        chmod_path = target_plan.repo_path
    if chmod_path.exists():
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
        return target_plan.live_path.read_bytes()
    exit_code, stdout, stderr = _run_command(
        command=target_plan.capture_command,
        cwd=target_plan.command_cwd,
        env=_build_target_env(target_plan),
        stream_output=False,
        interactive=False,
    )
    if exit_code != 0:
        raise ValueError(stderr.strip() or f"capture command exited with status {exit_code}")
    return stdout.encode("utf-8")


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp_file:
        temp_file.write(content)
        temp_path = Path(temp_file.name)
    temp_path.replace(path)


def _delete_file(path: Path, *, root: Path) -> None:
    if path.exists():
        path.unlink()
    prune_root = root if root.is_dir() else root.parent
    current = path.parent
    while current.exists() and current != prune_root and current != current.parent:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _run_command(
    *,
    command: str,
    cwd: Path | None,
    env: dict[str, str],
    stream_output: bool,
    interactive: bool,
) -> tuple[int, str, str]:
    if interactive and stream_output:
        return _run_command_with_terminal(command=command, cwd=cwd, env=env)

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


def _run_command_with_terminal(*, command: str, cwd: Path | None, env: dict[str, str]) -> tuple[int, str, str]:
    # TTY reconcile commands are allowed to launch full-screen editors. Piping
    # and prefixing their output corrupts terminal control sequences and leaves
    # the shell looking broken after exit, so dotman must hand them the tty.
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
