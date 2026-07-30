from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from dotman.progress import make_planning_sink
from dotman.ui_context import ui_config_scope

if TYPE_CHECKING:
    from dotman.progress import ProgressSink

from dotman.snapshot import build_restore_actions


@dataclass(frozen=True)
class SyncCommandRuntime:
    resolve_tracked_package_entry_text: Callable[..., Any]
    filter_plans_for_interactive_selection: Callable[..., Any]
    review_plans_for_interactive_diffs: Callable[..., bool]
    emit_interrupt_notice: Callable[[], None]
    interrupted_exit_code: int
    emit_payload: Callable[..., int]
    effective_execution_mode: Callable[..., str]
    prepare_push_plans_for_execution: Callable[..., Any]
    run_execution: Callable[..., int]
    resolve_snapshot_record: Callable[..., Any]
    review_restore_actions_for_interactive_diffs: Callable[..., bool]
    emit_restore_payload: Callable[..., int]
    run_restore_execution: Callable[..., int]
    emit_planning_guard_skips: Callable[..., None]


EngineFactory = Callable[[str | None], Any]
SYNC_COMMAND_NAMES = frozenset({"push", "pull", "restore"})


def run_sync_command(
    *,
    args: Any,
    engine_factory: EngineFactory,
    sync_runtime: SyncCommandRuntime,
) -> int:
    if args.command not in SYNC_COMMAND_NAMES:
        raise ValueError(f"unsupported sync command '{args.command}'")
    engine = engine_factory(args.config)
    full_paths = args.full_path if args.full_path is not None else engine.config.ui.full_paths
    with ui_config_scope(engine.config.ui):
        if args.command == "push":
            return _handle_push(args=args, engine=engine, handlers=sync_runtime, full_paths=full_paths)
        if args.command == "pull":
            return _handle_pull(args=args, engine=engine, handlers=sync_runtime, full_paths=full_paths)
        return _handle_restore(args=args, engine=engine, handlers=sync_runtime, full_paths=full_paths)



def _plan_operation(*, args: Any, engine: Any, handlers: SyncCommandRuntime, operation: str, sink: ProgressSink | None = None) -> Any:
    run_noop = getattr(args, "run_noop", False)
    if args.binding:
        _repo, binding = handlers.resolve_tracked_package_entry_text(
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
    return (
        engine.plan_push(sink=sink, run_noop=run_noop)
        if operation == "push"
        else engine.plan_pull(sink=sink, run_noop=run_noop)
    )


def _finish_all_guard_skipped_operation(
    *,
    args: Any,
    handlers: SyncCommandRuntime,
    plans: Any,
    operation: str,
    full_paths: bool,
) -> int | None:
    handlers.emit_planning_guard_skips(plans=plans, json_output=args.json_output)
    if not getattr(plans, "guard_skips", ()) or getattr(plans, "has_effective_work", True):
        return None
    if args.json_output or args.dry_run:
        return handlers.emit_payload(
            operation=operation,
            plans=plans,
            json_output=args.json_output,
            mode=handlers.effective_execution_mode(dry_run_requested=args.dry_run),
            full_paths=full_paths,
        )
    return 0



def _handle_push(*, args: Any, engine: Any, handlers: SyncCommandRuntime, full_paths: bool) -> int:
    assume_yes = getattr(args, "assume_yes", False)
    run_noop = getattr(args, "run_noop", False)
    sink = make_planning_sink(json_output=args.json_output)
    plans = _plan_operation(args=args, engine=engine, handlers=handlers, operation="push", sink=sink)
    skipped_result = _finish_all_guard_skipped_operation(
        args=args,
        handlers=handlers,
        plans=plans,
        operation="push",
        full_paths=full_paths,
    )
    if skipped_result is not None:
        return skipped_result
    if not handlers.review_plans_for_interactive_diffs(
        plans=plans,
        operation="push",
        json_output=args.json_output,
        full_paths=full_paths,
        assume_yes=assume_yes,
    ):
        handlers.emit_interrupt_notice()
        return handlers.interrupted_exit_code
    filter_kwargs = {
        "plans": plans,
        "operation": "push",
        "json_output": args.json_output,
        "full_paths": full_paths,
    }
    if run_noop:
        filter_kwargs["run_noop"] = True
    plans = handlers.filter_plans_for_interactive_selection(**filter_kwargs)
    if args.dry_run:
        return handlers.emit_payload(
            operation="push",
            plans=plans,
            json_output=args.json_output,
            mode=handlers.effective_execution_mode(dry_run_requested=True),
            full_paths=full_paths,
        )
    plans = handlers.prepare_push_plans_for_execution(
        plans=plans,
        json_output=args.json_output,
        full_paths=full_paths,
        assume_yes=assume_yes,
    )
    if plans is None:
        handlers.emit_interrupt_notice()
        return handlers.interrupted_exit_code
    return handlers.run_execution(
        operation="push",
        plans=plans,
        json_output=args.json_output,
        full_paths=full_paths,
        run_noop=run_noop,
        assume_yes=assume_yes,
        snapshot_config=engine.config.snapshots,
    )



def _handle_pull(*, args: Any, engine: Any, handlers: SyncCommandRuntime, full_paths: bool) -> int:
    assume_yes = getattr(args, "assume_yes", False)
    run_noop = getattr(args, "run_noop", False)
    sink = make_planning_sink(json_output=args.json_output)
    plans = _plan_operation(args=args, engine=engine, handlers=handlers, operation="pull", sink=sink)
    skipped_result = _finish_all_guard_skipped_operation(
        args=args,
        handlers=handlers,
        plans=plans,
        operation="pull",
        full_paths=full_paths,
    )
    if skipped_result is not None:
        return skipped_result
    if not handlers.review_plans_for_interactive_diffs(
        plans=plans,
        operation="pull",
        json_output=args.json_output,
        full_paths=full_paths,
        assume_yes=assume_yes,
    ):
        handlers.emit_interrupt_notice()
        return handlers.interrupted_exit_code
    filter_kwargs = {
        "plans": plans,
        "operation": "pull",
        "json_output": args.json_output,
        "full_paths": full_paths,
    }
    if run_noop:
        filter_kwargs["run_noop"] = True
    plans = handlers.filter_plans_for_interactive_selection(**filter_kwargs)
    if args.dry_run:
        return handlers.emit_payload(
            operation="pull",
            plans=plans,
            json_output=args.json_output,
            mode=handlers.effective_execution_mode(dry_run_requested=True),
            full_paths=full_paths,
        )
    return handlers.run_execution(
        operation="pull",
        plans=plans,
        json_output=args.json_output,
        full_paths=full_paths,
        run_noop=run_noop,
        assume_yes=assume_yes,
    )



def _handle_restore(*, args: Any, engine: Any, handlers: SyncCommandRuntime, full_paths: bool) -> int:
    snapshot = handlers.resolve_snapshot_record(
        engine.config.snapshots.path,
        args.snapshot,
        json_output=args.json_output,
    )
    restore_actions = build_restore_actions(snapshot)
    if not handlers.review_restore_actions_for_interactive_diffs(
        snapshot=snapshot,
        actions=restore_actions,
        json_output=args.json_output,
        full_paths=full_paths,
        assume_yes=getattr(args, "assume_yes", False),
    ):
        handlers.emit_interrupt_notice()
        return handlers.interrupted_exit_code
    if args.dry_run:
        return handlers.emit_restore_payload(
            snapshot=snapshot,
            actions=restore_actions,
            json_output=args.json_output,
            mode=handlers.effective_execution_mode(dry_run_requested=True),
            full_paths=full_paths,
        )
    return handlers.run_restore_execution(
        snapshot=snapshot,
        actions=restore_actions,
        json_output=args.json_output,
        full_paths=full_paths,
    )
