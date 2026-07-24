from __future__ import annotations

import io
from pathlib import Path

import pytest
import tomlkit

from dotman import cli


def test_root_toml_stdin_stdout_selection(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO('keep = "yes"\ndrop = "no"\n'))

    assert cli.main(["transform", "toml", "-", "-", "--mode", "cleanup", "--selectors", "keep"]) == 0

    assert tomlkit.parse(capsys.readouterr().out).unwrap() == {"keep": "yes"}


def test_root_toml_merge_compare_text_and_permissions(tmp_path: Path) -> None:
    base = tmp_path / "base.toml"
    overlay = tmp_path / "overlay.toml"
    compare = tmp_path / "compare.toml"
    output = tmp_path / "output.toml"
    base.write_text('local = "yes"\nmanaged = "old"\n', encoding="utf-8")
    overlay.write_text('# exact bytes survive\nmanaged = "repo"\n', encoding="utf-8")
    expected = '# exact bytes survive\nmanaged = "repo"\nlocal = "yes"\n'
    compare.write_text(expected, encoding="utf-8")
    base.chmod(0o640)

    assert cli.main(["transform", "toml", str(base), str(output), "--mode", "merge", "--overlay-file", str(overlay), "--selector-type", "retain", "--selectors", "local", "--compare-file", str(compare)]) == 0

    assert output.read_text(encoding="utf-8") == expected
    assert output.stat().st_mode & 0o777 == 0o640


def test_root_toml_help_and_required_selectors(capsys, tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as result:
        cli.main(["transform", "toml", "--help"])
    assert result.value.code == 0
    help_text = capsys.readouterr().out
    assert "exact TOML key path" in help_text
    assert "--compare-file" in help_text

    base = tmp_path / "base.toml"
    base.write_text('key = "value"\n', encoding="utf-8")
    with pytest.raises(SystemExit) as result:
        cli.main(["transform", "toml", str(base), "-", "--mode", "cleanup"])
    assert result.value.code == 2
    assert "at least one selector value is required" in capsys.readouterr().err


def test_root_toml_compare_reuses_crlf_bytes_for_file_and_stdout(
    tmp_path: Path, capsysbinary
) -> None:
    base = tmp_path / "base.toml"
    compare = tmp_path / "compare.toml"
    output = tmp_path / "output.toml"
    base.write_bytes(b'items = ["a"] # preserve CRLF\n')
    expected = b'items = ["a"] # preserve CRLF\r\n'
    compare.write_bytes(expected)
    args = ["transform", "toml", str(base), str(output), "--mode", "cleanup", "--selectors", "items", "--compare-file", str(compare)]
    assert cli.main(args) == 0
    assert output.read_bytes() == expected

    args[3] = "-"
    assert cli.main(args) == 0
    assert capsysbinary.readouterr().out == expected


def test_root_toml_compare_reuses_crlf_with_blank_separated_table_comment(
    tmp_path: Path,
) -> None:
    base = tmp_path / "base.toml"
    compare = tmp_path / "compare.toml"
    output = tmp_path / "output.toml"
    content = b"[first]\nvalue = 1\n\n# section note\n\n[second]\nvalue = 2\n"
    expected = content.replace(b"\n", b"\r\n")
    base.write_bytes(content)
    compare.write_bytes(expected)

    assert (
        cli.main(
            [
                "transform",
                "toml",
                str(base),
                str(output),
                "--mode",
                "cleanup",
                "--compare-file",
                str(compare),
                "--selector-type",
                "remove",
                "--selectors",
                "missing",
            ]
        )
        == 0
    )

    assert output.read_bytes() == expected


def test_root_toml_remove_keeps_independent_tail_comment_only(tmp_path: Path) -> None:
    base = tmp_path / "base.toml"
    base.write_text("""# document lead

[remove]
x = 1
# attached to remove

# independent

[keep]
y = 2
""", encoding="utf-8")

    assert cli.main(["transform", "toml", str(base), "-", "--mode", "cleanup", "--selector-type", "remove", "--selectors", "remove"]) == 0
    # Use a file output too, avoiding capture details in exact trivia assertion.
    output = tmp_path / "output.toml"
    assert cli.main(["transform", "toml", str(base), str(output), "--mode", "cleanup", "--selector-type", "remove", "--selectors", "remove"]) == 0
    text = output.read_text(encoding="utf-8")
    assert "attached to remove" not in text
    assert "# independent" in text
    assert text.index("# independent") < text.index("[keep]")


def test_root_toml_arrays_of_tables_are_atomic_for_exact_and_regex_cleanup(tmp_path: Path) -> None:
    base = tmp_path / "base.toml"
    base.write_text("""# before services
[[services]]
name = "first"

[[services]]
name = "second"
# attached to services

# independent after services

[tail]
enabled = true
""", encoding="utf-8")
    retained = tmp_path / "retained.toml"
    removed = tmp_path / "removed.toml"
    regex = tmp_path / "regex.toml"

    assert cli.main(["transform", "toml", str(base), str(retained), "--mode", "cleanup", "--selectors", "services"]) == 0
    assert [item["name"] for item in tomlkit.parse(retained.read_text())["services"]] == ["first", "second"]
    assert "tail" not in tomlkit.parse(retained.read_text())
    assert retained.read_text().index("first") < retained.read_text().index("second")

    assert cli.main(["transform", "toml", str(base), str(removed), "--mode", "cleanup", "--selector-type", "remove", "--selectors", "services"]) == 0
    assert "services" not in tomlkit.parse(removed.read_text())
    assert tomlkit.parse(removed.read_text())["tail"]["enabled"] is True
    assert "attached to services" not in removed.read_text()
    assert "# independent after services" in removed.read_text()
    assert removed.read_text().index("# independent after services") < removed.read_text().index("[tail]")

    assert cli.main(["transform", "toml", str(base), str(regex), "--mode", "cleanup", "--selectors", "re:^services$"]) == 0
    assert [item["name"] for item in tomlkit.parse(regex.read_text())["services"]] == ["first", "second"]


def test_root_toml_arrays_of_tables_merge_replacement_and_managed_deletion(tmp_path: Path) -> None:
    base = tmp_path / "base.toml"
    replacement = tmp_path / "replacement.toml"
    deletion = tmp_path / "deletion.toml"
    output = tmp_path / "output.toml"
    base.write_text('local = "keep"\n\n[[services]]\nname = "live"\n# attached to live services\n\n# independent after live services\n\n[tail]\nenabled = true\n', encoding="utf-8")
    replacement.write_text('# repo services\n[[services]]\nname = "repo"\n', encoding="utf-8")
    deletion.write_text('managed = "repo"\n', encoding="utf-8")

    common = ["--mode", "merge", "--selector-type", "remove", "--selectors", "services"]
    assert cli.main(["transform", "toml", str(base), str(output), *common, "--overlay-file", str(replacement)]) == 0
    doc = tomlkit.parse(output.read_text())
    assert [item["name"] for item in doc["services"]] == ["repo"]
    assert doc["local"] == "keep"
    assert "# repo services" in output.read_text()
    assert "attached to live services" not in output.read_text()
    assert "# independent after live services" in output.read_text()
    assert output.read_text().index("# independent after live services") < output.read_text().index("[tail]")

    assert cli.main(["transform", "toml", str(base), str(output), *common, "--overlay-file", str(deletion)]) == 0
    doc = tomlkit.parse(output.read_text())
    assert "services" not in doc
    assert list(doc) == ["local", "managed", "tail"]
    assert "attached to live services" not in output.read_text()
    assert "# independent after live services" in output.read_text()
    assert output.read_text().index("# independent after live services") < output.read_text().index("[tail]")


def test_root_toml_overlay_stdin_and_multiple_stdin_rejection(tmp_path: Path, monkeypatch) -> None:
    base = tmp_path / "base.toml"
    output = tmp_path / "output.toml"
    base.write_text('local = "yes"\nmanaged = "old"\n', encoding="utf-8")
    monkeypatch.setattr("sys.stdin", io.StringIO('managed = "stdin"\n'))
    assert cli.main(["transform", "toml", str(base), str(output), "--mode", "merge", "--overlay-file", "-", "--selectors", "local"]) == 0
    assert tomlkit.parse(output.read_text()).unwrap() == {"local": "yes", "managed": "stdin"}

    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert cli.main(["transform", "toml", "-", str(output), "--mode", "merge", "--overlay-file", "-", "--selectors", "local"]) == 2
