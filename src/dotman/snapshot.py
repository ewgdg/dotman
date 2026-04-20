from __future__ import annotations

import json
import os
import shutil
import stat
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence

from dotman.config import default_snapshot_root
from dotman.toml_utils import load_toml_file
from dotman.execution import delete_path_and_prune_empty_parents, write_bytes_atomic, write_symlink_atomic
from dotman.models import PackagePlan, SnapshotConfig


FINAL_SNAPSHOT_STATUSES = {"applied", "failed"}
RESTORABLE_SNAPSHOT_STATUSES = {"prepared", *FINAL_SNAPSHOT_STATUSES}


@dataclass(frozen=True)
class SnapshotEntry:
    live_path: Path
    existed_before: bool
    content_path: Path | None
    mode: int | None
    push_action: str
    path_kind: str = "file"
    symlink_target: str | None = None
    preserve_symlink_identity: bool = False
    restore_path: Path | None = None
    repo_name: str | None = None
    selection_label: str | None = None
    package_id: str | None = None
    target_name: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "live_path": str(self.live_path),
            "existed_before": self.existed_before,
            "content_path": str(self.content_path) if self.content_path is not None else None,
            "mode": self.mode,
            "push_action": self.push_action,
            "path_kind": self.path_kind,
            "symlink_target": self.symlink_target,
            "preserve_symlink_identity": self.preserve_symlink_identity,
            "repo_name": self.repo_name,
            "selection_label": self.selection_label,
            "package_id": self.package_id,
            "target_name": self.target_name,
        }
        if self.restore_path is not None:
            payload["restore_path"] = str(self.restore_path)
        return payload


@dataclass(frozen=True)
class SnapshotRecord:
    snapshot_id: str
    created_at: str
    status: str
    root: Path
    entries: tuple[SnapshotEntry, ...]
    restore_count: int = 0
    last_restored_at: str | None = None

    @property
    def entry_count(self) -> int:
        return len(self.entries)

    def to_dict(self) -> dict[str, object]:
        return {
            "snapshot_id": self.snapshot_id,
            "created_at": self.created_at,
            "status": self.status,
            "entry_count": self.entry_count,
            "restore_count": self.restore_count,
            "last_restored_at": self.last_restored_at,
            "path": str(self.root),
            "entries": [entry.to_dict() for entry in self.entries],
        }


@dataclass(frozen=True)
class RollbackAction:
    live_path: Path
    snapshot_path: Path
    action: str
    before_bytes: bytes
    after_bytes: bytes
    desired_mode: int | None
    after_link_target: str | None = None
    restore_path: Path | None = None

    def to_dict(self) -> dict[str, object]:
        payload = {
            "action": self.action,
            "live_path": str(self.live_path),
            "snapshot_path": str(self.snapshot_path),
            "desired_mode": self.desired_mode,
        }
        if self.after_link_target is not None:
            payload["after_link_target"] = self.after_link_target
        if self.restore_path is not None:
            payload["restore_path"] = str(self.restore_path)
        return payload


@dataclass(frozen=True)
class RollbackActionResult:
    action: RollbackAction
    status: str
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            **self.action.to_dict(),
            "status": self.status,
            "error": self.error,
        }


@dataclass(frozen=True)
class RollbackResult:
    snapshot: SnapshotRecord
    actions: tuple[RollbackActionResult, ...]
    status: str

    @property
    def exit_code(self) -> int:
        return 0 if self.status == "ok" else 1

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": "execute",
            "operation": "rollback",
            "status": self.status,
            "snapshot": self.snapshot.to_dict(),
            "actions": [action.to_dict() for action in self.actions],
        }


def create_push_snapshot(plans: Sequence[PackagePlan], snapshot_config: SnapshotConfig) -> SnapshotRecord | None:
    if not snapshot_config.enabled:
        return None

    pending_entries = list(_iter_push_snapshot_entries(plans))
    if not pending_entries:
        return None

    snapshot_id = _new_snapshot_id()
    created_at = _format_snapshot_timestamp(_utc_now())
    snapshot_root = snapshot_config.path / snapshot_id
    entries_root = snapshot_root / "entries"
    entries_root.mkdir(parents=True, exist_ok=False)

    entries: list[SnapshotEntry] = []
    for index, entry in enumerate(pending_entries, start=1):
        live_path = entry["live_path"]
        live_path_is_symlink = live_path.is_symlink()
        if live_path.exists() and live_path.is_dir() and not live_path_is_symlink:
            raise ValueError(f"snapshot capture expects file path, got directory: {live_path}")

        file_symlink_mode = entry.get("file_symlink_mode", "prompt")
        restore_path = live_path.resolve(strict=False) if live_path_is_symlink and file_symlink_mode == "follow" else None
        managed_path = restore_path or live_path
        current_is_symlink = managed_path.is_symlink()
        if managed_path.exists() and managed_path.is_dir() and not current_is_symlink:
            raise ValueError(f"snapshot capture expects file path, got directory: {managed_path}")

        preserve_symlink_identity = live_path_is_symlink and file_symlink_mode != "follow"
        existed_before = managed_path.exists() or (live_path_is_symlink and preserve_symlink_identity)
        content_path = None
        mode = None
        path_kind = "symlink" if live_path_is_symlink else "file"
        symlink_target = os.readlink(live_path) if live_path_is_symlink else None
        if existed_before and not preserve_symlink_identity:
            content_path = Path("entries") / f"{index:04d}.bin"
            (snapshot_root / content_path).write_bytes(managed_path.read_bytes())
            mode = stat.S_IMODE(managed_path.stat().st_mode)
        entries.append(
            SnapshotEntry(
                live_path=live_path,
                existed_before=existed_before,
                content_path=content_path,
                mode=mode,
                push_action=entry["push_action"],
                path_kind=path_kind,
                symlink_target=symlink_target,
                preserve_symlink_identity=preserve_symlink_identity,
                restore_path=restore_path,
                repo_name=entry["repo_name"],
                selection_label=entry["selection_label"],
                package_id=entry["package_id"],
                target_name=entry["target_name"],
            )
        )

    snapshot = SnapshotRecord(
        snapshot_id=snapshot_id,
        created_at=created_at,
        status="prepared",
        root=snapshot_root,
        entries=tuple(entries),
    )
    _write_snapshot_manifest(snapshot)
    return snapshot


def mark_snapshot_status(snapshot: SnapshotRecord, status: str) -> SnapshotRecord:
    updated_snapshot = replace(snapshot, status=status)
    _write_snapshot_manifest(updated_snapshot)
    return updated_snapshot


def record_snapshot_restore(snapshot: SnapshotRecord) -> SnapshotRecord:
    updated_snapshot = replace(
        snapshot,
        restore_count=snapshot.restore_count + 1,
        last_restored_at=_format_snapshot_timestamp(_utc_now()),
    )
    _write_snapshot_manifest(updated_snapshot)
    return updated_snapshot


def list_snapshots(snapshot_root: Path) -> list[SnapshotRecord]:
    if not snapshot_root.exists():
        return []

    snapshots: list[SnapshotRecord] = []
    for child in snapshot_root.iterdir():
        if not child.is_dir():
            continue
        manifest_path = child / "manifest.toml"
        if not manifest_path.exists():
            continue
        snapshots.append(load_snapshot(child))
    return sorted(snapshots, key=lambda snapshot: (snapshot.created_at, snapshot.snapshot_id), reverse=True)


def find_snapshot_matches(snapshot_root: Path, reference: str | None) -> list[SnapshotRecord]:
    snapshots = [snapshot for snapshot in list_snapshots(snapshot_root) if snapshot.status in RESTORABLE_SNAPSHOT_STATUSES]
    if reference is None or reference == "latest":
        return snapshots[:1]

    exact_matches = [snapshot for snapshot in snapshots if snapshot.snapshot_id == reference]
    if exact_matches:
        return exact_matches
    return [snapshot for snapshot in snapshots if snapshot.snapshot_id.startswith(reference)]


def resolve_snapshot(snapshot_root: Path, reference: str | None = None) -> SnapshotRecord:
    matches = find_snapshot_matches(snapshot_root, reference)
    if not matches:
        if reference is None or reference == "latest":
            raise ValueError("no snapshots are available")
        raise ValueError(f"snapshot '{reference}' did not match any available snapshot")
    if len(matches) > 1:
        raise ValueError(
            f"snapshot '{reference}' is ambiguous: " + ", ".join(snapshot.snapshot_id for snapshot in matches)
        )
    return matches[0]


def load_snapshot(snapshot_root: Path) -> SnapshotRecord:
    manifest_path = snapshot_root / "manifest.toml"
    payload = load_toml_file(manifest_path, context="snapshot manifest")
    snapshot_id = payload.get("snapshot_id")
    created_at = payload.get("created_at")
    status = payload.get("status")
    restore_count = payload.get("restore_count", 0)
    last_restored_at = payload.get("last_restored_at")
    entries_payload = payload.get("entries", [])
    if not isinstance(snapshot_id, str) or not isinstance(created_at, str) or not isinstance(status, str):
        raise ValueError(f"invalid snapshot manifest: {manifest_path}")
    if not isinstance(restore_count, int) or restore_count < 0:
        raise ValueError(f"invalid snapshot restore count in manifest: {manifest_path}")
    if last_restored_at is not None and not isinstance(last_restored_at, str):
        raise ValueError(f"invalid snapshot restore timestamp in manifest: {manifest_path}")
    if not isinstance(entries_payload, list):
        raise ValueError(f"invalid snapshot entries in manifest: {manifest_path}")

    entries: list[SnapshotEntry] = []
    for entry_payload in entries_payload:
        if not isinstance(entry_payload, dict):
            raise ValueError(f"invalid snapshot entry in manifest: {manifest_path}")
        live_path = entry_payload.get("live_path")
        existed_before = entry_payload.get("existed_before")
        content_path = entry_payload.get("content_path")
        mode = entry_payload.get("mode")
        push_action = entry_payload.get("push_action")
        path_kind = entry_payload.get("path_kind", "file")
        symlink_target = entry_payload.get("symlink_target")
        preserve_symlink_identity = entry_payload.get("preserve_symlink_identity", False)
        restore_path = entry_payload.get("restore_path")
        if not isinstance(live_path, str) or not isinstance(existed_before, bool) or not isinstance(push_action, str):
            raise ValueError(f"invalid snapshot entry fields in manifest: {manifest_path}")
        if content_path is not None and not isinstance(content_path, str):
            raise ValueError(f"invalid snapshot content path in manifest: {manifest_path}")
        if mode is not None and not isinstance(mode, int):
            raise ValueError(f"invalid snapshot mode in manifest: {manifest_path}")
        if not isinstance(path_kind, str) or path_kind not in {"file", "symlink"}:
            raise ValueError(f"invalid snapshot path kind in manifest: {manifest_path}")
        if symlink_target is not None and not isinstance(symlink_target, str):
            raise ValueError(f"invalid snapshot symlink target in manifest: {manifest_path}")
        if not isinstance(preserve_symlink_identity, bool):
            raise ValueError(f"invalid snapshot symlink identity flag in manifest: {manifest_path}")
        if restore_path is not None and not isinstance(restore_path, str):
            raise ValueError(f"invalid snapshot restore path in manifest: {manifest_path}")
        entries.append(
            SnapshotEntry(
                live_path=Path(live_path),
                existed_before=existed_before,
                content_path=Path(content_path) if content_path is not None else None,
                mode=mode,
                push_action=push_action,
                path_kind=path_kind,
                symlink_target=symlink_target,
                preserve_symlink_identity=preserve_symlink_identity,
                restore_path=Path(restore_path) if restore_path is not None else None,
                repo_name=entry_payload.get("repo_name") if isinstance(entry_payload.get("repo_name"), str) else None,
                selection_label=(
                    entry_payload.get("selection_label")
                    if isinstance(entry_payload.get("selection_label"), str)
                    else entry_payload.get("binding_label")
                    if isinstance(entry_payload.get("binding_label"), str)
                    else None
                ),
                package_id=entry_payload.get("package_id") if isinstance(entry_payload.get("package_id"), str) else None,
                target_name=entry_payload.get("target_name") if isinstance(entry_payload.get("target_name"), str) else None,
            )
        )

    return SnapshotRecord(
        snapshot_id=snapshot_id,
        created_at=created_at,
        status=status,
        root=snapshot_root,
        entries=tuple(entries),
        restore_count=restore_count,
        last_restored_at=last_restored_at,
    )


def prune_snapshots(snapshot_root: Path, *, max_generations: int) -> list[str]:
    pruned_ids: list[str] = []
    for snapshot in list_snapshots(snapshot_root)[max_generations:]:
        shutil.rmtree(snapshot.root)
        pruned_ids.append(snapshot.snapshot_id)
    return pruned_ids


def build_rollback_actions(snapshot: SnapshotRecord) -> list[RollbackAction]:
    actions: list[RollbackAction] = []
    for entry in snapshot.entries:
        restore_path = entry.restore_path or entry.live_path
        current_exists = restore_path.exists()
        current_is_symlink = restore_path.is_symlink()
        if current_exists and restore_path.is_dir() and not current_is_symlink:
            raise ValueError(f"rollback expects file path, got directory: {restore_path}")
        snapshot_path = _snapshot_restore_display_path(snapshot, entry.live_path)

        if entry.preserve_symlink_identity and entry.path_kind == "symlink":
            current_present = current_exists or current_is_symlink
            current_link_target = os.readlink(restore_path) if current_is_symlink else None
            if current_is_symlink and current_link_target == entry.symlink_target:
                action = "noop"
            else:
                action = "update" if current_present else "create"
            actions.append(
                RollbackAction(
                    live_path=entry.live_path,
                    snapshot_path=snapshot_path,
                    action=action,
                    before_bytes=b"",
                    after_bytes=b"",
                    desired_mode=None,
                    after_link_target=entry.symlink_target,
                    restore_path=restore_path,
                )
            )
            continue

        current_bytes = restore_path.read_bytes() if current_exists else b""
        current_mode = stat.S_IMODE(restore_path.stat().st_mode) if current_exists else None

        if entry.existed_before:
            if entry.content_path is None:
                raise ValueError(f"snapshot entry for {entry.live_path} is missing stored content")
            content_file = snapshot.root / entry.content_path
            if not content_file.exists():
                raise ValueError(f"snapshot content is missing for {entry.live_path}")
            desired_bytes = content_file.read_bytes()
            if current_exists and current_bytes == desired_bytes and current_mode == entry.mode:
                action = "noop"
            else:
                action = "update" if current_exists else "create"
            actions.append(
                RollbackAction(
                    live_path=entry.live_path,
                    snapshot_path=snapshot_path,
                    action=action,
                    before_bytes=current_bytes,
                    after_bytes=desired_bytes,
                    desired_mode=entry.mode,
                    restore_path=restore_path,
                )
            )
            continue

        action = "delete" if current_exists else "noop"
        actions.append(
            RollbackAction(
                live_path=entry.live_path,
                snapshot_path=snapshot_path,
                action=action,
                before_bytes=current_bytes,
                after_bytes=b"",
                desired_mode=None,
                restore_path=restore_path,
            )
        )
    return actions


def execute_rollback(snapshot: SnapshotRecord, actions: Sequence[RollbackAction]) -> RollbackResult:
    results: list[RollbackActionResult] = []
    failed = False
    for action in actions:
        if action.action == "noop":
            continue
        try:
            target_path = action.restore_path or action.live_path
            if action.after_link_target is not None:
                if action.action in {"create", "update"}:
                    write_symlink_atomic(target_path, action.after_link_target)
                elif action.action == "delete":
                    delete_path_and_prune_empty_parents(target_path, root=target_path.parent)
                else:
                    raise ValueError(f"unsupported rollback action '{action.action}'")
            elif action.action in {"create", "update"}:
                write_bytes_atomic(target_path, action.after_bytes)
                if action.desired_mode is not None:
                    target_path.chmod(action.desired_mode)
            elif action.action == "delete":
                delete_path_and_prune_empty_parents(target_path, root=target_path.parent)
            else:
                raise ValueError(f"unsupported rollback action '{action.action}'")
            results.append(RollbackActionResult(action=action, status="ok"))
        except Exception as exc:  # noqa: BLE001 - rollback should report the original failure text.
            results.append(RollbackActionResult(action=action, status="failed", error=str(exc)))
            failed = True
            break
    return RollbackResult(
        snapshot=snapshot,
        actions=tuple(results),
        status="failed" if failed else "ok",
    )


def _iter_push_snapshot_entries(plans: Sequence[PackagePlan]):
    seen_live_paths: set[Path] = set()
    for plan in plans:
        selection_label = plan.selection_label
        for target in plan.target_plans:
            if target.directory_items:
                for item in target.directory_items:
                    if item.live_path in seen_live_paths:
                        raise ValueError(f"duplicate snapshot live path: {item.live_path}")
                    seen_live_paths.add(item.live_path)
                    yield {
                        "live_path": item.live_path,
                        "push_action": item.action,
                        "repo_name": plan.repo_name,
                        "selection_label": selection_label,
                        "package_id": target.package_id,
                        "target_name": target.target_name,
                    }
                continue
            if target.action == "noop":
                continue
            if target.live_path in seen_live_paths:
                raise ValueError(f"duplicate snapshot live path: {target.live_path}")
            seen_live_paths.add(target.live_path)
            yield {
                "live_path": target.live_path,
                "push_action": target.action,
                "repo_name": plan.repo_name,
                "selection_label": selection_label,
                "package_id": target.package_id,
                "target_name": target.target_name,
                "file_symlink_mode": target.file_symlink_mode,
            }


def _write_snapshot_manifest(snapshot: SnapshotRecord) -> None:
    snapshot.root.mkdir(parents=True, exist_ok=True)
    manifest_path = snapshot.root / "manifest.toml"
    lines = [
        f"snapshot_id = {json.dumps(snapshot.snapshot_id)}",
        f"created_at = {json.dumps(snapshot.created_at)}",
        f"status = {json.dumps(snapshot.status)}",
        f"entry_count = {snapshot.entry_count}",
        f"restore_count = {snapshot.restore_count}",
    ]
    if snapshot.last_restored_at is not None:
        lines.append(f"last_restored_at = {json.dumps(snapshot.last_restored_at)}")
    lines.append("")
    for entry in snapshot.entries:
        lines.extend(
            [
                "[[entries]]",
                f"live_path = {json.dumps(str(entry.live_path))}",
                f"existed_before = {_toml_bool(entry.existed_before)}",
                f"push_action = {json.dumps(entry.push_action)}",
                f"path_kind = {json.dumps(entry.path_kind)}",
            ]
        )
        if entry.symlink_target is not None:
            lines.append(f"symlink_target = {json.dumps(entry.symlink_target)}")
        lines.append(f"preserve_symlink_identity = {_toml_bool(entry.preserve_symlink_identity)}")
        if entry.restore_path is not None:
            lines.append(f"restore_path = {json.dumps(str(entry.restore_path))}")
        if entry.content_path is not None:
            lines.append(f"content_path = {json.dumps(str(entry.content_path))}")
        if entry.mode is not None:
            lines.append(f"mode = {entry.mode}")
        if entry.repo_name is not None:
            lines.append(f"repo_name = {json.dumps(entry.repo_name)}")
        if entry.selection_label is not None:
            lines.append(f"selection_label = {json.dumps(entry.selection_label)}")
        if entry.package_id is not None:
            lines.append(f"package_id = {json.dumps(entry.package_id)}")
        if entry.target_name is not None:
            lines.append(f"target_name = {json.dumps(entry.target_name)}")
        lines.append("")
    manifest_path.write_text("\n".join(lines), encoding="utf-8")


def _new_snapshot_id() -> str:
    return f"{_utc_now().strftime('%Y-%m-%dT%H-%M-%S-%fZ')}-{uuid.uuid4().hex[:6]}"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _format_snapshot_timestamp(instant: datetime) -> str:
    return instant.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"


def _snapshot_restore_display_path(snapshot: SnapshotRecord, live_path: Path) -> Path:
    relative_live_path = _relative_display_path(live_path)
    return snapshot.root / "restore" / relative_live_path


def _relative_display_path(path: Path) -> Path:
    if path.is_absolute():
        return Path(*path.parts[1:]) if len(path.parts) > 1 else Path("content")
    return path if path.parts else Path("content")
