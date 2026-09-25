from __future__ import annotations

import asyncio

import pytest
from textual import events

from textual.widgets import DataTable, Static

from dotman.sync_deck import CommandDeck, SyncDeckApp, WorksetTable

SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
from tests.engine.test_sync_session import make_engine


def post_cell_click(app, offset):
    # Pilot.click bypasses App.on_event. Inject the terminal event seam so mouse
    # and keyboard ordering exercises the same boundary as a real terminal.
    table = app.query_one(DataTable)
    x, y = table.region.x + offset[0], table.region.y + offset[1]
    for event_type in (events.MouseDown, events.MouseUp):
        app.post_message(event_type(
            app.screen, x, y, 0, 0, 1, False, False, False,
            screen_x=x, screen_y=y,
        ))


def run(coroutine):
    return asyncio.run(asyncio.wait_for(coroutine, timeout=5))


def detail_strips(app):
    body = app.query_one("#detail-body")
    return [body.render_line(y) for y in range(body.size.height)]


def detail_lines(app):
    """All detail rows as rendered, without the ring or scroll clipping."""
    return [strip.text.rstrip() for strip in detail_strips(app)]


def review_strips(app):
    body = app.query_one("#review-body")
    return [body.render_line(y) for y in range(body.size.height)]


def review_text(app):
    return "\n".join(strip.text.rstrip() for strip in review_strips(app))


def detail_facts(app):
    """Whitespace-normalized rows, so grid column padding does not matter."""
    return [" ".join(line.split()) for line in detail_lines(app)]


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
                await pilot.resize_terminal(42, 16)
                assert table.size.width == 42
                await pilot.press("end")
                await pilot.pause()
                assert table.scroll_x > 0
                assert "Use repository" in table.render_line(1).text
                await pilot.resize_terminal(100, 24)
                await pilot.press("home")
                assert not any(row.approved for row in session.view.rows)
        run(interact())


def test_focused_row_highlight_spans_the_whole_row(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [
        ("a", "push-only", b"r", b"l", ""),
        ("b", "push-only", b"r", b"l", ""),
    ])
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        app = SyncDeckApp(CommandDeck(session, use_color=False))

        async def interact():
            async with app.run_test(size=(100, 24)) as pilot:
                await pilot.press("down")
                table = app.query_one(DataTable)
                cursor_background = table.get_component_rich_style("datatable--cursor").bgcolor
                focused_line = table.render_line(2)
                assert "main:app.b" in focused_line.text
                assert {segment.style.bgcolor for segment in focused_line} == {cursor_background}
        run(interact())


def test_long_target_identities_shrink_so_all_columns_fit_the_terminal(tmp_path, monkeypatch):
    long_name = "very_long_target_name_that_would_push_policy_and_resolution_off_screen"
    engine = make_engine(tmp_path, monkeypatch, [
        ("a", "push-only", b"r", b"l", ""),
        (long_name, "push-only", b"r", b"l", ""),
    ])
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        app = SyncDeckApp(CommandDeck(session, use_color=False))

        async def interact():
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                table = app.query_one(DataTable)
                assert table.max_scroll_x == 0
                header, short_row, long_row = (table.render_line(y).text for y in range(3))
                assert "Resolution" in header
                # The Selection header is no wider than its "[ ]" marker cells.
                assert header.index("Target") <= len(" [ ]  ")
                assert "main:app.a " in short_row
                # Middle elision keeps both the repo prefix and the target name tail.
                assert "main:app.very" in long_row and "…" in long_row and "off_screen" in long_row
                assert "Use repository" in long_row
                # The focused detail still names the full canonical identity.
                await pilot.press("down")
                assert f"main:app.{long_name}" in "".join(detail_lines(app))
                await pilot.resize_terminal(200, 24)
                await pilot.pause()
                assert f"main:app.{long_name}" in table.render_line(2).text
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
                log = app.query_one("#review")
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


def test_copy_key_copies_full_target_identity_and_review_text(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [
        ("one", "push-only", b"repo", b"live", ""),
    ])
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        app = SyncDeckApp(CommandDeck(session, use_color=False))

        async def interact():
            async with app.run_test(size=(40, 24)) as pilot:
                await pilot.press("y")
                # The narrow Target cell is elided; the copy keeps the full identity.
                assert app.clipboard == "main:app.one"
                await pilot.press("enter", "y")
                assert app.clipboard.startswith(":: Proposal Review — main:app.one")
                assert "\x1b[" not in app.clipboard
                assert "Copied" in str(app.query_one("#notice", Static).render())
        run(interact())


def test_mouse_click_focuses_identity_and_toggles_only_approval(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [
        ("one", "push-only", b"repo", b"live", ""),
        ("two", "push-only", b"repo", b"live", ""),
    ])
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        app = SyncDeckApp(CommandDeck(session, use_color=False))

        async def interact():
            async with app.run_test() as pilot:
                post_cell_click(app, (15, 2))
                await pilot.pause()
                assert app.deck.focused_row.row_id == "main:app.two"
                assert not any(row.approved for row in session.view.rows)
                post_cell_click(app, (2, 2))
                await pilot.pause()
                assert [row.approved for row in session.view.rows] == [False, True]
                await pilot.press("a")
                assert all(row.approved for row in session.view.rows)
                await pilot.press("u")
                assert not any(row.approved for row in session.view.rows)
                await pilot.press("escape")
                assert app.return_value is False
        run(interact())


def test_both_fallback_is_distinct_from_observation_failure(tmp_path, monkeypatch):
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
                assert "Use repository" in table.render_line(1).text
                assert "Observation failed" in table.render_line(2).text
                assert detail_facts(app)[:2] == ["main:app.both", "Fallback: absent"]
                await pilot.press("space", "a")
                assert [row.approved for row in session.view.rows] == [True, False]
                await pilot.press("down")
                assert detail_facts(app)[:2] == ["main:app.bad", "error: endpoint must be a regular file"]
        run(interact())


def test_detail_ring_shows_full_paths_and_state_with_hanging_indent(tmp_path, monkeypatch):
    long_name = "very_long_target_name_that_needs_wrapping_inside_the_detail_ring"
    engine = make_engine(tmp_path, monkeypatch, [(long_name, "push-only", b"repo", b"live", "")])
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        app = SyncDeckApp(CommandDeck(session, use_color=False))
        observation = session.view.rows[0].observation

        async def interact():
            async with app.run_test(size=(60, 40)) as pilot:
                await pilot.pause()
                assert app.query_one("#detail").styles.border_top[0] == "round"
                lines = detail_lines(app)
                assert "".join(lines[:2]) == f"main:app.{long_name}"
                live = next(index for index, line in enumerate(lines) if line.startswith("  Live path:"))
                value_column = len(lines[live]) - len(lines[live][len("  Live path:"):].lstrip())
                # Wrapped values continue under their value column, not at the ring edge.
                continuations = []
                for line in lines[live + 1:]:
                    if not line[:value_column].isspace():
                        break
                    continuations.append(line)
                assert continuations and all(not line[value_column].isspace() for line in continuations)
                assert "".join(line[value_column:] for line in [lines[live], *continuations]) == str(observation.live_path)
                facts = detail_facts(app)
                assert any(fact.startswith("Repository path:") for fact in facts)
                assert "Observation: drifted · Sync Base: " + observation.base.status in facts
        run(interact())


def test_tab_focuses_detail_for_keyboard_scrolling_and_esc_returns(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [
        (f"target_with_a_long_name_{index}", "push-only", b"repo", b"live", "") for index in range(2)
    ])
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        app = SyncDeckApp(CommandDeck(session, use_color=False))

        async def interact():
            async with app.run_test(size=(40, 16)) as pilot:
                await pilot.pause()
                table, detail = app.query_one(WorksetTable), app.query_one("#detail")
                assert detail.max_scroll_y > 0
                assert "Tab detail" in str(app.query_one("#help", Static).render())
                await pilot.press("tab")
                assert app.focused is detail
                await pilot.press("down", "down")
                await pilot.wait_for_animation()
                assert table.cursor_row == 0 and detail.scroll_y > 0
                # Row commands still act on the row the detail describes.
                await pilot.press("space")
                assert [row.approved for row in session.view.rows] == [True, False]
                await pilot.press("escape")
                assert app.is_running and app.focused is table
                await pilot.press("tab")
                await pilot.press("tab")
                assert app.focused is table
                await pilot.press("down")
                await pilot.pause()
                assert table.cursor_row == 1 and detail.scroll_y == 0
        run(interact())


def test_detail_styles_identity_and_diagnostics_like_the_workset(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [("bad", "push-only", b"repo", b"live", "")])
    live = tmp_path / "live/bad"
    live.unlink()
    live.mkdir()
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        app = SyncDeckApp(CommandDeck(session, use_color=True))

        async def interact():
            async with app.run_test(size=(110, 24)):
                strips = detail_strips(app)
                plain = list(strips[1])[-1].style
                styled = {segment.text.strip() for strip in strips for segment in strip if segment.style != plain}
                assert {"main", "bad", "error"} <= styled
                hints = app.query_one("#help", Static).render()
                assert hints.plain.startswith("Esc abort · X confirm")
                bold = {hints.plain[span.start:span.end] for span in hints.spans if "bold" in str(span.style)}
                assert {"Esc", "X", "Space"} <= bold and "confirm" not in bold
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
                text = review_text(app)
                assert "Frozen Pull Views" in text
                assert "+live" in text and "external change" not in text
                assert "Live remains unchanged" in text
                assert not session.view.rows[0].approved
                await pilot.press("space")
                assert session.view.rows[0].approved
                await pilot.press("escape", "x")
                assert "repos: 1 · live: 0" in str(app.query_one("#confirmation", Static).render())
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
                assert "No drifted work" in detail_lines(app)[0]
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
                visible_row = next(
                    y for y in range(table.size.height)
                    if "main:app.unit_24" in table.render_line(y).text
                )
                post_cell_click(app, (2, visible_row))
                await pilot.pause()
                assert not any(row.approved for row in session.view.rows)
                assert table.cursor_row == 24
        run(interact())


@pytest.mark.parametrize("navigation,start,expected", [
    ("down", 0, 1),
    ("up", 1, 0),
    ("pagedown", 0, "page"),
    ("pageup", 14, "page"),
    ("ctrl+end", 0, 24),
    ("ctrl+home", 24, 0),
    ("home", 7, 7),
    ("end", 7, 7),
    ("left", 7, 7),
    ("right", 7, 7),
    ("j", 0, 1),
    ("k", 1, 0),
    ("h", 7, 7),
    ("l", 7, 7),
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
                target = expected
                if expected == "page":
                    # A page is the table's visible row count, which depends on the surrounding layout.
                    table = app.query_one(DataTable)
                    page = table.scrollable_content_region.height - table.header_height
                    target = start + page if navigation == "pagedown" else start - page
                # Unlike Pilot.press, post without yielding between terminal keys.
                app.post_message(events.Key(navigation, None))
                app.post_message(events.Key(command, " " if command == "space" else None))
                await pilot.pause()
                table = app.query_one(DataTable)
                assert table.cursor_row == target
                assert app.deck.focused_row.row_id == f"main:app.unit_{target:02}"
                if command == "space":
                    assert [row.row_id for row in session.view.rows if row.approved] == [
                        f"main:app.unit_{target:02}"
                    ]
                else:
                    assert app.deck.reviewing
                    # Opening review queues its resize after the input batch settles.
                    await pilot.pause()
                    assert f"Proposal Review — main:app.unit_{target:02}" in review_text(app)
                    assert not any(row.approved for row in session.view.rows)
        run(interact())



@pytest.mark.parametrize("column,keys,focused,approvals", [
    (15, ("space",), 1, [False, True]),
    (15, ("enter",), 1, [False, False]),
    (15, ("up", "space"), 0, [True, False]),
])
def test_batched_mouse_and_keyboard_share_target(tmp_path, monkeypatch, column, keys, focused, approvals):
    engine = make_engine(tmp_path, monkeypatch, [
        ("one", "push-only", b"repo", b"live", ""),
        ("two", "push-only", b"repo", b"live", ""),
    ])
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        app = SyncDeckApp(CommandDeck(session, use_color=False))

        async def interact():
            async with app.run_test(size=(80, 12)) as pilot:
                post_cell_click(app, (column, 2))
                for key in keys:
                    app.post_message(events.Key(key, " " if key == "space" else None))
                await pilot.pause()
                assert app.query_one(DataTable).cursor_row == focused
                assert app.deck.focused_row.row_id == session.view.rows[focused].row_id
                assert [row.approved for row in session.view.rows] == approvals
                if keys[-1] == "enter":
                    await pilot.pause()
                    assert "Proposal Review — main:app.two" in review_text(app)
        run(interact())


def test_help_area_click_cannot_authorize_after_clear_key(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [
        ("one", "push-only", b"repo", b"live", ""),
        ("two", "push-only", b"repo", b"live", ""),
    ])
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        app = SyncDeckApp(CommandDeck(session, use_color=False))

        async def interact():
            async with app.run_test(size=(80, 12)) as pilot:
                await pilot.pause()
                # Terminal coordinates previously hit the clickable Approve all
                # footer. No yielding before U: a deferred click must not grant
                # Approval after the later explicit clear action.
                for event_type in (events.MouseDown, events.MouseUp):
                    app.post_message(event_type(
                        app.screen, 49, 11, 0, 0, 1, False, False, False,
                        screen_x=49, screen_y=11,
                    ))
                app.post_message(events.Key("u", "u"))
                await pilot.pause()
                assert not any(row.approved for row in session.view.rows)
                await pilot.resize_terminal(40, 12)
                help_widget = app.query_one("#help", Static)
                assert help_widget.size.height == 2
                rendered_help = " ".join(
                    help_widget.render_line(y).text for y in range(help_widget.size.height)
                )
                assert "Esc abort" in rendered_help and "X confirm" in rendered_help
        run(interact())


def test_resolution_menu_changes_intent_without_approval(tmp_path, monkeypatch):
    from textual.widgets import OptionList

    engine = make_engine(tmp_path, monkeypatch, [
        ('unit', 'both', b'repo', b'live', ''),
    ])
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        app = SyncDeckApp(CommandDeck(session, use_color=False))

        async def interact():
            async with app.run_test() as pilot:
                assert session.view.rows[0].intent == 'use-repository'
                assert any(line.startswith('Fallback:') for line in detail_facts(app))
                await pilot.press('r')
                menu = app.query_one(OptionList)
                assert menu.display
                assert menu.option_count == len(session.view.rows[0].allowed_intents)
                await pilot.press('home', 'j')
                assert menu.highlighted == 1
                await pilot.press('k', 'enter')
                assert session.view.rows[0].intent == session.view.rows[0].allowed_intents[0]
                assert not session.view.rows[0].approved
                assert not menu.display
                await pilot.press('r', 'escape')
                assert not menu.display
                assert app.return_value is None
        run(interact())


def test_resolution_cell_and_menu_support_mouse(tmp_path, monkeypatch):
    from textual.widgets import OptionList

    engine = make_engine(tmp_path, monkeypatch, [('unit', 'both', b'repo', b'live', '')])
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        app = SyncDeckApp(CommandDeck(session, use_color=False))

        async def interact():
            async with app.run_test(size=(100, 24)) as pilot:
                table = app.query_one(DataTable)
                resolution_x = table.render_line(0).text.index('Resolution')
                post_cell_click(app, (resolution_x, 1))
                await pilot.pause()
                assert app.query_one(OptionList).display
                await pilot.click('#resolution', offset=(2, 1))
                assert session.view.rows[0].intent == 'use-repository'
                assert not session.view.rows[0].approved
                assert not app.query_one(OptionList).display
        run(interact())


def test_retry_key_rematerializes_failed_review_without_approval(tmp_path, monkeypatch):
    from dotman import sync_session

    engine = make_engine(tmp_path, monkeypatch, [('unit', 'both', b'repo', b'live', '')])
    materialize = sync_session.materialize
    calls = []

    def fail_once(*args, **kwargs):
        calls.append(None)
        if len(calls) == 1:
            raise ValueError('Capture unavailable')
        return materialize(*args, **kwargs)

    monkeypatch.setattr(sync_session, 'materialize', fail_once)
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        app = SyncDeckApp(CommandDeck(session, use_color=False))

        async def interact():
            async with app.run_test() as pilot:
                await pilot.press('enter')
                assert session.view.rows[0].diagnostics
                await pilot.press('t')
                assert session.view.rows[0].proposal is not None
                assert not session.view.rows[0].diagnostics
                assert not session.view.rows[0].approved
                assert len(calls) == 2
        run(interact())


def test_materialization_keeps_deck_responsive_and_gates_actions(tmp_path, monkeypatch):
    import threading
    from contextvars import ContextVar
    from dotman.sync_base_store import FilePresent

    engine = make_engine(tmp_path, monkeypatch, [
        ("unit", "pull-only", b"repo", b"live", ""),
    ])
    ready, release = threading.Event(), threading.Event()
    context = ContextVar("materialization-test", default="missing")
    context.set("copied")
    seen = []
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        def capture(observation):
            seen.append(context.get())
            ready.set()
            assert release.wait(2), "deck event loop blocked during Capture"
            return FilePresent(b"live")
        monkeypatch.setattr(session, "_capture", capture)
        app = SyncDeckApp(CommandDeck(session, use_color=False))

        async def interact():
            async with app.run_test() as pilot:
                try:
                    table = app.query_one(WorksetTable)
                    help_widget = app.query_one("#help", Static)
                    spinning = lambda: any(frame in str(table.get_cell_at((0, 0))) for frame in SPINNER_FRAMES)
                    app.action_approve()
                    # Quick work must not flash progress; it appears only once work lingers,
                    # in the row's Selection cell rather than on an extra line.
                    assert not spinning()
                    for _ in range(100):
                        if ready.is_set():
                            break
                        await asyncio.sleep(.01)
                    assert ready.is_set()
                    for _ in range(100):
                        if spinning():
                            break
                        await asyncio.sleep(.01)
                    assert spinning()
                    assert "Ctrl+C abort" in str(help_widget.render())
                    revision = session.view.revision
                    app.action_clear_all()
                    app.action_approve_all()
                    app.action_review_or_confirm()
                    app.action_retry()
                    app.action_confirm()
                    app.action_resolution()
                    app.choose_resolution(0)
                    post_cell_click(app, (2, 1))
                    await pilot.pause()
                    assert not app.deck.confirming
                    assert session.view.revision == revision
                finally:
                    release.set()
                for _ in range(100):
                    if session.view.rows[0].approved:
                        break
                    await asyncio.sleep(.01)
                await pilot.pause()
                assert session.view.rows[0].approved
                assert seen == ["copied"]
                assert str(table.get_cell_at((0, 0))) == "[x]"
                assert "Ctrl+C abort" not in str(help_widget.render())
        run(interact())


def test_abort_waits_for_materialization_before_terminalizing_and_stops_batch(tmp_path, monkeypatch):
    import threading
    from dotman.sync_base_store import FilePresent

    engine = make_engine(tmp_path, monkeypatch, [
        ("one", "pull-only", b"repo", b"live", ""),
        ("two", "pull-only", b"repo", b"live", ""),
    ])
    ready, release = threading.Event(), threading.Event()
    captures = []
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        def capture(observation):
            captures.append(observation.identity.canonical)
            ready.set()
            assert release.wait(2)
            return FilePresent(b"live")
        monkeypatch.setattr(session, "_capture", capture)
        app = SyncDeckApp(CommandDeck(session, use_color=False))

        async def interact():
            async with app.run_test() as pilot:
                app.action_approve_all()
                try:
                    for _ in range(100):
                        if ready.is_set():
                            break
                        await asyncio.sleep(.01)
                    assert ready.is_set()
                    await pilot.press("ctrl+c", "ctrl+c")
                    assert not session.view.terminal
                    assert app.busy
                    assert app.return_value is None
                finally:
                    release.set()
                for _ in range(100):
                    if app.return_value is False:
                        break
                    await asyncio.sleep(.01)
                assert app.return_value is False
            session.abort()
            assert session.view.terminal
            assert captures == ["main:app.one"]
            assert not session.view.rows[1].approved
            terminal = session.view
            await asyncio.sleep(0)
            assert session.view == terminal
        run(interact())


def test_review_wraps_long_lines_under_their_column_and_reflows(tmp_path, monkeypatch):
    long_line = "word " * 30
    engine = make_engine(tmp_path, monkeypatch, [
        ("one", "push-only", f"{long_line}\n".encode(), b"live\n", ""),
    ])
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        app = SyncDeckApp(CommandDeck(session, use_color=False))

        async def interact():
            async with app.run_test(size=(60, 40)) as pilot:
                await pilot.press("enter")
                await pilot.pause()
                review = app.query_one("#review")
                assert review.virtual_size.width <= review.scrollable_content_region.width
                lines = review_text(app).splitlines()
                start = next(i for i, line in enumerate(lines) if line.strip().startswith("+word"))
                marker_column = lines[start].index("+")
                # Continuations hang under the content, right of the +/- marker.
                assert lines[start + 1].startswith(" " * (marker_column + 1) + "word")
                wrapped_rows = sum("word" in line for line in lines)
                await pilot.resize_terminal(120, 40)
                await pilot.pause()
                assert sum("word" in line for line in review_text(app).splitlines()) < wrapped_rows
        run(interact())


def test_review_colors_sections_and_diff_lines(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, [
        ("one", "push-only", b"repo-line\n", b"live-line\n", ""),
    ])
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        app = SyncDeckApp(CommandDeck(session, use_color=True))

        async def interact():
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.press("enter")
                await pilot.pause()
                styles = {segment.text.strip(): segment.style
                          for strip in review_strips(app) for segment in strip if segment.text.strip()}
                assert styles["Paths"].bold
                assert styles["live-line"].color.number == 1  # ANSI red
                assert styles["repo-line"].color.number == 2  # ANSI green
                assert styles["Approval:"].dim
        run(interact())
