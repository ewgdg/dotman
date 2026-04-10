from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence, TypeVar

from dotman.add import (
    add_editor_available,
    prepare_add_to_package,
    review_add_manifest,
    validate_package_id,
    write_add_result,
)
from dotman.diff_review import (
    ReviewItem,
    build_review_items,
    display_review_path,
    diff_status as review_diff_status,
    run_review_item_diff,
)
from dotman.engine import DotmanEngine, TrackedTargetConflictError, parse_binding_text, parse_package_ref_text
from dotman.execution import ExecutionSession, ExecutionStep, ExecutionStepResult, PackageExecutionResult, build_execution_session, execute_session
from dotman.models import Binding, HookPlan, filter_hook_plans_for_targets, package_ref_text
from dotman.reconcile import run_basic_reconcile
from dotman.reconcile_helpers import run_jinja_reconcile
from dotman.templates import build_template_context, render_template_file
from dotman.snapshot import (
    RollbackAction,
    SnapshotRecord,
    build_rollback_actions,
    create_push_snapshot,
    execute_rollback,
    find_snapshot_matches,
    list_snapshots,
    mark_snapshot_status,
    prune_snapshots,
    record_snapshot_restore,
)
from dotman.resolver import (
    ResolverOption,
    build_binding_field_kinds,
    build_binding_match_fields,
    build_fzf_search_fields,
    build_package_field_kinds,
    build_package_match_fields,
    build_profile_field_kinds,
    build_profile_match_fields,
    build_selector_field_kinds,
    build_selector_match_fields,
    parse_slash_qualified_query,
    rank_resolver_option,
)


ANSI_RESET = "\033[0m"
MENU_HEADER_MARKER = "::"
MENU_HEADER_MARKER_STYLE = ("1", "34")
MENU_INDEX_STYLE = ("1", "36")
MENU_PROMPT_STYLE = ("1",)
MENU_HINT_STYLE = ("2",)
MENU_REPO_STYLE = ("2", "34")
INTERRUPTED_EXIT_CODE = 130
MENU_SELECTION_OVERHEAD_LINES = 6
MENU_ACTION_STYLE_BY_NAME: dict[str, tuple[str, ...]] = {
    "create": ("1", "32"),
    "update": ("1", "36"),
    "delete": ("1", "31"),
}
EXECUTION_STATUS_STYLE_BY_NAME: dict[str, tuple[str, ...]] = {
    "ok": ("1", "32"),
    "failed": ("1", "31"),
    "skipped": ("1", "33"),
}
SNAPSHOT_STATUS_STYLE_BY_NAME: dict[str, tuple[str, ...]] = {
    "prepared": ("1", "33"),
    "applied": ("1", "32"),
    "failed": ("1", "31"),
}
SelectableItem = TypeVar("SelectableItem")


@dataclass(frozen=True)
class PendingSelectionItem:
    binding_label: str
    package_id: str
    target_name: str
    action: str
    source_path: str
    destination_path: str


@dataclass
class PayloadPackageSection:
    repo_name: str
    package_id: str
    profile: str
    hooks: dict[str, list[HookPlan]]
    targets: list[PendingSelectionItem]


def prompt(message: str) -> str:
    sys.stdout.write(message)
    sys.stdout.flush()
    answer = sys.stdin.readline()
    return answer.strip()


def colors_enabled() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def style_text(text: str, *codes: str) -> str:
    if not codes:
        return text
    return f"\033[{';'.join(codes)}m{text}{ANSI_RESET}"


def repo_name_from_binding_label(binding_label: str) -> str:
    return binding_label.split(":", 1)[0]


def repo_qualified_selector_text(*, repo_name: str, selector: str) -> str:
    return f"{repo_name}:{selector}"


def package_label_text(
    *,
    repo_name: str,
    package_id: str,
    bound_profile: str | None = None,
    target_name: str | None = None,
    package_first: bool = False,
    include_repo_context: bool = False,
) -> str:
    package_ref = package_ref_text(package_id=package_id, bound_profile=bound_profile)
    if package_first:
        package_text = (
            repo_qualified_selector_text(repo_name=repo_name, selector=package_ref)
            if include_repo_context
            else package_ref
        )
    else:
        package_text = repo_qualified_selector_text(repo_name=repo_name, selector=package_ref)
    if target_name is None:
        return package_text
    return f"{package_text} ({target_name})"


def render_package_label(
    *,
    repo_name: str,
    package_id: str,
    bound_profile: str | None = None,
    target_name: str | None = None,
    package_first: bool = False,
    include_repo_context: bool = False,
) -> str:
    package_ref = package_ref_text(package_id=package_id, bound_profile=bound_profile)
    if not colors_enabled():
        return package_label_text(
            repo_name=repo_name,
            package_id=package_id,
            bound_profile=bound_profile,
            target_name=target_name,
            package_first=package_first,
            include_repo_context=include_repo_context,
        )
    if package_first:
        if include_repo_context:
            package_label = (
                f"{style_text(repo_name, *MENU_REPO_STYLE)}"
                f"{style_text(':', *MENU_HINT_STYLE)}"
                f"{style_text(package_ref, '1')}"
            )
        else:
            package_label = style_text(package_ref, "1")
    else:
        package_label = (
            f"{style_text(repo_name, *MENU_REPO_STYLE)}"
            f"{style_text(':', *MENU_HINT_STYLE)}"
            f"{style_text(package_ref, '1')}"
        )
    if target_name is None:
        return package_label
    return f"{package_label} {style_text(f'({target_name})', *MENU_HINT_STYLE)}"


def render_package_target_label(*, repo_name: str, package_id: str, target_name: str) -> str:
    return render_package_label(repo_name=repo_name, package_id=package_id, target_name=target_name)


def package_profile_label_text(*, repo_name: str, package_id: str, profile: str) -> str:
    return f"{repo_qualified_selector_text(repo_name=repo_name, selector=package_id)}@{profile}"


def render_package_profile_label(*, repo_name: str, package_id: str, profile: str) -> str:
    if not colors_enabled():
        return package_profile_label_text(repo_name=repo_name, package_id=package_id, profile=profile)
    return (
        f"{style_text(repo_name, *MENU_REPO_STYLE)}"
        f"{style_text(':', *MENU_HINT_STYLE)}"
        f"{style_text(package_id, '1')}"
        f"{style_text(f'@{profile}', *MENU_HINT_STYLE)}"
    )


def binding_label_text(*, repo_name: str, selector: str, profile: str, selector_first: bool = False) -> str:
    return f"{repo_qualified_selector_text(repo_name=repo_name, selector=selector)}@{profile}"


def render_binding_label(*, repo_name: str, selector: str, profile: str, selector_first: bool = False) -> str:
    if not colors_enabled():
        return binding_label_text(
            repo_name=repo_name,
            selector=selector,
            profile=profile,
            selector_first=selector_first,
        )
    return (
        f"{style_text(repo_name, *MENU_REPO_STYLE)}"
        f"{style_text(':', *MENU_HINT_STYLE)}"
        f"{style_text(selector, '1')}"
        f"{style_text(f'@{profile}', *MENU_HINT_STYLE)}"
    )


def render_binding_reference(binding: Binding) -> str:
    return render_binding_label(
        repo_name=binding.repo,
        selector=binding.selector,
        profile=binding.profile,
    )


def find_remaining_tracked_package_after_untrack(engine: DotmanEngine, binding: Binding):
    repo = engine.get_repo(binding.repo)
    if binding.selector not in repo.packages:
        return None
    if repo.resolve_package(binding.selector).binding_mode == "multi_instance":
        return None
    try:
        return engine.describe_installed_package(f"{binding.repo}:{binding.selector}")
    except ValueError:
        return None


def render_tracked_reason(reason: str) -> str:
    if not colors_enabled():
        return reason
    return style_text(reason, *MENU_HINT_STYLE)


def render_info_section_header(label: str) -> str:
    if not colors_enabled():
        return f"  :: {label}"
    return (
        f"  {style_text('::', *MENU_HEADER_MARKER_STYLE)} "
        f"{style_text(label, '1')}"
    )


def format_snapshot_timestamp(timestamp: str | None) -> str:
    if timestamp is None:
        return "never"
    try:
        instant = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return timestamp
    return instant.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def render_snapshot_status(status: str) -> str:
    if not colors_enabled():
        return status
    return style_text(status, *SNAPSHOT_STATUS_STYLE_BY_NAME.get(status, ("1",)))


def render_snapshot_ref(snapshot_id: str) -> str:
    if not colors_enabled():
        return snapshot_id
    return style_text(snapshot_id, "1")


def render_snapshot_metadata_label(label: str) -> str:
    if not colors_enabled():
        return label
    return style_text(label, *MENU_HINT_STYLE)


def render_snapshot_provenance(*, repo_name: str | None, package_id: str | None, target_name: str | None, binding_label: str | None) -> str | None:
    if repo_name is None or package_id is None or target_name is None:
        return binding_label
    profile = None
    if binding_label is not None:
        binding_repo, _binding_selector, binding_profile = parse_binding_text(binding_label)
        if binding_repo is not None:
            repo_name = binding_repo
        profile = binding_profile
    if profile is not None:
        return render_package_profile_label(repo_name=repo_name, package_id=package_id, profile=profile) + (
            f" {style_text(f'({target_name})', *MENU_HINT_STYLE)}" if colors_enabled() else f" ({target_name})"
        )
    return render_package_target_label(repo_name=repo_name, package_id=package_id, target_name=target_name)


def render_snapshot_reason(action: str) -> str:
    if not colors_enabled():
        return f"before {action} (push)"
    return f"before {render_payload_action(action)} {style_text('(push)', *MENU_HINT_STYLE)}"


def render_selector_match_label(*, repo_name: str, selector: str, selector_kind: str) -> str:
    package_label = render_package_label(
        repo_name=repo_name,
        package_id=selector,
        package_first=True,
        include_repo_context=True,
    )
    if not colors_enabled():
        return f"{package_label} [{selector_kind}]"
    return f"{package_label} {style_text(f'[{selector_kind}]', *MENU_HINT_STYLE)}"


def print_selection_header(header_text: str) -> None:
    print()
    if not colors_enabled():
        print(header_text)
        return
    print(
        f"{style_text(MENU_HEADER_MARKER, *MENU_HEADER_MARKER_STYLE)} "
        f"{style_text(header_text, '1')}"
    )


def print_selection_item(index: int, label: str) -> None:
    if not colors_enabled():
        print(f"  {index:>2}) {label}")
        return
    print(f"  {style_text(f'{index:>2})', *MENU_INDEX_STYLE)} {label}")


def parse_selection_index(raw_answer: str, item_count: int) -> int:
    answer = raw_answer.strip()
    if not answer:
        return 1
    if not answer.isdigit():
        raise ValueError(f"unsupported selection: {answer}")
    selected_index = int(answer)
    if not 1 <= selected_index <= item_count:
        raise ValueError(f"selection index out of range: {selected_index}")
    return selected_index


def parse_selection_token(token: str, item_count: int) -> set[int]:
    if token.isdigit():
        selected_index = int(token)
        if not 1 <= selected_index <= item_count:
            raise ValueError(f"selection index out of range: {selected_index}")
        return {selected_index}
    if "-" not in token:
        raise ValueError(f"unsupported token: {token}")
    start_text, end_text = token.split("-", 1)
    if not start_text.isdigit() or not end_text.isdigit():
        raise ValueError(f"unsupported token: {token}")
    start_index = int(start_text)
    end_index = int(end_text)
    if start_index > end_index:
        raise ValueError(f"invalid range: {token}")
    if start_index < 1 or end_index > item_count:
        raise ValueError(f"selection index out of range: {token}")
    return set(range(start_index, end_index + 1))


def parse_selection_indexes(raw_answer: str, item_count: int) -> set[int]:
    answer = raw_answer.strip()
    if not answer:
        return set()
    keep_only_mode = answer.startswith("^")
    if keep_only_mode:
        answer = answer[1:].strip()
        if not answer:
            raise ValueError("missing keep-only selection after '^'")
    selected_indexes: set[int] = set()
    for token in answer.replace(",", " ").split():
        selected_indexes.update(parse_selection_token(token, item_count))
    if keep_only_mode:
        return set(range(1, item_count + 1)) - selected_indexes
    return selected_indexes


def _select_menu_option_with_prompt(*, header_text: str, option_labels: Sequence[str]) -> int:
    print_selection_header(header_text)
    indexed_labels = list(enumerate(option_labels, start=1))
    if selection_menu_bottom_up_enabled():
        indexed_labels.reverse()
    for index, option_label in indexed_labels:
        print_selection_item(index, option_label)
    while True:
        try:
            answer = prompt(selection_prompt())
            if answer.strip() == "?":
                print_selection_help()
                continue
            return parse_selection_index(answer, len(option_labels)) - 1
        except ValueError as exc:
            print(f"invalid selection: {exc}", file=sys.stderr)


def _fzf_available() -> bool:
    return shutil.which("fzf") is not None


def selection_menu_bottom_up_enabled() -> bool:
    raw_value = os.environ.get("DOTMAN_MENU_BOTTOM_UP")
    if raw_value is None:
        return True
    return raw_value.strip().lower() not in {"0", "false", "no", "off"}


def _should_use_fzf_for_selection(option_labels: Sequence[str]) -> bool:
    terminal_lines = shutil.get_terminal_size((80, 24)).lines
    return len(option_labels) > max(1, terminal_lines - MENU_SELECTION_OVERHEAD_LINES)


def _select_menu_option_with_fzf(
    *,
    header_text: str,
    option_labels: Sequence[str],
    option_search_fields: Sequence[Sequence[str]],
) -> int:
    field_count = max((len(fields) for fields in option_search_fields), default=1)
    entries = [
        "\t".join(
            [
                str(index),
                *list(fields),
                *([""] * (field_count - len(fields))),
                label,
            ]
        )
        for index, (fields, label) in enumerate(zip(option_search_fields, option_labels, strict=True), start=1)
    ]
    hidden_field_range = f"2..{field_count + 1}"
    label_field_index = str(field_count + 2)
    completed = subprocess.run(
        [
            "fzf",
            "--prompt=Select> ",
            f"--header={header_text}",
            "--delimiter=\t",
            f"--nth={hidden_field_range}",
            f"--with-nth={label_field_index}",
            "--accept-nth=1",
            "--no-sort",
            "--layout=reverse-list" if selection_menu_bottom_up_enabled() else "--layout=reverse",
        ],
        input="\n".join(entries) + "\n",
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise KeyboardInterrupt
    return parse_selection_index(completed.stdout.strip(), len(option_labels)) - 1


def select_menu_option(
    *,
    header_text: str,
    option_labels: Sequence[str],
    option_search_fields: Sequence[Sequence[str]] | None = None,
) -> int:
    search_fields = option_search_fields or [(label,) for label in option_labels]
    if _fzf_available() and _should_use_fzf_for_selection(option_labels):
        return _select_menu_option_with_fzf(
            header_text=header_text,
            option_labels=option_labels,
            option_search_fields=search_fields,
        )
    return _select_menu_option_with_prompt(header_text=header_text, option_labels=option_labels)


def selection_prompt() -> str:
    prompt_text = "Select a number"
    hint_text = '("?"; default: 1)'
    if not colors_enabled():
        return f"{prompt_text} {hint_text}: "
    return (
        f"{style_text(MENU_HEADER_MARKER, *MENU_HEADER_MARKER_STYLE)} "
        f"{style_text(prompt_text, *MENU_PROMPT_STYLE)} "
        f"{style_text(hint_text, *MENU_HINT_STYLE)}: "
    )


def pending_selection_prompt() -> str:
    prompt_text = "Exclude by number or range"
    hint_text = '("?"; e.g. "1 2 4-6" or "^3"; default: none)'
    if not colors_enabled():
        return f"\n{prompt_text} {hint_text}: "
    return (
        f"\n{style_text(MENU_HEADER_MARKER, *MENU_HEADER_MARKER_STYLE)} "
        f"{style_text(prompt_text, *MENU_PROMPT_STYLE)} "
        f"{style_text(hint_text, *MENU_HINT_STYLE)}: "
    )


def review_menu_prompt() -> str:
    prompt_text = "Review command"
    hint_text = '("?", number, "a", "c", "q"; default: continue)'
    if not colors_enabled():
        return f"\n{prompt_text} {hint_text}: "
    return (
        f"\n{style_text(MENU_HEADER_MARKER, *MENU_HEADER_MARKER_STYLE)} "
        f"{style_text(prompt_text, *MENU_PROMPT_STYLE)} "
        f"{style_text(hint_text, *MENU_HINT_STYLE)}: "
    )


def confirmation_prompt() -> str:
    prompt_text = "Confirm replacement"
    hint_text = '("y" to confirm; default: no)'
    if not colors_enabled():
        return f"{prompt_text} {hint_text}: "
    return (
        f"{style_text(MENU_HEADER_MARKER, *MENU_HEADER_MARKER_STYLE)} "
        f"{style_text(prompt_text, *MENU_PROMPT_STYLE)} "
        f"{style_text(hint_text, *MENU_HINT_STYLE)}: "
    )


def write_manifest_confirmation_prompt(*, repo_name: str, package_id: str) -> str:
    prompt_text = f"Write package config changes for {repo_name}:{package_id}?"
    hint_text = "[y/N]"
    if not colors_enabled():
        return f"{prompt_text} {hint_text} "
    return (
        f"{style_text(MENU_HEADER_MARKER, *MENU_HEADER_MARKER_STYLE)} "
        f"{style_text(prompt_text, *MENU_PROMPT_STYLE)} "
        f"{style_text(hint_text, *MENU_HINT_STYLE)} "
    )


def print_selection_help() -> None:
    print("Selection help:")
    print("  <number>  choose that item")


def print_pending_selection_help() -> None:
    print("Selection help:")
    print("  <number>       exclude one item")
    print("  <a-b>          exclude a range")
    print("  1 3 5-7        exclude multiple items or ranges")
    print("  ^<selection>   keep only the selected items")


def print_review_command_help() -> None:
    print("Review commands:")
    print("  <number>   inspect one diff")
    print("  a          inspect all diffs")
    print("  c          continue")
    print("  q          abort")
    print('  "?"        show this help')


def interactive_mode_enabled(*, json_output: bool) -> bool:
    return not json_output and sys.stdin.isatty()


def binding_replacement_scope(engine: DotmanEngine, binding: Binding) -> tuple[str, str, str | None]:
    repo = engine.get_repo(binding.repo)
    if binding.selector in repo.packages and repo.resolve_package(binding.selector).binding_mode == "multi_instance":
        return (binding.repo, binding.selector, binding.profile)
    return (binding.repo, binding.selector, None)


def find_recorded_binding_for_scope(engine: DotmanEngine, binding: Binding) -> Binding | None:
    repo = engine.get_repo(binding.repo)
    target_scope = binding_replacement_scope(engine, binding)
    for existing in engine.read_bindings(repo):
        if binding_replacement_scope(engine, existing) == target_scope:
            return existing
    return None


def find_recorded_binding_exact(engine: DotmanEngine, binding: Binding) -> Binding | None:
    repo = engine.get_repo(binding.repo)
    for existing in engine.read_bindings(repo):
        if (
            existing.repo == binding.repo
            and existing.selector == binding.selector
            and existing.profile == binding.profile
        ):
            return existing
    return None


def confirm_tracked_binding_replacement(
    *,
    existing_binding: Binding,
    replacement_binding: Binding,
) -> bool:
    binding_scope = f"{replacement_binding.repo}:{replacement_binding.selector}"
    print_selection_header(f"Confirm tracked binding replacement for {binding_scope}:")
    print(
        "  existing: "
        + render_binding_label(
            repo_name=existing_binding.repo,
            selector=existing_binding.selector,
            profile=existing_binding.profile,
        )
    )
    print(
        "  new:      "
        + render_binding_label(
            repo_name=replacement_binding.repo,
            selector=replacement_binding.selector,
            profile=replacement_binding.profile,
        )
    )
    while True:
        answer = prompt(confirmation_prompt()).strip().lower()
        if answer in {"", "n", "no"}:
            return False
        if answer in {"y", "yes"}:
            return True
        print("invalid confirmation: enter 'y' or 'n'", file=sys.stderr)


def ensure_track_binding_replacement_confirmed(
    engine: DotmanEngine,
    *,
    binding: Binding,
    json_output: bool,
) -> bool:
    existing_binding = find_recorded_binding_for_scope(engine, binding)
    if existing_binding is None or existing_binding.profile == binding.profile:
        return True
    if not interactive_mode_enabled(json_output=json_output):
        raise ValueError(
            f"refusing to replace tracked binding '{existing_binding.repo}:{existing_binding.selector}@"
            f"{existing_binding.profile}' with '{binding.repo}:{binding.selector}@{binding.profile}' "
            "in non-interactive mode"
        )
    return confirm_tracked_binding_replacement(
        existing_binding=existing_binding,
        replacement_binding=binding,
    )


def confirm_track_binding_implicit_overrides(*, binding: Binding, overrides: Sequence) -> bool:
    binding_label = f"{binding.repo}:{binding.selector}@{binding.profile}"
    print_selection_header(f"Confirm explicit override for {binding_label}:")
    print("  this explicit binding will replace implicitly tracked package owners:")
    for override in overrides:
        print(f"    new: {override.winner.binding_label} ({override.winner.package_id})")
        for contender in override.overridden:
            print(f"      implicit: {contender.binding_label} ({contender.package_id})")
    while True:
        answer = prompt(confirmation_prompt()).strip().lower()
        if answer in {"", "n", "no"}:
            return False
        if answer in {"y", "yes"}:
            return True
        print("invalid confirmation: enter 'y' or 'n'", file=sys.stderr)


def ensure_track_binding_implicit_overrides_confirmed(
    engine: DotmanEngine,
    *,
    binding: Binding,
    json_output: bool,
) -> bool:
    overrides = engine.preview_binding_implicit_overrides(binding)
    if not overrides:
        return True
    if not interactive_mode_enabled(json_output=json_output):
        raise ValueError(
            f"refusing to let '{binding.repo}:{binding.selector}@{binding.profile}' explicitly override implicitly tracked targets "
            "in non-interactive mode"
        )
    return confirm_track_binding_implicit_overrides(binding=binding, overrides=overrides)


def confirm_add_manifest_write(*, repo_name: str, package_id: str) -> bool:
    while True:
        answer = prompt(
            write_manifest_confirmation_prompt(repo_name=repo_name, package_id=package_id)
        ).strip().lower()
        if answer in {"", "n", "no"}:
            return False
        if answer in {"y", "yes"}:
            return True
        print("invalid confirmation: enter 'y' or 'n'", file=sys.stderr)


def prompt_for_conflicting_package_binding(
    *,
    binding: Binding,
    conflict: TrackedTargetConflictError,
    json_output: bool,
) -> Binding | None:
    if conflict.precedence != "implicit" or not interactive_mode_enabled(json_output=json_output):
        return None
    package_ids = sorted(
        {
            candidate.package_id
            for candidate in conflict.candidates
            if candidate.binding == binding
        }
    )
    if not package_ids:
        return None
    binding_label = f"{binding.repo}:{binding.selector}@{binding.profile}"
    if len(package_ids) == 1:
        package_id = package_ids[0]
        promoted_binding = Binding(repo=binding.repo, selector=package_id, profile=binding.profile)
        print_selection_header(f"Resolve implicit conflict for {binding_label}:")
        print(f"  target path: {conflict.live_path}")
        print(f"  requested: {binding_label}")
        print(f"  promote:   {promoted_binding.repo}:{promoted_binding.selector}@{promoted_binding.profile}")
        print("  explicit tracking can break the implicit tie for this package.")
        while True:
            answer = prompt(confirmation_prompt()).strip().lower()
            if answer in {"", "n", "no"}:
                return None
            if answer in {"y", "yes"}:
                return promoted_binding
            print("invalid confirmation: enter 'y' or 'n'", file=sys.stderr)

    selected_index = select_menu_option(
        header_text=f"Select a conflicting package to track explicitly for {binding_label}:",
        option_labels=package_ids,
        option_search_fields=[(package_id,) for package_id in package_ids],
    )
    return Binding(repo=binding.repo, selector=package_ids[selected_index], profile=binding.profile)


def parse_review_command(raw_answer: str, item_count: int) -> tuple[str, int | None]:
    answer = raw_answer.strip().lower()
    if not answer or answer == "c":
        return "continue", None
    if answer == "?":
        return "help", None
    if answer == "a":
        return "all", None
    if answer == "q":
        return "abort", None
    if answer.isdigit():
        selected_index = parse_selection_index(answer, item_count)
        return "inspect", selected_index - 1
    raise ValueError(f"unsupported review command: {raw_answer.strip()}")


def resolve_candidate_match(
    *,
    exact_matches: Sequence[SelectableItem],
    partial_matches: Sequence[SelectableItem],
    query_text: str,
    interactive: bool,
    exact_header_text: str,
    partial_header_text: str,
    option_resolver: Callable[[SelectableItem], ResolverOption],
    exact_error_text: str,
    partial_error_text: str,
    not_found_text: str,
) -> SelectableItem:
    ranked_exact_matches = sorted(
        exact_matches,
        key=lambda match: rank_resolver_option(
            query=query_text,
            option=option_resolver(match),
        ),
    )
    ranked_partial_matches = sorted(
        partial_matches,
        key=lambda match: rank_resolver_option(
            query=query_text,
            option=option_resolver(match),
        ),
    )
    if len(exact_matches) == 1:
        return ranked_exact_matches[0]
    if len(exact_matches) > 1:
        if not interactive:
            raise ValueError(exact_error_text)
        selected_index = select_menu_option(
            header_text=exact_header_text,
            option_labels=[option_resolver(match).display_label for match in ranked_exact_matches],
            option_search_fields=[
                build_fzf_search_fields(match_fields=option_resolver(match).match_fields)
                for match in ranked_exact_matches
            ],
        )
        return ranked_exact_matches[selected_index]
    if len(partial_matches) == 1:
        return ranked_partial_matches[0]
    if len(partial_matches) > 1:
        if not interactive:
            raise ValueError(partial_error_text)
        selected_index = select_menu_option(
            header_text=partial_header_text,
            option_labels=[option_resolver(match).display_label for match in ranked_partial_matches],
            option_search_fields=[
                build_fzf_search_fields(match_fields=option_resolver(match).match_fields)
                for match in ranked_partial_matches
            ],
        )
        return ranked_partial_matches[selected_index]
    raise ValueError(not_found_text)


def resolve_binding_text(
    engine: DotmanEngine,
    binding_text: str,
    *,
    json_output: bool,
) -> tuple[str, str]:
    explicit_repo, selector, selector_profile = parse_binding_text(binding_text)
    repo_names = [repo_config.name for repo_config in engine.config.ordered_repos]
    lookup_repo, lookup_selector = parse_slash_qualified_query(
        repo_names=repo_names,
        explicit_repo=explicit_repo,
        selector=selector,
    )
    exact_matches, partial_matches = engine.find_selector_matches(lookup_selector, lookup_repo)
    interactive = interactive_mode_enabled(json_output=json_output)
    repo, resolved_selector, _selector_kind = resolve_candidate_match(
        exact_matches=exact_matches,
        partial_matches=partial_matches,
        query_text=selector,
        interactive=interactive,
        exact_header_text=f"Select a repo for exact selector '{selector}':",
        partial_header_text=f"Select a selector match for '{selector}':",
        option_resolver=lambda match: ResolverOption(
            display_label=render_selector_match_label(
                repo_name=match[0].config.name,
                selector=match[1],
                selector_kind=match[2],
            ),
            match_fields=build_selector_match_fields(
                repo_name=match[0].config.name,
                selector=match[1],
            ),
            field_kinds=build_selector_field_kinds(),
        ),
        exact_error_text=f"selector '{selector}' is defined in multiple repos: "
        + ", ".join(f"{repo.config.name}:{match}" for repo, match, _ in exact_matches),
        partial_error_text=f"selector '{selector}' is ambiguous: "
        + ", ".join(f"{repo.config.name}:{match}" for repo, match, _ in partial_matches),
        not_found_text=f"selector '{selector}' did not match any package or group",
    )

    available_profiles = engine.list_profiles(repo.config.name)
    if not available_profiles:
        raise ValueError(f"repo '{repo.config.name}' does not define any profiles")

    resolved_profile = selector_profile
    if resolved_profile:
        exact_profile_matches = [profile_name for profile_name in available_profiles if profile_name == resolved_profile]
        partial_profile_matches = [
            profile_name for profile_name in available_profiles if resolved_profile in profile_name
        ]
        profile_selection_matches = partial_profile_matches
        profile_selection_header = (
            f"Select a profile match for '{resolved_profile}' in {repo.config.name}:{resolved_selector}:"
        )
        if interactive and not exact_profile_matches and not partial_profile_matches:
            profile_selection_matches = list(available_profiles)
            profile_selection_header = f"Select a profile for {repo.config.name}:{resolved_selector}:"
        resolved_profile = resolve_candidate_match(
            exact_matches=exact_profile_matches,
            partial_matches=profile_selection_matches,
            query_text=resolved_profile,
            interactive=interactive,
            exact_header_text=f"Select a profile for {repo.config.name}:{resolved_selector}:",
            partial_header_text=profile_selection_header,
            option_resolver=lambda profile_name: ResolverOption(
                display_label=profile_name,
                match_fields=build_profile_match_fields(profile=profile_name),
                field_kinds=build_profile_field_kinds(),
            ),
            exact_error_text=f"profile '{resolved_profile}' is defined multiple times in repo '{repo.config.name}'",
            partial_error_text=f"profile '{resolved_profile}' is ambiguous in repo '{repo.config.name}': "
            + ", ".join(partial_profile_matches),
            not_found_text=f"profile '{resolved_profile}' did not match any profile in repo '{repo.config.name}'",
        )
    elif len(available_profiles) == 1:
        resolved_profile = available_profiles[0]
    elif interactive:
        selected_index = select_menu_option(
            header_text=f"Select a profile for {repo.config.name}:{resolved_selector}:",
            option_labels=list(available_profiles),
        )
        resolved_profile = available_profiles[selected_index]
    else:
        raise ValueError("profile is required in non-interactive mode")

    return f"{repo.config.name}:{resolved_selector}", resolved_profile


def resolve_tracked_binding_text(
    engine: DotmanEngine,
    binding_text: str,
    *,
    operation: str,
    allow_package_owners: bool,
    json_output: bool,
) -> tuple[object, Binding]:
    explicit_repo, selector, profile = parse_binding_text(binding_text)
    repo_names = [repo_config.name for repo_config in engine.config.ordered_repos]
    lookup_repo, lookup_selector = parse_slash_qualified_query(
        repo_names=repo_names,
        explicit_repo=explicit_repo,
        selector=selector,
    )
    lookup_binding_text = (
        f"{lookup_repo}:{lookup_selector}" if lookup_repo is not None else lookup_selector
    )
    if profile is not None:
        lookup_binding_text = f"{lookup_binding_text}@{profile}"
    resolved_selector, resolved_profile, exact_matches, partial_matches, owner_bindings = (
        engine.find_tracked_binding_matches(lookup_binding_text)
    )
    interactive = interactive_mode_enabled(json_output=json_output)
    binding_label = selector if profile is None else f"{selector}@{profile}"
    binding_resolver = lambda match: ResolverOption(
        display_label=render_binding_label(
            repo_name=match[0].config.name,
            selector=match[1].selector,
            profile=match[1].profile,
            selector_first=True,
        ),
        match_fields=build_binding_match_fields(
            repo_name=match[0].config.name,
            selector=match[1].selector,
            profile=match[1].profile,
        ),
        field_kinds=build_binding_field_kinds(),
    )

    try:
        return resolve_candidate_match(
            exact_matches=exact_matches,
            partial_matches=partial_matches,
            query_text=binding_label,
            interactive=interactive,
            exact_header_text=f"Select a tracked binding for '{binding_label}':",
            partial_header_text=f"Select a tracked binding for '{binding_label}':",
            option_resolver=binding_resolver,
            exact_error_text=f"binding '{binding_label}' is ambiguous: "
            + ", ".join(f"{repo.config.name}:{binding.selector}@{binding.profile}" for repo, binding in exact_matches),
            partial_error_text=f"binding '{binding_label}' is ambiguous: "
            + ", ".join(f"{repo.config.name}:{binding.selector}@{binding.profile}" for repo, binding in partial_matches),
            not_found_text=f"binding '{binding_label}' is not currently tracked",
        )
    except ValueError as exc:
        if allow_package_owners and owner_bindings:
            if len(owner_bindings) == 1:
                owner_repo, owner_binding = owner_bindings[0]
            elif interactive:
                owner_repo, owner_binding = resolve_candidate_match(
                    exact_matches=[],
                    partial_matches=owner_bindings,
                    query_text=binding_label,
                    interactive=interactive,
                    exact_header_text=f"Select a tracked binding for '{binding_label}':",
                    partial_header_text=f"Select a tracked binding for '{binding_label}':",
                    option_resolver=binding_resolver,
                    exact_error_text="unused",
                    partial_error_text=f"{operation} target '{binding_label}' is ambiguous across tracked bindings: "
                    + ", ".join(
                        f"{repo.config.name}:{binding.selector}@{binding.profile}"
                        for repo, binding in owner_bindings
                    ),
                    not_found_text="unused",
                )
            else:
                candidates = ", ".join(
                    f"{repo.config.name}:{binding.selector}@{binding.profile}"
                    for repo, binding in owner_bindings
                )
                raise ValueError(f"{operation} target '{binding_label}' is ambiguous across tracked bindings: {candidates}") from None
            return owner_repo, Binding(
                repo=owner_repo.config.name,
                selector=resolved_selector,
                profile=owner_binding.profile,
            )
        if owner_bindings and not allow_package_owners:
            owners = ", ".join(
                f"{repo.config.name}:{binding.selector}@{binding.profile}"
                for repo, binding in owner_bindings
            )
            repo_name, _selector, _profile = parse_binding_text(binding_text)
            required_repo = repo_name or lookup_repo or owner_bindings[0][0].config.name
            required_ref = f"{required_repo}:{resolved_selector}"
            raise ValueError(
                f"cannot {operation} '{required_ref}': required by tracked bindings: {owners}"
            ) from None
        raise exc


def resolve_tracked_package_text(
    engine: DotmanEngine,
    package_text: str,
    *,
    json_output: bool,
) -> tuple[object, str, str | None]:
    explicit_repo, selector, bound_profile = parse_package_ref_text(package_text)
    package_query = package_ref_text(package_id=selector, bound_profile=bound_profile)
    repo_names = [repo_config.name for repo_config in engine.config.ordered_repos]
    lookup_repo, lookup_selector = parse_slash_qualified_query(
        repo_names=repo_names,
        explicit_repo=explicit_repo,
        selector=selector,
    )
    lookup_package_ref = package_ref_text(package_id=lookup_selector, bound_profile=bound_profile)
    lookup_package_text = f"{lookup_repo}:{lookup_package_ref}" if lookup_repo is not None else lookup_package_ref
    selector, bound_profile, exact_matches, partial_matches = engine.find_installed_package_matches(lookup_package_text)
    return resolve_candidate_match(
        exact_matches=exact_matches,
        partial_matches=partial_matches,
        query_text=package_query,
        interactive=interactive_mode_enabled(json_output=json_output),
        exact_header_text=f"Select a tracked package for '{package_query}':",
        partial_header_text=f"Select a tracked package for '{package_query}':",
        option_resolver=lambda match: ResolverOption(
            display_label=render_package_label(
                repo_name=match[0].config.name,
                package_id=match[1],
                bound_profile=match[2],
                package_first=True,
                include_repo_context=True,
            ),
            match_fields=build_package_match_fields(
                repo_name=match[0].config.name,
                package_id=match[1],
                bound_profile=match[2],
            ),
            field_kinds=build_package_field_kinds(has_bound_profile=match[2] is not None),
        ),
        exact_error_text=(
            (
                f"tracked package '{package_query}' is defined in multiple repos: "
                if len({repo.config.name for repo, _package_id, _match_bound_profile in exact_matches}) > 1
                else f"tracked package '{package_query}' is ambiguous: "
            )
            + ", ".join(
                f"{repo.config.name}:{package_ref_text(package_id=package_id, bound_profile=match_bound_profile)}"
                for repo, package_id, match_bound_profile in exact_matches
            )
        ),
        partial_error_text=f"tracked package '{package_query}' is ambiguous: "
        + ", ".join(
            f"{repo.config.name}:{package_ref_text(package_id=package_id, bound_profile=match_bound_profile)}"
            for repo, package_id, match_bound_profile in partial_matches
        ),
        not_found_text=f"tracked package '{package_query}' did not match any tracked package",
    )


def parse_add_package_query(
    engine: DotmanEngine,
    package_query: str,
) -> tuple[str | None, str]:
    explicit_repo, selector, profile = parse_binding_text(package_query)
    if profile is not None:
        raise ValueError("add package query expects a package selector, not a binding")
    repo_names = [repo_config.name for repo_config in engine.config.ordered_repos]
    lookup_repo, lookup_selector = parse_slash_qualified_query(
        repo_names=repo_names,
        explicit_repo=explicit_repo,
        selector=selector,
    )
    return lookup_repo, lookup_selector


def _query_fragment_rank(query: str | None, text: str) -> tuple[int, int, int]:
    if query is None or not query.strip():
        return (0, 0, len(text))
    normalized_query = query.strip().lower()
    normalized_text = text.lower()
    if normalized_text == normalized_query:
        return (0, 0, len(normalized_text))
    if normalized_text.startswith(normalized_query):
        return (1, 0, len(normalized_text))
    match_index = normalized_text.find(normalized_query)
    if match_index == -1:
        return (9, 999, len(normalized_text))
    return (2, match_index, len(normalized_text))


def rank_add_package_candidate(match: tuple[object, str], *, repo_query: str | None, package_query: str | None) -> tuple[int, int, int, int, int, int, str, str]:
    repo, package_id = match
    repo_rank = _query_fragment_rank(repo_query, repo.config.name)
    package_rank = _query_fragment_rank(package_query, package_id)
    return (*repo_rank, *package_rank, repo.config.name.lower(), package_id.lower())


def find_add_package_matches(
    engine: DotmanEngine,
    package_query: str,
) -> tuple[str | None, str, list[tuple[object, str]], list[tuple[object, str]]]:
    repo_query, package_fragment = parse_add_package_query(engine, package_query)
    exact_matches: list[tuple[object, str]] = []
    partial_matches: list[tuple[object, str]] = []
    normalized_repo_query = None if repo_query is None else repo_query.lower()
    normalized_package_query = package_fragment.lower()

    for repo_config in engine.config.ordered_repos:
        repo = engine.get_repo(repo_config.name)
        repo_name = repo.config.name
        repo_matches_exact = repo_query is None or repo_name == repo_query
        repo_matches_partial = repo_query is None or normalized_repo_query in repo_name.lower()
        if not repo_matches_partial:
            continue
        for package_id in repo.packages:
            normalized_package_id = package_id.lower()
            if repo_matches_exact and package_id == package_fragment:
                exact_matches.append((repo, package_id))
                continue
            if normalized_package_query in normalized_package_id:
                partial_matches.append((repo, package_id))

    unique_partials = {
        (repo.config.name, package_id): (repo, package_id)
        for repo, package_id in partial_matches
        if (repo, package_id) not in exact_matches
    }
    return repo_query, package_fragment, exact_matches, list(unique_partials.values())


def create_add_option_label(package_query: str | None) -> str:
    return "create a new package"


def prompt_for_new_package_id(*, default_package_id: str | None) -> str:
    while True:
        prompt_text = "Package ID"
        if default_package_id:
            prompt_text += f" [{default_package_id}]"
        package_id = prompt(f"{prompt_text}: ").strip()
        if not package_id:
            package_id = default_package_id or ""
        try:
            validate_package_id(package_id)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            continue
        return package_id


def prompt_for_add_repo_name(engine: DotmanEngine, *, repo_query: str | None) -> str:
    if repo_query is not None and repo_query in engine.config.repos:
        return repo_query
    matching_repos = [
        repo_config.name
        for repo_config in engine.config.ordered_repos
        if repo_query is None or repo_query.lower() in repo_config.name.lower()
    ]
    repo_names = matching_repos or [repo_config.name for repo_config in engine.config.ordered_repos]
    selected_index = select_menu_option(
        header_text="Select a repo for the new package:",
        option_labels=repo_names,
        option_search_fields=[(repo_name,) for repo_name in repo_names],
    )
    return repo_names[selected_index]


def resolve_add_package_text(
    engine: DotmanEngine,
    package_query: str | None,
    *,
    json_output: bool,
) -> tuple[str, str]:
    interactive = interactive_mode_enabled(json_output=json_output)
    if package_query is None:
        if not interactive:
            raise ValueError("package query is required in non-interactive mode")
        package_matches = [
            (engine.get_repo(repo_config.name), package_id)
            for repo_config in engine.config.ordered_repos
            for package_id in sorted(engine.get_repo(repo_config.name).packages)
        ]
        option_labels = [create_add_option_label(None)] + [
            render_package_label(
                repo_name=repo.config.name,
                package_id=package_id,
                package_first=True,
                include_repo_context=True,
            )
            for repo, package_id in package_matches
        ]
        option_search_fields = [("create", "new", "package")] + [
            build_fzf_search_fields(
                match_fields=build_package_match_fields(
                    repo_name=repo.config.name,
                    package_id=package_id,
                )
            )
            for repo, package_id in package_matches
        ]
        selected_index = select_menu_option(
            header_text="Select a package for add:",
            option_labels=option_labels,
            option_search_fields=option_search_fields,
        )
        if selected_index == 0:
            return (
                prompt_for_add_repo_name(engine, repo_query=None),
                prompt_for_new_package_id(default_package_id=None),
            )
        selected_repo, selected_package = package_matches[selected_index - 1]
        return selected_repo.config.name, selected_package

    repo_query, package_fragment, exact_matches, partial_matches = find_add_package_matches(engine, package_query)
    ranked_exact_matches = sorted(
        exact_matches,
        key=lambda match: rank_add_package_candidate(match, repo_query=repo_query, package_query=package_fragment),
    )
    ranked_partial_matches = sorted(
        partial_matches,
        key=lambda match: rank_add_package_candidate(match, repo_query=repo_query, package_query=package_fragment),
    )

    if len(ranked_exact_matches) == 1:
        selected_repo, selected_package = ranked_exact_matches[0]
        return selected_repo.config.name, selected_package

    if interactive:
        menu_matches = ranked_exact_matches or ranked_partial_matches
        option_labels = [create_add_option_label(package_query)] + [
            render_package_label(
                repo_name=repo.config.name,
                package_id=package_id,
                package_first=True,
                include_repo_context=True,
            )
            for repo, package_id in menu_matches
        ]
        option_search_fields = [("create", package_query)] + [
            build_fzf_search_fields(
                match_fields=build_package_match_fields(
                    repo_name=repo.config.name,
                    package_id=package_id,
                )
            )
            for repo, package_id in menu_matches
        ]
        selected_index = select_menu_option(
            header_text=f"Select a package for '{package_query}':",
            option_labels=option_labels,
            option_search_fields=option_search_fields,
        )
        if selected_index == 0:
            return (
                prompt_for_add_repo_name(engine, repo_query=repo_query),
                prompt_for_new_package_id(default_package_id=package_fragment),
            )
        selected_repo, selected_package = menu_matches[selected_index - 1]
        return selected_repo.config.name, selected_package

    if len(ranked_exact_matches) > 1:
        raise ValueError(
            f"package '{package_query}' is ambiguous: "
            + ", ".join(f"{repo.config.name}:{package_id}" for repo, package_id in ranked_exact_matches)
        )
    if len(ranked_partial_matches) == 1:
        selected_repo, selected_package = ranked_partial_matches[0]
        return selected_repo.config.name, selected_package
    if len(ranked_partial_matches) > 1:
        raise ValueError(
            f"package '{package_query}' is ambiguous: "
            + ", ".join(f"{repo.config.name}:{package_id}" for repo, package_id in ranked_partial_matches)
        )
    if repo_query is None:
        raise ValueError(
            f"package '{package_query}' did not match any package; use an explicit repo-qualified query to create one in non-interactive mode"
        )
    if repo_query not in engine.config.repos:
        raise ValueError(
            f"package '{package_query}' did not match any package and cannot create non-interactively without an exact repo"
        )
    validate_package_id(package_fragment)
    return repo_query, package_fragment


def select_non_conflicting_track_profile(
    engine: DotmanEngine,
    *,
    binding_text: str,
    current_profile: str,
    json_output: bool,
) -> str | None:
    if not interactive_mode_enabled(json_output=json_output):
        return None
    repo_name, _selector, _profile = parse_binding_text(binding_text)
    if repo_name is None:
        return None
    valid_profiles: list[str] = []
    for candidate_profile in engine.list_profiles(repo_name):
        if candidate_profile == current_profile:
            continue
        _repo, candidate_binding, _selector_kind = engine.resolve_binding(binding_text, profile=candidate_profile)
        try:
            engine.validate_recorded_binding(candidate_binding)
        except ValueError:
            continue
        valid_profiles.append(candidate_profile)
    if not valid_profiles:
        return None
    selected_index = select_menu_option(
        header_text=f"Select a non-conflicting profile for {binding_text}:",
        option_labels=valid_profiles,
    )
    return valid_profiles[selected_index]


def collect_pending_selection_items(plans: Sequence) -> list[PendingSelectionItem]:
    return collect_pending_selection_items_for_operation(plans, operation="push")


def selection_item_paths(*, operation: str, repo_path: Path | str, live_path: Path | str) -> tuple[str, str]:
    repo_text = str(repo_path)
    live_text = str(live_path)
    if operation == "pull":
        return live_text, repo_text
    return repo_text, live_text


def selection_item_action(*, operation: str, action: str) -> str:
    return action


def selection_item_identity(
    *,
    binding_label: str,
    package_id: str,
    target_name: str,
    operation: str,
    repo_path: Path | str,
    live_path: Path | str,
) -> tuple[str, str, str, str, str]:
    source_path, destination_path = selection_item_paths(
        operation=operation,
        repo_path=repo_path,
        live_path=live_path,
    )
    return (
        binding_label,
        package_id,
        target_name,
        source_path,
        destination_path,
    )


def collect_pending_selection_items_for_operation(plans: Sequence, *, operation: str) -> list[PendingSelectionItem]:
    selection_items: list[PendingSelectionItem] = []
    for plan in plans:
        binding_label = f"{plan.binding.repo}:{plan.binding.selector}@{plan.binding.profile}"
        for target in plan.target_plans:
            if target.directory_items:
                for item in target.directory_items:
                    source_path, destination_path = selection_item_paths(
                        operation=operation,
                        repo_path=item.repo_path,
                        live_path=item.live_path,
                    )
                    selection_items.append(
                        PendingSelectionItem(
                            binding_label=binding_label,
                            package_id=target.package_id,
                            target_name=target.target_name,
                            action=selection_item_action(operation=operation, action=item.action),
                            source_path=source_path,
                            destination_path=destination_path,
                        )
                    )
                continue
            if target.action == "noop":
                continue
            source_path, destination_path = selection_item_paths(
                operation=operation,
                repo_path=target.repo_path,
                live_path=target.live_path,
            )
            selection_items.append(
                PendingSelectionItem(
                    binding_label=binding_label,
                    package_id=target.package_id,
                    target_name=target.target_name,
                    action=selection_item_action(operation=operation, action=target.action),
                    source_path=source_path,
                    destination_path=destination_path,
                )
            )
    return selection_items


def print_pending_selection_item(index: int, item: PendingSelectionItem, *, full_paths: bool = False) -> None:
    repo_name = repo_name_from_binding_label(item.binding_label)
    package_target = package_label_text(
        repo_name=repo_name,
        package_id=item.package_id,
        target_name=item.target_name,
    )
    source_path = display_cli_path(item.source_path, full_paths=full_paths)
    destination_path = display_cli_path(item.destination_path, full_paths=full_paths)
    if not colors_enabled():
        item_text = (
            f"[{item.action}] {package_target}: "
            f"{source_path} -> {destination_path}"
        )
        print(f"  {index:>2}) {item_text}")
        return

    action_style = MENU_ACTION_STYLE_BY_NAME.get(item.action, ("1",))
    action_text = style_text(f"[{item.action}]", *action_style)
    package_label = render_package_target_label(
        repo_name=repo_name,
        package_id=item.package_id,
        target_name=item.target_name,
    )
    arrow_text = style_text("->", *MENU_HINT_STYLE)
    print(
        f"  {style_text(f'{index:>2})', *MENU_INDEX_STYLE)} "
        f"{action_text} {package_label}: {source_path} {arrow_text} {destination_path}"
    )


def prompt_for_excluded_items(
    selection_items: Sequence[PendingSelectionItem],
    *,
    operation: str,
    full_paths: bool = False,
) -> set[int]:
    print_selection_header(f"Select items to exclude from {operation}:")
    for index, item in enumerate(selection_items, start=1):
        print_pending_selection_item(index, item, full_paths=full_paths)
    while True:
        try:
            answer = prompt(pending_selection_prompt())
            if answer.strip() == "?":
                print_pending_selection_help()
                continue
            return parse_selection_indexes(answer, len(selection_items))
        except ValueError as exc:
            print(f"invalid selection: {exc}", file=sys.stderr)


def filter_plans_for_interactive_selection(
    *,
    plans: Sequence,
    operation: str,
    json_output: bool,
    full_paths: bool = False,
) -> list:
    if not interactive_mode_enabled(json_output=json_output):
        return list(plans)
    selection_items = collect_pending_selection_items_for_operation(plans, operation=operation)
    if not selection_items:
        return list(plans)
    excluded_indexes = prompt_for_excluded_items(
        selection_items,
        operation=operation,
        full_paths=full_paths,
    )
    if not excluded_indexes:
        return list(plans)

    excluded_targets: set[tuple[str, str, str, str, str]] = set()
    for excluded_index in excluded_indexes:
        item = selection_items[excluded_index - 1]
        excluded_targets.add(
            (
                item.binding_label,
                item.package_id,
                item.target_name,
                item.source_path,
                item.destination_path,
            )
        )

    filtered_plans = []
    for plan in plans:
        binding_label = f"{plan.binding.repo}:{plan.binding.selector}@{plan.binding.profile}"
        filtered_targets = []
        for target in plan.target_plans:
            if target.directory_items:
                remaining_items = tuple(
                    item
                    for item in target.directory_items
                    if selection_item_identity(
                        binding_label=binding_label,
                        package_id=target.package_id,
                        target_name=target.target_name,
                        operation=operation,
                        repo_path=item.repo_path,
                        live_path=item.live_path,
                    )
                    not in excluded_targets
                )
                if remaining_items:
                    filtered_targets.append(replace(target, directory_items=remaining_items))
                else:
                    filtered_targets.append(replace(target, action="noop", directory_items=()))
                continue
            if selection_item_identity(
                binding_label=binding_label,
                package_id=target.package_id,
                target_name=target.target_name,
                operation=operation,
                repo_path=target.repo_path,
                live_path=target.live_path,
            ) not in excluded_targets:
                filtered_targets.append(target)
        filtered_plans.append(
            replace(
                plan,
                hooks=filter_hook_plans_for_targets(plan.hooks, filtered_targets),
                target_plans=filtered_targets,
            )
        )
    return filtered_plans


def print_review_item(index: int, item: ReviewItem, *, full_paths: bool = False) -> None:
    repo_name = repo_name_from_binding_label(item.binding_label)
    package_target = package_label_text(
        repo_name=repo_name,
        package_id=item.package_id,
        target_name=item.target_name,
    )
    diff_text = review_diff_status(item)
    source_path = display_cli_path(item.source_path, full_paths=full_paths)
    destination_path = display_cli_path(item.destination_path, full_paths=full_paths)
    if not colors_enabled():
        item_text = (
            f"[{item.action}] {package_target} "
            f"[{diff_text}]: {source_path} -> {destination_path}"
        )
        print(f"  {index:>2}) {item_text}")
        return

    action_style = MENU_ACTION_STYLE_BY_NAME.get(item.action, ("1",))
    action_text = style_text(f"[{item.action}]", *action_style)
    package_label = render_package_target_label(
        repo_name=repo_name,
        package_id=item.package_id,
        target_name=item.target_name,
    )
    status_label = style_text(f"[{diff_text}]", *MENU_HINT_STYLE)
    arrow_text = style_text("->", *MENU_HINT_STYLE)
    print(
        f"  {style_text(f'{index:>2})', *MENU_INDEX_STYLE)} "
        f"{action_text} {package_label} {status_label}: "
        f"{source_path} {arrow_text} {destination_path}"
    )


def review_diff_header(review_item: ReviewItem, *, index: int, total: int) -> str:
    return (
        f"Diff {index}/{total}: "
        f"{package_label_text(
            repo_name=repo_name_from_binding_label(review_item.binding_label),
            package_id=review_item.package_id,
            target_name=review_item.target_name,
        )} "
        f"[{review_item.action}]"
    )


def print_review_diff_header(review_item: ReviewItem, *, index: int, total: int) -> None:
    header_text = review_diff_header(review_item, index=index, total=total)
    separator = "-" * 5
    if not colors_enabled():
        print()
        print(f"{separator} {header_text} {separator}")
        return
    repo_name = repo_name_from_binding_label(review_item.binding_label)
    prefix_text = style_text(f"Diff {index}/{total}:", "1")
    package_label = render_package_target_label(
        repo_name=repo_name,
        package_id=review_item.package_id,
        target_name=review_item.target_name,
    )
    action_text = style_text(f"[{review_item.action}]", *MENU_ACTION_STYLE_BY_NAME.get(review_item.action, ("1",)))
    print()
    print(
        f"{style_text(separator, *MENU_HINT_STYLE)} "
        f"{prefix_text} {package_label} {action_text} "
        f"{style_text(separator, *MENU_HINT_STYLE)}"
    )


def review_diff_footer(*, index: int, total: int) -> str:
    return f"End Diff {index}/{total}"


def print_review_diff_footer(*, index: int, total: int) -> None:
    footer_text = review_diff_footer(index=index, total=total)
    separator = "-" * 5
    if not colors_enabled():
        print(f"{separator} {footer_text} {separator}")
        return
    print(
        f"{style_text(separator, *MENU_HINT_STYLE)} "
        f"{style_text(footer_text, *MENU_HINT_STYLE)} "
        f"{style_text(separator, *MENU_HINT_STYLE)}"
    )


def run_diff_review_menu(
    review_items: Sequence[ReviewItem],
    *,
    operation: str,
    full_paths: bool = False,
) -> bool:
    print_selection_header(f"Review pending diffs for {operation}:")
    for index, item in enumerate(review_items, start=1):
        print_review_item(index, item, full_paths=full_paths)
    while True:
        try:
            command_name, selected_index = parse_review_command(prompt(review_menu_prompt()), len(review_items))
        except ValueError as exc:
            print(f"invalid selection: {exc}", file=sys.stderr)
            continue

        if command_name == "help":
            print_review_command_help()
            continue
        if command_name == "continue":
            return True
        if command_name == "abort":
            return False
        if command_name == "all":
            for item_index, item in enumerate(review_items, start=1):
                try:
                    print_review_diff_header(item, index=item_index, total=len(review_items))
                    run_review_item_diff(item)
                    print_review_diff_footer(index=item_index, total=len(review_items))
                except ValueError as exc:
                    print(f"review unavailable: {exc}", file=sys.stderr)
            continue
        if selected_index is None:
            print("invalid selection: missing review item", file=sys.stderr)
            continue
        if command_name == "inspect":
            try:
                print_review_diff_header(
                    review_items[selected_index],
                    index=selected_index + 1,
                    total=len(review_items),
                )
                run_review_item_diff(review_items[selected_index])
                print_review_diff_footer(index=selected_index + 1, total=len(review_items))
            except ValueError as exc:
                print(f"review unavailable: {exc}", file=sys.stderr)
            continue
    return True


def review_plans_for_interactive_diffs(
    *,
    plans: Sequence,
    operation: str,
    json_output: bool,
    full_paths: bool = False,
) -> bool:
    if not interactive_mode_enabled(json_output=json_output):
        return True
    review_items = build_review_items(plans, operation=operation)
    if not review_items:
        return True
    return run_diff_review_menu(review_items, operation=operation, full_paths=full_paths)


def emit_interrupt_notice() -> None:
    sys.stderr.write("\ninterrupted\n")


def add_binding_argument(parser: argparse.ArgumentParser, *, required: bool = True) -> None:
    parser.add_argument(
        "binding",
        nargs=None if required else "?",
        metavar="<binding>",
        help="Binding argument in the form <repo>:<selector>[@<profile>]",
    )


def add_package_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "package",
        metavar="<package>",
        help="Tracked package argument in the form <repo>:<package> or <package>",
    )


def add_live_path_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "live_path",
        metavar="<live-path>",
        help="Live file or directory path to adopt into package config",
    )


def add_package_query_argument(parser: argparse.ArgumentParser, *, required: bool = False) -> None:
    parser.add_argument(
        "package_query",
        nargs=None if required else "?",
        metavar="<package-query>",
        help="Package query in the form [<repo>:]<package>",
    )


def add_snapshot_argument(parser: argparse.ArgumentParser, *, required: bool = True) -> None:
    parser.add_argument(
        "snapshot",
        nargs=None if required else "?",
        metavar="<snapshot>",
        help="Snapshot ID, unique leading snapshot prefix, or 'latest'",
    )


def add_dry_run_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-d",
        "--dry-run",
        action="store_true",
        help="Preview only; skip execution after planning and diff review",
    )


def add_full_path_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--full-path",
        action="store_true",
        dest="full_path",
        help="Show unabridged absolute paths in human-readable push/pull output",
    )


def hide_subparser_from_help(subparsers, name: str) -> None:
    # Argparse has no public API for parseable-but-hidden subcommands.
    subparsers._choices_actions = [
        action for action in subparsers._choices_actions if getattr(action, "dest", None) != name
    ]


def _assign_nested_value(target: dict[str, object], key_parts: Sequence[str], value: str) -> None:
    current = target
    for key in key_parts[:-1]:
        nested = current.get(key)
        if not isinstance(nested, dict):
            nested = {}
            current[key] = nested
        current = nested
    current[key_parts[-1]] = value



def _template_vars_from_dotman_env(environ: dict[str, str]) -> dict[str, object]:
    variables: dict[str, object] = {}
    for key, value in environ.items():
        if not key.startswith("DOTMAN_VAR_"):
            continue
        path_parts = [part for part in key.removeprefix("DOTMAN_VAR_").split("__") if part]
        if not path_parts:
            continue
        _assign_nested_value(variables, path_parts, value)
    return variables



def _apply_template_var_assignments(variables: dict[str, object], assignments: Sequence[str]) -> dict[str, object]:
    for assignment in assignments:
        if "=" not in assignment:
            raise ValueError(f"invalid --var assignment '{assignment}'; expected <key=value>")
        dotted_key, value = assignment.split("=", 1)
        key_parts = [part for part in dotted_key.split(".") if part]
        if not key_parts:
            raise ValueError(f"invalid --var assignment '{assignment}'; expected <key=value>")
        _assign_nested_value(variables, key_parts, value)
    return variables



def run_jinja_render(*, source_path: str, profile: str | None, inferred_os: str | None, var_assignments: Sequence[str]) -> int:
    path = Path(source_path)
    variables = _template_vars_from_dotman_env(dict(os.environ))
    _apply_template_var_assignments(variables, var_assignments)
    if not path.exists():
        raise ValueError(f"jinja render failed for {path}: source path does not exist")
    context = build_template_context(
        variables,
        profile=profile or os.environ.get("DOTMAN_PROFILE") or "default",
        inferred_os=inferred_os or os.environ.get("DOTMAN_OS") or sys.platform,
    )
    rendered, _projection_kind = render_template_file(path, context)
    sys.stdout.write(rendered.decode("utf-8"))
    return 0



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dotman", description="dotman CLI")
    parser.add_argument("--config", metavar="<config-path>", help="Path to dotman config.toml")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Emit machine-readable JSON")

    subparsers = parser.add_subparsers(dest="command", required=True, title="commands", metavar="<command>")

    track_parser = subparsers.add_parser(
        "track",
        help="Track a binding in manager state",
        description="Track a binding in manager state",
    )
    add_binding_argument(track_parser)

    add_parser = subparsers.add_parser(
        "add",
        help="Create or update package config from a live path",
        description="Create or update package config from a live path",
    )
    add_live_path_argument(add_parser)
    add_package_query_argument(add_parser, required=False)

    push_parser = subparsers.add_parser(
        "push",
        help="Push tracked changes from repo to live paths",
        description="Push tracked changes from repo to live paths",
    )
    add_dry_run_argument(push_parser)
    add_full_path_argument(push_parser)
    add_binding_argument(push_parser, required=False)

    pull_parser = subparsers.add_parser(
        "pull",
        help="Pull live changes back into the repo",
        description="Pull live changes back into the repo",
    )
    add_dry_run_argument(pull_parser)
    add_full_path_argument(pull_parser)
    add_binding_argument(pull_parser, required=False)

    rollback_parser = subparsers.add_parser(
        "rollback",
        help="Restore managed live paths from a recorded snapshot",
        description="Restore managed live paths from a recorded snapshot",
    )
    add_dry_run_argument(rollback_parser)
    add_full_path_argument(rollback_parser)
    add_snapshot_argument(rollback_parser, required=False)

    untrack_parser = subparsers.add_parser(
        "untrack",
        help="Remove a tracked binding from manager state",
        description="Remove a tracked binding from manager state",
    )
    add_binding_argument(untrack_parser)

    forget_parser = subparsers.add_parser(
        "forget",
        help="Alias for untrack",
        description="Alias for untrack",
    )
    add_binding_argument(forget_parser)

    list_parser = subparsers.add_parser(
        "list",
        help="List tracked or installed items",
        description="List tracked or installed items",
    )
    list_subparsers = list_parser.add_subparsers(
        dest="list_command",
        required=True,
        title="list commands",
        metavar="<list-command>",
    )
    list_subparsers.add_parser(
        "tracked",
        help="List tracked packages",
        description="List tracked packages",
    )
    list_subparsers.add_parser(
        "snapshots",
        help="List available snapshots",
        description="List available snapshots",
    )
    list_subparsers.add_parser("installed", help=argparse.SUPPRESS)
    hide_subparser_from_help(list_subparsers, "installed")

    info_parser = subparsers.add_parser(
        "info",
        help="Show detailed information about tracked or installed items",
        description="Show detailed information about tracked or installed items",
    )
    info_subparsers = info_parser.add_subparsers(
        dest="info_command",
        required=True,
        title="info commands",
        metavar="<info-command>",
    )
    info_tracked_parser = info_subparsers.add_parser(
        "tracked",
        help="Show tracked package details",
        description="Show tracked package details",
    )
    add_package_argument(info_tracked_parser)
    info_snapshot_parser = info_subparsers.add_parser(
        "snapshot",
        help="Show snapshot details",
        description="Show snapshot details",
    )
    add_full_path_argument(info_snapshot_parser)
    add_snapshot_argument(info_snapshot_parser)
    info_installed_parser = info_subparsers.add_parser("installed", help=argparse.SUPPRESS)
    add_package_argument(info_installed_parser)
    hide_subparser_from_help(info_subparsers, "installed")

    reconcile_parser = subparsers.add_parser(
        "reconcile",
        help="Re-run a reconcile helper subcommand",
        description="Re-run a reconcile helper subcommand",
    )
    reconcile_subparsers = reconcile_parser.add_subparsers(
        dest="reconcile_command",
        required=True,
        title="reconcile commands",
        metavar="<reconcile-command>",
    )

    reconcile_editor_parser = reconcile_subparsers.add_parser(
        "editor",
        help="Open repo and live files in an editor for reconcile review",
        description="Open repo and live files in an editor for reconcile review",
    )
    reconcile_editor_parser.add_argument(
        "--repo-path",
        required=True,
        metavar="<repo-path>",
        help="Path to the repo copy of the target file",
    )
    reconcile_editor_parser.add_argument(
        "--live-path",
        required=True,
        metavar="<live-path>",
        help="Path to the live copy of the target file",
    )
    reconcile_editor_parser.add_argument(
        "--review-repo-path",
        metavar="<review-repo-path>",
        help="Optional prepared repo-side review file path",
    )
    reconcile_editor_parser.add_argument(
        "--review-live-path",
        metavar="<review-live-path>",
        help="Optional prepared live-side review file path",
    )
    reconcile_editor_parser.add_argument(
        "--additional-source",
        action="append",
        default=[],
        metavar="<source-path>",
        help="Additional repo source file to include in transactional reconcile editing",
    )
    reconcile_editor_parser.add_argument(
        "--editor",
        metavar="<editor-command>",
        help="Editor command to run instead of the default editor",
    )

    reconcile_jinja_parser = reconcile_subparsers.add_parser(
        "jinja",
        help="Reconcile a Jinja source with its recursive template dependencies",
        description="Reconcile a Jinja source with its recursive template dependencies",
    )
    reconcile_jinja_parser.add_argument(
        "--repo-path",
        required=True,
        metavar="<repo-path>",
        help="Path to the repo copy of the target file",
    )
    reconcile_jinja_parser.add_argument(
        "--live-path",
        required=True,
        metavar="<live-path>",
        help="Path to the live copy of the target file",
    )
    reconcile_jinja_parser.add_argument(
        "--review-repo-path",
        metavar="<review-repo-path>",
        help="Optional prepared repo-side review file path",
    )
    reconcile_jinja_parser.add_argument(
        "--review-live-path",
        metavar="<review-live-path>",
        help="Optional prepared live-side review file path",
    )
    reconcile_jinja_parser.add_argument(
        "--editor",
        metavar="<editor-command>",
        help="Editor command to run instead of the default editor",
    )

    render_parser = subparsers.add_parser(
        "render",
        help="Render built-in template helpers",
        description="Render built-in template helpers",
    )
    render_subparsers = render_parser.add_subparsers(
        dest="render_command",
        required=True,
        title="render commands",
        metavar="<render-command>",
    )
    render_jinja_parser = render_subparsers.add_parser(
        "jinja",
        help="Render a file with the built-in Jinja renderer",
        description="Render a file with the built-in Jinja renderer",
    )
    render_jinja_parser.add_argument(
        "source_path",
        metavar="<source-path>",
        help="Path to the Jinja source file",
    )
    render_jinja_parser.add_argument(
        "--profile",
        metavar="<profile>",
        help="Profile value to expose in template context",
    )
    render_jinja_parser.add_argument(
        "--os",
        dest="template_os",
        metavar="<os>",
        help="OS value to expose in template context",
    )
    render_jinja_parser.add_argument(
        "--var",
        action="append",
        default=[],
        metavar="<key=value>",
        help="Additional template var assignment using dotted keys",
    )
    return parser


def effective_execution_mode(*, dry_run_requested: bool) -> str:
    return "dry-run" if dry_run_requested else "execute"


def count_hook_commands(plans: Sequence) -> int:
    return sum(len(hook_plans) for plan in plans for hook_plans in plan.hooks.values())


def render_summary_stat(*, label: str, value: int) -> str:
    if not colors_enabled():
        return f"{label}: {value}"
    return f"{style_text(f'{label}:', *MENU_HINT_STYLE)} {style_text(str(value), '1')}"


def display_cli_path(reference_path: Path | str, *, full_paths: bool) -> str:
    return display_review_path(reference_path, compact=not full_paths)


def render_payload_section_label(label: str) -> str:
    if not colors_enabled():
        return label
    return style_text(label, *MENU_HINT_STYLE)


def print_payload_header(header_text: str) -> None:
    print()
    if not colors_enabled():
        print(f"{MENU_HEADER_MARKER} {header_text}")
        return
    print(
        f"{style_text(MENU_HEADER_MARKER, *MENU_HEADER_MARKER_STYLE)} "
        f"{style_text(header_text, '1')}"
    )


def print_payload_package_header(*, repo_name: str, package_id: str, profile: str) -> None:
    if not colors_enabled():
        print(f"  {MENU_HEADER_MARKER} {package_profile_label_text(repo_name=repo_name, package_id=package_id, profile=profile)}")
        return
    print(
        f"  {style_text(MENU_HEADER_MARKER, *MENU_HEADER_MARKER_STYLE)} "
        f"{render_package_profile_label(repo_name=repo_name, package_id=package_id, profile=profile)}"
    )


def render_payload_hook_label(hook_name: str) -> str:
    hook_label = f"[{hook_name}]"
    if not colors_enabled():
        return hook_label
    return style_text(hook_label, *MENU_HINT_STYLE)


def render_payload_action(action: str) -> str:
    if not colors_enabled():
        return action
    return style_text(action, *MENU_ACTION_STYLE_BY_NAME.get(action, ("1",)))


def print_payload_target_item(item: PendingSelectionItem, *, full_paths: bool = False) -> None:
    source_path = display_cli_path(item.source_path, full_paths=full_paths)
    destination_path = display_cli_path(item.destination_path, full_paths=full_paths)
    arrow_text = style_text("->", *MENU_HINT_STYLE) if colors_enabled() else "->"
    print(f"      {item.package_id}:{item.target_name} -> {render_payload_action(item.action)}")
    print(f"        {source_path} {arrow_text} {destination_path}")


def collect_payload_package_sections(plans: Sequence, *, operation: str) -> list[PayloadPackageSection]:
    package_sections: dict[tuple[str, str, str], PayloadPackageSection] = {}

    for plan in plans:
        targets_by_package: dict[str, list[PendingSelectionItem]] = {}
        for item in collect_pending_selection_items_for_operation([plan], operation=operation):
            targets_by_package.setdefault(item.package_id, []).append(item)

        hooks_by_package: dict[str, dict[str, list[HookPlan]]] = {}
        for hook_name, hook_plans in plan.hooks.items():
            for hook_plan in hook_plans:
                hooks_by_package.setdefault(hook_plan.package_id, {}).setdefault(hook_name, []).append(hook_plan)

        for package_id in plan.package_ids:
            package_targets = targets_by_package.get(package_id, [])
            package_hooks = hooks_by_package.get(package_id, {})
            if not package_targets and not package_hooks:
                continue
            section_key = (plan.binding.repo, package_id, plan.binding.profile)
            section = package_sections.get(section_key)
            if section is None:
                section = PayloadPackageSection(
                    repo_name=plan.binding.repo,
                    package_id=package_id,
                    profile=plan.binding.profile,
                    hooks={},
                    targets=[],
                )
                package_sections[section_key] = section
            for hook_name, hook_plans in package_hooks.items():
                section.hooks.setdefault(hook_name, []).extend(hook_plans)
            section.targets.extend(package_targets)

    return list(package_sections.values())


def emit_payload(*, operation: str, plans: Sequence, json_output: bool, mode: str, full_paths: bool = False) -> int:
    visible_plans = []
    for plan in plans:
        visible_targets = [target for target in plan.target_plans if target.action != "noop"]
        if not visible_targets:
            continue
        visible_plans.append(replace(plan, target_plans=visible_targets))
    payload = {
        "mode": mode,
        "operation": operation,
        "bindings": [plan.to_dict() for plan in visible_plans],
    }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    package_sections = collect_payload_package_sections(visible_plans, operation=operation)
    target_items = [item for section in package_sections for item in section.targets]
    print_payload_header(f"{mode} {operation}")
    print(f"  {render_payload_section_label('preview only; no files or hooks will be changed')}")
    print(
        "  "
        + " · ".join(
            [
                render_summary_stat(label="packages", value=len(package_sections)),
                render_summary_stat(label="target actions", value=len(target_items)),
                render_summary_stat(label="hook commands", value=count_hook_commands(visible_plans)),
            ]
        )
    )

    if not package_sections:
        print()
        print(f"  {render_payload_section_label('no pending target actions')}")
        return 0

    for section in package_sections:
        print()
        print_payload_package_header(
            repo_name=section.repo_name,
            package_id=section.package_id,
            profile=section.profile,
        )

        print(f"    {render_payload_section_label('targets:')}")
        for item in section.targets:
            print_payload_target_item(item, full_paths=full_paths)

        if section.hooks:
            print(f"    {render_payload_section_label('hooks:')}")
            for hook_name, hook_plans in section.hooks.items():
                print(f"      {render_payload_hook_label(hook_name)}")
                for index, hook_plan in enumerate(hook_plans, start=1):
                    for line in render_hook_command_lines(
                        hook_plan.command,
                        command_count=len(hook_plans),
                        index=index,
                    ):
                        print(f"  {line}")
    return 0


def execution_step_display(step: ExecutionStep, *, full_paths: bool) -> str:
    if step.hook_plan is not None:
        return step.hook_plan.command
    target = step.target_plan
    if target is None:
        return ""
    if step.kind == "chmod":
        reference_path = target.live_path if step.binding_plan.operation == "push" else target.repo_path
        chmod_mode = target.chmod or "?"
        return f"{chmod_mode} {display_cli_path(reference_path, full_paths=full_paths)}"
    if step.action == "reconcile":
        return display_cli_path(target.live_path, full_paths=full_paths)
    if step.directory_item is not None:
        reference_path = (
            step.directory_item.live_path
            if step.binding_plan.operation == "push"
            else step.directory_item.repo_path
        )
        return display_cli_path(reference_path, full_paths=full_paths)
    reference_path = target.live_path if step.binding_plan.operation == "push" else target.repo_path
    return display_cli_path(reference_path, full_paths=full_paths)


def render_execution_action(action: str) -> str:
    display_action = action.replace("_repo", " repo") if action.endswith("_repo") else action
    if not colors_enabled():
        return display_action
    style_key = action.removesuffix("_repo")
    return style_text(display_action, *MENU_ACTION_STYLE_BY_NAME.get(style_key, ("1",)))


def print_execution_header(*, session: ExecutionSession) -> None:
    step_count = sum(len(package.steps) for package in session.packages)
    print_payload_header(f"executing {session.operation}")
    print(
        "  "
        + " · ".join(
            [
                render_summary_stat(label="packages", value=len(session.packages)),
                render_summary_stat(label="steps", value=step_count),
            ]
        )
    )
    if not session.packages:
        print()
        print(f"  {render_payload_section_label('no pending target actions')}")


def print_execution_package_start(package) -> None:
    print()
    print_payload_package_header(
        repo_name=package.repo_name,
        package_id=package.package_id,
        profile=package.profile,
    )


def print_execution_step_start(
    _package,
    step: ExecutionStep,
    index: int,
    total: int,
    *,
    full_paths: bool,
) -> None:
    print(
        f"    [{index}/{total}] {render_execution_action(step.action):<11} "
        f"{execution_step_display(step, full_paths=full_paths)}"
    )


def render_execution_status(status: str) -> str:
    if not colors_enabled():
        return status
    return style_text(status, *EXECUTION_STATUS_STYLE_BY_NAME.get(status, ("1",)))


def print_execution_step_finish(
    _package,
    step_result: ExecutionStepResult,
    _index: int,
    _total: int,
) -> None:
    if step_result.status == "ok":
        print(f"      {render_execution_status('ok')}")
        return
    if step_result.error:
        print(f"      {step_result.error}")
    print(f"      {render_execution_status(step_result.status)}")


def print_execution_package_finish(package_result: PackageExecutionResult) -> None:
    if package_result.status == "skipped":
        print(f"    {render_execution_status('skipped')}")


def emit_execution_result(*, result, json_output: bool) -> int:
    if json_output:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return result.exit_code


def execute_plans(*, operation: str, plans: Sequence, json_output: bool, full_paths: bool = False):
    session = build_execution_session(plans, operation=operation)
    if json_output:
        return execute_session(session, stream_output=False)
    print_execution_header(session=session)
    if not session.packages:
        return execute_session(session, stream_output=True)
    return execute_session(
        session,
        stream_output=True,
        on_package_start=print_execution_package_start,
        on_step_start=lambda package, step, index, total: print_execution_step_start(
            package,
            step,
            index,
            total,
            full_paths=full_paths,
        ),
        on_step_finish=print_execution_step_finish,
        on_package_finish=print_execution_package_finish,
    )


def run_execution(*, operation: str, plans: Sequence, json_output: bool, full_paths: bool = False) -> int:
    return emit_execution_result(
        result=execute_plans(
            operation=operation,
            plans=plans,
            json_output=json_output,
            full_paths=full_paths,
        ),
        json_output=json_output,
    )


def emit_tracked_packages(*, packages: Sequence, json_output: bool) -> int:
    payload = {
        "mode": "dry-run",
        "operation": "list-tracked",
        "packages": [package.to_dict() for package in packages],
    }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    for package in packages:
        print(
            render_package_label(
                repo_name=package.repo,
                package_id=package.package_id,
                bound_profile=package.bound_profile,
            )
        )
    return 0


def emit_forgotten_binding(*, binding, still_tracked_package, json_output: bool) -> int:
    payload = {
        "mode": "state-only",
        "operation": "untrack",
        "binding": {
            "repo": binding.repo,
            "selector": binding.selector,
            "profile": binding.profile,
        },
    }
    if still_tracked_package is not None:
        payload["still_tracked_package"] = {
            "repo": still_tracked_package.repo,
            "package_id": still_tracked_package.package_id,
            "bindings": [
                {
                    **binding_detail.binding.to_dict(),
                    "tracked_reason": binding_detail.tracked_reason,
                }
                for binding_detail in still_tracked_package.bindings
            ],
        }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print(f"untracked {render_binding_reference(binding)}")
    if still_tracked_package is not None:
        print(
            f"{render_package_label(repo_name=still_tracked_package.repo, package_id=still_tracked_package.package_id, bound_profile=still_tracked_package.bound_profile)} "
            "remains tracked via:"
        )
        for binding_detail in still_tracked_package.bindings:
            print(
                f"  {render_tracked_reason(binding_detail.tracked_reason)}: "
                + render_binding_label(
                    repo_name=binding_detail.binding.repo,
                    selector=binding_detail.binding.selector,
                    profile=binding_detail.binding.profile,
                )
            )
    return 0


def emit_tracked_binding(*, binding, json_output: bool) -> int:
    payload = {
        "mode": "state-only",
        "operation": "track",
        "binding": {
            "repo": binding.repo,
            "selector": binding.selector,
            "profile": binding.profile,
        },
    }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print(f"tracked {render_binding_reference(binding)}")
    return 0


def emit_add_result(*, result, json_output: bool) -> int:
    if json_output:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 0

    package_label = render_package_label(
        repo_name=result.repo_name,
        package_id=result.package_id,
        package_first=True,
        include_repo_context=True,
    )
    action = "created" if result.created_package else "updated"
    print(f"{action} package config {package_label}")
    print(f"  manifest: {result.manifest_path}")
    print(f"  target:   {result.target_name} [{result.target_kind}]")
    print(f"  source:   {result.source_path}")
    print(f"  path:     {result.config_path}")
    if result.chmod is not None:
        print(f"  chmod:    {result.chmod}")
    manifest_only_note = "manifest only; repo source files were not copied"
    if colors_enabled():
        manifest_only_note = style_text(manifest_only_note, *MENU_HINT_STYLE)
    print(f"  {manifest_only_note}")
    return 0


def emit_kept_add_result(*, repo_name: str, package_id: str, json_output: bool) -> int:
    payload = {
        "mode": "config-only",
        "operation": "add",
        "repo": repo_name,
        "package_id": package_id,
        "written": False,
    }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print(f"kept package config unchanged {render_package_label(repo_name=repo_name, package_id=package_id, package_first=True, include_repo_context=True)}")
    return 0


def emit_noop_add_result(*, json_output: bool) -> int:
    payload = {
        "mode": "config-only",
        "operation": "add",
        "written": False,
        "changed": False,
    }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print("No package config changes.")
    return 0


def emit_kept_binding(*, binding, json_output: bool) -> int:
    payload = {
        "mode": "state-only",
        "operation": "track",
        "binding": {
            "repo": binding.repo,
            "selector": binding.selector,
            "profile": binding.profile,
        },
        "recorded": False,
    }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print(f"kept existing tracked binding {render_binding_reference(binding)}")
    return 0


def emit_skipped_tracking(*, binding, json_output: bool) -> int:
    payload = {
        "mode": "state-only",
        "operation": "track",
        "binding": {
            "repo": binding.repo,
            "selector": binding.selector,
            "profile": binding.profile,
        },
        "recorded": False,
    }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print(f"skipped tracking {render_binding_reference(binding)}")
    return 0


def render_hook_command_lines(command: str, *, command_count: int, index: int) -> list[str]:
    command_lines = command.splitlines() or [""]
    # Number multi-command hooks so users can tell distinct commands apart without cluttering single-command hooks.
    first_prefix = f"      [{index}] " if command_count > 1 else "      "
    continuation_prefix = " " * len(first_prefix)
    return [
        f"{first_prefix}{command_lines[0]}",
        *[f"{continuation_prefix}{line}" for line in command_lines[1:]],
    ]



def emit_tracked_package_detail(*, package_detail, json_output: bool) -> int:
    payload = {
        "mode": "dry-run",
        "operation": "info-tracked",
        "package": package_detail.to_dict(),
    }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print(
        render_package_label(
            repo_name=package_detail.repo,
            package_id=package_detail.package_id,
            bound_profile=package_detail.bound_profile,
        )
    )
    if package_detail.description:
        print(f"  {package_detail.description}")
    if package_detail.bindings:
        print()
        print(render_info_section_header("provenance"))
    for binding in package_detail.bindings:
        binding_label = render_binding_label(
            repo_name=binding.binding.repo,
            selector=binding.binding.selector,
            profile=binding.binding.profile,
        )
        print(f"    {render_tracked_reason(binding.tracked_reason)}: {binding_label}")

    bindings_with_hooks = [binding for binding in package_detail.bindings if binding.hooks]
    if bindings_with_hooks:
        print()
        print(render_info_section_header("hooks"))
    # Hook output stays package-centric here. Under the current tracked-winner model,
    # a package instance has one effective hook-bearing binding, so repeating the
    # provenance binding under ::hooks only adds noise.
    for binding in bindings_with_hooks:
        for hook_name, hook_plans in binding.hooks.items():
            hook_label = f"[{hook_name}]"
            if colors_enabled():
                hook_label = style_text(hook_label, *MENU_HINT_STYLE)
            print(f"    {hook_label}")
            for index, hook_plan in enumerate(hook_plans, start=1):
                for line in render_hook_command_lines(
                    hook_plan.command,
                    command_count=len(hook_plans),
                    index=index,
                ):
                    print(line)

    if package_detail.owned_targets:
        print()
        print(render_info_section_header("owned targets"))
    for target in package_detail.owned_targets:
        print(
            "    "
            + f"{style_text(target.target.target_name, '1') if colors_enabled() else target.target.target_name} "
            + f"-> {target.target.live_path}"
        )
    return 0


def resolve_snapshot_record(snapshot_root: Path, snapshot_ref: str | None, *, json_output: bool) -> SnapshotRecord:
    matches = find_snapshot_matches(snapshot_root, snapshot_ref)
    if not matches:
        if snapshot_ref is None or snapshot_ref == "latest":
            raise ValueError("no snapshots are available")
        raise ValueError(f"snapshot '{snapshot_ref}' did not match any available snapshot")
    if len(matches) == 1:
        return matches[0]
    if not interactive_mode_enabled(json_output=json_output):
        raise ValueError(
            f"snapshot '{snapshot_ref}' is ambiguous: " + ", ".join(snapshot.snapshot_id for snapshot in matches)
        )
    selected_index = select_menu_option(
        header_text=f"Select a snapshot for '{snapshot_ref}':",
        option_labels=[
            f"{snapshot.snapshot_id} [{snapshot.status}] ({snapshot.entry_count} path{'s' if snapshot.entry_count != 1 else ''})"
            for snapshot in matches
        ],
    )
    return matches[selected_index]


def visible_rollback_actions(actions: Sequence[RollbackAction]) -> list[RollbackAction]:
    return [action for action in actions if action.action != "noop"]


def build_rollback_review_items(snapshot: SnapshotRecord, actions: Sequence[RollbackAction]) -> list[ReviewItem]:
    review_items: list[ReviewItem] = []
    for action in actions:
        if action.action == "noop":
            continue
        review_items.append(
            ReviewItem(
                binding_label=f"snapshot:{snapshot.snapshot_id}",
                package_id="snapshot",
                target_name=str(action.live_path),
                action=action.action,
                operation="rollback",
                repo_path=action.snapshot_path,
                live_path=action.live_path,
                source_path=str(action.snapshot_path),
                destination_path=str(action.live_path),
                before_bytes=action.before_bytes,
                after_bytes=action.after_bytes,
            )
        )
    return review_items


def review_rollback_actions_for_interactive_diffs(
    *,
    snapshot: SnapshotRecord,
    actions: Sequence[RollbackAction],
    json_output: bool,
    full_paths: bool = False,
) -> bool:
    if not interactive_mode_enabled(json_output=json_output):
        return True
    review_items = build_rollback_review_items(snapshot, actions)
    if not review_items:
        return True
    return run_diff_review_menu(review_items, operation="rollback", full_paths=full_paths)


def emit_snapshot_list(
    *,
    snapshots: Sequence[SnapshotRecord],
    json_output: bool,
    max_generations: int | None = None,
) -> int:
    payload = {
        "mode": "dry-run",
        "operation": "list-snapshots",
        "snapshots": [snapshot.to_dict() for snapshot in snapshots],
    }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print_payload_header("snapshots")
    if max_generations is not None:
        print(
            "  "
            + " · ".join(
                [
                    render_summary_stat(label="retained", value=len(snapshots)),
                    render_summary_stat(label="limit", value=max_generations),
                ]
            )
        )
    else:
        print(f"  {render_summary_stat(label='snapshots', value=len(snapshots))}")

    if not snapshots:
        print()
        print(f"  {render_payload_section_label('no snapshots')}")
        return 0

    for index, snapshot in enumerate(snapshots, start=1):
        print()
        title = format_snapshot_timestamp(snapshot.created_at)
        if colors_enabled():
            title = style_text(title, "1")
        print(f"  {style_text(f'{index})', *MENU_INDEX_STYLE) if colors_enabled() else f'{index})'} {title}")
        print(f"     {render_snapshot_metadata_label('ref:')}          {render_snapshot_ref(snapshot.snapshot_id)}")
        print(f"     {render_snapshot_metadata_label('status:')}       {render_snapshot_status(snapshot.status)}")
        print(f"     {render_snapshot_metadata_label('paths:')}        {snapshot.entry_count}")
        if snapshot.restore_count > 0:
            restore_summary = f"{snapshot.restore_count}x"
            if snapshot.last_restored_at is not None:
                restore_summary += f" · {format_snapshot_timestamp(snapshot.last_restored_at)}"
            print(f"     {render_snapshot_metadata_label('restored:')}     {restore_summary}")
    return 0


def emit_snapshot_detail(*, snapshot: SnapshotRecord, json_output: bool, full_paths: bool = False) -> int:
    payload = {
        "mode": "dry-run",
        "operation": "info-snapshot",
        "snapshot": snapshot.to_dict(),
    }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    header_text = f"snapshot {snapshot.snapshot_id}"
    if colors_enabled():
        print(style_text(header_text, "1"))
    else:
        print(header_text)
    print(f"  {render_snapshot_metadata_label('created:')}       {format_snapshot_timestamp(snapshot.created_at)}")
    print(f"  {render_snapshot_metadata_label('status:')}        {render_snapshot_status(snapshot.status)}")
    print(f"  {render_snapshot_metadata_label('paths:')}         {snapshot.entry_count}")
    print(f"  {render_snapshot_metadata_label('restore count:')} {snapshot.restore_count}")
    if snapshot.last_restored_at is not None:
        print(
            f"  {render_snapshot_metadata_label('last restored:')} "
            f"{format_snapshot_timestamp(snapshot.last_restored_at)}"
        )

    if snapshot.entries:
        print()
        print(render_info_section_header("paths"))
    for entry in snapshot.entries:
        path_text = display_cli_path(entry.live_path, full_paths=full_paths)
        print(f"    {path_text}")
        print(f"      {render_snapshot_metadata_label('reason:')} {render_snapshot_reason(entry.push_action)}")
        provenance = render_snapshot_provenance(
            repo_name=entry.repo_name,
            package_id=entry.package_id,
            target_name=entry.target_name,
            binding_label=entry.binding_label,
        )
        if provenance is not None:
            print(f"      {render_snapshot_metadata_label('provenance:')} {provenance}")
    return 0


def emit_rollback_payload(
    *,
    snapshot: SnapshotRecord,
    actions: Sequence[RollbackAction],
    json_output: bool,
    mode: str,
    full_paths: bool = False,
) -> int:
    visible_actions = visible_rollback_actions(actions)
    payload = {
        "mode": mode,
        "operation": "rollback",
        "snapshot": snapshot.to_dict(),
        "actions": [action.to_dict() for action in visible_actions],
    }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print_payload_header(f"{mode} rollback")
    print(f"  snapshot: {snapshot.snapshot_id}")
    print(f"  created:  {snapshot.created_at}")
    print(f"  status:   {snapshot.status}")
    print(f"  {render_summary_stat(label='paths', value=len(visible_actions))}")
    if not visible_actions:
        print()
        print(f"  {render_payload_section_label('no pending target actions')}")
        return 0
    for action in visible_actions:
        print(
            f"  [{render_payload_action(action.action)}] "
            f"{display_cli_path(action.snapshot_path, full_paths=full_paths)} -> "
            f"{display_cli_path(action.live_path, full_paths=full_paths)}"
        )
    return 0


def print_rollback_execution_header(*, snapshot: SnapshotRecord, action_count: int) -> None:
    print_payload_header("executing rollback")
    print(f"  snapshot: {snapshot.snapshot_id}")
    print(f"  created:  {snapshot.created_at}")
    print(f"  status:   {snapshot.status}")
    print(f"  {render_summary_stat(label='paths', value=action_count)}")
    if action_count == 0:
        print()
        print(f"  {render_payload_section_label('no pending target actions')}")


def print_rollback_execution_step(index: int, total: int, action: RollbackAction, *, full_paths: bool) -> None:
    print(
        f"    [{index}/{total}] {render_execution_action(action.action):<11} "
        f"{display_cli_path(action.live_path, full_paths=full_paths)}"
    )


def emit_rollback_result(*, result, json_output: bool) -> int:
    if json_output:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return result.exit_code


def run_rollback_execution(
    *,
    snapshot: SnapshotRecord,
    actions: Sequence[RollbackAction],
    json_output: bool,
    full_paths: bool = False,
) -> int:
    visible_actions = visible_rollback_actions(actions)
    if not json_output:
        print_rollback_execution_header(snapshot=snapshot, action_count=len(visible_actions))
    if not visible_actions:
        return 0
    for index, action in enumerate(visible_actions, start=1):
        if not json_output:
            print_rollback_execution_step(index, len(visible_actions), action, full_paths=full_paths)
    result = execute_rollback(snapshot, visible_actions)
    if not json_output:
        for action_result in result.actions:
            if action_result.status == "ok":
                print(f"      {render_execution_status('ok')}")
                continue
            if action_result.error:
                print(f"      {action_result.error}")
            print(f"      {render_execution_status(action_result.status)}")
    return emit_rollback_result(result=result, json_output=json_output)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(list(argv) if argv is not None else None)
        if args.command == "reconcile" and args.reconcile_command == "editor":
            return run_basic_reconcile(
                repo_path=args.repo_path,
                live_path=args.live_path,
                additional_sources=args.additional_source,
                review_repo_path=args.review_repo_path,
                review_live_path=args.review_live_path,
                editor=args.editor,
            )
        if args.command == "reconcile" and args.reconcile_command == "jinja":
            return run_jinja_reconcile(
                repo_path=args.repo_path,
                live_path=args.live_path,
                review_repo_path=args.review_repo_path,
                review_live_path=args.review_live_path,
                editor=args.editor,
            )
        if args.command == "render" and args.render_command == "jinja":
            return run_jinja_render(
                source_path=args.source_path,
                profile=args.profile,
                inferred_os=args.template_os,
                var_assignments=args.var,
            )
        engine = DotmanEngine.from_config_path(args.config)
        if args.command == "track":
            binding_text, profile = resolve_binding_text(engine, args.binding, json_output=args.json_output)
            _repo, binding, _selector_kind = engine.resolve_binding(binding_text, profile=profile)
            while True:
                if not ensure_track_binding_replacement_confirmed(
                    engine,
                    binding=binding,
                    json_output=args.json_output,
                ):
                    existing_binding = find_recorded_binding_for_scope(engine, binding)
                    if existing_binding is None:
                        raise ValueError("missing existing tracked binding during replacement confirmation")
                    return emit_kept_binding(binding=existing_binding, json_output=args.json_output)
                try:
                    engine.validate_recorded_binding(binding)
                except TrackedTargetConflictError as exc:
                    promoted_binding = prompt_for_conflicting_package_binding(
                        binding=binding,
                        conflict=exc,
                        json_output=args.json_output,
                    )
                    if promoted_binding is not None:
                        binding = promoted_binding
                        binding_text = f"{binding.repo}:{binding.selector}"
                        continue
                    alternative_profile = select_non_conflicting_track_profile(
                        engine,
                        binding_text=binding_text,
                        current_profile=binding.profile,
                        json_output=args.json_output,
                    )
                    if alternative_profile is None:
                        raise
                    _repo, binding, _selector_kind = engine.resolve_binding(binding_text, profile=alternative_profile)
                    continue
                if not ensure_track_binding_implicit_overrides_confirmed(
                    engine,
                    binding=binding,
                    json_output=args.json_output,
                ):
                    existing_binding = find_recorded_binding_exact(engine, binding)
                    if existing_binding is not None:
                        return emit_kept_binding(binding=existing_binding, json_output=args.json_output)
                    return emit_skipped_tracking(binding=binding, json_output=args.json_output)
                engine.record_binding(binding)
                return emit_tracked_binding(binding=binding, json_output=args.json_output)
        if args.command == "add":
            repo_name, package_id = resolve_add_package_text(
                engine,
                args.package_query,
                json_output=args.json_output,
            )
            result = prepare_add_to_package(
                repo_root=engine.get_repo(repo_name).root,
                repo_name=repo_name,
                package_id=package_id,
                live_path_text=args.live_path,
            )
            if args.json_output or not interactive_mode_enabled(json_output=args.json_output):
                return emit_add_result(
                    result=write_add_result(result),
                    json_output=args.json_output,
                )
            if add_editor_available():
                review_result = review_add_manifest(result)
                if review_result is None:
                    raise ValueError("add review expected an editor, but none is configured")
                if review_result.exit_code != 0:
                    return review_result.exit_code
                if review_result.manifest_text == result.before_text:
                    return emit_noop_add_result(json_output=args.json_output)
                if not confirm_add_manifest_write(repo_name=repo_name, package_id=package_id):
                    return emit_kept_add_result(
                        repo_name=repo_name,
                        package_id=package_id,
                        json_output=args.json_output,
                    )
                result = write_add_result(result, manifest_text=review_result.manifest_text)
                return emit_add_result(result=result, json_output=args.json_output)
            return emit_add_result(result=write_add_result(result), json_output=args.json_output)
        if args.command == "push":
            if args.binding:
                _repo, binding = resolve_tracked_binding_text(
                    engine,
                    args.binding,
                    operation="push",
                    allow_package_owners=True,
                    json_output=args.json_output,
                )
                binding_text = f"{binding.repo}:{binding.selector}"
                plan = engine.plan_push_binding(binding_text, profile=binding.profile)
                plans = filter_plans_for_interactive_selection(
                    plans=[plan],
                    operation="push",
                    json_output=args.json_output,
                    full_paths=args.full_path,
                )
            else:
                plans = filter_plans_for_interactive_selection(
                    plans=engine.plan_push(),
                    operation="push",
                    json_output=args.json_output,
                    full_paths=args.full_path,
                )
            if not review_plans_for_interactive_diffs(
                plans=plans,
                operation="push",
                json_output=args.json_output,
                full_paths=args.full_path,
            ):
                emit_interrupt_notice()
                return INTERRUPTED_EXIT_CODE
            if args.dry_run:
                return emit_payload(
                    operation="push",
                    plans=plans,
                    json_output=args.json_output,
                    mode=effective_execution_mode(dry_run_requested=True),
                    full_paths=args.full_path,
                )
            snapshot = create_push_snapshot(plans, engine.config.snapshots)
            try:
                execution_result = execute_plans(
                    operation="push",
                    plans=plans,
                    json_output=args.json_output,
                    full_paths=args.full_path,
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
                snapshot = mark_snapshot_status(snapshot, "applied" if execution_result.exit_code == 0 else "failed")
                prune_snapshots(
                    engine.config.snapshots.path,
                    max_generations=engine.config.snapshots.max_generations,
                )
            return emit_execution_result(result=execution_result, json_output=args.json_output)
        if args.command == "pull":
            if args.binding:
                _repo, binding = resolve_tracked_binding_text(
                    engine,
                    args.binding,
                    operation="pull",
                    allow_package_owners=True,
                    json_output=args.json_output,
                )
                binding_text = f"{binding.repo}:{binding.selector}"
                profile = binding.profile
                plans = filter_plans_for_interactive_selection(
                    plans=[engine.plan_pull_binding(binding_text, profile=profile)],
                    operation="pull",
                    json_output=args.json_output,
                    full_paths=args.full_path,
                )
            else:
                plans = filter_plans_for_interactive_selection(
                    plans=engine.plan_pull(),
                    operation="pull",
                    json_output=args.json_output,
                    full_paths=args.full_path,
                )
            if not review_plans_for_interactive_diffs(
                plans=plans,
                operation="pull",
                json_output=args.json_output,
                full_paths=args.full_path,
            ):
                emit_interrupt_notice()
                return INTERRUPTED_EXIT_CODE
            if args.dry_run:
                return emit_payload(
                    operation="pull",
                    plans=plans,
                    json_output=args.json_output,
                    mode=effective_execution_mode(dry_run_requested=True),
                    full_paths=args.full_path,
                )
            return run_execution(
                operation="pull",
                plans=plans,
                json_output=args.json_output,
                full_paths=args.full_path,
            )
        if args.command == "rollback":
            snapshot = resolve_snapshot_record(
                engine.config.snapshots.path,
                args.snapshot,
                json_output=args.json_output,
            )
            rollback_actions = build_rollback_actions(snapshot)
            if not review_rollback_actions_for_interactive_diffs(
                snapshot=snapshot,
                actions=rollback_actions,
                json_output=args.json_output,
                full_paths=args.full_path,
            ):
                emit_interrupt_notice()
                return INTERRUPTED_EXIT_CODE
            if args.dry_run:
                return emit_rollback_payload(
                    snapshot=snapshot,
                    actions=rollback_actions,
                    json_output=args.json_output,
                    mode=effective_execution_mode(dry_run_requested=True),
                    full_paths=args.full_path,
                )
            exit_code = run_rollback_execution(
                snapshot=snapshot,
                actions=rollback_actions,
                json_output=args.json_output,
                full_paths=args.full_path,
            )
            if exit_code == 0:
                record_snapshot_restore(snapshot)
            return exit_code
        if args.command in {"untrack", "forget"}:
            _repo, binding = resolve_tracked_binding_text(
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
            return emit_forgotten_binding(
                binding=removed_binding,
                still_tracked_package=find_remaining_tracked_package_after_untrack(engine, removed_binding),
                json_output=args.json_output,
            )
        if args.command == "list" and args.list_command in {"tracked", "installed"}:
            return emit_tracked_packages(packages=engine.list_installed_packages(), json_output=args.json_output)
        if args.command == "list" and args.list_command == "snapshots":
            return emit_snapshot_list(
                snapshots=list_snapshots(engine.config.snapshots.path),
                json_output=args.json_output,
                max_generations=engine.config.snapshots.max_generations,
            )
        if args.command == "info" and args.info_command in {"tracked", "installed"}:
            _repo, package_id, bound_profile = resolve_tracked_package_text(
                engine,
                args.package,
                json_output=args.json_output,
            )
            package_ref = package_ref_text(package_id=package_id, bound_profile=bound_profile)
            return emit_tracked_package_detail(
                package_detail=engine.describe_installed_package(f"{_repo.config.name}:{package_ref}"),
                json_output=args.json_output,
            )
        if args.command == "info" and args.info_command == "snapshot":
            return emit_snapshot_detail(
                snapshot=resolve_snapshot_record(
                    engine.config.snapshots.path,
                    args.snapshot,
                    json_output=args.json_output,
                ),
                json_output=args.json_output,
                full_paths=args.full_path,
            )
    except KeyboardInterrupt:
        emit_interrupt_notice()
        return INTERRUPTED_EXIT_CODE
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
