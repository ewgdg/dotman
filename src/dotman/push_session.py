"""Permanent fixed repository-to-live orchestration over frozen Proposal work."""
from dataclasses import replace

from dotman.sync_observation import observe_scope
from dotman.sync_session import ProposalSession, SessionRow, AuxiliaryRow


class PushSession(ProposalSession):
    operation = "push"
    additional_default_approval = True

    @staticmethod
    def _observe(context, scope, **kwargs):
        return observe_scope(context, scope, directions=("push",),
                             omit_no_route=True, **kwargs)

    def _prepare_workset(self):
        # Push is opt-out: every drifted Proposal and includable auxiliary row
        # starts selected, and the direction is fixed to Use repository.
        self._view = replace(self.view, rows=tuple(
            replace(row, approved=row.kind == "drift", intent="use-repository" if row.kind == "drift" else None,
                    allowed_intents=(), fallback_reason=None,
                    allowed_commands=tuple(command for command in row.allowed_commands
                                           if command != "set-resolution-intent"))
            if isinstance(row, SessionRow)
            else replace(row, included=True)
            if isinstance(row, AuxiliaryRow) and "set-included" in row.allowed_commands
            else row
            for row in self.view.rows
        ))
        self._invalidate_inputs({row.row_id for row in self.view.rows if isinstance(row, SessionRow)},
                                clear_cache=False)
