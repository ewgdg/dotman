# Remove dead code found by static scan (#87)

## Goal and intention

Delete functions and classes in `src/dotman/` that no source code calls, so the
code no longer suggests behavior dotman does not support.

## Method

- `uvx vulture src/dotman --min-confidence 60`, excluding Textual handlers in
  `sync_deck.py`, iterated until no new findings appeared.
- Each candidate checked for callers in `src`, `tests`, `docs`, `examples` and
  for dynamic dispatch before deletion.

## Deleted

- Issue list: `edit_package_directory`, `render_tracked_issue_label`, unreachable
  `return True` after `while True`, `render_payload_hook_label`, `edit_status`,
  `run_review_item_edit`, `engine.resolve_selector_text`, `matches_ignore_pattern`,
  `filter_hook_plans_for_targets`, `build_fzf_search_fields`, `resolve_snapshot`,
  `SyncBaseLifecycle.selected_policy_resolved`, `SyncBaseStoreCorruptionError`,
  `SyncBaseStore.discard_corrupt`, `sync_deck_command.approve`, `target_paths.py`,
  `describe_owned_package_targets`, `effective_tracked_package_entry_keys`,
  `TransformOutput.as_text`/`is_binary`, `write_json_if_changed`, `write_plist`,
  `write_plist_if_changed`, TOML `write_document_if_changed`/`path_exists`/
  `strip_keys`/`merge_keys`/`merge_keys_except_stripped`, XML
  `overlay_retained_nodes`/`transform_xml`.
- Orphaned by the above: `_materialize_review_edit_paths` and its
  `_review_edit_*` helpers, `finalize_hook_plans_for_targets`,
  `executable_package_ids_for_targets`, TOML `merge_with_selector_action`.

## Kept

- `MemoryCommandRuntime`, `ScriptedInteraction`: deterministic test adapters for
  the `CommandRuntime` and `Interaction` protocols; they live beside the protocol.
- `terminal.exit_on_escape`: registered via prompt_toolkit's key binding decorator.
- `engine.resolve_full_spec_selector_text`: test-only selector resolver, kept by
  the #86 decision.
- Vulture's unused-variable findings: dataclass and class fields and `__exit__`
  arguments, not dead code.

## Test changes

- `selected_policy_resolved`: its Push/Sync ineligibility cleanup already lives
  in `sync_observation._discard_ineligible_bases`, covered by
  `tests/cli/test_push_base_maintenance.py`. Its lifecycle test was removed.
- `discard_corrupt`: store tests now discard corrupt records with `delete`, the
  path `dotman reset sync-base` uses.
- Transform file wrappers: tests compose `build_*_output` with
  `emit_transform_output` through small test-local drivers.
- `matches_ignore_pattern`: tests use `IgnoreMatcher.from_patterns`.

## Follow-ups (resolved)

- Removed pre-existing unused imports in `manifest.py`, `repository.py`,
  `snapshot.py`, `tracked_packages.py`, and the unused rendered
  `render_command` in `tracked_packages` (the summary stores the raw `render`
  name). `test_config.py` now imports `default_snapshot_root` from `dotman.config`.
- Pull and ineligible Bases: `_discard_ineligible_bases` has no operation check,
  and none is needed. Configured ineligibility is valid cleanup whichever
  operation observes it. In practice Pull filters push-only units out by
  direction before cleanup runs.
