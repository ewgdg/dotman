"""Three-way reconciliation of frozen repository representations."""

from dotman.command_runtime import CommandRuntime
from dotman.sync_base_store import FilePresent, DirectoryChildPresent, SyncBasePayload
from dotman.text_merge import TextMergeFailed, merge_text


class ReconciliationConflict(ValueError):
    """The frozen sides require an explicit resolution decision.

    `conflict` holds the zdiff3 merge output to resolve in the Editor; it is None
    when the sides disagree on presence and no text merge was possible.
    """

    def __init__(self, message: str, conflict: SyncBasePayload | None = None) -> None:
        super().__init__(message)
        self.conflict = conflict


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
        executable = (repository.executable if repository.executable == captured.executable
                      else captured.executable if repository.executable == base.executable
                      else repository.executable)
        try:
            content = reconcile(
                FilePresent(base.content), FilePresent(repository.content), FilePresent(captured.content),
                command_runtime=command_runtime,
            ).content
        except ReconciliationConflict as exc:
            raise ReconciliationConflict(str(exc), DirectoryChildPresent(exc.conflict.content, executable)) from exc
        return DirectoryChildPresent(content, executable)
    try:
        merged = merge_text(
            repository.content, base.content, captured.content,
            labels=("repository", "Sync Base", "Capture"), command_runtime=command_runtime,
        )
    except TextMergeFailed as exc:
        raise ReconciliationFailed(f"Reconciliation failed with {exc}") from exc
    except OSError as exc:
        raise ReconciliationFailed(str(exc)) from exc
    if merged.conflicted:
        raise ReconciliationConflict("Repository and Capture contain conflicting changes", FilePresent(merged.content))
    return FilePresent(merged.content)
