"""EngineLink: the client's thread posts typed messages; snapshots coalesce."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from overcode.tui_engine_link import (
    EngineBellRang,
    EngineConnectionChanged,
    EngineLink,
    EngineSnapshotReady,
)


def _link():
    app = MagicMock()
    posted = []
    app.post_message.side_effect = lambda m: posted.append(m) or True
    link = EngineLink(app)
    link.apply_snapshot = MagicMock()
    return link, posted


class TestMessages:
    def test_a_burst_of_snapshots_posts_once_and_applies_the_latest(self):
        link, posted = _link()
        first, latest = SimpleNamespace(seq=1), SimpleNamespace(seq=2)
        link._on_change(first)
        link._on_change(latest)
        assert [type(m) for m in posted] == [EngineSnapshotReady]
        link.take_snapshot()
        link.apply_snapshot.assert_called_once_with(latest)
        # The message is consumed: a second one finds nothing to apply twice
        link.take_snapshot()
        link.apply_snapshot.assert_called_once()

    def test_a_snapshot_after_the_take_posts_again(self):
        link, posted = _link()
        link._on_change(SimpleNamespace(seq=1))
        link.take_snapshot()
        link._on_change(SimpleNamespace(seq=2))
        assert len(posted) == 2

    def test_bell_and_connection_are_typed(self):
        link, posted = _link()
        link._on_bell("id-a", {"name": "a"})
        link._on_connection(False)
        bell, conn = posted
        assert isinstance(bell, EngineBellRang) and (bell.agent, bell.episode) == ("id-a", {"name": "a"})
        assert isinstance(conn, EngineConnectionChanged) and conn.connected is False


class TestWithoutAClient:
    def test_reports_are_no_ops_before_start(self):
        link, _ = _link()
        link.set_visible(True)
        link.visit("id-a")
        link.stop()
        assert link.stalled_for() is None
