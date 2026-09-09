"""Thin Sync CLI adapter; the session owns Approval and frozen execution."""

from __future__ import annotations

import json
import sys

from dotman.cli_style import render_sync_term, render_package_label, style_text, MENU_REPO_STYLE
from dotman.sync_scope import _parse_scope_selector
from dotman.sync_base_store import FilePresent, Missing
from dotman.sync_session import (
    AdditionalRow, BatchSetApproval, PrepareSourceReview, AuxiliaryRow, CommandRejected, EditProposal, PrepareProposalReview, Preview, SessionOpenFailed,
    SetApproval, SetIncluded, SetResolutionIntent, RetryMaterialization, SyncSession,
)
from dotman.ui_context import ui_config_scope


def approve(session: SyncSession, row_id: str, approved: bool):
    view = session.view
    return session.dispatch(SetApproval(view.session_id, view.revision, row_id, approved))


def set_selected(session: SyncSession, row, selected: bool):
    view = session.view
    command = SetIncluded if isinstance(row, AuxiliaryRow) else SetApproval
    return session.dispatch(command(view.session_id, view.revision, row.row_id, selected))


def set_all_selected(session: SyncSession, selected: bool):
    view = session.view
    return session.dispatch(BatchSetApproval(view.session_id, view.revision, selected))


def additional_label(row, *, use_color: bool = False) -> str:
    repo = style_text(row.repo, *MENU_REPO_STYLE) if use_color else row.repo
    return f"{repo}:{row.path}"


def row_diagnostics(row):
    if isinstance(row, AdditionalRow):
        return ()
    return row.diagnostics if isinstance(row, AuxiliaryRow) else (*row.observation.diagnostics, *row.diagnostics)


def auxiliary_resolution(kind: str) -> str:
    return {"probe": "Probe Work", "hook": "Hook Work", "directory-root": "Directory Root Work"}[kind]


def auxiliary_label(scope: str, kind: str, directions, *, use_color: bool = False) -> str:
    if use_color:
        if ":" in scope:
            identity = _parse_scope_selector(scope)
            scope = render_package_label(
                repo_name=identity.repo, package_id=identity.package_id,
                bound_profile=identity.bound_profile, target_name=identity.target_name,
                use_color=True,
            )
        else:
            scope = style_text(scope, *MENU_REPO_STYLE)
    annotations = " ".join(f"({direction}-hooks)" for direction in directions) if kind == "hook" else ""
    return f"{scope} {annotations}".rstrip()


def review(session: SyncSession, row_id: str):
    view = session.view
    row = next(row for row in view.rows if row.row_id == row_id)
    command = PrepareSourceReview if isinstance(row, AdditionalRow) else PrepareProposalReview
    return session.dispatch(command(view.session_id, view.revision, row_id))


def edit_proposal(session: SyncSession, row_id: str):
    view = session.view
    return session.dispatch(EditProposal(view.session_id, view.revision, row_id))


def set_resolution_intent(session: SyncSession, row_id: str, intent: str):
    view = session.view
    return session.dispatch(SetResolutionIntent(view.session_id, view.revision, row_id, intent))


def retry_materialization(session: SyncSession, row_id: str):
    view = session.view
    return session.dispatch(RetryMaterialization(view.session_id, view.revision, row_id))


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
                scope, preview=args.dry_run, run_noop=getattr(args, 'run_noop', False),
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
                    set_all_selected(session, True)

                # Unattended failures must not permit a partially understood
                # workset to mutate unrelated units.
                blocked = any(
                    row.kind == "diagnostic" or row.diagnostics
                    for row in session.view.rows if not isinstance(row, AdditionalRow)
                )
                if not interactive and blocked:
                    interrupted = any(
                        item.code == "interrupted"
                        for row in session.view.rows
                        for item in row_diagnostics(row)
                    )
                    self._emit(args, session, None, diagnostic={
                        "code": "interrupted", "message": "Sync materialization interrupted",
                    } if interrupted else None)
                    return 130 if interrupted else 1
                if not interactive and any(
                    row.kind == "drift" and row.included
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
            if unit["resolution"]:
                print(f"      {render_sync_term(resolution_label(unit['resolution']), use_color=self._use_color)}")
            if unit["fallback_reason"]:
                print(f"      {render_sync_term('Fallback', use_color=self._use_color)}: {unit['fallback_reason']}")
            if unit["primary_source_change"]:
                print(f"      repository {unit['primary_source_change']['kind']}")
            for effect in unit["effects"]:
                print(f"      {effect['kind']}")
            for item in unit["diagnostics"]:
                print(f"      {item['message']}")
            if unit["result"]:
                print(f"      {render_sync_term(unit['result'], use_color=self._use_color)}")
        for change in payload["additional_source_changes"]:
            selection = "approved" if change["approved"] else "unapproved"
            print(f"  [{render_sync_term(selection, use_color=self._use_color)}] {change['repo']}:{change['path']}")
            print(f"      {render_sync_term('Additional Source Change', use_color=self._use_color)}: {change['kind']}")
            if change["result"]:
                print(f"      {render_sync_term(change['result'], use_color=self._use_color)}")
            for item in change["diagnostics"]:
                print(f"      {item['message']}")
        for kind, key in (("probe", "probe_work"), ("directory-root", "directory_root_work"), ("hook", "hook_work")):
            for work in payload[key]:
                selection = "selected" if work["selected"] else "unselected"
                label = auxiliary_label(work["identity"], kind, work["directions"], use_color=self._use_color)
                term = auxiliary_resolution(kind)
                print(f"  [{render_sync_term(selection, use_color=self._use_color)}] {label}")
                print(f"      {render_sync_term(term, use_color=self._use_color)}")
                for item in work["diagnostics"]:
                    print(f"      {item['message']}")
        for item in payload["summary"]["diagnostics"]:
            print(item["message"], file=sys.stderr)


def sync_document(args, session, result, *, diagnostic=None) -> dict:
    """Project only public result metadata; frozen payload bytes never leave the session."""
    view = session.view if session is not None else None
    rows = {row.row_id: row for row in view.rows if not isinstance(row, (AuxiliaryRow, AdditionalRow))} if view else {}
    auxiliary = [row for row in view.rows if isinstance(row, AuxiliaryRow)] if view else []
    additional = [row for row in view.rows if isinstance(row, AdditionalRow)] if view else []
    additional_outcomes = {item.row_id: item for item in result.additional_changes} if result else {}
    def auxiliary_work(kind):
        return [{"identity": row.scope, "selected": row.included,
                 "directions": list(row.directions),
                 "diagnostics": [{"code": item.code, "message": item.message} for item in row.diagnostics]}
                for row in auxiliary if row.kind == kind]
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
        intent = row.intent if row else None
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
            "resolution": proposal.intent if proposal else intent,
            "generation": proposal.generation if proposal else None,
            "additional_source_changes": [item.row_id for item in additional if identity in item.references],
            "allowed_intents": list(row.allowed_intents) if row else [],
            "fallback_reason": row.fallback_reason if row else None,
            "capture": ("missing" if isinstance(proposal.capture, Missing) else "present") if proposal and proposal.capture is not None else None,
            "reconciliation": proposal.reconciliation if proposal else None,
            "selected": bool(row and row.approved),
            "approved": bool(row and row.approved),
            "materialization": materialization,
            "primary_source_change": primary_change_summary(proposal, observation.repository_path),
            "effects": [effect_summary(effect) for effect in proposal.publication_effects] if proposal else [],
            "base": {
                "status": observation.base.status,
                "provenance": observation.base.record.envelope.provenance if observation.base.record else None,
                # Availability/provenance remain frozen opening evidence; eligible
                # convergence additionally proves execution-time acknowledgment.
                "acknowledged": observation.base.acknowledged or bool(
                    not args.dry_run and outcome and outcome.status == "converged"
                    and observation.configured_policy in ("pull-only", "both")
                ),
            },
            "result": outcome.status if outcome else None,
            "diagnostics": diagnostics,
        })
    return {
        "operation": "sync",
        "mode": "dry-run" if args.dry_run else "execute",
        "status": result.status if result else "aborted" if diagnostic and diagnostic["code"] == "interrupted" else "failed",
        "scope": list(dict.fromkeys([unit["identity"] for unit in units] + [row.scope for row in auxiliary])) if view else list(args.scopes),
        "summary": {
            "sync_units": len(units),
            "selected_auxiliary": sum(row.included for row in auxiliary),
            "approved_units": sum(unit["approved"] for unit in units),
            "repository_changes": sum(unit["primary_source_change"] is not None for unit in units if unit["selected"]) + sum(row.approved for row in additional),
            "approved_additional_sources": sum(row.approved for row in additional),
            "live_writes": sum(effect["kind"] == "write" for unit in units if unit["selected"] for effect in unit["effects"]),
            "live_deletions": sum(effect["kind"] == "delete" for unit in units if unit["selected"] for effect in unit["effects"]),
            "diagnostics": ([diagnostic] if diagnostic else []) + [
                {"code": item.code, "message": item.message}
                for item in result.diagnostics
            ] if result else ([diagnostic] if diagnostic else []),
        },
        "sync_units": units,
        "additional_source_changes": [
            {
                "row_id": row.row_id, "repo": row.repo, "path": str(row.path),
                "approved": row.approved, "references": list(row.references),
                "kind": "write",
                "bytes": len(row.change.candidate),
                "result": additional_outcomes[row.row_id].status if row.row_id in additional_outcomes else None,
                "diagnostics": [
                    {"code": item.code, "message": item.message}
                    for item in additional_outcomes[row.row_id].diagnostics
                ] if row.row_id in additional_outcomes else [],
            }
            for row in additional
        ],
        "probe_work": auxiliary_work("probe"),
        "directory_root_work": auxiliary_work("directory-root"),
        "hook_work": auxiliary_work("hook"),
        # Preserve attempted and unattempted execution evidence, never infer success
        # from a materialized Proposal.
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
    return {"use-repository": "Use repository", "use-live": "Use live", "merge": "Merge", "editor": "Edited"}[intent]


def primary_change_summary(proposal, path) -> dict | None:
    if proposal is None or proposal.primary_source_change is None:
        return None
    change = proposal.primary_source_change
    summary = {"kind": "write" if isinstance(change, FilePresent) else "delete", "path": str(path)}
    if isinstance(change, FilePresent):
        summary["bytes"] = len(change.content)
    return summary
