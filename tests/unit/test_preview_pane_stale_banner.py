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
    pane._row_cache = {}
    return pane


def _build(pane):
    """The pane's rows as plain text, at a patched width of 80."""
    with patch.object(PreviewPane, "size",
                      new_callable=lambda: property(lambda self: SimpleNamespace(width=80))):
        return SimpleNamespace(plain="\n".join(row.text for row in pane._build_rows()))


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


class TestPreviewPaneCheapRendering:
    """#486: hard-wrap ourselves, reuse parsed lines, skip unchanged updates."""

    def test_long_line_is_hard_wrapped_to_the_width(self):
        from overcode.tui_widgets.preview_pane import _hard_wrap
        from rich.text import Text
        rows = _hard_wrap(Text("a" * 25), 10)
        assert [r.plain for r in rows] == ["a" * 10, "a" * 10, "a" * 5]

    def test_wide_characters_count_as_two_cells(self):
        from overcode.tui_widgets.preview_pane import _hard_wrap
        from rich.text import Text
        rows = _hard_wrap(Text("日本語テキスト"), 6)  # 7 chars, 14 cells
        assert [r.plain for r in rows] == ["日本語", "テキス", "ト"]

    def test_line_that_fits_is_returned_unsplit(self):
        from overcode.tui_widgets.preview_pane import _hard_wrap
        from rich.text import Text
        line = Text("short")
        assert _hard_wrap(line, 80) == [line]

    def test_long_content_line_appears_on_several_rows(self):
        pane = _make_pane(["x" * 200])
        lines = _build(pane).plain.splitlines()
        assert lines[1:] == ["x" * 80, "x" * 80, "x" * 40]

    def test_unchanged_lines_are_not_parsed_again(self):
        pane = _make_pane(["one", "two"])
        _build(pane)
        pane.content_lines = ["one", "two", "three"]
        with patch.object(PreviewPane, "_parse_rows", autospec=True,
                          side_effect=PreviewPane._parse_rows) as parse:
            rendered = _build(pane).plain
        assert [c.args[1] for c in parse.call_args_list] == ["three"]
        assert rendered.splitlines()[1:] == ["one", "two", "three"]



class TestPreviewPaneMounted:
    """_show and render_line on a mounted pane (#486)."""

    @staticmethod
    def _app():
        from textual.app import App

        class PreviewApp(App):
            def compose(self):
                yield PreviewPane(id="preview-pane")

        return PreviewApp()

    @staticmethod
    def _screen_text(app) -> list:
        pane = app.query_one(PreviewPane)
        return [pane.render_line(y).text.rstrip() for y in range(pane.size.height)]

    async def test_shows_the_lines_and_skips_unchanged_updates(self):
        app = self._app()
        async with app.run_test(size=(60, 10)) as pilot:
            pane = app.query_one(PreviewPane)
            pane.session_name = "agent"
            pane.content_lines = ["hello", "\x1b[31mred\x1b[0m"]
            assert pane._show() is True
            await pilot.pause()
            text = self._screen_text(app)
            assert text[0].startswith("─── agent ")
            assert text[1:3] == ["hello", "red"]
            with patch.object(PreviewPane, "_build_rows", autospec=True) as build:
                assert pane._show() is False
            build.assert_not_called()

    async def test_scrolls_to_the_latest_line(self):
        app = self._app()
        async with app.run_test(size=(60, 10)) as pilot:
            pane = app.query_one(PreviewPane)
            widget = SimpleNamespace(session=SimpleNamespace(name="agent"),
                                     pane_content=[f"line {i}" for i in range(50)])
            pane.update_from_widget(widget)
            await pilot.pause()
            await pilot.pause()
            assert self._screen_text(app)[-1] == "line 49"

    async def test_rewraps_on_resize(self):
        app = self._app()
        async with app.run_test(size=(60, 10)) as pilot:
            pane = app.query_one(PreviewPane)
            pane.content_lines = ["y" * 50]
            pane._show()
            await pilot.pause()
            assert pane.virtual_size.height == 2  # header + one row
            await pilot.resize_terminal(30, 10)
            await pilot.pause()
            assert pane.virtual_size.height == 3  # header + two rows
