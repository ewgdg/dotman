"""Planning commands run concurrently, yet results keep target order."""

from tests.engine.test_sync_session import make_engine, open_session


def _rendezvous(signals, mine: str, other: str, then: str) -> str:
    # Announce, then wait up to ~2s for the sibling. Serial planning can never
    # satisfy both sides, so each command fails unless they overlap.
    return (
        f"touch {signals / mine}; i=0; "
        f"while [ ! -e {signals / other} ] && [ $i -lt 100 ]; do sleep 0.02; i=$((i+1)); done; "
        f"[ -e {signals / other} ] && {then}"
    )


def test_comparison_projections_overlap_and_keep_target_order(tmp_path, monkeypatch):
    signals = tmp_path / "signals"
    signals.mkdir()

    def target(name, other):
        render = _rendezvous(signals, name, other, 'cat "$DOTMAN_REPO_PATH"')
        return (name, "both", b"same", b"same",
                f"render = '{render}'\ncompare = {{ repo = \"render\", live = \"raw\" }}")

    engine = make_engine(tmp_path, monkeypatch, [target("first", "second"), target("second", "first")])
    with open_session(engine) as session:
        observations = session.view.observations

    assert [(unit.identity.target_name, unit.state, unit.diagnostics) for unit in observations] == [
        ("first", "directly-in-sync", ()),
        ("second", "directly-in-sync", ()),
    ]
