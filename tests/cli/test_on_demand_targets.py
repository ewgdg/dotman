"""On-demand targets run only when selected by exact target selector."""

import json
from pathlib import Path

import pytest

from dotman.cli import main
from dotman.engine import DotmanEngine
from dotman.sync_base_store import FilePresent, SyncBaseRecord, SyncBaseStore
from tests.engine.test_manifest_vocabulary import load_manifest_repo, write_manifest_repo
from tests.engine.test_sync_session import make_engine


def on_demand_engine(tmp_path, monkeypatch, *, on_demand="true"):
    """One plain file, one on-demand file and one on-demand probe that logs its runs."""
    engine = make_engine(tmp_path, monkeypatch, [
        ("plain", "push-only", b"repo", b"live", ""),
        ("extra", "both", b"repo", b"live", f"on_demand = {on_demand}"),
    ])
    manifest = tmp_path / "repo/packages/app/package.toml"
    manifest.write_text(manifest.read_text() + f'''
[targets.check]
probe = "echo probe >> {tmp_path / 'probe.log'}"
sync_policy = "push-only"
on_demand = {on_demand}
''')
    return DotmanEngine.from_config_path(engine.config.config_path)


def run(engine, capsys, *args, json_output=True):
    code = main(["--config", str(engine.config.config_path), *(["--json"] if json_output else []),
                 "--unattended", *args])
    output = capsys.readouterr().out
    return code, json.loads(output) if json_output else output


def planned(document) -> set[str]:
    return {unit["identity"] for unit in document["sync_units"]} | {
        work["identity"] for work in document["probe_work"]}


@pytest.mark.parametrize("operation", ["push", "pull"])
def test_selector_less_run_skips_on_demand_targets_quietly(tmp_path, monkeypatch, capsys, operation):
    engine = on_demand_engine(tmp_path, monkeypatch)
    code, document = run(engine, capsys, operation, "--dry-run")
    assert code == 0
    assert not planned(document) & {"main:app.extra", "main:app.check"}
    assert document["on_demand_skips"] == []
    _code, output = run(engine, capsys, operation, "--dry-run", json_output=False)
    assert "on-demand" not in output


def test_package_selector_skips_on_demand_targets_and_names_them(tmp_path, monkeypatch, capsys):
    engine = on_demand_engine(tmp_path, monkeypatch)
    code, document = run(engine, capsys, "push", "--dry-run", "main:app")
    assert code == 0
    assert planned(document) == {"main:app.plain"}
    assert document["on_demand_skips"] == [{"identity": "main:app.extra"}, {"identity": "main:app.check"}]
    _code, output = run(engine, capsys, "push", "--dry-run", "main:app", json_output=False)
    lines = [line.strip() for line in output.splitlines() if "on-demand" in line]
    assert lines == [
        "[skipped] main:app.extra (on-demand: select it by name to run it)",
        "[skipped] main:app.check (on-demand: select it by name to run it)",
    ]
    assert not (tmp_path / "probe.log").exists()


@pytest.mark.parametrize("operation,expected", [
    ("pull", ["main:app.extra"]),
    ("sync", ["main:app.extra", "main:app.check"]),
])
def test_package_selector_reports_only_on_demand_targets_the_operation_could_run(
    tmp_path, monkeypatch, capsys, operation, expected,
):
    # main:app.check is push-only: naming it in a pull would plan nothing.
    engine = on_demand_engine(tmp_path, monkeypatch)
    _code, document = run(engine, capsys, operation, "--dry-run", "main:app")
    assert [skip["identity"] for skip in document["on_demand_skips"]] == expected


def test_executed_package_selector_names_skipped_targets_before_the_timeline(tmp_path, monkeypatch, capsys):
    engine = on_demand_engine(tmp_path, monkeypatch)
    code, output = run(engine, capsys, "push", "main:app", json_output=False)
    assert code == 0
    assert "[skipped] main:app.check (on-demand: select it by name to run it)" in output
    assert not (tmp_path / "probe.log").exists()


def test_exact_target_selector_runs_on_demand_probe(tmp_path, monkeypatch, capsys):
    engine = on_demand_engine(tmp_path, monkeypatch)
    code, document = run(engine, capsys, "push", "main:app.check")
    assert code == 0
    assert planned(document) == {"main:app.check"}
    assert document["on_demand_skips"] == []
    assert (tmp_path / "probe.log").read_text().splitlines() == ["probe"]


def test_executed_probe_without_hooks_reports_ok_not_pending(tmp_path, monkeypatch, capsys):
    # The probe ran during planning and nothing else runs at its scope, so the
    # finished run must not leave it looking unattempted.
    engine = on_demand_engine(tmp_path, monkeypatch)
    code, output = run(engine, capsys, "push", "main:app.check", json_output=False)
    assert code == 0
    assert "[pending]" not in output
    _code, report = run(engine, capsys, "push", "--report", "main:app.check", json_output=False)
    assert "[ok] main:app.check" in report


def test_exact_selector_beside_its_package_selector_is_not_reported_skipped(tmp_path, monkeypatch):
    engine = on_demand_engine(tmp_path, monkeypatch)
    scope = engine.resolve_sync_scope(["main:app", "main:app.check"])
    assert {target.canonical for target in scope.targets} == {"main:app.plain", "main:app.check"}
    assert [target.canonical for target in scope.on_demand_skips_for("push")] == ["main:app.extra"]


def test_on_demand_false_keeps_targets_in_every_scope(tmp_path, monkeypatch, capsys):
    engine = on_demand_engine(tmp_path, monkeypatch, on_demand="false")
    _code, document = run(engine, capsys, "push", "--dry-run")
    assert planned(document) == {"main:app.plain", "main:app.extra", "main:app.check"}
    assert document["on_demand_skips"] == []


def test_on_demand_sync_base_is_listed_and_not_orphaned(tmp_path, monkeypatch):
    # Sync Base inspection sees the whole tracked graph, not the default scope.
    engine = on_demand_engine(tmp_path, monkeypatch)
    with SyncBaseStore.open(engine._tracked_state_context.state_root, "main") as store:
        store.replace(SyncBaseRecord(b"main:app.extra", FilePresent(b"repo")))
    assert [entry["identity"] for entry in engine.list_sync_bases()] == ["main:app.extra"]
    check = next(check for check in engine.doctor().checks if check.key == "sync_bases_orphaned")
    assert check.status == "ok"


def test_on_demand_must_be_boolean(tmp_path: Path) -> None:
    repo_root = write_manifest_repo(tmp_path, target_manifest=['on_demand = "yes"'])
    with pytest.raises(ValueError, match=r"target 'config' on_demand must be a boolean"):
        load_manifest_repo(tmp_path, repo_root)


def test_on_demand_typo_is_rejected(tmp_path: Path) -> None:
    repo_root = write_manifest_repo(tmp_path, target_manifest=["on_demnad = true"])
    with pytest.raises(ValueError, match=r"target 'config' has unsupported keys: on_demnad"):
        load_manifest_repo(tmp_path, repo_root)
