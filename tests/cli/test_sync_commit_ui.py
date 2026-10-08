from __future__ import annotations

from textual.widgets import OptionList

from dotman.sync_deck import CommandDeck, SyncDeckApp
from dotman.sync_session import CommitOption
from tests.cli.test_sync_deck_textual import run
from tests.engine.test_sync_commit import TWO_DRIFTED, git, git_identity  # noqa: F401 - autouse fixture
from tests.engine.test_sync_session import make_engine


def commit_list_lines(app):
    commit_list = app.query_one("#commit-list", OptionList)
    return [commit_list.render_line(y).text.rstrip() for y in range(commit_list.size.height)]


def selected(session):
    return [option.selected for option in session.view.commit_options]


def test_confirmation_offers_commit_toggles_with_message(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, TWO_DRIFTED)
    branch = git(tmp_path / "repo", "branch", "--show-current").strip()
    with engine.open_pull_session(engine.resolve_sync_scope()) as session:
        app = SyncDeckApp(CommandDeck(session, use_color=False))

        async def interact():
            async with app.run_test(size=(100, 24)) as pilot:
                assert not app.query_one("#confirmation-page").display
                await pilot.press("c")
                assert app.query_one("#commit-list").display
                assert any(f"[ ] main@{branch}  chore(dotman): pull app (2 targets)" in line
                           for line in commit_list_lines(app))
                assert "g commit all" in str(app.query_one("#help").render())
                await pilot.press("x")
                await pilot.pause()
                assert selected(session) == [True]
                # g turns everything on unless everything already is.
                await pilot.press("g")
                await pilot.pause()
                assert selected(session) == [False]
                await pilot.press("g")
                await pilot.pause()
                assert selected(session) == [True]
                # Leaving confirmation keeps the choice.
                await pilot.press("escape", "c")
                await pilot.pause()
                assert any("[x] main@" in line for line in commit_list_lines(app))
                await pilot.press("enter")
                assert app.return_value is True
        run(interact())


def test_confirmation_hides_commit_list_when_nothing_writes_a_repository(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, TWO_DRIFTED)
    with engine.open_pull_session(engine.resolve_sync_scope(), commit=True) as session:
        app = SyncDeckApp(CommandDeck(session, use_color=False))

        async def interact():
            async with app.run_test(size=(100, 24)) as pilot:
                await pilot.press("u", "c")
                await pilot.pause()
                assert not app.query_one("#commit-list").display
                assert "commit all" not in str(app.query_one("#help").render())
        run(interact())


def test_preview_offers_no_commit(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, TWO_DRIFTED)
    with engine.open_pull_session(engine.resolve_sync_scope(), preview=True) as session:
        app = SyncDeckApp(CommandDeck(session, use_color=False))

        async def interact():
            async with app.run_test(size=(100, 24)) as pilot:
                await pilot.press("c")
                assert not app.query_one("#commit-list").display
        run(interact())


def test_long_commit_list_scrolls_inside_the_page(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, TWO_DRIFTED)
    with engine.open_pull_session(engine.resolve_sync_scope()) as session:
        deck = CommandDeck(session, use_color=False)
        many = tuple((CommitOption(f"repo{index:02}", "main"), "chore(dotman): pull app") for index in range(30))
        monkeypatch.setattr(deck, "commit_choices", lambda: many)
        app = SyncDeckApp(deck)

        async def interact():
            async with app.run_test(size=(100, 24)) as pilot:
                await pilot.press("c")
                await pilot.pause()
                page = app.query_one("#confirmation-page").region
                assert app.query_one("#commit-list").region.bottom <= page.bottom
                assert ":: Execute?" in str(app.query_one("#confirmation").render())
        run(interact())
