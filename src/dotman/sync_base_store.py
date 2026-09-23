from __future__ import annotations

import base64
import binascii
import errno
import fcntl
import hashlib
import json
import os
import re
import stat
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Final, Self, TypeAlias

from dotman.config import validate_state_key

STORE_EPOCH: Final = 1
RECORD_FILE_PREFIX: Final = "sync-base-"
LOCK_FILE_NAME: Final = "sync-bases.lock"
_PRIVATE_DIRECTORY_MODE: Final = 0o700
_PRIVATE_FILE_MODE: Final = 0o600


class SyncBaseStoreError(RuntimeError):
    """A failure that prevents a Sync Base store operation from being trusted."""


class SyncBaseStoreUnsupportedRuntimeError(SyncBaseStoreError):
    """The runtime lacks required secure filesystem capabilities."""


class SyncBaseStoreSecurityError(SyncBaseStoreError):
    """The store's filesystem layout is not private and trustworthy."""


class SyncBaseStoreLockedError(SyncBaseStoreError):
    """Another store operation owns a conflicting lock."""


class SyncBaseStoreEpochError(SyncBaseStoreError):
    """A record uses an unsupported format epoch."""


class SyncBaseStoreCorruptionError(SyncBaseStoreError):
    """The store cannot be interpreted safely."""


class SyncBaseRecordCorruptionError(SyncBaseStoreError):
    """One self-contained record failed integrity validation."""

    def __init__(
        self,
        detail: str,
        *,
        affected_identities: tuple[bytes, ...],
        reason: str = "record_corrupt",
    ) -> None:
        self.reason = reason
        self.detail = detail
        self.affected_identities = affected_identities
        super().__init__(detail)


def _require_bytes(value: object, *, field_name: str, allow_empty: bool) -> bytes:
    if type(value) is not bytes:
        raise TypeError(f"{field_name} must be bytes")
    if not allow_empty and not value:
        raise ValueError(f"{field_name} must not be empty")
    return value


@dataclass(frozen=True)
class Missing:
    """The acknowledged repository representation is absent."""


@dataclass(frozen=True)
class FilePresent:
    content: bytes

    def __post_init__(self) -> None:
        _require_bytes(self.content, field_name="file content", allow_empty=True)


@dataclass(frozen=True)
class DirectoryChildPresent:
    content: bytes
    executable: bool

    def __post_init__(self) -> None:
        _require_bytes(
            self.content, field_name="directory-child content", allow_empty=True
        )
        if type(self.executable) is not bool:
            raise TypeError("directory-child executable must be bool")


SyncBasePayload: TypeAlias = Missing | FilePresent | DirectoryChildPresent


@dataclass(frozen=True)
class SyncBaseEnvelope:
    fingerprint: str

    def __post_init__(self) -> None:
        if (
            type(self.fingerprint) is not str
            or re.fullmatch(r"[0-9a-f]{64}", self.fingerprint) is None
        ):
            raise ValueError("invalid effective-input fingerprint")


@dataclass(frozen=True)
class SyncBaseRecord:
    identity: bytes
    payload: SyncBasePayload
    envelope: SyncBaseEnvelope

    def __post_init__(self) -> None:
        _require_bytes(
            self.identity, field_name="canonical identity", allow_empty=False
        )
        if type(self.envelope) is not SyncBaseEnvelope:
            raise TypeError("Sync Base envelope must be a SyncBaseEnvelope")
        SyncBaseEnvelope(self.envelope.fingerprint)
        if type(self.payload) not in (Missing, FilePresent, DirectoryChildPresent):
            raise TypeError("Sync Base payload must have an exact supported type")
        if type(self.payload) is FilePresent:
            FilePresent(self.payload.content)
        elif type(self.payload) is DirectoryChildPresent:
            DirectoryChildPresent(self.payload.content, self.payload.executable)


@dataclass(frozen=True)
class SyncBaseScan:
    """Validated records and isolated corruption, without exposing unusable metadata."""

    records: tuple[SyncBaseRecord, ...]
    corrupt_count: int


def _identity(status: os.stat_result) -> tuple[int, int]:
    return status.st_dev, status.st_ino


def _validate_inode(path: Path, status: os.stat_result, *, directory: bool) -> None:
    if stat.S_ISLNK(status.st_mode):
        raise SyncBaseStoreSecurityError(
            f"Sync Base store path must not be a symlink: {path}"
        )
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    kind = "directory" if directory else "regular file"
    if not expected_type(status.st_mode):
        raise SyncBaseStoreSecurityError(
            f"Sync Base store path must be a {kind}: {path}"
        )
    if status.st_uid != os.geteuid():
        raise SyncBaseStoreSecurityError(
            f"Sync Base store path has wrong owner: {path}"
        )
    if not directory and status.st_nlink != 1:
        raise SyncBaseStoreSecurityError(
            f"Sync Base store file must not have hard links: {path}"
        )


def _validate_status(path: Path, status: os.stat_result, *, directory: bool) -> None:
    _validate_inode(path, status, directory=directory)
    expected_mode = _PRIVATE_DIRECTORY_MODE if directory else _PRIVATE_FILE_MODE
    if stat.S_IMODE(status.st_mode) != expected_mode:
        raise SyncBaseStoreSecurityError(
            f"Sync Base store path mode must be {expected_mode:#05o}: {path}"
        )


class _PrivateLayout:
    """Pin the private tree; never follow a replaced directory during Python I/O."""

    def __init__(self, manager_root: Path, state_key: str, *, create: bool) -> None:
        self.directory = manager_root / "repos" / state_key
        self._directories: list[tuple[Path, int]] = []
        self._files: dict[str, int] = {}
        self._parent_descriptor: int | None = None
        try:
            if create:
                manager_root.parent.mkdir(parents=True, exist_ok=True)
            # The XDG parent is caller-trusted, outside the private-tree contract.
            self._parent_descriptor = os.open(
                manager_root.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
            )
            parent_descriptor = self._parent_descriptor
            for path in (manager_root, manager_root / "repos", self.directory):
                if create:
                    try:
                        os.mkdir(
                            path.name, _PRIVATE_DIRECTORY_MODE, dir_fd=parent_descriptor
                        )
                        os.fsync(parent_descriptor)
                    except FileExistsError:
                        pass
                before = os.stat(
                    path.name, dir_fd=parent_descriptor, follow_symlinks=False
                )
                _validate_inode(path, before, directory=True)
                descriptor = os.open(
                    path.name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=parent_descriptor,
                )
                self._directories.append((path, descriptor))
                opened = os.fstat(descriptor)
                if _identity(opened) != _identity(before):
                    raise SyncBaseStoreSecurityError(
                        f"Sync Base directory changed while opening: {path}"
                    )
                _validate_status(path, os.fstat(descriptor), directory=True)
                self.check_directories()
                parent_descriptor = descriptor
        except BaseException:
            self.close()
            raise

    @property
    def descriptor(self) -> int:
        return self._directories[-1][1]

    def check_directories(self) -> None:
        for path, descriptor in self._directories:
            current = path.lstat()
            opened = os.fstat(descriptor)
            _validate_status(path, current, directory=True)
            _validate_status(path, opened, directory=True)
            if _identity(current) != _identity(opened):
                raise SyncBaseStoreSecurityError(
                    f"Sync Base directory was substituted: {path}"
                )

    def open_file(self, name: str, *, create: bool = False) -> int:
        descriptor = self._open_file_descriptor(name, create=create)
        self._files[name] = descriptor
        return descriptor

    def _open_file_descriptor(self, name: str, *, create: bool = False) -> int:
        self.check_directories()
        path = self.directory / name
        before = (
            None
            if create
            else os.stat(name, dir_fd=self.descriptor, follow_symlinks=False)
        )
        if before is not None:
            _validate_inode(path, before, directory=False)
        flags = os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        flags |= (os.O_RDWR | os.O_CREAT | os.O_EXCL) if create else os.O_RDONLY
        descriptor = os.open(name, flags, _PRIVATE_FILE_MODE, dir_fd=self.descriptor)
        try:
            opened = os.fstat(descriptor)
            if before is not None and _identity(opened) != _identity(before):
                raise SyncBaseStoreSecurityError(
                    f"Sync Base file changed while opening: {path}"
                )
            _validate_status(path, os.fstat(descriptor), directory=False)
            current = os.stat(name, dir_fd=self.descriptor, follow_symlinks=False)
            _validate_status(path, current, directory=False)
            if _identity(current) != _identity(opened):
                raise SyncBaseStoreSecurityError(
                    f"Sync Base file was substituted: {path}"
                )
            self.check_directories()
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    def file_names(self) -> set[str]:
        # Reusing a scanned directory's open file description can miss newly
        # created entries on Btrfs. Open a fresh stream relative to the pinned
        # directory; dup would share the old enumeration state.
        descriptor = os.open(
            ".",
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=self.descriptor,
        )
        try:
            return set(os.listdir(descriptor))
        finally:
            os.close(descriptor)

    def check(self) -> set[str]:
        self.check_directories()
        names = {
            name
            for name in self.file_names()
            if name.startswith(RECORD_FILE_PREFIX) or name == LOCK_FILE_NAME
        }
        for name in names:
            path = self.directory / name
            current = os.stat(name, dir_fd=self.descriptor, follow_symlinks=False)
            _validate_status(path, current, directory=False)
            if name in self._files and _identity(current) != _identity(
                os.fstat(self._files[name])
            ):
                raise SyncBaseStoreSecurityError(
                    f"Sync Base file was substituted: {path}"
                )
        if not self._files.keys() <= names:
            raise SyncBaseStoreSecurityError("an opened Sync Base file disappeared")
        return names

    def close(self) -> None:
        for descriptor in self._files.values():
            os.close(descriptor)
        self._files.clear()
        for _, descriptor in reversed(self._directories):
            os.close(descriptor)
        self._directories.clear()
        if self._parent_descriptor is not None:
            os.close(self._parent_descriptor)
            self._parent_descriptor = None


@contextmanager
def _locked(descriptor: int, *, write: bool) -> Iterator[None]:
    try:
        fcntl.flock(
            descriptor, (fcntl.LOCK_EX if write else fcntl.LOCK_SH) | fcntl.LOCK_NB
        )
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            raise SyncBaseStoreLockedError(
                "Sync Base store transaction is locked"
            ) from exc
        raise
    try:
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)


@contextmanager
def _store_errors() -> Iterator[None]:
    try:
        yield
    except OSError as exc:
        raise SyncBaseStoreSecurityError(
            f"Sync Base filesystem operation failed: {exc}"
        ) from exc


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def _record_name(identity: bytes) -> str:
    return f"{RECORD_FILE_PREFIX}{hashlib.sha256(identity).hexdigest()}.json"


def _metadata_digest(body: dict[str, object]) -> str:
    # Authenticate the expected payload digest, not its encoded bytes, so intact
    # metadata can distinguish damaged payload from damaged record structure.
    metadata = {key: value for key, value in body.items() if key != "content"}
    return hashlib.sha256(_canonical_json(metadata)).hexdigest()


def _encode(record: SyncBaseRecord) -> bytes:
    payload = record.payload
    body = {
        "epoch": STORE_EPOCH,
        "identity": base64.b64encode(record.identity).decode("ascii"),
        "fingerprint": record.envelope.fingerprint,
        "shape": "missing"
        if isinstance(payload, Missing)
        else "file"
        if isinstance(payload, FilePresent)
        else "directory-child",
        "content": None
        if isinstance(payload, Missing)
        else base64.b64encode(payload.content).decode("ascii"),
        "executable": payload.executable
        if isinstance(payload, DirectoryChildPresent)
        else None,
        "content_digest": None
        if isinstance(payload, Missing)
        else hashlib.sha256(payload.content).hexdigest(),
        "content_size": None if isinstance(payload, Missing) else len(payload.content),
    }
    return _canonical_json({"record": body, "digest": _metadata_digest(body)})


def _decode(
    content: bytes, name: str, expected_identity: bytes | None
) -> SyncBaseRecord:
    try:
        container = json.loads(content)
        if type(container) is not dict or set(container) != {"record", "digest"}:
            raise ValueError("invalid record container")
        body = container["record"]
        if type(body) is not dict or set(body) != {
            "epoch",
            "identity",
            "fingerprint",
            "shape",
            "content",
            "executable",
            "content_digest",
            "content_size",
        }:
            raise ValueError("invalid record fields")
        if container["digest"] != _metadata_digest(body):
            raise ValueError("record digest mismatch")
        if type(body["epoch"]) is not int or body["epoch"] != STORE_EPOCH:
            raise SyncBaseStoreEpochError(
                f"unsupported Sync Base record epoch {body['epoch']}"
            )
        identity = base64.b64decode(body["identity"], validate=True)
        if (
            not identity
            or _record_name(identity) != name
            or (expected_identity is not None and identity != expected_identity)
        ):
            raise ValueError("record identity mismatch")
        envelope = SyncBaseEnvelope(body["fingerprint"])
        shape = body["shape"]
        if shape == "missing":
            if any(
                body[field] is not None
                for field in ("content", "executable", "content_digest", "content_size")
            ):
                raise ValueError("Missing record contains payload")
            payload: SyncBasePayload = Missing()
        else:
            if not (
                (shape == "file" and body["executable"] is None)
                or (shape == "directory-child" and type(body["executable"]) is bool)
            ):
                raise ValueError("invalid payload shape")
            if (
                type(body["content_size"]) is not int
                or body["content_size"] < 0
                or type(body["content_digest"]) is not str
                or re.fullmatch(r"[0-9a-f]{64}", body["content_digest"]) is None
            ):
                raise ValueError("invalid payload metadata")
            try:
                raw = base64.b64decode(body["content"], validate=True)
                if (
                    len(raw) != body["content_size"]
                    or hashlib.sha256(raw).hexdigest() != body["content_digest"]
                    or base64.b64encode(raw).decode("ascii") != body["content"]
                ):
                    raise ValueError("payload integrity mismatch")
            except (ValueError, TypeError, binascii.Error) as exc:
                raise SyncBaseRecordCorruptionError(
                    f"invalid Sync Base payload {name}: {exc}",
                    affected_identities=(identity,),
                    reason="payload_corrupt",
                ) from exc
            payload = (
                FilePresent(raw)
                if shape == "file"
                else DirectoryChildPresent(raw, body["executable"])
            )
        result = SyncBaseRecord(identity, payload, envelope)
        # A single canonical representation rejects duplicate fields and ambiguous encodings.
        if _encode(result) != content:
            raise ValueError("noncanonical record encoding")
        return result
    except (
        ValueError,
        TypeError,
        KeyError,
        UnicodeError,
        binascii.Error,
        RecursionError,
    ) as exc:
        raise SyncBaseRecordCorruptionError(
            f"invalid Sync Base record {name}: {exc}",
            affected_identities=()
            if expected_identity is None
            else (expected_identity,),
        ) from exc


class SyncBaseStore:
    """Private per-unit records with atomic replacement and short nonblocking locks."""

    def __init__(
        self,
        manager_root: Path,
        state_key: str,
        layout: _PrivateLayout,
        lock_descriptor: int,
        *,
        read_only: bool,
    ) -> None:
        self.manager_state_root = manager_root
        self.repo_state_key = state_key
        self.repo_state_directory = layout.directory
        self.read_only = read_only
        self._layout = layout
        self._lock_descriptor = lock_descriptor
        self._reading = False
        self._closed = False

    @classmethod
    def open(
        cls,
        manager_state_root: str | Path,
        repo_state_key: str,
        *,
        read_only: bool = False,
        create: bool = True,
    ) -> SyncBaseStore:
        """Open private storage without changing rejected or read-only layouts."""
        manager_root = Path(manager_state_root)
        if not manager_root.is_absolute():
            raise ValueError("manager state root must be absolute")
        state_key = validate_state_key(repo_state_key, repo_name=repo_state_key)
        create = create and not read_only
        with _store_errors():
            cls._check_runtime()
            layout = _PrivateLayout(manager_root, state_key, create=create)
            try:
                names = layout.check()
                if LOCK_FILE_NAME in names:
                    lock = layout.open_file(LOCK_FILE_NAME)
                elif create and not names:
                    lock = layout.open_file(LOCK_FILE_NAME, create=True)
                    os.fsync(lock)
                    os.fsync(layout.descriptor)
                else:
                    raise SyncBaseStoreSecurityError("Sync Base store lock is missing")
                with _locked(lock, write=False):
                    layout.check()
                return cls(manager_root, state_key, layout, lock, read_only=read_only)
            except BaseException:
                layout.close()
                raise

    @staticmethod
    def _check_runtime() -> None:
        dir_fd_functions = {function.__name__ for function in os.supports_dir_fd}
        if (
            not {"open", "mkdir", "stat", "rename", "unlink"} <= dir_fd_functions
            or "listdir" not in {function.__name__ for function in os.supports_fd}
            or "stat"
            not in {function.__name__ for function in os.supports_follow_symlinks}
            or not all(
                hasattr(os, name)
                for name in (
                    "O_DIRECTORY",
                    "O_NOFOLLOW",
                    "O_CLOEXEC",
                    "O_NONBLOCK",
                    "pread",
                    "geteuid",
                )
            )
            or not callable(getattr(fcntl, "flock", None))
        ):
            raise SyncBaseStoreUnsupportedRuntimeError(
                "Sync Base storage requires POSIX descriptor-relative I/O and flock"
            )

    def _require_open(self) -> None:
        if self._closed:
            raise SyncBaseStoreError("Sync Base store is closed")

    @contextmanager
    def read_transaction(self) -> Iterator[SyncBaseStore]:
        """Hold a coherent view across reads; cooperating writes fail nonblocking."""
        self._require_open()
        if self._reading:
            raise SyncBaseStoreError("a Sync Base read transaction is already active")
        with _store_errors(), _locked(self._lock_descriptor, write=False):
            self._layout.check()
            self._reading = True
            try:
                yield self
            finally:
                self._reading = False

    @contextmanager
    def _write_transaction(self) -> Iterator[None]:
        self._require_open()
        if self.read_only:
            raise SyncBaseStoreError("Sync Base store is read-only")
        if self._reading:
            raise SyncBaseStoreError(
                "cannot mutate inside a Sync Base read transaction"
            )
        with _store_errors(), _locked(self._lock_descriptor, write=True):
            self._layout.check()
            yield

    def _read_name(
        self, name: str, identity: bytes | None = None
    ) -> SyncBaseRecord | None:
        self._layout.check()
        try:
            descriptor = self._layout._open_file_descriptor(name)
        except FileNotFoundError:
            return None
        try:
            before = os.fstat(descriptor)
            content = bytearray()
            while len(content) < before.st_size:
                chunk = os.pread(
                    descriptor, before.st_size - len(content), len(content)
                )
                if not chunk:
                    raise SyncBaseStoreSecurityError(
                        "Sync Base record changed during read"
                    )
                content.extend(chunk)
            after = os.fstat(descriptor)
            current = os.stat(
                name, dir_fd=self._layout.descriptor, follow_symlinks=False
            )
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ) or _identity(current) != _identity(after):
                raise SyncBaseStoreSecurityError("Sync Base record changed during read")
            self._layout.check()
            return _decode(bytes(content), name, identity)
        finally:
            os.close(descriptor)

    def record_path(self, identity: bytes) -> Path:
        """Locate one record for diagnostics without opening or creating storage."""
        identity = _require_bytes(
            identity, field_name="canonical identity", allow_empty=False
        )
        return self.repo_state_directory / _record_name(identity)

    def read(self, identity: bytes) -> SyncBaseRecord | None:
        identity = _require_bytes(
            identity, field_name="canonical identity", allow_empty=False
        )
        self._require_open()
        if not self._reading:
            with self.read_transaction():
                return self.read(identity)
        with _store_errors():
            return self._read_name(_record_name(identity), identity)

    def scan(self) -> SyncBaseScan:
        """Read a coherent inventory; record corruption never hides healthy units."""
        self._require_open()
        if not self._reading:
            with self.read_transaction():
                return self.scan()
        with _store_errors():
            records = []
            corrupt_count = 0
            for name in sorted(self._layout.check()):
                if not name.startswith(RECORD_FILE_PREFIX):
                    continue
                try:
                    record = self._read_name(name)
                except SyncBaseRecordCorruptionError:
                    # A broken envelope may have no recoverable identity. Count the
                    # self-contained file rather than trusting corrupted metadata.
                    corrupt_count += 1
                    continue
                if record is None:
                    raise SyncBaseStoreSecurityError(
                        "Sync Base record disappeared during enumeration"
                    )
                records.append(record)
            return SyncBaseScan(
                tuple(sorted(records, key=lambda record: record.identity)),
                corrupt_count,
            )

    def identities(self) -> tuple[bytes, ...]:
        """Return only identities whose records passed integrity validation."""
        return tuple(record.identity for record in self.scan().records)

    def replace(self, record: SyncBaseRecord) -> None:
        if type(record) is not SyncBaseRecord:
            raise TypeError("record must be a SyncBaseRecord")
        record = SyncBaseRecord(record.identity, record.payload, record.envelope)
        content = _encode(record)
        name = _record_name(record.identity)
        with self._write_transaction():
            # A corrupt record is unavailable, not permission to overwrite it silently.
            self._read_name(name, record.identity)
            temporary = f".sync-base-{uuid.uuid4().hex}.tmp"
            descriptor = self._layout._open_file_descriptor(temporary, create=True)
            try:
                with os.fdopen(descriptor, "wb", closefd=False) as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(descriptor)
                self._layout.check()
                current = os.stat(
                    temporary, dir_fd=self._layout.descriptor, follow_symlinks=False
                )
                _validate_status(
                    self.repo_state_directory / temporary, current, directory=False
                )
                if _identity(current) != _identity(os.fstat(descriptor)):
                    raise SyncBaseStoreSecurityError(
                        "Sync Base temporary file was substituted"
                    )
                os.replace(
                    temporary,
                    name,
                    src_dir_fd=self._layout.descriptor,
                    dst_dir_fd=self._layout.descriptor,
                )
                os.fsync(self._layout.descriptor)
            finally:
                os.close(descriptor)
                try:
                    os.unlink(temporary, dir_fd=self._layout.descriptor)
                except FileNotFoundError:
                    pass

    def _delete(self, identity: bytes) -> bool:
        name = _record_name(identity)
        self._layout.check()
        try:
            descriptor = self._layout._open_file_descriptor(name)
        except FileNotFoundError:
            return False
        os.close(descriptor)
        os.unlink(name, dir_fd=self._layout.descriptor)
        os.fsync(self._layout.descriptor)
        return True

    def delete(self, identity: bytes) -> bool:
        identity = _require_bytes(
            identity, field_name="canonical identity", allow_empty=False
        )
        with self._write_transaction():
            return self._delete(identity)

    def discard_corrupt(self, identity: bytes) -> bool:
        """Revalidate corruption under the write lock before explicitly deleting it."""
        identity = _require_bytes(
            identity, field_name="canonical identity", allow_empty=False
        )
        with self._write_transaction():
            try:
                self._read_name(_record_name(identity), identity)
            except SyncBaseRecordCorruptionError:
                return self._delete(identity)
            return False

    def close(self) -> None:
        if self._reading:
            raise SyncBaseStoreError("cannot close during a Sync Base read transaction")
        if not self._closed:
            self._closed = True
            self._layout.close()

    def __enter__(self) -> Self:
        self._require_open()
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
