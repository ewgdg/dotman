from __future__ import annotations

from pathlib import Path

from dotman.cli import main


def test_reconcile_jinja_subcommand_discovers_recursive_template_dependencies(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo_path = tmp_path / "profile"
    live_path = tmp_path / "profile.live"
    shared_path = tmp_path / "shared.sh"
    nested_path = tmp_path / "nested.sh"
    repo_path.write_text("{% include 'shared.sh' %}\n", encoding="utf-8")
    live_path.write_text("live\n", encoding="utf-8")
    shared_path.write_text("{% include 'nested.sh' %}\n", encoding="utf-8")
    nested_path.write_text("nested\n", encoding="utf-8")

    recorded: dict[str, object] = {}

    def fake_run_basic_reconcile(
        *,
        repo_path: str,
        live_path: str,
        additional_sources: list[str],
        review_repo_path: str | None = None,
        review_live_path: str | None = None,
        editor: str | None = None,
        assume_yes: bool = False,
    ) -> int:
        recorded["repo_path"] = repo_path
        recorded["live_path"] = live_path
        recorded["additional_sources"] = additional_sources
        recorded["review_repo_path"] = review_repo_path
        recorded["review_live_path"] = review_live_path
        recorded["editor"] = editor
        return 0

    monkeypatch.setattr("dotman.reconcile_helpers.run_basic_reconcile", fake_run_basic_reconcile)

    exit_code = main(
        [
            "reconcile",
            "jinja",
            "--repo-path",
            str(repo_path),
            "--live-path",
            str(live_path),
            "--review-repo-path",
            str(repo_path),
            "--review-live-path",
            str(live_path),
            "--editor",
            "nvim",
        ]
    )

    assert exit_code == 0
    assert recorded["repo_path"] == str(repo_path)
    assert recorded["live_path"] == str(live_path)
    assert recorded["additional_sources"] == [str(shared_path.resolve()), str(nested_path.resolve())]
    assert recorded["review_repo_path"] == str(repo_path)
    assert recorded["review_live_path"] == str(live_path)
    assert recorded["editor"] == "nvim"
