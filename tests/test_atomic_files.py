from __future__ import annotations

import os
import stat
from pathlib import Path

from dotman.atomic_files import write_bytes_atomic



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
