"""Metadata-only Sync Base workflows; never observe drift or execute projections."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import hashlib
import os

from dotman.models import ResolvedSyncTarget
from dotman.operation_lock import OperationLock
from dotman.sync_base_lifecycle import (
    BaseInspection, SyncBaseGit, SyncBaseLifecycle, record_matches_identity,
)
from dotman.sync_base_store import (
    DATABASE_FILE_NAME, Missing, DirectoryChildPresent,
    SyncBaseStore, SyncBaseStoreError, SyncBaseRecordCorruptionError,
)
from dotman.sync_directory import census_directory, child_metadata
from dotman.sync_observation import _base_unit, _resolve_inputs
from dotman.sync_scope import _parse_scope_selector, resolve_sync_scope, sync_unit_identity_bytes


def _identity(text: str) -> ResolvedSyncTarget:
    parsed = _parse_scope_selector(text)
    if parsed.target_name is None or any(character in text for character in "*?[]"):
        raise ValueError("Sync Base requires one exact repo-qualified file or directory-child identity")
    identity = ResolvedSyncTarget(
        repo=parsed.repo, package_id=parsed.package_id, target_name=parsed.target_name,
        bound_profile=parsed.bound_profile, child_path=parsed.child_path,
    )
    if identity.canonical != text:
        raise ValueError("Sync Base identity must be canonical")
    sync_unit_identity_bytes(identity)
    return identity


def _unit(context, inputs, identity):
    entry = inputs.get(replace(identity, child_path=None))
    if entry is None:
        raise ValueError(f"Sync Unit '{identity.canonical}' is not currently tracked")
    item, metadata = entry
    directory = metadata.target.target_type == "directory"
    if metadata.target.probe is not None:
        raise ValueError("Probe Work is not a Sync Unit")
    if directory != (identity.child_path is not None):
        raise ValueError("Sync Base requires a file target or an exact directory child")
    if directory:
        metadata = child_metadata(metadata, identity.child_path)
    return _base_unit(context, identity, item, metadata)


def _store_exists(context, repo) -> bool:
    directory = context.tracked_state.state_root / "repos" / repo.config.state_key
    try:
        return any(name.startswith(DATABASE_FILE_NAME) for name in os.listdir(directory))
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise SyncBaseStoreError(f"{repo.config.name}: {directory / DATABASE_FILE_NAME}: {exc}") from exc


@contextmanager
def _open(context, repo, *, read_only=True):
    try:
        # Inspection and exact reset must not fill gaps in an existing store.
        with SyncBaseStore.open(
            context.tracked_state.state_root, repo.config.state_key,
            read_only=read_only, create=False,
        ) as store:
            yield store
    except SyncBaseStoreError as exc:
        path = context.tracked_state.state_root / "repos" / repo.config.state_key / DATABASE_FILE_NAME
        raise SyncBaseStoreError(f"{repo.config.name}: {path}: {exc}") from exc


def _detail(unit, inspection):
    result = {
        "identity": unit.identity.canonical, "status": inspection.status,
        "reason": inspection.reason, "policy": unit.configured_policy,
        "eligibility": unit.eligible, "commit": None, "provenance": None,
        "payload": None,
        "checks": {"integrity": None, "fingerprint_match": None, "commit_available": None, "ancestry": None},
    }
    # Do not reveal any authoritative-looking metadata from an unusable record.
    record = inspection.record
    if inspection.status == "usable":
        payload = record.payload
        missing = isinstance(payload, Missing)
        result.update(
            commit=record.envelope.commit_oid, provenance=record.envelope.provenance,
            payload={
                "kind": "missing" if missing else "present",
                "size": 0 if missing else len(payload.content),
                "digest": None if missing else hashlib.sha256(payload.content).hexdigest(),
                "executable": payload.executable if isinstance(payload, DirectoryChildPresent) else None,
            },
            checks={"integrity": True, "fingerprint_match": True, "commit_available": True, "ancestry": True},
        )
    else:
        reason = inspection.reason
        checks = result["checks"]
        if reason in ("record_corrupt", "payload_corrupt"):
            checks["integrity"] = False
        elif reason in ("inputs_changed", "commit_missing", "history_changed"):
            checks["integrity"] = True
            checks["fingerprint_match"] = reason != "inputs_changed"
            if reason != "inputs_changed":
                checks["commit_available"] = reason != "commit_missing"
            if reason == "history_changed":
                checks["ancestry"] = False
    return result


def info_sync_base(context, text: str):
    identity = _identity(text)
    scope = resolve_sync_scope(context, [text])
    inputs, _ = _resolve_inputs(context, scope)
    unit = _unit(context, inputs, identity)
    repo = context.repositories[identity.repo]
    if not _store_exists(context, repo):
        return _detail(unit, BaseInspection(
            "unavailable" if unit.eligible else "not-applicable",
            "absent" if unit.eligible else "ineligible",
        ))
    with _open(context, repo) as store, store.read_transaction():
        # An empty store needs no Git ancestry proof, including unborn repositories.
        if unit.eligible and unit.identity_bytes not in store.identities():
            return _detail(unit, BaseInspection("unavailable", "absent"))
        git = SyncBaseGit(repo.root, context.command_runtime)
        head = git.freeze_head() if unit.eligible else None
        return _detail(unit, SyncBaseLifecycle(store, git, operation="sync", preview=True).inspect(unit, head))


def list_sync_bases(context):
    scope = resolve_sync_scope(context)
    inputs, _ = _resolve_inputs(context, scope)
    entries = []
    for repo_config in context.config.ordered_repos:
        repo = context.repositories[repo_config.name]
        if not _store_exists(context, repo):
            continue
        with _open(context, repo) as store, store.read_transaction():
            git = SyncBaseGit(repo.root, context.command_runtime)
            head = None
            lifecycle = SyncBaseLifecycle(store, git, operation="sync", preview=True)
            for key in store.identities():
                try:
                    identity = _identity(key.decode("utf-8"))
                    if identity.repo != repo.config.name:
                        continue
                    unit = _unit(context, inputs, identity)
                except (ValueError, UnicodeError):
                    continue
                if not unit.eligible:
                    continue
                if head is None:
                    head = git.freeze_head()
                inspected = lifecycle.inspect(unit, head)
                if inspected.status == "usable":
                    entries.append(_detail(unit, inspected))
    return sorted(entries, key=lambda entry: entry["identity"])


def reset_sync_base(context, text: str):
    identity = _identity(text)
    with OperationLock.acquire(context.tracked_state.state_root):
        scope = resolve_sync_scope(context, [text])
        inputs, _ = _resolve_inputs(context, scope)
        _unit(context, inputs, identity)
        repo = context.repositories[identity.repo]
        deleted = False
        if _store_exists(context, repo):
            with _open(context, repo, read_only=False) as store:
                deleted = store.delete(sync_unit_identity_bytes(identity))
        return {"identity": text, "status": "reset" if deleted else "already_absent"}


def doctor_sync_bases(context):
    from dotman.doctor import DoctorCheck

    checks = []
    # Failed static resolution is not evidence that stored identities are orphaned.
    try:
        scope = resolve_sync_scope(context)
        inputs, _ = _resolve_inputs(context, scope)
    except ValueError:
        inputs = None
    censuses = {}
    for repo_config in context.config.ordered_repos:
        repo = context.repositories[repo_config.name]
        path = context.tracked_state.state_root / "repos" / repo.config.state_key / DATABASE_FILE_NAME
        try:
            if not _store_exists(context, repo):
                continue
            corrupt = set()
            orphaned = 0
            with _open(context, repo) as store, store.read_transaction():
                for key in store.identities():
                    try:
                        record = store.read(key)
                        identity = _identity(key.decode("utf-8"))
                        if (
                            identity.repo != repo.config.name
                            or not record_matches_identity(record, identity)
                        ):
                            corrupt.add(key)
                            continue
                    except SyncBaseRecordCorruptionError as exc:
                        corrupt.update(exc.affected_identities)
                        continue
                    except (ValueError, UnicodeError):
                        corrupt.add(key)
                        continue
                    if inputs is not None:
                        try:
                            _unit(context, inputs, identity)
                        except ValueError:
                            orphaned += 1
                            continue
                        if identity.child_path is not None:
                            target = replace(identity, child_path=None)
                            item, metadata = inputs[target]
                            # Inspection cannot execute Guards to prove participation.
                            guarded_scopes = (item.repo, metadata.package, metadata.target, *metadata.path_rules)
                            if any(
                                (scope.hooks or {}).get(name)
                                for scope in guarded_scopes
                                for name in ("guard_push", "guard_pull")
                            ):
                                continue
                            if target not in censuses:
                                censuses[target] = census_directory(
                                    metadata,
                                    follow_live_directories=context.config.dir_symlink_mode == "follow",
                                )
                            census = censuses[target]
                            # Exclusions and failed discovery cannot prove absence.
                            if census.unrestricted and identity.child_path not in dict(census.entries):
                                orphaned += 1
            for category, count in (("corrupt", len(corrupt)), ("orphaned", orphaned)):
                checks.append(DoctorCheck(
                    key=f"sync_bases_{category}", status="warn" if count else "ok",
                    detail=f"{count} {category} Sync Bases", path=path, repo_name=repo.config.name, count=count,
                ))
        except (SyncBaseStoreError, OSError) as exc:
            checks.append(DoctorCheck(
                key="sync_bases_store", status="failed", detail=str(exc), path=path,
                repo_name=repo.config.name,
            ))
    return checks
