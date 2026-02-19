"""
Unit tests for FullscreenPreview widget.

Tests the content building, state management, and key bindings
in isolation without requiring a running Textual application.
"""

import pytest
from unittest.mock import MagicMock, patch
from rich.text import Text
from rich.panel import Panel


# ---------------------------------------------------------------------------
# Helper to build a bare FullscreenPreview without the Textual app
# ---------------------------------------------------------------------------

def _make_bare_preview(**extra_attrs):
    """Create a FullscreenPreview bypassing __init__ for unit-testing methods.

    Textual's ``app`` is a property that uses a ContextVar, so we create a
    test subclass that overrides it with a simple attribute.
    """
    from overcode.tui_widgets.fullscreen_preview import FullscreenPreview

    class _TestableFullscreenPreview(FullscreenPreview):
        _mock_app = MagicMock()

        @property
        def app(self):
            return self._mock_app

    widget = _TestableFullscreenPreview.__new__(_TestableFullscreenPreview)
    widget._id = "fullscreen-preview"
    widget._is_mounted = False
    widget._running = False
    # Default attributes from __init__
    widget._content_lines = []
    widget._session_name = ""
    widget._monochrome = False
    widget._previous_focus = None
    widget._previous_focus_session_id = None
    # Each instance gets its own mock app
    widget._mock_app = MagicMock()
    # Apply overrides
    for k, v in extra_attrs.items():
        if k == "app":
            widget._mock_app = v
        else:
            setattr(widget, k, v)
    return widget


# ===========================================================================
# _build_content
# ===========================================================================


class TestBuildContent:
    """Tests for FullscreenPreview._build_content."""

    def test_empty_content_shows_no_output(self):
        widget = _make_bare_preview(_content_lines=[], _session_name="agent-1")
        panel = widget._build_content()
        assert isinstance(panel, Panel)
        # The renderable inside should contain "(no output)"
        renderable = panel.renderable
        assert "(no output)" in renderable.plain

    def test_single_line_content(self):
        widget = _make_bare_preview(
            _content_lines=["Hello, world!"],
            _session_name="agent-1",
        )
        panel = widget._build_content()
        renderable = panel.renderable
        assert "Hello, world!" in renderable.plain

    def test_multiple_lines_content(self):
        widget = _make_bare_preview(
            _content_lines=["line 1", "line 2", "line 3"],
            _session_name="test",
        )
        panel = widget._build_content()
        renderable = panel.renderable
        plain = renderable.plain
        assert "line 1" in plain
        assert "line 2" in plain
        assert "line 3" in plain

    def test_ansi_codes_stripped(self):
        """ANSI escape codes should be stripped from content."""
        widget = _make_bare_preview(
            _content_lines=["\x1b[32mGreen text\x1b[0m"],
            _session_name="test",
        )
        panel = widget._build_content()
        renderable = panel.renderable
        plain = renderable.plain
        assert "Green text" in plain
        # ANSI codes should be stripped
        assert "\x1b" not in plain

    def test_session_name_in_title(self):
        widget = _make_bare_preview(
            _content_lines=["content"],
            _session_name="my-agent",
        )
        panel = widget._build_content()
        # Panel title should contain session name
        assert panel.title is not None
        title_plain = panel.title.plain if hasattr(panel.title, 'plain') else str(panel.title)
        assert "my-agent" in title_plain

    def test_subtitle_contains_help(self):
        widget = _make_bare_preview(
            _content_lines=["content"],
            _session_name="test",
        )
        panel = widget._build_content()
        assert panel.subtitle is not None
        sub_plain = panel.subtitle.plain if hasattr(panel.subtitle, 'plain') else str(panel.subtitle)
        assert "Esc" in sub_plain or "close" in sub_plain

    def test_empty_session_name(self):
        widget = _make_bare_preview(
            _content_lines=["content"],
            _session_name="",
        )
        panel = widget._build_content()
        # Should not crash with empty session name
        assert isinstance(panel, Panel)

    def test_content_with_special_characters(self):
        widget = _make_bare_preview(
            _content_lines=["<html>&amp;</html>", "line with 'quotes'", 'line with "double"'],
            _session_name="test",
        )
        panel = widget._build_content()
        renderable = panel.renderable
        plain = renderable.plain
        assert "<html>" in plain
        assert "'quotes'" in plain

    def test_many_lines(self):
        """Should handle many lines without error."""
        lines = [f"line {i}" for i in range(500)]
        widget = _make_bare_preview(
            _content_lines=lines,
            _session_name="test",
        )
        panel = widget._build_content()
        renderable = panel.renderable
        plain = renderable.plain
        assert "line 0" in plain
        assert "line 499" in plain


# ===========================================================================
# State management
# ===========================================================================


class TestFullscreenPreviewState:
    """Tests for state tracking in FullscreenPreview."""

    def test_initial_state(self):
        widget = _make_bare_preview()
        assert widget._content_lines == []
        assert widget._session_name == ""
        assert widget._monochrome is False
        assert widget._previous_focus is None
        assert widget._previous_focus_session_id is None

    def test_state_after_setting_content(self):
        widget = _make_bare_preview()
        widget._content_lines = ["a", "b"]
        widget._session_name = "agent-1"
        widget._monochrome = True
        assert widget._content_lines == ["a", "b"]
        assert widget._session_name == "agent-1"
        assert widget._monochrome is True


# ===========================================================================
# hide — focus restoration logic
# ===========================================================================


class TestFullscreenPreviewHide:
    """Tests for FullscreenPreview.hide focus restoration."""

    def test_hide_clears_previous_focus(self):
        widget = _make_bare_preview()
        widget.remove_class = MagicMock()

        widget.app.query.return_value = []
        widget._previous_focus = MagicMock()
        widget._previous_focus_session_id = None

        widget.hide()

        assert widget._previous_focus is None
        assert widget._previous_focus_session_id is None
        widget.remove_class.assert_called_once_with("visible")

    def test_hide_restores_focus_by_session_id(self):
        widget = _make_bare_preview()
        widget.remove_class = MagicMock()

        # Mock a SessionSummary widget with matching session ID
        mock_session_widget = MagicMock()
        mock_session_widget.session.id = "sess-123"

        widget.app.query.return_value = [mock_session_widget]

        widget._previous_focus_session_id = "sess-123"
        widget._previous_focus = MagicMock()

        widget.hide()

        mock_session_widget.focus.assert_called_once()
        assert widget._previous_focus is None

    def test_hide_falls_back_to_previous_focus(self):
        widget = _make_bare_preview()
        widget.remove_class = MagicMock()

        widget.app.query.return_value = []

        prev_focus = MagicMock()
        widget._previous_focus = prev_focus
        widget._previous_focus_session_id = None

        widget.hide()

        prev_focus.focus.assert_called_once()

    def test_hide_handles_focus_error_gracefully(self):
        widget = _make_bare_preview()
        widget.remove_class = MagicMock()

        widget.app.query.return_value = []

        prev_focus = MagicMock()
        prev_focus.focus.side_effect = Exception("widget destroyed")
        widget._previous_focus = prev_focus
        widget._previous_focus_session_id = None

        # Should not raise
        widget.hide()
        assert widget._previous_focus is None


# ===========================================================================
# on_key
# ===========================================================================


class TestFullscreenPreviewOnKey:
    """Tests for FullscreenPreview.on_key handling."""

    def test_escape_calls_hide(self):
        widget = _make_bare_preview()
        widget.hide = MagicMock()
        event = MagicMock()
        event.key = "escape"
        widget.on_key(event)
        widget.hide.assert_called_once()
        event.stop.assert_called_once()

    def test_f_calls_hide(self):
        widget = _make_bare_preview()
        widget.hide = MagicMock()
        event = MagicMock()
        event.key = "f"
        widget.on_key(event)
        widget.hide.assert_called_once()
        event.stop.assert_called_once()

    def test_q_calls_hide(self):
        widget = _make_bare_preview()
        widget.hide = MagicMock()
        event = MagicMock()
        event.key = "q"
        widget.on_key(event)
        widget.hide.assert_called_once()
        event.stop.assert_called_once()

    def test_other_key_does_not_hide(self):
        widget = _make_bare_preview()
        widget.hide = MagicMock()
        event = MagicMock()
        event.key = "a"
        widget.on_key(event)
        widget.hide.assert_not_called()
        event.stop.assert_not_called()

    def test_up_key_does_not_hide(self):
        widget = _make_bare_preview()
        widget.hide = MagicMock()
        event = MagicMock()
        event.key = "up"
        widget.on_key(event)
        widget.hide.assert_not_called()

    def test_enter_key_does_not_hide(self):
        widget = _make_bare_preview()
        widget.hide = MagicMock()
        event = MagicMock()
        event.key = "enter"
        widget.on_key(event)
        widget.hide.assert_not_called()


# ===========================================================================
# Bindings
# ===========================================================================


class TestFullscreenPreviewBindings:
    """Tests for FullscreenPreview key bindings definition."""

    def test_bindings_defined(self):
        from overcode.tui_widgets.fullscreen_preview import FullscreenPreview
        binding_keys = [b.key for b in FullscreenPreview.BINDINGS]
        assert "up" in binding_keys
        assert "down" in binding_keys
        assert "k" in binding_keys
        assert "j" in binding_keys
        assert "pageup" in binding_keys
        assert "pagedown" in binding_keys
        assert "home" in binding_keys
        assert "end" in binding_keys

    def test_scroll_actions_exist(self):
        """Verify the scroll action methods exist on the class."""
        from overcode.tui_widgets.fullscreen_preview import FullscreenPreview
        assert hasattr(FullscreenPreview, 'action_scroll_up_20')
        assert hasattr(FullscreenPreview, 'action_scroll_down_20')
