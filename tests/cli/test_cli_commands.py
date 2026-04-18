from __future__ import annotations

from types import SimpleNamespace

from dotman.cli import build_parser
from dotman.cli_commands import CliCommandHandlers, dispatch_command


def test_parser_provides_full_path_default_for_commands_without_the_flag() -> None:
    args = build_parser().parse_args(["list", "tracked"])

    assert hasattr(args, "full_path")
    assert args.full_path is None


def test_dispatch_command_uses_parsed_full_path_default() -> None:
    recorded: dict[str, object] = {}

    def emit_tracked_packages(**kwargs) -> int:
        recorded.update(kwargs)
        return 0

    handlers = CliCommandHandlers(
        run_basic_reconcile=lambda **kwargs: 0,
        run_jinja_reconcile=lambda **kwargs: 0,
        run_jinja_render=lambda **kwargs: 0,
        run_patch_capture=lambda **kwargs: 0,
        resolve_binding_text=lambda *args, **kwargs: None,
        ensure_track_binding_replacement_confirmed=lambda **kwargs: True,
        find_recorded_bindings_for_scope=lambda **kwargs: [],
        emit_kept_binding=lambda **kwargs: 0,
        emit_skipped_tracking=lambda **kwargs: 0,
        prompt_for_conflicting_package_binding=lambda **kwargs: None,
        select_non_conflicting_track_profile=lambda **kwargs: None,
        ensure_track_binding_implicit_overrides_confirmed=lambda **kwargs: True,
        find_recorded_binding_exact=lambda **kwargs: None,
        emit_tracked_binding=lambda **kwargs: 0,
        resolve_add_package_text=lambda **kwargs: ("repo", "package"),
        interactive_mode_enabled=lambda **kwargs: False,
        add_editor_available=lambda: False,
        review_add_manifest=lambda **kwargs: None,
        confirm_add_manifest_write=lambda **kwargs: True,
        emit_add_result=lambda **kwargs: 0,
        emit_noop_add_result=lambda **kwargs: 0,
        emit_kept_add_result=lambda **kwargs: 0,
        open_editor_path=lambda **kwargs: 0,
        resolve_tracked_binding_text=lambda **kwargs: None,
        resolve_tracked_target_text=lambda **kwargs: None,
        filter_plans_for_interactive_selection=lambda **kwargs: [],
        review_plans_for_interactive_diffs=lambda **kwargs: True,
        emit_interrupt_notice=lambda: None,
        interrupted_exit_code=130,
        emit_payload=lambda **kwargs: 0,
        effective_execution_mode=lambda **kwargs: "execute",
        prepare_push_plans_for_execution=lambda **kwargs: [],
        execute_plans=lambda **kwargs: [],
        emit_execution_result=lambda **kwargs: 0,
        run_execution=lambda **kwargs: 0,
        resolve_snapshot_record=lambda **kwargs: None,
        review_rollback_actions_for_interactive_diffs=lambda **kwargs: True,
        emit_rollback_payload=lambda **kwargs: 0,
        run_rollback_execution=lambda **kwargs: 0,
        emit_forgotten_binding=lambda **kwargs: 0,
        find_remaining_tracked_package_after_untrack=lambda **kwargs: None,
        emit_tracked_packages=emit_tracked_packages,
        resolve_tracked_package_text=lambda **kwargs: None,
        emit_tracked_package_detail=lambda **kwargs: 0,
        resolve_variable_text=lambda **kwargs: None,
        emit_variables=lambda **kwargs: 0,
        emit_variable_detail=lambda **kwargs: 0,
        emit_snapshot_list=lambda **kwargs: 0,
        emit_snapshot_detail=lambda **kwargs: 0,
    )

    args = build_parser().parse_args(["list", "tracked"])
    engine = SimpleNamespace(
        config=SimpleNamespace(selection_menu=SimpleNamespace(full_paths=True)),
        list_tracked_state=lambda: SimpleNamespace(packages=[], invalid_bindings=[]),
    )

    exit_code = dispatch_command(args=args, engine_factory=lambda _: engine, handlers=handlers)

    assert exit_code == 0
    assert recorded["json_output"] is False

