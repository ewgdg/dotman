from __future__ import annotations

import io
from pathlib import Path
import plistlib

import pytest

from dotman import cli


def dump(path: Path, data: dict, fmt=plistlib.FMT_XML) -> None:
    path.write_bytes(plistlib.dumps(data, fmt=fmt, sort_keys=True))


def test_root_plist_binary_stdout_and_nested_selection(tmp_path: Path, capfdbinary) -> None:
    base = tmp_path / "base.plist"
    dump(base, {"settings": {"keep": 1, "drop": 2}, "other": 3})

    assert cli.main(["transform", "plist", str(base), "-", "--mode", "cleanup", "--selectors", "settings.keep", "--output-format", "binary"]) == 0

    raw = capfdbinary.readouterr().out
    assert raw.startswith(b"bplist00")
    assert plistlib.loads(raw) == {"settings": {"keep": 1}}


def test_root_plist_binary_stdin_merge_and_permissions(tmp_path: Path, monkeypatch) -> None:
    overlay = tmp_path / "overlay.plist"
    output = tmp_path / "output.plist"
    dump(overlay, {"managed": "repo"})
    stdin = io.TextIOWrapper(io.BytesIO(plistlib.dumps({"local": True, "managed": "old"}, fmt=plistlib.FMT_BINARY)))
    monkeypatch.setattr("sys.stdin", stdin)

    assert cli.main(["transform", "plist", "-", str(output), "--mode", "merge", "--overlay-file", str(overlay), "--selector-type", "remove", "--selectors", "managed", "--output-format", "binary"]) == 0
    assert plistlib.loads(output.read_bytes()) == {"local": True, "managed": "repo"}
    assert output.read_bytes().startswith(b"bplist00")


def test_root_plist_compare_reuses_bytes_and_base_mode(tmp_path: Path) -> None:
    base = tmp_path / "base.plist"
    compare = tmp_path / "compare.plist"
    output = tmp_path / "output.plist"
    dump(base, {"value": 1}, plistlib.FMT_BINARY)
    dump(compare, {"value": 1}, plistlib.FMT_XML)
    base.chmod(0o640)

    assert cli.main(["transform", "plist", str(base), str(output), "--mode", "cleanup", "--compare-file", str(compare), "--output-format", "binary"]) == 0
    assert output.read_bytes() == compare.read_bytes()
    assert output.stat().st_mode & 0o777 == 0o640


def test_root_plist_compare_does_not_reuse_bool_for_integer(tmp_path: Path) -> None:
    base = tmp_path / "base.plist"
    compare = tmp_path / "compare.plist"
    output = tmp_path / "output.plist"
    dump(base, {"value": 1})
    dump(compare, {"value": True})

    assert cli.main(["transform", "plist", str(base), str(output), "--mode", "cleanup", "--compare-file", str(compare), "--output-format", "binary"]) == 0

    assert output.read_bytes().startswith(b"bplist00")
    value = plistlib.loads(output.read_bytes())["value"]
    assert type(value) is int
    assert value == 1


def test_root_plist_compare_checks_nested_dict_and_list_value_types(tmp_path: Path) -> None:
    base = tmp_path / "base.plist"
    compare = tmp_path / "compare.plist"
    output = tmp_path / "output.plist"
    dump(base, {"nested": {"values": [0, {"value": 1}]}})
    dump(compare, {"nested": {"values": [False, {"value": True}]}})

    assert cli.main(["transform", "plist", str(base), str(output), "--mode", "cleanup", "--compare-file", str(compare), "--output-format", "binary"]) == 0

    values = plistlib.loads(output.read_bytes())["nested"]["values"]
    assert type(values[0]) is int
    assert type(values[1]["value"]) is int
    assert values == [0, {"value": 1}]


def test_root_plist_help_and_invalid_regex(capsys, tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as result:
        cli.main(["transform", "plist", "--help"])
    assert result.value.code == 0
    help_text = capsys.readouterr().out
    assert "--output-format {xml,binary}" in help_text
    assert "Unprefixed selectors use exact:" in help_text

    base = tmp_path / "base.plist"
    dump(base, {})
    with pytest.raises(SystemExit) as result:
        cli.main(["transform", "plist", str(base), "-", "--mode", "cleanup", "--selectors", "re:["])
    assert result.value.code == 2
    assert "invalid plist key path selector regex" in capsys.readouterr().err
