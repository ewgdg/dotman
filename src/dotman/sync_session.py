"""One-shot Sync boundary: immutable views, semantic commands and typed outcomes."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass, replace
from typing import Callable, Literal
from uuid import uuid4

from dotman.models import ResolvedSyncScope
from dotman.planning import PlanningContext
from dotman.sync_base_store import SyncBaseStoreError
from dotman.sync_base_lifecycle import SyncBaseGitError
from dotman.operation_lock import OperationBusy, OperationLock, OperationLockError
from dotman.sync_observation import Diagnostic, Observation, observe_scope


CommandName = Literal["set-included", "execute", "abort"]


@dataclass(frozen=True)
class SessionRow:
    row_id: str
    kind: Literal["drift", "diagnostic"]
    included: bool
    observation: Observation
    allowed_commands: tuple[CommandName, ...]


@dataclass(frozen=True)
class SessionView:
    session_id: str
    revision: int
    preview: bool
    terminal: bool
    observations: tuple[Observation, ...]
    rows: tuple[SessionRow, ...]
    allowed_commands: tuple[CommandName, ...]


@dataclass(frozen=True)
class SetIncluded:
    session_id: str
    revision: int
    row_id: str
    included: bool


@dataclass(frozen=True)
class Execute:
    session_id: str
    revision: int


@dataclass(frozen=True)
class Abort:
    session_id: str
    revision: int


SessionCommand = SetIncluded | Execute | Abort


@dataclass(frozen=True)
class InclusionChanged:
    row_id: str
    included: bool


@dataclass(frozen=True)
class SyncUnitResult:
    identity: str
    status: Literal["directly-in-sync", "pending", "excluded", "observation-failed"]
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class SyncResult:
    status: Literal["completed", "incomplete", "failed", "aborted"]
    units: tuple[SyncUnitResult, ...]

    @property
    def exit_code(self) -> int:
        return {"completed": 0, "incomplete": 1, "failed": 1, "aborted": 130}[
            self.status
        ]


@dataclass(frozen=True)
class CommandAccepted:
    view: SessionView
    result: InclusionChanged | SyncResult


@dataclass(frozen=True)
class CommandRejected:
    view: SessionView
    reason: Literal[
        "invalid",
        "foreign-session",
        "stale",
        "unknown-row",
        "disallowed",
        "preview",
        "terminal",
    ]


@dataclass(frozen=True)
class SessionOpenFailed:
    diagnostic: Diagnostic


@dataclass(frozen=True)
class SessionOpened:
    view: SessionView


@dataclass(frozen=True)
class SessionChanged:
    view: SessionView


@dataclass(frozen=True)
class SessionFinished:
    view: SessionView
    result: SyncResult


SessionEvent = SessionOpened | SessionChanged | SessionFinished
SessionEventSink = Callable[[SessionEvent], None]


class SyncSession:
    def __init__(
        self,
        observations: tuple[Observation, ...],
        *,
        preview: bool,
        event_sink: SessionEventSink | None = None,
    ) -> None:
        self._event_sink = event_sink
        self._operation_lock: OperationLock | None = None
        self._view = SessionView(
            uuid4().hex,
            0,
            preview,
            False,
            observations,
            tuple(
                SessionRow(
                    unit.identity.canonical,
                    "drift"
                    if unit.state == "drifted" and not unit.diagnostics
                    else "diagnostic",
                    unit.state == "drifted" and not unit.diagnostics,
                    unit,
                    ("set-included",)
                    if unit.state == "drifted" and not unit.diagnostics
                    else (),
                )
                for unit in observations
                if unit.state != "directly-in-sync" or unit.diagnostics
            ),
            ("abort",) if preview else ("execute", "abort"),
        )

    @classmethod
    def open(
        cls,
        context: PlanningContext,
        scope: ResolvedSyncScope,
        *,
        preview: bool = False,
        event_sink: SessionEventSink | None = None,
    ) -> SyncSession | SessionOpenFailed:
        with ExitStack() as resources:
            lock = None
            try:
                if not preview:
                    lock = resources.enter_context(
                        OperationLock.acquire(context.tracked_state.state_root)
                    )
                observations = observe_scope(context, scope, preview=preview)
            except OperationBusy as exc:
                return SessionOpenFailed(Diagnostic("operation-busy", str(exc)))
            except OperationLockError as exc:
                return SessionOpenFailed(Diagnostic("operation-lock-failed", str(exc)))
            except (SyncBaseStoreError, SyncBaseGitError) as exc:
                return SessionOpenFailed(Diagnostic("base-failed", str(exc)))
            except ValueError as exc:
                return SessionOpenFailed(Diagnostic("planning-failed", str(exc)))
            except (KeyboardInterrupt, InterruptedError):
                return SessionOpenFailed(
                    Diagnostic("interrupted", "Sync opening interrupted")
                )
            except OSError as exc:
                return SessionOpenFailed(Diagnostic("observation-failed", str(exc)))
            session = cls(observations, preview=preview, event_sink=event_sink)
            session._operation_lock = lock
            # Adapter exceptions are programming failures, not planning results.
            session._emit(SessionOpened(session.view))
            resources.pop_all()
            return session

    @property
    def view(self) -> SessionView:
        return self._view

    def _emit(self, event: SessionEvent) -> None:
        if self._event_sink is not None:
            self._event_sink(event)

    def dispatch(self, command: SessionCommand) -> CommandAccepted | CommandRejected:
        view = self.view
        if type(command) not in (SetIncluded, Execute, Abort):
            return CommandRejected(view, "invalid")
        if view.terminal:
            return CommandRejected(view, "terminal")
        if type(command.session_id) is not str or type(command.revision) is not int:
            return CommandRejected(view, "invalid")
        if command.session_id != view.session_id:
            return CommandRejected(view, "foreign-session")
        if command.revision != view.revision:
            return CommandRejected(view, "stale")
        if isinstance(command, SetIncluded):
            if type(command.row_id) is not str or type(command.included) is not bool:
                return CommandRejected(view, "invalid")
            row = next((row for row in view.rows if row.row_id == command.row_id), None)
            if row is None:
                return CommandRejected(view, "unknown-row")
            if "set-included" not in row.allowed_commands:
                return CommandRejected(view, "disallowed")
            self._view = replace(
                view,
                revision=view.revision + 1,
                rows=tuple(
                    replace(item, included=command.included)
                    if item.row_id == row.row_id
                    else item
                    for item in view.rows
                ),
            )
            result = CommandAccepted(
                self.view, InclusionChanged(row.row_id, command.included)
            )
            self._emit(SessionChanged(self.view))
            return result
        if isinstance(command, Execute) and view.preview:
            return CommandRejected(view, "preview")
        result = self._finish(aborted=isinstance(command, Abort))
        return CommandAccepted(self.view, result)

    def execute(self) -> CommandAccepted | CommandRejected:
        """Execute the current view; adapters with cached views use Execute tokens."""
        return self.dispatch(Execute(self.view.session_id, self.view.revision))

    def abort(self) -> CommandAccepted | CommandRejected:
        return self.dispatch(Abort(self.view.session_id, self.view.revision))

    def _finish(self, *, aborted: bool) -> SyncResult:
        rows = {row.row_id: row for row in self.view.rows}
        units = []
        for observation in self.view.observations:
            status = observation.state
            if status == "drifted":
                status = (
                    "pending"
                    if rows[observation.identity.canonical].included
                    else "excluded"
                )
            units.append(
                SyncUnitResult(
                    observation.identity.canonical, status, observation.diagnostics
                )
            )
        # No Approval or materialization exists at this boundary. Never turn
        # observed drift into claimed convergence just because no effects run.
        status = (
            "aborted"
            if aborted
            else "failed"
            if any(
                unit.diagnostics or unit.status == "observation-failed"
                for unit in units
            )
            else "incomplete"
            if any(unit.status == "pending" for unit in units)
            else "completed"
        )
        result = SyncResult(status, tuple(units))
        self._view = replace(
            self.view,
            revision=self.view.revision + 1,
            terminal=True,
            allowed_commands=(),
            rows=tuple(replace(row, allowed_commands=()) for row in self.view.rows),
        )
        if self._operation_lock is not None:
            self._operation_lock.close()
            self._operation_lock = None
        self._emit(SessionFinished(self.view, result))
        return result

    def __enter__(self) -> SyncSession:
        return self

    def __exit__(self, *_exc) -> None:
        if not self.view.terminal:
            self.abort()
