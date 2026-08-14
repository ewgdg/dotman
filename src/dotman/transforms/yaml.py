#!/usr/bin/env python3

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any

import yaml

from dotman.transforms.cli import run_engine_cli
from dotman.transforms.framework import (
    BaseTransformEngine,
    SelectorAction,
    SelectorSpec,
    TransformMode,
    TransformOutput,
    TransformRequest,
    compile_selector_regexes,
)


YamlDict = dict[Any, Any]
YamlKeyPath = tuple[str, ...]
KeyRegex = re.Pattern[str]
DEFAULT_YAML_INDENT = 2
_YAML_INDENT_RE = re.compile(r"^( +)\S")
_MISSING = object()
_BOOL_TAG = "tag:yaml.org,2002:bool"
_STRICT_BOOL_RE = re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$")


def resolvers_without_legacy_booleans(
    resolvers: dict[Any, list[tuple[str, re.Pattern[str]]]],
) -> dict[Any, list[tuple[str, re.Pattern[str]]]]:
    return {
        initial: [resolver for resolver in initial_resolvers if resolver[0] != _BOOL_TAG]
        for initial, initial_resolvers in resolvers.items()
    }


class StrictBooleanSafeLoader(yaml.SafeLoader):
    yaml_implicit_resolvers = resolvers_without_legacy_booleans(
        yaml.SafeLoader.yaml_implicit_resolvers
    )


# PyYAML defaults to YAML 1.1's yes/no/on/off booleans. Configuration keys such
# as a reasoning effort named "off" must remain strings; only explicit boolean
# words should resolve to bool values.
StrictBooleanSafeLoader.add_implicit_resolver(
    _BOOL_TAG, _STRICT_BOOL_RE, list("tTfF")
)


def parse_yaml_text(text: str) -> Any:
    return yaml.load(text, Loader=StrictBooleanSafeLoader)


@dataclass
class YamlPathSelector:
    include_subtree: bool = False
    children: dict[str, "YamlPathSelector"] = field(default_factory=dict)


def load_yaml(path: Path, *, stdin_text: str | None = None) -> YamlDict:
    if path == Path("-"):
        assert stdin_text is not None
        source_text = stdin_text
    elif not path.exists():
        return {}
    else:
        source_text = path.read_text(encoding="utf-8")

    loaded = parse_yaml_text(source_text)
    if not isinstance(loaded, dict):
        raise ValueError(f"Expected top-level YAML mapping in {path}")
    return loaded


def compile_key_regexes(raw_key_regexes: tuple[str, ...]) -> tuple[KeyRegex, ...]:
    return compile_selector_regexes(raw_key_regexes, "YAML key selector")


def parse_yaml_key_path(raw_key: str) -> YamlKeyPath:
    key_path = tuple(split_yaml_key(raw_key))
    if not key_path:
        raise ValueError("YAML key paths must not be empty")
    return key_path


def split_yaml_key(raw_key: str) -> list[str]:
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
            append_yaml_key_part(parts, current)
            current = []
            continue

        current.append(char)

    if escape:
        current.append("\\")
    if in_quotes:
        raise ValueError(f"unterminated quoted YAML key path: {raw_key}")

    append_yaml_key_part(parts, current)
    return parts


def append_yaml_key_part(parts: list[str], current: list[str]) -> None:
    key_part = "".join(current)
    if key_part:
        parts.append(key_part)


def parse_yaml_key_paths(raw_key_paths: tuple[str, ...]) -> tuple[YamlKeyPath, ...]:
    return tuple(parse_yaml_key_path(raw_key) for raw_key in raw_key_paths)


def build_yaml_path_selector(key_paths: tuple[YamlKeyPath, ...]) -> YamlPathSelector:
    root = YamlPathSelector()
    for key_path in key_paths:
        current = root
        for key_part in key_path:
            if current.include_subtree:
                break
            current = current.children.setdefault(key_part, YamlPathSelector())
        current.include_subtree = True
        current.children.clear()
    return root


def yaml_key_path_text(key_path: YamlKeyPath) -> str:
    return ".".join(key_path)


def matches_key_regexes(key_path: YamlKeyPath, key_regexes: tuple[KeyRegex, ...]) -> bool:
    path_text = yaml_key_path_text(key_path)
    return any(key_regex.search(path_text) for key_regex in key_regexes)


def iter_yaml_key_paths(value: Any, prefix: YamlKeyPath = ()) -> tuple[YamlKeyPath, ...]:
    if not isinstance(value, dict):
        return ()

    key_paths: list[YamlKeyPath] = []
    for key, child_value in value.items():
        key_path = prefix + (str(key),)
        key_paths.append(key_path)
        key_paths.extend(iter_yaml_key_paths(child_value, key_path))
    return tuple(key_paths)


def yaml_key_paths_matching_regexes(
    data: YamlDict,
    key_regexes: tuple[KeyRegex, ...],
) -> tuple[YamlKeyPath, ...]:
    if not key_regexes:
        return ()
    return tuple(
        key_path
        for key_path in iter_yaml_key_paths(data)
        if matches_key_regexes(key_path, key_regexes)
    )


def selected_yaml_key_paths(
    data: YamlDict,
    exact_key_paths: tuple[YamlKeyPath, ...],
    key_regexes: tuple[KeyRegex, ...],
) -> tuple[YamlKeyPath, ...]:
    return exact_key_paths + yaml_key_paths_matching_regexes(data, key_regexes)


def retained_yaml_value(value: Any, selector: YamlPathSelector) -> Any:
    if selector.include_subtree:
        return value
    if not isinstance(value, dict):
        return _MISSING

    retained_data: YamlDict = {}
    for key, child_value in value.items():
        child_selector = selector.children.get(str(key))
        if child_selector is None:
            continue
        retained_value = retained_yaml_value(child_value, child_selector)
        if retained_value is not _MISSING:
            retained_data[key] = retained_value

    if not retained_data:
        return _MISSING
    return retained_data


def stripped_yaml_value(value: Any, selector: YamlPathSelector) -> Any:
    if selector.include_subtree:
        return _MISSING
    if not isinstance(value, dict):
        return value

    stripped_data: YamlDict = {}
    for key, child_value in value.items():
        child_selector = selector.children.get(str(key))
        if child_selector is None:
            stripped_data[key] = child_value
            continue

        stripped_value = stripped_yaml_value(child_value, child_selector)
        if stripped_value is not _MISSING:
            stripped_data[key] = stripped_value

    return stripped_data


def filter_retained_keys(
    data: YamlDict,
    retained_key_paths: tuple[YamlKeyPath, ...],
    retained_key_regexes: tuple[KeyRegex, ...] = (),
) -> YamlDict:
    if not retained_key_paths and not retained_key_regexes:
        return dict(data)

    path_selector = build_yaml_path_selector(retained_key_paths)
    retained_data: YamlDict = {}
    for key, value in data.items():
        if matches_key_regexes((str(key),), retained_key_regexes):
            retained_data[key] = value
            continue

        child_selector = path_selector.children.get(str(key))
        if child_selector is None:
            continue

        retained_value = retained_yaml_value(value, child_selector)
        if retained_value is not _MISSING:
            retained_data[key] = retained_value

    return retained_data


def filter_stripped_keys(
    data: YamlDict,
    stripped_key_paths: tuple[YamlKeyPath, ...],
    stripped_key_regexes: tuple[KeyRegex, ...] = (),
) -> YamlDict:
    if not stripped_key_paths and not stripped_key_regexes:
        return dict(data)

    path_selector = build_yaml_path_selector(stripped_key_paths)
    stripped_data: YamlDict = {}
    for key, value in data.items():
        if matches_key_regexes((str(key),), stripped_key_regexes):
            continue

        child_selector = path_selector.children.get(str(key))
        if child_selector is None:
            stripped_data[key] = value
            continue

        stripped_value = stripped_yaml_value(value, child_selector)
        if stripped_value is not _MISSING:
            stripped_data[key] = stripped_value

    return stripped_data


def select_yaml_data(
    data: YamlDict,
    selector_action: SelectorAction,
    selected_key_paths: tuple[YamlKeyPath, ...],
    selected_key_regexes: tuple[KeyRegex, ...] = (),
) -> YamlDict:
    if selector_action == SelectorAction.REMOVE:
        return filter_stripped_keys(data, selected_key_paths, selected_key_regexes)
    return filter_retained_keys(data, selected_key_paths, selected_key_regexes)


def should_recurse_overlay(selector: YamlPathSelector | None) -> bool:
    return selector is not None and not selector.include_subtree and bool(selector.children)


def overlay_yaml_objects(
    original_base_data: YamlDict,
    preserved_base_data: YamlDict,
    overlay_data: YamlDict,
    path_selector: YamlPathSelector,
    whole_key_regexes: tuple[KeyRegex, ...] = (),
) -> YamlDict:
    merged_data: YamlDict = {}

    # Keep surviving keys in live order so repo-managed value changes do not also
    # produce noisy key-movement diffs.
    for key in original_base_data:
        overlay_has_key = key in overlay_data
        preserved_has_key = key in preserved_base_data
        child_selector = path_selector.children.get(str(key))

        if overlay_has_key and preserved_has_key:
            overlay_value = overlay_data[key]
            preserved_value = preserved_base_data[key]
            base_value = original_base_data[key]
            if (
                should_recurse_overlay(child_selector)
                and not matches_key_regexes((str(key),), whole_key_regexes)
                and isinstance(base_value, dict)
                and isinstance(preserved_value, dict)
                and isinstance(overlay_value, dict)
            ):
                merged_data[key] = overlay_yaml_objects(
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


def overlay_yaml_data(
    original_base_data: YamlDict,
    preserved_base_data: YamlDict,
    overlay_data: YamlDict,
    selected_key_paths: tuple[YamlKeyPath, ...] = (),
    selected_key_regexes: tuple[KeyRegex, ...] = (),
) -> YamlDict:
    return overlay_yaml_objects(
        original_base_data,
        preserved_base_data,
        overlay_data,
        build_yaml_path_selector(selected_key_paths),
        selected_key_regexes,
    )


def yaml_values_semantically_equal(left: Any, right: Any) -> bool:
    # YAML distinguishes booleans from integers and integers from floats, so a
    # strict type check prevents 'true' from matching 1 during compare reuse.
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            yaml_values_semantically_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            yaml_values_semantically_equal(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return left == right


def detect_yaml_indent(text: str) -> int | None:
    for line in text.splitlines():
        match = _YAML_INDENT_RE.match(line)
        if match:
            return len(match.group(1))
    return None


def detect_yaml_indent_from_path(path: Path | None) -> int | None:
    if path is None or not path.exists():
        return None

    text = path.read_text(encoding="utf-8")
    try:
        parse_yaml_text(text)
    except Exception:
        return None

    indent = detect_yaml_indent(text)
    if indent is None or indent < 1:
        return None
    return indent


def select_yaml_indent(*reference_paths: Path | None) -> int:
    for reference_path in reference_paths:
        indent = detect_yaml_indent_from_path(reference_path)
        if indent is not None:
            return indent
    return DEFAULT_YAML_INDENT


def yaml_text(data: YamlDict, indent: int = DEFAULT_YAML_INDENT) -> str:
    # Keep SafeDumper's YAML 1.1 resolver so ambiguous strings are quoted for
    # compatibility with both YAML 1.1 and 1.2 consumers.
    return yaml.safe_dump(
        data,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        indent=indent,
    )


def get_existing_bytes_if_semantically_unchanged(
    path: Path,
    data: YamlDict,
) -> bytes | None:
    if not path.exists():
        return None

    existing_bytes = path.read_bytes()
    try:
        existing_data = parse_yaml_text(existing_bytes.decode("utf-8"))
    except Exception:
        return None

    if not yaml_values_semantically_equal(existing_data, data):
        return None

    return existing_bytes


def build_yaml_output(
    data: YamlDict,
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

    indent = select_yaml_indent(compare_path, *indent_reference_paths, mode_reference_path)
    return TransformOutput(
        content=yaml_text(data, indent=indent),
        mode_reference_path=mode_reference_path,
    )


class YamlTransformEngine(BaseTransformEngine):
    name = "yaml"
    SELECTOR_SPECS = (
        SelectorSpec(
            name="key",
            prefix="exact",
            is_default=True,
            description="exact dotted or quoted nested YAML mapping key path",
            examples=("buildDir", "settings.window.width", '"key.with.dots".value'),
        ),
        SelectorSpec(
            name="key_regex",
            prefix="re",
            description="regex matching full YAML mapping key paths",
            examples=(r"^build", r"Dir$"),
        ),
    )

    def requires_selectors(self) -> bool:
        return False

    def configure_parser(self, parser) -> None:
        parser.add_argument(
            "--compare-file",
            type=Path,
            help="Optional YAML file to compare against for semantic no-op text reuse.",
        )

    def build_engine_options(self, parsed_args) -> dict[str, Any]:
        return {
            "compare_path": parsed_args.compare_file,
            "stdout": parsed_args.stdout,
            "stdin_text": parsed_args.stdin_text,
        }

    def validate_request(self, request: TransformRequest) -> None:
        super().validate_request(request)
        parse_yaml_key_paths(request.selector_values("key"))
        compile_key_regexes(request.selector_values("key_regex"))

    def transform(self, request: TransformRequest) -> TransformOutput:
        self.validate_request(request)
        exact_key_paths = parse_yaml_key_paths(request.selector_values("key"))
        selected_key_regexes = compile_key_regexes(request.selector_values("key_regex"))

        base_data = load_yaml(
            request.base_path,
            stdin_text=request.engine_option("stdin_text"),
        )
        selected_key_paths = selected_yaml_key_paths(
            base_data,
            exact_key_paths,
            selected_key_regexes,
        )
        transformed_data = select_yaml_data(
            base_data,
            request.selector_action,
            selected_key_paths,
        )

        if request.mode == TransformMode.MERGE:
            assert request.overlay_path is not None
            overlay_data = load_yaml(
                request.overlay_path,
                stdin_text=request.engine_option("stdin_text"),
            )
            transformed_data = overlay_yaml_data(
                base_data,
                transformed_data,
                overlay_data,
                selected_key_paths,
            )

        return build_yaml_output(
            transformed_data,
            mode_reference_path=(None if request.base_path == Path("-") else request.base_path),
            compare_path=request.engine_option("compare_path"),
            indent_reference_paths=(request.base_path, request.overlay_path),
        )


def main(argv: list[str] | None = None) -> int:
    return run_engine_cli(YamlTransformEngine(), argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
