from __future__ import annotations

from tests.cli.test_help import capture_parser_help


def test_info_help_lists_trackable_subcommand(capsys) -> None:
    output = capture_parser_help(capsys, "info")

    assert "Show package or group details with tracked status" in output


def test_info_trackable_help_uses_explicit_query_placeholder(capsys) -> None:
    output = capture_parser_help(capsys, "info", "trackable")

    assert "usage: dotman info trackable [-h] <query>" in output
    assert "Show package or group details with tracked status" in output

