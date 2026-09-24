import pytest


def test_failed_publication_render_is_not_deferred_to_execution(tmp_path, monkeypatch):
    from tests.engine.test_sync_session import make_engine

    marker = tmp_path / 'renders'
    engine = make_engine(tmp_path, monkeypatch, [('unit', 'both', b'repo', None,
        f'render = "echo render >> {marker}; exit 9"')])
    with pytest.raises(ValueError, match='command projection failed'):
        engine.plan_push_query('main:app@default')
    assert marker.read_text().splitlines() == ['render']
    assert not (tmp_path / 'live/unit').exists()


