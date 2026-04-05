from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from dotman.engine import DotmanEngine
from dotman.reconcile import run_basic_reconcile


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="dotman CLI")
    parser.add_argument("--config", help="Path to dotman config.toml")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Emit machine-readable JSON")

    subparsers = parser.add_subparsers(dest="command", required=True)

    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("binding")

    import_parser = subparsers.add_parser("import")
    import_parser.add_argument("binding")

    subparsers.add_parser("upgrade")

    remove_parser = subparsers.add_parser("remove")
    remove_subparsers = remove_parser.add_subparsers(dest="remove_command", required=True)
    remove_binding_parser = remove_subparsers.add_parser("binding")
    remove_binding_parser.add_argument("binding")

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
    reconcile_editor_parser.add_argument("--additional-source", action="append", default=[])
    reconcile_editor_parser.add_argument("--editor")
    return parser


def emit_payload(*, operation: str, plans: Sequence, json_output: bool) -> int:
    payload = {
        "mode": "dry-run",
        "operation": operation,
        "bindings": [plan.to_dict() for plan in plans],
    }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    for plan in plans:
        print(f"{plan.binding.repo}:{plan.binding.selector}@{plan.binding.profile}")
        for target in plan.target_plans:
            print(f"  {target.package_id}:{target.target_name} -> {target.action}")
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


def emit_removed_binding(*, binding, json_output: bool) -> int:
    payload = {
        "mode": "state-only",
        "operation": "remove-binding",
        "binding": {
            "repo": binding.repo,
            "selector": binding.selector,
            "profile": binding.profile,
        },
    }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print(f"removed {binding.repo}:{binding.selector}@{binding.profile}")
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
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "reconcile" and args.reconcile_command == "editor":
            return run_basic_reconcile(
                repo_path=args.repo_path,
                live_path=args.live_path,
                additional_sources=args.additional_source,
                editor=args.editor,
            )
        engine = DotmanEngine.from_config_path(args.config)
        if args.command == "apply":
            plan = engine.plan_apply(args.binding)
            engine.record_binding(plan.binding)
            return emit_payload(operation="apply", plans=[plan], json_output=args.json_output)
        if args.command == "import":
            return emit_payload(operation="import", plans=[engine.plan_import(args.binding)], json_output=args.json_output)
        if args.command == "upgrade":
            return emit_payload(operation="upgrade", plans=engine.plan_upgrade(), json_output=args.json_output)
        if args.command == "remove" and args.remove_command == "binding":
            return emit_removed_binding(binding=engine.remove_binding(args.binding), json_output=args.json_output)
        if args.command == "list" and args.list_command == "installed":
            return emit_installed_packages(packages=engine.list_installed_packages(), json_output=args.json_output)
        if args.command == "info" and args.info_command == "installed":
            return emit_installed_package_detail(
                package_detail=engine.describe_installed_package(args.package),
                json_output=args.json_output,
            )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
