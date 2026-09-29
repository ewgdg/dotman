#!/usr/bin/env python3

from __future__ import annotations

import copy
from dataclasses import dataclass
import tomllib
import re
from collections.abc import Iterable
from typing import Any, NamedTuple
from pathlib import Path
import tomlkit
from tomlkit.container import Container
from tomlkit.items import (
    AbstractTable,
    AoT,
    Array,
    InlineTable,
    Item,
    Key,
    Null,
    SingleKey,
    Table,
    Trivia,
    Whitespace,
)
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
    decode_reference_text,
    read_input_text,
    read_reference_bytes,
    values_strictly_equal,
)


@dataclass(frozen=True)
class SplitTable:
    """A table whose content is split across the document, as its parts.

    Out-of-order headers (`[a]` ... `[b]` ... `[a.c]`) and repeated dotted
    keys leave one entry per part in the parent's body.
    """

    parts: tuple[Table, ...]


TomlContainer = TOMLDocument | AbstractTable | SplitTable


def detect_line_ending(text: str) -> str | None:
    """Return the line ending of the first line break in text, if any."""
    first_newline_index = text.find("\n")
    if first_newline_index < 0:
        return None
    return "\r\n" if text[:first_newline_index].endswith("\r") else "\n"


def parse_document(text: str) -> TOMLDocument:
    """Parse text with every comment block placed with its owner."""
    # tomlkit keeps CRLF inside multiline string values, while TOML readers like
    # tomllib normalize it to LF; parse LF text so values match what readers see
    # even after a string is re-rendered in another form (e.g. inline).
    return assign_comment_owners(tomlkit.parse(text.replace("\r\n", "\n")))


def parse_source_text(text: str) -> TOMLDocument:
    # A source's last line may lack a line break, but merging can move that
    # line before others, where it would run into the next key.
    if text and not text.endswith("\n"):
        text += "\n"
    return parse_document(text)


def load_document(
    path: Path, *, stdin_bytes: bytes | None = None
) -> tuple[TOMLDocument, str | None]:
    """Load a document parsed from LF text, with the source's line ending."""
    source_text = read_input_text(path, stdin_bytes=stdin_bytes)
    if source_text is None:
        return tomlkit.document(), None
    return parse_source_text(source_text), detect_line_ending(source_text)


def choose_line_ending(*source_line_endings: str | None) -> str:
    """Return the first known source line ending, defaulting to LF."""
    return next((line_ending for line_ending in source_line_endings if line_ending), "\n")


def render_document_text(doc: TOMLDocument, line_ending: str) -> str:
    # Documents are parsed from LF text and tomlkit writes LF for trivia it
    # creates, so apply the source line ending once at render time.
    return doc.as_string().replace("\n", line_ending)


def get_existing_text_if_unchanged(
    compare_path: Path,
    doc: TOMLDocument,
    content: str,
) -> bytes | None:
    existing_bytes = read_reference_bytes(compare_path)
    existing_content = decode_reference_text(existing_bytes)
    if existing_content is None:
        return None
    try:
        existing_doc = parse_document(existing_content)
    except Exception:
        existing_doc = None

    # Reparse the output so both sides place comments by the same ownership rule.
    if (
        existing_doc is not None
        and values_strictly_equal(existing_doc.unwrap(), doc.unwrap())
        and document_comment_signature(existing_doc)
        == document_comment_signature(parse_document(content))
    ):
        return existing_bytes

    if existing_content != content:
        return None

    return existing_bytes


def build_document_output(
    doc: TOMLDocument,
    *,
    mode_reference_path: Path,
    line_ending: str,
    compare_path: Path | None = None,
) -> TransformOutput:
    content = render_document_text(doc, line_ending)
    if compare_path is not None:
        existing_content = get_existing_text_if_unchanged(compare_path, doc, content)
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



def parse_key_path(raw_key: str) -> tuple[str, ...]:
    return tuple(split_toml_key(raw_key))


BASIC_STRING_KEY_SEGMENT = re.compile(r'"(?:[^"\\]|\\.)*"', re.DOTALL)
LITERAL_STRING_KEY_SEGMENT = re.compile(r"'[^']*'")


def split_toml_key(raw_key: str) -> list[str]:
    raw_segments: list[str] = []
    current: list[str] = []
    open_quote: str | None = None
    escape = False

    # Split on dots outside quotes, keeping each segment's raw text so quoted
    # segments can be decoded as a whole. Only basic ("...") strings have
    # escapes; literal ('...') strings end at the next single quote.
    for char in raw_key:
        if open_quote == '"' and escape:
            escape = False
        elif open_quote == '"' and char == "\\":
            escape = True
        elif open_quote is None and char in "\"'":
            open_quote = char
        elif char == open_quote:
            open_quote = None
        elif char == "." and open_quote is None:
            raw_segments.append("".join(current))
            current = []
            continue
        current.append(char)

    if open_quote is not None:
        raise ValueError(f"unterminated quoted TOML key: {raw_key}")

    raw_segments.append("".join(current))
    return [parse_key_part(raw_segment, raw_key) for raw_segment in raw_segments]


def parse_key_part(raw_segment: str, raw_key: str) -> str:
    segment = raw_segment.strip()
    if not segment:
        raise ValueError(
            f"empty segment in TOML key path {raw_key!r}; use \"\" for an empty key"
        )
    if segment.startswith("'"):
        if not LITERAL_STRING_KEY_SEGMENT.fullmatch(segment):
            raise ValueError(f"quoted TOML key segment must be one literal string: {raw_key}")
        return segment[1:-1]
    if not segment.startswith('"'):
        return segment
    if not BASIC_STRING_KEY_SEGMENT.fullmatch(segment):
        raise ValueError(f"quoted TOML key segment must be one basic string: {raw_key}")
    try:
        # Decode escapes with TOML's own basic-string rules; `""` is the empty key.
        return tomllib.loads(f"key = {segment}")["key"]
    except tomllib.TOMLDecodeError as error:
        raise ValueError(
            f"quoted TOML key segment is not a valid basic string: {segment}"
        ) from error


def split_key_path(key_path: tuple[str, ...]) -> tuple[tuple[str, ...], str]:
    return key_path[:-1], key_path[-1]


def is_table_like(value: object) -> bool:
    # Inline tables hold selectable keys like tables do.
    return isinstance(value, (AbstractTable, SplitTable))


def as_single_table(value: Any) -> Any:
    """Rebuild a table split across the document as one table copy.

    Other values are returned unchanged. The header, with its comments, comes
    from the part that prints one, and later parts' content follows the first
    part's content. The copy is a
    fresh table because a copied part keeps dotted-key rendering state that
    would emit nested dotted tables at a path relative to the wrong parent.
    """
    if not isinstance(value, SplitTable):
        return value
    body = Container()
    for child_name in child_key_names(value):
        body[child_name] = copy.deepcopy(as_single_table(body_item(value, child_name)))
    # Wrap only after filling: tomlkit indents a table's new children by any
    # spaces in the table's indent, which holds its attached comments.
    header_part = next(
        (part for part in value.parts if not part.is_super_table()), value.parts[0]
    )
    return Table(
        body,
        copy.deepcopy(header_part.trivia),
        is_aot_element=False,
        is_super_table=header_part.is_super_table(),
    )


def has_selected_ancestor(item_path: tuple[str, ...], selected_paths: set[tuple[str, ...]]) -> bool:
    return any(item_path[:length] in selected_paths for length in range(1, len(item_path)))


def get_container(root: TomlContainer, table_path: tuple[str, ...]) -> TomlContainer | None:
    current: Any = root
    for part in table_path:
        current = body_item(current, part)
        if not is_table_like(current):
            return None
    return current


def get_key_path_value(root: TomlContainer, key_path: tuple[str, ...]) -> Any | None:
    table_path, key_name = split_key_path(key_path)
    container = get_container(root, table_path)
    if container is None:
        return None
    return body_item(container, key_name)


def container_body_entries(container: TomlContainer) -> list[tuple[object, object]]:
    if isinstance(container, SplitTable):
        return [entry for part in container.parts for entry in container_body_entries(part)]
    if isinstance(container, TOMLDocument):
        return container.body
    return container.value.body


def child_key_names(container: TomlContainer) -> list[str]:
    # keys() gives insertion order, which merge and retain follow; the body
    # holds values above tables.
    parts = container.parts if isinstance(container, SplitTable) else (container,)
    return list(dict.fromkeys(key_name for part in parts for key_name in part.keys()))


def body_item(container: TomlContainer, key_name: str) -> Any:
    """Return the stored item; unlike get(), a boolean keeps its comments."""
    matches = [
        item
        for key, item in container_body_entries(container)
        if key is not None and key.key == key_name
    ]
    if len(matches) <= 1:
        return matches[0] if matches else None
    if all(isinstance(match, AoT) for match in matches):
        # An array of tables continued in another part of a split table.
        return AoT([element for match in matches for element in match.body], parsed=True)
    return SplitTable(tuple(matches))


def item_text(item: object) -> str:
    if hasattr(item, "as_string"):
        return item.as_string()
    return str(item)


def parsed_item_comment(item: object) -> str:
    try:
        return item.trivia.comment
    except (AttributeError, RuntimeError):
        return ""


def attached_comment_lines(item: object) -> tuple[str, ...]:
    return tuple(
        stripped_line
        for line in item.trivia.indent.splitlines()
        if (stripped_line := line.lstrip()).startswith("#")
    )


def next_key_name(
    body_entries: list[tuple[object, object]],
    start_index: int,
) -> str | None:
    for key, item in body_entries[start_index:]:
        if key is not None and not isinstance(item, Null):
            return str(key.key)
    return None


def array_comment_signature(
    array: Array,
    path: tuple[str, ...],
) -> list[tuple[tuple[str, ...], str, int, str]]:
    """Identify comments held in tomlkit's lossless array item groups."""
    comments: list[tuple[tuple[str, ...], str, int, str]] = []
    attachment_counts: dict[tuple[tuple[str, ...], str], int] = {}
    value_index = 0

    # Public Array iteration omits standalone comments, so lossless comparison
    # must inspect the item groups tomlkit uses to retain multiline trivia.
    for group in array._value:
        value = group.value
        comment = parsed_item_comment(group.comment)
        value_exists = value is not None and not isinstance(value, Null)
        item_path = path + (f"[{value_index}]",)

        if comment:
            attachment_kind = "element-inline" if value_exists else "before-element"
            attachment = (item_path, attachment_kind)
            attachment_index = attachment_counts.get(attachment, 0)
            attachment_counts[attachment] = attachment_index + 1
            comments.append((*attachment, attachment_index, comment))

        if value_exists:
            if isinstance(value, Array):
                comments.extend(array_comment_signature(value, item_path))
            value_index += 1

    return comments


def document_comment_signature(
    container: TomlContainer,
    path: tuple[str, ...] = (),
) -> tuple[tuple[tuple[str, ...], str, int, str], ...]:
    """Identify parsed comments by semantic attachment, independent of key order."""
    comments: list[tuple[tuple[str, ...], str, int, str]] = []
    attachment_counts: dict[tuple[tuple[str, ...], str], int] = {}
    body_entries = container_body_entries(container)

    for index, (key, item) in enumerate(body_entries):
        comment = parsed_item_comment(item)
        if key is None:
            if not comment:
                continue
            following_key_name = next_key_name(body_entries, index + 1)
            if following_key_name is None:
                attachment_path = path
                attachment_kind = "container-tail"
            else:
                attachment_path = path + (following_key_name,)
                attachment_kind = "before-item"
            attachment = (attachment_path, attachment_kind)
            attachment_index = attachment_counts.get(attachment, 0)
            attachment_counts[attachment] = attachment_index + 1
            comments.append((*attachment, attachment_index, comment))
            continue

        if isinstance(item, Null):
            continue

        item_path = path + (str(key.key),)
        if not isinstance(item, AoT):
            attachment = (item_path, "before-item")
            for attached_comment in attached_comment_lines(item):
                attachment_index = attachment_counts.get(attachment, 0)
                attachment_counts[attachment] = attachment_index + 1
                comments.append((*attachment, attachment_index, attached_comment))

        if comment:
            attachment_kind = "table-header" if isinstance(item, Table) else "item-inline"
            comments.append((item_path, attachment_kind, 0, comment))

        if isinstance(item, Table):
            comments.extend(document_comment_signature(item, item_path))
        elif isinstance(item, AoT):
            for table_index, table in enumerate(item):
                table_path = item_path + (f"[{table_index}]",)
                comments.extend(
                    (table_path, "before-item", line_index, attached_comment)
                    for line_index, attached_comment in enumerate(attached_comment_lines(table))
                )
                if table.trivia.comment:
                    comments.append((table_path, "table-header", 0, table.trivia.comment))
                comments.extend(document_comment_signature(table, table_path))
        elif isinstance(item, Array):
            comments.extend(array_comment_signature(item, item_path))

    return tuple(sorted(comments))


def is_comment_entry(entry: tuple[object, object]) -> bool:
    key, item = entry
    return key is None and item_text(item).lstrip().startswith("#")


# Comment ownership
#
# tomlkit stores comments by parse position: a comment above `[b]` lands at the
# end of the previous table's body. After parsing, each comment block (comment
# lines with no blank line between them) is placed with its owner instead:
#
# 1. A block directly above an item is attached to it: it is moved into the
#    item's header indent, so it moves and is deleted with the item.
# 2. A block that directly follows a table's content and is not attached to a
#    following item stays at the end of that table.
# 3. Every other block is independent: a standalone entry in the container of
#    the following item, so deleting items around it leaves it in place.
#
# Placement changes only; the document renders the same text.

TriviaEntry = tuple[None, object]


def entries_text(entries: list[TriviaEntry]) -> str:
    return "".join(item_text(item) for _key, item in entries)


def is_dotted_table_entry(key: object, item: object) -> bool:
    # A dotted key (`a.b = 1`) is a header-less table; tomlkit keeps the comments
    # around it in the parent's body, never inside it.
    return isinstance(item, Table) and key.is_dotted()


def is_section_entry(key: object, item: object) -> bool:
    """Whether a body entry renders under its own [table] or [[array]] header."""
    return (
        key is not None
        and isinstance(item, (Table, AoT))
        and not is_dotted_table_entry(key, item)
    )


def header_item(item: object) -> Any:
    """Return the item whose indent renders first, where attached comments go."""
    if isinstance(item, AoT):
        return header_item(item.body[0])
    if isinstance(item, Table) and item.is_super_table():
        # An implicit parent (`b` in `[b.k]`) prints no header of its own.
        first_child = next(child for key, child in container_body_entries(item) if key is not None)
        return header_item(first_child)
    return item


def attach_comments(item: object, entries: list[TriviaEntry]) -> None:
    if entries:
        header = header_item(item)
        header.trivia.indent = entries_text(entries) + header.trivia.indent


def textual_end_table(item: object) -> Table:
    """Return the table whose body renders last within item."""
    if isinstance(item, AoT):
        return textual_end_table(item.body[-1])
    entries = container_body_entries(item)
    if entries and is_section_entry(*entries[-1]):
        return textual_end_table(entries[-1][1])
    return item


def split_trivia_run(
    run: list[TriviaEntry],
    *,
    follows_table: bool,
    precedes_item: bool,
) -> tuple[list[TriviaEntry], list[TriviaEntry], list[TriviaEntry]]:
    """Split trivia between two items into (table-owned, independent, attached) parts."""
    attached_start = len(run)
    if precedes_item:
        while attached_start > 0 and is_comment_entry(run[attached_start - 1]):
            attached_start -= 1
    rest, attached = run[:attached_start], run[attached_start:]
    owned_end = 0
    if follows_table:
        while owned_end < len(rest) and is_comment_entry(rest[owned_end]):
            owned_end += 1
    return rest[:owned_end], rest[owned_end:], attached


def append_entries(container: Container | Table, entries: list[tuple[object, object]]) -> None:
    body = container.value if isinstance(container, Table) else container
    for key, item in entries:
        body.append(key, item)


def rebuilt(container: TOMLDocument | Table, entries: list[tuple[object, object]]) -> TOMLDocument | Table:
    """A copy of container holding entries, filled the way tomlkit's parser fills one."""
    body = TOMLDocument(True) if isinstance(container, TOMLDocument) else Container(True)
    append_entries(body, entries)
    if isinstance(container, TOMLDocument):
        return body
    return Table(
        body,
        container.trivia,
        is_aot_element=container.is_aot_element(),
        is_super_table=container.is_super_table(),
        name=container.name,
        display_name=container.display_name,
    )


def assign_comment_owners(doc: TOMLDocument) -> TOMLDocument:
    owned_doc, tail = with_comment_owners(doc)
    previous = next((item for key, item in reversed(owned_doc.body) if key is not None), None)
    owned, independent, _attached = split_trivia_run(
        tail, follows_table=isinstance(previous, (Table, AoT)), precedes_item=False
    )
    if owned:
        append_entries(textual_end_table(previous), owned)
    append_entries(owned_doc, independent)
    # Rebuilt containers are filled in parser mode, which keeps entries in the
    # order given; leave it like tomlkit.parse() does.
    owned_doc.parsing(False)
    return owned_doc


def with_comment_owners(
    container: TOMLDocument | Table,
) -> tuple[TOMLDocument | Table, list[TriviaEntry]]:
    """Rebuild container with its comments placed; also return its trailing trivia."""
    # A standalone comment would make tomlkit print an implicit parent's header,
    # so an independent block inside one stays above the next child's header.
    is_implicit_parent = isinstance(container, Table) and container.is_super_table()
    entries: list[tuple[object, object]] = []
    run: list[TriviaEntry] = []
    previous: object = None

    for key, item in container_body_entries(container):
        if key is None:
            run.append((key, item))
            continue
        owned, independent, attached = split_trivia_run(
            run, follows_table=isinstance(previous, (Table, AoT)), precedes_item=True
        )
        if owned:
            append_entries(textual_end_table(previous), owned)
        if is_implicit_parent:
            attached = independent + attached
        else:
            entries.extend(independent)
        item, run = item_with_comment_owners(key, item)
        attach_comments(item, attached)
        entries.append((key, item))
        previous = item

    return rebuilt(container, entries), run


def item_with_comment_owners(key: object, item: object) -> tuple[object, list[TriviaEntry]]:
    if isinstance(item, AoT):
        return array_of_tables_with_comment_owners(item)
    if isinstance(item, Table) and not is_dotted_table_entry(key, item):
        return with_comment_owners(item)
    return item, []


def array_of_tables_with_comment_owners(array_of_tables: AoT) -> tuple[AoT, list[TriviaEntry]]:
    elements: list[Table] = []
    run: list[TriviaEntry] = []
    for element in array_of_tables.body:
        element, element_run = with_comment_owners(element)
        if elements:
            # An array of tables holds no standalone entries, so only an attached
            # block moves; the rest stays at the end of the earlier element.
            owned, independent, attached = split_trivia_run(
                run, follows_table=True, precedes_item=True
            )
            append_entries(textual_end_table(elements[-1]), owned + independent)
            attach_comments(element, attached)
        elements.append(element)
        run = element_run
    return AoT(elements, name=array_of_tables.name, parsed=True), run


def inline_table_without_key(inline_table: InlineTable, removed_key_name: str) -> InlineTable:
    rebuilt = tomlkit.inline_table()
    for key_name, value in inline_table.items():
        if key_name != removed_key_name:
            rebuilt[key_name] = copy.deepcopy(value)
    return rebuilt


def parts_holding(container: TomlContainer, key_name: str) -> list[TOMLDocument | AbstractTable]:
    """The containers whose own body holds key_name: some parts of a split table, or container."""
    parts = container.parts if isinstance(container, SplitTable) else (container,)
    return [
        part
        for part in parts
        if any(key is not None and key.key == key_name for key, _item in container_body_entries(part))
    ]


def delete_key_path(root: TomlContainer, key_path: tuple[str, ...]) -> None:
    table_path, key_name = split_key_path(key_path)
    container = get_container(root, table_path)
    if container is None:
        return
    if isinstance(container, InlineTable):
        if key_name not in container:
            return
        # tomlkit leaves a dangling separator when deleting a middle key from an
        # inline table (`{b = 1, , e = 3}`), so replace it with a rebuilt copy.
        parent_path, inline_table_name = split_key_path(table_path)
        [holder] = parts_holding(get_container(root, parent_path), inline_table_name)
        holder[inline_table_name] = inline_table_without_key(container, key_name)
        return
    for holder in parts_holding(container, key_name):
        del holder[key_name]


def iter_item_paths_in_order(
    root: TomlContainer,
    prefix: tuple[str, ...] = (),
) -> Iterable[tuple[str, ...]]:
    for key_name in child_key_names(root):
        key_path = prefix + (key_name,)
        yield key_path
        value = body_item(root, key_name)
        if is_table_like(value):
            yield from iter_item_paths_in_order(value, key_path)


def matches_path_regex(item_path: tuple[str, ...], path_regexes: list[re.Pattern[str]]) -> bool:
    raw_item_path = ".".join(item_path)
    return any(path_regex.search(raw_item_path) for path_regex in path_regexes)


def parse_key_paths(raw_key_paths: Iterable[str]) -> list[tuple[str, ...]]:
    return [parse_key_path(raw_key) for raw_key in raw_key_paths]


def compile_table_regexes(raw_table_regexes: Iterable[str]) -> list[re.Pattern[str]]:
    return list(compile_selector_regexes(raw_table_regexes, "TOML path selector"))


# Scan strings and comments as whole tokens so blank-line runs are collapsed
# only between items, never inside multiline string values.
TOML_STRING_COMMENT_OR_BLANK_RUN = re.compile(
    r'(?P<opaque>"""(?:\\.|[^\\])*?"""(?:"{1,2})?'
    r"|'''.*?'''(?:'{1,2})?"
    r'|"(?:\\.|[^"\\\n])*"'
    r"|'[^'\n]*'"
    r"|#[^\r\n]*)"
    # A line holding only spaces or tabs counts as blank. Documents are LF text.
    r"|(?P<leading_blank_run>\A(?:[ \t]*\n){2,})"
    r"|(?P<trailing_blank_run>\n(?:[ \t]*\n)*[ \t]*\Z)"
    r"|(?P<blank_run>\n(?:[ \t]*\n){2,})",
    re.DOTALL,
)
# Runs collapse to one blank line; blank lines at the end separate nothing.
BLANK_RUN_REPLACEMENTS = {
    "leading_blank_run": "\n",
    "trailing_blank_run": "\n",
    "blank_run": "\n\n",
}


def normalize_blank_lines(content: str) -> str:
    return TOML_STRING_COMMENT_OR_BLANK_RUN.sub(
        lambda match: BLANK_RUN_REPLACEMENTS.get(match.lastgroup, match.group(0)),
        content,
    )


def normalize_document(doc: TOMLDocument) -> TOMLDocument:
    return parse_document(normalize_blank_lines(doc.as_string()))


def ensure_container(
    root: TomlContainer,
    table_path: tuple[str, ...],
    source_root: TomlContainer,
) -> TomlContainer:
    """Create the tables along a path copied from source_root, keeping inline style."""
    current: TomlContainer = root
    source_current: Any = source_root
    for part in table_path:
        source_current = source_current[part]
        next_value = current.get(part)
        if not is_table_like(next_value):
            current[part] = (
                tomlkit.inline_table() if isinstance(source_current, InlineTable) else tomlkit.table()
            )
            next_value = current[part]
        current = next_value
    return current


def build_document_with_stripped_matchers(
    source_doc: TOMLDocument,
    stripped_key_paths: list[tuple[str, ...]],
    stripped_table_regexes: list[re.Pattern[str]],
) -> TOMLDocument:
    stripped_doc = copy.deepcopy(source_doc)
    selected_paths = {
        item_path
        for item_path in iter_item_paths_in_order(stripped_doc)
        if matches_path_regex(item_path, stripped_table_regexes)
    } | set(stripped_key_paths)
    # Delete only the topmost selections: deleting a descendant first can
    # leave tomlkit reporting an emptied dotted parent that no longer deletes.
    for item_path in selected_paths:
        if not has_selected_ancestor(item_path, selected_paths):
            delete_key_path(stripped_doc, item_path)

    return normalize_document(stripped_doc)


def build_stripped_document_output(
    base_path: Path,
    stripped_key_paths: list[tuple[str, ...]],
    stripped_table_regexes: list[re.Pattern[str]],
    compare_path: Path | None = None,
    stdin_bytes: bytes | None = None,
) -> TransformOutput:
    source_doc, source_line_ending = load_document(base_path, stdin_bytes=stdin_bytes)
    normalized_doc = build_document_with_stripped_matchers(
        source_doc,
        stripped_key_paths,
        stripped_table_regexes,
    )
    return build_document_output(
        normalized_doc,
        mode_reference_path=base_path,
        line_ending=choose_line_ending(source_line_ending),
        compare_path=compare_path,
    )



def copy_retained_paths(
    source_doc: TomlContainer,
    target_doc: TomlContainer,
    retained_key_paths: Iterable[tuple[str, ...]],
    retained_table_regexes: list[re.Pattern[str]],
) -> None:
    """Copy every selected item in one pass so the target keeps document order."""
    retained_key_path_set = set(retained_key_paths)
    copied_paths: set[tuple[str, ...]] = set()
    for item_path in iter_item_paths_in_order(source_doc):
        if item_path not in retained_key_path_set and not matches_path_regex(
            item_path, retained_table_regexes
        ):
            continue
        # A copied table already carries its whole subtree.
        if has_selected_ancestor(item_path, copied_paths):
            continue

        retained_item = get_key_path_value(source_doc, item_path)
        if retained_item is None:
            continue

        parent_path, item_name = split_key_path(item_path)
        source_key = keys_by_name(get_container(source_doc, parent_path))[item_name]
        retained_value = copy.deepcopy(as_single_table(retained_item))
        target_container = ensure_container(target_doc, parent_path, source_doc)
        target_container[copied_item_key(source_key, retained_value)] = retained_value
        copied_paths.add(item_path)


def build_document_with_retained_matchers(
    source_doc: TOMLDocument,
    retained_key_paths: Iterable[tuple[str, ...]],
    retained_table_regexes: list[re.Pattern[str]],
) -> TOMLDocument:
    retained_doc = tomlkit.document()
    copy_retained_paths(source_doc, retained_doc, retained_key_paths, retained_table_regexes)
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


def adapt_merged_value(
    value: Any,
    source_container: TomlContainer,
    merged_is_inline: bool,
) -> Item:
    """Copy a value from source_container, adapted to the merged container's style."""
    # Container lookups return booleans as plain bool, which has no trivia.
    value = tomlkit.item(copy.deepcopy(as_single_table(value)))
    if merged_is_inline and isinstance(value, (Table, AoT)):
        # Inline tables cannot hold [table] or [[array]] sections; let tomlkit
        # rebuild the plain data in inline form.
        return tomlkit.item(value.unwrap(), _parent=tomlkit.inline_table())
    if merged_is_inline != isinstance(source_container, InlineTable):
        # Line breaks and comments of a key-value line are invalid inside an
        # inline table, and a value parsed inline has no line break of its own.
        value.trivia.indent = ""
        value.trivia.comment_ws = ""
        value.trivia.comment = ""
        value.trivia.trail = "" if merged_is_inline else "\n"
    return value


def trivia_identity_text(entries: list[TriviaEntry]) -> str:
    """Comment text of a trivia run, ignoring blank-line padding."""
    return "\n".join(line for line in entries_text(entries).splitlines() if line.strip())


@dataclass(frozen=True)
class IndependentTrivia:
    """A container's standalone trivia runs, by the key each run precedes."""

    before_key: dict[str, list[TriviaEntry]]
    tail: list[TriviaEntry]

    def block_texts(self) -> set[str]:
        return {
            text
            for run in (*self.before_key.values(), self.tail)
            if (text := trivia_identity_text(run))
        }


def collect_independent_trivia(container: TomlContainer) -> IndependentTrivia:
    if isinstance(container, InlineTable):
        # Entries without a key in an inline table are separators, not comments.
        return IndependentTrivia({}, [])
    before_key: dict[str, list[TriviaEntry]] = {}
    run: list[TriviaEntry] = []
    for key, item in container_body_entries(container):
        if key is None:
            run.append((None, item))
            continue
        before_key.setdefault(key.key, []).extend(run)
        run = []
    return IndependentTrivia(before_key, run)


def append_trivia(container: TomlContainer, entries: list[TriviaEntry]) -> None:
    if isinstance(container, InlineTable):
        # An inline table holds no standalone comments or blank lines.
        return
    for _key, item in entries:
        if isinstance(item, Whitespace):
            # tomlkit inserts new values above unfixed trailing whitespace, which
            # would move a blank line below the item it separates.
            item = Whitespace(item.s, fixed=True)
        container.append(None, copy.deepcopy(item))


class MergeEntry(NamedTuple):
    key: Key
    preserved_value: Any
    overlay_value: Any


class MergedItem(NamedTuple):
    key: Key
    value: Item
    leading_trivia: list[TriviaEntry]
    is_section: bool
    from_overlay: bool


SECTION_SEPARATOR: list[TriviaEntry] = [(None, Whitespace("\n"))]


def keys_by_name(container: TomlContainer) -> dict[str, Key]:
    return {key.key: key for key, _item in container_body_entries(container) if key is not None}


def renders_section_inside(item: object) -> bool:
    """Whether a table renders a [table] or [[array]] header anywhere inside it."""
    return isinstance(item, Table) and any(
        is_section_entry(key, child)
        or (is_dotted_table_entry(key, child) and renders_section_inside(child))
        for key, child in container_body_entries(item)
    )


def copied_item_key(key: Key, value: Item) -> Key:
    """Key to set a copied value under.

    A dotted key keeps its `a.b = 1` form while the value is still a table that
    renders only key-value lines; tomlkit prints a section nested under a dotted
    key at the wrong path. Other keys are rebuilt from the name, as a key parsed
    from a table header keeps header spelling that is invalid elsewhere.
    """
    if key.is_dotted() and isinstance(value, Table) and not renders_section_inside(value):
        return key
    return SingleKey(key.key)


def merge_containers(
    base: TomlContainer,
    preserved_base: TomlContainer,
    overlay: TomlContainer,
) -> TomlContainer:
    """Merge overlay onto the kept base; every item brings its own comments.

    Keys follow the base order, then overlay-only, then kept-base-only keys, with
    values before sections. A table on both sides takes its header from the
    overlay and merges its body.
    """
    merged_is_inline = isinstance(overlay, InlineTable)
    # Tables are filled under blank trivia and take the overlay's trivia only
    # afterwards: tomlkit indents new children by any spaces in their table's
    # indent, which holds the table's attached comments.
    if merged_is_inline:
        # Deleting keys from a parsed inline table leaves broken separators in
        # tomlkit, so inline results start fresh.
        merged: TomlContainer | Container = InlineTable(Container(), Trivia(), new=True)
    elif isinstance(overlay, TOMLDocument):
        merged = tomlkit.document()
    else:
        merged = Container()

    overlay_trivia = collect_independent_trivia(overlay)
    preserved_trivia = collect_independent_trivia(preserved_base)
    overlay_block_texts = overlay_trivia.block_texts()

    def merged_value(entry: MergeEntry) -> Item:
        if isinstance(entry.preserved_value, AbstractTable) and isinstance(
            entry.overlay_value, AbstractTable
        ):
            base_value = as_single_table(body_item(base, entry.key.key))
            return merge_containers(base_value, entry.preserved_value, entry.overlay_value)
        if entry.overlay_value is not None:
            return adapt_merged_value(entry.overlay_value, overlay, merged_is_inline)
        return adapt_merged_value(entry.preserved_value, preserved_base, merged_is_inline)

    def leading_trivia(entry: MergeEntry) -> list[TriviaEntry]:
        if entry.overlay_value is not None:
            return overlay_trivia.before_key.get(entry.key.key, [])
        leading = preserved_trivia.before_key.get(entry.key.key, [])
        # The overlay already places this block, e.g. at a key it moved to
        # after capture removed the key the block used to precede.
        return [] if trivia_identity_text(leading) in overlay_block_texts else leading

    def merged_item(entry: MergeEntry) -> MergedItem:
        value = merged_value(entry)
        key = copied_item_key(entry.key, value)
        return MergedItem(
            key,
            value,
            leading_trivia(entry),
            is_section=not merged_is_inline and is_section_entry(key, value),
            from_overlay=entry.overlay_value is not None,
        )

    key_names = dict.fromkeys(
        key_name for source in (base, overlay, preserved_base) for key_name in child_key_names(source)
    )
    overlay_keys = keys_by_name(overlay)
    preserved_keys = keys_by_name(preserved_base)
    merged_items = [
        merged_item(
            MergeEntry(
                overlay_keys.get(key_name) or preserved_keys[key_name],
                as_single_table(body_item(preserved_base, key_name)),
                as_single_table(body_item(overlay, key_name)),
            )
        )
        for key_name in key_names
        if key_name in overlay_keys or key_name in preserved_keys
    ]
    # tomlkit moves a value added after a table above all tables, away from its
    # leading comments, so values go first and nothing is moved.
    merged_items.sort(key=lambda item: item.is_section)

    for index, item in enumerate(merged_items):
        leading = item.leading_trivia
        if item.is_section and not item.from_overlay and not leading and index > 0:
            # A kept base section has no place in the overlay's layout, so it is
            # set off like a new table. tomlkit does that only for an empty
            # header indent, which may hold attached comments here.
            leading = SECTION_SEPARATOR
        append_trivia(merged, leading)
        merged[item.key] = item.value
    append_trivia(merged, overlay_trivia.tail or preserved_trivia.tail)

    if isinstance(merged, InlineTable):
        return InlineTable(merged.value, copy.deepcopy(overlay.trivia), new=True)
    if type(merged) is Container:
        return Table(
            merged,
            copy.deepcopy(overlay.trivia),
            is_aot_element=False,
            is_super_table=overlay.is_super_table(),
            name=overlay.name,
            display_name=overlay.display_name,
        )
    return merged


def build_merged_document_output(
    base_path: Path,
    overlay_path: Path,
    selector_action: SelectorAction,
    key_paths: list[tuple[str, ...]],
    table_regexes: list[re.Pattern[str]],
    compare_path: Path | None = None,
    stdin_bytes: bytes | None = None,
) -> TransformOutput:
    base_doc, base_line_ending = load_document(base_path, stdin_bytes=stdin_bytes)
    preserved_base = build_document_with_selector_action(
        base_doc,
        selector_action,
        key_paths,
        table_regexes,
    )
    overlay_doc, overlay_line_ending = load_document(overlay_path, stdin_bytes=stdin_bytes)
    merged_doc = normalize_document(merge_containers(base_doc, preserved_base, overlay_doc))
    return build_document_output(
        merged_doc,
        mode_reference_path=base_path,
        line_ending=choose_line_ending(base_line_ending, overlay_line_ending),
        compare_path=compare_path,
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
            "stdin_bytes": parsed_args.stdin_bytes,
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
        stdin_bytes = request.engine_option("stdin_bytes")

        if request.mode == TransformMode.CLEANUP:
            if request.selector_action == SelectorAction.REMOVE:
                return build_stripped_document_output(
                    request.base_path,
                    key_paths,
                    table_regexes,
                    compare_path=compare_path,
                    stdin_bytes=stdin_bytes,
                )

            source_doc, source_line_ending = load_document(
                request.base_path, stdin_bytes=stdin_bytes
            )
            filtered_doc = build_document_with_selector_action(
                source_doc,
                request.selector_action,
                key_paths,
                table_regexes,
            )
            return build_document_output(
                filtered_doc,
                mode_reference_path=request.base_path,
                line_ending=choose_line_ending(source_line_ending),
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
            stdin_bytes=stdin_bytes,
        )


def main(argv: list[str] | None = None) -> int:
    return run_engine_cli(TomlTransformEngine(), argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
