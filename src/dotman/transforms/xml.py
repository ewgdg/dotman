#!/usr/bin/env python3

from __future__ import annotations

import copy
import fnmatch
from pathlib import Path
import re
import xml.dom.minidom
import xml.etree.ElementTree as ET

from dotman.transforms.cli import run_engine_cli
from dotman.transforms.framework import (
    BaseTransformEngine,
    SelectorAction,
    SelectorSpec,
    TransformOutput,
    TransformRequest,
    compile_selector_regexes,
    emit_transform_output,
)


NodeRegex = re.Pattern[str]


def compile_node_regexes(raw_node_regexes: tuple[str, ...]) -> tuple[NodeRegex, ...]:
    return compile_selector_regexes(raw_node_regexes, "XML node selector")


def matches_node_path(
    node_path: str,
    node_matchers: list[str],
    node_regexes: tuple[NodeRegex, ...] = (),
) -> bool:
    return any(fnmatch.fnmatch(node_path, node_matcher) for node_matcher in node_matchers) or any(
        node_regex.search(node_path) for node_regex in node_regexes
    )


def element_identity_key(element: ET.Element) -> tuple[tuple[str, str], ...] | None:
    identity_parts: list[tuple[str, str]] = []
    for attribute_name in ("id", "name", "key", "uuid"):
        attribute_value = element.attrib.get(attribute_name)
        if attribute_value is not None:
            identity_parts.append((attribute_name, attribute_value))

    text_value = (element.text or "").strip()
    if text_value:
        identity_parts.append(("text", text_value))

    if not identity_parts:
        return None

    return tuple(identity_parts)


def pop_matching_child(
    target: ET.Element,
    candidates: list[ET.Element],
) -> ET.Element | None:
    identity_key = element_identity_key(target)
    if identity_key is not None:
        for index, child in enumerate(candidates):
            if child.tag == target.tag and element_identity_key(child) == identity_key:
                return candidates.pop(index)

    for index, child in enumerate(candidates):
        if child.tag == target.tag:
            return candidates.pop(index)

    return None


def overlay_with_base_slots(
    original_base_node: ET.Element,
    preserved_base_node: ET.Element | None,
    overlay_node: ET.Element,
) -> ET.Element:
    if preserved_base_node is None:
        return copy.deepcopy(overlay_node)

    result = copy.deepcopy(preserved_base_node)
    result.attrib.clear()
    result.attrib.update(copy.deepcopy(overlay_node.attrib))
    result.text = overlay_node.text
    result.tail = overlay_node.tail

    preserved_children = list(preserved_base_node)
    overlay_children = [copy.deepcopy(child) for child in overlay_node]
    merged_children: list[ET.Element] = []

    for base_child in original_base_node:
        preserved_child = pop_matching_child(base_child, preserved_children)
        overlay_child = pop_matching_child(base_child, overlay_children)

        if overlay_child is not None and preserved_child is not None:
            merged_children.append(
                overlay_with_base_slots(base_child, preserved_child, overlay_child)
            )
            continue
        if overlay_child is not None:
            merged_children.append(overlay_child)
            continue
        if preserved_child is not None:
            merged_children.append(preserved_child)

    merged_children.extend(preserved_children)
    merged_children.extend(overlay_children)
    result[:] = merged_children
    return result


def overlay_retained_nodes(
    base_root: ET.Element,
    overlay_root: ET.Element,
    node_matchers: list[str] | None = None,
    node_regexes: tuple[NodeRegex, ...] = (),
) -> None:
    if node_matchers is None:
        base_root.attrib.clear()
        base_root.attrib.update(copy.deepcopy(overlay_root.attrib))
        base_root.text = overlay_root.text
        base_root.tail = overlay_root.tail

    def overlay_retained_nodes_recursion(
        base_node: ET.Element,
        overlay_node: ET.Element,
        cur_path: str,
        node_matchers: list[str] | None = None,
    ) -> None:
        original_base_children = list(base_node)
        children_by_tag: dict[str, list[tuple[int, ET.Element]]] = {}
        children_by_identity: dict[
            tuple[str, tuple[tuple[str, str], ...]], list[tuple[int, ET.Element]]
        ] = {}

        for index, child in enumerate(original_base_children):
            children_by_tag.setdefault(child.tag, []).append((index, child))
            identity_key = element_identity_key(child)
            if identity_key is not None:
                children_by_identity.setdefault((child.tag, identity_key), []).append(
                    (index, child)
                )

        used_indices: set[int] = set()
        merged_children: list[ET.Element] = []

        def find_matching_child(
            target: ET.Element,
        ) -> tuple[int, ET.Element] | None:
            identity_key = element_identity_key(target)
            if identity_key is not None:
                for index, child in children_by_identity.get(
                    (target.tag, identity_key), []
                ):
                    if index not in used_indices:
                        return (index, child)
                return None

            for index, child in children_by_tag.get(target.tag, []):
                if index not in used_indices:
                    return (index, child)

            return None

        for overlay_child in overlay_node:
            child_path = f"{cur_path}/{overlay_child.tag}"
            match = find_matching_child(overlay_child)
            should_overlay = False
            if node_matchers or node_regexes:
                should_overlay = matches_node_path(
                    child_path,
                    node_matchers or [],
                    node_regexes,
                )
            else:
                should_overlay = len(overlay_child) == 0 or match is None

            if match is not None:
                base_child_index, base_child = match
                used_indices.add(base_child_index)
                if should_overlay:
                    merged_children.append(copy.deepcopy(overlay_child))
                else:
                    overlay_retained_nodes_recursion(
                        base_child,
                        overlay_child,
                        child_path,
                        node_matchers,
                    )
                    merged_children.append(base_child)
            elif should_overlay:
                merged_children.append(copy.deepcopy(overlay_child))

        for index, child in enumerate(original_base_children):
            if index in used_indices:
                continue
            merged_children.append(child)

        base_node[:] = merged_children

    overlay_retained_nodes_recursion(base_root, overlay_root, base_root.tag, node_matchers)


def build_tree_with_retained_nodes(
    source_root: ET.Element,
    node_matchers: list[str],
    node_regexes: tuple[NodeRegex, ...] = (),
) -> ET.Element:
    def build_retained_subtree(current: ET.Element, cur_path: str) -> ET.Element | None:
        if matches_node_path(cur_path, node_matchers, node_regexes):
            return copy.deepcopy(current)

        retained_children: list[ET.Element] = []
        for child in current:
            child_path = f"{cur_path}/{child.tag}"
            retained_child = build_retained_subtree(child, child_path)
            if retained_child is not None:
                retained_children.append(retained_child)

        retained_current = ET.Element(current.tag, dict(current.attrib))
        retained_current.text = current.text
        retained_current.tail = current.tail
        retained_current.extend(retained_children)
        if retained_children or cur_path == source_root.tag:
            return retained_current
        return None

    retained_root = build_retained_subtree(source_root, source_root.tag)
    if retained_root is None:
        return ET.Element(source_root.tag, dict(source_root.attrib))
    return retained_root


def strip_nodes(
    root: ET.Element,
    node_matchers: list[str],
    node_regexes: tuple[NodeRegex, ...] = (),
) -> None:
    def strip_nodes_recursion(
        current: ET.Element,
        parent: ET.Element | None,
        cur_path: str,
        node_matchers: list[str],
    ) -> None:
        if matches_node_path(cur_path, node_matchers, node_regexes):
            if parent is not None:
                parent.remove(current)
            else:
                current.clear()
            return

        for child in list(current):
            child_path = f"{cur_path}/{child.tag}"
            strip_nodes_recursion(child, current, child_path, node_matchers)

    strip_nodes_recursion(root, None, root.tag, node_matchers)


def build_tree_with_stripped_nodes(
    source_root: ET.Element,
    node_matchers: list[str],
    node_regexes: tuple[NodeRegex, ...] = (),
) -> ET.Element:
    stripped_root = copy.deepcopy(source_root)
    strip_nodes(stripped_root, node_matchers, node_regexes)
    return stripped_root


def build_tree_with_selector_action(
    source_root: ET.Element,
    node_matchers: list[str],
    selector_action: SelectorAction,
    node_regexes: tuple[NodeRegex, ...] = (),
) -> ET.Element:
    if not node_matchers and not node_regexes:
        return copy.deepcopy(source_root)
    if selector_action == SelectorAction.RETAIN:
        return build_tree_with_retained_nodes(source_root, node_matchers, node_regexes)
    return build_tree_with_stripped_nodes(source_root, node_matchers, node_regexes)


def parse_node_matchers(raw_node_matchers: tuple[str, ...] | list[str]) -> list[str]:
    parsed_node_matchers: list[str] = []
    for raw_matcher_group in raw_node_matchers:
        for raw_matcher in raw_matcher_group.split(","):
            node_matcher = raw_matcher.strip()
            if node_matcher:
                parsed_node_matchers.append(node_matcher)
    return parsed_node_matchers


def sort_xml_attributes(root: ET.Element) -> None:
    for elem in root.iter():
        sorted_attributes = dict(sorted(elem.attrib.items()))
        elem.attrib.clear()
        elem.attrib.update(sorted_attributes)


def strip_whitespace_text_nodes(root: ET.Element) -> None:
    for elem in root.iter():
        if elem.text is not None and elem.text.strip() == "":
            elem.text = None
        if elem.tail is not None and elem.tail.strip() == "":
            elem.tail = None


def canonical_xml_sort_key(element: ET.Element) -> str:
    normalized = copy.deepcopy(element)
    strip_whitespace_text_nodes(normalized)
    sort_xml_attributes(normalized)
    return ET.tostring(normalized, encoding="unicode")


def sort_selected_children(root: ET.Element, parent_matchers: list[str]) -> None:
    def sort_selected_children_recursion(current: ET.Element, cur_path: str) -> None:
        if matches_node_path(cur_path, parent_matchers):
            current[:] = sorted(current, key=canonical_xml_sort_key)

        for child in current:
            child_path = f"{cur_path}/{child.tag}"
            sort_selected_children_recursion(child, child_path)

    sort_selected_children_recursion(root, root.tag)


def normalized_xml_for_compare(
    root: ET.Element,
    child_sort_parent_matchers: list[str] | None = None,
) -> str:
    normalized = copy.deepcopy(root)
    strip_whitespace_text_nodes(normalized)
    sort_xml_attributes(normalized)
    if child_sort_parent_matchers:
        sort_selected_children(normalized, child_sort_parent_matchers)
    return ET.tostring(normalized, encoding="unicode")


def get_existing_xml_bytes_if_semantically_unchanged(
    compare_path: Path,
    root: ET.Element,
    child_sort_parent_matchers: list[str] | None = None,
 ) -> bytes | None:
    if not compare_path.is_file():
        return None

    existing_bytes = compare_path.read_bytes()
    try:
        existing_root = ET.fromstring(existing_bytes)
    except ET.ParseError:
        return None

    if normalized_xml_for_compare(
        existing_root,
        child_sort_parent_matchers=child_sort_parent_matchers,
    ) != normalized_xml_for_compare(
        root,
        child_sort_parent_matchers=child_sort_parent_matchers,
    ):
        return None

    return existing_bytes


def build_pretty_xml_text(root: ET.Element) -> str:
    xml_string = ET.tostring(root, encoding="unicode")
    dom = xml.dom.minidom.parseString(xml_string)
    pretty_xml = dom.toprettyxml(indent="  ", newl="\n")
    return "\n".join(line for line in pretty_xml.splitlines() if line.strip())



def render_xml_output(
    base_path: str | Path,
    node_matchers: list[str] | None = None,
    node_regexes: tuple[NodeRegex, ...] = (),
    sort_attributes: bool = False,
    overlay_path: str | Path | None = None,
    selector_action: SelectorAction | None = None,
    compare_path: str | Path | None = None,
    child_sort_parent_matchers: list[str] | None = None,
    stdin_bytes: bytes | None = None,
) -> TransformOutput:
    base_path = Path(base_path)
    overlay_path = Path(overlay_path) if overlay_path is not None else None
    compare_path = Path(compare_path) if compare_path is not None else None
    effective_selector_action = (
        selector_action
        if selector_action is not None
        else SelectorAction.RETAIN
        if overlay_path is not None
        else SelectorAction.REMOVE
    )
    parsed_node_matchers = node_matchers or []
    parsed_child_sort_parent_matchers = child_sort_parent_matchers or []

    base_root = None
    if base_path == Path("-"):
        assert stdin_bytes is not None
        base_root = ET.fromstring(stdin_bytes)
    elif base_path.is_file():
        base_root = ET.parse(base_path).getroot()

    overlay_root = None
    if overlay_path == Path("-"):
        assert stdin_bytes is not None
        overlay_root = ET.fromstring(stdin_bytes)
    elif overlay_path is not None and overlay_path.is_file():
        overlay_root = ET.parse(overlay_path).getroot()

    if base_root is None:
        if overlay_root is None:
            raise FileNotFoundError(f"File not found: {base_path}")
        root = ET.Element(overlay_root.tag)
    else:
        root = build_tree_with_selector_action(
            base_root,
            parsed_node_matchers,
            effective_selector_action,
            node_regexes,
        )

    if overlay_root is not None:
        if base_root is None:
            root = copy.deepcopy(overlay_root)
        else:
            preserved_root = root
            root = overlay_with_base_slots(base_root, preserved_root, overlay_root)

    if sort_attributes:
        sort_xml_attributes(root)
    if parsed_child_sort_parent_matchers:
        sort_selected_children(root, parsed_child_sort_parent_matchers)

    if compare_path is not None:
        existing_bytes = get_existing_xml_bytes_if_semantically_unchanged(
            compare_path,
            root,
            child_sort_parent_matchers=parsed_child_sort_parent_matchers,
        )
        if existing_bytes is not None:
            return TransformOutput(
                content=existing_bytes,
                mode_reference_path=None if base_path == Path("-") else base_path,
                reused_compare_path=compare_path,
            )

    return TransformOutput(
        content=build_pretty_xml_text(root),
        mode_reference_path=None if base_path == Path("-") else base_path,
    )



def transform_xml(
    base_path: str | Path,
    output_path: str | Path | None,
    node_matchers: list[str] | None = None,
    node_regexes: tuple[NodeRegex, ...] = (),
    sort_attributes: bool = False,
    overlay_path: str | Path | None = None,
    selector_action: SelectorAction | None = None,
    compare_path: str | Path | None = None,
    child_sort_parent_matchers: list[str] | None = None,
    stdout: bool = False,
 ) -> None:
    emit_transform_output(
        Path(output_path) if output_path is not None else None,
        render_xml_output(
            base_path,
            node_matchers=node_matchers,
            node_regexes=node_regexes,
            sort_attributes=sort_attributes,
            overlay_path=overlay_path,
            selector_action=selector_action,
            compare_path=compare_path,
            child_sort_parent_matchers=child_sort_parent_matchers,
        ),
        stdout=stdout,
    )


class XmlTransformEngine(BaseTransformEngine):
    name = "xml"
    SELECTOR_SPECS = (
        SelectorSpec(
            name="node_matcher",
            prefix="exact",
            is_default=True,
            description="fnmatch-style XML node path matcher",
            examples=("config/WindowGeometry", "config/*WindowState"),
        ),
        SelectorSpec(
            name="node_regex",
            prefix="re",
            description="regex matching XML node paths",
            examples=(r"^config/Window", r"/(Geometry|State)$"),
        ),
    )

    def configure_parser(self, parser) -> None:
        parser.add_argument(
            "--compare-file",
            type=Path,
            help="Optional XML file to compare against for semantic no-op byte reuse.",
        )
        parser.add_argument(
            "--sort-attributes",
            action="store_true",
            dest="sort_attributes",
            help="Sort attributes of each element alphabetically.",
        )
        parser.add_argument(
            "--sort-children",
            action="append",
            default=[],
            metavar="NODE_PATH",
            help=(
                "Sort immediate children under matching XML node paths. "
                "Accepts repeated flags and comma-separated values."
            ),
        )

    def build_engine_options(self, parsed_args) -> dict[str, object]:
        return {
            "compare_path": parsed_args.compare_file,
            "sort_attributes": parsed_args.sort_attributes,
            "child_sort_parent_matchers": tuple(parsed_args.sort_children),
            "stdout": parsed_args.stdout,
            "stdin_bytes": parsed_args.stdin_bytes,
        }

    def validate_request(self, request: TransformRequest) -> None:
        super().validate_request(request)
        compile_node_regexes(request.selector_values("node_regex"))
        if (
            not parse_node_matchers(request.selector_values("node_matcher"))
            and not request.selector_values("node_regex")
        ):
            raise ValueError("node matchers must not be empty")

    def transform(self, request: TransformRequest) -> TransformOutput:
        self.validate_request(request)
        return render_xml_output(
            request.base_path,
            node_matchers=parse_node_matchers(request.selector_values("node_matcher")),
            node_regexes=compile_node_regexes(request.selector_values("node_regex")),
            sort_attributes=bool(request.engine_option("sort_attributes", False)),
            overlay_path=request.overlay_path,
            selector_action=request.selector_action,
            compare_path=request.engine_option("compare_path"),
            child_sort_parent_matchers=parse_node_matchers(
                request.engine_option("child_sort_parent_matchers", ())
            ),
            stdin_bytes=request.engine_option("stdin_bytes"),
        )

def main(argv: list[str] | None = None) -> int:
    return run_engine_cli(XmlTransformEngine(), argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
