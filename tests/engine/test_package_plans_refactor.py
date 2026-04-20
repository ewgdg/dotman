from __future__ import annotations

from pathlib import Path

import pytest

from dotman.engine import DotmanEngine
from dotman.models import Binding
from tests.helpers import (
    write_manager_config,
    write_multi_instance_repo,
    write_shared_stack_repo,
    write_single_repo_config,
)


def test_plan_push_query_returns_package_plans_for_direct_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    engine = DotmanEngine.from_config_path(write_manager_config(tmp_path))

    operation_plan = engine.plan_push_query("example:git@basic")

    assert operation_plan.operation == "push"
    assert len(operation_plan.package_plans) == 1
    package_plan = operation_plan.package_plans[0]
    assert package_plan.package_id == "git"
    assert package_plan.selection.explicit is True
    assert package_plan.selection.source_kind == "selector_query"
    assert package_plan.selection.source_selector == "git"
    assert package_plan.bound_profile is None


def test_plan_push_query_expands_dependency_package_into_implicit_package_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    write_shared_stack_repo(repo_root)
    engine = DotmanEngine.from_config_path(write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root))

    operation_plan = engine.plan_push_query("fixture:shared-stack@basic")

    assert [(plan.package_id, plan.selection.explicit) for plan in operation_plan.package_plans] == [
        ("shared-stack", True),
        ("shared", False),
    ]
    dependency_plan = operation_plan.package_plans[1]
    assert dependency_plan.selection.source_kind == "dependency"
    assert dependency_plan.selection.owner_identity is not None
    assert dependency_plan.selection.owner_identity.package_id == "shared-stack"


def test_plan_push_query_keeps_multi_instance_bound_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    write_multi_instance_repo(repo_root)
    engine = DotmanEngine.from_config_path(write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root))

    operation_plan = engine.plan_push_query("fixture:profiled@work")

    package_plan = operation_plan.package_plans[0]
    assert package_plan.package_id == "profiled"
    assert package_plan.bound_profile == "work"
    assert package_plan.requested_profile == "work"


def test_tracked_plan_returns_package_plans_for_explicit_and_implicit_packages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    repo_root = tmp_path / "repo"
    write_shared_stack_repo(repo_root)
    engine = DotmanEngine.from_config_path(write_single_repo_config(tmp_path, repo_name="fixture", repo_path=repo_root))
    engine.record_tracked_package_entry(Binding(repo="fixture", selector="shared-stack", profile="basic"))

    operation_plan = engine.plan_push()

    assert [(plan.package_id, plan.selection.explicit) for plan in operation_plan.package_plans] == [
        ("shared-stack", True),
        ("shared", False),
    ]
    assert operation_plan.to_dict()["packages"][0]["package_id"] == "shared-stack"
