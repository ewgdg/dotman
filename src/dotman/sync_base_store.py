from __future__ import annotations

import errno
import fcntl
import hashlib
import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import closing, contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Final, Self, TypeAlias

from dotman.config import validate_state_key

STORE_EPOCH: Final = 1
DATABASE_FILE_NAME: Final = "sync-bases.sqlite3"
_LOCK_FILE_NAME: Final = f"{DATABASE_FILE_NAME}.lock"
_PRIVATE_DIRECTORY_MODE: Final = 0o700
_PRIVATE_FILE_MODE: Final = 0o600
_SQLITE_APPLICATION_ID: Final = 0x444D4253  # "DMBS": Dotman Base Store.
_DIGEST_SIZE: Final = hashlib.sha256().digest_size

_STORE_METADATA_SQL = """CREATE TABLE store_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    epoch INTEGER NOT NULL
) STRICT"""
_PAYLOADS_SQL = """CREATE TABLE payloads (
    id INTEGER PRIMARY KEY,
    digest BLOB NOT NULL CHECK (length(digest) = 32),
    byte_length INTEGER NOT NULL CHECK (byte_length >= 0),
    content BLOB NOT NULL
) STRICT"""
_PAYLOAD_LOOKUP_SQL = "CREATE INDEX payload_lookup ON payloads (digest, byte_length)"
_BASE_RECORDS_SQL = """CREATE TABLE base_records (
    identity BLOB PRIMARY KEY NOT NULL,
    shape TEXT NOT NULL CHECK (shape IN ('missing', 'file', 'directory-child')),
    payload_id INTEGER REFERENCES payloads(id) ON DELETE RESTRICT,
    executable INTEGER,
    CHECK (
        (shape = 'missing' AND payload_id IS NULL AND executable IS NULL)
        OR (shape = 'file' AND payload_id IS NOT NULL AND executable IS NULL)
        OR (
            shape = 'directory-child'
            AND payload_id IS NOT NULL
            AND executable IN (0, 1)
        )
    )
) STRICT"""
_EXPECTED_SCHEMA = {
    ("table", "store_metadata"): _STORE_METADATA_SQL,
    ("table", "payloads"): _PAYLOADS_SQL,
    ("index", "payload_lookup"): _PAYLOAD_LOOKUP_SQL,
    ("table", "base_records"): _BASE_RECORDS_SQL,
}
_EXPECTED_STORE_FILES = {
    DATABASE_FILE_NAME,
    _LOCK_FILE_NAME,
    f"{DATABASE_FILE_NAME}-wal",
    f"{DATABASE_FILE_NAME}-shm",
    f"{DATABASE_FILE_NAME}-journal",
}


class SyncBaseStoreError(RuntimeError):
    """A failure that prevents a Sync Base store operation from being trusted."""


class SyncBaseStoreUnsupportedRuntimeError(SyncBaseStoreError):
    """The runtime lacks capabilities required for safe Sync Base storage."""


class SyncBaseStoreSecurityError(SyncBaseStoreError):
    """The store's filesystem layout is not private and trustworthy."""


class SyncBaseStoreLockedError(SyncBaseStoreError):
    """Another process or store instance owns the repository store lock."""


class SyncBaseStoreEpochError(SyncBaseStoreError):
    """The store uses a format epoch this implementation cannot open."""


class SyncBaseStoreCorruptionError(SyncBaseStoreError):
    """The SQLite container or fixed store schema is corrupt."""


class SyncBaseRecordCorruptionError(SyncBaseStoreError):
    """One record or shared payload failed its content integrity contract."""

    def __init__(self, detail: str, *, affected_identities: tuple[bytes, ...]) -> None:
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
    """A committed repository representation in which the Sync Unit is absent."""


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
            self.content,
            field_name="directory-child content",
            allow_empty=True,
        )
        if type(self.executable) is not bool:
            raise TypeError("directory-child executable must be bool")


SyncBasePayload: TypeAlias = Missing | FilePresent | DirectoryChildPresent


@dataclass(frozen=True)
class SyncBaseRecord:
    """One Base keyed by its caller-produced canonical Sync Unit identity bytes."""

    identity: bytes
    payload: SyncBasePayload

    def __post_init__(self) -> None:
        _require_bytes(
            self.identity,
            field_name="canonical identity",
            allow_empty=False,
        )
        if not isinstance(self.payload, (Missing, FilePresent, DirectoryChildPresent)):
            raise TypeError(
                "Sync Base payload must be Missing, FilePresent, or DirectoryChildPresent"
            )


def _sha256_digest(content: bytes) -> bytes:
    return hashlib.sha256(content).digest()


def _identity(status: os.stat_result) -> tuple[int, int]:
    return status.st_dev, status.st_ino


def _validate_status(path: Path, status: os.stat_result, *, directory: bool) -> None:
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
    expected_mode = _PRIVATE_DIRECTORY_MODE if directory else _PRIVATE_FILE_MODE
    if stat.S_IMODE(status.st_mode) != expected_mode:
        raise SyncBaseStoreSecurityError(
            f"Sync Base store path mode must be {expected_mode:#05o}: {path}"
        )
    if not directory and status.st_nlink != 1:
        raise SyncBaseStoreSecurityError(
            f"Sync Base store file must not have hard links: {path}"
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
                    except FileExistsError:
                        pass
                before = os.stat(
                    path.name, dir_fd=parent_descriptor, follow_symlinks=False
                )
                # mkdir does not return an inode handle. Never chmod its path:
                # it could already have been replaced, even just after creation.
                _validate_status(path, before, directory=True)
                descriptor = os.open(
                    path.name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=parent_descriptor,
                )
                self._directories.append((path, descriptor))
                if _identity(os.fstat(descriptor)) != _identity(before):
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
        self.check_directories()
        path = self.directory / name
        before = (
            None
            if create
            else os.stat(name, dir_fd=self.descriptor, follow_symlinks=False)
        )
        if before is not None:
            _validate_status(path, before, directory=False)
        flags = os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        flags |= (os.O_RDWR | os.O_CREAT | os.O_EXCL) if create else os.O_RDONLY
        descriptor = os.open(name, flags, _PRIVATE_FILE_MODE, dir_fd=self.descriptor)
        try:
            opened = os.fstat(descriptor)
            if before is not None and _identity(opened) != _identity(before):
                raise SyncBaseStoreSecurityError(
                    f"Sync Base file changed while opening: {path}"
                )
            if create:
                os.fchmod(descriptor, _PRIVATE_FILE_MODE)
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
        self._files[name] = descriptor
        return descriptor

    def check(self, *, allow_journal: bool = False) -> set[str]:
        self.check_directories()
        names = {
            name
            for name in os.listdir(self.descriptor)
            if name.startswith(DATABASE_FILE_NAME)
        }
        for name in names:
            path = self.directory / name
            if name not in _EXPECTED_STORE_FILES:
                raise SyncBaseStoreSecurityError(
                    f"unexpected Sync Base store file: {path}"
                )
            current = os.stat(name, dir_fd=self.descriptor, follow_symlinks=False)
            _validate_status(path, current, directory=False)
            if name in self._files:
                opened = os.fstat(self._files[name])
                _validate_status(path, opened, directory=False)
                if _identity(current) != _identity(opened):
                    raise SyncBaseStoreSecurityError(
                        f"Sync Base file was substituted: {path}"
                    )
        if not self._files.keys() <= names:
            raise SyncBaseStoreSecurityError("an opened Sync Base file disappeared")
        sidecars = names - {DATABASE_FILE_NAME, _LOCK_FILE_NAME}
        if allow_journal:
            sidecars -= {f"{DATABASE_FILE_NAME}-journal"}
        if sidecars:
            # Do not let SQLite decide whether untrusted recovery evidence is
            # hot, stale or dispensable: even opening it may remove evidence.
            raise SyncBaseStoreCorruptionError(
                "Sync Base store has unexpected recovery sidecar evidence"
            )
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
    except sqlite3.DatabaseError as exc:
        if getattr(exc, "sqlite_errorcode", 0) & 0xFF in (
            sqlite3.SQLITE_BUSY,
            sqlite3.SQLITE_LOCKED,
        ):
            raise SyncBaseStoreLockedError(
                f"Sync Base SQLite transaction is locked: {exc}"
            ) from exc
        raise SyncBaseStoreCorruptionError(
            f"Sync Base SQLite operation failed: {exc}"
        ) from exc
    except OSError as exc:
        raise SyncBaseStoreSecurityError(
            f"Sync Base filesystem operation failed: {exc}"
        ) from exc


def _normalized_sql(value: str) -> str:
    return " ".join(value.split())


class SyncBaseStore:
    """Private, fixed-format persistence with short nonblocking transactions."""

    def __init__(
        self,
        manager_root: Path,
        state_key: str,
        layout: _PrivateLayout,
        lock_descriptor: int,
        database_descriptor: int,
        *,
        read_only: bool,
    ) -> None:
        self.manager_state_root = manager_root
        self.repo_state_key = state_key
        self.repo_state_directory = layout.directory
        self.database_path = layout.directory / DATABASE_FILE_NAME
        self.read_only = read_only
        self._layout = layout
        self._lock_descriptor = lock_descriptor
        self._database_descriptor = database_descriptor
        self._read_connection: sqlite3.Connection | None = None
        self._closed = False

    @classmethod
    def open(
        cls,
        manager_state_root: str | Path,
        repo_state_key: str,
        *,
        read_only: bool = False,
    ) -> SyncBaseStore:
        manager_root = Path(manager_state_root)
        if not manager_root.is_absolute():
            raise ValueError("manager state root must be absolute")
        state_key = validate_state_key(repo_state_key, repo_name=repo_state_key)
        with _store_errors():
            cls._check_runtime()
            layout = _PrivateLayout(manager_root, state_key, create=not read_only)
            try:
                # Acquire an existing lock before examining sidecars: a live
                # writer's journal is contention, not evidence to recover.
                names = set(os.listdir(layout.descriptor))
                lock_descriptor = (
                    layout.open_file(_LOCK_FILE_NAME)
                    if _LOCK_FILE_NAME in names
                    else None
                )
                with (
                    _locked(lock_descriptor, write=False)
                    if lock_descriptor is not None
                    else nullcontext()
                ):
                    names = layout.check()
                    database_descriptor = (
                        layout.open_file(DATABASE_FILE_NAME)
                        if DATABASE_FILE_NAME in names
                        else None
                    )
                    if database_descriptor is not None:
                        with closing(cls._preflight(layout, database_descriptor)):
                            pass
                    elif read_only:
                        raise SyncBaseStoreError("Sync Base store does not exist")
                if lock_descriptor is None:
                    if read_only:
                        raise SyncBaseStoreSecurityError(
                            "Sync Base store lock is missing"
                        )
                    # Only create anything after an existing DB has passed
                    # side-effect-free preflight, including schema and epoch.
                    layout.check()
                    lock_descriptor = layout.open_file(_LOCK_FILE_NAME, create=True)
                with _locked(lock_descriptor, write=not read_only):
                    layout.check()
                    if database_descriptor is None:
                        database_descriptor = layout.open_file(
                            DATABASE_FILE_NAME, create=True
                        )
                        store = cls(
                            manager_root,
                            state_key,
                            layout,
                            lock_descriptor,
                            database_descriptor,
                            read_only=read_only,
                        )
                        with closing(store._writable_connection()) as connection:
                            cls._initialize(connection)
                            layout.check(allow_journal=True)
                            connection.execute("COMMIT")
                    else:
                        with closing(cls._preflight(layout, database_descriptor)):
                            pass
                        store = cls(
                            manager_root,
                            state_key,
                            layout,
                            lock_descriptor,
                            database_descriptor,
                            read_only=read_only,
                        )
                return store
            except BaseException:
                layout.close()
                raise

    @classmethod
    def _check_runtime(cls) -> None:
        # Capability registries identify platform support independently of wrappers
        # around the I/O functions (including fault-injection instrumentation).
        dir_fd_functions = {function.__name__ for function in os.supports_dir_fd}
        fd_functions = {function.__name__ for function in os.supports_fd}
        nofollow_functions = {
            function.__name__ for function in os.supports_follow_symlinks
        }
        if (
            not {"open", "mkdir", "stat"} <= dir_fd_functions
            or "listdir" not in fd_functions
            or "stat" not in nofollow_functions
            or not all(
                hasattr(os, name)
                for name in ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC", "O_NONBLOCK")
            )
            or not all(
                callable(getattr(os, name, None))
                for name in ("pread", "fchmod", "geteuid")
            )
            or not callable(getattr(fcntl, "flock", None))
        ):
            raise SyncBaseStoreUnsupportedRuntimeError(
                "unsupported Sync Base runtime: POSIX descriptor-relative I/O, "
                "no-follow opens and flock are required"
            )
        if sqlite3.sqlite_version_info < (3, 37, 0):
            # The fixed schema uses STRICT tables, introduced in SQLite 3.37.
            raise SyncBaseStoreUnsupportedRuntimeError(
                "unsupported Sync Base runtime: SQLite 3.37 or newer is required"
            )
        # Python >=3.11 alone does not guarantee deserialize: its availability
        # depends on the linked SQLite build. Probe before creating even a directory.
        with closing(
            sqlite3.connect(":memory:", isolation_level=None, timeout=0)
        ) as connection:
            if not callable(getattr(connection, "deserialize", None)):
                raise SyncBaseStoreUnsupportedRuntimeError(
                    "unsupported Sync Base runtime: Python SQLite "
                    "Connection.deserialize support is required"
                )
            cls._configure_connection(connection)

    @staticmethod
    def _configure_connection(connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA busy_timeout = 0")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA trusted_schema = OFF")
        # SQLITE_TEMP_STORE=0 overrides the runtime pragma and forces disk
        # files. Refuse that build before any untrusted schema is evaluated.
        if ("TEMP_STORE=0",) in connection.execute("PRAGMA compile_options").fetchall():
            raise SyncBaseStoreSecurityError(
                "SQLite build forces disk temporary storage"
            )
        connection.execute("PRAGMA temp_store = MEMORY")
        connection.execute("PRAGMA secure_delete = ON")

    @classmethod
    def _preflight(cls, layout: _PrivateLayout, descriptor: int) -> sqlite3.Connection:
        layout.check()
        before = os.fstat(descriptor)
        # Read through the validated inode, not a SQLite pathname. SQLite only
        # sees a memory database, so it cannot recover/unlink an untrusted sidecar.
        content = bytearray()
        while len(content) < before.st_size:
            chunk = os.pread(descriptor, before.st_size - len(content), len(content))
            if not chunk:
                raise SyncBaseStoreCorruptionError(
                    "Sync Base database changed during preflight"
                )
            content.extend(chunk)
        after = os.fstat(descriptor)
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise SyncBaseStoreSecurityError(
                "Sync Base database changed during preflight"
            )
        layout.check()
        # A rollback-format header is required before deserialize; a WAL image
        # is not self-contained and must never silently lose pending commits.
        if content[:16] != b"SQLite format 3\x00" or content[18:20] != b"\x01\x01":
            raise SyncBaseStoreCorruptionError(
                "Sync Base database has an invalid rollback-journal header"
            )
        connection = sqlite3.connect(":memory:", isolation_level=None, timeout=0)
        try:
            cls._configure_connection(connection)
            connection.deserialize(bytes(content))
            connection.execute("PRAGMA query_only = ON")
            cls._validate_existing(connection, journal_mode="memory")
        except BaseException:
            connection.close()
            raise
        return connection

    def _writable_connection(self) -> sqlite3.Connection:
        self._layout.check()
        # Use the native SQLite pathname on Linux and macOS, not a platform's
        # descriptor filesystem. Private ownership and the held transaction lock
        # exclude cooperating substitutions; recheck the pinned inode before SQL.
        # mode=rw forbids SQLite from creating a replacement for a missing file.
        path = self.database_path
        connection = sqlite3.connect(
            path.as_uri() + "?mode=rw", uri=True, isolation_level=None, timeout=0
        )
        try:
            self._layout.check()
            actual_path = Path(connection.execute("PRAGMA database_list").fetchone()[2])
            actual_status = actual_path.lstat()
            _validate_status(actual_path, actual_status, directory=False)
            if _identity(actual_status) != _identity(
                os.fstat(self._database_descriptor)
            ):
                raise SyncBaseStoreSecurityError(
                    "SQLite opened a substituted Sync Base database"
                )
            self._configure_connection(connection)
            # DELETE is SQLite's initial rollback mode, not an on-open conversion.
            if connection.execute("PRAGMA journal_mode").fetchone() != ("delete",):
                raise SyncBaseStoreCorruptionError(
                    "Sync Base store must use DELETE journal mode"
                )
        except BaseException:
            connection.close()
            raise
        return connection

    @classmethod
    def _initialize(cls, connection: sqlite3.Connection) -> None:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(f"PRAGMA application_id = {_SQLITE_APPLICATION_ID}")
        connection.execute(f"PRAGMA user_version = {STORE_EPOCH}")
        for statement in (
            _STORE_METADATA_SQL,
            _PAYLOADS_SQL,
            _PAYLOAD_LOOKUP_SQL,
            _BASE_RECORDS_SQL,
        ):
            connection.execute(statement)
        connection.execute(
            "INSERT INTO store_metadata (singleton, epoch) VALUES (1, ?)",
            (STORE_EPOCH,),
        )
        cls._validate_existing(connection, journal_mode="delete")

    @classmethod
    def _validate_existing(
        cls, connection: sqlite3.Connection, *, journal_mode: str
    ) -> None:
        integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
        if integrity_rows != [("ok",)]:
            detail = "; ".join(str(row[0]) for row in integrity_rows)
            raise SyncBaseStoreCorruptionError(
                f"Sync Base SQLite integrity check failed: {detail}"
            )

        application_id = connection.execute("PRAGMA application_id").fetchone()
        if application_id != (_SQLITE_APPLICATION_ID,):
            raise SyncBaseStoreCorruptionError(
                "database is not a Dotman Sync Base store"
            )
        user_version = connection.execute("PRAGMA user_version").fetchone()
        if user_version is None or user_version[0] != STORE_EPOCH:
            found = None if user_version is None else user_version[0]
            raise SyncBaseStoreEpochError(
                f"unsupported Sync Base store epoch {found}; expected {STORE_EPOCH}"
            )

        schema_rows = connection.execute(
            """SELECT type, name, tbl_name, sql
               FROM sqlite_schema
               ORDER BY type, name"""
        ).fetchall()
        # This fixed schema has exactly one SQLite-created object. Do not use
        # LIKE 'sqlite_%': '_' is a wildcard and would hide ordinary sqliteX
        # tables/triggers. Other internal objects are not part of this format.
        internal = ("index", "sqlite_autoindex_base_records_1", "base_records", None)
        if schema_rows.count(internal) != 1:
            raise SyncBaseStoreCorruptionError("Sync Base primary-key index is invalid")
        actual_schema = {
            (row[0], row[1]): row[3] for row in schema_rows if row != internal
        }
        if set(actual_schema) != set(_EXPECTED_SCHEMA):
            raise SyncBaseStoreCorruptionError(
                "Sync Base store schema objects do not match the fixed epoch"
            )
        for key, expected_sql in _EXPECTED_SCHEMA.items():
            actual_sql = actual_schema[key]
            if not isinstance(actual_sql, str) or _normalized_sql(
                actual_sql
            ) != _normalized_sql(expected_sql):
                raise SyncBaseStoreCorruptionError(
                    f"Sync Base store schema definition is invalid: {key[1]}"
                )

        metadata = connection.execute(
            "SELECT singleton, epoch FROM store_metadata"
        ).fetchall()
        if len(metadata) != 1 or metadata[0][0] != 1:
            raise SyncBaseStoreCorruptionError("Sync Base store metadata is invalid")
        if type(metadata[0][1]) is not int or metadata[0][1] != STORE_EPOCH:
            raise SyncBaseStoreEpochError(
                f"unsupported Sync Base store epoch {metadata[0][1]}; expected {STORE_EPOCH}"
            )

        if connection.execute("PRAGMA journal_mode").fetchone() != (journal_mode,):
            raise SyncBaseStoreCorruptionError(
                "Sync Base store journal mode is invalid"
            )
        foreign_key_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_rows:
            raise SyncBaseStoreCorruptionError(
                "Sync Base store has invalid payload references"
            )

    def _require_open(self) -> None:
        if self._closed:
            raise SyncBaseStoreError("Sync Base store is closed")

    @contextmanager
    def read_transaction(self) -> Iterator[SyncBaseStore]:
        """Read a fresh committed snapshot; nested reads share this transaction."""
        self._require_open()
        if self._read_connection is not None:
            raise SyncBaseStoreError("a Sync Base read transaction is already active")
        with (
            _store_errors(),
            _locked(self._lock_descriptor, write=False),
            closing(
                self._preflight(self._layout, self._database_descriptor)
            ) as connection,
        ):
            connection.execute("BEGIN")
            self._read_connection = connection
            try:
                yield self
            finally:
                self._read_connection = None
                connection.execute("ROLLBACK")

    @contextmanager
    def _write_transaction(self) -> Iterator[sqlite3.Connection]:
        self._require_open()
        if self.read_only:
            raise SyncBaseStoreError("Sync Base store is read-only")
        if self._read_connection is not None:
            raise SyncBaseStoreError(
                "cannot mutate inside a Sync Base read transaction"
            )
        with _store_errors(), _locked(self._lock_descriptor, write=True):
            with closing(self._preflight(self._layout, self._database_descriptor)):
                pass
            with closing(self._writable_connection()) as connection:
                try:
                    self._layout.check()
                    connection.execute("BEGIN IMMEDIATE")
                    self._validate_existing(connection, journal_mode="delete")
                    yield connection
                    # Validate before the commit point, so failure rolls back the
                    # old record and payload. No security checks follow COMMIT.
                    self._layout.check(allow_journal=True)
                    connection.execute("COMMIT")
                except BaseException:
                    if connection.in_transaction:
                        connection.execute("ROLLBACK")
                    raise

    def read(self, identity: bytes) -> SyncBaseRecord | None:
        canonical_identity = _require_bytes(
            identity, field_name="canonical identity", allow_empty=False
        )
        self._require_open()
        if self._read_connection is None:
            with self.read_transaction():
                return self.read(canonical_identity)
        connection = self._read_connection
        try:
            row = connection.execute(
                """SELECT r.shape, r.payload_id, r.executable,
                          p.digest, p.byte_length, p.content
                   FROM base_records AS r
                   LEFT JOIN payloads AS p ON p.id = r.payload_id
                   WHERE r.identity = ?""",
                (canonical_identity,),
            ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise SyncBaseStoreCorruptionError(
                f"cannot read Sync Base record: {exc}"
            ) from exc
        if row is None:
            return None

        shape, payload_id, executable, digest, byte_length, content = row
        if shape == "missing":
            if any(value is not None for value in row[1:]):
                self._raise_record_corruption(
                    connection,
                    "Missing Sync Base unexpectedly references a payload",
                    canonical_identity,
                    payload_id,
                )
            return SyncBaseRecord(canonical_identity, Missing())
        if shape not in {"file", "directory-child"}:
            self._raise_record_corruption(
                connection,
                "Sync Base record has an invalid payload shape",
                canonical_identity,
                payload_id,
            )
        if (
            type(payload_id) is not int
            or type(digest) is not bytes
            or len(digest) != _DIGEST_SIZE
            or type(byte_length) is not int
            or byte_length < 0
            or type(content) is not bytes
            or len(content) != byte_length
            or _sha256_digest(content) != digest
        ):
            self._raise_record_corruption(
                connection,
                "Sync Base payload failed digest or length validation",
                canonical_identity,
                payload_id,
            )
        if shape == "file":
            if executable is not None:
                self._raise_record_corruption(
                    connection,
                    "file Sync Base has unexpected executable state",
                    canonical_identity,
                    payload_id,
                )
            return SyncBaseRecord(canonical_identity, FilePresent(content))
        if executable not in (0, 1) or type(executable) is not int:
            self._raise_record_corruption(
                connection,
                "directory-child Sync Base has invalid executable state",
                canonical_identity,
                payload_id,
            )
        return SyncBaseRecord(
            canonical_identity,
            DirectoryChildPresent(content, executable=bool(executable)),
        )

    @staticmethod
    def _raise_record_corruption(
        connection: sqlite3.Connection,
        detail: str,
        identity: bytes,
        payload_id: object,
    ) -> None:
        affected_identities = (identity,)
        if type(payload_id) is int:
            try:
                rows = connection.execute(
                    "SELECT identity FROM base_records WHERE payload_id = ? ORDER BY identity",
                    (payload_id,),
                ).fetchall()
            except sqlite3.DatabaseError as exc:
                raise SyncBaseStoreCorruptionError(
                    f"cannot identify corrupt Sync Base payload references: {exc}"
                ) from exc
            if rows and all(type(row[0]) is bytes for row in rows):
                affected_identities = tuple(row[0] for row in rows)
        raise SyncBaseRecordCorruptionError(
            detail,
            affected_identities=affected_identities,
        )

    def replace(self, record: SyncBaseRecord) -> None:
        if not isinstance(record, SyncBaseRecord):
            raise TypeError("record must be a SyncBaseRecord")
        # Re-run dataclass validation so an object forged through low-level
        # construction cannot enter a transaction with malformed values.
        record = SyncBaseRecord(record.identity, record.payload)
        if type(record.payload) is FilePresent:
            payload: SyncBasePayload = FilePresent(record.payload.content)
        elif type(record.payload) is DirectoryChildPresent:
            payload = DirectoryChildPresent(
                record.payload.content, record.payload.executable
            )
        elif type(record.payload) is Missing:
            payload = Missing()
        else:
            raise TypeError("Sync Base payload must have an exact supported type")
        record = SyncBaseRecord(record.identity, payload)
        with self._write_transaction() as connection:
            prior_row = connection.execute(
                "SELECT payload_id FROM base_records WHERE identity = ?",
                (record.identity,),
            ).fetchone()
            prior_payload_id = None if prior_row is None else prior_row[0]
            if prior_payload_id is not None and type(prior_payload_id) is not int:
                raise SyncBaseStoreCorruptionError(
                    "existing Sync Base record has an invalid payload reference"
                )

            payload_id: int | None = None
            shape: str
            executable: int | None = None
            if isinstance(record.payload, Missing):
                shape = "missing"
            else:
                payload_id = self._find_or_insert_payload(
                    connection,
                    record.payload.content,
                )
                if isinstance(record.payload, FilePresent):
                    shape = "file"
                else:
                    shape = "directory-child"
                    executable = int(record.payload.executable)
            connection.execute(
                """INSERT INTO base_records (identity, shape, payload_id, executable)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(identity) DO UPDATE SET
                       shape = excluded.shape,
                       payload_id = excluded.payload_id,
                       executable = excluded.executable""",
                (record.identity, shape, payload_id, executable),
            )
            self._garbage_collect_payload(connection, prior_payload_id)

    def _find_or_insert_payload(
        self,
        connection: sqlite3.Connection,
        content: bytes,
    ) -> int:
        digest = _sha256_digest(content)
        if type(digest) is not bytes or len(digest) != _DIGEST_SIZE:
            raise SyncBaseStoreCorruptionError(
                "SHA-256 digest provider returned an invalid digest"
            )
        byte_length = len(content)
        candidates = connection.execute(
            """SELECT id, content FROM payloads
               WHERE digest = ? AND byte_length = ?
               ORDER BY id""",
            (digest, byte_length),
        ).fetchall()
        for payload_id, stored_content in candidates:
            if type(payload_id) is not int or type(stored_content) is not bytes:
                raise SyncBaseStoreCorruptionError(
                    "Sync Base payload lookup returned an invalid row"
                )
            if (
                len(stored_content) != byte_length
                or _sha256_digest(stored_content) != digest
            ):
                reference_rows = connection.execute(
                    "SELECT identity FROM base_records WHERE payload_id = ? ORDER BY identity",
                    (payload_id,),
                ).fetchall()
                if reference_rows and all(
                    type(row[0]) is bytes for row in reference_rows
                ):
                    raise SyncBaseRecordCorruptionError(
                        "stored Sync Base payload failed digest or length validation",
                        affected_identities=tuple(row[0] for row in reference_rows),
                    )
                raise SyncBaseStoreCorruptionError(
                    "unreferenced Sync Base payload failed digest or length validation"
                )
            if stored_content == content:
                return payload_id

        cursor = connection.execute(
            "INSERT INTO payloads (digest, byte_length, content) VALUES (?, ?, ?)",
            (digest, byte_length, content),
        )
        payload_id = cursor.lastrowid
        if type(payload_id) is not int:
            raise SyncBaseStoreCorruptionError(
                "SQLite did not identify the inserted payload"
            )
        inserted = connection.execute(
            "SELECT digest, byte_length, content FROM payloads WHERE id = ?",
            (payload_id,),
        ).fetchone()
        if inserted != (digest, byte_length, content):
            raise SyncBaseStoreCorruptionError(
                "inserted Sync Base payload did not verify exactly"
            )
        return payload_id

    @staticmethod
    def _garbage_collect_payload(
        connection: sqlite3.Connection,
        payload_id: int | None,
    ) -> None:
        if payload_id is None:
            return
        connection.execute(
            """DELETE FROM payloads
               WHERE id = ? AND NOT EXISTS (
                   SELECT 1 FROM base_records WHERE payload_id = payloads.id
               )""",
            (payload_id,),
        )

    def delete(self, identity: bytes) -> bool:
        canonical_identity = _require_bytes(
            identity,
            field_name="canonical identity",
            allow_empty=False,
        )
        with self._write_transaction() as connection:
            prior_row = connection.execute(
                "SELECT payload_id FROM base_records WHERE identity = ?",
                (canonical_identity,),
            ).fetchone()
            prior_payload_id = None if prior_row is None else prior_row[0]
            if prior_payload_id is not None and type(prior_payload_id) is not int:
                raise SyncBaseStoreCorruptionError(
                    "existing Sync Base record has an invalid payload reference"
                )
            cursor = connection.execute(
                "DELETE FROM base_records WHERE identity = ?",
                (canonical_identity,),
            )
            self._garbage_collect_payload(connection, prior_payload_id)
        return cursor.rowcount == 1

    def close(self) -> None:
        if self._read_connection is not None:
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


__all__ = [
    "DATABASE_FILE_NAME",
    "STORE_EPOCH",
    "DirectoryChildPresent",
    "FilePresent",
    "Missing",
    "SyncBasePayload",
    "SyncBaseRecord",
    "SyncBaseRecordCorruptionError",
    "SyncBaseStore",
    "SyncBaseStoreCorruptionError",
    "SyncBaseStoreEpochError",
    "SyncBaseStoreError",
    "SyncBaseStoreLockedError",
    "SyncBaseStoreSecurityError",
]
