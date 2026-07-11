from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from dotman.transforms import toml as MODULE

REPO_ROOT = Path(__file__).resolve().parents[1]


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
    MODULE.write_document_if_changed(output_path, retained_doc, mode_reference_path=repo_path)

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
    MODULE.write_document_if_changed(
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

    MODULE.strip_keys(
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

    MODULE.strip_keys(
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

    MODULE.strip_keys(
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

    MODULE.write_document_if_changed(
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

    MODULE.write_document_if_changed(
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

    repo_path.write_text('model_provider = "openai_http"\n', encoding="utf-8")
    compare_path.write_text(
        'model = "gpt-5.4"\n# keep me\nmodel_provider = "openai_http"\n',
        encoding="utf-8",
    )

    merged_doc = MODULE.load_document(repo_path)
    merged_doc["model"] = "gpt-5.4"

    MODULE.write_document_if_changed(
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

    MODULE.write_document_if_changed(
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

    MODULE.merge_keys(
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

    MODULE.merge_keys(
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

    MODULE.merge_keys(
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

    MODULE.merge_keys(
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

    MODULE.merge_keys(
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

    MODULE.merge_keys(
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

    MODULE.merge_keys(
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

    MODULE.merge_keys(
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

    MODULE.merge_keys(
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

    MODULE.merge_keys_except_stripped(
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

repo_path = Path(sys.argv[1])
live_path = Path(sys.argv[1])
repo_path = Path(sys.argv[2])
output_path = Path(sys.argv[3])

module.merge_keys(
    live_path,
    output_path,
    repo_path,
    {("model",), ("model_reasoning_effort",)},
    [],
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
