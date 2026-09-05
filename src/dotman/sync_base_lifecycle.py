"""Shared Base decisions; callers own selection, Observation, effects and ordering."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Literal

from dotman.command_runtime import (
    ArgvCommand,
    CommandRequest,
    CommandResult,
    CommandRuntime,
    raise_for_command_interruption,
)
from dotman.models import ResolvedSyncTarget
from dotman.sync_base_store import (
    DirectoryChildPresent,
    FilePresent,
    Missing,
    SyncBaseEnvelope,
    SyncBasePayload,
    SyncBaseRecord,
    SyncBaseRecordCorruptionError,
    SyncBaseStore,
    SyncBaseStoreError,
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
class FrozenGitHead:
    commit_oid: str
    object_format: str


@dataclass(frozen=True)
class FrozenBaseUnit:
    unit: BaseUnit
    head: FrozenGitHead
    payload: SyncBasePayload | None
    primary_clean: bool
    failure: SyncBaseGitError | None = None

    def record(self, *, primary_changed: bool = False) -> SyncBaseRecord:
        if self.failure is not None:
            raise self.failure
        if self.payload is None:
            raise AssertionError("a successful frozen Base must have a typed payload")
        return SyncBaseRecord(
            self.unit.identity_bytes,
            self.payload,
            SyncBaseEnvelope(
                self.head.commit_oid,
                self.head.object_format,
                self.unit.fingerprint,
                "exact"
                if self.primary_clean and not primary_changed
                else "conservative",
            ),
        )


class SyncBaseGitError(RuntimeError):
    """Git could not prove a required frozen Base fact."""


class SyncBaseGit:
    """Concrete Git facts through Command Runtime, without touching the real index."""

    def __init__(self, repository: Path, runtime: CommandRuntime) -> None:
        self.repository = repository
        self.runtime = runtime

    def _run(
        self,
        *arguments: str,
        env: Mapping[str, str] | None = None,
        input: bytes | None = None,
        allowed: tuple[int, ...] = (0,),
    ) -> CommandResult:
        # Inspect local objects only, using committed parents rather than
        # repository or ambient ancestry overrides. Optional-lock suppression
        # alone does not prevent a promisor remote from writing fetched objects.
        try:
            result = self.runtime.run(
                CommandRequest(
                    ArgvCommand(
                        (
                            "git",
                            "--no-optional-locks",
                            "--no-lazy-fetch",
                            "-c",
                            "core.fsmonitor=false",
                            *arguments,
                        )
                    ),
                    cwd=self.repository,
                    env={
                        "GIT_NO_REPLACE_OBJECTS": "1",
                        "GIT_GRAFT_FILE": os.devnull,
                        "GIT_LITERAL_PATHSPECS": "1",
                        **(env or {}),
                    },
                    excluded_env_keys=frozenset(
                        key for key in os.environ if key.startswith("GIT_")
                    ),
                    input=input,
                )
            )
        except OSError as exc:
            raise SyncBaseGitError(f"cannot run Git: {exc}") from exc
        raise_for_command_interruption(result)
        if result.exit_code not in allowed:
            raise SyncBaseGitError(
                f"Git {' '.join(arguments)} failed: {result.stderr.decode(errors='replace').strip()}"
            )
        return result

    def freeze_head(self) -> FrozenGitHead:
        object_format = (
            self._run("rev-parse", "--show-object-format").stdout.decode().strip()
        )
        oid = (
            self._run("rev-parse", "--verify", "HEAD^{commit}").stdout.decode().strip()
        )
        return FrozenGitHead(oid, object_format)

    def commit_available(self, commit_oid: str) -> bool:
        # Batch lookup reports a missing object on stdout with success; fatal
        # repository/I/O failures must remain errors, not stale-Base diagnoses.
        result = self._run(
            "cat-file",
            "--batch-check=%(objecttype)",
            input=commit_oid.encode("ascii") + b"\n",
        )
        return result.stdout == b"commit\n"

    def is_ancestor(self, commit_oid: str, head: FrozenGitHead) -> bool:
        return (
            self._run(
                "merge-base",
                "--is-ancestor",
                commit_oid,
                head.commit_oid,
                allowed=(0, 1),
            ).exit_code
            == 0
        )

    def freeze_units(
        self,
        head: FrozenGitHead,
        units: tuple[BaseUnit, ...],
    ) -> tuple[FrozenBaseUnit, ...]:
        if not units:
            return ()
        paths = tuple(dict.fromkeys(unit.primary_source for unit in units))
        # No rename inference: each identity's Primary path alone owns provenance.
        status = self._run(
            "status",
            "--porcelain=v1",
            "-z",
            "--no-renames",
            "--untracked-files=all",
            "--ignored=matching",
            "--",
            *paths,
        ).stdout
        dirty = {os.fsdecode(entry[3:]) for entry in status.split(b"\x00") if entry}
        entries = self._run("ls-tree", "-z", head.commit_oid, "--", *paths).stdout
        modes: dict[str, bytes] = {}
        failures: dict[str, SyncBaseGitError] = {}
        for entry in entries.split(b"\x00"):
            if not entry:
                continue
            metadata, path = entry.split(b"\t", 1)
            mode, kind, _oid = metadata.split()
            if kind != b"blob" or mode not in (b"100644", b"100755"):
                failures[os.fsdecode(path)] = SyncBaseGitError(
                    f"committed Primary Source is not a regular file: {os.fsdecode(path)}"
                )
            else:
                modes[os.fsdecode(path)] = mode

        with TemporaryDirectory(prefix="dotman-base-") as temporary:
            root = Path(temporary)
            checkout = root / "checkout"
            checkout.mkdir()
            environment = {
                "GIT_INDEX_FILE": str(root / "index"),
                "GIT_WORK_TREE": str(checkout),
            }
            self._run("read-tree", head.commit_oid, env=environment)
            contents: dict[str, bytes] = {}
            for source in modes:
                try:
                    # Checkout converts committed attributes in the alternate
                    # index/worktree. Run per path so one filter failure cannot
                    # discard successful peers or force a new status observation.
                    self._run(
                        "checkout-index",
                        "--stdin",
                        "-z",
                        env=environment,
                        input=os.fsencode(source) + b"\x00",
                    )
                    path = checkout / source
                    if not stat.S_ISREG(path.lstat().st_mode):
                        raise SyncBaseGitError(
                            f"checkout is not a regular file: {source}"
                        )
                    contents[source] = path.read_bytes()
                except (SyncBaseGitError, OSError) as exc:
                    failures[source] = (
                        exc
                        if isinstance(exc, SyncBaseGitError)
                        else SyncBaseGitError(
                            f"cannot read isolated checkout for {source}: {exc}"
                        )
                    )
            frozen: list[FrozenBaseUnit] = []
            for unit in units:
                source = unit.primary_source
                payload: SyncBasePayload | None = Missing()
                if source in failures:
                    payload = None
                elif source in contents:
                    payload = (
                        FilePresent(contents[source])
                        if unit.identity.child_path is None
                        else DirectoryChildPresent(
                            contents[source], modes[source] == b"100755"
                        )
                    )
                # Ignored untracked directories may be emitted as a directory,
                # even with path-scoped status. Such a source is never clean.
                clean = not any(
                    source == path or source.startswith(path.rstrip("/") + "/")
                    for path in dirty
                )
                frozen.append(
                    FrozenBaseUnit(unit, head, payload, clean, failures.get(source))
                )
            return tuple(frozen)


@dataclass(frozen=True)
class BaseInspection:
    status: Literal["usable", "unavailable", "not-applicable"]
    reason: str | None = None
    record: SyncBaseRecord | None = None


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
    failure: SyncBaseStoreError | SyncBaseGitError | None = None


class SyncBaseLifecycle:
    """No session orchestration: invoke each method at its documented boundary."""

    def __init__(
        self,
        store: SyncBaseStore,
        git: SyncBaseGit,
        *,
        operation: Literal["push", "pull", "sync"],
        preview: bool = False,
    ) -> None:
        if operation not in ("push", "pull", "sync"):
            raise ValueError("invalid Base operation")
        self.store = store
        self.git = git
        self.operation = operation
        self.preview = preview

    def inspect(self, unit: BaseUnit, head: FrozenGitHead) -> BaseInspection:
        """Read only; no checkout, projections, live reads or cleanup."""
        if not unit.eligible:
            return BaseInspection("not-applicable", "ineligible")
        try:
            record = self.store.read(unit.identity_bytes)
        except SyncBaseRecordCorruptionError as exc:
            return BaseInspection("unavailable", exc.reason)
        if record is None:
            return BaseInspection("unavailable", "absent")
        if (
            record.identity != unit.identity_bytes
            or (
                isinstance(record.payload, FilePresent)
                and unit.identity.child_path is not None
            )
            or (
                isinstance(record.payload, DirectoryChildPresent)
                and unit.identity.child_path is None
            )
            or record.envelope.object_format != head.object_format
        ):
            return BaseInspection("unavailable", "record_corrupt")
        if record.envelope.fingerprint != unit.fingerprint:
            return BaseInspection("unavailable", "inputs_changed")
        if not self.git.commit_available(record.envelope.commit_oid):
            return BaseInspection("unavailable", "commit_missing")
        if not self.git.is_ancestor(record.envelope.commit_oid, head):
            return BaseInspection("unavailable", "history_changed")
        return BaseInspection("usable", record=record)

    def selected_policy_resolved(self, unit: BaseUnit) -> BaseLifecycleResult:
        """Real Push/Sync: immediately after selected static resolution, before Guards/review."""
        if self.preview or self.operation == "pull" or unit.eligible:
            return BaseLifecycleResult()
        return BaseLifecycleResult(deleted=self.store.delete(unit.identity_bytes))

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
            frozen, primary_changed=False, live_fact="direct-agreement"
        )

    def complete(
        self,
        frozen: FrozenBaseUnit,
        proposal: ProposalCompletion,
    ) -> BaseLifecycleResult:
        """At the unit's earliest ordered completion point, before its target post-hook."""
        if self.preview or not proposal.ready or self.operation == "pull":
            return BaseLifecycleResult()
        if not frozen.unit.eligible:
            return BaseLifecycleResult(converged=True)
        result = self._acknowledge(
            frozen,
            primary_changed=proposal.primary_effect != "not-required",
            live_fact=proposal.live_fact,
        )
        return BaseLifecycleResult(
            converged=result.acknowledged,
            acknowledged=result.acknowledged,
            live_fact=result.live_fact,
            failure=result.failure,
        )

    def _acknowledge(
        self,
        frozen: FrozenBaseUnit,
        *,
        primary_changed: bool,
        live_fact: str,
    ) -> BaseLifecycleResult:
        try:
            if not self.git.commit_available(
                frozen.head.commit_oid
            ) or not self.git.is_ancestor(frozen.head.commit_oid, frozen.head):
                raise SyncBaseGitError("frozen acknowledgment commit is unavailable")
            self.store.replace(frozen.record(primary_changed=primary_changed))
        except (SyncBaseStoreError, SyncBaseGitError) as exc:
            return BaseLifecycleResult(failure=exc)
        return BaseLifecycleResult(acknowledged=True, live_fact=live_fact)
