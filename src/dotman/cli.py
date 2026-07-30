from __future__ import annotations

import os
import sys
from collections.abc import Sequence

from dotman import cli_emit, cli_interaction
from dotman.cli_parser import build_parser, normalize_edit_query_argv
from dotman.engine import DotmanEngine
from dotman.inspection_commands import InspectionCommandRunner
from dotman.interaction import Interaction, TerminalInteraction
from dotman.standalone_commands import StandaloneCommandRunner
from dotman.state_commands import StateCommandRunner
from dotman.sync_commands import SyncCommandRunner


INTERRUPTED_EXIT_CODE = 130


def colors_enabled() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def main(
    argv: Sequence[str] | None = None,
    *,
    interaction: Interaction | None = None,
) -> int:
    try:
        raw_argv = list(argv) if argv is not None else sys.argv[1:]
        args = build_parser().parse_args(normalize_edit_query_argv(raw_argv))
        active_interaction = interaction
        stdin_isatty = getattr(sys.stdin, "isatty", None)
        if active_interaction is None and stdin_isatty is not None and stdin_isatty():
            active_interaction = TerminalInteraction()
        engine_factory = lambda config_path: DotmanEngine.from_config_path(
            config_path,
            file_symlink_mode=args.file_symlink_mode,
            dir_symlink_mode=args.dir_symlink_mode,
        )
        use_color = colors_enabled()
        command_runners = (
            StandaloneCommandRunner(),
            InspectionCommandRunner(
                engine_factory=engine_factory,
                runtime=cli_interaction.InspectionRuntime(),
                use_color=use_color,
            ),
            StateCommandRunner(
                engine_factory=engine_factory,
                runtime=cli_interaction.StateRuntime(active_interaction),
                use_color=use_color,
            ),
            SyncCommandRunner(
                engine_factory=engine_factory,
                use_color=use_color,
            ),
        )
        runner_by_command = {
            command_name: runner
            for runner in command_runners
            for command_name in runner.command_names
        }
        selected_runner = runner_by_command.get(args.command)
        if selected_runner is None:
            raise ValueError(f"unsupported command '{args.command}'")
        return selected_runner.run(args)
    except KeyboardInterrupt:
        cli_interaction.emit_interrupt_notice()
        return INTERRUPTED_EXIT_CODE
    except ValueError as exc:
        cli_emit.emit_error(
            exc,
            use_color=sys.stderr.isatty() and os.environ.get("NO_COLOR") is None,
        )
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
