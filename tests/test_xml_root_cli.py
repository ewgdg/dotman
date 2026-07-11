from __future__ import annotations

import io
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from dotman import cli


def test_root_xml_stdin_stdout_selection(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("<config><keep/><drop/></config>"))
    assert cli.main(["transform", "xml", "-", "-", "--mode", "cleanup", "--selectors", "config/keep"]) == 0
    root = ET.fromstring(capsys.readouterr().out)
    assert [child.tag for child in root] == ["keep"]


def test_root_xml_merge_compare_bytes_and_permissions(tmp_path: Path) -> None:
    base = tmp_path / "base.xml"
    overlay = tmp_path / "overlay.xml"
    compare = tmp_path / "compare.xml"
    output = tmp_path / "output.xml"
    base.write_text("<config><local/><managed>old</managed></config>")
    overlay.write_text("<config><managed>repo</managed></config>")
    expected = "<?xml version='1.0'?><config>\n <local/>\n <managed>repo</managed>\n</config>"
    compare.write_text(expected)
    base.chmod(0o640)
    assert cli.main(["transform", "xml", str(base), str(output), "--mode", "merge", "--overlay-file", str(overlay), "--selector-type", "remove", "--selectors", "config/managed", "--compare-file", str(compare)]) == 0
    assert output.read_text() == expected
    assert output.stat().st_mode & 0o777 == 0o640


def test_root_xml_help_and_required_selectors(capsys, tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as result:
        cli.main(["transform", "xml", "--help"])
    assert result.value.code == 0
    help_text = capsys.readouterr().out
    assert "--sort-attributes" in help_text
    assert "--sort-children NODE_PATH" in help_text
    base = tmp_path / "base.xml"
    base.write_text("<config/>")
    with pytest.raises(SystemExit) as result:
        cli.main(["transform", "xml", str(base), "-", "--mode", "cleanup"])
    assert result.value.code == 2
    assert "at least one selector value is required" in capsys.readouterr().err
