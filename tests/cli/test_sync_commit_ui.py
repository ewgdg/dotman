from __future__ import annotations

from textual.widgets import OptionList

from dotman.sync_deck import CommandDeck, SyncDeckApp, WorksetTable
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
                # The subject sits on its own line under the repo it commits to.
                lines = commit_list_lines(app)
                index = lines.index(f"[ ] main@{branch}")
                assert lines[index + 1] == "    chore(dotman): pull app (2 targets)"
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


def test_long_commit_lines_wrap_under_the_repo(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, TWO_DRIFTED)
    with engine.open_pull_session(engine.resolve_sync_scope()) as session:
        deck = CommandDeck(session, use_color=False)
        choices = (
            (CommitOption("dotfiles", "feature/very-long-branch-name"),
             "chore(dotman): pull 12 targets in 7 packages across the whole workstation"),
            (CommitOption("work-laptop-dotfiles-shared-with-the-team-config", "main"), "chore(dotman): pull zsh.zshrc"),
        )
        monkeypatch.setattr(deck, "commit_choices", lambda: choices)
        app = SyncDeckApp(deck)

        async def interact():
            async with app.run_test(size=(60, 24)) as pilot:
                await pilot.press("c")
                await pilot.pause()
                lines = [line for line in commit_list_lines(app) if line.strip()]
                # The toggle column stays clear and no text is cut off.
                assert all(line.startswith(("[x] ", "[ ] ", "    ")) for line in lines)
                text = "".join(line[4:] for line in lines).replace(" ", "")
                assert "chore(dotman):pull12targetsin7packagesacrossthewholeworkstation" in text
                assert "work-laptop-dotfiles-shared-with-the-team-config@main" in text
                assert "…" not in text
        run(interact())


def visible_segments(strip):
    return [segment for segment in strip if segment.text.strip()]


def test_cursor_marks_only_the_repo_line_in_workset_cursor_colours(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, TWO_DRIFTED)
    with engine.open_pull_session(engine.resolve_sync_scope()) as session:
        deck = CommandDeck(session, use_color=True)
        choices = ((CommitOption("main", "main"), "chore(dotman): pull app"),
                   (CommitOption("work", "main"), "chore(dotman): pull zsh"))
        monkeypatch.setattr(deck, "commit_choices", lambda: choices)
        app = SyncDeckApp(deck)

        async def interact():
            async with app.run_test(size=(100, 24)) as pilot:
                await pilot.pause()
                workset_cursor = visible_segments(app.query_one(WorksetTable).render_line(1))[0].style
                await pilot.press("c")
                await pilot.pause()
                commit_list = app.query_one("#commit-list", OptionList)

                def entry_lines(index):
                    return [visible_segments(commit_list.render_line(y)) for y in (2 * index, 2 * index + 1)]

                def under_cursor(segments):
                    return all((segment.style.color, segment.style.bgcolor)
                               == (workset_cursor.color, workset_cursor.bgcolor) for segment in segments)

                repo_line, subject_line = entry_lines(0)
                # Like the workset cursor: one line, its text flattened to the cursor colour.
                assert under_cursor(repo_line)
                assert not any(segment.style.bgcolor == workset_cursor.bgcolor for segment in subject_line)
                assert next(segment for segment in repo_line if segment.text == "main").style.bold
                await pilot.press("j")
                await pilot.pause()
                assert not under_cursor(entry_lines(0)[0])
                assert under_cursor(entry_lines(1)[0])
        run(interact())
