"""Tests for #385 — stale-content banner on sister-unreachable preview."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from overcode.tui_widgets.preview_pane import PreviewPane


def _make_pane(content_lines, session_name="agent", banner=""):
    """Build a PreviewPane without running __init__ (avoids textual mount)."""
    pane = PreviewPane.__new__(PreviewPane)
    pane.content_lines = content_lines
    pane.monochrome = True  # keep plain text for easy assertion
    pane.session_name = session_name
    pane.stale_banner = banner
    return pane


def _build(pane):
    """Invoke _build_content with a patched size property."""
    with patch.object(PreviewPane, "size",
                      new_callable=lambda: property(lambda self: SimpleNamespace(width=80))):
        return pane._build_content()


class TestPreviewPaneBanner:
    def test_no_banner_when_empty(self):
        pane = _make_pane(["hello"])
        rendered = _build(pane).plain
        assert "⚠" not in rendered
        assert "hello" in rendered

    def test_banner_rendered_above_content(self):
        pane = _make_pane(["hello"], banner="sister-two unreachable — last updated 42s ago")
        rendered = _build(pane).plain
        lines = rendered.splitlines()
        # Header is line 0, banner is line 1, content follows
        assert "⚠" in lines[1]
        assert "sister-two unreachable" in lines[1]
        assert any("hello" in line for line in lines[2:])

    def test_banner_shown_even_when_no_content(self):
        """Sister offline with no cached pane content should still warn."""
        pane = _make_pane([], banner="hostx unreachable — last updated 2m ago")
        rendered = _build(pane).plain
        assert "hostx unreachable" in rendered
        assert "(no output)" in rendered


class TestStaleBannerHelper:
    """Test _stale_banner_for logic without standing up the full TUI."""

    @staticmethod
    def _invoke(tui_self, session):
        """Call the unbound method against a mock self."""
        from overcode.tui import SupervisorTUI
        return SupervisorTUI._stale_banner_for(tui_self, session)

    def test_local_session_returns_empty(self):
        tui = MagicMock()
        session = SimpleNamespace(is_remote=False, source_url="http://x:1")
        assert self._invoke(tui, session) == ""

    def test_remote_reachable_returns_empty(self):
        sister = SimpleNamespace(
            url="http://host:15337", reachable=True, name="host",
            last_fetch=datetime.now().isoformat(),
        )
        tui = MagicMock()
        tui._sister_poller.get_sister_states.return_value = [sister]
        session = SimpleNamespace(is_remote=True, source_url="http://host:15337")
        assert self._invoke(tui, session) == ""

    def test_remote_unreachable_returns_banner(self):
        last = (datetime.now() - timedelta(seconds=45)).isoformat()
        sister = SimpleNamespace(
            url="http://host:15337", reachable=False, name="host-two",
            last_fetch=last,
        )
        tui = MagicMock()
        tui._sister_poller.get_sister_states.return_value = [sister]
        tui._sister_last_fetch_age.side_effect = lambda s: "45s ago"
        session = SimpleNamespace(is_remote=True, source_url="http://host:15337")
        banner = self._invoke(tui, session)
        assert "host-two unreachable" in banner
        assert "45s ago" in banner

    def test_remote_unknown_sister_returns_empty(self):
        """Session claims source_url not in the poller's list — no banner."""
        tui = MagicMock()
        tui._sister_poller.get_sister_states.return_value = []
        session = SimpleNamespace(is_remote=True, source_url="http://gone:1")
        assert self._invoke(tui, session) == ""


class TestSisterLastFetchAge:
    """Test _sister_last_fetch_age formatting."""

    @staticmethod
    def _call(sister):
        from overcode.tui import SupervisorTUI
        return SupervisorTUI._sister_last_fetch_age(sister)

    def test_never_fetched(self):
        assert self._call(SimpleNamespace(last_fetch=None)) == "never"

    def test_bad_timestamp(self):
        assert self._call(SimpleNamespace(last_fetch="not-a-date")) == "unknown"

    def test_seconds_form(self):
        ts = (datetime.now() - timedelta(seconds=30)).isoformat()
        out = self._call(SimpleNamespace(last_fetch=ts))
        assert out.endswith("s ago")

    def test_minutes_form(self):
        ts = (datetime.now() - timedelta(minutes=5)).isoformat()
        out = self._call(SimpleNamespace(last_fetch=ts))
        assert out.endswith("m ago")

    def test_hours_form(self):
        ts = (datetime.now() - timedelta(hours=2)).isoformat()
        out = self._call(SimpleNamespace(last_fetch=ts))
        assert out.endswith("h ago")
