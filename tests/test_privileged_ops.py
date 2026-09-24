from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_privileged_ops_write_bytes_atomic_cleans_stale_temp_files(tmp_path: Path) -> None:
    stale_temp_file = tmp_path / f".dotman-999999-{'deadbeef' * 4}.tmp"
    stale_temp_file.write_text("stale\n", encoding="utf-8")
    target_path = tmp_path / "config.txt"

    completed = subprocess.run(
        [sys.executable, "-m", "dotman.privileged_ops", "write-bytes-atomic", str(target_path)],
        input=b"payload\n",
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    assert target_path.read_bytes() == b"payload\n"
    assert not stale_temp_file.exists()



