from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from dotman.add import prepare_add_to_package, write_add_result
from dotman.engine import TrackedTargetConflictError
from dotman.models import package_ref_text
from dotman.snapshot import (
    build_rollback_actions,
    create_push_snapshot,
    list_snapshots,
    mark_snapshot_status,
    prune_snapshots,
    record_snapshot_restore,
)


@dataclass(frozen=True)
class CliCommandHandlers:
    run_basic_reconcile: Callable[..., int]
    run_jinja_reconcile: Callable[..., int]
    run_jinja_render: Callable[..., int]
    run_patch_capture: Callable[..., int]
    resolve_binding_text: Callable[..., Any]
    ensure_track_binding_replacement_confirmed: Callable[..., bool]
    find_recorded_bindings_for_scope: Callable[..., list[Any]]
    emit_kept_binding: Callable[..., int]
    emit_skipped_tracking: Callable[..., int]
    prompt_for_conflicting_package_binding: Callable[..., Any]
    select_non_conflicting_track_profile: Callable[..., Any]
    ensure_track_binding_implicit_overrides_confirmed: Callable[..., bool]
    find_recorded_binding_exact: Callable[..., Any]
    emit_tracked_binding: Callable[..., int]
    resolve_add_package_text: Callable[..., tuple[str, str]]
    interactive_mode_enabled: Callable[..., bool]
    add_editor_available: Callable[[], bool]
    review_add_manifest: Callable[..., Any]
    confirm_add_manifest_write: Callable[..., bool]
    emit_add_result: Callable[..., int]
    emit_noop_add_result: Callable[..., int]
    emit_kept_add_result: Callable[..., int]
    resolve_tracked_binding_text: Callable[..., Any]
    filter_plans_for_interactive_selection: Callable[..., Any]
    review_plans_for_interactive_diffs: Callable[..., bool]
    emit_interrupt_notice: Callable[[], None]
    interrupted_exit_code: int
    emit_payload: Callable[..., int]
    effective_execution_mode: Callable[..., str]
    prepare_push_plans_for_execution: Callable[..., Any]
    execute_plans: Callable[..., Any]
    emit_execution_result: Callable[..., int]
    run_execution: Callable[..., int]
    resolve_snapshot_record: Callable[..., Any]
    review_rollback_actions_for_interactive_diffs: Callable[..., bool]
    emit_rollback_payload: Callable[..., int]
    run_rollback_execution: Callable[..., int]
    emit_forgotten_binding: Callable[..., int]
    find_remaining_tracked_package_after_untrack: Callable[..., Any]
    emit_tracked_packages: Callable[..., int]
    resolve_tracked_package_text: Callable[..., Any]
    emit_tracked_package_detail: Callable[..., int]
    emit_snapshot_list: Callable[..., int]
    emit_snapshot_detail: Callable[..., int]


EngineFactory = Callable[[str | None], Any]


def dispatch_command(*, args: Any, engine_factory: EngineFactory, handlers: CliCommandHandlers) -> int:
    pre_engine_result = _dispatch_pre_engine_command(args=args, handlers=handlers)
    if pre_engine_result is not None:
        return pre_engine_result

    engine = engine_factory(args.config)
    if args.command == "track":
        return _handle_track(args=args, engine=engine, handlers=handlers)
    if args.command == "add":
        return _handle_add(args=args, engine=engine, handlers=handlers)
    if args.command == "push":
        return _handle_push(args=args, engine=engine, handlers=handlers)
    if args.command == "pull":
        return _handle_pull(args=args, engine=engine, handlers=handlers)
    if args.command == "rollback":
        return _handle_rollback(args=args, engine=engine, handlers=handlers)
    if args.command in {"untrack", "forget"}:
        return _handle_untrack(args=args, engine=engine, handlers=handlers)
    if args.command == "list" and args.list_command in {"tracked", "installed"}:
        tracked_state = engine.list_tracked_state()
        return handlers.emit_tracked_packages(
            engine=engine,
            packages=tracked_state.packages,
            invalid_bindings=tracked_state.invalid_bindings,
            json_output=args.json_output,
        )
    if args.command == "list" and args.list_command == "snapshots":
        return handlers.emit_snapshot_list(
            snapshots=list_snapshots(engine.config.snapshots.path),
            json_output=args.json_output,
            max_generations=engine.config.snapshots.max_generations,
        )
    if args.command == "info" and args.info_command in {"tracked", "installed"}:
        return _handle_info_tracked(args=args, engine=engine, handlers=handlers)
    if args.command == "info" and args.info_command == "snapshot":
        return handlers.emit_snapshot_detail(
            snapshot=handlers.resolve_snapshot_record(
                engine.config.snapshots.path,
                args.snapshot,
                json_output=args.json_output,
            ),
            json_output=args.json_output,
            full_paths=args.full_path,
        )
    return 0



def _dispatch_pre_engine_command(*, args: Any, handlers: CliCommandHandlers) -> int | None:
    if args.command == "capture" and args.capture_command == "patch":
        return handlers.run_patch_capture(
            repo_path=args.repo_path,
            review_repo_path=args.review_repo_path,
            review_live_path=args.review_live_path,
            profile=args.profile,
            inferred_os=args.template_os,
            var_assignments=args.var,
        )
    if args.command == "reconcile" and args.reconcile_command == "editor":
        return handlers.run_basic_reconcile(
            repo_path=args.repo_path,
            live_path=args.live_path,
            additional_sources=args.additional_source,
            review_repo_path=args.review_repo_path,
            review_live_path=args.review_live_path,
            editor=args.editor,
            assume_yes=getattr(args, "assume_yes", False),
        )
    if args.command == "reconcile" and args.reconcile_command == "jinja":
        return handlers.run_jinja_reconcile(
            repo_path=args.repo_path,
            live_path=args.live_path,
            review_repo_path=args.review_repo_path,
            review_live_path=args.review_live_path,
            editor=args.editor,
            assume_yes=getattr(args, "assume_yes", False),
        )
    if args.command == "render" and args.render_command == "jinja":
        return handlers.run_jinja_render(
            source_path=args.source_path,
            profile=args.profile,
            inferred_os=args.template_os,
            var_assignments=args.var,
        )
    return None



def _handle_track(*, args: Any, engine: Any, handlers: CliCommandHandlers) -> int:
    assume_yes = getattr(args, "assume_yes", False)
    binding_text, profile = handlers.resolve_binding_text(engine, args.binding, json_output=args.json_output)
    _repo, binding, _selector_kind = engine.resolve_binding(binding_text, profile=profile)
    while True:
        if not handlers.ensure_track_binding_replacement_confirmed(
            engine,
            binding=binding,
            json_output=args.json_output,
            assume_yes=assume_yes,
        ):
            existing_bindings = handlers.find_recorded_bindings_for_scope(engine, binding)
            if len(existing_bindings) == 1:
                return handlers.emit_kept_binding(binding=existing_bindings[0], json_output=args.json_output)
            return handlers.emit_skipped_tracking(binding=binding, json_output=args.json_output)
        try:
            engine.validate_recorded_binding(binding)
        except TrackedTargetConflictError as exc:
            promoted_binding = handlers.prompt_for_conflicting_package_binding(
                engine,
                binding=binding,
                conflict=exc,
                json_output=args.json_output,
            )
            if promoted_binding is not None:
                binding = promoted_binding
                binding_text = f"{binding.repo}:{binding.selector}"
                continue
            alternative_profile = handlers.select_non_conflicting_track_profile(
                engine,
                binding_text=binding_text,
                current_profile=binding.profile,
                json_output=args.json_output,
            )
            if alternative_profile is None:
                raise
            _repo, binding, _selector_kind = engine.resolve_binding(binding_text, profile=alternative_profile)
            continue
        if not handlers.ensure_track_binding_implicit_overrides_confirmed(
            engine,
            binding=binding,
            json_output=args.json_output,
            assume_yes=assume_yes,
        ):
            existing_binding = handlers.find_recorded_binding_exact(engine, binding)
            if existing_binding is not None:
                return handlers.emit_kept_binding(binding=existing_binding, json_output=args.json_output)
            return handlers.emit_skipped_tracking(binding=binding, json_output=args.json_output)
        engine.record_binding(binding)
        return handlers.emit_tracked_binding(binding=binding, json_output=args.json_output)



def _handle_add(*, args: Any, engine: Any, handlers: CliCommandHandlers) -> int:
    repo_name, package_id = handlers.resolve_add_package_text(
        engine,
        args.package_query,
        json_output=args.json_output,
    )
    assume_yes = getattr(args, "assume_yes", False)
    result = prepare_add_to_package(
        repo_root=engine.get_repo(repo_name).root,
        repo_name=repo_name,
        package_id=package_id,
        live_path_text=args.live_path,
    )
    if args.json_output or not handlers.interactive_mode_enabled(json_output=args.json_output):
        return handlers.emit_add_result(
            result=write_add_result(result),
            json_output=args.json_output,
        )
    if handlers.add_editor_available():
        review_result = handlers.review_add_manifest(result)
        if review_result is None:
            raise ValueError("add review expected an editor, but none is configured")
        if review_result.exit_code != 0:
            return review_result.exit_code
        if review_result.manifest_text == result.before_text:
            return handlers.emit_noop_add_result(json_output=args.json_output)
        if not handlers.confirm_add_manifest_write(repo_name=repo_name, package_id=package_id, assume_yes=assume_yes):
            return handlers.emit_kept_add_result(
                repo_name=repo_name,
                package_id=package_id,
                json_output=args.json_output,
            )
        result = write_add_result(result, manifest_text=review_result.manifest_text)
        return handlers.emit_add_result(result=result, json_output=args.json_output)
    return handlers.emit_add_result(result=write_add_result(result), json_output=args.json_output)



def _plan_operation(*, args: Any, engine: Any, handlers: CliCommandHandlers, operation: str) -> Any:
    if args.binding:
        _repo, binding = handlers.resolve_tracked_binding_text(
            engine,
            args.binding,
            operation=operation,
            allow_package_owners=True,
            json_output=args.json_output,
        )
        binding_text = f"{binding.repo}:{binding.selector}"
        if operation == "push":
            return [engine.plan_push_binding(binding_text, profile=binding.profile)]
        return [engine.plan_pull_binding(binding_text, profile=binding.profile)]
    return engine.plan_push() if operation == "push" else engine.plan_pull()



def _handle_push(*, args: Any, engine: Any, handlers: CliCommandHandlers) -> int:
    assume_yes = getattr(args, "assume_yes", False)
    run_noop = getattr(args, "run_noop", False)
    plans = _plan_operation(args=args, engine=engine, handlers=handlers, operation="push")
    if not handlers.review_plans_for_interactive_diffs(
        plans=plans,
        operation="push",
        json_output=args.json_output,
        full_paths=args.full_path,
        assume_yes=assume_yes,
    ):
        handlers.emit_interrupt_notice()
        return handlers.interrupted_exit_code
    plans = handlers.filter_plans_for_interactive_selection(
        plans=plans,
        operation="push",
        json_output=args.json_output,
        full_paths=args.full_path,
    )
    if args.dry_run:
        return handlers.emit_payload(
            operation="push",
            plans=plans,
            json_output=args.json_output,
            mode=handlers.effective_execution_mode(dry_run_requested=True),
            full_paths=args.full_path,
        )
    plans = handlers.prepare_push_plans_for_execution(
        plans=plans,
        json_output=args.json_output,
        full_paths=args.full_path,
        assume_yes=assume_yes,
    )
    if plans is None:
        handlers.emit_interrupt_notice()
        return handlers.interrupted_exit_code
    snapshot = create_push_snapshot(plans, engine.config.snapshots)
    try:
        execution_result = handlers.execute_plans(
            operation="push",
            plans=plans,
            json_output=args.json_output,
            full_paths=args.full_path,
            run_noop=run_noop,
            assume_yes=assume_yes,
        )
    except Exception:
        if snapshot is not None:
            mark_snapshot_status(snapshot, "failed")
            prune_snapshots(
                engine.config.snapshots.path,
                max_generations=engine.config.snapshots.max_generations,
            )
        raise
    if snapshot is not None:
        mark_snapshot_status(snapshot, "applied" if execution_result.exit_code == 0 else "failed")
        prune_snapshots(
            engine.config.snapshots.path,
            max_generations=engine.config.snapshots.max_generations,
        )
    return handlers.emit_execution_result(result=execution_result, json_output=args.json_output)



def _handle_pull(*, args: Any, engine: Any, handlers: CliCommandHandlers) -> int:
    assume_yes = getattr(args, "assume_yes", False)
    run_noop = getattr(args, "run_noop", False)
    plans = _plan_operation(args=args, engine=engine, handlers=handlers, operation="pull")
    if not handlers.review_plans_for_interactive_diffs(
        plans=plans,
        operation="pull",
        json_output=args.json_output,
        full_paths=args.full_path,
        assume_yes=assume_yes,
    ):
        handlers.emit_interrupt_notice()
        return handlers.interrupted_exit_code
    plans = handlers.filter_plans_for_interactive_selection(
        plans=plans,
        operation="pull",
        json_output=args.json_output,
        full_paths=args.full_path,
    )
    if args.dry_run:
        return handlers.emit_payload(
            operation="pull",
            plans=plans,
            json_output=args.json_output,
            mode=handlers.effective_execution_mode(dry_run_requested=True),
            full_paths=args.full_path,
        )
    return handlers.run_execution(
        operation="pull",
        plans=plans,
        json_output=args.json_output,
        full_paths=args.full_path,
        run_noop=run_noop,
        assume_yes=assume_yes,
    )



def _handle_rollback(*, args: Any, engine: Any, handlers: CliCommandHandlers) -> int:
    snapshot = handlers.resolve_snapshot_record(
        engine.config.snapshots.path,
        args.snapshot,
        json_output=args.json_output,
    )
    rollback_actions = build_rollback_actions(snapshot)
    if not handlers.review_rollback_actions_for_interactive_diffs(
        snapshot=snapshot,
        actions=rollback_actions,
        json_output=args.json_output,
        full_paths=args.full_path,
        assume_yes=getattr(args, "assume_yes", False),
    ):
        handlers.emit_interrupt_notice()
        return handlers.interrupted_exit_code
    if args.dry_run:
        return handlers.emit_rollback_payload(
            snapshot=snapshot,
            actions=rollback_actions,
            json_output=args.json_output,
            mode=handlers.effective_execution_mode(dry_run_requested=True),
            full_paths=args.full_path,
        )
    exit_code = handlers.run_rollback_execution(
        snapshot=snapshot,
        actions=rollback_actions,
        json_output=args.json_output,
        full_paths=args.full_path,
    )
    if exit_code == 0:
        record_snapshot_restore(snapshot)
    return exit_code



def _handle_untrack(*, args: Any, engine: Any, handlers: CliCommandHandlers) -> int:
    _repo, binding = handlers.resolve_tracked_binding_text(
        engine,
        args.binding,
        operation="untrack",
        allow_package_owners=False,
        json_output=args.json_output,
    )
    removed_binding = engine.remove_binding(
        f"{binding.repo}:{binding.selector}@{binding.profile}",
        operation="untrack",
    )
    return handlers.emit_forgotten_binding(
        binding=removed_binding,
        still_tracked_package=handlers.find_remaining_tracked_package_after_untrack(engine, removed_binding),
        json_output=args.json_output,
    )



def _handle_info_tracked(*, args: Any, engine: Any, handlers: CliCommandHandlers) -> int:
    repo, package_id, bound_profile = handlers.resolve_tracked_package_text(
        engine,
        args.package,
        json_output=args.json_output,
    )
    package_ref = package_ref_text(package_id=package_id, bound_profile=bound_profile)
    return handlers.emit_tracked_package_detail(
        package_detail=engine.describe_tracked_package(f"{repo.config.name}:{package_ref}"),
        json_output=args.json_output,
    )
