from pathlib import Path
import pytest
from dotman.engine import DotmanEngine
from tests.helpers import write_single_repo_config

def repo(tmp_path, target_lines, directory=False):
    root=tmp_path/"repo"; (root/"packages"/"app"/"files").mkdir(parents=True); (root/"profiles").mkdir()
    if directory:
        (root/"packages"/"app"/"files"/"x").mkdir()
        (root/"packages"/"app"/"files"/"x"/"a.md").write_text("hello")
    else:
        (root/"packages"/"app"/"files"/"x").write_text("hello")
    (root/"profiles"/"default.toml").write_text("")
    (root/"packages"/"app"/"package.toml").write_text("\n".join(["id='app'","[targets.x]","source='files/x'","path='~/.x'","type='directory'" if directory else ""]+target_lines))
    return root

def engine(tmp_path, root):
    return DotmanEngine.from_config_path(write_single_repo_config(tmp_path,repo_name="r",repo_path=root))

def test_flat_defaults_and_nested_rules(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME",str(tmp_path/"home"))
    e=engine(tmp_path,repo(tmp_path,["[targets.x.path_rules.docs]","pattern='*.md'","priority=2"],directory=True))
    t=e.plan_push_query("r:app@default").package_plans[0].target_plans[0]
    assert t.render_command is None
    assert t.compare_repo=="raw"
    assert t.compare_live=="capture"
    spec=e._tracked_state_context.repositories["r"].packages["app"].targets["x"]
    assert spec.render=="raw" and spec.capture=="raw"
    assert spec.compare_repo=="raw" and spec.compare_live=="capture"
    assert spec.editor.type=="default"
    assert spec.path_rules[0].name=="docs" and spec.path_rules[0].priority==2

def test_reject_removed_schema(tmp_path):
    root=repo(tmp_path,['reconcile="jinja"'])
    with pytest.raises(ValueError,match="unsupported keys: reconcile"):
        engine(tmp_path,root)

def test_command_objects_and_patch_validation(tmp_path):
    root=repo(tmp_path,['render={run="cat"}','capture="patch"','compare={repo="render",live="raw"}','editor={run="vim",io="pipe",elevation="root",additional_sources=["inc"]}'])
    e=engine(tmp_path,root); spec=e._tracked_state_context.repositories["r"].packages["app"].targets["x"]
    assert spec.render=="cat" and spec.capture=="patch" and spec.editor.run=="vim"
    plan=e.plan_push_query("r:app@default").package_plans[0].target_plans[0]
    assert plan.render=="cat" and plan.capture=="patch"
    assert plan.editor.run=="vim" and plan.editor.io=="pipe"
    assert plan.compare_repo=="render" and plan.compare_live=="raw"

def test_flat_fields_inherit_and_override_by_name(tmp_path):
    from dotman.manifest import build_target_spec, merge_target_specs
    base = build_target_spec(target_name="x", manifest_path=tmp_path/"package.toml",
        target_payload={"source":"x","path":"~/.x","render":"jinja","compare":{"repo":"render","live":"raw"},
                        "editor":{"run":"vim","additional_sources":["a"]},
                        "path_rules":{"docs":{"pattern":"*.md","priority":4,"render":"jinja"}}})
    child = build_target_spec(target_name="x", manifest_path=tmp_path/"package.toml",
        target_payload={"source":"x","path":"~/.x","path_rules":{"docs":{"pattern":"*.md","capture":"patch"}}})
    merged=merge_target_specs(base,child)
    assert merged.render=="jinja" and merged.compare_repo=="render" and merged.editor.run=="vim"
    assert merged.path_rules[0].render=="jinja" and merged.path_rules[0].capture=="patch" and merged.path_rules[0].priority==4

def test_patch_capture_requires_flat_comparison_contract(tmp_path):
    root=repo(tmp_path,['render="jinja"','capture="patch"'])
    with pytest.raises(ValueError, match="compare.repo"):
        engine(tmp_path,root)


def test_matching_path_rules_compose_each_field_without_resetting_lower_priority(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = repo(tmp_path, [
        'render="jinja"',
        'capture="raw"',
        'compare={repo="render",live="raw"}',
        "[targets.x.path_rules.base]",
        'pattern="*.md"',
        'render="jinja"',
        'compare={repo="render",live="raw"}',
        "[targets.x.path_rules.high]",
        'pattern="a.md"',
        'capture="raw"',
    ], directory=True)
    e = engine(tmp_path, root)
    target = e.plan_push_query("r:app@default").package_plans[0].target_plans[0]
    child = next(item for item in target.directory_items if item.relative_path == "a.md")
    assert child.render_command == "jinja"
    assert child.capture_command is None
    assert child.compare_repo == "render"
    assert child.compare_live == "raw"


def test_preset_comparison_sides_merge_independently(tmp_path):
    from dotman.manifest import build_target_spec
    spec = build_target_spec(
        target_name="x",
        manifest_path=tmp_path / "package.toml",
        target_payload={"source": "x", "path": "~/.x", "preset": "jinja-editor", "compare": {"live": "capture"}},
    )
    assert spec.compare_repo == "render"
    assert spec.compare_live == "capture"


def test_editor_additional_sources_inherit_independently(tmp_path):
    from dotman.manifest import build_target_spec, merge_target_specs
    base = build_target_spec(target_name="x", manifest_path=tmp_path/"package.toml",
        target_payload={"source":"x","path":"~/.x", "editor":{"run":"vim","additional_sources":["base"]}})
    child = build_target_spec(target_name="x", manifest_path=tmp_path/"package.toml",
        target_payload={"source":"x","path":"~/.x", "editor":{"run":"nvim"}})
    merged = merge_target_specs(base, child)
    assert merged.editor.run == "nvim"
    assert merged.additional_sources == ("base",)


def test_builtin_name_command_object_remains_command_in_plan_and_serialization(tmp_path):
    from dotman.manifest import build_target_spec
    spec = build_target_spec(
        target_name="x",
        manifest_path=tmp_path / "package.toml",
        target_payload={"source": "x", "path": "~/.x", "render": {"run": "jinja"}},
    )
    assert spec.render.startswith("__dotman_command__:")
    from dotman.models import TargetPlan
    plan = TargetPlan(
        package_id="app", target_name="x", repo_path=tmp_path/"x",
        live_path=tmp_path/"live", action="noop", target_kind="file",
        projection_kind="command", render=spec.render,
    )
    assert plan.to_dict()["render"] == {"run": "jinja"}


def test_command_projection_stages_protected_inputs_without_elevation(tmp_path, monkeypatch):
    from dotman.command_runtime import CommandResult, MemoryCommandRuntime
    # Use the real plan seam so staging is observable through the command runtime.
    root = repo(tmp_path, ["render = 'cat \"$DOTMAN_SOURCE\"'"])
    seen = {}
    def run(request):
        seen["source"] = request.env["DOTMAN_SOURCE"]
        seen["content"] = Path(seen["source"]).read_text()
        return CommandResult(exit_code=0, stdout=b"hello")
    runtime = MemoryCommandRuntime([run])
    engine_obj = DotmanEngine.from_config_path(
        write_single_repo_config(tmp_path, repo_name="r", repo_path=root),
        command_runtime=runtime,
    )
    monkeypatch.setattr("dotman.projection.needs_sudo_for_read", lambda _path: True)
    plan = engine_obj.plan_push_query("r:app@default").package_plans[0].target_plans[0]
    assert plan.desired_bytes == b"hello"
    request = runtime.requests[0]
    assert request.elevation == "none"
    assert seen["source"] != str(plan.repo_path)
    assert seen["content"] == "hello"


def test_directory_plan_child_propagates_path_rule_editor_sources_and_sync_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "repo"
    package = root / "packages" / "app"
    source = package / "files" / "config"
    source.mkdir(parents=True)
    (source / "a.md").write_text("hello")
    (package / "inc").write_text("included")
    (root / "profiles").mkdir(parents=True)
    (root / "profiles" / "default.toml").write_text("")
    (package / "package.toml").write_text("""
id = "app"

[targets.config]
source = "files/config"
path = "~/.config/app"
type = "directory"

[targets.config.path_rules.docs]
pattern = "*.md"
sync_policy = "push-only"
editor = { run = "custom-editor", io = "pipe", additional_sources = ["inc"] }
""")
    e = engine(tmp_path, root)
    push_target = e.plan_push_query("r:app@default").package_plans[0].target_plans[0]
    item = push_target.directory_items[0]
    assert item.relative_path == "a.md"
    assert item.editor.run == "custom-editor"
    assert item.editor.io == "pipe"
    assert item.additional_sources == ("inc",)
    assert item.sync_policy == "push-only"
    assert item.to_dict()["additional_sources"] == ["inc"]
    assert item.to_dict()["sync_policy"] == "push-only"

    # A child rule can narrow participation independently of its directory target.
    assert e.plan_pull_query("r:app@default").package_plans[0].target_plans[0].directory_items == ()


def test_path_rule_preset_compare_sides_merge_independently(tmp_path):
    from dotman.manifest import build_target_spec
    spec = build_target_spec(
        target_name="config",
        manifest_path=tmp_path / "package.toml",
        target_payload={
            "source": "files/config",
            "path": "~/.config",
            "path_rules": {
                "templates": {
                    "pattern": "*.tmpl",
                    "preset": "jinja-editor",
                    "compare": {"live": "capture"},
                }
            },
        },
    )
    rule = spec.path_rules[0]
    assert rule.compare_repo == "render"
    assert rule.compare_live == "capture"


def test_multi_parent_named_path_rule_inheritance_preserves_and_overrides_explicit_fields(tmp_path):
    from dotman.manifest import build_target_spec, merge_target_specs
    from dotman.models import PackageSpec

    def target(payload):
        return build_target_spec(
            target_name="config",
            manifest_path=tmp_path / "package.toml",
            target_payload={"source": "files/config", "path": "~/.config", "path_rules": {"docs": payload}},
        )

    parent_a = PackageSpec(
        id="a", package_root=tmp_path,
        targets={"config": target({"pattern": "*.md", "priority": 2, "render": "jinja"})},
    )
    parent_b = PackageSpec(
        id="b", package_root=tmp_path,
        targets={"config": target({"capture": "patch"})},
    )
    merged = merge_target_specs(parent_a.targets["config"], parent_b.targets["config"])
    rule = merged.path_rules[0]
    assert rule.pattern == "*.md"
    assert rule.priority == 2
    assert rule.render == "jinja"
    assert rule.capture == "patch"

    explicit = target({"pattern": "*.txt", "priority": 9})
    merged_explicit = merge_target_specs(merged, explicit)
    assert merged_explicit.path_rules[0].pattern == "*.txt"
    assert merged_explicit.path_rules[0].priority == 9


def test_forced_builtin_commands_execute_as_commands_for_render_capture_compare(tmp_path, monkeypatch):
    from dotman.command_runtime import CommandResult, MemoryCommandRuntime

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = repo(tmp_path, [
        'render={run="jinja"}',
        'capture={run="patch"}',
        'compare={repo={run="render"},live={run="capture"}}',
    ])
    live = tmp_path / "home" / ".x"
    live.parent.mkdir(parents=True)
    live.write_text("hello", encoding="utf-8")
    seen = []

    def run(request):
        seen.append(request)
        return CommandResult(exit_code=0, stdout=b"hello")

    runtime = MemoryCommandRuntime([run, run, run])
    e = engine(tmp_path, root)
    e = DotmanEngine.from_config_path(write_single_repo_config(tmp_path, repo_name="r", repo_path=root), command_runtime=runtime)
    push = e.plan_push_query("r:app@default").package_plans[0].target_plans[0]
    assert push.render == "__dotman_command__:jinja"
    assert push.render_command == "__dotman_command__:jinja"
    assert seen[0].command.source == "jinja"
    pull = e.plan_pull_query("r:app@default").package_plans[0].target_plans[0]
    assert [request.command.source for request in seen[1:]] == ["render", "capture"]
    assert pull.to_dict()["render"] == {"run": "jinja"}
    assert pull.to_dict()["capture"] == {"run": "patch"}
    assert pull.to_dict()["compare"] == {"repo": {"run": "render"}, "live": {"run": "capture"}}
    assert "__dotman_command__" not in str(pull.to_dict())


def test_forced_builtin_path_rule_identity_survives_execution_and_serialization(tmp_path, monkeypatch):
    from dotman.command_runtime import CommandResult, MemoryCommandRuntime

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = repo(tmp_path, [
        '[targets.x.path_rules.docs]',
        'pattern="*.md"',
        'render={run="jinja"}',
        'capture={run="patch"}',
        'compare={repo={run="render"},live={run="capture"}}',
    ], directory=True)
    live = tmp_path / "home" / ".x"
    live.mkdir(parents=True)
    (live / "a.md").write_text("different", encoding="utf-8")
    seen = []

    def run(request):
        seen.append(request)
        return CommandResult(exit_code=0, stdout=b"hello")

    runtime = MemoryCommandRuntime([run, run, run])
    e = DotmanEngine.from_config_path(write_single_repo_config(tmp_path, repo_name="r", repo_path=root), command_runtime=runtime)
    plan = e.plan_push_query("r:app@default").package_plans[0].target_plans[0]
    child = plan.directory_items[0]
    assert child.render_command == "__dotman_command__:jinja"
    assert child.capture_command == "__dotman_command__:patch"
    assert child.compare_repo == "__dotman_command__:render"
    assert child.compare_live == "__dotman_command__:capture"
    assert "__dotman_command__" not in str(child.to_dict())
    pulled = e.plan_pull_query("r:app@default").package_plans[0].target_plans[0]
    assert pulled.directory_items == ()
    assert [request.command.source for request in seen[1:]] == ["render", "capture"]


def test_push_only_delete_directory_plan_resolves_child_compare_editor_and_deletes(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "repo"
    package = root / "packages" / "app"
    source = package / "files" / "config"
    source.mkdir(parents=True)
    (source / "a.md").write_text("desired\n")
    (root / "profiles").mkdir(parents=True)
    (root / "profiles" / "default.toml").write_text("")
    (package / "package.toml").write_text('''
id = "app"

[targets.config]
source = "files/config"
path = "~/.config/app"
type = "directory"
sync_policy = "push-only-delete"
compare = { repo = "render", live = "raw" }
editor = { run = "custom-editor", io = "pipe" }

[targets.config.path_rules.docs]
pattern = "*.md"
compare = { repo = "capture", live = "render" }
editor = { run = "child-editor", io = "pipe" }
''')
    live_dir = tmp_path / "home" / ".config" / "app"
    live_dir.mkdir(parents=True)
    (live_dir / "a.md").write_text("live\n")
    e = engine(tmp_path, root)
    target = e.plan_push_query("r:app@default").package_plans[0].target_plans[0]
    assert target.action == "delete"
    assert target.compare_repo == "render"
    assert target.compare_live == "raw"
    assert target.editor.run == "custom-editor"
    assert target.editor_explicit is True
    assert target.directory_items[0].action == "delete"
    assert target.directory_items[0].compare_repo == "capture"
    assert target.directory_items[0].compare_live == "render"
    assert target.directory_items[0].editor.run == "child-editor"


def test_path_rule_push_only_delete_deletes_live_children_without_repo_actions(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "repo"
    package = root / "packages" / "app"
    source = package / "files" / "config"
    source.mkdir(parents=True)
    (source / "managed.txt").write_text("repo version\n", encoding="utf-8")
    (source / "new.txt").write_text("new repo file\n", encoding="utf-8")
    (root / "profiles").mkdir(parents=True)
    (root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    (package / "package.toml").write_text("""
id = "app"

[targets.config]
source = "files/config"
path = "~/.config/app"
type = "directory"

[targets.config.path_rules.cleanup]
pattern = "*"
sync_policy = "push-only-delete"
""", encoding="utf-8")
    live_dir = tmp_path / "home" / ".config" / "app"
    live_dir.mkdir(parents=True)
    (live_dir / "managed.txt").write_text("live version\n", encoding="utf-8")
    (live_dir / "stale.txt").write_text("stale\n", encoding="utf-8")

    engine_obj = engine(tmp_path, root)
    target = engine_obj.plan_push_query("r:app@default").package_plans[0].target_plans[0]

    assert target.action == "delete"
    assert [(item.relative_path, item.action) for item in target.directory_items] == [
        ("managed.txt", "delete"),
        ("stale.txt", "delete"),
    ]


def test_resolve_package_merges_partial_named_path_rule_across_actual_parent_manifests(tmp_path):
    root = tmp_path / "repo"
    for package_id in ("parent-a", "parent-b", "child"):
        (root / "packages" / package_id).mkdir(parents=True)
    (root / "profiles").mkdir()
    (root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    (root / "packages" / "parent-a" / "package.toml").write_text("""
id = "parent-a"

[targets.config.path_rules.docs]
pattern = "*.md"
""", encoding="utf-8")
    (root / "packages" / "parent-b" / "package.toml").write_text("""
id = "parent-b"

[targets.config.path_rules.docs]
priority = 7
render = "jinja"
""", encoding="utf-8")
    (root / "packages" / "child" / "package.toml").write_text("""
id = "child"
extends = ["parent-a", "parent-b"]

[targets.config]
source = "files/config"
path = "~/.config/app"
type = "directory"
""", encoding="utf-8")

    engine_obj = engine(tmp_path, root)
    resolved = engine_obj.repos["r"].resolve_package("child")
    rule = resolved.targets["config"].path_rules[0]

    assert rule.pattern == "*.md"
    assert rule.priority == 7
    assert rule.render == "jinja"
