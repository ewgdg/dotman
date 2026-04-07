from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence

from dotman.diff_review import (
    ReviewItem,
    build_review_items,
    diff_status as review_diff_status,
    run_review_item_diff,
)
from dotman.engine import DotmanEngine, parse_binding_text
from dotman.reconcile import run_basic_reconcile


ANSI_RESET = "\033[0m"
MENU_HEADER_MARKER = "::"
MENU_HEADER_MARKER_STYLE = ("1", "34")
MENU_INDEX_STYLE = ("1", "36")
MENU_PROMPT_STYLE = ("1",)
MENU_HINT_STYLE = ("2",)
MENU_REPO_STYLE = ("2", "34")
INTERRUPTED_EXIT_CODE = 130
MENU_ACTION_STYLE_BY_NAME: dict[str, tuple[str, ...]] = {
    "create": ("1", "32"),
    "update": ("1", "36"),
    "delete": ("1", "31"),
}


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
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def style_text(text: str, *codes: str) -> str:
    if not codes:
        return text
    return f"\033[{';'.join(codes)}m{text}{ANSI_RESET}"


def repo_name_from_binding_label(binding_label: str) -> str:
    return binding_label.split(":", 1)[0]


def package_target_text(*, repo_name: str, package_id: str, target_name: str) -> str:
    return f"{repo_name}/{package_id} ({target_name})"


def render_package_target_label(*, repo_name: str, package_id: str, target_name: str) -> str:
    if not colors_enabled():
        return package_target_text(repo_name=repo_name, package_id=package_id, target_name=target_name)
    return (
        f"{style_text(repo_name, *MENU_REPO_STYLE)}"
        f"{style_text('/', *MENU_HINT_STYLE)}"
        f"{style_text(package_id, '1')} "
        f"{style_text(f'({target_name})', *MENU_HINT_STYLE)}"
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


def select_menu_option(*, header_text: str, option_labels: Sequence[str]) -> int:
    print_selection_header(header_text)
    for index, option_label in enumerate(option_labels, start=1):
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


def resolve_binding_text(
    engine: DotmanEngine,
    binding_text: str,
    *,
    json_output: bool,
) -> tuple[str, str]:
    explicit_repo, selector, selector_profile = parse_binding_text(binding_text)
    exact_matches, partial_matches = engine.find_selector_matches(selector, explicit_repo)
    interactive = interactive_mode_enabled(json_output=json_output)

    if len(exact_matches) == 1:
        repo, resolved_selector, _selector_kind = exact_matches[0]
    elif len(exact_matches) > 1:
        if not interactive:
            candidates = ", ".join(f"{repo.config.name}:{match}" for repo, match, _ in exact_matches)
            raise ValueError(f"selector '{selector}' is defined in multiple repos: {candidates}")
        selected_index = select_menu_option(
            header_text=f"Select a repo for exact selector '{selector}':",
            option_labels=[f"{repo.config.name}:{match} [{kind}]" for repo, match, kind in exact_matches],
        )
        repo, resolved_selector, _selector_kind = exact_matches[selected_index]
    elif len(partial_matches) == 1:
        repo, resolved_selector, _selector_kind = partial_matches[0]
    elif len(partial_matches) > 1:
        if not interactive:
            candidates = ", ".join(f"{repo.config.name}:{match}" for repo, match, _ in partial_matches)
            raise ValueError(f"selector '{selector}' is ambiguous: {candidates}")
        selected_index = select_menu_option(
            header_text=f"Select a selector match for '{selector}':",
            option_labels=[f"{repo.config.name}:{match} [{kind}]" for repo, match, kind in partial_matches],
        )
        repo, resolved_selector, _selector_kind = partial_matches[selected_index]
    else:
        raise ValueError(f"selector '{selector}' did not match any package or group")

    resolved_profile = selector_profile
    if not resolved_profile:
        available_profiles = engine.list_profiles(repo.config.name)
        if not available_profiles:
            raise ValueError(f"repo '{repo.config.name}' does not define any profiles")
        if len(available_profiles) == 1:
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


def print_pending_selection_item(index: int, item: PendingSelectionItem) -> None:
    repo_name = repo_name_from_binding_label(item.binding_label)
    package_target = package_target_text(
        repo_name=repo_name,
        package_id=item.package_id,
        target_name=item.target_name,
    )
    if not colors_enabled():
        item_text = (
            f"[{item.action}] {package_target}: "
            f"{item.source_path} -> {item.destination_path}"
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
        f"{action_text} {package_label}: {item.source_path} {arrow_text} {item.destination_path}"
    )


def prompt_for_excluded_items(selection_items: Sequence[PendingSelectionItem], *, operation: str) -> set[int]:
    print_selection_header(f"Select items to exclude from {operation}:")
    for index, item in enumerate(selection_items, start=1):
        print_pending_selection_item(index, item)
    while True:
        try:
            answer = prompt(pending_selection_prompt())
            if answer.strip() == "?":
                print_pending_selection_help()
                continue
            return parse_selection_indexes(answer, len(selection_items))
        except ValueError as exc:
            print(f"invalid selection: {exc}", file=sys.stderr)


def filter_plans_for_interactive_selection(*, plans: Sequence, operation: str, json_output: bool) -> list:
    if not interactive_mode_enabled(json_output=json_output):
        return list(plans)
    selection_items = collect_pending_selection_items_for_operation(plans, operation=operation)
    if not selection_items:
        return list(plans)
    excluded_indexes = prompt_for_excluded_items(selection_items, operation=operation)
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
        filtered_plans.append(replace(plan, target_plans=filtered_targets))
    return filtered_plans


def print_review_item(index: int, item: ReviewItem) -> None:
    repo_name = repo_name_from_binding_label(item.binding_label)
    package_target = package_target_text(
        repo_name=repo_name,
        package_id=item.package_id,
        target_name=item.target_name,
    )
    diff_text = review_diff_status(item)
    if not colors_enabled():
        item_text = (
            f"[{item.action}] {package_target} "
            f"[{diff_text}]: {item.source_path} -> {item.destination_path}"
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
        f"{item.source_path} {arrow_text} {item.destination_path}"
    )


def review_diff_header(review_item: ReviewItem, *, index: int, total: int) -> str:
    return (
        f"Diff {index}/{total}: "
        f"{package_target_text(
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


def run_diff_review_menu(review_items: Sequence[ReviewItem], *, operation: str) -> bool:
    print_selection_header(f"Review pending diffs for {operation}:")
    for index, item in enumerate(review_items, start=1):
        print_review_item(index, item)
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


def review_plans_for_interactive_diffs(*, plans: Sequence, operation: str, json_output: bool) -> bool:
    if not interactive_mode_enabled(json_output=json_output):
        return True
    review_items = build_review_items(plans, operation=operation)
    if not review_items:
        return True
    return run_diff_review_menu(review_items, operation=operation)


def emit_interrupt_notice() -> None:
    sys.stderr.write("\ninterrupted\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="dotman CLI")
    parser.add_argument("--config", help="Path to dotman config.toml")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Emit machine-readable JSON")

    subparsers = parser.add_subparsers(dest="command", required=True)

    track_parser = subparsers.add_parser("track")
    track_parser.add_argument("binding")

    push_parser = subparsers.add_parser("push")
    push_parser.add_argument("binding", nargs="?")

    pull_parser = subparsers.add_parser("pull")
    pull_parser.add_argument("binding", nargs="?")

    untrack_parser = subparsers.add_parser("untrack")
    untrack_parser.add_argument("binding")

    forget_parser = subparsers.add_parser("forget")
    forget_parser.add_argument("binding")

    list_parser = subparsers.add_parser("list")
    list_subparsers = list_parser.add_subparsers(dest="list_command", required=True)
    list_subparsers.add_parser("installed")

    info_parser = subparsers.add_parser("info")
    info_subparsers = info_parser.add_subparsers(dest="info_command", required=True)
    info_installed_parser = info_subparsers.add_parser("installed")
    info_installed_parser.add_argument("package")

    reconcile_parser = subparsers.add_parser("reconcile")
    reconcile_subparsers = reconcile_parser.add_subparsers(dest="reconcile_command", required=True)

    reconcile_editor_parser = reconcile_subparsers.add_parser("editor")
    reconcile_editor_parser.add_argument("--repo-path", required=True)
    reconcile_editor_parser.add_argument("--live-path", required=True)
    reconcile_editor_parser.add_argument("--review-repo-path")
    reconcile_editor_parser.add_argument("--review-live-path")
    reconcile_editor_parser.add_argument("--additional-source", action="append", default=[])
    reconcile_editor_parser.add_argument("--editor")
    return parser


def emit_payload(*, operation: str, plans: Sequence, json_output: bool) -> int:
    visible_plans = []
    for plan in plans:
        visible_targets = [target for target in plan.target_plans if target.action != "noop"]
        if not visible_targets:
            continue
        visible_plans.append(replace(plan, target_plans=visible_targets))
    payload = {
        "mode": "dry-run",
        "operation": operation,
        "bindings": [plan.to_dict() for plan in visible_plans],
    }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    for plan in visible_plans:
        for target in plan.target_plans:
            print(f"{target.package_id}:{target.target_name} -> {target.action}")
    return 0


def emit_installed_packages(*, packages: Sequence, json_output: bool) -> int:
    payload = {
        "mode": "dry-run",
        "operation": "list-installed",
        "packages": [package.to_dict() for package in packages],
    }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    for package in packages:
        bindings = ", ".join(f"{binding.repo}:{binding.selector}@{binding.profile}" for binding in package.bindings)
        print(f"{package.repo}:{package.package_id} [{bindings}]")
    return 0


def emit_forgotten_binding(*, binding, json_output: bool) -> int:
    payload = {
        "mode": "state-only",
        "operation": "untrack",
        "binding": {
            "repo": binding.repo,
            "selector": binding.selector,
            "profile": binding.profile,
        },
    }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print(f"untracked {binding.repo}:{binding.selector}@{binding.profile}")
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

    print(f"tracked {binding.repo}:{binding.selector}@{binding.profile}")
    return 0


def emit_installed_package_detail(*, package_detail, json_output: bool) -> int:
    payload = {
        "mode": "dry-run",
        "operation": "info-installed",
        "package": package_detail.to_dict(),
    }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print(f"{package_detail.repo}:{package_detail.package_id}")
    if package_detail.description:
        print(f"  {package_detail.description}")
    for binding in package_detail.bindings:
        print(f"  {binding.binding.repo}:{binding.binding.selector}@{binding.binding.profile}")
        for target in binding.targets:
            print(f"    {target.target_name} -> {target.live_path}")
    return 0


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
        engine = DotmanEngine.from_config_path(args.config)
        if args.command == "track":
            binding_text, profile = resolve_binding_text(engine, args.binding, json_output=args.json_output)
            _repo, binding, _selector_kind = engine.resolve_binding(binding_text, profile=profile)
            engine.record_binding(binding)
            return emit_tracked_binding(binding=binding, json_output=args.json_output)
        if args.command == "push":
            if args.binding:
                _repo, binding = engine.resolve_tracked_binding(
                    args.binding,
                    operation="push",
                    allow_package_owners=True,
                )
                binding_text = f"{binding.repo}:{binding.selector}"
                plan = engine.plan_push_binding(binding_text, profile=binding.profile)
                filtered_plans = filter_plans_for_interactive_selection(
                    plans=[plan],
                    operation="push",
                    json_output=args.json_output,
                )
                if not review_plans_for_interactive_diffs(
                    plans=filtered_plans,
                    operation="push",
                    json_output=args.json_output,
                ):
                    emit_interrupt_notice()
                    return INTERRUPTED_EXIT_CODE
                plan = filtered_plans[0]
                return emit_payload(operation="push", plans=[plan], json_output=args.json_output)
            plans = filter_plans_for_interactive_selection(
                plans=engine.plan_push(),
                operation="push",
                json_output=args.json_output,
            )
            if not review_plans_for_interactive_diffs(
                plans=plans,
                operation="push",
                json_output=args.json_output,
            ):
                emit_interrupt_notice()
                return INTERRUPTED_EXIT_CODE
            return emit_payload(
                operation="push",
                plans=plans,
                json_output=args.json_output,
            )
        if args.command == "pull":
            if args.binding:
                _repo, binding = engine.resolve_tracked_binding(
                    args.binding,
                    operation="pull",
                    allow_package_owners=True,
                )
                binding_text = f"{binding.repo}:{binding.selector}"
                profile = binding.profile
                plans = filter_plans_for_interactive_selection(
                    plans=[engine.plan_pull_binding(binding_text, profile=profile)],
                    operation="pull",
                    json_output=args.json_output,
                )
                if not review_plans_for_interactive_diffs(
                    plans=plans,
                    operation="pull",
                    json_output=args.json_output,
                ):
                    emit_interrupt_notice()
                    return INTERRUPTED_EXIT_CODE
                return emit_payload(
                    operation="pull",
                    plans=plans,
                    json_output=args.json_output,
                )
            plans = filter_plans_for_interactive_selection(
                plans=engine.plan_pull(),
                operation="pull",
                json_output=args.json_output,
            )
            if not review_plans_for_interactive_diffs(
                plans=plans,
                operation="pull",
                json_output=args.json_output,
            ):
                emit_interrupt_notice()
                return INTERRUPTED_EXIT_CODE
            return emit_payload(
                operation="pull",
                plans=plans,
                json_output=args.json_output,
            )
        if args.command in {"untrack", "forget"}:
            return emit_forgotten_binding(
                binding=engine.remove_binding(args.binding, operation="untrack"),
                json_output=args.json_output,
            )
        if args.command == "list" and args.list_command == "installed":
            return emit_installed_packages(packages=engine.list_installed_packages(), json_output=args.json_output)
        if args.command == "info" and args.info_command == "installed":
            return emit_installed_package_detail(
                package_detail=engine.describe_installed_package(args.package),
                json_output=args.json_output,
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
