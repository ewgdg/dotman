from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Sequence, TypeVar

from dotman import cli_style
from dotman.add import (
    add_editor_available,
    review_add_manifest,
    validate_package_id,
)
from dotman.capture import capture_patch
from dotman.diff_review import (
    ReviewItem,
    build_review_items,
    run_review_item_diff,
)
from dotman.engine import DotmanEngine, TrackedTargetConflictError, parse_binding_text, parse_package_ref_text
from dotman.models import Binding, filter_hook_plans_for_targets, package_ref_text
from dotman.reconcile import run_basic_reconcile
from dotman.reconcile_helpers import run_jinja_reconcile
from dotman.templates import JinjaRenderError, build_template_context, render_template_file, render_template_string
from dotman.snapshot import (
    RollbackAction,
    SnapshotRecord,
    find_snapshot_matches,
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
from dotman.cli_parser import build_parser as build_cli_parser
from dotman import cli_emit, cli_commands


MENU_HEADER_MARKER = cli_style.MENU_HEADER_MARKER
MENU_HEADER_MARKER_STYLE = cli_style.MENU_HEADER_MARKER_STYLE
MENU_INDEX_STYLE = cli_style.MENU_INDEX_STYLE
MENU_PROMPT_STYLE = cli_style.MENU_PROMPT_STYLE
MENU_HINT_STYLE = cli_style.MENU_HINT_STYLE
MENU_REPO_STYLE = cli_style.MENU_REPO_STYLE
MENU_ACTION_STYLE_BY_NAME = cli_style.MENU_ACTION_STYLE_BY_NAME
EXECUTION_STATUS_STYLE_BY_NAME = cli_style.EXECUTION_STATUS_STYLE_BY_NAME
INTERRUPTED_EXIT_CODE = 130
MENU_SELECTION_OVERHEAD_LINES = 6
SelectableItem = TypeVar("SelectableItem")


@dataclass(frozen=True)
class PendingSelectionItem:
    binding_label: str
    package_id: str
    target_name: str
    action: str
    source_path: str
    destination_path: str

def prompt(message: str) -> str:
    sys.stdout.write(message)
    sys.stdout.flush()
    answer = sys.stdin.readline()
    return answer.strip()


def colors_enabled() -> bool:
    return cli_style.colors_enabled()


def style_text(text: str, *codes: str) -> str:
    return cli_style.style_text(text, *codes)


def repo_name_from_binding_label(binding_label: str) -> str:
    return cli_style.repo_name_from_binding_label(binding_label)


def repo_qualified_selector_text(*, repo_name: str, selector: str) -> str:
    return cli_style.repo_qualified_selector_text(repo_name=repo_name, selector=selector)


def package_label_text(
    *,
    repo_name: str,
    package_id: str,
    bound_profile: str | None = None,
    target_name: str | None = None,
    package_first: bool = False,
    include_repo_context: bool = False,
) -> str:
    return cli_style.package_label_text(
        repo_name=repo_name,
        package_id=package_id,
        bound_profile=bound_profile,
        target_name=target_name,
        package_first=package_first,
        include_repo_context=include_repo_context,
    )


def render_package_label(
    *,
    repo_name: str,
    package_id: str,
    bound_profile: str | None = None,
    target_name: str | None = None,
    package_first: bool = False,
    include_repo_context: bool = False,
) -> str:
    return cli_style.render_package_label(
        repo_name=repo_name,
        package_id=package_id,
        bound_profile=bound_profile,
        target_name=target_name,
        package_first=package_first,
        include_repo_context=include_repo_context,
        use_color=colors_enabled(),
    )


def render_package_target_label(*, repo_name: str, package_id: str, target_name: str) -> str:
    return cli_style.render_package_target_label(
        repo_name=repo_name,
        package_id=package_id,
        target_name=target_name,
        use_color=colors_enabled(),
    )


def package_profile_label_text(*, repo_name: str, package_id: str, profile: str) -> str:
    return cli_style.package_profile_label_text(repo_name=repo_name, package_id=package_id, profile=profile)


def render_package_profile_label(*, repo_name: str, package_id: str, profile: str) -> str:
    return cli_style.render_package_profile_label(
        repo_name=repo_name,
        package_id=package_id,
        profile=profile,
        use_color=colors_enabled(),
    )


def binding_label_text(*, repo_name: str, selector: str, profile: str, selector_first: bool = False) -> str:
    return cli_style.binding_label_text(
        repo_name=repo_name,
        selector=selector,
        profile=profile,
        selector_first=selector_first,
    )


def render_binding_label(*, repo_name: str, selector: str, profile: str, selector_first: bool = False) -> str:
    return cli_style.render_binding_label(
        repo_name=repo_name,
        selector=selector,
        profile=profile,
        selector_first=selector_first,
        use_color=colors_enabled(),
    )


def render_binding_reference(binding: Binding) -> str:
    return cli_style.render_binding_reference(binding, use_color=colors_enabled())


def find_remaining_tracked_package_after_untrack(engine: DotmanEngine, binding: Binding):
    try:
        repo = engine.get_repo(binding.repo)
    except ValueError:
        return None
    if binding.selector not in repo.packages:
        return None
    if repo.resolve_package(binding.selector).binding_mode == "multi_instance":
        return None
    try:
        return engine.describe_tracked_package(f"{binding.repo}:{binding.selector}")
    except ValueError:
        return None


def render_tracked_reason(reason: str) -> str:
    return cli_style.render_tracked_reason(reason, use_color=colors_enabled())


def render_tracked_state(state: str) -> str:
    return cli_style.render_tracked_state(state, use_color=colors_enabled())


def render_tracked_issue_label(engine: DotmanEngine, issue) -> str:
    bound_profile: str | None = None
    try:
        repo = engine.get_repo(issue.repo)
    except ValueError:
        repo = None
    if repo is not None and issue.selector in repo.packages:
        package = repo.resolve_package(issue.selector)
        if package.binding_mode == "multi_instance":
            bound_profile = issue.profile
    return render_package_label(
        repo_name=issue.repo,
        package_id=issue.selector,
        bound_profile=bound_profile,
    )


def render_info_section_header(label: str) -> str:
    return cli_style.render_info_section_header(label, use_color=colors_enabled())


def resolve_variable_text(
    engine: DotmanEngine,
    variable_text: str,
    *,
    json_output: bool,
) -> str:
    query_text = variable_text.strip().removeprefix("vars.")
    exact_matches, partial_matches = engine.find_variable_matches(variable_text)
    return resolve_candidate_match(
        exact_matches=exact_matches,
        partial_matches=partial_matches,
        query_text=query_text,
        interactive=interactive_mode_enabled(json_output=json_output),
        exact_header_text=f"Select a variable for '{variable_text}':",
        partial_header_text=f"Select a variable for '{variable_text}':",
        option_resolver=lambda match: ResolverOption(
            display_label=match,
            match_fields=(match,),
            field_kinds=("variable",),
        ),
        exact_error_text=f"variable '{variable_text}' is ambiguous: " + ", ".join(exact_matches),
        partial_error_text=f"variable '{variable_text}' is ambiguous: " + ", ".join(partial_matches),
        not_found_text=f"variable '{variable_text}' did not match any resolved variable",
    )


def format_snapshot_timestamp(timestamp: str | None) -> str:
    return cli_style.format_snapshot_timestamp(timestamp)


def render_snapshot_status(status: str) -> str:
    return cli_style.render_snapshot_status(status, use_color=colors_enabled())


def render_snapshot_ref(snapshot_id: str) -> str:
    return cli_style.render_snapshot_ref(snapshot_id, use_color=colors_enabled())


def render_snapshot_metadata_label(label: str) -> str:
    return cli_style.render_snapshot_metadata_label(label, use_color=colors_enabled())


def render_snapshot_provenance(*, repo_name: str | None, package_id: str | None, target_name: str | None, binding_label: str | None) -> str | None:
    return cli_style.render_snapshot_provenance(
        repo_name=repo_name,
        package_id=package_id,
        target_name=target_name,
        binding_label=binding_label,
        use_color=colors_enabled(),
    )


def render_snapshot_reason(action: str) -> str:
    return cli_style.render_snapshot_reason(action, use_color=colors_enabled())


def render_menu_badge(text: str) -> str:
    return cli_style.render_menu_badge(text, use_color=colors_enabled())


def join_menu_display_fields(*fields: str) -> str:
    return cli_style.join_menu_display_fields(*fields)


def build_selector_match_display_fields(*, repo_name: str, selector: str, selector_kind: str) -> tuple[str, ...]:
    return cli_style.build_selector_match_display_fields(
        repo_name=repo_name,
        selector=selector,
        selector_kind=selector_kind,
        use_color=colors_enabled(),
    )


def render_selector_match_label(*, repo_name: str, selector: str, selector_kind: str) -> str:
    return cli_style.render_selector_match_label(
        repo_name=repo_name,
        selector=selector,
        selector_kind=selector_kind,
        use_color=colors_enabled(),
    )


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


# Use an invisible delimiter plus a literal space so fzf can keep badge fields
# separate from the searchable label while rendering `label [badge]` compactly.
FZF_FIELD_DELIMITER = "\x1f "


def _select_menu_option_with_fzf(
    *,
    header_text: str,
    option_labels: Sequence[str],
    option_search_fields: Sequence[Sequence[str]],
    option_display_fields: Sequence[Sequence[str]] | None = None,
) -> int:
    del option_search_fields
    if option_display_fields is not None and len(option_display_fields) != len(option_labels):
        raise ValueError("fzf display fields must align with option labels")
    display_fields_by_option = [
        tuple(field for field in fields if field)
        for fields in (option_display_fields or [(label,) for label in option_labels])
    ]
    field_count = max((len(fields) for fields in display_fields_by_option), default=1)
    entries = [
        FZF_FIELD_DELIMITER.join(
            [
                str(index),
                *display_fields,
                *("" for _ in range(field_count - len(display_fields))),
            ]
        )
        for index, display_fields in enumerate(display_fields_by_option, start=1)
    ]
    visible_field_range = f"2..{field_count + 1}"
    completed = subprocess.run(
        [
            "fzf",
            "--prompt=Select> ",
            f"--header={header_text}",
            f"--delimiter={FZF_FIELD_DELIMITER}",
            "--ansi",
            "--nth=1",
            f"--with-nth={visible_field_range}",
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
    option_display_fields: Sequence[Sequence[str]] | None = None,
) -> int:
    search_fields = option_search_fields or [(label,) for label in option_labels]
    if _fzf_available() and _should_use_fzf_for_selection(option_labels):
        return _select_menu_option_with_fzf(
            header_text=header_text,
            option_labels=option_labels,
            option_search_fields=search_fields,
            option_display_fields=option_display_fields,
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
    hint_text = '("?", number, "n", "a", "c", "q"; default: next)'
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


def partial_match_confirmation_prompt(*, candidate_label: str) -> str:
    prompt_text = f"Did you mean '{candidate_label}'?"
    hint_text = "[y/N]"
    if not colors_enabled():
        return f"{prompt_text} {hint_text} "
    return (
        f"{style_text(prompt_text, *MENU_PROMPT_STYLE)} "
        f"{style_text(hint_text, *MENU_HINT_STYLE)} "
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


def push_symlink_replacement_prompt() -> str:
    prompt_text = "Replace symlinked live target(s) before push?"
    hint_text = "[y/N]"
    if not colors_enabled():
        return f"{prompt_text} {hint_text} "
    return (
        f"{style_text(MENU_HEADER_MARKER, *MENU_HEADER_MARKER_STYLE)} "
        f"{style_text(prompt_text, *MENU_PROMPT_STYLE)} "
        f"{style_text(hint_text, *MENU_HINT_STYLE)} "
    )


def review_continue_prompt() -> str:
    prompt_text = "Continue?"
    hint_text = "[Y/n]"
    if not colors_enabled():
        return f"{prompt_text} {hint_text} "
    return (
        f"{style_text(MENU_HEADER_MARKER, *MENU_HEADER_MARKER_STYLE)} "
        f"{style_text(prompt_text, *MENU_PROMPT_STYLE)} "
        f"{style_text(hint_text, *MENU_HINT_STYLE)} "
    )


def confirm_review_continue(*, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    while True:
        answer = prompt(review_continue_prompt()).strip().lower()
        if answer in {"", "y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("invalid confirmation: enter 'y' or 'n'", file=sys.stderr)


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
    print("  n          inspect next diff")
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


def find_recorded_bindings_for_scope(engine: DotmanEngine, binding: Binding) -> list[Binding]:
    repo = engine.get_repo(binding.repo)
    existing_by_scope = {
        binding_replacement_scope(engine, existing): existing
        for existing in engine.read_effective_bindings(repo)
    }
    matches: list[Binding] = []
    for expanded_binding in engine.expand_binding_for_tracking(binding):
        existing = existing_by_scope.get(binding_replacement_scope(engine, expanded_binding))
        if existing is not None and existing not in matches:
            matches.append(existing)
    return matches


def find_recorded_binding_for_scope(engine: DotmanEngine, binding: Binding) -> Binding | None:
    matches = find_recorded_bindings_for_scope(engine, binding)
    return matches[0] if len(matches) == 1 else None


def find_recorded_binding_exact(engine: DotmanEngine, binding: Binding) -> Binding | None:
    repo = engine.get_repo(binding.repo)
    expanded_bindings = engine.expand_binding_for_tracking(binding)
    if len(expanded_bindings) != 1:
        return None
    expanded_binding = expanded_bindings[0]
    for existing in engine.read_effective_bindings(repo):
        if (
            existing.repo == expanded_binding.repo
            and existing.selector == expanded_binding.selector
            and existing.profile == expanded_binding.profile
        ):
            return existing
    return None


def confirm_tracked_binding_replacement(
    *,
    existing_binding: Binding,
    replacement_binding: Binding,
    assume_yes: bool = False,
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
    if assume_yes:
        return True
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
    assume_yes: bool = False,
) -> bool:
    expanded_bindings = engine.expand_binding_for_tracking(binding)
    existing_bindings = find_recorded_bindings_for_scope(engine, binding)
    replacements = [
        (existing_binding, expanded_binding)
        for expanded_binding in expanded_bindings
        for existing_binding in existing_bindings
        if binding_replacement_scope(engine, existing_binding) == binding_replacement_scope(engine, expanded_binding)
        and existing_binding.profile != expanded_binding.profile
    ]
    if not replacements:
        return True
    if assume_yes:
        if len(replacements) == 1:
            existing_binding, replacement_binding = replacements[0]
            return confirm_tracked_binding_replacement(
                existing_binding=existing_binding,
                replacement_binding=replacement_binding,
                assume_yes=True,
            )
        print_selection_header(f"Confirm tracked binding replacements for {binding.repo}:{binding.selector}@{binding.profile}:")
        for existing_binding, replacement_binding in replacements:
            print(f"  existing: {render_binding_reference(existing_binding)}")
            print(f"  new:      {render_binding_reference(replacement_binding)}")
        return True
    if len(replacements) == 1:
        existing_binding, replacement_binding = replacements[0]
        if not interactive_mode_enabled(json_output=json_output):
            raise ValueError(
                f"refusing to replace tracked binding '{existing_binding.repo}:{existing_binding.selector}@"
                f"{existing_binding.profile}' with '{replacement_binding.repo}:{replacement_binding.selector}@{replacement_binding.profile}' "
                "in non-interactive mode"
            )
        return confirm_tracked_binding_replacement(
            existing_binding=existing_binding,
            replacement_binding=replacement_binding,
            assume_yes=False,
        )
    replacement_labels = ", ".join(
        f"{existing.repo}:{existing.selector}@{existing.profile} -> {replacement.repo}:{replacement.selector}@{replacement.profile}"
        for existing, replacement in replacements
    )
    if not interactive_mode_enabled(json_output=json_output):
        raise ValueError(
            f"refusing to replace tracked bindings for '{binding.repo}:{binding.selector}@{binding.profile}' "
            f"in non-interactive mode: {replacement_labels}"
        )
    print_selection_header(f"Confirm tracked binding replacements for {binding.repo}:{binding.selector}@{binding.profile}:")
    for existing_binding, replacement_binding in replacements:
        print(f"  existing: {render_binding_reference(existing_binding)}")
        print(f"  new:      {render_binding_reference(replacement_binding)}")
    while True:
        answer = prompt(confirmation_prompt()).strip().lower()
        if answer in {"", "n", "no"}:
            return False
        if answer in {"y", "yes"}:
            return True
        print("invalid confirmation: enter 'y' or 'n'", file=sys.stderr)


def confirm_partial_candidate_match(*, candidate_label: str) -> bool:
    while True:
        answer = prompt(partial_match_confirmation_prompt(candidate_label=candidate_label)).strip().lower()
        if answer in {"", "n", "no"}:
            return False
        if answer in {"y", "yes"}:
            return True
        print("invalid confirmation: enter 'y' or 'n'", file=sys.stderr)


def confirm_track_binding_implicit_overrides(*, binding: Binding, overrides: Sequence, assume_yes: bool = False) -> bool:
    binding_label = f"{binding.repo}:{binding.selector}@{binding.profile}"
    print_selection_header(f"Confirm explicit override for {binding_label}:")
    print("  this explicit binding will replace implicitly tracked package owners:")
    for override in overrides:
        print(f"    new: {override.winner.binding_label} ({override.winner.package_id})")
        for contender in override.overridden:
            print(f"      implicit: {contender.binding_label} ({contender.package_id})")
    if assume_yes:
        return True
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
    assume_yes: bool = False,
) -> bool:
    overrides = engine.preview_binding_implicit_overrides(binding)
    if not overrides:
        return True
    if assume_yes:
        return confirm_track_binding_implicit_overrides(
            binding=binding,
            overrides=overrides,
            assume_yes=True,
        )
    if not interactive_mode_enabled(json_output=json_output):
        raise ValueError(
            f"refusing to let '{binding.repo}:{binding.selector}@{binding.profile}' explicitly override implicitly tracked targets "
            "in non-interactive mode"
        )
    return confirm_track_binding_implicit_overrides(binding=binding, overrides=overrides)


def confirm_add_manifest_write(*, repo_name: str, package_id: str, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    while True:
        answer = prompt(
            write_manifest_confirmation_prompt(repo_name=repo_name, package_id=package_id)
        ).strip().lower()
        if answer in {"", "n", "no"}:
            return False
        if answer in {"y", "yes"}:
            return True
        print("invalid confirmation: enter 'y' or 'n'", file=sys.stderr)


def confirm_push_symlink_replacement(*, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    while True:
        answer = prompt(push_symlink_replacement_prompt()).strip().lower()
        if answer in {"", "n", "no"}:
            return False
        if answer in {"y", "yes"}:
            return True
        print("invalid confirmation: enter 'y' or 'n'", file=sys.stderr)


def prompt_for_conflicting_package_binding(
    engine: DotmanEngine,
    *,
    binding: Binding,
    conflict: TrackedTargetConflictError,
    json_output: bool,
) -> Binding | None:
    if conflict.precedence != "implicit" or not interactive_mode_enabled(json_output=json_output):
        return None
    candidate_bindings = set(engine.expand_binding_for_tracking(binding))
    package_ids = sorted(
        {
            candidate.package_id
            for candidate in conflict.candidates
            if candidate.binding in candidate_bindings
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
    if not answer or answer == "n":
        return "next", None
    if answer == "c":
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
        resolved_options = [option_resolver(match) for match in ranked_exact_matches]
        selected_index = select_menu_option(
            header_text=exact_header_text,
            option_labels=[option.display_label for option in resolved_options],
            option_search_fields=[
                build_fzf_search_fields(match_fields=option.match_fields)
                for option in resolved_options
            ],
            option_display_fields=[option.display_fields or (option.display_label,) for option in resolved_options],
        )
        return ranked_exact_matches[selected_index]
    if len(partial_matches) == 1:
        partial_match = ranked_partial_matches[0]
        partial_option = option_resolver(partial_match)
        if not interactive:
            raise ValueError(
                f"no exact match for '{query_text}'; use exact name '{partial_option.display_label}'"
            )
        if not confirm_partial_candidate_match(candidate_label=partial_option.display_label):
            raise ValueError(f"confirmation required for partial match '{query_text}'")
        return partial_match
    if len(partial_matches) > 1:
        if not interactive:
            raise ValueError(partial_error_text)
        resolved_options = [option_resolver(match) for match in ranked_partial_matches]
        selected_index = select_menu_option(
            header_text=partial_header_text,
            option_labels=[option.display_label for option in resolved_options],
            option_search_fields=[
                build_fzf_search_fields(match_fields=option.match_fields)
                for option in resolved_options
            ],
            option_display_fields=[option.display_fields or (option.display_label,) for option in resolved_options],
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
            display_fields=build_selector_match_display_fields(
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
) -> tuple[object | None, Binding]:
    explicit_repo, selector, profile = parse_binding_text(binding_text)
    interactive = interactive_mode_enabled(json_output=json_output)
    binding_label = selector if profile is None else f"{selector}@{profile}"

    if operation == "untrack":
        resolved_selector, _resolved_profile, exact_matches, partial_matches = engine.find_persisted_binding_matches(binding_text)
        package_matches, owner_bindings = engine._tracked_package_matches_for_untrack(
            selector=resolved_selector,
            profile=profile,
            repo_name=explicit_repo,
        )

        def persisted_option(record) -> ResolverOption:
            base_label = render_binding_label(
                repo_name=record.binding.repo,
                selector=record.binding.selector,
                profile=record.binding.profile,
                selector_first=True,
            )
            state_badge = ""
            if record.repo is None or record.state_key != record.binding.repo:
                state_badge = render_menu_badge(f"[{record.state_key}]")
            return ResolverOption(
                display_label=join_menu_display_fields(base_label, state_badge),
                display_fields=(base_label, state_badge) if state_badge else (base_label,),
                match_fields=build_binding_match_fields(
                    repo_name=record.binding.repo,
                    selector=record.binding.selector,
                    profile=record.binding.profile,
                ),
                field_kinds=build_binding_field_kinds(),
            )

        def package_option(package) -> ResolverOption:
            display_label = render_package_label(
                repo_name=package.repo,
                package_id=package.package_id,
                bound_profile=package.bound_profile,
                package_first=True,
                include_repo_context=True,
            )
            return ResolverOption(
                display_label=display_label,
                match_fields=build_package_match_fields(
                    repo_name=package.repo,
                    package_id=package.package_id,
                    bound_profile=package.bound_profile,
                ),
                field_kinds=build_package_field_kinds(has_bound_profile=package.bound_profile is not None),
            )

        def package_owner_error(package) -> ValueError:
            matching_owner_bindings = [
                binding
                for binding in package.bindings
                if profile is None or binding.profile == profile
            ]
            owners = ", ".join(
                render_binding_label(
                    repo_name=binding.repo,
                    selector=binding.selector,
                    profile=binding.profile,
                    selector_first=True,
                )
                for binding in matching_owner_bindings
            )
            required_repo = explicit_repo or package.repo
            required_ref = render_package_label(
                repo_name=required_repo,
                package_id=package.package_id,
                bound_profile=package.bound_profile,
                package_first=True,
                include_repo_context=True,
            )
            return ValueError(
                f"cannot {operation} '{required_ref}': required by tracked bindings: {owners}"
            )

        filtered_package_matches = [
            package
            for package in package_matches
            if not any(
                record.binding.repo == package.repo and record.binding.selector == package.package_id
                for record in partial_matches
            )
        ]

        def combined_option(match) -> ResolverOption:
            match_kind, item = match
            if match_kind == "binding":
                return persisted_option(item)
            return package_option(item)

        if interactive and (exact_matches or partial_matches or filtered_package_matches):
            selected_kind, selected_item = resolve_candidate_match(
                exact_matches=[("binding", record) for record in exact_matches],
                partial_matches=[("binding", record) for record in partial_matches]
                + [("package", package) for package in filtered_package_matches],
                query_text=binding_label,
                interactive=True,
                exact_header_text=f"Select a tracked binding for '{binding_label}':",
                partial_header_text=(
                    f"Select an untrack target for '{binding_label}':"
                    if filtered_package_matches
                    else f"Select a tracked binding for '{binding_label}':"
                ),
                option_resolver=combined_option,
                exact_error_text="unused",
                partial_error_text="unused",
                not_found_text=f"binding '{binding_label}' is not currently tracked",
            )
            if selected_kind == "binding":
                return selected_item.repo, selected_item.binding
            raise package_owner_error(selected_item)

        if len(exact_matches) == 1:
            record = exact_matches[0]
            return record.repo, record.binding
        if len(exact_matches) > 1:
            raise ValueError(
                f"binding '{binding_label}' is ambiguous: "
                + ", ".join(
                    f"{record.binding.repo}:{record.binding.selector}@{record.binding.profile}"
                    for record in exact_matches
                )
            )

        if partial_matches:
            if filtered_package_matches:
                package_candidates = ", ".join(
                    f"{package.repo}:{package.package_ref}"
                    for package in filtered_package_matches
                )
                raise ValueError(
                    f"binding '{binding_label}' is ambiguous: tracked packages: {package_candidates}"
                )
            if len(partial_matches) == 1:
                record = partial_matches[0]
                raise ValueError(
                    f"no exact match for '{binding_label}'; use exact name '{persisted_option(record).display_label}'"
                )
            raise ValueError(
                f"binding '{binding_label}' is ambiguous: "
                + ", ".join(
                    f"{record.binding.repo}:{record.binding.selector}@{record.binding.profile}"
                    for record in partial_matches
                )
            )

        if filtered_package_matches:
            if len(filtered_package_matches) > 1:
                raise ValueError(
                    f"binding '{binding_label}' is ambiguous: tracked packages: "
                    + ", ".join(
                        f"{package.repo}:{package.package_ref}" for package in filtered_package_matches
                    )
                )
            raise package_owner_error(filtered_package_matches[0])

        raise ValueError(f"binding '{binding_label}' is not currently tracked")

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

    package_matches, _package_owner_bindings = engine._tracked_package_matches_for_untrack(
        selector=resolved_selector,
        profile=resolved_profile,
        repo_name=lookup_repo,
    )
    direct_binding_match_keys = {
        (repo.config.name, binding.selector, binding.profile)
        for repo, binding in [*exact_matches, *partial_matches]
    }
    owner_target_matches: list[tuple[object, str, Binding]] = []
    seen_owner_target_matches: set[tuple[str, str, str, str]] = set()
    for package in package_matches:
        repo = engine.get_repo(package.repo)
        for owner_binding in package.bindings:
            if resolved_profile is not None and owner_binding.profile != resolved_profile:
                continue
            if (package.repo, package.package_id, owner_binding.profile) in direct_binding_match_keys:
                continue
            owner_match_key = (
                package.repo,
                package.package_id,
                owner_binding.profile,
                owner_binding.selector,
            )
            if owner_match_key in seen_owner_target_matches:
                continue
            seen_owner_target_matches.add(owner_match_key)
            owner_target_matches.append(
                (
                    repo,
                    package.package_id,
                    Binding(
                        repo=owner_binding.repo,
                        selector=owner_binding.selector,
                        profile=owner_binding.profile,
                    ),
                )
            )

    owner_exact_matches = [match for match in owner_target_matches if match[1] == resolved_selector]
    owner_partial_matches = [match for match in owner_target_matches if match[1] != resolved_selector]

    def owner_target_resolver(match) -> ResolverOption:
        owner_repo, package_id, owner_binding = match
        target_label = render_package_profile_label(
            repo_name=owner_repo.config.name,
            package_id=package_id,
            profile=owner_binding.profile,
        )
        owner_label = binding_label_text(
            repo_name=owner_repo.config.name,
            selector=owner_binding.selector,
            profile=owner_binding.profile,
            selector_first=True,
        )
        owner_badge = render_menu_badge(f"[via {owner_label}]")
        return ResolverOption(
            display_label=target_label,
            display_fields=(target_label, owner_badge),
            match_fields=build_binding_match_fields(
                repo_name=owner_repo.config.name,
                selector=package_id,
                profile=owner_binding.profile,
            ),
            field_kinds=build_binding_field_kinds(),
        )

    def owner_target_error_label(match) -> str:
        owner_repo, package_id, owner_binding = match
        return (
            f"{owner_repo.config.name}:{package_id}@{owner_binding.profile}"
            f" via {owner_repo.config.name}:{owner_binding.selector}@{owner_binding.profile}"
        )

    def binding_from_owner_match(match) -> tuple[object, Binding]:
        owner_repo, package_id, owner_binding = match
        return owner_repo, Binding(
            repo=owner_repo.config.name,
            selector=package_id,
            profile=owner_binding.profile,
        )

    if allow_package_owners and not exact_matches and (partial_matches or owner_exact_matches or owner_partial_matches):
        # Tracked package targets can be selected through owner bindings. Combine them
        # with partial tracked-binding hits so ambiguous user input goes through the
        # normal resolver instead of silently preferring one path.
        def combined_resolver(match) -> ResolverOption:
            match_kind, item = match
            if match_kind == "binding":
                return binding_resolver(item)
            return owner_target_resolver(item)

        combined_exact_matches = [(
            "owner", match
        ) for match in owner_exact_matches] if not partial_matches else []
        combined_partial_matches = [("binding", match) for match in partial_matches] + [
            ("owner", match)
            for match in ([*owner_exact_matches, *owner_partial_matches] if partial_matches else owner_partial_matches)
        ]
        selected_kind, selected_item = resolve_candidate_match(
            exact_matches=combined_exact_matches,
            partial_matches=combined_partial_matches,
            query_text=binding_label,
            interactive=interactive,
            exact_header_text=f"Select a tracked binding for '{binding_label}':",
            partial_header_text=f"Select a tracked binding for '{binding_label}':",
            option_resolver=combined_resolver,
            exact_error_text=f"binding '{binding_label}' is ambiguous: "
            + ", ".join(owner_target_error_label(match) for match in owner_exact_matches),
            partial_error_text=f"binding '{binding_label}' is ambiguous: "
            + ", ".join(
                [
                    *(
                        f"{repo.config.name}:{binding.selector}@{binding.profile}"
                        for repo, binding in partial_matches
                    ),
                    *(
                        owner_target_error_label(match)
                        for match in ([*owner_exact_matches, *owner_partial_matches] if partial_matches else owner_partial_matches)
                    ),
                ]
            ),
            not_found_text=f"binding '{binding_label}' is not currently tracked",
        )
        if selected_kind == "binding":
            return selected_item
        return binding_from_owner_match(selected_item)

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
            return binding_from_owner_match((owner_repo, owner_binding))
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
            cli_emit.emit_error(exc, use_color=sys.stderr.isatty() and os.environ.get("NO_COLOR") is None)
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
    diff_badge = "[diff unavailable]" if item.diff_unavailable_reason is not None else None
    source_path = display_cli_path(item.source_path, full_paths=full_paths)
    destination_path = display_cli_path(item.destination_path, full_paths=full_paths)
    if not colors_enabled():
        item_text = f"[{item.action}] {package_target}"
        if diff_badge is not None:
            item_text += f" {diff_badge}"
        item_text += f": {source_path} -> {destination_path}"
        print(f"  {index:>2}) {item_text}")
        return

    action_style = MENU_ACTION_STYLE_BY_NAME.get(item.action, ("1",))
    action_text = style_text(f"[{item.action}]", *action_style)
    package_label = render_package_target_label(
        repo_name=repo_name,
        package_id=item.package_id,
        target_name=item.target_name,
    )
    badge_text = f" {style_text(diff_badge, *MENU_HINT_STYLE)}" if diff_badge is not None else ""
    arrow_text = style_text("->", *MENU_HINT_STYLE)
    print(
        f"  {style_text(f'{index:>2})', *MENU_INDEX_STYLE)} "
        f"{action_text} {package_label}{badge_text}: "
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
    prefix_text = style_text(f"Diff {index}/{total}:", *MENU_HINT_STYLE)
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
    assume_yes: bool = False,
) -> bool:
    print_selection_header(f"Review pending diffs for {operation}:")
    for index, item in enumerate(review_items, start=1):
        print_review_item(index, item, full_paths=full_paths)

    last_viewed_index: int | None = None
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
            if review_items:
                last_viewed_index = len(review_items) - 1
            continue
        if command_name == "next":
            selected_index = 0 if last_viewed_index is None else last_viewed_index + 1
            if selected_index >= len(review_items):
                if confirm_review_continue(assume_yes=assume_yes):
                    return True
                continue
        if selected_index is None:
            print("invalid selection: missing review item", file=sys.stderr)
            continue
        if command_name in {"inspect", "next"}:
            last_viewed_index = selected_index
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
    assume_yes: bool = False,
) -> bool:
    if not interactive_mode_enabled(json_output=json_output):
        return True
    review_items = build_review_items(plans, operation=operation)
    if not review_items:
        return True
    return run_diff_review_menu(review_items, operation=operation, full_paths=full_paths, assume_yes=assume_yes)


def _push_symlink_hazard_description(hazard: cli_emit.PushSymlinkHazard, *, full_paths: bool) -> str:
    live_path = cli_emit.display_cli_path(hazard.live_path, full_paths=full_paths)
    symlink_target = hazard.symlink_target or "<unknown>"
    return f"{hazard.binding_label} {hazard.package_id}:{hazard.target_name} ({live_path} -> {symlink_target})"


def prepare_push_plans_for_execution(
    *,
    plans: Sequence,
    json_output: bool,
    full_paths: bool = False,
    assume_yes: bool = False,
) -> list | None:
    hazards = cli_emit.collect_push_live_symlink_hazards(plans)
    if not hazards:
        return list(plans)

    interactive = interactive_mode_enabled(json_output=json_output)
    if interactive:
        cli_emit.print_push_live_symlink_hazard_warning(hazards, use_color=colors_enabled(), full_paths=full_paths)

    hazard_descriptions = ", ".join(
        _push_symlink_hazard_description(hazard, full_paths=full_paths)
        for hazard in hazards
    )
    unsupported = [hazard for hazard in hazards if not hazard.replaceable]
    if unsupported:
        unsupported_descriptions = ", ".join(
            _push_symlink_hazard_description(hazard, full_paths=full_paths)
            for hazard in unsupported
        )
        if interactive:
            raise ValueError(
                f"refusing to replace unsupported symlinked directory target(s): {unsupported_descriptions}"
            )
        raise ValueError(f"refusing to replace symlinked live target(s) in non-interactive mode: {hazard_descriptions}")

    if assume_yes:
        return cli_emit.allow_push_live_symlink_replacements(plans)
    if not interactive:
        raise ValueError(f"refusing to replace symlinked live target(s) in non-interactive mode: {hazard_descriptions}")

    if not confirm_push_symlink_replacement(assume_yes=assume_yes):
        return None
    return cli_emit.allow_push_live_symlink_replacements(plans)


def emit_interrupt_notice() -> None:
    sys.stderr.write("\ninterrupted\n")


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
        raise JinjaRenderError(path=path, detail="source path does not exist")
    context = build_template_context(
        variables,
        profile=profile or os.environ.get("DOTMAN_PROFILE") or "default",
        inferred_os=inferred_os or os.environ.get("DOTMAN_OS") or sys.platform,
    )
    rendered, _projection_kind = render_template_file(path, context)
    sys.stdout.write(rendered.decode("utf-8"))
    return 0



def run_patch_capture(
    *,
    repo_path: str,
    review_repo_path: str | None,
    review_live_path: str | None,
    profile: str | None,
    inferred_os: str | None,
    var_assignments: Sequence[str],
) -> int:
    resolved_repo_path = Path(repo_path).expanduser().resolve()
    variables = _template_vars_from_dotman_env(dict(os.environ))
    _apply_template_var_assignments(variables, var_assignments)
    context = build_template_context(
        variables,
        profile=profile or os.environ.get("DOTMAN_PROFILE") or "default",
        inferred_os=inferred_os or os.environ.get("DOTMAN_OS") or sys.platform,
    )
    captured = capture_patch(
        repo_path=resolved_repo_path,
        review_repo_path=review_repo_path,
        review_live_path=review_live_path,
        project_repo_bytes=lambda candidate_bytes: render_template_string(
            candidate_bytes.decode("utf-8"),
            context,
            base_dir=resolved_repo_path.parent,
            source_path=resolved_repo_path,
        ).encode("utf-8"),
    )
    sys.stdout.buffer.write(captured)
    return 0



def build_parser():
    return build_cli_parser()


effective_execution_mode = cli_emit.effective_execution_mode


def display_cli_path(reference_path: Path | str, *, full_paths: bool) -> str:
    return cli_emit.display_cli_path(reference_path, full_paths=full_paths)


def emit_payload(*, operation: str, plans: Sequence, json_output: bool, mode: str, full_paths: bool = False) -> int:
    return cli_emit.emit_payload(
        operation=operation,
        plans=plans,
        json_output=json_output,
        mode=mode,
        full_paths=full_paths,
        use_color=colors_enabled(),
        collect_pending_selection_items_for_operation=collect_pending_selection_items_for_operation,
    )


emit_execution_result = cli_emit.emit_execution_result



def execute_plans(
    *,
    operation: str,
    plans: Sequence,
    json_output: bool,
    full_paths: bool = False,
    run_noop: bool = False,
    assume_yes: bool = False,
):
    return cli_emit.execute_plans(
        operation=operation,
        plans=plans,
        json_output=json_output,
        full_paths=full_paths,
        use_color=colors_enabled(),
        run_noop=run_noop,
        assume_yes=assume_yes,
    )



def run_execution(
    *,
    operation: str,
    plans: Sequence,
    json_output: bool,
    full_paths: bool = False,
    run_noop: bool = False,
    assume_yes: bool = False,
) -> int:
    return cli_emit.run_execution(
        operation=operation,
        plans=plans,
        json_output=json_output,
        full_paths=full_paths,
        use_color=colors_enabled(),
        run_noop=run_noop,
        assume_yes=assume_yes,
    )



def emit_tracked_packages(*, engine: DotmanEngine, packages: Sequence, invalid_bindings: Sequence, json_output: bool) -> int:
    return cli_emit.emit_tracked_packages(
        engine=engine,
        packages=packages,
        invalid_bindings=invalid_bindings,
        json_output=json_output,
        use_color=colors_enabled(),
    )


def emit_variables(*, variables: Sequence, json_output: bool) -> int:
    return cli_emit.emit_variables(
        variables=variables,
        json_output=json_output,
        use_color=colors_enabled(),
    )


def emit_variable_detail(*, variable_detail, json_output: bool) -> int:
    return cli_emit.emit_variable_detail(
        variable_detail=variable_detail,
        json_output=json_output,
        use_color=colors_enabled(),
    )



def emit_forgotten_binding(*, binding, still_tracked_package, json_output: bool) -> int:
    return cli_emit.emit_forgotten_binding(
        binding=binding,
        still_tracked_package=still_tracked_package,
        json_output=json_output,
        use_color=colors_enabled(),
    )



def emit_tracked_binding(*, binding, json_output: bool) -> int:
    return cli_emit.emit_tracked_binding(
        binding=binding,
        json_output=json_output,
        use_color=colors_enabled(),
    )



def emit_add_result(*, result, json_output: bool) -> int:
    return cli_emit.emit_add_result(
        result=result,
        json_output=json_output,
        use_color=colors_enabled(),
    )



def emit_kept_add_result(*, repo_name: str, package_id: str, json_output: bool) -> int:
    return cli_emit.emit_kept_add_result(
        repo_name=repo_name,
        package_id=package_id,
        json_output=json_output,
        use_color=colors_enabled(),
    )


emit_noop_add_result = cli_emit.emit_noop_add_result



def emit_kept_binding(*, binding, json_output: bool) -> int:
    return cli_emit.emit_kept_binding(
        binding=binding,
        json_output=json_output,
        use_color=colors_enabled(),
    )



def emit_skipped_tracking(*, binding, json_output: bool) -> int:
    return cli_emit.emit_skipped_tracking(
        binding=binding,
        json_output=json_output,
        use_color=colors_enabled(),
    )


render_hook_command_lines = cli_emit.render_hook_command_lines



def emit_tracked_package_detail(*, package_detail, json_output: bool) -> int:
    return cli_emit.emit_tracked_package_detail(
        package_detail=package_detail,
        json_output=json_output,
        use_color=colors_enabled(),
    )


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


visible_rollback_actions = cli_emit.visible_rollback_actions
build_rollback_review_items = cli_emit.build_rollback_review_items



def review_rollback_actions_for_interactive_diffs(
    *,
    snapshot: SnapshotRecord,
    actions: Sequence[RollbackAction],
    json_output: bool,
    full_paths: bool = False,
    assume_yes: bool = False,
) -> bool:
    if not interactive_mode_enabled(json_output=json_output):
        return True
    review_items = build_rollback_review_items(snapshot, actions)
    if not review_items:
        return True
    return run_diff_review_menu(review_items, operation="rollback", full_paths=full_paths, assume_yes=assume_yes)



def emit_snapshot_list(
    *,
    snapshots: Sequence[SnapshotRecord],
    json_output: bool,
    max_generations: int | None = None,
) -> int:
    return cli_emit.emit_snapshot_list(
        snapshots=snapshots,
        json_output=json_output,
        max_generations=max_generations,
        use_color=colors_enabled(),
    )



def emit_snapshot_detail(*, snapshot: SnapshotRecord, json_output: bool, full_paths: bool = False) -> int:
    return cli_emit.emit_snapshot_detail(
        snapshot=snapshot,
        json_output=json_output,
        full_paths=full_paths,
        use_color=colors_enabled(),
    )



def emit_rollback_payload(
    *,
    snapshot: SnapshotRecord,
    actions: Sequence[RollbackAction],
    json_output: bool,
    mode: str,
    full_paths: bool = False,
) -> int:
    return cli_emit.emit_rollback_payload(
        snapshot=snapshot,
        actions=actions,
        json_output=json_output,
        mode=mode,
        full_paths=full_paths,
        use_color=colors_enabled(),
    )


emit_rollback_result = cli_emit.emit_rollback_result



def run_rollback_execution(
    *,
    snapshot: SnapshotRecord,
    actions: Sequence[RollbackAction],
    json_output: bool,
    full_paths: bool = False,
) -> int:
    return cli_emit.run_rollback_execution(
        snapshot=snapshot,
        actions=actions,
        json_output=json_output,
        full_paths=full_paths,
        use_color=colors_enabled(),
    )


def _build_command_handlers() -> cli_commands.CliCommandHandlers:
    return cli_commands.CliCommandHandlers(
        run_basic_reconcile=run_basic_reconcile,
        run_jinja_reconcile=run_jinja_reconcile,
        run_jinja_render=run_jinja_render,
        run_patch_capture=run_patch_capture,
        resolve_binding_text=resolve_binding_text,
        ensure_track_binding_replacement_confirmed=ensure_track_binding_replacement_confirmed,
        find_recorded_bindings_for_scope=find_recorded_bindings_for_scope,
        emit_kept_binding=emit_kept_binding,
        emit_skipped_tracking=emit_skipped_tracking,
        prompt_for_conflicting_package_binding=prompt_for_conflicting_package_binding,
        select_non_conflicting_track_profile=select_non_conflicting_track_profile,
        ensure_track_binding_implicit_overrides_confirmed=ensure_track_binding_implicit_overrides_confirmed,
        find_recorded_binding_exact=find_recorded_binding_exact,
        emit_tracked_binding=emit_tracked_binding,
        resolve_add_package_text=resolve_add_package_text,
        interactive_mode_enabled=interactive_mode_enabled,
        add_editor_available=add_editor_available,
        review_add_manifest=review_add_manifest,
        confirm_add_manifest_write=confirm_add_manifest_write,
        emit_add_result=emit_add_result,
        emit_noop_add_result=emit_noop_add_result,
        emit_kept_add_result=emit_kept_add_result,
        resolve_tracked_binding_text=resolve_tracked_binding_text,
        filter_plans_for_interactive_selection=filter_plans_for_interactive_selection,
        review_plans_for_interactive_diffs=review_plans_for_interactive_diffs,
        emit_interrupt_notice=emit_interrupt_notice,
        interrupted_exit_code=INTERRUPTED_EXIT_CODE,
        emit_payload=emit_payload,
        effective_execution_mode=effective_execution_mode,
        prepare_push_plans_for_execution=prepare_push_plans_for_execution,
        execute_plans=execute_plans,
        emit_execution_result=emit_execution_result,
        run_execution=run_execution,
        resolve_snapshot_record=resolve_snapshot_record,
        review_rollback_actions_for_interactive_diffs=review_rollback_actions_for_interactive_diffs,
        emit_rollback_payload=emit_rollback_payload,
        run_rollback_execution=run_rollback_execution,
        emit_forgotten_binding=emit_forgotten_binding,
        find_remaining_tracked_package_after_untrack=find_remaining_tracked_package_after_untrack,
        emit_tracked_packages=emit_tracked_packages,
        resolve_tracked_package_text=resolve_tracked_package_text,
        emit_tracked_package_detail=emit_tracked_package_detail,
        resolve_variable_text=resolve_variable_text,
        emit_variables=emit_variables,
        emit_variable_detail=emit_variable_detail,
        emit_snapshot_list=emit_snapshot_list,
        emit_snapshot_detail=emit_snapshot_detail,
    )



def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(list(argv) if argv is not None else None)
        return cli_commands.dispatch_command(
            args=args,
            engine_factory=lambda config_path: DotmanEngine.from_config_path(
                config_path,
                file_symlink_mode=args.file_symlink_mode,
                dir_symlink_mode=args.dir_symlink_mode,
            ),
            handlers=_build_command_handlers(),
        )
    except KeyboardInterrupt:
        emit_interrupt_notice()
        return INTERRUPTED_EXIT_CODE
    except ValueError as exc:
        cli_emit.emit_error(exc, use_color=sys.stderr.isatty() and os.environ.get("NO_COLOR") is None)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
