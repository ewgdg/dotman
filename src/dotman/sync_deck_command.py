"""Thin Sync CLI adapter; the session owns Approval and frozen execution."""

from __future__ import annotations

import json
import sys

from dotman.cli_style import render_sync_term
from dotman.sync_base_store import FilePresent
from dotman.sync_session import (
    CommandRejected, PrepareProposalReview, Preview, SessionOpenFailed,
    SetApproval, SyncSession,
)
from dotman.ui_context import ui_config_scope


def approve(session: SyncSession, row_id: str, approved: bool):
    view = session.view
    return session.dispatch(SetApproval(view.session_id, view.revision, row_id, approved))


def review(session: SyncSession, row_id: str):
    view = session.view
    return session.dispatch(PrepareProposalReview(view.session_id, view.revision, row_id))


class SyncDeckCommandRunner:
    command_names = frozenset({"sync"})

    def __init__(self, *, engine_factory, use_color: bool) -> None:
        self._engine_factory = engine_factory
        self._use_color = use_color

    def run(self, args) -> int:
        interactive = (
            not args.json_output and not args.unattended
            and sys.stdin.isatty() and sys.stdout.isatty()
        )
        if not interactive and not args.unattended:
            self._emit(args, None, None, diagnostic={
                "code": "unattended-decision",
                "message": "Sync requires a terminal or explicit --unattended.",
            })
            return 1
        try:
            engine = self._engine_factory(args.config)
            scope = engine.resolve_sync_scope(args.scopes)
        except ValueError as exc:
            self._emit(args, None, None, diagnostic={
                "code": "invalid-input", "message": str(exc),
            })
            return 2
        except (KeyboardInterrupt, InterruptedError):
            self._emit(args, None, None, diagnostic={
                "code": "interrupted", "message": "Sync preflight interrupted",
            })
            return 130
        with ui_config_scope(engine.config.ui):
            opened = engine.open_sync_session(
                scope, preview=args.dry_run,
            )
            if isinstance(opened, SessionOpenFailed):
                self._emit(args, None, None, diagnostic={
                    "code": opened.diagnostic.code, "message": opened.diagnostic.message,
                })
                return 130 if opened.diagnostic.code == "interrupted" else 1
            with opened as session:
                if interactive:
                    from dotman.sync_deck import run_command_deck
                    try:
                        confirmed = run_command_deck(session, use_color=self._use_color)
                    except (KeyboardInterrupt, InterruptedError):
                        confirmed = False
                    if not confirmed:
                        aborted = session.abort()
                        self._emit(args, session, aborted.result, diagnostic={
                            "code": "interrupted", "message": "Sync aborted",
                        })
                        return 130
                elif args.unattended:
                    for row in session.view.rows:
                        if "set-approval" in row.allowed_commands:
                            approve(session, row.row_id, True)

                # Unattended failures must not permit a partially understood
                # workset to mutate unrelated units.
                blocked = any(row.kind == "diagnostic" or row.diagnostics for row in session.view.rows)
                if not interactive and blocked:
                    interrupted = any(
                        item.code == "interrupted"
                        for row in session.view.rows
                        for item in (*row.observation.diagnostics, *row.diagnostics)
                    )
                    self._emit(args, session, None, diagnostic={
                        "code": "interrupted", "message": "Sync materialization interrupted",
                    } if interrupted else None)
                    return 130 if interrupted else 1
                if not interactive and any(
                    row.included and row.kind == "drift"
                    and "set-approval" not in row.allowed_commands
                    for row in session.view.rows
                ):
                    self._emit(args, session, None, diagnostic={
                        "code": "unattended-decision",
                        "message": "Participating drift has no supported automatic resolution.",
                    })
                    return 1
                if args.dry_run:
                    view = session.view
                    dispatched = session.dispatch(Preview(view.session_id, view.revision))
                else:
                    dispatched = session.execute()
                if isinstance(dispatched, CommandRejected):
                    self._emit(args, session, None, diagnostic={
                        "code": "command-rejected", "message": dispatched.reason,
                    })
                    return 1
                self._emit(args, session, dispatched.result)
                return dispatched.result.exit_code

    def _emit(self, args, session: SyncSession | None, result, *, diagnostic=None) -> None:
        payload = sync_document(args, session, result, diagnostic=diagnostic)
        if args.json_output:
            print(json.dumps(payload))
            return
        print(":: Sync preview" if args.dry_run else ":: Sync")
        for unit in payload["sync_units"]:
            selection = "approved" if unit["approved"] else "unapproved"
            print(f"  [{render_sync_term(selection, use_color=self._use_color)}] {unit['identity']}")
            if unit["resolution_intent"]:
                print(f"      {render_sync_term(resolution_label(unit['resolution_intent']), use_color=self._use_color)}")
            if unit["primary_source_change"]:
                print(f"      repository {unit['primary_source_change']['kind']}")
            for effect in unit["effects"]:
                print(f"      {effect['kind']}")
            for item in unit["diagnostics"]:
                print(f"      {item['message']}")
            if unit["result"]:
                print(f"      {render_sync_term(unit['result'], use_color=self._use_color)}")
        for item in payload["summary"]["diagnostics"]:
            print(item["message"], file=sys.stderr)


def sync_document(args, session, result, *, diagnostic=None) -> dict:
    """Project only public result metadata; frozen payload bytes never leave the session."""
    view = session.view if session is not None else None
    rows = {row.row_id: row for row in view.rows} if view else {}
    outcomes = {unit.identity: unit for unit in result.units} if result else {}
    units = []
    for observation in view.observations if view else ():
        identity = observation.identity.canonical
        row = rows.get(identity)
        proposal = row.proposal if row else None
        outcome = outcomes.get(identity)
        unit_diagnostics = outcome.diagnostics if outcome else (
            *observation.diagnostics, *(row.diagnostics if row else ())
        )
        diagnostics = [
            {"code": item.code, "message": item.message}
            for item in unit_diagnostics
        ]
        intent = proposal.intent if proposal else (
            row.allowed_intents[0] if row and len(row.allowed_intents) == 1 else None
        )
        if proposal:
            materialization = "ready"
        elif row and row.diagnostics:
            materialization = "failed"
        elif row and row.allowed_intents:
            materialization = "pending"
        else:
            materialization = "not-applicable"
        units.append({
            "identity": identity,
            "policy": observation.effective_policy,
            "observation": observation.state,
            "resolution_intent": intent,
            "selected": bool(row and row.approved),
            "approved": bool(row and row.approved),
            "materialization": materialization,
            "primary_source_change": primary_change_summary(proposal, observation.repository_path),
            "effects": [effect_summary(effect) for effect in proposal.publication_effects] if proposal else [],
            "base": {
                "status": observation.base.status,
                "provenance": observation.base.record.provenance if observation.base.record else None,
                "acknowledged": observation.base.acknowledged,
            },
            "result": outcome.status if outcome else None,
            "diagnostics": diagnostics,
        })
    return {
        "operation": "sync",
        "mode": "dry-run" if args.dry_run else "execute",
        "status": result.status if result else "aborted" if diagnostic and diagnostic["code"] == "interrupted" else "failed",
        "scope": [unit["identity"] for unit in units] if view else list(args.scopes),
        "summary": {
            "sync_units": len(units),
            "approved_units": sum(unit["approved"] for unit in units),
            "repository_changes": sum(unit["primary_source_change"] is not None for unit in units if unit["selected"]),
            "live_writes": sum(effect["kind"] == "write" for unit in units if unit["selected"] for effect in unit["effects"]),
            "live_deletions": sum(effect["kind"] == "delete" for unit in units if unit["selected"] for effect in unit["effects"]),
            "diagnostics": ([diagnostic] if diagnostic else []) + [
                {"code": item.code, "message": item.message}
                for item in result.diagnostics
            ] if result else ([diagnostic] if diagnostic else []),
        },
        "sync_units": units,
        "additional_source_changes": [],
        "probe_work": [],
        "directory_root_work": [],
        "hook_work": [],
        # Report actual steps, not success inferred from materialized Proposals.
        "stages": [
            {
                "stage": item.stage,
                "kind": item.kind,
                "action": item.action,
                "scope": item.scope,
                "scope_identity": item.scope_identity,
                "repo": item.repo,
                "package_id": item.package_id,
                "status": item.status,
                "skip_reason": item.skip_reason,
                "exit_code": item.exit_code,
                "error": item.error,
            }
            for item in result.steps
        ] if result else [],
    }


def effect_summary(effect) -> dict:
    summary = {"kind": effect.kind, "path": str(effect.path)}
    if effect.content is not None:
        summary["bytes"] = len(effect.content)
    if effect.mode is not None:
        summary["mode"] = format(effect.mode, "04o")
    return summary


def resolution_label(intent: str) -> str:
    return {"use-repository": "Use repository", "use-live": "Use live"}[intent]


def primary_change_summary(proposal, path) -> dict | None:
    if proposal is None or proposal.primary_source_change is None:
        return None
    change = proposal.primary_source_change
    summary = {"kind": "write" if isinstance(change, FilePresent) else "delete", "path": str(path)}
    if isinstance(change, FilePresent):
        summary["bytes"] = len(change.content)
    return summary
