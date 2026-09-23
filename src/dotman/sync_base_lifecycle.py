"""Shared Base decisions; callers own selection, Observation, effects and ordering."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath
from typing import Literal

from dotman.models import ResolvedSyncTarget
from dotman.sync_base_store import (
    DirectoryChildPresent,
    FilePresent,
    SyncBaseEnvelope,
    SyncBasePayload,
    SyncBaseRecord,
    SyncBaseRecordCorruptionError,
    SyncBaseStore,
    SyncBaseStoreError,
    SyncBaseStoreDurabilityError,
)
from dotman.sync_scope import sync_unit_identity_bytes


@dataclass(frozen=True, init=False)
class BaseProfileContext:
    """An immutable, type-preserving snapshot of resolved profile/variable inputs."""

    canonical_json: str

    def __init__(self, context: Mapping[str, object] | None = None) -> None:
        # Snapshot nested mappings/lists without retaining caller-owned mutability.
        object.__setattr__(
            self,
            "canonical_json",
            json.dumps(
                {} if context is None else dict(context),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ),
        )


@dataclass(frozen=True)
class BaseInputs:
    """Effective interpretation only: never policy, Guards, Pull Views or live paths."""

    render: str = "raw"
    capture: str = "raw"
    profile_context: BaseProfileContext = field(default_factory=BaseProfileContext)
    path_rules: tuple[str, ...] = ()
    file_symlink_mode: Literal["prompt", "follow"] = "prompt"
    dir_symlink_mode: Literal["fail", "follow"] = "fail"

    def __post_init__(self) -> None:
        if self.file_symlink_mode not in ("prompt", "follow"):
            raise ValueError("invalid file symlink interpretation")
        if self.dir_symlink_mode not in ("fail", "follow"):
            raise ValueError("invalid directory symlink interpretation")
        if not isinstance(self.render, str) or not isinstance(self.capture, str):
            raise TypeError("effective projections must be strings")
        if type(self.path_rules) is not tuple or any(
            type(name) is not str for name in self.path_rules
        ):
            raise TypeError("Path Rule names must be an immutable string tuple")
        if type(self.profile_context) is not BaseProfileContext:
            raise TypeError("profile context must be a frozen BaseProfileContext")


@dataclass(frozen=True)
class BaseUnit:
    """One successfully statically resolved, selected file or directory child."""

    identity: ResolvedSyncTarget
    primary_source: str
    configured_policy: str
    inputs: BaseInputs

    def __post_init__(self) -> None:
        sync_unit_identity_bytes(self.identity)
        path = PurePosixPath(self.primary_source)
        if (
            not path.parts
            or path.is_absolute()
            or path.as_posix() != self.primary_source
            or any(part in (".", "..", ".git") for part in path.parts)
            or "\x00" in self.primary_source
            or "\\" in self.primary_source
        ):
            raise ValueError(
                "Primary Source must be a normalized repository-relative file"
            )
        if self.configured_policy not in (
            "push-only",
            "pull-only",
            "both",
            "push-only-delete",
        ):
            raise ValueError("invalid configured Sync Policy")

    @property
    def identity_bytes(self) -> bytes:
        return sync_unit_identity_bytes(self.identity)

    @property
    def eligible(self) -> bool:
        return self.configured_policy in ("pull-only", "both")

    @property
    def fingerprint(self) -> str:
        inputs = asdict(self.inputs)
        content = json.dumps(
            {"primary_source": self.primary_source, "inputs": inputs},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
        return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True)
class FrozenBaseUnit:
    """The final repository outcome, frozen alongside its qualifying evidence."""

    unit: BaseUnit
    payload: SyncBasePayload

    def record(self) -> SyncBaseRecord:
        return SyncBaseRecord(
            self.unit.identity_bytes,
            self.payload,
            SyncBaseEnvelope(self.unit.fingerprint),
        )


@dataclass(frozen=True)
class BaseInspection:
    status: Literal["usable", "unavailable", "not-applicable"]
    reason: str | None = None
    record: SyncBaseRecord | None = None
    failure: SyncBaseStoreError | None = None


EffectStatus = Literal["not-required", "pending", "succeeded", "failed"]


@dataclass(frozen=True)
class ProposalCompletion:
    """Final unit-owned effects only; Additional Sources and hooks are not operands."""

    intent: Literal["use-repository", "use-live", "merge", "editor"]
    approved: bool
    included: bool = True
    materialized: bool = True
    primary_effect: EffectStatus = "not-required"
    publication_effects: EffectStatus = "not-required"

    def __post_init__(self) -> None:
        if self.intent not in ("use-repository", "use-live", "merge", "editor"):
            raise ValueError("invalid final Proposal intent")
        if any(
            value not in ("not-required", "pending", "succeeded", "failed")
            for value in (self.primary_effect, self.publication_effects)
        ):
            raise ValueError("invalid required-effect result")

    @property
    def ready(self) -> bool:
        return (
            self.approved
            and self.included
            and self.materialized
            and self.primary_effect in ("not-required", "succeeded")
            and self.publication_effects in ("not-required", "succeeded")
        )

    @property
    def live_fact(self) -> str:
        if self.primary_effect == self.publication_effects == "not-required":
            return "approved-no-write"
        return {
            "use-repository": "published-repository-outcome",
            "use-live": "frozen-live-capture-input",
            "merge": "frozen-merged-live-outcome",
            "editor": "rematerialized-policy-outcome",
        }[self.intent]


@dataclass(frozen=True)
class BaseLifecycleResult:
    converged: bool = False
    acknowledged: bool = False
    deleted: bool = False
    live_fact: str | None = None
    failure: SyncBaseStoreError | None = None

    @property
    def warning_code(self) -> str | None:
        if self.failure is None:
            return None
        return ("base-durability-uncertain" if isinstance(self.failure, SyncBaseStoreDurabilityError)
                else "base-acknowledgment-failed")


def record_matches_identity(
    record: SyncBaseRecord, identity: ResolvedSyncTarget
) -> bool:
    """Validate record shape against its canonical Sync Unit identity."""
    return (
        record.identity == sync_unit_identity_bytes(identity)
        and not (
            isinstance(record.payload, FilePresent)
            and identity.child_path is not None
        )
        and not (
            isinstance(record.payload, DirectoryChildPresent)
            and identity.child_path is None
        )
    )


class SyncBaseLifecycle:
    """No session orchestration: invoke each method at its documented boundary."""

    def __init__(
        self,
        store: SyncBaseStore,
        *,
        operation: Literal["push", "pull", "sync"],
        preview: bool = False,
    ) -> None:
        if operation not in ("push", "pull", "sync"):
            raise ValueError("invalid Base operation")
        self.store = store
        self.operation = operation
        self.preview = preview

    def inspect(self, unit: BaseUnit) -> BaseInspection:
        """Read only; no projections, live reads or cleanup."""
        if not unit.eligible:
            return BaseInspection("not-applicable", "ineligible")
        try:
            record = self.store.read(unit.identity_bytes)
        except SyncBaseRecordCorruptionError as exc:
            return BaseInspection("unavailable", exc.reason, failure=exc)
        except SyncBaseStoreError as exc:
            return BaseInspection("unavailable", "storage_unavailable", failure=exc)
        if record is None:
            return BaseInspection("unavailable", "absent")
        if not record_matches_identity(record, unit.identity):
            return BaseInspection(
                "unavailable", "record_corrupt",
                failure=SyncBaseStoreError("checkpoint does not match Sync Unit identity"),
            )
        if record.envelope.fingerprint != unit.fingerprint:
            return BaseInspection("unavailable", "inputs_changed")
        return BaseInspection("usable", record=record)

    def maintain(self, unit: BaseUnit) -> BaseInspection:
        """Reclaim interpretation-invalid records, never recreate rejected storage."""
        inspected = self.inspect(unit)
        if not self.preview and inspected.reason == "inputs_changed":
            try:
                self.store.delete(unit.identity_bytes)
            except SyncBaseStoreError as exc:
                return BaseInspection("unavailable", "inputs_changed", failure=exc)
            return BaseInspection("unavailable", "absent")
        return inspected

    def selected_policy_resolved(self, unit: BaseUnit) -> BaseLifecycleResult:
        """Real Push/Sync: immediately after selected static resolution, before Guards/review."""
        if self.preview or self.operation == "pull" or unit.eligible:
            return BaseLifecycleResult()
        try:
            return BaseLifecycleResult(deleted=self.store.delete(unit.identity_bytes))
        except SyncBaseStoreError as exc:
            return BaseLifecycleResult(failure=exc)

    def direct_agreement(
        self,
        frozen: FrozenBaseUnit,
        *,
        fresh_observation: bool = True,
        participating: bool = True,
    ) -> BaseLifecycleResult:
        """Immediately after actual direct Observation, before review or hooks."""
        if (
            self.preview
            or not fresh_observation
            or not participating
            or not frozen.unit.eligible
        ):
            return BaseLifecycleResult()
        return self._acknowledge(
            frozen, live_fact="direct-agreement"
        )

    def complete(
        self,
        frozen: FrozenBaseUnit,
        proposal: ProposalCompletion,
        *,
        qualified: bool = True,
    ) -> BaseLifecycleResult:
        """At the unit's earliest ordered completion point, before its target post-hook."""
        if self.preview or not proposal.ready:
            return BaseLifecycleResult()
        if not frozen.unit.eligible or not qualified:
            return BaseLifecycleResult(converged=True)
        result = self._acknowledge(
            frozen,
            live_fact=proposal.live_fact,
        )
        return BaseLifecycleResult(
            converged=True,
            acknowledged=result.acknowledged,
            live_fact=result.live_fact,
            failure=result.failure,
        )

    def _acknowledge(
        self,
        frozen: FrozenBaseUnit,
        *,
        live_fact: str,
    ) -> BaseLifecycleResult:
        try:
            self.store.replace(frozen.record())
        except SyncBaseStoreDurabilityError as exc:
            return BaseLifecycleResult(acknowledged=True, live_fact=live_fact, failure=exc)
        except SyncBaseStoreError as exc:
            return BaseLifecycleResult(failure=exc)
        return BaseLifecycleResult(acknowledged=True, live_fact=live_fact)
