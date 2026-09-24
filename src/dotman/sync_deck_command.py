"""Command Deck CLI adapters; sessions own Approval and frozen execution."""

from __future__ import annotations

import json
from dataclasses import replace
import sys

from dotman.edit_resolution import EditResolver
from dotman.interaction import Interaction
from dotman.interaction_policy import interaction_scope
from dotman.progress import make_planning_sink
from dotman.cli_style import render_sync_term, render_package_label, render_summary_stat, style_text, MENU_REPO_STYLE
from dotman.sync_scope import _parse_scope_selector, split_scope_child_path
from dotman.sync_base_store import DirectoryChildPresent, FilePresent, Missing
from dotman.sync_session import (
    AdditionalRow, BatchSetApproval, PrepareSourceReview, AuxiliaryRow, CommandRejected, EditProposal, PrepareProposalReview, Preview, SessionOpenFailed,
    SetApproval, SetIncluded, SetResolutionIntent, RetryMaterialization, SyncSession, has_errors,
)
from dotman.ui_context import ui_config_scope


def selection_uses_inclusion(row) -> bool:
    # Directory children are ordinary Proposal rows; only auxiliary work uses
    # inclusion because it has no independent Proposal/Approval.
    return isinstance(row, AuxiliaryRow)


def set_selected(session: SyncSession, row, selected: bool):
    view = session.view
    command = SetIncluded if selection_uses_inclusion(row) else SetApproval
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
    return row.diagnostics if isinstance(row, AuxiliaryRow) else (
        *row.observation.diagnostics, *row.diagnostics,
        *(row.proposal.checkpoint_warnings if row.proposal else ()),
    )


def auxiliary_resolution(kind: str) -> str:
    return {"probe": "Probe Work", "hook": "Hook Work", "directory-root": "Directory Root Work",
            "guard-skip": "Guard skipped"}[kind]


def auxiliary_label(scope: str, kind: str, directions, *, path_rule_pattern: str | None = None,
                    use_color: bool = False) -> str:
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
    if kind == "hook":
        annotations = " ".join(f"({direction}-hooks)" for direction in directions)
    elif kind == "hook-step":
        # A single executed hook, annotated with its action (e.g. pre_push).
        annotations = " ".join(f"({action})" for action in directions)
    elif kind == "guard-skip":
        annotations = " ".join(f"(guard_{direction})" for direction in directions)
        if path_rule_pattern is not None:
            annotations += f" (path rule: {path_rule_pattern})"
    else:
        annotations = ""
    return f"{scope} {annotations}".rstrip()


def guard_skip_explanation(row) -> str:
    """Guard exit 100 omits work by design; say which Guard and why."""
    reason = f" ({row.guard_skip.reason})" if row.guard_skip.reason else ""
    return f"guard_{row.directions[0]} exited 100{reason}"


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
    operation = "sync"

    def _open(self, engine, scope, args):
        return engine.open_sync_session(scope, preview=args.dry_run, run_noop=getattr(args, "run_noop", False),
                                        sink=make_planning_sink(json_output=args.json_output, unit="target"))

    def _select_defaults(self, session):
        set_all_selected(session, True)

    def _resolve_scope_inputs(self, engine, scopes, *, interaction: Interaction | None) -> list[str]:
        """Resolve shorthand or ambiguous scopes to the canonical identities Sync requires."""
        resolver = EditResolver(engine.config, engine=engine, interaction=interaction, use_color=self._use_color)

        def resolve(text: str) -> str:
            # Directory-child paths are not tracked targets; resolve the owning
            # target and keep the child suffix verbatim for the engine to validate.
            target_text, child_path = split_scope_child_path(text)
            identity = resolver.resolve_tracked_identity(target_text, subject="sync scope")
            return identity if child_path is None else f"{identity}/{child_path}"

        return [resolve(text) for text in scopes]

    def __init__(self, *, engine_factory, use_color: bool, interaction: Interaction | None = None) -> None:
        self._engine_factory = engine_factory
        self._use_color = use_color
        self._interaction = interaction

    def run(self, args) -> int:
        with interaction_scope(unattended=args.unattended):
            return self._run(args)

    def _run(self, args) -> int:
        interactive = (
            not args.json_output and not args.unattended
            and sys.stdin.isatty() and sys.stdout.isatty()
        )
        if not interactive and not args.unattended:
            self._emit(args, None, None, diagnostic={
                "code": "unattended-decision",
                "message": f"{self.operation.title()} requires a terminal or explicit --unattended.",
            })
            return 1
        # Configuration load failures keep the CLI's structured error and hint.
        engine = self._engine_factory(args.config)
        try:
            scope = engine.resolve_sync_scope(self._resolve_scope_inputs(
                engine, args.scopes, interaction=self._interaction if interactive else None,
            ))
        except ValueError as exc:
            self._emit(args, None, None, diagnostic={
                "code": "invalid-input", "message": str(exc),
            })
            return 2
        except (KeyboardInterrupt, InterruptedError):
            self._emit(args, None, None, diagnostic={
                "code": "interrupted", "message": f"{self.operation.title()} preflight interrupted",
            })
            return 130
        ui = engine.config.ui
        if getattr(args, 'full_path', None) is not None:
            ui = replace(ui, full_paths=args.full_path)
        with ui_config_scope(ui):
            opened = self._open(engine, scope, args)
            if isinstance(opened, SessionOpenFailed):
                self._emit(args, None, None, diagnostic={
                    "code": opened.diagnostic.code, "message": opened.diagnostic.message,
                }, output_line=opened.output_line)
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
                            "code": "interrupted", "message": f"{self.operation.title()} aborted",
                        })
                        return 130
                elif args.unattended:
                    self._select_defaults(session)

                # Unattended failures must not permit a partially understood
                # workset to mutate unrelated units.
                blocked = any(
                    has_errors(row_diagnostics(row))
                    for row in session.view.rows if not isinstance(row, AdditionalRow)
                )
                if not interactive and blocked:
                    interrupted = any(
                        item.code == "interrupted"
                        for row in session.view.rows
                        for item in row_diagnostics(row)
                    )
                    self._emit(args, session, None, diagnostic={
                        "code": "interrupted", "message": f"{self.operation.title()} materialization interrupted",
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
                        "code": dispatched.diagnostics[0].code if dispatched.diagnostics else "command-rejected",
                        "message": dispatched.diagnostics[0].message if dispatched.diagnostics else dispatched.reason,
                    })
                    return 1
                self._emit(args, session, dispatched.result)
                return dispatched.result.exit_code

    def _emit(self, args, session: SyncSession | None, result, *, diagnostic=None, output_line=None) -> None:
        payload = sync_document(args, session, result, diagnostic=diagnostic)
        if args.json_output:
            print(json.dumps(payload))
            return
        print(f":: {self.operation.title()}" + (" preview" if args.dry_run else ""))
        term = lambda text: render_sync_term(text, use_color=self._use_color)
        # Unselected entries only matter when they explain a problem.
        for unit in payload["sync_units"]:
            if not (unit["selected"] or unit["diagnostics"]):
                continue
            print(f"  [{term(entry_outcome(unit['result'], unit['diagnostics']))}] {unit['identity']}")
            if unit["resolution"]:
                print(f"      {term(resolution_label(unit['resolution']))}")
            if unit["fallback_reason"]:
                print(f"      {term('Fallback')}: {unit['fallback_reason']}")
            if unit["primary_source_change"]:
                print(f"      repository {unit['primary_source_change']['kind']}")
            for effect in unit["effects"]:
                print(f"      {effect['kind']}")
            for item in unit["diagnostics"]:
                print(f"      {item['message']}")
        for change in payload["additional_source_changes"]:
            if not (change["approved"] or change["diagnostics"]):
                continue
            print(f"  [{term(entry_outcome(change['result'], change['diagnostics']))}] {change['repo']}:{change['path']}")
            print(f"      {term('Additional Source Change')}: {change['kind']}")
            for item in change["diagnostics"]:
                print(f"      {item['message']}")
        for kind, key in (("probe", "probe_work"), ("directory-root", "directory_root_work"), ("hook", "hook_work")):
            for work in payload[key]:
                if not (work["selected"] or work["diagnostics"]):
                    continue
                label = auxiliary_label(work["identity"], kind, work["directions"], use_color=self._use_color)
                outcome = auxiliary_outcome(work["identity"], payload["stages"], preview=args.dry_run,
                                            diagnostics=work["diagnostics"])
                print(f"  [{term(outcome)}] {label}")
                print(f"      {term(auxiliary_resolution(kind))}")
                for item in work["diagnostics"]:
                    print(f"      {item['message']}")
        # Hooks run inside units, so their failures would otherwise surface only as skipped units.
        for step in result.steps if result else ():
            if step.kind != "hook" or step.status not in ("failed", "interrupted"):
                continue
            label = auxiliary_label(step.scope_identity or step.repo, "hook-step", (step.action,),
                                    use_color=self._use_color)
            print(f"  [{term(step.status)}] {label}")
            if step.status == "failed":
                reason = f": {step.output_line}" if step.output_line else ""
                print(f"      exit {step.exit_code}{reason}")
        for skip in payload["guard_skips"]:
            label = auxiliary_label(skip["identity"], "guard-skip", (skip["direction"],),
                                    path_rule_pattern=skip["path_rule_pattern"], use_color=self._use_color)
            reason = f": {skip['reason']}" if skip["reason"] else ""
            print(f"  [{render_sync_term('skipped', use_color=self._use_color)}] {label}")
            print(f"      {render_sync_term('Guard skipped', use_color=self._use_color)}{reason}")
        summary = payload["summary"]
        stats = summary_stats(
            (("approved", summary["approved_units"]), ("repos", summary["repository_changes"])),
            writes=summary["live_writes"], deletions=summary["live_deletions"],
            trailing=(("in-sync", summary["in_sync_units"]),), use_color=self._use_color,
        )
        print(f":: {render_sync_term(payload['status'], use_color=self._use_color)} — {stats}")
        for item in payload["summary"]["diagnostics"]:
            # Command output belongs to the session-open diagnostic and stays out of JSON.
            reason = f": {output_line}" if output_line and item is diagnostic else ""
            print(f"{item['message']}{reason}", file=sys.stderr)


# Collapse detailed Sync statuses into the execution log vocabulary
# (ok/failed/interrupted/skipped), plus preview and not-yet-run states.
ENTRY_OUTCOME_BY_STATUS = {
    "applied": "ok",
    "converged": "ok",
    "directly-in-sync": "ok",
    "would-apply": "would-apply",
    "would-converge": "would-apply",
    "execution-failed": "failed",
    "observation-failed": "failed",
    "not-converged": "failed",
    "interrupted": "interrupted",
    "skipped": "skipped",
    "pending": "pending",
    "excluded": "pending",
}


def entry_outcome(status: str | None, diagnostics) -> str:
    if status is None:
        # No execution result: blocked before execution, or the session failed early.
        return "failed" if any(item.get("severity", "error") == "error" for item in diagnostics) else "pending"
    return ENTRY_OUTCOME_BY_STATUS[status]


def auxiliary_outcome(identity: str, stages, *, preview: bool, diagnostics) -> str:
    """Auxiliary Work has no unit result; derive its outcome from the steps run at its scope."""
    statuses = {stage["status"] for stage in stages if stage["scope_identity"] == identity}
    for status in ("failed", "interrupted"):
        if status in statuses:
            return status
    if "ok" in statuses:
        return "ok"
    if statuses:
        return "skipped"
    return entry_outcome("would-apply" if preview else None, diagnostics)


def summary_stats(leading, *, writes, deletions, trailing=(), use_color) -> str:
    """Join counts as dimmed `label: n` stats; live writes and deletions share one `live` stat."""
    live = render_summary_stat(label="live", value=writes + deletions, use_color=use_color)
    # Deletions are destructive, so they stay visible inside the merged live count.
    if deletions:
        live += f" ({deletions} deleted)"
    stats = [render_summary_stat(label=label, value=value, use_color=use_color) for label, value in leading]
    stats.append(live)
    stats.extend(render_summary_stat(label=label, value=value, use_color=use_color) for label, value in trailing)
    return " · ".join(stats)


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
    in_sync_units = 0
    for observation in view.observations if view else ():
        identity = observation.identity.canonical
        row = rows.get(identity)
        proposal = row.proposal if row else None
        outcome = outcomes.get(identity)
        unit_diagnostics = outcome.diagnostics if outcome else (
            *observation.diagnostics, *(row.diagnostics if row else ()),
            *(proposal.checkpoint_warnings if proposal else ()),
        )
        # Clean in-sync units have nothing to approve; listing them buries actionable
        # units and bloats output on large scopes, so only their count is reported.
        if observation.state == "directly-in-sync" and not unit_diagnostics:
            in_sync_units += 1
            continue
        diagnostics = [
            {"code": item.code, "message": item.message, "severity": item.severity}
            for item in unit_diagnostics
        ]
        intent = row.intent if row else None
        if proposal:
            materialization = "ready"
        elif row and row.diagnostics:
            materialization = "failed"
        elif row and "prepare-proposal-review" in row.allowed_commands:
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
            "selected": bool(row and (row.included if selection_uses_inclusion(row) else row.approved)),
            "approved": bool(row and row.approved),
            "symlink_replacement_authorized": bool(row and row.symlink_authorized),
            "materialization": materialization,
            "primary_source_change": primary_change_summary(proposal, observation.repository_path),
            "effects": [effect_summary(effect) for effect in proposal.publication_effects] if proposal else [],
            "base": {
                "status": observation.base.status,
                "fingerprint": observation.base.record.envelope.fingerprint if observation.base.record else None,
                "qualified": proposal.checkpoint_qualified if proposal else None,
                "acknowledged": outcome.acknowledged if outcome else observation.base.acknowledged,
            },
            "result": outcome.status if outcome else None,
            "diagnostics": diagnostics,
        })
    return {
        "operation": getattr(args, "command", "sync"),
        "mode": "dry-run" if args.dry_run else "execute",
        "status": result.status if result else "aborted" if diagnostic and diagnostic["code"] == "interrupted" else "failed",
        "scope": list(dict.fromkeys([unit["identity"] for unit in units] + [row.scope for row in auxiliary])) if view else list(getattr(args, "scopes", ()) or ([args.binding] if getattr(args, "binding", None) else [])),
        "summary": {
            "sync_units": len(units),
            "in_sync_units": in_sync_units,
            "selected_auxiliary": sum(row.included for row in auxiliary),
            "approved_units": sum(unit["approved"] for unit in units),
            "repository_changes": sum(unit["primary_source_change"] is not None for unit in units if unit["selected"]) + sum(row.approved for row in additional),
            "approved_additional_sources": sum(row.approved for row in additional),
            "live_writes": sum(effect["kind"] == "write" for unit in units if unit["selected"] for effect in unit["effects"]),
            "live_deletions": sum(effect["kind"] == "delete" for unit in units if unit["selected"] for effect in unit["effects"]),
            "diagnostics": ([diagnostic] if diagnostic else []) + [
                {"code": item.code, "message": item.message, "severity": item.severity}
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
        "guard_skips": [
            {"identity": row.scope, "direction": row.directions[0], "scope_kind": row.guard_skip.scope_kind,
             "path_rule_pattern": row.guard_skip.path_rule_pattern, "reason": row.guard_skip.reason}
            for row in auxiliary if row.kind == "guard-skip"
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
    present_types = (FilePresent, DirectoryChildPresent)
    summary = {"kind": "write" if isinstance(change, present_types) else "delete", "path": str(path)}
    if isinstance(change, present_types):
        summary["bytes"] = len(change.content)
        if isinstance(change, DirectoryChildPresent):
            summary["executable"] = change.executable
    return summary


class PullDeckCommandRunner(SyncDeckCommandRunner):
    """Pull has fixed initially approved work, not unattended Sync defaults."""

    command_names = frozenset({"pull"})
    operation = "pull"

    def _open(self, engine, scope, args):
        return engine.open_pull_session(scope, preview=args.dry_run,
                                        run_noop=getattr(args, "run_noop", False),
                                        sink=make_planning_sink(json_output=args.json_output, unit="target"))

    def _select_defaults(self, session):
        # Opening already materialized standing opt-out Approval. In particular,
        # do not retry and silently reauthorize a failed initial Proposal.
        pass


class PushDeckCommandRunner(SyncDeckCommandRunner):
    """Push has fixed initially approved work, not unattended Sync defaults."""

    command_names = frozenset({"push"})
    operation = "push"

    def _open(self, engine, scope, args):
        return engine.open_push_session(scope, preview=args.dry_run,
                                        run_noop=getattr(args, "run_noop", False),
                                        sink=make_planning_sink(json_output=args.json_output, unit="target"))

    def _select_defaults(self, session):
        # Opening already materialized standing opt-out Approval. In particular,
        # do not retry and silently reauthorize a failed initial Proposal.
        pass
