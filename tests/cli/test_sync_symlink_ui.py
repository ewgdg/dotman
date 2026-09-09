import asyncio
import json

from dotman.sync_deck import CommandDeck, SyncDeckApp
from tests.cli.test_sync_deck_command import runner_for, arguments
from tests.engine.test_sync_session import open_session
from tests.engine.test_sync_symlinks import linked_file


def test_unattended_rejects_prompt_replacement(tmp_path, monkeypatch, capsys):
    engine, path, referent = linked_file(tmp_path, monkeypatch)
    assert runner_for(engine).run(arguments(dry_run=False)) == 1
    payload = json.loads(capsys.readouterr().out)
    assert "symlink-authorization-required" in str(payload)
    assert path.is_symlink()
    assert referent.read_bytes() == b"old"


def test_deck_authorization_is_explicit_and_separate_from_selection(tmp_path, monkeypatch):
    engine, path, referent = linked_file(tmp_path, monkeypatch)
    with open_session(engine) as session:
        app = SyncDeckApp(CommandDeck(session, use_color=False))
        async def interact():
            async with app.run_test(size=(120, 24)) as pilot:
                await pilot.press("space")
                await pilot.pause()
                assert not session.view.rows[0].approved
                await pilot.press("l")
                await pilot.pause()
                assert session.view.rows[0].symlink_authorized
                assert not session.view.rows[0].approved
                await pilot.press("space")
                await pilot.pause()
                assert session.view.rows[0].approved
                assert path.is_symlink()
        asyncio.run(asyncio.wait_for(interact(), timeout=5))
