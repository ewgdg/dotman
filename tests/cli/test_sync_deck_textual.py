from __future__ import annotations

import asyncio

import pytest
from textual import events

from textual.widgets import DataTable, RichLog, Static

from dotman.sync_deck import CommandDeck, SyncDeckApp
from tests.engine.test_sync_session import make_engine


def run(coroutine):
    return asyncio.run(asyncio.wait_for(coroutine, timeout=5))


def test_rendered_columns_align_across_variable_identities_and_resize(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [
        ("a", "push-only", b"r", b"l", ""),
        ("longer_target", "push-only", b"r", b"l", ""),
    ])
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        app = SyncDeckApp(CommandDeck(session, use_color=False))

        async def interact():
            async with app.run_test(size=(100, 24)) as pilot:
                table = app.query_one(DataTable)
                lines = [table.render_line(y).text for y in range(3)]
                assert lines[1].index("push-only") == lines[0].index("Policy")
                assert lines[2].index("push-only") == lines[0].index("Policy")
                assert lines[1].index("main:app.a") == lines[0].index("Target")
                assert lines[2].index("main:app.longer_target") == lines[0].index("Target")
                await pilot.resize_terminal(42, 12)
                assert table.size.width == 42
                await pilot.press("right", "right", "right")
                await pilot.pause()
                assert table.scroll_x > 0
                assert "Use repository" in table.render_line(1).text
                await pilot.resize_terminal(100, 24)
                await pilot.press("left", "left", "left")
                assert not any(row.approved for row in session.view.rows)
        run(interact())


def test_keyboard_review_scroll_return_approval_and_confirmation(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [
        ("one", "push-only", b"repo", b"live", ""),
        ("two", "push-only", b"repo\n" * 100, b"live\n" * 100, ""),
    ])
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        app = SyncDeckApp(CommandDeck(session, use_color=False))

        async def interact():
            async with app.run_test() as pilot:
                await pilot.press("down", "enter", "down", "down")
                log = app.query_one(RichLog)
                await pilot.pause()
                assert log.scroll_y > 0
                position = log.scroll_y
                assert not any(row.approved for row in session.view.rows)
                await pilot.press("escape", "enter")
                await pilot.pause()
                assert log.scroll_y == position
                await pilot.press("escape", "space", "x")
                frozen = session.view
                await pilot.press("space", "a", "u", "down")
                assert session.view == frozen
                await pilot.press("escape", "x", "enter")
                assert app.return_value is True
        run(interact())
        assert [row.approved for row in session.view.rows] == [False, True]


def test_mouse_click_focuses_identity_and_toggles_only_approval(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [
        ("one", "push-only", b"repo", b"live", ""),
        ("two", "push-only", b"repo", b"live", ""),
    ])
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        app = SyncDeckApp(CommandDeck(session, use_color=False))

        async def interact():
            async with app.run_test() as pilot:
                await pilot.click("#workset", offset=(15, 2))
                assert app.deck.focused_row.row_id == "main:app.two"
                assert not any(row.approved for row in session.view.rows)
                await pilot.click("#workset", offset=(2, 2))
                assert [row.approved for row in session.view.rows] == [False, True]
                await pilot.press("a")
                assert all(row.approved for row in session.view.rows)
                await pilot.press("u")
                assert not any(row.approved for row in session.view.rows)
                await pilot.press("escape")
                assert app.return_value is False
        run(interact())


def test_unsupported_capability_is_distinct_from_observation_failure(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [
        ("both", "both", b"repo", b"live", ""),
        ("bad", "push-only", b"repo", b"live", ""),
    ])
    live = tmp_path / "live/bad"
    live.unlink()
    live.mkdir()
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        app = SyncDeckApp(CommandDeck(session, use_color=False))

        async def interact():
            async with app.run_test(size=(110, 24)) as pilot:
                table = app.query_one(DataTable)
                assert "Unsupported" in table.render_line(1).text
                assert "Observation failed" in table.render_line(2).text
                assert "one-sided file" in str(app.query_one("#detail", Static).render())
                await pilot.press("space", "a")
                assert not any(row.approved for row in session.view.rows)
                await pilot.press("down")
                assert "regular file" in str(app.query_one("#detail", Static).render())
        run(interact())


def test_pull_review_keeps_frozen_evidence_and_never_approves_on_open(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [
        ("unit", "pull-only", b"repo\n", b"live\n", ""),
    ])
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        (tmp_path / "live/unit").write_bytes(b"external change\n")
        app = SyncDeckApp(CommandDeck(session, use_color=True))

        async def interact():
            async with app.run_test(size=(100, 24)) as pilot:
                assert "Use live" in app.query_one(DataTable).render_line(1).text
                await pilot.press("enter")
                text = "\n".join(line.text for line in app.query_one(RichLog).lines)
                assert "Frozen Pull Views" in text
                assert "+live" in text and "external change" not in text
                assert "Live remains unchanged" in text
                assert not session.view.rows[0].approved
                await pilot.press("space")
                assert session.view.rows[0].approved
                await pilot.press("escape", "x")
                assert "1 repository changes / 0 live writes" in str(app.query_one("#confirmation", Static).render())
                await pilot.press("ctrl+c")
                assert app.return_value is False
        run(interact())


def test_empty_workset_can_cancel_without_a_cursor_target(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [
        ("unit", "push-only", b"same", b"same", ""),
    ])
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        app = SyncDeckApp(CommandDeck(session, use_color=False))

        async def interact():
            async with app.run_test() as pilot:
                assert "No drifted work" in str(app.query_one("#detail", Static).render())
                await pilot.press("down", "space", "enter", "a", "u", "escape")
                assert app.return_value is False
                assert session.view.rows == ()
        run(interact())


def test_long_workset_scrolls_without_losing_focused_row(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [
        (f"unit_{index:02}", "push-only", b"repo", b"live", "")
        for index in range(25)
    ])
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        app = SyncDeckApp(CommandDeck(session, use_color=False))

        async def interact():
            async with app.run_test(size=(80, 12)) as pilot:
                await pilot.press(*(["down"] * 24))
                table = app.query_one(DataTable)
                assert table.scroll_y > 0
                assert app.deck.focused_row.row_id == "main:app.unit_24"
                await pilot.press("enter", "escape", "space")
                assert table.cursor_row == 24
                assert [row.row_id for row in session.view.rows if row.approved] == ["main:app.unit_24"]
                assert "Policy" in table.render_line(0).text
        run(interact())


@pytest.mark.parametrize("navigation,start,expected", [
    ("down", 0, 1),
    ("up", 1, 0),
    ("pagedown", 0, 7),
    ("pageup", 14, 7),
    ("ctrl+end", 0, 24),
    ("ctrl+home", 24, 0),
    ("home", 7, 7),
    ("end", 7, 7),
    ("left", 7, 7),
    ("right", 7, 7),
])
@pytest.mark.parametrize("command", ["space", "enter"])
def test_batched_navigation_targets_new_row(tmp_path, monkeypatch, navigation, start, expected, command):
    engine = make_engine(tmp_path, monkeypatch, [
        (f"unit_{index:02}", "push-only", b"repo", b"live", "")
        for index in range(25)
    ])
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        app = SyncDeckApp(CommandDeck(session, use_color=False))

        async def interact():
            async with app.run_test(size=(80, 12)) as pilot:
                await pilot.press(*(["down"] * start))
                if navigation in ("home", "left"):
                    await pilot.press("right")
                # Unlike Pilot.press, post without yielding between terminal keys.
                app.post_message(events.Key(navigation, None))
                app.post_message(events.Key(command, " " if command == "space" else None))
                await pilot.pause()
                table = app.query_one(DataTable)
                assert table.cursor_row == expected
                if navigation in ("home", "end", "left", "right"):
                    assert table.cursor_column == {"home": 0, "end": 3, "left": 0, "right": 1}[navigation]
                assert app.deck.focused_row.row_id == f"main:app.unit_{expected:02}"
                if command == "space":
                    assert [row.row_id for row in session.view.rows if row.approved] == [
                        f"main:app.unit_{expected:02}"
                    ]
                else:
                    assert app.deck.reviewing
                    assert f"Proposal Review — main:app.unit_{expected:02}" in "\n".join(
                        line.text for line in app.query_one(RichLog).lines
                    )
                    assert not any(row.approved for row in session.view.rows)
        run(interact())
