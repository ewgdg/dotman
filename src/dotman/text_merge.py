"""Three-way text merge through `git merge-file`."""

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from dotman.command_runtime import (
    ArgvCommand, CommandRequest, CommandRuntime, raise_for_command_interruption,
)


class TextMergeFailed(ValueError):
    """Git could not perform the merge; this is not a content conflict."""


@dataclass(frozen=True)
class TextMerge:
    """Merged bytes; when conflicted, they hold zdiff3 conflict blocks for resolution."""

    content: bytes
    conflicted: bool


def merge_text(
    current: bytes,
    base: bytes,
    other: bytes,
    *,
    labels: tuple[str, str, str],
    command_runtime: CommandRuntime,
) -> TextMerge:
    """Merge `other` into `current`; conflicts stay in the content as zdiff3 blocks.

    Launch failures propagate as OSError so callers keep their own error types.
    """
    with TemporaryDirectory(prefix="dotman-merge-") as directory:
        root = Path(directory)
        for name, content in (("current", current), ("base", base), ("other", other)):
            (root / name).write_bytes(content)
        current_label, base_label, other_label = labels
        result = command_runtime.run(CommandRequest(
            ArgvCommand(("git", "merge-file", "--stdout", "--zdiff3",
                         "-L", current_label, "-L", base_label, "-L", other_label,
                         "current", "base", "other")),
            cwd=root,
        ))
        raise_for_command_interruption(result)
        # Git returns a conflict count (capped at 127); errors return negative
        # status, represented as 255 by a normal process exit.
        if result.exit_code > 127:
            raise TextMergeFailed(
                f"exit {result.exit_code}: "
                + result.stderr.decode(errors="replace").strip().replace(str(root), "<merge>")
            )
        return TextMerge(result.stdout, conflicted=result.exit_code > 0)
