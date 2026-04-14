from __future__ import annotations

import argparse


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


def add_jinja_context_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--profile",
        metavar="<profile>",
        help="Profile value to expose in template context",
    )
    parser.add_argument(
        "--os",
        dest="template_os",
        metavar="<os>",
        help="OS value to expose in template context",
    )
    parser.add_argument(
        "--var",
        action="append",
        default=[],
        metavar="<key=value>",
        help="Additional template var assignment using dotted keys",
    )


def hide_subparser_from_help(subparsers, name: str) -> None:
    # Argparse has no public API for parseable-but-hidden subcommands.
    subparsers._choices_actions = [
        action for action in subparsers._choices_actions if getattr(action, "dest", None) != name
    ]


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

    capture_parser = subparsers.add_parser(
        "capture",
        help="Patch review content back into repo source",
        description="Patch review content back into repo source",
    )
    capture_subparsers = capture_parser.add_subparsers(
        dest="capture_command",
        required=True,
        title="capture commands",
        metavar="<capture-command>",
    )
    capture_patch_parser = capture_subparsers.add_parser(
        "patch",
        help="Patch a rendered Jinja source file from review content",
        description="Patch a rendered Jinja source file from review content",
    )
    capture_patch_parser.add_argument(
        "--repo-path",
        required=True,
        metavar="<repo-path>",
        help="Path to the repo source file",
    )
    capture_patch_parser.add_argument(
        "--review-repo-path",
        metavar="<review-repo-path>",
        help="Prepared repo-side review file path",
    )
    capture_patch_parser.add_argument(
        "--review-live-path",
        metavar="<review-live-path>",
        help="Prepared live-side review file path",
    )
    add_jinja_context_arguments(capture_patch_parser)

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
    add_jinja_context_arguments(render_jinja_parser)
    return parser
