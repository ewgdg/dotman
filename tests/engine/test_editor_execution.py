from pathlib import Path

from dotman.command_runtime import ArgvCommand, CommandResult, MemoryCommandRuntime
from dotman.execution import build_execution_session, execute_session
from dotman.engine import DotmanEngine
from dotman.models import EditorSpec, TargetPlan
from tests.helpers import make_package_plan, write_single_repo_config


def _editor_plan(tmp_path: Path, editor: EditorSpec, *, explicit=True) -> tuple[Path, Path, object]:
    package_root = tmp_path / "package"
    package_root.mkdir()
    repo_path = package_root / "files" / "config"
    repo_path.parent.mkdir()
    repo_path.write_text("repo\n", encoding="utf-8")
    live_path = tmp_path / "live"
    live_path.write_text("live\n", encoding="utf-8")
    include = package_root / "files" / "include"
    include.write_text("include\n", encoding="utf-8")
    target = TargetPlan(
        package_id="app",
        target_name="config",
        repo_path=repo_path,
        live_path=live_path,
        action="update",
        target_kind="file",
        projection_kind="raw",
        editor=editor,
        editor_explicit=explicit,
        additional_sources=("files/include",),
        review_before_bytes=b"repo\n",
        review_after_bytes=b"live\n",
        command_cwd=package_root,
        command_env={"DOTMAN_PACKAGE_ROOT": str(package_root)},
    )
    return repo_path, include, make_package_plan(
        operation="pull",
        repo_name="fixture",
        package_id="app",
        requested_profile="default",
        target_plans=[target],
        repo_root=tmp_path,
    )


def test_default_editor_gets_staged_primary_and_additional_sources_with_review_in_env(tmp_path, monkeypatch):
    repo_path, include, package = _editor_plan(tmp_path, EditorSpec(type="default", io="pipe"))
    monkeypatch.setenv("EDITOR", "configured-editor")
    seen = {}

    def run(request):
        seen["request"] = request
        primary, additional = request.command.arguments[1:]
        assert Path(primary).read_text() == "repo\n"
        assert Path(additional).read_text() == "include\n"
        assert Path(request.env["DOTMAN_REPO_PATH"]) == Path(primary)
        assert Path(request.env["DOTMAN_EDITOR_REVIEW_PATH"]).name == "reconcile-review.md"
        assert request.env["DOTMAN_EDITOR_REVIEW_PATH"] not in request.command.arguments
        Path(primary).write_text("edited\n")
        return CommandResult(exit_code=0)

    result = execute_session(
        build_execution_session([package], operation="pull"),
        stream_output=False,
        assume_yes=True,
        command_runtime=MemoryCommandRuntime([run]),
    )

    assert result.status == "ok"
    assert repo_path.read_text() == "edited\n"
    assert include.read_text() == "include\n"
    assert seen["request"].elevation == "none"


def test_editor_failure_discards_staged_changes(tmp_path, monkeypatch):
    repo_path, include, package = _editor_plan(
        tmp_path,
        EditorSpec(type=None, run="custom-editor", io="pipe"),
    )

    def run(request):
        primary = Path(request.command.arguments[1])
        primary.write_text("must-not-commit\n")
        return CommandResult(exit_code=7)

    result = execute_session(
        build_execution_session([package], operation="pull"),
        stream_output=False,
        assume_yes=True,
        command_runtime=MemoryCommandRuntime([run]),
    )

    assert result.status == "failed"
    assert repo_path.read_text() == "repo\n"
    assert include.read_text() == "include\n"


def test_jinja_editor_discovers_dependency_chain_after_configured_sources(tmp_path, monkeypatch):
    repo_path, _include, package = _editor_plan(
        tmp_path,
        EditorSpec(type="jinja", io="pipe"),
    )
    nested = repo_path.parent / "nested.j2"
    nested.write_text("nested\n")
    repo_path.write_text("{% include 'nested.j2' %}\n")
    monkeypatch.setenv("EDITOR", "configured-editor")
    seen = {}

    def run(request):
        seen["args"] = request.command.arguments
        assert len(request.command.arguments) == 4
        assert Path(request.command.arguments[1]).read_text() == "{% include 'nested.j2' %}\n"
        assert Path(request.command.arguments[2]).read_text() == "include\n"
        assert Path(request.command.arguments[3]).read_text() == "nested\n"
        return CommandResult(exit_code=0)

    result = execute_session(
        build_execution_session([package], operation="pull"),
        stream_output=False,
        assume_yes=True,
        command_runtime=MemoryCommandRuntime([run]),
    )

    assert result.status == "ok"
    assert len(seen["args"]) == 4




def test_default_editor_resolves_git_editor_through_command_runtime(tmp_path, monkeypatch):
    _repo_path, _include, package = _editor_plan(
        tmp_path,
        EditorSpec(type="default", io="pipe"),
    )
    for name in ("VISUAL", "EDITOR", "GIT_EDITOR"):
        monkeypatch.delenv(name, raising=False)
    seen = []

    def resolve_or_edit(request):
        seen.append(request)
        if isinstance(request.command, ArgvCommand) and request.command.arguments == ("git", "config", "--get", "core.editor"):
            return CommandResult(exit_code=0, stdout=b"git-editor --wait\n")
        return CommandResult(exit_code=0)

    result = execute_session(
        build_execution_session([package], operation="pull"),
        stream_output=False,
        assume_yes=True,
        command_runtime=MemoryCommandRuntime([resolve_or_edit, resolve_or_edit]),
    )

    assert result.status == "ok"
    assert seen[0].command == ArgvCommand(("git", "config", "--get", "core.editor"))
    assert seen[1].command.arguments[:2] == ("git-editor", "--wait")


def test_default_editor_prefers_nvim_over_vi_when_both_are_available(tmp_path, monkeypatch):
    _repo_path, _include, package = _editor_plan(tmp_path, EditorSpec(type="default", io="pipe"))
    for name in ("VISUAL", "EDITOR", "GIT_EDITOR"):
        monkeypatch.delenv(name, raising=False)

    available = {"nvim", "vi"}
    monkeypatch.setattr("dotman.reconcile.shutil.which", lambda name: name if name in available else None)
    seen = []

    def run(request):
        seen.append(request)
        if request.command == ArgvCommand(("git", "config", "--get", "core.editor")):
            return CommandResult(exit_code=1)
        return CommandResult(exit_code=0)

    result = execute_session(
        build_execution_session([package], operation="pull"),
        stream_output=False,
        assume_yes=True,
        command_runtime=MemoryCommandRuntime([run, run]),
    )

    assert result.status == "ok"
    assert seen[1].command.arguments[0] == "nvim"


def test_custom_editor_that_mentions_dotman_uses_transactional_contract(tmp_path, monkeypatch):
    repo_path, _include, package = _editor_plan(
        tmp_path,
        EditorSpec(
            type=None,
            run='dotman reconcile editor --repo-path "$DOTMAN_REPO_PATH"',
            io="pipe",
        ),
    )
    seen = {}

    def run(request):
        seen["request"] = request
        assert request.command.source.endswith("editable-2-include")
        assert request.env["DOTMAN_EDITOR_REVIEW_PATH"] not in request.command.source
        Path(request.env["DOTMAN_REPO_PATH"]).write_text("edited\n", encoding="utf-8")
        return CommandResult(exit_code=0)

    result = execute_session(
        build_execution_session([package], operation="pull"),
        stream_output=False,
        assume_yes=True,
        command_runtime=MemoryCommandRuntime([run]),
    )

    assert result.status == "ok"
    assert repo_path.read_text(encoding="utf-8") == "edited\n"



def test_file_capture_stages_dotman_readable_live_input(tmp_path, monkeypatch):
    from dotman.models import TargetPlan

    repo_path = tmp_path / "repo"
    live_path = tmp_path / "live"
    repo_path.write_text("repo\n", encoding="utf-8")
    live_path.write_text("live\n", encoding="utf-8")
    target = TargetPlan(
        package_id="app", target_name="config", repo_path=repo_path, live_path=live_path,
        action="update", target_kind="file", projection_kind="command",
        capture="cat", capture_command='cat "$DOTMAN_LIVE_PATH"',
        command_cwd=tmp_path, command_env={"DOTMAN_SOURCE": str(repo_path), "DOTMAN_LIVE_PATH": str(live_path)},
    )
    package = make_package_plan(operation="pull", repo_name="fixture", package_id="app",
                                requested_profile="default", target_plans=[target], repo_root=tmp_path)
    seen = {}

    def run(request):
        if getattr(request.command, "arguments", ())[0:2] == ("sudo", "-v"):
            return CommandResult(exit_code=0)
        seen["path"] = Path(request.env["DOTMAN_LIVE_PATH"])
        seen["source"] = Path(request.env["DOTMAN_SOURCE"])
        seen["content"] = seen["path"].read_text(encoding="utf-8")
        seen["source_content"] = seen["source"].read_text(encoding="utf-8")
        seen["elevation"] = request.elevation
        return CommandResult(exit_code=0, stdout=b"live\n")

    monkeypatch.setattr("dotman.execution.needs_sudo_for_read", lambda _path: True)
    monkeypatch.setattr("dotman.execution.read_bytes", lambda path: Path(path).read_bytes())
    monkeypatch.setattr("dotman.execution.request_sudo", lambda _reason: None)
    result = execute_session(build_execution_session([package], operation="pull"), stream_output=False,
                             assume_yes=True, command_runtime=MemoryCommandRuntime([run]))
    assert result.status == "ok"
    assert seen["path"] != live_path
    assert seen["source"] != repo_path
    assert seen["content"] == "live\n"
    assert seen["source_content"] == "repo\n"
    assert seen["elevation"] == "none"


def test_directory_capture_stages_dotman_readable_live_input(tmp_path, monkeypatch):
    from dotman.models import DirectoryPlanItem, TargetPlan

    repo_path = tmp_path / "repo"; repo_path.mkdir()
    live_path = tmp_path / "live"; live_path.mkdir()
    repo_file = repo_path / "a.conf"; repo_file.write_text("repo\n", encoding="utf-8")
    live_file = live_path / "a.conf"; live_file.write_text("live\n", encoding="utf-8")
    item = DirectoryPlanItem(relative_path="a.conf", action="update", repo_path=repo_file,
                             live_path=live_file, capture_command='cat "$DOTMAN_LIVE_PATH"')
    target = TargetPlan(package_id="app", target_name="config", repo_path=repo_path, live_path=live_path,
                        action="update", target_kind="directory", projection_kind="directory",
                        directory_items=(item,), command_cwd=tmp_path,
                        command_env={"DOTMAN_LIVE_PATH": str(live_file)})
    package = make_package_plan(operation="pull", repo_name="fixture", package_id="app",
                                requested_profile="default", target_plans=[target], repo_root=tmp_path)
    seen = {}

    def run(request):
        if getattr(request.command, "arguments", ())[0:2] == ("sudo", "-v"):
            return CommandResult(exit_code=0)
        seen["path"] = Path(request.env["DOTMAN_LIVE_PATH"])
        seen["content"] = seen["path"].read_text(encoding="utf-8")
        seen["relative"] = request.env["DOTMAN_TARGET_RELATIVE_PATH"]
        seen["elevation"] = request.elevation
        return CommandResult(exit_code=0, stdout=b"live\n")

    monkeypatch.setattr("dotman.execution.needs_sudo_for_read", lambda _path: True)
    monkeypatch.setattr("dotman.execution.read_bytes", lambda path: Path(path).read_bytes())
    monkeypatch.setattr("dotman.execution.request_sudo", lambda _reason: None)
    result = execute_session(build_execution_session([package], operation="pull"), stream_output=False,
                             assume_yes=True, command_runtime=MemoryCommandRuntime([run]))
    assert result.status == "ok"
    assert seen["path"] != live_file
    assert seen["content"] == "live\n"
    assert seen["relative"] == "a.conf"
    assert seen["elevation"] == "none"


def test_editor_discovery_prefers_visual_and_uses_review_env(tmp_path, monkeypatch):
    repo_path, include, package = _editor_plan(tmp_path, EditorSpec(type="default", io="pipe"))
    monkeypatch.setenv("VISUAL", "visual-editor --wait")
    monkeypatch.setenv("EDITOR", "fallback-editor")
    seen = {}

    def run(request):
        seen["command"] = request.command
        seen["env"] = dict(request.env)
        seen["repo_content"] = Path(request.env["DOTMAN_REPO_PATH"]).read_text(encoding="utf-8")
        primary, additional = request.command.arguments[2:]
        assert Path(primary).read_text(encoding="utf-8") == "repo\n"
        assert Path(additional).read_text(encoding="utf-8") == "include\n"
        Path(primary).write_text("visual edit\n", encoding="utf-8")
        return CommandResult(exit_code=0)

    result = execute_session(
        build_execution_session([package], operation="pull"),
        stream_output=False,
        assume_yes=True,
        command_runtime=MemoryCommandRuntime([run]),
    )

    assert result.status == "ok"
    assert seen["command"].arguments[:2] == ("visual-editor", "--wait")
    assert seen["env"]["DOTMAN_EDITOR_REVIEW_PATH"] not in seen["command"].arguments
    assert seen["env"]["DOTMAN_EDITOR_SOURCE_PATH"] == seen["command"].arguments[2]
    assert seen["env"]["DOTMAN_EDITOR_ADDITIONAL_SOURCE_PATHS"] == seen["command"].arguments[3]
    assert seen["repo_content"] == "repo\n"
    assert repo_path.read_text(encoding="utf-8") == "visual edit\n"
    assert include.read_text(encoding="utf-8") == "include\n"


def test_directory_child_editor_runs_transactionally(tmp_path):
    from dotman.models import DirectoryPlanItem, TargetPlan

    repo_path = tmp_path / "repo"; repo_path.mkdir()
    live_path = tmp_path / "live"; live_path.mkdir()
    repo_file = repo_path / "a.conf"; repo_file.write_text("repo\n", encoding="utf-8")
    live_file = live_path / "a.conf"; live_file.write_text("live\n", encoding="utf-8")
    item = DirectoryPlanItem(
        relative_path="a.conf", action="update", repo_path=repo_file, live_path=live_file,
        editor=EditorSpec(type=None, run="custom-editor", io="pipe"),
        review_before_bytes=b"repo\n", review_after_bytes=b"live\n",
    )
    target = TargetPlan(package_id="app", target_name="config", repo_path=repo_path, live_path=live_path,
                        action="update", target_kind="directory", projection_kind="directory",
                        directory_items=(item,), command_cwd=tmp_path)
    package = make_package_plan(operation="pull", repo_name="fixture", package_id="app",
                                requested_profile="default", target_plans=[target], repo_root=tmp_path)
    seen = {}

    def run(request):
        seen["request"] = request
        primary = Path(request.env["DOTMAN_EDITOR_PRIMARY_PATH"])
        assert primary.read_text(encoding="utf-8") == "repo\n"
        primary.write_text("edited\n", encoding="utf-8")
        return CommandResult(exit_code=0)

    result = execute_session(build_execution_session([package], operation="pull"), stream_output=False,
                             assume_yes=True, command_runtime=MemoryCommandRuntime([run]))
    assert result.status == "ok"
    assert seen["request"].command.arguments[0] == "custom-editor"
    assert repo_file.read_text(encoding="utf-8") == "edited\n"


def test_push_only_delete_directory_child_executes_delete_not_push(tmp_path):
    from dotman.models import DirectoryPlanItem, TargetPlan

    repo_path = tmp_path / "repo"; repo_path.mkdir()
    live_path = tmp_path / "live"; live_path.mkdir()
    live_file = live_path / "a.conf"; live_file.write_text("live\n", encoding="utf-8")
    item = DirectoryPlanItem(relative_path="a.conf", action="delete", repo_path=repo_path / "a.conf", live_path=live_file)
    target = TargetPlan(package_id="app", target_name="config", repo_path=repo_path, live_path=live_path,
                        action="delete", target_kind="directory", projection_kind="directory", directory_items=(item,))
    package = make_package_plan(operation="push", repo_name="fixture", package_id="app",
                                requested_profile="default", target_plans=[target], repo_root=tmp_path)
    result = execute_session(build_execution_session([package], operation="push"), stream_output=False,
                             assume_yes=True, command_runtime=MemoryCommandRuntime([]))
    assert result.status == "ok"
    assert not live_file.exists()

def test_inherited_editor_additional_sources_use_declaring_package_root(tmp_path, monkeypatch):
    """Inherited source paths stay anchored to the package that declared them."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo_root = tmp_path / "repo"
    parent_root = repo_root / "packages" / "parent"
    child_root = repo_root / "packages" / "child"
    parent_root.mkdir(parents=True)
    child_root.mkdir(parents=True)
    (repo_root / "profiles").mkdir()
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")

    child_source = child_root / "files" / "config"
    child_source.parent.mkdir(parents=True)
    child_source.write_text("repository\n", encoding="utf-8")
    parent_source = parent_root / "shared" / "editor-context.txt"
    parent_source.parent.mkdir(parents=True)
    parent_source.write_text("declaring package\n", encoding="utf-8")
    (parent_root / "package.toml").write_text(
        """id = "parent"

[targets.config]
source = "files/config"
path = "~/.config"
editor = { run = "custom-editor", io = "pipe", additional_sources = ["shared/editor-context.txt"] }
""",
        encoding="utf-8",
    )
    (child_root / "package.toml").write_text(
        """id = "child"
extends = ["parent"]

[targets.config]
source = "files/config"
path = "~/.config"
""",
        encoding="utf-8",
    )
    live_path = tmp_path / "home" / ".config"
    live_path.parent.mkdir(parents=True)
    live_path.write_text("live\n", encoding="utf-8")

    config_path = write_single_repo_config(tmp_path, repo_name="r", repo_path=repo_root)
    seen = {}

    def run(request):
        seen["request"] = request
        primary, additional = request.command.arguments[1:]
        assert Path(primary).read_text(encoding="utf-8") == "repository\n"
        assert Path(additional).read_text(encoding="utf-8") == "declaring package\n"
        Path(primary).write_text("edited\n", encoding="utf-8")
        return CommandResult(exit_code=0)

    runtime = MemoryCommandRuntime([run])
    engine = DotmanEngine.from_config_path(config_path, command_runtime=runtime)
    resolved_target = engine.get_repo("r").resolve_package("child").targets["config"]
    assert resolved_target.declared_in == child_root
    assert resolved_target.editor.additional_sources_root == parent_root

    package = engine.plan_pull_query("r:child@default").package_plans[0]
    planned_target = package.target_plans[0]
    assert planned_target.additional_sources_root == parent_root
    result = execute_session(
        build_execution_session([package], operation="pull"),
        stream_output=False,
        assume_yes=True,
        command_runtime=runtime,
    )

    assert result.status == "ok"
    assert seen["request"].elevation == "none"
    assert child_source.read_text(encoding="utf-8") == "edited\n"




def test_inherited_editor_append_additional_sources_keep_each_declaring_root(tmp_path, monkeypatch):
    """Parent and child editor inputs are resolved in their respective packages."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo_root = tmp_path / "repo"
    parent_root = repo_root / "packages" / "parent"
    child_root = repo_root / "packages" / "child"
    parent_root.mkdir(parents=True)
    child_root.mkdir(parents=True)
    (repo_root / "profiles").mkdir()
    (repo_root / "profiles" / "default.toml").write_text("", encoding="utf-8")
    (parent_root / "parent.txt").write_text("parent\n", encoding="utf-8")
    (child_root / "child.txt").write_text("child\n", encoding="utf-8")
    (child_root / "files").mkdir()
    (child_root / "files" / "config").write_text("repository\n", encoding="utf-8")
    (parent_root / "package.toml").write_text(
        """id = "parent"

[targets.config]
source = "files/config"
path = "~/.config"
editor = { run = "custom-editor", io = "pipe", additional_sources = ["parent.txt"] }
""",
        encoding="utf-8",
    )
    (child_root / "package.toml").write_text(
        """id = "child"
extends = ["parent"]

[targets.config]
source = "files/config"
path = "~/.config"

[append.targets.config.editor]
additional_sources = ["child.txt"]
""",
        encoding="utf-8",
    )
    live_path = tmp_path / "home" / ".config"
    live_path.parent.mkdir(parents=True)
    live_path.write_text("live\n", encoding="utf-8")
    config_path = write_single_repo_config(tmp_path, repo_name="r", repo_path=repo_root)
    seen = {}

    def run(request):
        seen["request"] = request
        paths = request.command.arguments[1:]
        assert [Path(path).read_text(encoding="utf-8") for path in paths] == [
            "repository\n", "parent\n", "child\n"
        ]
        Path(paths[0]).write_text("edited\n", encoding="utf-8")
        return CommandResult(exit_code=0)

    engine = DotmanEngine.from_config_path(config_path, command_runtime=MemoryCommandRuntime([run]))
    package = engine.plan_pull_query("r:child@default").package_plans[0]
    target = package.target_plans[0]
    assert target.additional_sources == ("parent.txt", "child.txt")
    assert tuple(entry.root for entry in target.additional_source_entries) == (parent_root, child_root)
    result = execute_session(
        build_execution_session([package], operation="pull"),
        stream_output=False,
        assume_yes=True,
        command_runtime=MemoryCommandRuntime([run]),
    )
    assert result.status == "ok"
    assert (child_root / "files" / "config").read_text(encoding="utf-8") == "edited\n"
