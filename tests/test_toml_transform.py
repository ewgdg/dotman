from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib

from tomlkit.toml_document import TOMLDocument

from dotman.transforms import toml as MODULE
from dotman.transforms.framework import SelectorAction, emit_transform_output

REPO_ROOT = Path(__file__).resolve().parents[1]


# File-level drivers: compose the engine's output builders with the shared
# emitter so tests can pass parsed selectors without going through argv.
def write_document_if_changed(
    path: Path | None,
    doc: TOMLDocument,
    mode_reference_path: Path,
    compare_path: Path | None = None,
    stdout: bool = False,
) -> None:
    emit_transform_output(
        path,
        MODULE.build_document_output(
            doc, mode_reference_path=mode_reference_path, line_ending="\n", compare_path=compare_path
        ),
        stdout=stdout,
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
        MODULE.build_stripped_document_output(
            base_path, stripped_key_paths, stripped_table_regexes, compare_path=compare_path
        ),
        stdout=stdout,
    )


def merge_with_selector_action(
    selector_action: SelectorAction,
    base_path: Path,
    output_path: Path | None,
    overlay_path: Path,
    key_paths,
    table_regexes: list[re.Pattern[str]],
    compare_path: Path | None = None,
    stdout: bool = False,
) -> None:
    emit_transform_output(
        output_path,
        MODULE.build_merged_document_output(
            base_path, overlay_path, selector_action, list(key_paths), table_regexes, compare_path=compare_path
        ),
        stdout=stdout,
    )


def merge_keys(*args, **kwargs) -> None:
    merge_with_selector_action(SelectorAction.RETAIN, *args, **kwargs)


def merge_keys_except_stripped(*args, **kwargs) -> None:
    merge_with_selector_action(SelectorAction.REMOVE, *args, **kwargs)


def test_toml_engine_declares_typed_selectors() -> None:
    selector_specs = {spec.name: spec for spec in MODULE.TomlTransformEngine.selector_specs()}

    assert selector_specs["key"].prefix == "exact"
    assert selector_specs["table_regex"].prefix == "re"


def test_main_accepts_typed_selector_flags(tmp_path: Path) -> None:
    live_path = tmp_path / "live.toml"
    repo_path = tmp_path / "repo.toml"
    output_path = tmp_path / "output.toml"

    live_path.write_text(
        """approval_policy = "on-request"
model = "gpt-5.4"

[mcp_servers.playwright.env]
PLAYWRIGHT_MCP_EXTENSION_TOKEN = "secret"
""",
        encoding="utf-8",
    )
    repo_path.write_text(
        """approval_policy = "on-request"

[mcp_servers.context7]
command = "npx"
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
            "model",
            "re:^mcp_servers\\.playwright\\.env$",
        ]
    )

    assert exit_code == 0
    merged_doc = MODULE.load_document(output_path)
    assert merged_doc["model"] == "gpt-5.4"
    assert (
        merged_doc["mcp_servers"]["playwright"]["env"]["PLAYWRIGHT_MCP_EXTENSION_TOKEN"]
        == "secret"
    )


def test_cleanup_does_not_reuse_compare_file_with_stale_comments(tmp_path: Path) -> None:
    live_path = tmp_path / "live.toml"
    repo_path = tmp_path / "repo.toml"
    output_path = tmp_path / "output.toml"

    live_path.write_text(
        'model = "local-only"\n\n[features]\nhooks = true\n',
        encoding="utf-8",
    )
    repo_path.write_text(
        '# model = "old-local-value"\n\n[features]\nhooks = true\n',
        encoding="utf-8",
    )

    exit_code = MODULE.main(
        [
            str(live_path),
            str(output_path),
            "--mode",
            "cleanup",
            "--compare-file",
            str(repo_path),
            "--selector-type",
            "remove",
            "--selectors",
            "model",
        ]
    )

    assert exit_code == 0
    assert '# model = "old-local-value"' not in output_path.read_text(encoding="utf-8")
    assert MODULE.load_document(output_path).unwrap() == {"features": {"hooks": True}}


def test_cleanup_does_not_reuse_stale_array_of_table_header_comment(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live.toml"
    repo_path = tmp_path / "repo.toml"
    output_path = tmp_path / "output.toml"

    live_path.write_text(
        '[[services]] # current\nname = "api"\n',
        encoding="utf-8",
    )
    repo_path.write_text(
        '[[services]] # stale\nname = "api"\n',
        encoding="utf-8",
    )

    exit_code = MODULE.main(
        [
            str(live_path),
            str(output_path),
            "--mode",
            "cleanup",
            "--compare-file",
            str(repo_path),
            "--selectors",
            "services",
        ]
    )

    assert exit_code == 0
    assert "# current" in output_path.read_text(encoding="utf-8")
    assert "# stale" not in output_path.read_text(encoding="utf-8")


def test_cleanup_does_not_reuse_comment_attached_to_different_key(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live.toml"
    repo_path = tmp_path / "repo.toml"
    output_path = tmp_path / "output.toml"
    live_text = "a = 1\nb = 2 # note\n"

    live_path.write_text(live_text, encoding="utf-8")
    repo_path.write_text("a = 1 # note\nb = 2\n", encoding="utf-8")

    exit_code = MODULE.main(
        [
            str(live_path),
            str(output_path),
            "--mode",
            "cleanup",
            "--compare-file",
            str(repo_path),
            "--selectors",
            "a",
            "b",
        ]
    )

    assert exit_code == 0
    assert output_path.read_text(encoding="utf-8") == live_text


def test_cleanup_does_not_reuse_stale_multiline_array_comment(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live.toml"
    repo_path = tmp_path / "repo.toml"
    output_path = tmp_path / "output.toml"
    live_text = "values = [\n  1, # current\n  2,\n]\n"

    live_path.write_text(live_text, encoding="utf-8")
    repo_path.write_text(
        "values = [\n  1, # stale\n  2,\n]\n",
        encoding="utf-8",
    )

    exit_code = MODULE.main(
        [
            str(live_path),
            str(output_path),
            "--mode",
            "cleanup",
            "--compare-file",
            str(repo_path),
            "--selectors",
            "values",
        ]
    )

    assert exit_code == 0
    assert output_path.read_text(encoding="utf-8") == live_text


def test_cleanup_compare_treats_hash_inside_string_as_value_content(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live.toml"
    compare_path = tmp_path / "compare.toml"
    output_path = tmp_path / "output.toml"
    compare_text = "fragment = 'section#details'\n"

    live_path.write_text('fragment = "section#details"\n', encoding="utf-8")
    compare_path.write_text(compare_text, encoding="utf-8")

    exit_code = MODULE.main(
        [
            str(live_path),
            str(output_path),
            "--mode",
            "cleanup",
            "--compare-file",
            str(compare_path),
            "--selectors",
            "fragment",
        ]
    )

    assert exit_code == 0
    assert output_path.read_text(encoding="utf-8") == compare_text


def test_merge_does_not_reuse_stale_live_comment(tmp_path: Path) -> None:
    live_path = tmp_path / "live.toml"
    repo_path = tmp_path / "repo.toml"
    output_path = tmp_path / "output.toml"

    live_path.write_text(
        'model = "local-only"\n\n# stale\nmanaged = true\n',
        encoding="utf-8",
    )
    repo_path.write_text("# current\nmanaged = true\n", encoding="utf-8")

    exit_code = MODULE.main(
        [
            str(live_path),
            str(output_path),
            "--mode",
            "merge",
            "--overlay-file",
            str(repo_path),
            "--compare-file",
            str(live_path),
            "--selectors",
            "model",
        ]
    )

    assert exit_code == 0
    output = output_path.read_text(encoding="utf-8")
    assert "# current" in output
    assert "# stale" not in output


def test_merge_does_not_drop_leading_comment_from_overlay_table(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live.toml"
    repo_path = tmp_path / "repo.toml"
    output_path = tmp_path / "output.toml"

    live_path.write_text(
        'local = "yes"\n\n[managed]\nenabled = true\n',
        encoding="utf-8",
    )
    repo_path.write_text(
        "# current\n[managed]\nenabled = true\n",
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
            "--compare-file",
            str(live_path),
            "--selectors",
            "local",
        ]
    )

    assert exit_code == 0
    assert "# current\n[managed]" in output_path.read_text(encoding="utf-8")


def test_parse_key_paths_and_table_regexes() -> None:
    key_paths = MODULE.parse_key_paths(["model", "model_reasoning_effort"])
    table_regexes = MODULE.compile_table_regexes(
        ["^projects\\.", "^mcp_servers\\.playwright\\.env$"]
    )

    assert key_paths == [("model",), ("model_reasoning_effort",)]
    assert [pattern.pattern for pattern in table_regexes] == [
        "^projects\\.",
        "^mcp_servers\\.playwright\\.env$",
    ]


def test_retain_matchers_in_strip_mode_keeps_only_selected_content(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo.toml"
    output_path = tmp_path / "output.toml"

    repo_path.write_text(
        """approval_policy = "on-request"
model = "gpt-5.4"

[mcp_servers.context7]
command = "npx"

[projects."/tmp/example"]
trust_level = "trusted"
""",
        encoding="utf-8",
    )

    retained_doc = MODULE.build_document_with_retained_matchers(
        MODULE.load_document(repo_path),
        {("model",)},
        [MODULE.re.compile(r"^projects\.")],
    )
    write_document_if_changed(output_path, retained_doc, mode_reference_path=repo_path)

    output = output_path.read_text(encoding="utf-8")
    assert 'model = "gpt-5.4"' in output
    assert "[projects" in output
    assert "approval_policy" not in output
    assert "mcp_servers" not in output


def test_regex_removes_matching_nested_keys_without_removing_siblings(tmp_path: Path) -> None:
    source_path = tmp_path / "source.toml"
    output_path = tmp_path / "output.toml"

    source_path.write_text(
        """[widget.media]
enabled = true
volume = 75

[widget.clock]
enabled = false
format = "HH:mm"
""",
        encoding="utf-8",
    )

    stripped_doc = MODULE.build_document_with_stripped_matchers(
        MODULE.load_document(source_path),
        [],
        [MODULE.re.compile(r"^widget\.[^.]+\.enabled$")],
    )
    write_document_if_changed(
        output_path,
        stripped_doc,
        mode_reference_path=source_path,
    )

    output_doc = MODULE.load_document(output_path)
    assert "enabled" not in output_doc["widget"]["media"]
    assert output_doc["widget"]["media"]["volume"] == 75
    assert "enabled" not in output_doc["widget"]["clock"]
    assert output_doc["widget"]["clock"]["format"] == "HH:mm"


def test_regex_retain_keeps_matching_nested_keys_only(tmp_path: Path) -> None:
    source_path = tmp_path / "source.toml"

    source_path.write_text(
        """[widget.media]
enabled = true
volume = 75

[widget.clock]
enabled = false
format = "HH:mm"
""",
        encoding="utf-8",
    )

    retained_doc = MODULE.build_document_with_retained_matchers(
        MODULE.load_document(source_path),
        [],
        [MODULE.re.compile(r"^widget\.[^.]+\.enabled$")],
    )

    assert retained_doc.unwrap() == {
        "widget": {
            "media": {"enabled": True},
            "clock": {"enabled": False},
        }
    }


def test_strip_table_preserves_following_commented_tables(tmp_path: Path) -> None:
    live_path = tmp_path / "live.toml"
    output_path = tmp_path / "output.toml"

    live_path.write_text(
        """[mcp_servers.context7]
command = "npx"

[mcp_servers.node_repl]
command = "node_repl"

[mcp_servers.node_repl.env]
CODEX_HOME = "/tmp/codex"

# [mcp_servers.chrome-devtools]
# command = "npx"
# args = ["chrome-devtools-mcp@latest"]

[features]
hooks = true
""",
        encoding="utf-8",
    )

    strip_keys(
        live_path,
        output_path,
        [("mcp_servers", "node_repl")],
        [],
    )

    output = output_path.read_text(encoding="utf-8")
    assert "[mcp_servers]\n" not in output
    assert "[mcp_servers.node_repl]" not in output
    assert "CODEX_HOME" not in output
    assert "# [mcp_servers.chrome-devtools]" in output
    assert '# args = ["chrome-devtools-mcp@latest"]' in output
    assert "[features]" in output


def test_strip_parent_table_preserves_blank_separated_tail_comments(tmp_path: Path) -> None:
    live_path = tmp_path / "live.toml"
    output_path = tmp_path / "output.toml"

    live_path.write_text(
        """[mcp_servers.node_repl]
command = "node_repl"

[mcp_servers.node_repl.env]
CODEX_HOME = "/tmp/codex"

# Keep this note even when the parent table is removed.

[features]
hooks = true
""",
        encoding="utf-8",
    )

    strip_keys(
        live_path,
        output_path,
        [("mcp_servers",)],
        [],
    )

    output = output_path.read_text(encoding="utf-8")
    assert "[mcp_servers" not in output
    assert "# Keep this note even when the parent table is removed." in output
    assert "[features]" in output


def test_strip_table_removes_attached_tail_comments_without_blank_separator(tmp_path: Path) -> None:
    live_path = tmp_path / "live.toml"
    output_path = tmp_path / "output.toml"

    live_path.write_text(
        """[mcp_servers.node_repl]
command = "node_repl"
# Attached to node_repl.

[features]
hooks = true
""",
        encoding="utf-8",
    )

    strip_keys(
        live_path,
        output_path,
        [("mcp_servers", "node_repl")],
        [],
    )

    output = output_path.read_text(encoding="utf-8")
    assert "Attached to node_repl" not in output
    assert "[features]" in output


def test_write_document_with_compare_file_skips_rewrite_for_matching_output(
    tmp_path: Path,
 ) -> None:
    repo_path = tmp_path / "repo.toml"
    output_path = tmp_path / "output.toml"

    repo_path.write_text('model = "gpt-5.4"\n', encoding="utf-8")
    retained_doc = MODULE.load_document(repo_path)
    output_path.write_text(retained_doc.as_string(), encoding="utf-8")
    os.utime(output_path, ns=(1, 1))

    write_document_if_changed(
        output_path,
        retained_doc,
        mode_reference_path=repo_path,
        compare_path=output_path,
    )

    assert output_path.stat().st_mtime_ns == 1


def test_write_document_with_compare_file_skips_rewrite_for_semantic_match(
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "repo.toml"
    compare_path = tmp_path / "compare.toml"
    output_path = tmp_path / "output.toml"

    repo_path.write_text('model_provider = "openai_http"\n', encoding="utf-8")
    compare_path.write_text(
        'model = "gpt-5.4"\nmodel_provider = "openai_http"\n',
        encoding="utf-8",
    )
    output_path.write_text("stale\n", encoding="utf-8")
    os.utime(output_path, ns=(1, 1))

    merged_doc = MODULE.load_document(repo_path)
    merged_doc["model"] = "gpt-5.4"

    write_document_if_changed(
        output_path,
        merged_doc,
        mode_reference_path=repo_path,
        compare_path=compare_path,
    )

    assert output_path.stat().st_mtime_ns != 1
    assert output_path.read_text(encoding="utf-8") == compare_path.read_text(encoding="utf-8")


def test_write_document_with_compare_file_reuses_existing_text_in_stdout_mode(
    tmp_path: Path,
    capsys,
) -> None:
    repo_path = tmp_path / "repo.toml"
    compare_path = tmp_path / "compare.toml"

    repo_path.write_text(
        '# keep me\nmodel_provider = "openai_http"\n',
        encoding="utf-8",
    )
    compare_path.write_text(
        'model = "gpt-5.4"\n# keep me\nmodel_provider = "openai_http"\n',
        encoding="utf-8",
    )

    merged_doc = MODULE.load_document(repo_path)
    merged_doc["model"] = "gpt-5.4"

    write_document_if_changed(
        None,
        merged_doc,
        mode_reference_path=repo_path,
        compare_path=compare_path,
        stdout=True,
    )

    assert capsys.readouterr().out == compare_path.read_text(encoding="utf-8")


def test_write_document_without_compare_file_rewrites_matching_output(
    tmp_path: Path,
 ) -> None:
    repo_path = tmp_path / "repo.toml"
    output_path = tmp_path / "output.toml"

    repo_path.write_text('model = "gpt-5.4"\n', encoding="utf-8")
    retained_doc = MODULE.load_document(repo_path)
    output_path.write_text(retained_doc.as_string(), encoding="utf-8")
    os.utime(output_path, ns=(1, 1))

    write_document_if_changed(
        output_path,
        retained_doc,
        mode_reference_path=repo_path,
    )

    assert output_path.stat().st_mtime_ns != 1


def test_merge_preserves_selected_live_keys_and_reapplies_repo_content(tmp_path: Path) -> None:
    live_path = tmp_path / "live.toml"
    repo_path = tmp_path / "repo.toml"
    output_path = tmp_path / "output.toml"

    live_path.write_text(
        """approval_policy = "on-request"
model = "gpt-5.4"

[mcp_servers.context7]
command = "npx"

[mcp_servers.playwright]
command = "npx"

[mcp_servers.playwright.env]
PLAYWRIGHT_MCP_EXTENSION_TOKEN = "secret"

[projects."/tmp/example"]
trust_level = "trusted"
""",
        encoding="utf-8",
    )
    repo_path.write_text(
        """approval_policy = "on-request"
web_search = "repo"

[mcp_servers.context7]
command = "npx"
""",
        encoding="utf-8",
    )

    merge_keys(
        live_path,
        output_path,
        repo_path,
        {
            ("model",),
            ("mcp_servers", "playwright", "env", "PLAYWRIGHT_MCP_EXTENSION_TOKEN"),
        },
        [],
    )

    merged_doc = MODULE.load_document(output_path)

    assert merged_doc["model"] == "gpt-5.4"
    assert merged_doc["web_search"] == "repo"
    assert "projects" not in merged_doc
    assert "playwright" in merged_doc["mcp_servers"]
    assert "command" not in merged_doc["mcp_servers"]["playwright"]
    assert (
        merged_doc["mcp_servers"]["playwright"]["env"]["PLAYWRIGHT_MCP_EXTENSION_TOKEN"]
        == "secret"
    )


def test_merge_does_not_restore_omitted_live_table_when_overlay_keeps_a_comment(tmp_path: Path) -> None:
    live_path = tmp_path / "live.toml"
    repo_path = tmp_path / "repo.toml"
    output_path = tmp_path / "output.toml"

    live_path.write_text(
        """[mcp_servers.context7]
command = "npx"

[mcp_servers.node_repl]
command = "node_repl"
""",
        encoding="utf-8",
    )
    repo_path.write_text(
        """# [mcp_servers.context7]
# command = "npx"

[features]
hooks = true
""",
        encoding="utf-8",
    )

    merge_keys(
        live_path,
        output_path,
        repo_path,
        {("mcp_servers", "node_repl")},
        [],
    )

    merged_doc = MODULE.load_document(output_path)

    assert "context7" not in merged_doc["mcp_servers"]
    assert merged_doc["mcp_servers"]["node_repl"]["command"] == "node_repl"
    assert "# [mcp_servers.context7]" in output_path.read_text(encoding="utf-8")


def test_merge_restores_regex_selected_tables(tmp_path: Path) -> None:
    live_path = tmp_path / "live.toml"
    repo_path = tmp_path / "repo.toml"
    output_path = tmp_path / "output.toml"

    live_path.write_text(
        """approval_policy = "on-request"

[mcp_servers.context7]
command = "npx"

[mcp_servers.playwright]
command = "npx"

[mcp_servers.playwright.env]
PLAYWRIGHT_MCP_EXTENSION_TOKEN = "secret"
""",
        encoding="utf-8",
    )
    repo_path.write_text(
        """approval_policy = "on-request"

[mcp_servers.context7]
command = "npx"
""",
        encoding="utf-8",
    )

    merge_keys(
        live_path,
        output_path,
        repo_path,
        set(),
        [MODULE.re.compile(r"^mcp_servers\.playwright\.env$")],
    )

    merged_doc = MODULE.load_document(output_path)

    assert "context7" in merged_doc["mcp_servers"]
    assert "playwright" in merged_doc["mcp_servers"]
    assert "command" not in merged_doc["mcp_servers"]["playwright"]
    assert (
        merged_doc["mcp_servers"]["playwright"]["env"]["PLAYWRIGHT_MCP_EXTENSION_TOKEN"]
        == "secret"
    )


def test_merge_with_compare_file_reuses_semantically_matching_live_bytes(tmp_path: Path) -> None:
    live_path = tmp_path / "live.toml"
    repo_path = tmp_path / "repo.toml"
    output_path = tmp_path / "output.toml"

    live_path.write_text(
        """approval_policy = "on-request"
sandbox_mode = "workspace-write"
web_search = "live"
personality = "pragmatic"
model = "gpt-5.4"
model_reasoning_effort = "high"

model_provider = "openai_http"

[model_providers.openai_http]
name = "OpenAI HTTP only"

[projects."/tmp/example"]
trust_level = "trusted"
""",
        encoding="utf-8",
    )
    repo_path.write_text(
        """approval_policy = "on-request"
sandbox_mode = "workspace-write"
web_search = "live"
personality = "pragmatic"

model_provider = "openai_http"

[model_providers.openai_http]
name = "OpenAI HTTP only"
""",
        encoding="utf-8",
    )

    merge_keys(
        live_path,
        output_path,
        repo_path,
        {
            ("model",),
            ("model_reasoning_effort",),
        },
        [MODULE.re.compile(r"^projects\.")],
        compare_path=live_path,
    )

    assert output_path.read_text(encoding="utf-8") == live_path.read_text(encoding="utf-8")


def test_merge_preserves_top_level_leading_comments_without_compare_file(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live.toml"
    repo_path = tmp_path / "repo.toml"
    output_path = tmp_path / "output.toml"

    live_text = """approval_policy = "on-request"
sandbox_mode = "workspace-write"
web_search = "live"
personality = "pragmatic"
model = "gpt-5.4"
model_reasoning_effort = "high"

# model_provider = "openai_http"

[model_providers.openai_http]
name = "OpenAI HTTP only"

[projects."/tmp/example"]
trust_level = "trusted"
"""
    repo_path.write_text(
        """approval_policy = "on-request"
sandbox_mode = "workspace-write"
web_search = "live"
personality = "pragmatic"

# model_provider = "openai_http"

[model_providers.openai_http]
name = "OpenAI HTTP only"
""",
        encoding="utf-8",
    )
    live_path.write_text(live_text, encoding="utf-8")

    merge_keys(
        live_path,
        output_path,
        repo_path,
        {
            ("model",),
            ("model_reasoning_effort",),
        },
        [MODULE.re.compile(r"^projects\.")],
    )

    assert output_path.read_text(encoding="utf-8") == live_text


def test_merge_keeps_independent_comment_at_overlay_position_when_live_key_inserted(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live.toml"
    repo_path = tmp_path / "repo.toml"
    output_path = tmp_path / "output.toml"

    live_path.write_text(
        """approval_policy = "on-request"

# model_provider = "openai_http"

notify = ["turn-ended"]

[model_providers.openai_http]
name = "OpenAI HTTP only"
""",
        encoding="utf-8",
    )
    repo_path.write_text(
        """approval_policy = "on-request"

# model_provider = "openai_http"

[model_providers.openai_http]
name = "OpenAI HTTP only"
""",
        encoding="utf-8",
    )

    merge_keys(
        live_path,
        output_path,
        repo_path,
        {("notify",)},
        [],
    )

    assert output_path.read_text(encoding="utf-8") == """approval_policy = "on-request"
notify = ["turn-ended"]

# model_provider = "openai_http"

[model_providers.openai_http]
name = "OpenAI HTTP only"
"""


def test_merge_does_not_add_blank_after_attached_table_leading_comment(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live.toml"
    repo_path = tmp_path / "repo.toml"
    output_path = tmp_path / "output.toml"

    live_path.write_text(
        """model = "live"

[profiles.obsidian]
model = "gpt-5.4-mini"

# Extra settings that only apply when `sandbox = "workspace-write"`.
[sandbox_workspace_write]
network_access = true
model = "live"
""",
        encoding="utf-8",
    )
    repo_path.write_text(
        """[profiles.obsidian]
model = "gpt-5.4-mini"

# Extra settings that only apply when `sandbox = "workspace-write"`.
[sandbox_workspace_write]
network_access = true
""",
        encoding="utf-8",
    )

    merge_keys(
        live_path,
        output_path,
        repo_path,
        {("model",)},
        [],
    )

    assert output_path.read_text(encoding="utf-8") == """model = "live"

[profiles.obsidian]
model = "gpt-5.4-mini"

# Extra settings that only apply when `sandbox = "workspace-write"`.
[sandbox_workspace_write]
network_access = true
"""


def test_merge_dedupes_independent_comments_with_different_blank_padding(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live.toml"
    repo_path = tmp_path / "repo.toml"
    output_path = tmp_path / "output.toml"

    live_path.write_text(
        """[mcp_servers.context7]
command = "npx"

[mcp_servers.node_repl]
command = "node_repl"

[mcp_servers.node_repl.env]
CODEX_HOME = "/tmp/codex"

# [mcp_servers.chrome-devtools]
# command = "npx"


[notice]
hide_full_access_warning = true
""",
        encoding="utf-8",
    )
    repo_path.write_text(
        """[mcp_servers.context7]
command = "npx"

# [mcp_servers.chrome-devtools]
# command = "npx"

[features]
hooks = true
""",
        encoding="utf-8",
    )

    merge_keys(
        live_path,
        output_path,
        repo_path,
        {
            ("mcp_servers", "node_repl"),
            ("notice",),
        },
        [],
    )

    output = output_path.read_text(encoding="utf-8")
    assert output.count("# [mcp_servers.chrome-devtools]") == 1
    assert output.index("# [mcp_servers.chrome-devtools]") < output.index("[features]")
    assert output.index("[notice]") < output.index("# [mcp_servers.chrome-devtools]")


def test_merge_treats_blank_lines_as_single_section_separators(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live.toml"
    repo_path = tmp_path / "repo.toml"
    output_path = tmp_path / "output.toml"

    live_path.write_text(
        """[tui]
status_line = ["current-dir"]

[tui.model_availability_nux]
"gpt-5.5" = 4

[projects."/tmp/example"]
trust_level = "trusted"
""",
        encoding="utf-8",
    )
    repo_path.write_text(
        """[tui]
status_line = ["current-dir"]

[plugins."browser@openai-bundled"]
enabled = true
""",
        encoding="utf-8",
    )

    merge_keys(
        live_path,
        output_path,
        repo_path,
        set(),
        [
            MODULE.re.compile(r"^projects\."),
            MODULE.re.compile(r"^tui\.model_availability_nux$"),
        ],
    )

    assert output_path.read_text(encoding="utf-8") == """[tui]
status_line = ["current-dir"]

[tui.model_availability_nux]
"gpt-5.5" = 4

[projects."/tmp/example"]
trust_level = "trusted"

[plugins."browser@openai-bundled"]
enabled = true
"""


def test_merge_skips_missing_preserved_paths(tmp_path: Path) -> None:
    live_path = tmp_path / "live.toml"
    repo_path = tmp_path / "repo.toml"
    output_path = tmp_path / "output.toml"

    live_path.write_text(
        """approval_policy = "on-request"
""",
        encoding="utf-8",
    )
    repo_path.write_text(
        """approval_policy = "on-request"
""",
        encoding="utf-8",
    )

    merge_keys(
        live_path,
        output_path,
        repo_path,
        {("mcp_servers", "playwright", "env", "PLAYWRIGHT_MCP_EXTENSION_TOKEN")},
        [],
    )

    merged_doc = MODULE.load_document(output_path)

    assert merged_doc["approval_policy"] == "on-request"
    assert "mcp_servers" not in merged_doc


def test_merge_remove_preserves_unselected_live_keys_and_reapplies_repo_content(
    tmp_path: Path,
) -> None:
    live_path = tmp_path / "live.toml"
    repo_path = tmp_path / "repo.toml"
    output_path = tmp_path / "output.toml"

    live_path.write_text(
        """approval_policy = "live"
keep_local = "noise"
model = "live-model"

[mcp_servers.context7]
command = "live-context7"

[mcp_servers.playwright]
command = "live-playwright"

[projects."/tmp/example"]
trust_level = "trusted"
""",
        encoding="utf-8",
    )
    repo_path.write_text(
        """approval_policy = "on-request"
model = "repo-model"

[mcp_servers.context7]
command = "repo-context7"

[projects."/tmp/example"]
trust_level = "repo"
""",
        encoding="utf-8",
    )

    merge_keys_except_stripped(
        live_path,
        output_path,
        repo_path,
        [("model",)],
        [MODULE.re.compile(r"^projects\.")],
    )

    merged_doc = MODULE.load_document(output_path)

    assert merged_doc["approval_policy"] == "on-request"
    assert merged_doc["keep_local"] == "noise"
    assert merged_doc["model"] == "repo-model"
    assert merged_doc["mcp_servers"]["context7"]["command"] == "repo-context7"
    assert merged_doc["mcp_servers"]["playwright"]["command"] == "live-playwright"
    assert merged_doc["projects"]["/tmp/example"]["trust_level"] == "repo"


def test_merge_preserves_overlay_key_order_across_hash_seeds(tmp_path: Path) -> None:
    live_path = tmp_path / "live.toml"
    repo_path = tmp_path / "repo.toml"

    live_path.write_text(
        """approval_policy = "on-request"
model_reasoning_effort = "high"
model = "gpt-5.4"
""",
        encoding="utf-8",
    )
    repo_path.write_text(
        'approval_policy = "on-request"\n',
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from pathlib import Path

from dotman.transforms import toml as module
from dotman.transforms.framework import SelectorAction, emit_transform_output

repo_path = Path(sys.argv[1])
live_path = Path(sys.argv[1])
repo_path = Path(sys.argv[2])
output_path = Path(sys.argv[3])

emit_transform_output(
    output_path,
    module.build_merged_document_output(
        live_path,
        repo_path,
        SelectorAction.RETAIN,
        list({("model",), ("model_reasoning_effort",)}),
        [],
    ),
)
print(output_path.read_text(encoding="utf-8"))
""",
            str(live_path),
            str(repo_path),
            str(tmp_path / "output.toml"),
        ],
        capture_output=True,
        check=True,
        cwd=REPO_ROOT,
        text=True,
        env={**os.environ, "PYTHONHASHSEED": "1"},
    )

    output = completed.stdout
    assert output.index('model_reasoning_effort = "high"') < output.index('model = "gpt-5.4"')


def run_toml_transform(
    tmp_path: Path,
    base_text: str,
    *selector_args: str,
    overlay_text: str | None = None,
    compare_text: str | None = None,
) -> str:
    """Run the engine CLI and return the output text without newline translation."""
    base_path = tmp_path / "base.toml"
    output_path = tmp_path / "output.toml"
    base_path.write_bytes(base_text.encode("utf-8"))
    argv = [str(base_path), str(output_path)]
    if overlay_text is None:
        argv += ["--mode", "cleanup"]
    else:
        overlay_path = tmp_path / "overlay.toml"
        overlay_path.write_bytes(overlay_text.encode("utf-8"))
        argv += ["--mode", "merge", "--overlay-file", str(overlay_path)]
    if compare_text is not None:
        compare_path = tmp_path / "compare.toml"
        compare_path.write_bytes(compare_text.encode("utf-8"))
        argv += ["--compare-file", str(compare_path)]

    assert MODULE.main([*argv, *selector_args]) == 0
    return output_path.read_bytes().decode("utf-8")


def test_blank_line_collapsing_keeps_multiline_string_content(tmp_path: Path) -> None:
    base_text = 'a = """\nline1\n\n\n\nline5"""\nb = 2\n'
    literal_base_text = "a = '''\nline1\n\n\n\nline5'''\nb = 2\n"

    removed = run_toml_transform(tmp_path, base_text, "--selector-type", "remove", "--selectors", "b")
    literal_removed = run_toml_transform(
        tmp_path, literal_base_text, "--selector-type", "remove", "--selectors", "b"
    )
    merged = run_toml_transform(tmp_path, base_text, "--selectors", "a", overlay_text="c = 1\n")

    assert tomllib.loads(removed) == {"a": "line1\n\n\n\nline5"}
    assert tomllib.loads(literal_removed) == {"a": "line1\n\n\n\nline5"}
    assert tomllib.loads(merged) == {"a": "line1\n\n\n\nline5", "c": 1}


def test_blank_line_collapsing_still_merges_separator_runs(tmp_path: Path) -> None:
    output = run_toml_transform(
        tmp_path,
        "a = 1\n\nb = 2\n\nc = 3\n",
        "--selector-type",
        "remove",
        "--selectors",
        "b",
    )

    assert output == "a = 1\n\nc = 3\n"


def test_crlf_base_keeps_crlf_line_endings(tmp_path: Path) -> None:
    base_text = "a = 1\r\nb = 2\r\n\r\n[t]\r\nx = 1\r\n\r\n[u]\r\ny = 2\r\n"

    retained = run_toml_transform(tmp_path, base_text, "--selectors", "t.x", "a")
    merged = run_toml_transform(
        tmp_path,
        base_text,
        "--selector-type",
        "remove",
        "--selectors",
        "a",
        overlay_text="c = 3\n\n[v]\nq = 1\n",
    )

    merged_with_crlf_overlay = run_toml_transform(
        tmp_path,
        base_text,
        "--selector-type",
        "remove",
        "--selectors",
        "a",
        overlay_text="c = 3\r\n\r\n[v]\r\nq = 1\r\n",
    )

    expected_merge = "b = 2\r\nc = 3\r\n\r\n[t]\r\nx = 1\r\n\r\n[u]\r\ny = 2\r\n\r\n[v]\r\nq = 1\r\n"
    assert retained == "a = 1\r\n\r\n[t]\r\nx = 1\r\n"
    assert merged == expected_merge
    assert merged_with_crlf_overlay == expected_merge


OUT_OF_ORDER_TABLES = "[a]\nx = 1\n[b]\ny = 2\n[a.c]\nz = 3\n"
REPEATED_DOTTED_KEYS = "a.x = 1\nb = 2\na.y = 2\n"


def test_selectors_reach_into_split_tables(tmp_path: Path) -> None:
    def cleanup(base_text: str, *selector_args: str) -> dict:
        return tomllib.loads(run_toml_transform(tmp_path, base_text, *selector_args))

    assert cleanup(OUT_OF_ORDER_TABLES, "--selector-type", "remove", "--selectors", "a.c") == {
        "a": {"x": 1},
        "b": {"y": 2},
    }
    assert cleanup(OUT_OF_ORDER_TABLES, "--selectors", "a.c") == {"a": {"c": {"z": 3}}}
    assert cleanup(OUT_OF_ORDER_TABLES, "--selectors", r"re:^a\.c\.z$") == {"a": {"c": {"z": 3}}}
    assert cleanup(REPEATED_DOTTED_KEYS, "--selector-type", "remove", "--selectors", "a.y") == {
        "a": {"x": 1},
        "b": 2,
    }
    assert cleanup(REPEATED_DOTTED_KEYS, "--selectors", "a.y") == {"a": {"y": 2}}


def test_merge_keeps_every_part_of_split_tables(tmp_path: Path) -> None:
    def merge(base_text: str, overlay_text: str, *selector_args: str) -> dict:
        return tomllib.loads(
            run_toml_transform(tmp_path, base_text, *selector_args, overlay_text=overlay_text)
        )

    assert merge("k = 1\n", OUT_OF_ORDER_TABLES, "--selectors", "zzz") == {
        "a": {"x": 1, "c": {"z": 3}},
        "b": {"y": 2},
    }
    assert merge("k = 1\n", REPEATED_DOTTED_KEYS, "--selectors", "zzz") == {
        "a": {"x": 1, "y": 2},
        "b": 2,
    }
    assert merge(
        OUT_OF_ORDER_TABLES, "[a]\nw = 1\n", "--selector-type", "remove", "--selectors", "b"
    ) == {"a": {"x": 1, "w": 1, "c": {"z": 3}}}
    assert merge(OUT_OF_ORDER_TABLES, "[a]\nw = 1\n", "--selectors", "a.c") == {
        "a": {"w": 1, "c": {"z": 3}}
    }


def test_selectors_reach_into_inline_tables(tmp_path: Path) -> None:
    base_text = "a = {b = 1, c = 2}\nz = 3\n"

    retained = run_toml_transform(tmp_path, base_text, "--selectors", "a.b")
    removed = run_toml_transform(tmp_path, base_text, "--selector-type", "remove", "--selectors", "a.b")
    regex_removed = run_toml_transform(
        tmp_path, base_text, "--selector-type", "remove", "--selectors", r"re:^a\.c$"
    )

    middle_removed = run_toml_transform(
        tmp_path,
        "a = {b = 1, c = 2, e = 3}  # note\n",
        "--selector-type",
        "remove",
        "--selectors",
        "a.c",
    )

    assert retained == "a = {b = 1}\n"
    assert removed == "a = {c = 2}\nz = 3\n"
    assert regex_removed == "a = {b = 1}\nz = 3\n"
    assert middle_removed == "a = {b = 1, e = 3}  # note\n"


def test_merge_keeps_retained_live_keys_across_inline_and_table_styles(tmp_path: Path) -> None:
    inline_overlay = run_toml_transform(
        tmp_path,
        "[a]\nx = 1\nlive = 9\n\n[a.sub]\nq = 1\n",
        "--selectors",
        "a.live",
        "a.sub",
        overlay_text="a = {x = 2}\n",
    )
    table_overlay = run_toml_transform(
        tmp_path,
        "a = {x = 1, live = 9}\n",
        "--selectors",
        "a.live",
        overlay_text="[a]\nx = 2\n",
    )

    commented_inline_overlay = run_toml_transform(
        tmp_path,
        "a = {x = 1, live = 9}\n",
        "--selectors",
        "a.live",
        overlay_text="a = {x = 2}  # repo note\n",
    )

    assert tomllib.loads(inline_overlay) == {"a": {"x": 2, "live": 9, "sub": {"q": 1}}}
    assert commented_inline_overlay == "a = {x = 2, live = 9}  # repo note\n"
    assert inline_overlay.startswith("a = {")
    assert tomllib.loads(table_overlay) == {"a": {"x": 2, "live": 9}}
    assert table_overlay.startswith("[a]\n")


def test_retain_mixing_regex_and_exact_selectors_keeps_document_order(tmp_path: Path) -> None:
    output = run_toml_transform(tmp_path, "[t]\nx = 1\n[u]\ny = 1\n", "--selectors", "re:^u$", "t")

    assert output == "[t]\nx = 1\n\n[u]\ny = 1\n"


def test_compare_file_reuse_requires_values_of_the_same_type(tmp_path: Path) -> None:
    def cleanup_with_compare(base_text: str, compare_text: str) -> str:
        return run_toml_transform(
            tmp_path,
            base_text,
            "--selector-type",
            "remove",
            "--selectors",
            "unused",
            compare_text=compare_text,
        )

    stale_lookalikes = [
        ("a = true\n", "a = 1\n"),
        ("a = 1\n", "a = 1.0\n"),
        ("a = [1, 2]\n", "a = [1.0, 2]\n"),
        ("a = {b = 0.0}\n", "a = {b = -0.0}\n"),
        ("t = 1979-05-27T07:32:00Z\n", "t = 1979-05-27T00:32:00-07:00\n"),
    ]
    for base_text, compare_text in stale_lookalikes:
        assert cleanup_with_compare(base_text, compare_text) == base_text

    reusable_compare_text = "a  =  nan\nt = 1979-05-27T07:32:00Z\n"
    assert (
        cleanup_with_compare("a = nan\nt = 1979-05-27T07:32:00Z\n", reusable_compare_text)
        == reusable_compare_text
    )


def test_quoted_selector_segments_follow_toml_basic_string_rules() -> None:
    assert MODULE.parse_key_paths(
        ['"é"', r'"a\tb"', '" a"', 'x."a.b"', r'"q\"t"', r'"é"', 'a.""', 'a..b', " a . b "]
    ) == [
        ("é",),
        ("a\tb",),
        (" a",),
        ("x", "a.b"),
        ('q"t',),
        ("é",),
        ("a", ""),
        ("a", "b"),
        ("a", "b"),
    ]


def test_remove_quoted_empty_key_keeps_its_table(tmp_path: Path) -> None:
    output = run_toml_transform(
        tmp_path,
        '[a]\n"" = 1\nk = 2\n',
        "--selector-type",
        "remove",
        "--selectors",
        'a.""',
    )

    assert output == "[a]\nk = 2\n"
