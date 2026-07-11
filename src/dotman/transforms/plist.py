#!/usr/bin/env python3

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import plistlib
import re
from typing import Any

from dotman.transforms.cli import run_engine_cli
from dotman.transforms.framework import (
    BaseTransformEngine,
    SelectorAction,
    SelectorSpec,
    TransformMode,
    TransformOutput,
    TransformRequest,
    compile_selector_regexes,
    emit_transform_output,
)


PlistDict = dict[str, Any]
PlistKeyPath = tuple[str, ...]
KeyRegex = re.Pattern[str]
_MISSING = object()


@dataclass
class PlistPathSelector:
    include_subtree: bool = False
    children: dict[str, "PlistPathSelector"] = field(default_factory=dict)


def load_plist(path: Path, *, stdin_bytes: bytes | None = None) -> PlistDict:
    if path == Path("-"):
        assert stdin_bytes is not None
        loaded = plistlib.loads(stdin_bytes)
    elif not path.exists():
        return {}
    else:
        with path.open("rb") as handle:
            loaded = plistlib.load(handle)

    if not isinstance(loaded, dict):
        raise ValueError(f"Expected plist dictionary in {path}")

    return loaded


def compile_key_regexes(raw_key_regexes: tuple[str, ...]) -> tuple[KeyRegex, ...]:
    return compile_selector_regexes(raw_key_regexes, "plist key path selector")


def parse_plist_key_path(raw_key: str) -> PlistKeyPath:
    key_path = tuple(split_plist_key(raw_key))
    if not key_path:
        raise ValueError("plist key paths must not be empty")
    return key_path


def split_plist_key(raw_key: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    in_quotes = False
    escape = False

    for char in raw_key:
        if in_quotes and escape:
            current.append(char)
            escape = False
            continue

        if in_quotes and char == "\\":
            escape = True
            continue

        if char == '"':
            in_quotes = not in_quotes
            continue

        if char == "." and not in_quotes:
            append_plist_key_part(parts, current)
            current = []
            continue

        current.append(char)

    if escape:
        current.append("\\")
    if in_quotes:
        raise ValueError(f"unterminated quoted plist key path: {raw_key}")

    append_plist_key_part(parts, current)
    return parts


def append_plist_key_part(parts: list[str], current: list[str]) -> None:
    key_part = "".join(current)
    if key_part:
        parts.append(key_part)


def parse_plist_key_paths(raw_key_paths: tuple[str, ...]) -> tuple[PlistKeyPath, ...]:
    return tuple(parse_plist_key_path(raw_key) for raw_key in raw_key_paths)


def build_plist_path_selector(key_paths: tuple[PlistKeyPath, ...]) -> PlistPathSelector:
    root = PlistPathSelector()
    for key_path in key_paths:
        current = root
        for key_part in key_path:
            if current.include_subtree:
                break
            current = current.children.setdefault(key_part, PlistPathSelector())
        current.include_subtree = True
        current.children.clear()
    return root


def plist_key_path_text(key_path: PlistKeyPath) -> str:
    return ".".join(key_path)


def matches_key_regexes(
    key_path: PlistKeyPath,
    key_regexes: tuple[KeyRegex, ...],
) -> bool:
    path_text = plist_key_path_text(key_path)
    return any(key_regex.search(path_text) for key_regex in key_regexes)


def iter_plist_key_paths(
    value: Any,
    prefix: PlistKeyPath = (),
) -> tuple[PlistKeyPath, ...]:
    if not isinstance(value, dict):
        return ()

    key_paths: list[PlistKeyPath] = []
    for key, child_value in value.items():
        key_path = prefix + (key,)
        key_paths.append(key_path)
        key_paths.extend(iter_plist_key_paths(child_value, key_path))
    return tuple(key_paths)


def plist_key_paths_matching_regexes(
    data: PlistDict,
    key_regexes: tuple[KeyRegex, ...],
) -> tuple[PlistKeyPath, ...]:
    if not key_regexes:
        return ()
    return tuple(
        key_path
        for key_path in iter_plist_key_paths(data)
        if matches_key_regexes(key_path, key_regexes)
    )


def selected_plist_key_paths(
    data: PlistDict,
    exact_key_paths: tuple[PlistKeyPath, ...],
    key_regexes: tuple[KeyRegex, ...],
) -> tuple[PlistKeyPath, ...]:
    return exact_key_paths + plist_key_paths_matching_regexes(data, key_regexes)


def retained_plist_value(value: Any, selector: PlistPathSelector) -> Any:
    if selector.include_subtree:
        return value
    if not isinstance(value, dict):
        return _MISSING

    retained_data: PlistDict = {}
    for key, child_value in value.items():
        child_selector = selector.children.get(key)
        if child_selector is None:
            continue
        retained_value = retained_plist_value(child_value, child_selector)
        if retained_value is not _MISSING:
            retained_data[key] = retained_value

    if not retained_data:
        return _MISSING
    return retained_data


def stripped_plist_value(value: Any, selector: PlistPathSelector) -> Any:
    if selector.include_subtree:
        return _MISSING
    if not isinstance(value, dict):
        return value

    stripped_data: PlistDict = {}
    for key, child_value in value.items():
        child_selector = selector.children.get(key)
        if child_selector is None:
            stripped_data[key] = child_value
            continue
        stripped_value = stripped_plist_value(child_value, child_selector)
        if stripped_value is not _MISSING:
            stripped_data[key] = stripped_value

    return stripped_data


def filter_retained_keys(
    data: PlistDict,
    retained_key_paths: tuple[PlistKeyPath, ...],
    retained_key_regexes: tuple[KeyRegex, ...] = (),
) -> PlistDict:
    if not retained_key_paths and not retained_key_regexes:
        return dict(data)

    selected_key_paths = selected_plist_key_paths(
        data,
        retained_key_paths,
        retained_key_regexes,
    )
    retained_value = retained_plist_value(
        data,
        build_plist_path_selector(selected_key_paths),
    )
    return {} if retained_value is _MISSING else retained_value


def filter_stripped_keys(
    data: PlistDict,
    stripped_key_paths: tuple[PlistKeyPath, ...],
    stripped_key_regexes: tuple[KeyRegex, ...] = (),
) -> PlistDict:
    if not stripped_key_paths and not stripped_key_regexes:
        return dict(data)

    selected_key_paths = selected_plist_key_paths(
        data,
        stripped_key_paths,
        stripped_key_regexes,
    )
    stripped_value = stripped_plist_value(
        data,
        build_plist_path_selector(selected_key_paths),
    )
    return {} if stripped_value is _MISSING else stripped_value


def select_plist_data(
    data: PlistDict,
    selector_action: SelectorAction,
    selected_key_paths: tuple[PlistKeyPath, ...],
    selected_key_regexes: tuple[KeyRegex, ...] = (),
) -> PlistDict:
    if selector_action == SelectorAction.REMOVE:
        return filter_stripped_keys(data, selected_key_paths, selected_key_regexes)
    return filter_retained_keys(data, selected_key_paths, selected_key_regexes)


def should_recurse_overlay(selector: PlistPathSelector | None) -> bool:
    return selector is not None and not selector.include_subtree and bool(selector.children)


def overlay_plist_dicts(
    original_base_data: PlistDict,
    preserved_base_data: PlistDict,
    overlay_data: PlistDict,
    path_selector: PlistPathSelector,
) -> PlistDict:
    merged_data: PlistDict = {}

    for key in original_base_data:
        overlay_has_key = key in overlay_data
        preserved_has_key = key in preserved_base_data
        child_selector = path_selector.children.get(key)

        if overlay_has_key and preserved_has_key:
            overlay_value = overlay_data[key]
            preserved_value = preserved_base_data[key]
            base_value = original_base_data[key]
            if (
                should_recurse_overlay(child_selector)
                and isinstance(base_value, dict)
                and isinstance(preserved_value, dict)
                and isinstance(overlay_value, dict)
            ):
                merged_data[key] = overlay_plist_dicts(
                    base_value,
                    preserved_value,
                    overlay_value,
                    child_selector,
                )
                continue
            merged_data[key] = overlay_value
            continue

        if overlay_has_key:
            merged_data[key] = overlay_data[key]
            continue
        if preserved_has_key:
            merged_data[key] = preserved_base_data[key]

    for source_data in (overlay_data, preserved_base_data):
        for key, value in source_data.items():
            if key in merged_data:
                continue
            merged_data[key] = value

    return merged_data


def overlay_plist_data(
    original_base_data: PlistDict,
    preserved_base_data: PlistDict,
    overlay_data: PlistDict,
    selected_key_paths: tuple[PlistKeyPath, ...] = (),
) -> PlistDict:
    return overlay_plist_dicts(
        original_base_data,
        preserved_base_data,
        overlay_data,
        build_plist_path_selector(selected_key_paths),
    )


def plist_format_from_name(format_name: str) -> int:
    return plistlib.FMT_XML if format_name == "xml" else plistlib.FMT_BINARY


def write_plist(path: Path, data: PlistDict, fmt: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        plistlib.dump(data, handle, fmt=plist_format_from_name(fmt), sort_keys=True)


def plist_bytes(data: PlistDict, fmt: str) -> bytes:
    return plistlib.dumps(data, fmt=plist_format_from_name(fmt), sort_keys=True)


def plist_values_semantically_equal(left: Any, right: Any) -> bool:
    # Python considers bool a numeric subtype (`True == 1`), but plist stores
    # booleans and integers as distinct value types.
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            plist_values_semantically_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            plist_values_semantically_equal(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return left == right


def get_existing_bytes_if_semantically_unchanged(
    path: Path,
    data: PlistDict,
) -> bytes | None:
    if not path.exists():
        return None

    existing_bytes = path.read_bytes()
    try:
        existing_data = plistlib.loads(existing_bytes)
    except Exception:
        return None

    if not plist_values_semantically_equal(existing_data, data):
        return None

    return existing_bytes


def build_plist_output(
    data: PlistDict,
    output_format: str,
    *,
    mode_reference_path: Path,
    compare_path: Path | None,
) -> TransformOutput:
    if compare_path is not None:
        existing_bytes = get_existing_bytes_if_semantically_unchanged(compare_path, data)
        if existing_bytes is not None:
            return TransformOutput(
                content=existing_bytes,
                mode_reference_path=mode_reference_path,
                reused_compare_path=compare_path,
            )
    return TransformOutput(
        content=plist_bytes(data, output_format),
        mode_reference_path=mode_reference_path,
    )


def write_plist_if_changed(
    output_path: Path | None,
    data: PlistDict,
    output_format: str,
    mode_reference_path: Path,
    compare_path: Path | None,
    stdout: bool = False,
) -> None:
    emit_transform_output(
        output_path,
        build_plist_output(
            data,
            output_format,
            mode_reference_path=mode_reference_path,
            compare_path=compare_path,
        ),
        stdout=stdout,
    )


class PlistTransformEngine(BaseTransformEngine):
    name = "plist"
    SELECTOR_SPECS = (
        SelectorSpec(
            name="key",
            prefix="exact",
            is_default=True,
            description="exact plist dictionary key path",
            examples=("NSUserKeyEquivalents", "settings.window.width"),
        ),
        SelectorSpec(
            name="key_regex",
            prefix="re",
            description="regex matching dotted plist dictionary key paths",
            examples=(r"^NS", r"^settings\.window\."),
        ),
    )

    def requires_selectors(self) -> bool:
        return False

    def configure_parser(self, parser) -> None:
        parser.add_argument(
            "--compare-file",
            type=Path,
            help="Optional plist to compare against for semantic no-op byte reuse.",
        )
        parser.add_argument(
            "--output-format",
            choices=("xml", "binary"),
            default="xml",
            help="Serialization format for the output plist.",
        )

    def build_engine_options(self, parsed_args) -> dict[str, Any]:
        return {
            "compare_path": parsed_args.compare_file,
            "output_format": parsed_args.output_format,
            "stdout": parsed_args.stdout,
            "stdin_bytes": parsed_args.stdin_bytes,
        }

    def validate_request(self, request: TransformRequest) -> None:
        super().validate_request(request)
        parse_plist_key_paths(request.selector_values("key"))
        compile_key_regexes(request.selector_values("key_regex"))

    def transform(self, request: TransformRequest) -> TransformOutput:
        self.validate_request(request)
        exact_key_paths = parse_plist_key_paths(request.selector_values("key"))
        selected_key_regexes = compile_key_regexes(request.selector_values("key_regex"))
        output_format = str(request.engine_option("output_format", "xml"))

        base_data = load_plist(
            request.base_path, stdin_bytes=request.engine_option("stdin_bytes")
        )
        selected_key_paths = selected_plist_key_paths(
            base_data,
            exact_key_paths,
            selected_key_regexes,
        )
        transformed_data = select_plist_data(
            base_data,
            request.selector_action,
            selected_key_paths,
        )

        if request.mode == TransformMode.MERGE:
            assert request.overlay_path is not None
            overlay_data = load_plist(
                request.overlay_path, stdin_bytes=request.engine_option("stdin_bytes")
            )
            transformed_data = overlay_plist_data(
                base_data,
                transformed_data,
                overlay_data,
                selected_key_paths,
            )

        return build_plist_output(
            transformed_data,
            output_format,
            mode_reference_path=(None if request.base_path == Path("-") else request.base_path),
            compare_path=request.engine_option("compare_path"),
        )


def main(argv: list[str] | None = None) -> int:
    return run_engine_cli(PlistTransformEngine(), argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
