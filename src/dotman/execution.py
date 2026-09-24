from __future__ import annotations

import signal
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from dotman.atomic_files import write_bytes_atomic as atomic_write_bytes_atomic
from dotman.atomic_files import write_symlink_atomic as atomic_write_symlink_atomic
from dotman.command_runtime import (
    INTERRUPTED_EXIT_CODE,
    CommandRequest,
    ShellCommand,
    current_command_runtime,
)
from dotman.models import HookPlan, PackagePlan, TargetPlan


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

    @property
    def command(self) -> str | None:
        if self.hook_plan is not None:
            return self.hook_plan.command
        return None


@dataclass(frozen=True)
class ExecutionStepResult:
    step: ExecutionStep
    status: str
    skip_reason: str | None = None
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None


def _execute_step(step: ExecutionStep, *, stream_output: bool, unattended: bool) -> ExecutionStepResult:
    # Publication applies target effects itself; only hook steps run through here.
    if step.kind != "hook":
        raise ValueError(f"unsupported execution step kind '{step.kind}'")
    try:
        if step.hook_plan.io == "tty":
            _require_interactive_terminal_for_hook()
        command_result = current_command_runtime().run(
            CommandRequest(
                command=ShellCommand(step.hook_plan.command),
                cwd=step.hook_plan.cwd,
                env=_build_hook_env(step, unattended=unattended),
                io=step.hook_plan.io,
                stream_output=stream_output if step.hook_plan.io == "pipe" else False,
                elevation=step.hook_plan.elevation,
            )
        )
        exit_code = command_result.exit_code
        stdout = command_result.stdout_text
        stderr = command_result.stderr_text
        if exit_code == 0:
            return ExecutionStepResult(step=step, status="ok", exit_code=exit_code, stdout=stdout, stderr=stderr)
        if _is_interrupt_exit_code(exit_code):
            return ExecutionStepResult(
                step=step,
                status="interrupted",
                exit_code=INTERRUPTED_EXIT_CODE,
                error=f"command interrupted with status {INTERRUPTED_EXIT_CODE}",
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
    except Exception as exc:  # noqa: BLE001 - fail-fast execution should surface the original error text.
        return ExecutionStepResult(step=step, status="failed", error=str(exc))


def _is_guard_step(step: ExecutionStep) -> bool:
    return step.kind == "hook" and step.action.startswith("guard_")


def directory_synced_file_mode(*, destination_mode: int, source_mode: int) -> int:
    exec_bits = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    if source_mode & exec_bits:
        return destination_mode | exec_bits
    return destination_mode & ~exec_bits


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


def _is_interrupt_exit_code(exit_code: int) -> bool:
    return exit_code == INTERRUPTED_EXIT_CODE or exit_code == -signal.SIGINT


def _require_interactive_terminal_for_hook() -> None:
    _require_interactive_terminal(setting_name="hook command io")


def _require_interactive_terminal(*, setting_name: str) -> None:
    if sys.stdin.isatty() and sys.stdout.isatty() and sys.stderr.isatty():
        return
    raise ValueError(f"{setting_name} 'tty' requires an interactive terminal")


def _build_hook_env(step: ExecutionStep, *, unattended: bool) -> dict[str, str]:
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
    env["DOTMAN_UNATTENDED"] = "1" if unattended else "0"
    return env


def _flatten_vars(output: dict[str, str], *, prefix: str, value: object) -> None:
    if isinstance(value, dict):
        for nested_key, nested_value in value.items():
            _flatten_vars(output, prefix=f"{prefix}__{nested_key}", value=nested_value)
        return
    output[prefix] = str(value)


__all__ = [
    "ExecutionStep",
    "ExecutionStepResult",
]
