"""Keep the manual demo scene honest: every case must still show what it advertises."""

from dotman.engine import DotmanEngine
from dotman.sync_deck import CommandDeck
from tests.demo_scene import CASES, build_scene, scene_environment


def test_demo_scene_cases_show_what_they_advertise(tmp_path, monkeypatch):
    root = tmp_path / "scene"
    for name, value in scene_environment(root).items():
        monkeypatch.setenv(name, value)
    engine = DotmanEngine.from_config_path(build_scene(root))
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=True) as session:
        deck = CommandDeck(session, use_color=False)
        rows = {row.row_id.rsplit(".", 1)[-1]: index for index, row in enumerate(session.view.rows)}
        for case in CASES:
            assert case.name in rows, f"{case.name} is missing from the Command Deck"
            deck.focus = rows[case.name]
            deck.open_review()
            assert case.shows in deck.review_text(), f"{case.name} no longer shows {case.shows!r}"
            deck.back()
