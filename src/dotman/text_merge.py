"""Three-way text merge through `git merge-file`."""

from pathlib import Path
from tempfile import TemporaryDirectory

from dotman.command_runtime import (
    ArgvCommand, CommandRequest, CommandRuntime, raise_for_command_interruption,
)


class TextMergeFailed(ValueError):
    """Git could not perform the merge; this is not a content conflict."""


def merge_text(
    current: bytes,
    base: bytes,
    other: bytes,
    *,
    labels: tuple[str, str, str],
    command_runtime: CommandRuntime,
) -> bytes | None:
    """Return merged bytes, or None when the changes conflict.

    Launch failures propagate as OSError so callers keep their own error types.
    """
    with TemporaryDirectory(prefix="dotman-merge-") as directory:
        root = Path(directory)
        for name, content in (("current", current), ("base", base), ("other", other)):
            (root / name).write_bytes(content)
        current_label, base_label, other_label = labels
        result = command_runtime.run(CommandRequest(
            ArgvCommand(("git", "merge-file", "--stdout",
                         "-L", current_label, "-L", base_label, "-L", other_label,
                         "current", "base", "other")),
            cwd=root,
        ))
        raise_for_command_interruption(result)
        # Git returns a conflict count (capped at 127); errors return negative
        # status, represented as 255 by a normal process exit.
        if 1 <= result.exit_code <= 127:
            return None
        if result.exit_code:
            raise TextMergeFailed(
                f"exit {result.exit_code}: "
                + result.stderr.decode(errors="replace").strip().replace(str(root), "<merge>")
            )
        return result.stdout
