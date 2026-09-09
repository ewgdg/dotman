"""Three-way reconciliation of frozen repository representations."""

from pathlib import Path
from tempfile import TemporaryDirectory

from dotman.command_runtime import (
    ArgvCommand, CommandRequest, CommandRuntime, raise_for_command_interruption,
)
from dotman.sync_base_store import FilePresent, Missing, DirectoryChildPresent, SyncBasePayload


class ReconciliationConflict(ValueError):
    """The frozen sides require an explicit resolution decision."""


class ReconciliationFailed(ValueError):
    """The reconciliation provider failed; its frozen operands remain retryable."""


def reconcile(
    base: SyncBasePayload,
    repository: SyncBasePayload,
    captured: SyncBasePayload,
    *,
    command_runtime: CommandRuntime,
) -> SyncBasePayload:
    if repository == captured:
        return repository
    if repository == base:
        return captured
    if captured == base:
        return repository
    if not all(isinstance(value, (FilePresent, DirectoryChildPresent)) for value in (base, repository, captured)):
        raise ReconciliationConflict("Repository and Capture disagree on file presence")
    if isinstance(repository, DirectoryChildPresent):
        if not isinstance(base, DirectoryChildPresent) or not isinstance(captured, DirectoryChildPresent):
            raise ValueError("Child reconciliation requires child ancestry and Capture")
        # Bytes and Git executable state are independent merge operands. A mode
        # change must not turn otherwise non-overlapping byte changes into conflict.
        content = reconcile(
            FilePresent(base.content), FilePresent(repository.content), FilePresent(captured.content),
            command_runtime=command_runtime,
        ).content
        executable = (repository.executable if repository.executable == captured.executable
                      else captured.executable if repository.executable == base.executable
                      else repository.executable)
        return DirectoryChildPresent(content, executable)
    with TemporaryDirectory(prefix="dotman-merge-") as directory:
        root = Path(directory)
        for name, value in (("base", base), ("repository", repository), ("capture", captured)):
            (root / name).write_bytes(value.content)
        try:
            result = command_runtime.run(CommandRequest(
                ArgvCommand(("git", "merge-file", "--stdout",
                             "-L", "repository", "-L", "Sync Base", "-L", "Capture",
                             "repository", "base", "capture")),
                cwd=root,
            ))
        except OSError as exc:
            raise ReconciliationFailed(str(exc)) from exc
        raise_for_command_interruption(result)
        # Git returns a conflict count (capped at 127); errors return negative
        # status, represented as 255 by a normal process exit.
        if 1 <= result.exit_code <= 127:
            raise ReconciliationConflict("Repository and Capture contain conflicting changes")
        if result.exit_code:
            raise ReconciliationFailed(
                f"Reconciliation failed with exit {result.exit_code}: "
                + result.stderr.decode(errors="replace").strip().replace(str(root), "<reconciliation>")
            )
        return FilePresent(result.stdout)
