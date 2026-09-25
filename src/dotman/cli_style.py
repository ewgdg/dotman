from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Sequence

from dotman.package_resolution import parse_full_spec_selector_text
from dotman.models import FullSpecSelector, ResolvedPackageIdentity, package_ref_text, repo_qualified_target_text, target_ref_text


ANSI_RESET = "\033[0m"
MENU_HEADER_MARKER = "::"
MENU_HEADER_MARKER_STYLE = ("1", "34")
MENU_INDEX_STYLE = ("1", "36")
MENU_PROMPT_STYLE = ("1",)
MENU_HINT_STYLE = ("2",)
MENU_REPO_STYLE = ("2", "34")
# Target segment needs stronger contrast than repo + separator, and should not
# reuse cyan already used for indices/update actions.
MENU_TARGET_STYLE = ("2", "33")
TRACKED_STATE_STYLE_BY_NAME: dict[str, tuple[str, ...]] = {
    "explicit": ("2",),
    "implicit": ("2",),
    "orphan": ("2", "33"),
    "invalid": ("2", "31"),
}
MENU_ACTION_STYLE_BY_NAME: dict[str, tuple[str, ...]] = {
    "create": ("1", "32"),
    "update": ("1", "36"),
    "chmod": ("1", "36"),
    "delete": ("1", "31"),
    "install": ("1", "36"),
    "probe": ("1", "36"),
    "acknowledge": ("2", "36"),
}
EXECUTION_STATUS_STYLE_BY_NAME: dict[str, tuple[str, ...]] = {
    "ok": ("1", "32"),
    "failed": ("1", "31"),
    "interrupted": ("1", "31"),
    "skipped": ("1", "33"),
}
SNAPSHOT_STATUS_STYLE_BY_NAME: dict[str, tuple[str, ...]] = {
    "prepared": ("1", "33"),
    "applied": ("1", "32"),
    "failed": ("1", "31"),
}
SUDO_BADGE_STYLE = ("1", "33")


def colors_enabled() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def style_text(text: str, *codes: str) -> str:
    if not codes:
        return text
    return f"\033[{';'.join(codes)}m{text}{ANSI_RESET}"


def repo_name_from_selection_label(selection_label: str) -> str:
    return selection_label.split(":", 1)[0]


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
    if package_first and not include_repo_context:
        return target_ref_text(package_id=package_id, target_name=target_name, bound_profile=bound_profile)
    return repo_qualified_target_text(
        repo_name=repo_name,
        package_id=package_id,
        target_name=target_name,
        bound_profile=bound_profile,
    )


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
    return (
        f"{package_label}"
        f"{style_text('.', *MENU_HINT_STYLE)}"
        f"{style_text(target_name, *MENU_TARGET_STYLE)}"
    )


def render_package_target_label(
    *,
    repo_name: str,
    package_id: str,
    target_name: str,
    bound_profile: str | None = None,
    use_color: bool,
) -> str:
    return render_package_label(
        repo_name=repo_name,
        package_id=package_id,
        bound_profile=bound_profile,
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


def full_spec_selector_label_text(*, repo_name: str, selector: str, profile: str, selector_first: bool = False) -> str:
    return f"{repo_qualified_selector_text(repo_name=repo_name, selector=selector)}@{profile}"


def render_full_spec_selector_label(*, repo_name: str, selector: str, profile: str, selector_first: bool = False, use_color: bool) -> str:
    if not use_color:
        return full_spec_selector_label_text(
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


def render_full_spec_selector_reference(package_entry: FullSpecSelector, *, use_color: bool) -> str:
    return render_full_spec_selector_label(
        repo_name=package_entry.repo,
        selector=package_entry.selector,
        profile=package_entry.profile,
        use_color=use_color,
    )


def render_package_identity_label(identity: ResolvedPackageIdentity, *, use_color: bool) -> str:
    return render_package_label(
        repo_name=identity.repo,
        package_id=identity.package_id,
        bound_profile=identity.bound_profile,
        use_color=use_color,
    )


def render_full_spec_selector_label_text(selection_label: str, *, use_color: bool) -> str:
    repo_name, selector, profile = parse_full_spec_selector_text(selection_label)
    if repo_name is None or profile is None:
        return selection_label
    return render_full_spec_selector_label(
        repo_name=repo_name,
        selector=selector,
        profile=profile,
        use_color=use_color,
    )


def render_profile_conflict_contender(contender: str, *, use_color: bool) -> str:
    selection_label, separator, owner_label = contender.partition(" required by ")
    rendered_selection = render_full_spec_selector_label_text(selection_label, use_color=use_color)
    if not separator:
        return rendered_selection
    return (
        f"{rendered_selection} "
        "required by "
        f"{render_full_spec_selector_label_text(owner_label, use_color=use_color)}"
    )


def render_profile_conflict_detail(error: object, *, use_color: bool) -> str:
    package_identity = getattr(error, "package_identity", None)
    if package_identity is None:
        return str(error)

    conflict_kind = getattr(error, "conflict_kind", "")
    if conflict_kind == "ambiguous_implicit":
        header = "ambiguous implicit profile contexts for"
    elif conflict_kind == "conflicting_explicit":
        header = "conflicting explicit profile contexts for"
    else:
        header = "conflicting profile contexts for"

    package_label = render_package_identity_label(package_identity, use_color=use_color)
    contenders = tuple(getattr(error, "contenders", ()))
    return "\n".join(
        [
            f"{header} {package_label}:",
            *(f"  {render_profile_conflict_contender(contender, use_color=use_color)}" for contender in contenders),
        ]
    )


def render_variable_name(variable_name: str, *, use_color: bool) -> str:
    if not use_color:
        return variable_name
    return style_text(variable_name, "1")


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


def render_error_metadata_label(label: str, *, use_color: bool) -> str:
    return render_snapshot_metadata_label(label, use_color=use_color)


def render_snapshot_provenance(
    *,
    repo_name: str | None,
    package_id: str | None,
    target_name: str | None,
    selection_label: str | None,
    use_color: bool,
) -> str | None:
    if repo_name is None or package_id is None or target_name is None:
        return selection_label
    if selection_label is not None:
        binding_repo, _binding_selector, _binding_profile = parse_full_spec_selector_text(selection_label)
        if binding_repo is not None:
            repo_name = binding_repo
    return render_package_target_label(
        repo_name=repo_name,
        package_id=package_id,
        target_name=target_name,
        use_color=use_color,
    )


def render_annotation_parentheses(annotation_text: str, *, use_color: bool) -> str:
    if not annotation_text:
        return ""
    annotation = f" ({annotation_text})"
    if not use_color:
        return annotation
    return style_text(annotation, *MENU_HINT_STYLE)


def render_payload_section_label(label: str, *, use_color: bool) -> str:
    if not use_color:
        return label
    return style_text(label, *MENU_HINT_STYLE)


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


def render_sudo_badge(*, use_color: bool) -> str:
    if not use_color:
        return "[sudo]"
    return style_text("[sudo]", *SUDO_BADGE_STYLE)


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


def render_key_hints(hints: Sequence[tuple[str, str]], *, use_color: bool) -> str:
    """Render `key action` pairs with bold keys and dimmed actions and separators."""
    if not use_color:
        return " · ".join(f"{key} {action}" for key, action in hints)
    return style_text(" · ", *MENU_HINT_STYLE).join(
        f"{style_text(key, *MENU_PROMPT_STYLE)} {style_text(action, *MENU_HINT_STYLE)}" for key, action in hints
    )


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


# Shared semantic colors for Sync's Approval, Resolution and completion terms.
SYNC_TERM_STYLE_BY_NAME: dict[str, tuple[str, ...]] = {
    "selected": ("1", "32"),
    "unselected": ("2",),
    # Auxiliary kinds share the Resolution column but are not decisions; keep
    # them recessive so actual Resolutions stand out.
    "Probe Work": ("2",),
    "Directory Root Work": ("2",),
    "Additional Source Change": ("2",),
    "Hook Work": ("2",),
    "Guard skipped": ("2",),
    "Link replacement authorized": ("1", "32"),
    "Link replacement requires authorization": ("33",),
    "approved": ("1", "32"),
    "unapproved": ("2",),
    "Use repository": ("1", "36"),
    "Use live": ("1", "36"),
    "Merge": ("1", "36"),
    "Edited": ("1", "36"),
    "Fallback": ("33",),
    "completed": ("1", "32"),
    "incomplete": ("1", "33"),
    "aborted": ("1", "31"),
    "converged": ("1", "32"),
    "directly-in-sync": ("2", "32"),
    "drifted": ("33",),
    "observation-failed": ("1", "31"),
    "diagnostic": ("1", "31"),
    "error": ("1", "31"),
    "warning": ("33",),
    "blocked": ("1", "31"),
    "conflict": ("1", "31"),
    "Unsupported": ("33",),
    "In sync": ("2", "32"),
    "Observation failed": ("1", "31"),
    "Proposal failed": ("1", "31"),
    "failed": ("1", "31"),
    "pending": ("33",),
    "would-converge": ("36",),
    "would-apply": ("36",),
    "applied": ("1", "32"),
    "ok": ("1", "32"),
    "skipped": ("2",),
    "execution-failed": ("1", "31"),
    "not-converged": ("1", "33"),
    "usable": ("1", "32"),
    "unavailable": ("33",),
    "not applicable": ("2",),
    "not-applicable": ("2",),
    "reset": ("1", "32"),
    "already_absent": ("2",),
    "unattempted": ("2",),
    "interrupted": ("1", "31"),
}


def render_sync_term(term: str, *, use_color: bool) -> str:
    return style_text(term, *SYNC_TERM_STYLE_BY_NAME.get(term, ())) if use_color else term


# Unified-diff line kinds keyed by prefix; longer prefixes are checked first.
DIFF_LINE_STYLE_BY_PREFIX: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("+++", ("1",)),
    ("---", ("1",)),
    ("@@", ("36",)),
    ("old mode", ("2",)),
    ("new mode", ("2",)),
    ("\\", ("2",)),
    ("+", ("32",)),
    ("-", ("31",)),
)


# zdiff3 conflict regions, colored like common merge editors (e.g. VS Code): current
# (repository) side green, incoming (Capture) side blue, common ancestor dimmed.
CONFLICT_REGION_BY_MARKER = {"<<<<<<<": "repository", "|||||||": "base", "=======": "capture", ">>>>>>>": None}
CONFLICT_STYLE_BY_REGION = {"repository": ("32",), "base": ("2",), "capture": ("34",), None: ()}


def render_conflict_lines(lines: Sequence[str], *, use_color: bool) -> list[str]:
    """Style zdiff3 merge output by conflict side; marker lines are bold in their side's color."""
    if not use_color:
        return list(lines)
    rendered, region = [], None
    for line in lines:
        marker = line[:7]
        if marker in CONFLICT_REGION_BY_MARKER and line[7:8] in ("", " "):
            codes = ("1", *CONFLICT_STYLE_BY_REGION[region if marker == ">>>>>>>" else CONFLICT_REGION_BY_MARKER[marker]])
            region = CONFLICT_REGION_BY_MARKER[marker]
            rendered.append(style_text(line, *codes))
        else:
            rendered.append(style_text(line, *CONFLICT_STYLE_BY_REGION[region]))
    return rendered


def render_diff_line(line: str, *, use_color: bool) -> tuple[str, str]:
    """Split a unified-diff line into its one-column marker and styled content."""
    marker, content = line[:1], line[1:]
    if not use_color:
        return marker, content
    codes = next((codes for prefix, codes in DIFF_LINE_STYLE_BY_PREFIX if line.startswith(prefix)), ())
    return (style_text(marker, *codes), style_text(content, *codes)) if codes else (marker, content)
