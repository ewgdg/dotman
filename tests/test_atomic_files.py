from __future__ import annotations

import os
import re
import stat
import threading
from pathlib import Path

import pytest

import dotman.atomic_files as atomic_files
from dotman.atomic_files import (
    write_bytes_atomic,
    write_symlink_atomic,
    write_text_atomic,
)


def test_overlapping_atomic_writes_do_not_remove_each_others_temporary_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    first_target = tmp_path / "first.txt"
    second_target = tmp_path / "second.txt"
    first_writer_ready_to_replace = threading.Event()
    allow_first_writer_to_replace = threading.Event()
    first_writer_errors: list[BaseException] = []
    original_replace = Path.replace

    def overlap_replacements(temp_path: Path, target_path: Path) -> Path:
        if target_path == first_target:
            first_writer_ready_to_replace.set()
            assert allow_first_writer_to_replace.wait(timeout=5)
        return original_replace(temp_path, target_path)

    monkeypatch.setattr(Path, "replace", overlap_replacements)

    def run_first_writer() -> None:
        try:
            write_bytes_atomic(first_target, b"first\n")
        except BaseException as error:
            first_writer_errors.append(error)

    first_writer = threading.Thread(target=run_first_writer)
    first_writer.start()
    assert first_writer_ready_to_replace.wait(timeout=5)

    try:
        write_bytes_atomic(second_target, b"second\n")
    finally:
        allow_first_writer_to_replace.set()
    first_writer.join(timeout=5)

    assert not first_writer.is_alive()
    assert first_writer_errors == []
    assert first_target.read_bytes() == b"first\n"
    assert second_target.read_bytes() == b"second\n"


def test_stale_temp_sweep_removes_only_valid_names_with_definitely_absent_owners(
    tmp_path: Path,
    monkeypatch,
) -> None:
    token = "a" * 32
    absent_pid = os.getpid() + 100_000
    unverifiable_pid = absent_pid + 1
    live_temp = tmp_path / f".dotman-{os.getpid()}-{token}.tmp"
    absent_temp = tmp_path / f".dotman-{absent_pid}-{token}.tmp"
    unverifiable_temp = tmp_path / f".dotman-{unverifiable_pid}-{token}.tmp"
    malformed_temp = tmp_path / ".dotman-unknown-owner.tmp"
    for temp_path in (live_temp, absent_temp, unverifiable_temp, malformed_temp):
        temp_path.write_text("temporary\n", encoding="utf-8")
    os.utime(live_temp, (0, 0))

    def probe_process(pid: int, signal_number: int) -> None:
        assert signal_number == 0
        if pid == absent_pid:
            raise ProcessLookupError
        if pid == unverifiable_pid:
            raise OSError("liveness cannot be verified")
        raise AssertionError(f"unexpected PID probe: {pid}")

    monkeypatch.setattr(atomic_files.os, "kill", probe_process)

    atomic_files.cleanup_stale_atomic_temp_files(tmp_path)

    assert live_temp.exists()
    assert not absent_temp.exists()
    assert unverifiable_temp.exists()
    assert malformed_temp.exists()


def test_atomic_writer_uses_validated_pid_and_collision_resistant_token_name(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target_path = tmp_path / "config.txt"
    replacement_sources: list[Path] = []
    original_replace = Path.replace

    def record_replacement(temp_path: Path, replacement_path: Path) -> Path:
        replacement_sources.append(temp_path)
        return original_replace(temp_path, replacement_path)

    monkeypatch.setattr(Path, "replace", record_replacement)

    write_bytes_atomic(target_path, b"payload\n")

    assert len(replacement_sources) == 1
    assert re.fullmatch(
        rf"\.dotman-{os.getpid()}-[0-9a-f]{{32}}\.tmp",
        replacement_sources[0].name,
    )



def test_write_bytes_atomic_preserves_existing_file_mode(tmp_path: Path) -> None:
    target_path = tmp_path / "config.txt"
    target_path.write_text("before\n", encoding="utf-8")
    target_path.chmod(0o644)

    write_bytes_atomic(target_path, b"after\n")

    assert target_path.read_text(encoding="utf-8") == "after\n"
    assert stat.S_IMODE(target_path.stat().st_mode) == 0o644



def test_write_bytes_atomic_uses_process_umask_for_new_files(tmp_path: Path) -> None:
    target_path = tmp_path / "config.txt"
    original_umask = os.umask(0o022)
    try:
        write_bytes_atomic(target_path, b"payload\n")
    finally:
        os.umask(original_umask)

    assert target_path.read_text(encoding="utf-8") == "payload\n"
    assert stat.S_IMODE(target_path.stat().st_mode) == 0o644


def test_write_text_atomic_preserves_content_and_existing_file_mode(tmp_path: Path) -> None:
    target_path = tmp_path / "config.txt"
    target_path.write_text("before\n", encoding="utf-8")
    target_path.chmod(0o640)

    write_text_atomic(target_path, "snowman ☃\n")

    assert target_path.read_bytes() == "snowman ☃\n".encode("utf-8")
    assert stat.S_IMODE(target_path.stat().st_mode) == 0o640


def test_write_text_atomic_uses_process_umask_for_new_files(tmp_path: Path) -> None:
    target_path = tmp_path / "config.txt"
    original_umask = os.umask(0o027)
    try:
        write_text_atomic(target_path, "payload\n")
    finally:
        os.umask(original_umask)

    assert target_path.read_text(encoding="utf-8") == "payload\n"
    assert stat.S_IMODE(target_path.stat().st_mode) == 0o640


def test_write_symlink_atomic_preserves_the_destination_file_mode(tmp_path: Path) -> None:
    destination_path = tmp_path / "destination"
    destination_path.write_text("destination\n", encoding="utf-8")
    destination_path.chmod(0o640)
    link_path = tmp_path / "link"
    link_path.write_text("replaced\n", encoding="utf-8")

    write_symlink_atomic(link_path, destination_path)

    assert link_path.is_symlink()
    assert link_path.readlink() == destination_path
    assert stat.S_IMODE(destination_path.stat().st_mode) == 0o640


def test_write_symlink_atomic_cleans_its_temporary_path_in_finally(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def interrupt_replace(temp_path: Path, target_path: Path) -> Path:
        raise KeyboardInterrupt

    monkeypatch.setattr(Path, "replace", interrupt_replace)

    with pytest.raises(KeyboardInterrupt):
        write_symlink_atomic(tmp_path / "link", "destination")

    assert list(tmp_path.glob(".dotman-*.tmp")) == []
