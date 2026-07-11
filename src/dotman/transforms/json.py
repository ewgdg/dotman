#!/usr/bin/env python3

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
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


JsonDict = dict[str, Any]
JsonKeyPath = tuple[str, ...]
KeyRegex = re.Pattern[str]
DEFAULT_JSON_INDENT = "  "
_JSON_INDENT_RE = re.compile(r"^([ \t]+)\S")
_MISSING = object()


@dataclass
class JsonPathSelector:
    include_subtree: bool = False
    children: dict[str, "JsonPathSelector"] = field(default_factory=dict)


def load_json(path: Path, *, stdin_text: str | None = None) -> JsonDict:
    if path == Path("-"):
        assert stdin_text is not None
        source_text = stdin_text
    elif not path.exists():
        return {}
    else:
        source_text = path.read_text(encoding="utf-8")

    loaded = json.loads(source_text)
    if not isinstance(loaded, dict):
        raise ValueError(f"Expected top-level JSON object in {path}")
    return loaded



def compile_key_regexes(raw_key_regexes: tuple[str, ...]) -> tuple[KeyRegex, ...]:
    return compile_selector_regexes(raw_key_regexes, "JSON key selector")



def parse_json_key_path(raw_key: str) -> JsonKeyPath:
    key_path = tuple(split_json_key(raw_key))
    if not key_path:
        raise ValueError("JSON key paths must not be empty")
    return key_path



def split_json_key(raw_key: str) -> list[str]:
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
            append_json_key_part(parts, current)
            current = []
            continue

        current.append(char)

    if escape:
        current.append("\\")
    if in_quotes:
        raise ValueError(f"unterminated quoted JSON key path: {raw_key}")

    append_json_key_part(parts, current)
    return parts



def append_json_key_part(parts: list[str], current: list[str]) -> None:
    key_part = "".join(current)
    if key_part:
        parts.append(key_part)



def parse_json_key_paths(raw_key_paths: tuple[str, ...]) -> tuple[JsonKeyPath, ...]:
    return tuple(parse_json_key_path(raw_key) for raw_key in raw_key_paths)



def build_json_path_selector(key_paths: tuple[JsonKeyPath, ...]) -> JsonPathSelector:
    root = JsonPathSelector()
    for key_path in key_paths:
        current = root
        for key_part in key_path:
            if current.include_subtree:
                break
            current = current.children.setdefault(key_part, JsonPathSelector())
        current.include_subtree = True
        current.children.clear()
    return root



def json_key_path_text(key_path: JsonKeyPath) -> str:
    return ".".join(key_path)



def matches_key_regexes(key_path: JsonKeyPath, key_regexes: tuple[KeyRegex, ...]) -> bool:
    path_text = json_key_path_text(key_path)
    return any(key_regex.search(path_text) for key_regex in key_regexes)



def iter_json_key_paths(value: Any, prefix: JsonKeyPath = ()) -> tuple[JsonKeyPath, ...]:
    if not isinstance(value, dict):
        return ()

    key_paths: list[JsonKeyPath] = []
    for key, child_value in value.items():
        key_path = prefix + (key,)
        key_paths.append(key_path)
        key_paths.extend(iter_json_key_paths(child_value, key_path))
    return tuple(key_paths)



def json_key_paths_matching_regexes(
    data: JsonDict,
    key_regexes: tuple[KeyRegex, ...],
) -> tuple[JsonKeyPath, ...]:
    if not key_regexes:
        return ()
    return tuple(
        key_path
        for key_path in iter_json_key_paths(data)
        if matches_key_regexes(key_path, key_regexes)
    )



def selected_json_key_paths(
    data: JsonDict,
    exact_key_paths: tuple[JsonKeyPath, ...],
    key_regexes: tuple[KeyRegex, ...],
) -> tuple[JsonKeyPath, ...]:
    return exact_key_paths + json_key_paths_matching_regexes(data, key_regexes)



def retained_json_value(value: Any, selector: JsonPathSelector) -> Any:
    if selector.include_subtree:
        return value
    if not isinstance(value, dict):
        return _MISSING

    retained_data: JsonDict = {}
    for key, child_value in value.items():
        child_selector = selector.children.get(key)
        if child_selector is None:
            continue
        retained_value = retained_json_value(child_value, child_selector)
        if retained_value is not _MISSING:
            retained_data[key] = retained_value

    if not retained_data:
        return _MISSING
    return retained_data



def stripped_json_value(value: Any, selector: JsonPathSelector) -> Any:
    if selector.include_subtree:
        return _MISSING
    if not isinstance(value, dict):
        return value

    stripped_data: JsonDict = {}
    for key, child_value in value.items():
        child_selector = selector.children.get(key)
        if child_selector is None:
            stripped_data[key] = child_value
            continue

        stripped_value = stripped_json_value(child_value, child_selector)
        if stripped_value is not _MISSING:
            stripped_data[key] = stripped_value

    return stripped_data



def filter_retained_keys(
    data: JsonDict,
    retained_key_paths: tuple[JsonKeyPath, ...],
    retained_key_regexes: tuple[KeyRegex, ...] = (),
) -> JsonDict:
    if not retained_key_paths and not retained_key_regexes:
        return dict(data)

    path_selector = build_json_path_selector(retained_key_paths)
    retained_data: JsonDict = {}
    for key, value in data.items():
        if matches_key_regexes((key,), retained_key_regexes):
            retained_data[key] = value
            continue

        child_selector = path_selector.children.get(key)
        if child_selector is None:
            continue

        retained_value = retained_json_value(value, child_selector)
        if retained_value is not _MISSING:
            retained_data[key] = retained_value

    return retained_data



def filter_stripped_keys(
    data: JsonDict,
    stripped_key_paths: tuple[JsonKeyPath, ...],
    stripped_key_regexes: tuple[KeyRegex, ...] = (),
) -> JsonDict:
    if not stripped_key_paths and not stripped_key_regexes:
        return dict(data)

    path_selector = build_json_path_selector(stripped_key_paths)
    stripped_data: JsonDict = {}
    for key, value in data.items():
        if matches_key_regexes((key,), stripped_key_regexes):
            continue

        child_selector = path_selector.children.get(key)
        if child_selector is None:
            stripped_data[key] = value
            continue

        stripped_value = stripped_json_value(value, child_selector)
        if stripped_value is not _MISSING:
            stripped_data[key] = stripped_value

    return stripped_data



def select_json_data(
    data: JsonDict,
    selector_action: SelectorAction,
    selected_key_paths: tuple[JsonKeyPath, ...],
    selected_key_regexes: tuple[KeyRegex, ...] = (),
) -> JsonDict:
    if selector_action == SelectorAction.REMOVE:
        return filter_stripped_keys(data, selected_key_paths, selected_key_regexes)
    return filter_retained_keys(data, selected_key_paths, selected_key_regexes)



def should_recurse_overlay(selector: JsonPathSelector | None) -> bool:
    return selector is not None and not selector.include_subtree and bool(selector.children)



def overlay_json_objects(
    original_base_data: JsonDict,
    preserved_base_data: JsonDict,
    overlay_data: JsonDict,
    path_selector: JsonPathSelector,
    whole_key_regexes: tuple[KeyRegex, ...] = (),
) -> JsonDict:
    merged_data: JsonDict = {}

    # Keep surviving keys in live order so repo-managed value changes do not also
    # produce noisy key-movement diffs.
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
                and not matches_key_regexes((key,), whole_key_regexes)
                and isinstance(base_value, dict)
                and isinstance(preserved_value, dict)
                and isinstance(overlay_value, dict)
            ):
                merged_data[key] = overlay_json_objects(
                    base_value,
                    preserved_value,
                    overlay_value,
                    child_selector,
                    (),
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



def overlay_json_data(
    original_base_data: JsonDict,
    preserved_base_data: JsonDict,
    overlay_data: JsonDict,
    selected_key_paths: tuple[JsonKeyPath, ...] = (),
    selected_key_regexes: tuple[KeyRegex, ...] = (),
) -> JsonDict:
    return overlay_json_objects(
        original_base_data,
        preserved_base_data,
        overlay_data,
        build_json_path_selector(selected_key_paths),
        selected_key_regexes,
    )



def detect_json_indent(text: str) -> str | None:
    for line in text.splitlines():
        match = _JSON_INDENT_RE.match(line)
        if match:
            return match.group(1)
    return None



def detect_json_indent_from_path(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None

    text = path.read_text(encoding="utf-8")
    try:
        json.loads(text)
    except Exception:
        return None

    return detect_json_indent(text)



def select_json_indent(*reference_paths: Path | None) -> str:
    for reference_path in reference_paths:
        indent = detect_json_indent_from_path(reference_path)
        if indent is not None:
            return indent
    return DEFAULT_JSON_INDENT



def json_text(data: JsonDict, indent: str = DEFAULT_JSON_INDENT) -> str:
    return json.dumps(data, indent=indent, ensure_ascii=False) + "\n"



def get_existing_bytes_if_semantically_unchanged(path: Path, data: JsonDict) -> bytes | None:
    if not path.exists():
        return None

    existing_bytes = path.read_bytes()
    try:
        existing_data = json.loads(existing_bytes.decode("utf-8"))
    except Exception:
        return None

    if existing_data != data:
        return None

    return existing_bytes



def build_json_output(
    data: JsonDict,
    *,
    mode_reference_path: Path | None,
    compare_path: Path | None = None,
    indent_reference_paths: tuple[Path | None, ...] = (),
) -> TransformOutput:
    if compare_path is not None:
        existing_bytes = get_existing_bytes_if_semantically_unchanged(compare_path, data)
        if existing_bytes is not None:
            return TransformOutput(
                content=existing_bytes,
                mode_reference_path=mode_reference_path,
                reused_compare_path=compare_path,
            )

    indent = select_json_indent(compare_path, *indent_reference_paths, mode_reference_path)
    return TransformOutput(
        content=json_text(data, indent=indent),
        mode_reference_path=mode_reference_path,
    )



def write_json_if_changed(
    output_path: Path | None,
    data: JsonDict,
    mode_reference_path: Path | None,
    compare_path: Path | None,
    stdout: bool = False,
) -> None:
    emit_transform_output(
        output_path,
        build_json_output(
            data,
            mode_reference_path=mode_reference_path,
            compare_path=compare_path,
            indent_reference_paths=(mode_reference_path,),
        ),
        stdout=stdout,
    )


class JsonTransformEngine(BaseTransformEngine):
    name = "json"
    SELECTOR_SPECS = (
        SelectorSpec(
            name="key",
            prefix="exact",
            is_default=True,
            description="exact dotted or quoted nested JSON object key path",
            examples=("buildDir", "settings.window.width", '"key.with.dots".value'),
        ),
        SelectorSpec(
            name="key_regex",
            prefix="re",
            description="regex matching full JSON object key paths",
            examples=(r"^build", r"Dir$"),
        ),
    )

    def requires_selectors(self) -> bool:
        return False

    def configure_parser(self, parser) -> None:
        parser.add_argument(
            "--compare-file",
            type=Path,
            help="Optional JSON file to compare against for semantic no-op text reuse.",
        )

    def build_engine_options(self, parsed_args) -> dict[str, Any]:
        return {
            "compare_path": parsed_args.compare_file,
            "stdout": parsed_args.stdout,
            "stdin_text": parsed_args.stdin_text,
        }

    def validate_request(self, request: TransformRequest) -> None:
        super().validate_request(request)
        parse_json_key_paths(request.selector_values("key"))
        compile_key_regexes(request.selector_values("key_regex"))

    def transform(self, request: TransformRequest) -> TransformOutput:
        self.validate_request(request)
        exact_key_paths = parse_json_key_paths(request.selector_values("key"))
        selected_key_regexes = compile_key_regexes(request.selector_values("key_regex"))

        base_data = load_json(
            request.base_path,
            stdin_text=request.engine_option("stdin_text"),
        )
        selected_key_paths = selected_json_key_paths(
            base_data,
            exact_key_paths,
            selected_key_regexes,
        )
        transformed_data = select_json_data(
            base_data,
            request.selector_action,
            selected_key_paths,
        )

        if request.mode == TransformMode.MERGE:
            assert request.overlay_path is not None
            overlay_data = load_json(
                request.overlay_path,
                stdin_text=request.engine_option("stdin_text"),
            )
            transformed_data = overlay_json_data(
                base_data,
                transformed_data,
                overlay_data,
                selected_key_paths,
            )

        return build_json_output(
            transformed_data,
            mode_reference_path=(None if request.base_path == Path("-") else request.base_path),
            compare_path=request.engine_option("compare_path"),
            indent_reference_paths=(request.base_path, request.overlay_path),
        )



def main(argv: list[str] | None = None) -> int:
    return run_engine_cli(JsonTransformEngine(), argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
