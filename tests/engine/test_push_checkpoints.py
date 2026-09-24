from tests.engine.test_sync_session import make_engine


def test_failed_publication_render_is_not_deferred_to_execution(tmp_path, monkeypatch):
    marker = tmp_path / 'renders'
    engine = make_engine(tmp_path, monkeypatch, [('unit', 'both', b'repo', None,
        f'render = "echo render >> {marker}; exit 9"')])
    with engine.open_push_session(engine.resolve_sync_scope()) as session:
        observation, = session.view.observations
        assert observation.state == 'observation-failed'
        result = session.execute().result
    assert result.status == 'failed'
    assert marker.read_text().splitlines() == ['render']
    assert not (tmp_path / 'live/unit').exists()
