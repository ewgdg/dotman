from __future__ import annotations

import io
from pathlib import Path
import json

import pytest

import dotman.transforms.framework as MODULE


class DummyEngine(MODULE.BaseTransformEngine):
    name = "dummy"
    SELECTOR_SPECS = (
        MODULE.SelectorSpec(
            name="key",
            prefix="exact",
            is_default=True,
            description="Exact key selector",
        ),
    )

    def transform(self, request: MODULE.TransformRequest) -> MODULE.TransformOutput:
        self.validate_request(request)
        return MODULE.TransformOutput(content="ok\n", mode_reference_path=request.base_path)


def test_selector_spec_records_prefix_and_default_status() -> None:
    spec = MODULE.SelectorSpec(
        name="table_regex",
        prefix="re",
        description="Regex table selector",
    )

    assert spec.prefix == "re"
    assert spec.is_default is False


def test_compile_selector_regexes_reports_invalid_pattern() -> None:
    with pytest.raises(ValueError, match="invalid test selector regex"):
        MODULE.compile_selector_regexes(["["], "test selector")


def test_transform_request_requires_overlay_in_merge_mode(tmp_path: Path) -> None:
    request = MODULE.TransformRequest(
        base_path=tmp_path / "base",
        output_path=tmp_path / "output",
        mode=MODULE.TransformMode.MERGE,
        selector_action=MODULE.SelectorAction.RETAIN,
        selectors_by_type={"key": ("model",)},
    )

    with pytest.raises(ValueError, match="overlay_path is required"):
        request.validate_basic()


def test_transform_request_rejects_overlay_in_cleanup_mode(tmp_path: Path) -> None:
    request = MODULE.TransformRequest(
        base_path=tmp_path / "base",
        output_path=tmp_path / "output",
        mode=MODULE.TransformMode.CLEANUP,
        selector_action=MODULE.SelectorAction.REMOVE,
        selectors_by_type={"key": ("model",)},
        overlay_path=tmp_path / "live",
    )

    with pytest.raises(ValueError, match="only valid when mode=merge"):
        request.validate_basic()


def test_transform_request_requires_output_path_without_stdout(tmp_path: Path) -> None:
    request = MODULE.TransformRequest(
        base_path=tmp_path / "base",
        output_path=None,
        mode=MODULE.TransformMode.CLEANUP,
        selector_action=MODULE.SelectorAction.RETAIN,
        selectors_by_type={"key": ("model",)},
    )

    with pytest.raises(ValueError, match="output_path is required unless stdout output is enabled"):
        request.validate_basic()


def test_transform_request_allows_stdout_without_output_path(tmp_path: Path) -> None:
    request = MODULE.TransformRequest(
        base_path=tmp_path / "base",
        output_path=None,
        mode=MODULE.TransformMode.CLEANUP,
        selector_action=MODULE.SelectorAction.RETAIN,
        selectors_by_type={"key": ("model",)},
        engine_options={"stdout": True},
    )

    request.validate_basic()


def test_base_engine_rejects_unknown_selector_types(tmp_path: Path) -> None:
    engine = DummyEngine()
    request = MODULE.TransformRequest(
        base_path=tmp_path / "base",
        output_path=tmp_path / "output",
        mode=MODULE.TransformMode.CLEANUP,
        selector_action=MODULE.SelectorAction.RETAIN,
        selectors_by_type={"table_regex": (r"^projects\.",)},
    )

    with pytest.raises(ValueError, match="does not support selector types"):
        engine.validate_request(request)


def test_emit_transform_output_decodes_binary_when_stdout_is_text_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    base_path = tmp_path / "base"
    base_path.write_text("ref\n", encoding="utf-8")

    fake_stdout = io.StringIO()
    monkeypatch.setattr(MODULE.sys, "stdout", fake_stdout)

    MODULE.emit_transform_output(
        None,
        MODULE.TransformOutput(content="snowman ☃".encode("utf-8"), mode_reference_path=base_path),
        stdout=True,
    )

    assert fake_stdout.getvalue() == "snowman ☃"


def test_emit_transform_output_skips_rewrite_when_reusing_same_compare_path(
    tmp_path: Path,
) -> None:
    reference_path = tmp_path / "reference"
    output_path = tmp_path / "output"
    reference_path.write_text("ref\n", encoding="utf-8")
    output_path.write_text("keep\n", encoding="utf-8")
    output_path.chmod(0o600)
    output_path.touch()
    original_mtime = output_path.stat().st_mtime_ns

    MODULE.emit_transform_output(
        output_path,
        MODULE.TransformOutput(
            content="keep\n",
            mode_reference_path=reference_path,
            reused_compare_path=output_path,
        ),
    )

    assert output_path.read_text(encoding="utf-8") == "keep\n"
    assert output_path.stat().st_mtime_ns == original_mtime
    assert output_path.stat().st_mode & 0o777 == reference_path.stat().st_mode & 0o777


def test_root_cli_json_transform_is_standalone(tmp_path, monkeypatch, capsys) -> None:
    from dotman import cli

    base = tmp_path / "base.json"
    base.write_text('{"managed": 1, "local": 2}\n', encoding="utf-8")
    monkeypatch.setattr(
        cli.DotmanEngine,
        "from_config_path",
        classmethod(lambda cls, *args, **kwargs: (_ for _ in ()).throw(AssertionError("engine created"))),
    )

    assert cli.main(["transform", "json", str(base), "--mode", "cleanup", "--selectors", "managed", "--stdout"]) == 0
    assert json.loads(capsys.readouterr().out) == {"managed": 1}


def test_root_cli_supports_stdin_and_output_dash(monkeypatch, capsys) -> None:
    from dotman import cli

    monkeypatch.setattr("sys.stdin", io.StringIO('{"a": 1}\n'))
    assert cli.main(["transform", "json", "-", "-", "--mode", "cleanup"]) == 0
    assert json.loads(capsys.readouterr().out) == {"a": 1}


def test_root_cli_rejects_two_stdin_inputs(monkeypatch, capsys) -> None:
    from dotman import cli

    monkeypatch.setattr("sys.stdin", io.StringIO("must not be read"))
    assert cli.main(["transform", "json", "-", "--mode", "merge", "--overlay-file", "-", "--stdout"]) == 2
    assert "at most one" in capsys.readouterr().err


def test_stdout_takes_precedence_over_output_operand(tmp_path, capsys) -> None:
    from dotman import cli

    base = tmp_path / "base.json"
    output = tmp_path / "unused.json"
    base.write_text('{"a": 1}\n', encoding="utf-8")
    assert cli.main(["transform", "json", str(base), str(output), "--mode", "cleanup", "--stdout"]) == 0
    assert json.loads(capsys.readouterr().out) == {"a": 1}
    assert not output.exists()


def test_root_help_documents_json_selector_contract(capsys) -> None:
    from dotman import cli

    with pytest.raises(SystemExit) as exit_info:
        cli.main(["transform", "json", "--help"])
    assert exit_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "Unprefixed selectors use exact:" in help_text
    assert "exact:" in help_text
    assert "re:" in help_text
    assert "dotted or quoted nested JSON object key path" in help_text
    assert "full JSON object key paths" in " ".join(help_text.split())


def test_root_merge_reads_overlay_from_stdin(tmp_path, monkeypatch) -> None:
    from dotman import cli

    base = tmp_path / "base.json"
    output = tmp_path / "output.json"
    base.write_text('{"managed": {"old": 1}, "local": 2}\n', encoding="utf-8")
    monkeypatch.setattr("sys.stdin", io.StringIO('{"managed": {"new": 3}}\n'))

    assert cli.main(["transform", "json", str(base), str(output), "--mode", "merge", "--overlay-file", "-", "--selectors", "managed"]) == 0
    assert json.loads(output.read_text()) == {"managed": {"new": 3}}


def test_root_compare_reuses_raw_bytes_to_stdout(tmp_path, capfdbinary) -> None:
    from dotman import cli

    base = tmp_path / "base.json"
    compare = tmp_path / "compare.json"
    base.write_text('{"value": 1}\n', encoding="utf-8")
    expected = b'{\r\n  "value": 1\r\n}\r\n'
    compare.write_bytes(expected)

    assert cli.main(["transform", "json", str(base), "--mode", "cleanup", "--compare-file", str(compare), "--stdout"]) == 0
    assert capfdbinary.readouterr().out == expected


def test_root_compare_reuses_raw_bytes_at_different_output_path(tmp_path) -> None:
    from dotman import cli

    base = tmp_path / "base.json"
    compare = tmp_path / "compare.json"
    output = tmp_path / "output.json"
    base.write_text('{"value": 1}\n', encoding="utf-8")
    expected = b'{\r\n\t"value": 1\r\n}\r\n'
    compare.write_bytes(expected)

    assert cli.main(["transform", "json", str(base), str(output), "--mode", "cleanup", "--compare-file", str(compare)]) == 0
    assert output.read_bytes() == expected


def test_root_file_output_inherits_base_permissions(tmp_path) -> None:
    from dotman import cli

    base = tmp_path / "base.json"
    output = tmp_path / "output.json"
    base.write_text('{"value": 1}\n', encoding="utf-8")
    base.chmod(0o640)

    assert cli.main(["transform", "json", str(base), str(output), "--mode", "cleanup"]) == 0
    assert output.stat().st_mode & 0o777 == 0o640


def test_root_stdin_base_does_not_sync_permissions(tmp_path, monkeypatch) -> None:
    from dotman import cli

    output = tmp_path / "output.json"
    monkeypatch.setattr("sys.stdin", io.StringIO('{"value": 1}\n'))
    chmod_calls = []
    original_chmod = Path.chmod
    monkeypatch.setattr(Path, "chmod", lambda self, mode: chmod_calls.append((self, mode)))

    assert cli.main(["transform", "json", "-", str(output), "--mode", "cleanup"]) == 0
    assert json.loads(output.read_text()) == {"value": 1}
    assert chmod_calls == []
    monkeypatch.setattr(Path, "chmod", original_chmod)


def test_root_nested_regex_removal_and_mode_validation(tmp_path, capsys) -> None:
    from dotman import cli

    base = tmp_path / "base.json"
    overlay = tmp_path / "overlay.json"
    output = tmp_path / "output.json"
    base.write_text('{"settings": {"secret": 1, "keep": 2}, "other": 3}\n', encoding="utf-8")
    overlay.write_text('{}\n', encoding="utf-8")

    assert cli.main(["transform", "json", str(base), str(output), "--mode", "cleanup", "--selector-type", "remove", "--selectors", "re:^settings\\.secret$"]) == 0
    assert json.loads(output.read_text()) == {"settings": {"keep": 2}, "other": 3}
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["transform", "json", str(base), str(output), "--mode", "cleanup", "--overlay-file", str(overlay)])
    assert exit_info.value.code == 2
    assert "only valid when mode=merge" in capsys.readouterr().err
