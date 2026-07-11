#!/usr/bin/env python3

from __future__ import annotations

import copy
from dataclasses import dataclass
import re
from collections.abc import Iterable
from typing import Any
from pathlib import Path
import tomlkit
from tomlkit.items import AoT, Null, Table
from tomlkit.toml_document import TOMLDocument

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


TomlContainer = TOMLDocument | Table


@dataclass(frozen=True)
class TopLevelBodyRegion:
    key_name: str
    key: object
    item: object
    leading_entries: tuple[tuple[None, object], ...]


def load_document(path: Path, *, stdin_text: str | None = None) -> TOMLDocument:
    if path == Path("-"):
        assert stdin_text is not None
        source_text = stdin_text
    elif not path.exists():
        return tomlkit.document()
    else:
        source_text = path.read_text(encoding="utf-8")
    return detach_table_tail_trivia(tomlkit.parse(source_text))


def get_existing_text_if_unchanged(compare_path: Path, doc: TOMLDocument) -> bytes | None:
    if not compare_path.exists():
        return None

    existing_bytes = compare_path.read_bytes()
    existing_content = existing_bytes.decode("utf-8")
    try:
        existing_doc = tomlkit.parse(existing_content)
    except Exception:
        existing_doc = None

    if existing_doc is not None and existing_doc.unwrap() == doc.unwrap():
        return existing_bytes

    if existing_content != doc.as_string():
        return None

    return existing_bytes


def build_document_output(
    doc: TOMLDocument,
    *,
    mode_reference_path: Path,
    compare_path: Path | None = None,
) -> TransformOutput:
    content = doc.as_string()
    if compare_path is not None:
        existing_content = get_existing_text_if_unchanged(compare_path, doc)
        if existing_content is not None:
            return TransformOutput(
                content=existing_content,
                mode_reference_path=mode_reference_path,
                reused_compare_path=compare_path,
            )

    return TransformOutput(
        content=content,
        mode_reference_path=mode_reference_path,
    )



def write_document_if_changed(
    path: Path | None,
    doc: TOMLDocument,
    mode_reference_path: Path,
    compare_path: Path | None = None,
    stdout: bool = False,
) -> None:
    emit_transform_output(
        path,
        build_document_output(
            doc,
            mode_reference_path=mode_reference_path,
            compare_path=compare_path,
        ),
        stdout=stdout,
    )


def parse_key_path(raw_key: str) -> tuple[str, ...]:
    key_path = tuple(split_toml_key(raw_key))
    if not key_path:
        raise ValueError("key paths must not be empty")
    return key_path


def split_toml_key(raw_key: str) -> list[str]:
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
            append_key_part(parts, current)
            current = []
            continue

        current.append(char)

    if in_quotes:
        raise ValueError(f"unterminated quoted TOML key: {raw_key}")

    append_key_part(parts, current)
    return parts


def append_key_part(parts: list[str], current: list[str]) -> None:
    raw_part = "".join(current).strip()
    if not raw_part:
        return
    if raw_part.startswith('"') and raw_part.endswith('"'):
        parts.append(bytes(raw_part[1:-1], "utf-8").decode("unicode_escape"))
        return
    parts.append(raw_part)


def split_key_path(key_path: tuple[str, ...]) -> tuple[tuple[str, ...], str]:
    return key_path[:-1], key_path[-1]


def get_container(root: TomlContainer, table_path: tuple[str, ...]) -> TomlContainer | None:
    current: Any = root
    for part in table_path:
        if part not in current:
            return None
        current = current[part]
        if not isinstance(current, Table):
            return None
    return current


def path_exists(root: TomlContainer, key_path: tuple[str, ...]) -> bool:
    table_path, key_name = split_key_path(key_path)
    container = get_container(root, table_path)
    return container is not None and key_name in container


def get_key_path_value(root: TomlContainer, key_path: tuple[str, ...]) -> Any | None:
    table_path, key_name = split_key_path(key_path)
    container = get_container(root, table_path)
    if container is None or key_name not in container:
        return None
    return container[key_name]


def container_storage(container: TomlContainer) -> Any:
    if isinstance(container, TOMLDocument):
        return container
    return container.value


def container_body_entries(container: TomlContainer) -> list[tuple[object, object]]:
    return container_storage(container)._body


def increment_container_map_indexes(container: TomlContainer, start_index: int, offset: int) -> None:
    storage = container_storage(container)
    for key, value in storage._map.items():
        if isinstance(value, tuple):
            storage._map[key] = tuple(
                index + offset if index >= start_index else index for index in value
            )
            continue

        if value >= start_index:
            storage._map[key] = value + offset


def item_text(item: object) -> str:
    if hasattr(item, "as_string"):
        return item.as_string()
    return str(item)


def is_comment_entry(entry: tuple[object, object]) -> bool:
    key, item = entry
    return key is None and item_text(item).lstrip().startswith("#")


def is_blank_entry(entry: tuple[object, object]) -> bool:
    key, item = entry
    return key is None and "\n" in item_text(item) and not is_comment_entry(entry)


def split_blank_separated_tail_trivia(
    entries: list[tuple[object, object]],
    *,
    require_trailing_blank: bool = True,
) -> tuple[list[tuple[object, object]], tuple[tuple[None, object], ...]]:
    split_index = len(entries)

    while split_index > 0 and entries[split_index - 1][0] is None:
        split_index -= 1

    tail_entries = entries[split_index:]
    if not tail_entries:
        return entries, ()
    if require_trailing_blank and not is_blank_entry(tail_entries[-1]):
        return entries, ()

    # An attached comment may precede a blank-separated independent block in
    # the same tomlkit trivia run. Split at that separator, not at run start.
    independent_start = next(
        (
            index
            for index, entry in enumerate(tail_entries)
            if is_blank_entry(entry)
            and any(is_comment_entry(candidate) for candidate in tail_entries[index + 1 :])
        ),
        None,
    )
    if independent_start is None:
        return entries, ()

    split_index += independent_start
    tail_entries = entries[split_index:]
    retained_entries = entries[:split_index]
    detached_entries = tuple((None, copy.deepcopy(item)) for _key, item in tail_entries)
    return retained_entries, detached_entries


def detach_table_tail_trivia(doc: TOMLDocument) -> TOMLDocument:
    # tomlkit keeps blank-separated comments after a table inside that table.
    # Bubble those tail blocks out so selector deletes do not own them.
    detached_entries = detach_child_table_tail_trivia(doc)
    if detached_entries:
        container_body_entries(doc).extend(detached_entries)
    return doc


def detach_child_table_tail_trivia(
    container: TomlContainer,
    *,
    detach_own_tail: bool = True,
) -> tuple[tuple[None, object], ...]:
    body_entries = container_body_entries(container)
    index = 0

    while index < len(body_entries):
        key, item = body_entries[index]
        detached_entries: tuple[tuple[None, object], ...] = ()
        if key is not None and isinstance(item, Table):
            detached_entries = detach_child_table_tail_trivia(item)
        elif key is not None and isinstance(item, AoT):
            # Trivia after the final element belongs after the whole atomic AoT.
            # Earlier element tails remain inside the AoT as element separators.
            for table_index, table in enumerate(item):
                candidate_entries = detach_child_table_tail_trivia(
                    table,
                    detach_own_tail=table_index == len(item) - 1,
                )
                if candidate_entries:
                    detached_entries = candidate_entries

        if detached_entries:
            insert_trivia_at_body_index(container, index + 1, detached_entries)
            body_entries = container_body_entries(container)
            index += len(detached_entries)
        index += 1

    if not detach_own_tail:
        return ()

    retained_entries, detached_entries = split_blank_separated_tail_trivia(
        body_entries,
        require_trailing_blank=False,
    )
    if detached_entries:
        body_entries[:] = retained_entries
    return detached_entries


def insert_trivia_at_body_index(
    container: TomlContainer,
    index: int,
    trivia_entries: tuple[tuple[None, object], ...],
) -> None:
    if not trivia_entries:
        return

    body_entries = container_body_entries(container)
    increment_container_map_indexes(container, index, len(trivia_entries))
    for offset, entry in enumerate(trivia_entries):
        body_entries.insert(index + offset, entry)


def delete_key_path(root: TomlContainer, key_path: tuple[str, ...]) -> None:
    table_path, key_name = split_key_path(key_path)
    container = get_container(root, table_path)
    if container is not None and key_name in container:
        del container[key_name]


def iter_table_paths(root: TomlContainer, prefix: tuple[str, ...] = ()) -> Iterable[tuple[str, ...]]:
    for key, value in root.items():
        if not isinstance(value, Table):
            continue
        key_path = prefix + (str(key),)
        yield key_path
        yield from iter_table_paths(value, key_path)


def iter_item_paths_in_order(
    root: TomlContainer,
    prefix: tuple[str, ...] = (),
) -> Iterable[tuple[str, ...]]:
    for key, value in root.items():
        key_path = prefix + (str(key),)
        yield key_path
        if isinstance(value, Table):
            yield from iter_item_paths_in_order(value, key_path)


def matches_path_regex(item_path: tuple[str, ...], path_regexes: list[re.Pattern[str]]) -> bool:
    raw_item_path = ".".join(item_path)
    return any(path_regex.search(raw_item_path) for path_regex in path_regexes)


def parse_key_paths(raw_key_paths: Iterable[str]) -> list[tuple[str, ...]]:
    return [parse_key_path(raw_key) for raw_key in raw_key_paths]


def compile_table_regexes(raw_table_regexes: Iterable[str]) -> list[re.Pattern[str]]:
    return list(compile_selector_regexes(raw_table_regexes, "TOML path selector"))


def normalize_blank_lines(content: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", content)


def normalize_document(doc: TOMLDocument) -> TOMLDocument:
    return tomlkit.parse(normalize_blank_lines(doc.as_string()))


def table_has_leading_blank_separator(table: Table) -> bool:
    return table.trivia.indent.startswith("\n")


def remove_last_table_tail_blank_separator(table: Table) -> None:
    body_entries = container_body_entries(table)
    if body_entries and is_blank_entry(body_entries[-1]):
        body_entries.pop()
        return

    for key, item in reversed(body_entries):
        if key is not None and isinstance(item, Table):
            remove_last_table_tail_blank_separator(item)
            return


def collapse_duplicate_table_separators(container: TomlContainer) -> None:
    """Keep one blank separator when both adjacent tables carry separator trivia.

    tomlkit may store a section separator as trailing blank whitespace on the
    previous table, while the next table may also carry a leading blank indent.
    Treat blank lines as separators: when both sides provide one, keep the next
    table's leading separator and remove the previous table's tail separator.
    """
    body_entries = container_body_entries(container)
    keyed_entries = [entry for entry in body_entries if entry[0] is not None]
    adjacent_pairs = zip(keyed_entries, keyed_entries[1:])
    for (_left_key, left_item), (_right_key, right_item) in adjacent_pairs:
        if not isinstance(left_item, Table) or not isinstance(right_item, Table):
            continue
        if table_has_leading_blank_separator(right_item):
            remove_last_table_tail_blank_separator(left_item)

    for key, item in list(body_entries):
        if key is not None and isinstance(item, Table):
            collapse_duplicate_table_separators(item)


def ensure_container(root: TomlContainer, table_path: tuple[str, ...]) -> TomlContainer:
    current: TomlContainer = root
    for part in table_path:
        next_value = current.get(part)
        if not isinstance(next_value, Table):
            current[part] = tomlkit.table()
            next_value = current[part]
        current = next_value
    return current


def collect_top_level_body_regions(
    source_doc: TOMLDocument,
) -> tuple[dict[str, TopLevelBodyRegion], tuple[tuple[None, object], ...]]:
    regions: dict[str, TopLevelBodyRegion] = {}
    pending_leading_entries: list[tuple[None, object]] = []

    for key, item in source_doc._body:
        if key is None:
            if isinstance(item, Null):
                continue
            pending_leading_entries.append((None, copy.deepcopy(item)))
            continue

        if isinstance(item, Null):
            continue

        key_name = key.key
        regions[key_name] = TopLevelBodyRegion(
            key_name=key_name,
            key=copy.deepcopy(key),
            item=copy.deepcopy(item),
            leading_entries=tuple(pending_leading_entries),
        )
        pending_leading_entries = []

    return regions, tuple(pending_leading_entries)


def entries_text(entries: tuple[tuple[None, object], ...]) -> str:
    return "".join(item_text(item) for _key, item in entries)


def trivia_identity_text(entries: tuple[tuple[None, object], ...]) -> str:
    return "\n".join(line for line in entries_text(entries).splitlines() if line.strip())


def split_independent_leading_trivia(
    leading_entries: tuple[tuple[None, object], ...],
) -> tuple[tuple[tuple[None, object], ...], tuple[tuple[None, object], ...]]:
    retained_entries, independent_entries = split_blank_separated_tail_trivia(
        list(leading_entries),
        require_trailing_blank=True,
    )
    return tuple(retained_entries), independent_entries


def collect_independent_leading_trivia_texts(
    regions: dict[str, TopLevelBodyRegion],
) -> set[str]:
    independent_texts: set[str] = set()
    for region in regions.values():
        _attached_entries, independent_entries = split_independent_leading_trivia(
            region.leading_entries
        )
        if independent_entries:
            independent_texts.add(trivia_identity_text(independent_entries))
    return independent_texts


def add_trivia_entries(
    target_doc: TOMLDocument,
    entries: tuple[tuple[None, object], ...],
) -> None:
    for _unused_key, entry in entries:
        target_doc.add(copy.deepcopy(entry))


def restore_top_level_leading_trivia(
    merged_doc: TOMLDocument,
    overlay_doc: TOMLDocument,
    base_doc: TOMLDocument,
    preserved_base: TOMLDocument,
) -> TOMLDocument:
    merged_regions, _merged_trailing_entries = collect_top_level_body_regions(merged_doc)
    overlay_regions, overlay_trailing_entries = collect_top_level_body_regions(overlay_doc)
    base_regions, base_trailing_entries = collect_top_level_body_regions(base_doc)
    preserved_regions, _preserved_trailing_entries = collect_top_level_body_regions(preserved_base)

    rebuilt_doc = tomlkit.document()
    overlay_independent_texts = collect_independent_leading_trivia_texts(overlay_regions)
    emitted_independent_texts: set[str] = set()

    for merged_region in merged_regions.values():
        overlay_region = overlay_regions.get(merged_region.key_name)
        base_region = base_regions.get(merged_region.key_name)
        preserved_region = preserved_regions.get(merged_region.key_name)

        if overlay_region is not None:
            leading_entries = overlay_region.leading_entries
            leading_source = "overlay"
        elif base_region is not None:
            leading_entries = base_region.leading_entries
            leading_source = "base"
        elif preserved_region is not None:
            leading_entries = preserved_region.leading_entries
            leading_source = "preserved"
        else:
            leading_entries = ()
            leading_source = "merged"

        if (
            overlay_region is not None
            and preserved_region is not None
            and isinstance(overlay_region.item, Table)
            and isinstance(preserved_region.item, Table)
        ):
            item_region = merged_region
        elif overlay_region is not None:
            item_region = overlay_region
        elif base_region is not None:
            item_region = base_region
        else:
            item_region = merged_region

        attached_entries, independent_entries = split_independent_leading_trivia(
            leading_entries
        )
        key_to_append = copy.deepcopy(item_region.key)
        item_to_append = copy.deepcopy(item_region.item)
        if attached_entries and isinstance(item_to_append, Table):
            item_to_append.trivia.indent = entries_text(attached_entries) + item_to_append.trivia.indent
        else:
            add_trivia_entries(rebuilt_doc, attached_entries)

        independent_identity = trivia_identity_text(independent_entries)
        independent_seen = independent_identity in emitted_independent_texts
        independent_claimed_by_overlay = (
            leading_source != "overlay" and independent_identity in overlay_independent_texts
        )
        if independent_entries and not independent_seen and not independent_claimed_by_overlay:
            add_trivia_entries(rebuilt_doc, independent_entries)
            emitted_independent_texts.add(independent_identity)

        rebuilt_doc.append(key_to_append, item_to_append)

    trailing_entries: tuple[tuple[None, object], ...] = overlay_trailing_entries or base_trailing_entries
    add_trivia_entries(rebuilt_doc, trailing_entries)

    return rebuilt_doc


def build_document_with_stripped_matchers(
    source_doc: TOMLDocument,
    stripped_key_paths: list[tuple[str, ...]],
    stripped_table_regexes: list[re.Pattern[str]],
) -> TOMLDocument:
    stripped_doc = copy.deepcopy(source_doc)
    item_paths = sorted(iter_item_paths_in_order(stripped_doc), key=len, reverse=True)
    for item_path in item_paths:
        if matches_path_regex(item_path, stripped_table_regexes):
            delete_key_path(stripped_doc, item_path)

    for key_path in stripped_key_paths:
        delete_key_path(stripped_doc, key_path)

    return normalize_document(stripped_doc)


def build_stripped_document_output(
    base_path: Path,
    stripped_key_paths: list[tuple[str, ...]],
    stripped_table_regexes: list[re.Pattern[str]],
    compare_path: Path | None = None,
    stdin_text: str | None = None,
) -> TransformOutput:
    normalized_doc = build_document_with_stripped_matchers(
        load_document(base_path, stdin_text=stdin_text),
        stripped_key_paths,
        stripped_table_regexes,
    )
    return build_document_output(
        normalized_doc,
        mode_reference_path=base_path,
        compare_path=compare_path,
    )



def strip_keys(
    base_path: Path,
    output_path: Path | None,
    stripped_key_paths: list[tuple[str, ...]],
    stripped_table_regexes: list[re.Pattern[str]],
    compare_path: Path | None = None,
    stdout: bool = False,
 ) -> None:
    emit_transform_output(
        output_path,
        build_stripped_document_output(
            base_path,
            stripped_key_paths,
            stripped_table_regexes,
            compare_path=compare_path,
        ),
        stdout=stdout,
    )


def overlay_preserved_keys(
    overlay_doc: TomlContainer,
    base_doc: TomlContainer,
    retained_key_paths: Iterable[tuple[str, ...]],
) -> None:
    retained_key_path_set = set(retained_key_paths)
    for key_path in iter_item_paths_in_order(overlay_doc):
        if key_path not in retained_key_path_set:
            continue
        retained_value = get_key_path_value(overlay_doc, key_path)
        if retained_value is None:
            continue

        table_path, key_name = split_key_path(key_path)
        target_container = ensure_container(base_doc, table_path)
        target_container[key_name] = copy.deepcopy(retained_value)


def overlay_preserved_regex_paths(
    overlay_doc: TomlContainer,
    base_doc: TomlContainer,
    retained_table_regexes: list[re.Pattern[str]],
) -> None:
    if not retained_table_regexes:
        return

    for item_path in iter_item_paths_in_order(overlay_doc):
        if not matches_path_regex(item_path, retained_table_regexes):
            continue

        retained_item = get_key_path_value(overlay_doc, item_path)
        if retained_item is None:
            continue

        parent_path, item_name = split_key_path(item_path)
        target_container = ensure_container(base_doc, parent_path)
        target_container[item_name] = copy.deepcopy(retained_item)


def build_document_with_retained_matchers(
    source_doc: TOMLDocument,
    retained_key_paths: Iterable[tuple[str, ...]],
    retained_table_regexes: list[re.Pattern[str]],
) -> TOMLDocument:
    retained_doc = tomlkit.document()
    overlay_preserved_regex_paths(source_doc, retained_doc, retained_table_regexes)
    overlay_preserved_keys(source_doc, retained_doc, retained_key_paths)
    return normalize_document(retained_doc)


def build_document_with_selector_action(
    source_doc: TOMLDocument,
    selector_action: SelectorAction,
    key_paths: list[tuple[str, ...]],
    table_regexes: list[re.Pattern[str]],
) -> TOMLDocument:
    if selector_action == SelectorAction.REMOVE:
        return build_document_with_stripped_matchers(
            source_doc,
            key_paths,
            table_regexes,
        )
    return build_document_with_retained_matchers(
        source_doc,
        key_paths,
        table_regexes,
    )


def clone_empty_container(source: TomlContainer) -> TomlContainer:
    cloned = copy.deepcopy(source)
    for key in list(cloned.keys()):
        del cloned[key]
    return cloned


def overlay_with_base_slots(
    original_base: TomlContainer,
    preserved_base: TomlContainer,
    overlay_doc: TomlContainer,
) -> TomlContainer:
    merged = clone_empty_container(preserved_base)

    for key in original_base.keys():
        key_name = str(key)
        base_value = original_base.get(key_name)
        preserved_value = preserved_base.get(key_name)
        overlay_value = overlay_doc.get(key_name)

        if (
            isinstance(base_value, Table)
            and isinstance(preserved_value, Table)
            and isinstance(overlay_value, Table)
        ):
            merged[key_name] = overlay_with_base_slots(base_value, preserved_value, overlay_value)
            continue

        if overlay_value is not None:
            merged[key_name] = copy.deepcopy(overlay_value)
            continue

        if preserved_value is not None:
            merged[key_name] = copy.deepcopy(preserved_value)

    for key, overlay_value in overlay_doc.items():
        key_name = str(key)
        if key_name in merged:
            continue
        merged[key_name] = copy.deepcopy(overlay_value)

    for key, preserved_value in preserved_base.items():
        key_name = str(key)
        if key_name in merged:
            continue
        merged[key_name] = copy.deepcopy(preserved_value)

    return merged


def build_merged_document_output(
    base_path: Path,
    overlay_path: Path,
    selector_action: SelectorAction,
    key_paths: list[tuple[str, ...]],
    table_regexes: list[re.Pattern[str]],
    compare_path: Path | None = None,
    stdin_text: str | None = None,
) -> TransformOutput:
    base_doc = load_document(base_path, stdin_text=stdin_text)
    preserved_base = build_document_with_selector_action(
        base_doc,
        selector_action,
        key_paths,
        table_regexes,
    )
    overlay_doc = load_document(overlay_path, stdin_text=stdin_text)
    merged_doc = normalize_document(overlay_with_base_slots(base_doc, preserved_base, overlay_doc))
    merged_doc = restore_top_level_leading_trivia(merged_doc, overlay_doc, base_doc, preserved_base)
    collapse_duplicate_table_separators(merged_doc)
    return build_document_output(
        merged_doc,
        mode_reference_path=base_path,
        compare_path=compare_path,
    )



def merge_with_selector_action(
    base_path: Path,
    output_path: Path | None,
    overlay_path: Path,
    selector_action: SelectorAction,
    key_paths: list[tuple[str, ...]],
    table_regexes: list[re.Pattern[str]],
    compare_path: Path | None = None,
    stdout: bool = False,
) -> None:
    emit_transform_output(
        output_path,
        build_merged_document_output(
            base_path,
            overlay_path,
            selector_action,
            key_paths,
            table_regexes,
            compare_path=compare_path,
        ),
        stdout=stdout,
    )


def merge_keys(
    base_path: Path,
    output_path: Path | None,
    overlay_path: Path,
    retained_key_paths: Iterable[tuple[str, ...]],
    retained_table_regexes: list[re.Pattern[str]],
    compare_path: Path | None = None,
    stdout: bool = False,
) -> None:
    merge_with_selector_action(
        base_path,
        output_path,
        overlay_path,
        SelectorAction.RETAIN,
        list(retained_key_paths),
        retained_table_regexes,
        compare_path=compare_path,
        stdout=stdout,
    )


def merge_keys_except_stripped(
    base_path: Path,
    output_path: Path | None,
    overlay_path: Path,
    stripped_key_paths: list[tuple[str, ...]],
    stripped_table_regexes: list[re.Pattern[str]],
    compare_path: Path | None = None,
    stdout: bool = False,
 ) -> None:
    merge_with_selector_action(
        base_path,
        output_path,
        overlay_path,
        SelectorAction.REMOVE,
        stripped_key_paths,
        stripped_table_regexes,
        compare_path=compare_path,
        stdout=stdout,
    )


class TomlTransformEngine(BaseTransformEngine):
    name = "toml"
    SELECTOR_SPECS = (
        SelectorSpec(
            name="key",
            prefix="exact",
            is_default=True,
            description="exact TOML key path",
            examples=("model", "mcp_servers.playwright.env.PLAYWRIGHT_MCP_EXTENSION_TOKEN"),
        ),
        SelectorSpec(
            name="table_regex",
            prefix="re",
            description="regex matching dotted TOML table or key paths",
            examples=(r"^projects\.", r"^widget\.[^.]+\.enabled$"),
        ),
    )

    def configure_parser(self, parser) -> None:
        parser.add_argument(
            "--compare-file",
            type=Path,
            help="Optional TOML file to compare against for exact no-op text reuse.",
        )

    def build_engine_options(self, parsed_args) -> dict[str, Any]:
        return {
            "compare_path": parsed_args.compare_file,
            "stdout": parsed_args.stdout,
            "stdin_text": parsed_args.stdin_text,
        }

    def validate_request(self, request: TransformRequest) -> None:
        super().validate_request(request)
        parse_key_paths(request.selector_values("key"))
        compile_table_regexes(request.selector_values("table_regex"))

    def transform(self, request: TransformRequest) -> TransformOutput:
        self.validate_request(request)
        key_paths = parse_key_paths(request.selector_values("key"))
        table_regexes = compile_table_regexes(request.selector_values("table_regex"))
        compare_path = request.engine_option("compare_path")
        stdin_text = request.engine_option("stdin_text")

        if request.mode == TransformMode.CLEANUP:
            if request.selector_action == SelectorAction.REMOVE:
                return build_stripped_document_output(
                    request.base_path,
                    key_paths,
                    table_regexes,
                    compare_path=compare_path,
                    stdin_text=stdin_text,
                )

            filtered_doc = build_document_with_selector_action(
                load_document(request.base_path, stdin_text=stdin_text),
                request.selector_action,
                key_paths,
                table_regexes,
            )
            return build_document_output(
                filtered_doc,
                mode_reference_path=request.base_path,
                compare_path=compare_path,
            )

        assert request.overlay_path is not None
        return build_merged_document_output(
            request.base_path,
            request.overlay_path,
            request.selector_action,
            key_paths,
            table_regexes,
            compare_path=compare_path,
            stdin_text=stdin_text,
        )


def main(argv: list[str] | None = None) -> int:
    return run_engine_cli(TomlTransformEngine(), argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
