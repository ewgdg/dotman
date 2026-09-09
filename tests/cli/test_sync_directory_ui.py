import asyncio
import json

from textual.widgets import DataTable

from dotman.cli import main
from dotman.sync_deck import CommandDeck, SyncDeckApp
from tests.engine.test_sync_directory_observation import directory_engine, open_directory, put


def test_unattended_directory_census_reports_capability_without_mutation(tmp_path, monkeypatch, capsys):
    engine = directory_engine(tmp_path, monkeypatch)
    repo, live = tmp_path / 'repo/packages/app/tree', tmp_path / 'live/tree'
    put(repo, 'nested/child', b'repo'); put(live, 'nested/child', b'live')
    assert main(['--config', str(engine.config.config_path), '--json', '--unattended', 'sync']) == 1
    payload = json.loads(capsys.readouterr().out)
    unit, = payload['sync_units']
    assert unit['identity'] == 'main:app.tree/nested/child'
    assert unit['observation'] == 'drifted'
    assert unit['capability_diagnostics'][0]['code'] == 'directory-convergence-unavailable'
    assert unit['materialization'] == 'not-applicable'
    assert unit['selected'] and not unit['approved']
    assert unit['effects'] == []
    assert (live / 'nested/child').read_bytes() == b'live'


def test_deck_renders_canonical_child_and_independent_inclusion(tmp_path, monkeypatch):
    engine = directory_engine(tmp_path, monkeypatch)
    repo, live = tmp_path / 'repo/packages/app/tree', tmp_path / 'live/tree'
    put(repo, 'nested/child', b'repo'); put(live, 'nested/child', b'live')
    with open_directory(engine) as session:
        app = SyncDeckApp(CommandDeck(session, use_color=True))

        async def interact():
            async with app.run_test(size=(110, 24)) as pilot:
                table = app.query_one(DataTable)
                assert 'main:app.tree/nested/child' in table.render_line(1).text
                assert 'Unsupported' in table.render_line(1).text
                assert '[x]' in table.render_line(1).text
                await pilot.press('space')
                assert not session.view.rows[0].included
                assert '[ ]' in table.render_line(1).text
                await pilot.press('a')
                assert session.view.rows[0].included
                assert not session.view.rows[0].approved
                await pilot.press('u')
                assert not session.view.rows[0].included

        asyncio.run(asyncio.wait_for(interact(), timeout=5))


def test_human_output_explains_child_capability(tmp_path, monkeypatch, capsys):
    engine = directory_engine(tmp_path, monkeypatch)
    put(tmp_path / 'repo/packages/app/tree', 'child')
    assert main(['--config', str(engine.config.config_path), '--unattended', 'sync']) == 1
    output = capsys.readouterr().out
    assert 'main:app.tree/child' in output
    assert 'child convergence is not supported' in output
