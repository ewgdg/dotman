"""Behavior shared by the JSON, YAML, and plist mapping transforms."""

from __future__ import annotations

import json
import math
from pathlib import Path
import plistlib

import pytest
import yaml

from dotman import cli

MAPPING_FORMATS = {
    "json": (lambda data: json.dumps(data).encode(), lambda content: json.loads(content)),
    "yaml": (lambda data: yaml.safe_dump(data).encode(), lambda content: yaml.safe_load(content)),
    "plist": (plistlib.dumps, plistlib.loads),
}


def write_mapping(path: Path, transform_format: str, data: dict) -> Path:
    serialize, _ = MAPPING_FORMATS[transform_format]
    path.write_bytes(serialize(data))
    return path


def run_transform(transform_format: str, tmp_path: Path, base: dict, *arguments: str) -> object:
    base_path = write_mapping(tmp_path / f"base.{transform_format}", transform_format, base)
    output_path = tmp_path / f"output.{transform_format}"
    assert cli.main(["transform", transform_format, str(base_path), str(output_path), *arguments]) == 0
    _, deserialize = MAPPING_FORMATS[transform_format]
    return deserialize(output_path.read_bytes())


@pytest.mark.parametrize("transform_format", MAPPING_FORMATS)
def test_retain_regex_matching_nothing_retains_nothing(transform_format, tmp_path) -> None:
    base = {"live": 1, "nested": {"value": 2}}

    assert run_transform(
        transform_format, tmp_path, base, "--mode", "cleanup", "--selectors", "re:^absent"
    ) == {}


@pytest.mark.parametrize("transform_format", MAPPING_FORMATS)
def test_merge_with_unmatched_retain_regex_keeps_only_overlay(transform_format, tmp_path) -> None:
    overlay_path = write_mapping(tmp_path / f"overlay.{transform_format}", transform_format, {"managed": 3})

    assert run_transform(
        transform_format,
        tmp_path,
        {"live": 1},
        "--mode",
        "merge",
        "--overlay-file",
        str(overlay_path),
        "--selectors",
        "re:^absent",
    ) == {"managed": 3}


@pytest.mark.parametrize("transform_format", MAPPING_FORMATS)
def test_quoted_empty_segment_selects_the_empty_key(transform_format, tmp_path) -> None:
    base = {"a": {"": "empty", "b": "kept"}, "": "top"}

    assert run_transform(
        transform_format,
        tmp_path,
        base,
        "--mode",
        "cleanup",
        "--selector-type",
        "remove",
        "--selectors",
        'a.""',
    ) == {"a": {"b": "kept"}, "": "top"}


@pytest.mark.parametrize("transform_format", MAPPING_FORMATS)
@pytest.mark.parametrize(
    ("result_value", "stale_compare_value"),
    [(True, 1), (0.0, -0.0), (1, 1.0)],
)
def test_compare_file_is_not_reused_for_a_value_of_another_type(
    transform_format, result_value, stale_compare_value, tmp_path
) -> None:
    compare_path = write_mapping(
        tmp_path / f"compare.{transform_format}", transform_format, {"value": stale_compare_value}
    )

    output = run_transform(
        transform_format,
        tmp_path,
        {"value": result_value},
        "--mode",
        "cleanup",
        "--compare-file",
        str(compare_path),
    )

    output_value = output["value"]
    assert type(output_value) is type(result_value)
    if isinstance(result_value, float):
        assert math.copysign(1, output_value) == math.copysign(1, result_value)
