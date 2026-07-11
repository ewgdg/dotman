from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET

from dotman.transforms import xml as MODULE


def parse_xml(path: Path) -> ET.Element:
    return ET.parse(path).getroot()


def write_semantically_equal_overlay_xml(repo_path: Path, live_path: Path) -> str:
    repo_path.write_text(
        """<config>
  <keep id="1"></keep>
  <tail></tail>
</config>
""",
        encoding="utf-8",
    )
    live_text = """<?xml version="1.0"?>
<config>
 <keep id="1"/>
 <WindowGeometry y="200" x="100"/>
 <tail/>
</config>"""
    live_path.write_text(live_text, encoding="utf-8")
    return live_text


def test_xml_engine_declares_typed_selectors() -> None:
    selector_specs = {spec.name: spec for spec in MODULE.XmlTransformEngine.selector_specs()}

    assert selector_specs["node_matcher"].prefix == "exact"
    assert selector_specs["node_regex"].prefix == "re"


def test_main_accepts_typed_selector_flags(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo.xml"
    live_path = tmp_path / "live.xml"
    output_path = tmp_path / "output.xml"

    repo_path.write_text(
        """<config>
  <keep>repo</keep>
</config>
""",
        encoding="utf-8",
    )
    live_path.write_text(
        """<config>
  <keep>live</keep>
  <WindowGeometry y="200" x="100" />
</config>
""",
        encoding="utf-8",
    )

    exit_code = MODULE.main(
        [
            str(live_path),
            str(output_path),
            "--mode",
            "merge",
            "--overlay-file",
            str(repo_path),
            "--selector-type",
            "retain",
            "--selectors",
            "config/WindowGeometry",
            "--sort-attributes",
        ]
    )

    assert exit_code == 0
    root = parse_xml(output_path)
    assert root.findtext("keep") == "repo"
    assert root.find("WindowGeometry") is not None
    assert root.find("WindowGeometry").attrib == {"x": "100", "y": "200"}


def test_main_accepts_regex_node_selector_flags(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo.xml"
    live_path = tmp_path / "live.xml"
    output_path = tmp_path / "output.xml"

    repo_path.write_text(
        """<config>
  <keep>repo</keep>
</config>
""",
        encoding="utf-8",
    )
    live_path.write_text(
        """<config>
  <keep>live</keep>
  <WindowGeometry y="200" x="100" />
  <WindowState fullscreen="true" />
</config>
""",
        encoding="utf-8",
    )

    exit_code = MODULE.main(
        [
            str(live_path),
            str(output_path),
            "--mode",
            "merge",
            "--overlay-file",
            str(repo_path),
            "--selector-type",
            "retain",
            "--selectors",
            r"re:^config/Window",
        ]
    )

    assert exit_code == 0
    root = parse_xml(output_path)
    assert root.findtext("keep") == "repo"
    assert root.find("WindowGeometry") is not None
    assert root.find("WindowState") is not None


def test_main_accepts_sort_children_flags(tmp_path: Path) -> None:
    input_path = tmp_path / "input.xml"
    output_path = tmp_path / "output.xml"

    input_path.write_text(
        """<config>
  <mutedDictionaries>
    <mutedDictionary>b</mutedDictionary>
    <mutedDictionary>a</mutedDictionary>
  </mutedDictionaries>
</config>
""",
        encoding="utf-8",
    )

    exit_code = MODULE.main(
        [
            str(input_path),
            str(output_path),
            "--mode",
            "cleanup",
            "--sort-children",
            "config/mutedDictionaries",
            "--selector-type",
            "remove",
            "--selectors",
            "config/WindowGeometry",
        ]
    )

    assert exit_code == 0
    root = parse_xml(output_path)
    assert [
        child.text for child in root.findall("mutedDictionaries/mutedDictionary")
    ] == ["a", "b"]


def test_parse_node_matchers_accepts_repeated_and_comma_separated_values() -> None:
    assert MODULE.parse_node_matchers(
        ("config/WindowGeometry,config/WindowState", "config/timeForNewReleaseCheck")
    ) == [
        "config/WindowGeometry",
        "config/WindowState",
        "config/timeForNewReleaseCheck",
    ]


def test_strip_nodes_removes_selected_live_only_xml_paths(tmp_path: Path) -> None:
    input_path = tmp_path / "input.xml"
    output_path = tmp_path / "output.xml"

    input_path.write_text(
        """<config>
  <WindowGeometry x="100" y="200" />
  <WindowState fullscreen="true" />
  <timeForNewReleaseCheck>12345</timeForNewReleaseCheck>
  <keep>repo</keep>
</config>
""",
        encoding="utf-8",
    )

    MODULE.transform_xml(
        str(input_path),
        str(output_path),
        node_matchers=[
            "config/WindowGeometry",
            "config/WindowState",
            "config/timeForNewReleaseCheck",
        ],
        sort_attributes=True,
    )

    root = parse_xml(output_path)

    assert root.find("keep") is not None
    assert root.find("WindowGeometry") is None
    assert root.find("WindowState") is None
    assert root.find("timeForNewReleaseCheck") is None


def test_merge_mode_retain_preserves_selected_live_paths_and_reapplies_repo_tree(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live.xml"
    repo_path = tmp_path / "repo.xml"
    output_path = tmp_path / "output.xml"

    live_path.write_text(
        """<config>
  <keep>live</keep>
  <WindowGeometry x="100" y="200" />
  <WindowState fullscreen="true" />
  <timeForNewReleaseCheck>12345</timeForNewReleaseCheck>
</config>
""",
        encoding="utf-8",
    )
    repo_path.write_text(
        """<config>
  <keep>repo</keep>
</config>
""",
        encoding="utf-8",
    )

    MODULE.transform_xml(
        str(live_path),
        str(output_path),
        overlay_path=str(repo_path),
        node_matchers=[
            "config/WindowGeometry",
            "config/WindowState",
            "config/timeForNewReleaseCheck",
        ],
        selector_action=MODULE.SelectorAction.RETAIN,
    )

    root = parse_xml(output_path)

    assert root.findtext("keep") == "repo"
    assert root.find("WindowGeometry") is not None
    assert root.find("WindowGeometry").attrib == {"x": "100", "y": "200"}
    assert root.find("WindowState") is not None
    assert root.findtext("timeForNewReleaseCheck") == "12345"


def test_retain_node_matchers_in_strip_mode_keeps_only_selected_paths(tmp_path: Path) -> None:
    input_path = tmp_path / "input.xml"
    output_path = tmp_path / "output.xml"

    input_path.write_text(
        """<config>
  <keep>repo</keep>
  <WindowGeometry x="100" y="200" />
  <Section>
    <KeepNested>value</KeepNested>
    <RetainNested>selected</RetainNested>
  </Section>
</config>
""",
        encoding="utf-8",
    )

    MODULE.transform_xml(
        input_path,
        output_path,
        node_matchers=["config/WindowGeometry", "config/Section/RetainNested"],
        selector_action=MODULE.SelectorAction.RETAIN,
    )

    root = parse_xml(output_path)

    assert root.find("keep") is None
    assert root.find("WindowGeometry") is not None
    assert root.find("Section") is not None
    assert root.find("Section/RetainNested") is not None
    assert root.find("Section/KeepNested") is None


def test_merge_mode_remove_preserves_unselected_live_paths_and_reapplies_repo_tree(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live.xml"
    repo_path = tmp_path / "repo.xml"
    output_path = tmp_path / "output.xml"

    live_path.write_text(
        """<config>
  <KeepLocal>noise</KeepLocal>
  <WindowGeometry x="100" y="200" />
  <WindowState fullscreen="true" />
</config>
""",
        encoding="utf-8",
    )
    repo_path.write_text(
        """<config>
  <WindowGeometry x="10" y="10" />
</config>
""",
        encoding="utf-8",
    )

    MODULE.transform_xml(
        live_path,
        output_path,
        overlay_path=repo_path,
        node_matchers=["config/WindowGeometry"],
        selector_action=MODULE.SelectorAction.REMOVE,
    )

    root = parse_xml(output_path)

    assert root.findtext("KeepLocal") == "noise"
    assert root.find("WindowGeometry").attrib == {"x": "10", "y": "10"}
    assert root.find("WindowState") is not None


def test_merge_mode_remove_reflects_nested_deletions_from_repo(tmp_path: Path) -> None:
    live_path = tmp_path / "live.xml"
    repo_path = tmp_path / "repo.xml"
    output_path = tmp_path / "output.xml"

    live_path.write_text(
        """<config>
  <KeepLocal>noise</KeepLocal>
  <Managed>
    <NestedValue>repo</NestedValue>
    <DeleteMe>stale</DeleteMe>
  </Managed>
</config>
""",
        encoding="utf-8",
    )
    repo_path.write_text(
        """<config>
  <Managed>
    <NestedValue>repo</NestedValue>
  </Managed>
</config>
""",
        encoding="utf-8",
    )

    MODULE.transform_xml(
        live_path,
        output_path,
        overlay_path=repo_path,
        node_matchers=["config/Managed"],
        selector_action=MODULE.SelectorAction.REMOVE,
    )

    root = parse_xml(output_path)

    assert root.findtext("KeepLocal") == "noise"
    assert root.findtext("Managed/NestedValue") == "repo"
    assert root.find("Managed/DeleteMe") is None


def test_merge_with_compare_file_preserves_live_order_and_bytes_when_semantically_equal(
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "repo.xml"
    live_path = tmp_path / "live.xml"
    output_path = tmp_path / "output.xml"

    live_text = write_semantically_equal_overlay_xml(repo_path, live_path)

    MODULE.transform_xml(
        str(live_path),
        str(output_path),
        overlay_path=str(repo_path),
        node_matchers=["config/WindowGeometry"],
        compare_path=str(live_path),
    )

    assert output_path.read_text(encoding="utf-8") == live_text


def test_merge_without_compare_file_reserializes_semantically_equal_output(
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "repo.xml"
    live_path = tmp_path / "live.xml"
    output_path = tmp_path / "output.xml"

    live_text = write_semantically_equal_overlay_xml(repo_path, live_path)

    MODULE.transform_xml(
        str(live_path),
        str(output_path),
        overlay_path=str(repo_path),
        node_matchers=["config/WindowGeometry"],
    )

    assert output_path.read_text(encoding="utf-8") != live_text
    root = parse_xml(output_path)
    assert root.find("keep") is not None
    assert root.find("keep").attrib == {"id": "1"}
    assert root.find("WindowGeometry") is not None
    assert root.find("WindowGeometry").attrib == {"x": "100", "y": "200"}


def test_sort_children_canonicalizes_only_selected_parent_paths(tmp_path: Path) -> None:
    input_path = tmp_path / "input.xml"
    output_path = tmp_path / "output.xml"

    input_path.write_text(
        """<config>
  <mutedDictionaries>
    <mutedDictionary>b</mutedDictionary>
    <mutedDictionary>a</mutedDictionary>
    <mutedDictionary>c</mutedDictionary>
  </mutedDictionaries>
  <programs>
    <program name="b" />
    <program name="a" />
  </programs>
</config>
""",
        encoding="utf-8",
    )

    MODULE.transform_xml(
        input_path,
        output_path,
        node_matchers=["config/WindowGeometry"],
        selector_action=MODULE.SelectorAction.REMOVE,
        child_sort_parent_matchers=["config/mutedDictionaries"],
    )

    root = parse_xml(output_path)

    assert [
        child.text for child in root.findall("mutedDictionaries/mutedDictionary")
    ] == ["a", "b", "c"]
    assert [
        child.attrib["name"] for child in root.findall("programs/program")
    ] == ["b", "a"]


def test_cleanup_with_compare_file_treats_selected_child_lists_as_semantically_equal(
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "repo.xml"
    live_path = tmp_path / "live.xml"
    output_path = tmp_path / "output.xml"

    repo_text = """<?xml version="1.0"?>
<config>
  <mutedDictionaries>
    <mutedDictionary>a</mutedDictionary>
    <mutedDictionary>b</mutedDictionary>
  </mutedDictionaries>
</config>"""
    repo_path.write_text(repo_text, encoding="utf-8")
    live_path.write_text(
        """<?xml version="1.0"?>
<config>
  <mutedDictionaries>
    <mutedDictionary>b</mutedDictionary>
    <mutedDictionary>a</mutedDictionary>
  </mutedDictionaries>
</config>""",
        encoding="utf-8",
    )

    MODULE.transform_xml(
        live_path,
        output_path,
        node_matchers=["config/WindowGeometry"],
        compare_path=repo_path,
        child_sort_parent_matchers=["config/mutedDictionaries"],
    )

    assert output_path.read_text(encoding="utf-8") == repo_text
