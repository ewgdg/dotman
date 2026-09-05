from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from typing import Any, Literal

from dotman import cli_emit, cli_interaction
from dotman.engine import DotmanEngine
from dotman.config import default_state_root
from dotman.operation_lock import OperationLock
from dotman.models import OperationPlan, SnapshotConfig
from dotman.operation_runner import run_restore_operation, run_sync_operation
from dotman.progress import ProgressSink, make_planning_sink
from dotman.snapshot import RestoreAction, SnapshotRecord, build_restore_actions
from dotman.ui_context import ui_config_scope


EngineFactory = Callable[[str | None], DotmanEngine]
SyncOperation = Literal["push", "pull"]
INTERRUPTED_EXIT_CODE = 130


class SyncCommandRunner:
    """Plan and run push, pull, and restore through typed operation boundaries."""

    command_names = frozenset({"push", "pull", "restore"})

    def __init__(self, *, engine_factory: EngineFactory, use_color: bool) -> None:
        self._engine_factory = engine_factory
        self._use_color = use_color

    def run(self, args: Any) -> int:
        if args.command not in self.command_names:
            raise ValueError(f"unsupported sync command '{args.command}'")

        engine = self._engine_factory(args.config)
        full_paths = args.full_path if args.full_path is not None else engine.config.ui.full_paths
        with ui_config_scope(engine.config.ui):
            if args.command in {"push", "pull"}:
                # Own the operation before planning and retain it throughout
                # review, early returns and execution. Preview never takes it.
                with nullcontext() if args.dry_run else OperationLock.acquire(default_state_root()):
                    return self._run_sync(
                        args=args,
                        engine=engine,
                        operation=args.command,
                        full_paths=full_paths,
                    )
            return self._run_restore(args=args, engine=engine, full_paths=full_paths)

    def _plan_operation(
        self,
        *,
        args: Any,
        engine: DotmanEngine,
        operation: SyncOperation,
        sink: ProgressSink | None = None,
    ) -> OperationPlan:
        run_noop = getattr(args, "run_noop", False)
        if args.binding:
            _repo, binding = cli_interaction.resolve_tracked_package_entry_text(
                engine,
                args.binding,
                operation=operation,
                allow_package_owners=True,
                json_output=args.json_output,
            )
            binding_text = f"{binding.repo}:{binding.selector}"
            if operation == "push":
                return engine.plan_push_query(binding_text, profile=binding.profile, run_noop=run_noop)
            return engine.plan_pull_query(binding_text, profile=binding.profile, run_noop=run_noop)
        if operation == "push":
            return engine.plan_push(sink=sink, run_noop=run_noop)
        return engine.plan_pull(sink=sink, run_noop=run_noop)

    def _finish_all_guard_skipped_operation(
        self,
        *,
        args: Any,
        plans: OperationPlan,
        operation: SyncOperation,
        full_paths: bool,
    ) -> int | None:
        cli_emit.emit_planning_guard_skips(
            plans=plans,
            json_output=args.json_output,
            use_color=self._use_color,
        )
        if not plans.guard_skips or plans.has_effective_work:
            return None
        if args.json_output or args.dry_run:
            return self._emit_payload(
                operation=operation,
                plans=plans,
                json_output=args.json_output,
                mode=cli_emit.effective_execution_mode(dry_run_requested=args.dry_run),
                full_paths=full_paths,
            )
        return 0

    def _run_sync(
        self,
        *,
        args: Any,
        engine: DotmanEngine,
        operation: SyncOperation,
        full_paths: bool,
    ) -> int:
        assume_yes = getattr(args, "assume_yes", False)
        run_noop = getattr(args, "run_noop", False)
        plans = self._plan_operation(
            args=args,
            engine=engine,
            operation=operation,
            sink=make_planning_sink(json_output=args.json_output),
        )
        skipped_result = self._finish_all_guard_skipped_operation(
            args=args,
            plans=plans,
            operation=operation,
            full_paths=full_paths,
        )
        if skipped_result is not None:
            return skipped_result
        if not cli_interaction.review_plans_for_interactive_diffs(
            plans=plans,
            operation=operation,
            json_output=args.json_output,
            full_paths=full_paths,
            assume_yes=assume_yes,
        ):
            cli_interaction.emit_interrupt_notice()
            return INTERRUPTED_EXIT_CODE
        filtered_plans = cli_interaction.filter_plans_for_interactive_selection(
            plans=plans,
            operation=operation,
            json_output=args.json_output,
            full_paths=full_paths,
            run_noop=run_noop,
        )
        if not isinstance(filtered_plans, OperationPlan):
            raise TypeError("sync selection must preserve the operation plan")
        plans = filtered_plans
        if args.dry_run:
            return self._emit_payload(
                operation=operation,
                plans=plans,
                json_output=args.json_output,
                mode=cli_emit.effective_execution_mode(dry_run_requested=True),
                full_paths=full_paths,
            )
        if operation == "push":
            prepared_plans = cli_interaction.prepare_push_plans_for_execution(
                plans=plans,
                json_output=args.json_output,
                full_paths=full_paths,
                assume_yes=assume_yes,
            )
            if prepared_plans is None:
                cli_interaction.emit_interrupt_notice()
                return INTERRUPTED_EXIT_CODE
            plans = prepared_plans
        return self._run_execution(
            operation=operation,
            plans=plans,
            json_output=args.json_output,
            full_paths=full_paths,
            run_noop=run_noop,
            assume_yes=assume_yes,
            snapshot_config=engine.config.snapshots if operation == "push" else None,
        )

    def _run_restore(self, *, args: Any, engine: DotmanEngine, full_paths: bool) -> int:
        snapshot = cli_interaction.resolve_snapshot_record(
            engine.config.snapshots.path,
            args.snapshot,
            json_output=args.json_output,
        )
        actions = build_restore_actions(snapshot)
        if not cli_interaction.review_restore_actions_for_interactive_diffs(
            snapshot=snapshot,
            actions=actions,
            json_output=args.json_output,
            full_paths=full_paths,
            assume_yes=getattr(args, "assume_yes", False),
        ):
            cli_interaction.emit_interrupt_notice()
            return INTERRUPTED_EXIT_CODE
        if args.dry_run:
            return cli_emit.emit_restore_payload(
                snapshot=snapshot,
                actions=actions,
                json_output=args.json_output,
                mode=cli_emit.effective_execution_mode(dry_run_requested=True),
                full_paths=full_paths,
                use_color=self._use_color,
            )
        return self._run_restore_execution(
            snapshot=snapshot,
            actions=actions,
            json_output=args.json_output,
            full_paths=full_paths,
        )

    def _emit_payload(
        self,
        *,
        operation: SyncOperation,
        plans: OperationPlan,
        json_output: bool,
        mode: str,
        full_paths: bool,
    ) -> int:
        return cli_emit.emit_payload(
            operation=operation,
            plans=plans,
            json_output=json_output,
            mode=mode,
            full_paths=full_paths,
            use_color=self._use_color,
            collect_pending_selection_items_for_operation=cli_interaction.collect_pending_selection_items_for_operation,
        )

    def _run_execution(
        self,
        *,
        operation: SyncOperation,
        plans: OperationPlan,
        json_output: bool,
        full_paths: bool,
        run_noop: bool,
        assume_yes: bool,
        snapshot_config: SnapshotConfig | None = None,
    ) -> int:
        renderer = (
            cli_emit.JsonExecutionRenderer()
            if json_output
            else cli_emit.HumanExecutionRenderer(full_paths=full_paths, use_color=self._use_color)
        )
        result = run_sync_operation(
            operation=operation,
            plans=plans,
            stream_output=renderer.stream_output,
            run_noop=run_noop,
            assume_yes=assume_yes,
            snapshot_config=snapshot_config,
            event_sink=renderer.render_sync_event,
        )
        return renderer.render_sync_result(result)

    def _run_restore_execution(
        self,
        *,
        snapshot: SnapshotRecord,
        actions: list[RestoreAction],
        json_output: bool,
        full_paths: bool,
    ) -> int:
        renderer = (
            cli_emit.JsonExecutionRenderer()
            if json_output
            else cli_emit.HumanExecutionRenderer(full_paths=full_paths, use_color=self._use_color)
        )
        result = run_restore_operation(
            snapshot=snapshot,
            actions=actions,
            event_sink=renderer.render_restore_event,
        )
        return renderer.render_restore_result(result)
