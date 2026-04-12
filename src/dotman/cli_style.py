from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

from dotman.engine import parse_binding_text
from dotman.models import Binding, package_ref_text


ANSI_RESET = "\033[0m"
MENU_HEADER_MARKER = "::"
MENU_HEADER_MARKER_STYLE = ("1", "34")
MENU_INDEX_STYLE = ("1", "36")
MENU_PROMPT_STYLE = ("1",)
MENU_HINT_STYLE = ("2",)
MENU_REPO_STYLE = ("2", "34")
TRACKED_STATE_STYLE_BY_NAME: dict[str, tuple[str, ...]] = {
    "explicit": ("2",),
    "implicit": ("2",),
    "orphan": ("2", "33"),
    "invalid": ("2", "31"),
}
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
    use_color: bool,
) -> str:
    package_ref = package_ref_text(package_id=package_id, bound_profile=bound_profile)
    if not use_color:
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


def render_package_target_label(*, repo_name: str, package_id: str, target_name: str, use_color: bool) -> str:
    return render_package_label(
        repo_name=repo_name,
        package_id=package_id,
        target_name=target_name,
        use_color=use_color,
    )


def package_profile_label_text(*, repo_name: str, package_id: str, profile: str) -> str:
    return f"{repo_qualified_selector_text(repo_name=repo_name, selector=package_id)}@{profile}"


def render_package_profile_label(*, repo_name: str, package_id: str, profile: str, use_color: bool) -> str:
    if not use_color:
        return package_profile_label_text(repo_name=repo_name, package_id=package_id, profile=profile)
    return (
        f"{style_text(repo_name, *MENU_REPO_STYLE)}"
        f"{style_text(':', *MENU_HINT_STYLE)}"
        f"{style_text(package_id, '1')}"
        f"{style_text(f'@{profile}', *MENU_HINT_STYLE)}"
    )


def binding_label_text(*, repo_name: str, selector: str, profile: str, selector_first: bool = False) -> str:
    return f"{repo_qualified_selector_text(repo_name=repo_name, selector=selector)}@{profile}"


def render_binding_label(*, repo_name: str, selector: str, profile: str, selector_first: bool = False, use_color: bool) -> str:
    if not use_color:
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


def render_binding_reference(binding: Binding, *, use_color: bool) -> str:
    return render_binding_label(
        repo_name=binding.repo,
        selector=binding.selector,
        profile=binding.profile,
        use_color=use_color,
    )


def render_tracked_reason(reason: str, *, use_color: bool) -> str:
    if not use_color:
        return reason
    return style_text(reason, *MENU_HINT_STYLE)


def render_tracked_state(state: str, *, use_color: bool) -> str:
    if not use_color:
        return state
    return style_text(state, *TRACKED_STATE_STYLE_BY_NAME.get(state, MENU_HINT_STYLE))


def render_info_section_header(label: str, *, use_color: bool) -> str:
    if not use_color:
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


def render_snapshot_status(status: str, *, use_color: bool) -> str:
    if not use_color:
        return status
    return style_text(status, *SNAPSHOT_STATUS_STYLE_BY_NAME.get(status, ("1",)))


def render_snapshot_ref(snapshot_id: str, *, use_color: bool) -> str:
    if not use_color:
        return snapshot_id
    return style_text(snapshot_id, "1")


def render_snapshot_metadata_label(label: str, *, use_color: bool) -> str:
    if not use_color:
        return label
    return style_text(label, *MENU_HINT_STYLE)


def render_snapshot_provenance(
    *,
    repo_name: str | None,
    package_id: str | None,
    target_name: str | None,
    binding_label: str | None,
    use_color: bool,
) -> str | None:
    if repo_name is None or package_id is None or target_name is None:
        return binding_label
    profile = None
    if binding_label is not None:
        binding_repo, _binding_selector, binding_profile = parse_binding_text(binding_label)
        if binding_repo is not None:
            repo_name = binding_repo
        profile = binding_profile
    if profile is not None:
        return render_package_profile_label(
            repo_name=repo_name,
            package_id=package_id,
            profile=profile,
            use_color=use_color,
        ) + (f" {style_text(f'({target_name})', *MENU_HINT_STYLE)}" if use_color else f" ({target_name})")
    return render_package_target_label(
        repo_name=repo_name,
        package_id=package_id,
        target_name=target_name,
        use_color=use_color,
    )


def render_payload_section_label(label: str, *, use_color: bool) -> str:
    if not use_color:
        return label
    return style_text(label, *MENU_HINT_STYLE)


def render_payload_hook_label(hook_name: str, *, use_color: bool) -> str:
    hook_label = hook_name.replace("_", " ")
    if not use_color:
        return hook_label
    return style_text(hook_label, *MENU_HINT_STYLE)


def render_payload_action(action: str, *, use_color: bool) -> str:
    if not use_color:
        return action
    return style_text(action, *MENU_ACTION_STYLE_BY_NAME.get(action, ("1",)))


def render_snapshot_reason(action: str, *, use_color: bool) -> str:
    if not use_color:
        return f"before {action} (push)"
    return f"before {render_payload_action(action, use_color=True)} {style_text('(push)', *MENU_HINT_STYLE)}"


def render_menu_badge(text: str, *, use_color: bool) -> str:
    if not use_color:
        return text
    return style_text(text, *MENU_HINT_STYLE)


def join_menu_display_fields(*fields: str) -> str:
    visible_fields = [field for field in fields if field]
    if not visible_fields:
        return ""
    return visible_fields[0] + "".join(f" {field}" for field in visible_fields[1:])


def build_selector_match_display_fields(*, repo_name: str, selector: str, selector_kind: str, use_color: bool) -> tuple[str, ...]:
    return (
        render_package_label(
            repo_name=repo_name,
            package_id=selector,
            package_first=True,
            include_repo_context=True,
            use_color=use_color,
        ),
        render_menu_badge(f"[{selector_kind}]", use_color=use_color),
    )


def render_selector_match_label(*, repo_name: str, selector: str, selector_kind: str, use_color: bool) -> str:
    return join_menu_display_fields(
        *build_selector_match_display_fields(
            repo_name=repo_name,
            selector=selector,
            selector_kind=selector_kind,
            use_color=use_color,
        )
    )


def render_summary_stat(*, label: str, value: int, use_color: bool) -> str:
    if not use_color:
        return f"{label}: {value}"
    return f"{style_text(f'{label}:', *MENU_HINT_STYLE)} {style_text(str(value), '1')}"


def render_execution_action(action: str, *, use_color: bool) -> str:
    display_action = action.replace("_repo", " repo") if action.endswith("_repo") else action
    if not use_color:
        return display_action
    style_key = action.removesuffix("_repo")
    return style_text(display_action, *MENU_ACTION_STYLE_BY_NAME.get(style_key, ("1",)))


def render_execution_status(status: str, *, use_color: bool) -> str:
    if not use_color:
        return status
    return style_text(status, *EXECUTION_STATUS_STYLE_BY_NAME.get(status, ("1",)))
