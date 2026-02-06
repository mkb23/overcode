"""
Unit tests for TUI view actions.

Tests display settings, toggles, and visual modes that can be
isolated from the full TUI.
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock


class TestToggleTimeline:
    """Test action_toggle_timeline method."""

    def test_hides_visible_timeline(self):
        """Should hide the timeline when it is currently displayed."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_timeline = MagicMock()
        mock_timeline.display = True

        mock_tui = MagicMock()
        mock_tui.query_one.return_value = mock_timeline
        mock_tui._prefs = MagicMock()

        ViewActionsMixin.action_toggle_timeline(mock_tui)

        assert mock_timeline.display is False
        assert mock_tui._prefs.timeline_visible is False
        mock_tui._save_prefs.assert_called_once()
        assert "hidden" in mock_tui.notify.call_args[0][0]

    def test_shows_hidden_timeline(self):
        """Should show the timeline when it is currently hidden."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_timeline = MagicMock()
        mock_timeline.display = False

        mock_tui = MagicMock()
        mock_tui.query_one.return_value = mock_timeline
        mock_tui._prefs = MagicMock()

        ViewActionsMixin.action_toggle_timeline(mock_tui)

        assert mock_timeline.display is True
        assert mock_tui._prefs.timeline_visible is True
        mock_tui._save_prefs.assert_called_once()
        assert "shown" in mock_tui.notify.call_args[0][0]

    def test_handles_no_matches(self):
        """Should handle NoMatches gracefully when timeline is not found."""
        from overcode.tui_actions.view import ViewActionsMixin
        from textual.css.query import NoMatches

        mock_tui = MagicMock()
        mock_tui.query_one.side_effect = NoMatches()

        # Should not raise
        ViewActionsMixin.action_toggle_timeline(mock_tui)
        mock_tui.notify.assert_not_called()


class TestToggleHelp:
    """Test action_toggle_help method."""

    def test_shows_help_when_hidden(self):
        """Should add visible class when help overlay is hidden."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_overlay = MagicMock()
        mock_overlay.has_class.return_value = False

        mock_tui = MagicMock()
        mock_tui.query_one.return_value = mock_overlay

        ViewActionsMixin.action_toggle_help(mock_tui)

        mock_overlay.add_class.assert_called_once_with("visible")
        mock_overlay.remove_class.assert_not_called()

    def test_hides_help_when_visible(self):
        """Should remove visible class when help overlay is shown."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_overlay = MagicMock()
        mock_overlay.has_class.return_value = True

        mock_tui = MagicMock()
        mock_tui.query_one.return_value = mock_overlay

        ViewActionsMixin.action_toggle_help(mock_tui)

        mock_overlay.remove_class.assert_called_once_with("visible")
        mock_overlay.add_class.assert_not_called()

    def test_handles_no_matches(self):
        """Should handle NoMatches gracefully when overlay is not found."""
        from overcode.tui_actions.view import ViewActionsMixin
        from textual.css.query import NoMatches

        mock_tui = MagicMock()
        mock_tui.query_one.side_effect = NoMatches()

        # Should not raise
        ViewActionsMixin.action_toggle_help(mock_tui)


class TestManualRefresh:
    """Test action_manual_refresh method."""

    def test_calls_all_refresh_methods(self):
        """Should call all four refresh methods and notify."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()

        ViewActionsMixin.action_manual_refresh(mock_tui)

        mock_tui.refresh_sessions.assert_called_once()
        mock_tui.update_all_statuses.assert_called_once()
        mock_tui.update_daemon_status.assert_called_once()
        mock_tui.update_timeline.assert_called_once()
        mock_tui.notify.assert_called_once()
        assert "Refreshed" in mock_tui.notify.call_args[0][0]


class TestToggleExpandAll:
    """Test action_toggle_expand_all method."""

    def test_collapses_all_when_any_expanded(self):
        """Should collapse all widgets when at least one is expanded."""
        from overcode.tui_actions.view import ViewActionsMixin

        widget1 = MagicMock()
        widget1.expanded = True
        widget1.session.id = "s1"

        widget2 = MagicMock()
        widget2.expanded = False
        widget2.session.id = "s2"

        mock_tui = MagicMock()
        mock_tui.query.return_value = [widget1, widget2]
        mock_tui.expanded_states = {}

        ViewActionsMixin.action_toggle_expand_all(mock_tui)

        # When any are expanded, new_state = not True = False
        assert widget1.expanded is False
        assert widget2.expanded is False
        assert mock_tui.expanded_states["s1"] is False
        assert mock_tui.expanded_states["s2"] is False

    def test_expands_all_when_none_expanded(self):
        """Should expand all widgets when none are expanded."""
        from overcode.tui_actions.view import ViewActionsMixin

        widget1 = MagicMock()
        widget1.expanded = False
        widget1.session.id = "s1"

        widget2 = MagicMock()
        widget2.expanded = False
        widget2.session.id = "s2"

        mock_tui = MagicMock()
        mock_tui.query.return_value = [widget1, widget2]
        mock_tui.expanded_states = {}

        ViewActionsMixin.action_toggle_expand_all(mock_tui)

        assert widget1.expanded is True
        assert widget2.expanded is True

    def test_noop_with_no_widgets(self):
        """Should do nothing when there are no session widgets."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui.query.return_value = []

        # Should not raise
        ViewActionsMixin.action_toggle_expand_all(mock_tui)


class TestCycleDetail:
    """Test action_cycle_detail method."""

    def test_cycles_to_next_detail_level(self):
        """Should cycle detail_level_index and update widgets."""
        from overcode.tui_actions.view import ViewActionsMixin

        widget1 = MagicMock()
        widget2 = MagicMock()

        mock_tui = MagicMock()
        mock_tui.detail_level_index = 0
        mock_tui.DETAIL_LEVELS = [5, 10, 20, 50]
        mock_tui.query.return_value = [widget1, widget2]
        mock_tui._prefs = MagicMock()

        ViewActionsMixin.action_cycle_detail(mock_tui)

        assert mock_tui.detail_level_index == 1
        assert widget1.detail_lines == 10
        assert widget2.detail_lines == 10
        assert mock_tui._prefs.detail_lines == 10
        mock_tui._save_prefs.assert_called_once()
        assert "10 lines" in mock_tui.notify.call_args[0][0]

    def test_wraps_around_at_end(self):
        """Should wrap back to index 0 when at the last level."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui.detail_level_index = 3  # Last index
        mock_tui.DETAIL_LEVELS = [5, 10, 20, 50]
        mock_tui.query.return_value = []
        mock_tui._prefs = MagicMock()

        ViewActionsMixin.action_cycle_detail(mock_tui)

        assert mock_tui.detail_level_index == 0
        assert mock_tui._prefs.detail_lines == 5


class TestCycleSummary:
    """Test action_cycle_summary method."""

    def test_cycles_to_next_summary_level(self):
        """Should cycle summary_level_index and update widgets."""
        from overcode.tui_actions.view import ViewActionsMixin

        widget1 = MagicMock()

        mock_tui = MagicMock()
        mock_tui.summary_level_index = 0
        mock_tui.SUMMARY_LEVELS = ["low", "med", "full", "custom"]
        mock_tui.query.return_value = [widget1]
        mock_tui._prefs = MagicMock()

        ViewActionsMixin.action_cycle_summary(mock_tui)

        assert mock_tui.summary_level_index == 1
        assert widget1.summary_detail == "med"
        assert mock_tui._prefs.summary_detail == "med"
        mock_tui._save_prefs.assert_called_once()
        assert "med" in mock_tui.notify.call_args[0][0]

    def test_wraps_around_at_end(self):
        """Should wrap back to index 0 when at the last level."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui.summary_level_index = 3  # Last (custom)
        mock_tui.SUMMARY_LEVELS = ["low", "med", "full", "custom"]
        mock_tui.query.return_value = []
        mock_tui._prefs = MagicMock()

        ViewActionsMixin.action_cycle_summary(mock_tui)

        assert mock_tui.summary_level_index == 0
        assert mock_tui._prefs.summary_detail == "low"


class TestCycleSummaryContent:
    """Test action_cycle_summary_content method."""

    def test_cycles_to_next_content_mode(self):
        """Should cycle summary_content_mode and update widgets."""
        from overcode.tui_actions.view import ViewActionsMixin

        widget1 = MagicMock()

        mock_tui = MagicMock()
        mock_tui.summary_content_mode = "ai_short"
        mock_tui.SUMMARY_CONTENT_MODES = [
            "ai_short", "ai_long", "orders", "annotation", "heartbeat"
        ]
        mock_tui.query.return_value = [widget1]
        mock_tui._prefs = MagicMock()

        ViewActionsMixin.action_cycle_summary_content(mock_tui)

        assert mock_tui.summary_content_mode == "ai_long"
        assert widget1.summary_content_mode == "ai_long"
        assert mock_tui._prefs.summary_content_mode == "ai_long"
        mock_tui._save_prefs.assert_called_once()
        assert "AI Summary (context)" in mock_tui.notify.call_args[0][0]

    def test_wraps_around_at_end(self):
        """Should wrap back to first mode when at the end."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui.summary_content_mode = "heartbeat"
        mock_tui.SUMMARY_CONTENT_MODES = [
            "ai_short", "ai_long", "orders", "annotation", "heartbeat"
        ]
        mock_tui.query.return_value = []
        mock_tui._prefs = MagicMock()

        ViewActionsMixin.action_cycle_summary_content(mock_tui)

        assert mock_tui.summary_content_mode == "ai_short"

    def test_handles_unknown_current_mode(self):
        """Should start from index 0 if current mode is not in the list."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui.summary_content_mode = "nonexistent"
        mock_tui.SUMMARY_CONTENT_MODES = [
            "ai_short", "ai_long", "orders", "annotation", "heartbeat"
        ]
        mock_tui.query.return_value = []
        mock_tui._prefs = MagicMock()

        ViewActionsMixin.action_cycle_summary_content(mock_tui)

        # index 0 -> next is index 1
        assert mock_tui.summary_content_mode == "ai_long"


class TestToggleViewMode:
    """Test action_toggle_view_mode method."""

    def test_switches_from_tree_to_list_preview(self):
        """Should switch from tree to list_preview mode."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui.view_mode = "tree"
        mock_tui._prefs = MagicMock()

        ViewActionsMixin.action_toggle_view_mode(mock_tui)

        assert mock_tui.view_mode == "list_preview"
        assert mock_tui._prefs.view_mode == "list_preview"
        mock_tui._save_prefs.assert_called_once()

    def test_switches_from_list_preview_to_tree(self):
        """Should switch from list_preview to tree mode."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui.view_mode = "list_preview"
        mock_tui._prefs = MagicMock()

        ViewActionsMixin.action_toggle_view_mode(mock_tui)

        assert mock_tui.view_mode == "tree"
        assert mock_tui._prefs.view_mode == "tree"
        mock_tui._save_prefs.assert_called_once()


class TestToggleTmuxSync:
    """Test action_toggle_tmux_sync method."""

    def test_enables_tmux_sync(self):
        """Should enable tmux sync and trigger immediate sync."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui.tmux_sync = False
        mock_tui._prefs = MagicMock()

        ViewActionsMixin.action_toggle_tmux_sync(mock_tui)

        assert mock_tui.tmux_sync is True
        assert mock_tui._prefs.tmux_sync is True
        mock_tui._save_prefs.assert_called_once()
        mock_tui._update_subtitle.assert_called_once()
        mock_tui._sync_tmux_window.assert_called_once()

    def test_disables_tmux_sync(self):
        """Should disable tmux sync and not trigger immediate sync."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui.tmux_sync = True
        mock_tui._prefs = MagicMock()

        ViewActionsMixin.action_toggle_tmux_sync(mock_tui)

        assert mock_tui.tmux_sync is False
        assert mock_tui._prefs.tmux_sync is False
        mock_tui._save_prefs.assert_called_once()
        mock_tui._update_subtitle.assert_called_once()
        # Should NOT call _sync_tmux_window when disabling
        mock_tui._sync_tmux_window.assert_not_called()


class TestToggleShowTerminated:
    """Test action_toggle_show_terminated method."""

    def test_enables_show_terminated(self):
        """Should toggle show_terminated on and refresh widgets."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui.show_terminated = False
        mock_tui._terminated_sessions = ["s1", "s2"]
        mock_tui._prefs = MagicMock()

        ViewActionsMixin.action_toggle_show_terminated(mock_tui)

        assert mock_tui.show_terminated is True
        assert mock_tui._prefs.show_terminated is True
        mock_tui._save_prefs.assert_called_once()
        mock_tui.update_session_widgets.assert_called_once()
        assert "visible" in mock_tui.notify.call_args[0][0]
        assert "2" in mock_tui.notify.call_args[0][0]

    def test_disables_show_terminated(self):
        """Should toggle show_terminated off and refresh widgets."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui.show_terminated = True
        mock_tui._terminated_sessions = []
        mock_tui._prefs = MagicMock()

        ViewActionsMixin.action_toggle_show_terminated(mock_tui)

        assert mock_tui.show_terminated is False
        assert mock_tui._prefs.show_terminated is False
        assert "hidden" in mock_tui.notify.call_args[0][0]


class TestToggleHideAsleep:
    """Test action_toggle_hide_asleep method."""

    def test_enables_hide_asleep(self):
        """Should toggle hide_asleep on and show count."""
        from overcode.tui_actions.view import ViewActionsMixin

        session1 = MagicMock()
        session1.is_asleep = True
        session2 = MagicMock()
        session2.is_asleep = False

        mock_tui = MagicMock()
        mock_tui.hide_asleep = False
        mock_tui.sessions = [session1, session2]
        mock_tui._prefs = MagicMock()

        ViewActionsMixin.action_toggle_hide_asleep(mock_tui)

        assert mock_tui.hide_asleep is True
        assert mock_tui._prefs.hide_asleep is True
        mock_tui._save_prefs.assert_called_once()
        mock_tui._update_subtitle.assert_called_once()
        mock_tui.update_session_widgets.assert_called_once()
        assert "hidden" in mock_tui.notify.call_args[0][0]
        assert "1" in mock_tui.notify.call_args[0][0]

    def test_disables_hide_asleep(self):
        """Should toggle hide_asleep off and show visible count."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui.hide_asleep = True
        mock_tui.sessions = []
        mock_tui._prefs = MagicMock()

        ViewActionsMixin.action_toggle_hide_asleep(mock_tui)

        assert mock_tui.hide_asleep is False
        assert "visible" in mock_tui.notify.call_args[0][0]


class TestCycleSortMode:
    """Test action_cycle_sort_mode method."""

    @patch("overcode.tui_logic.get_sort_mode_display_name", return_value="By Status")
    @patch("overcode.tui_logic.cycle_sort_mode", return_value="by_status")
    def test_cycles_sort_mode(self, mock_cycle, mock_display_name):
        """Should cycle sort mode and refresh."""
        from overcode.tui_actions.view import ViewActionsMixin

        widget1 = MagicMock()
        widget1.session.id = "s1"

        mock_tui = MagicMock()
        mock_tui._prefs = MagicMock()
        mock_tui._prefs.sort_mode = "alphabetical"
        mock_tui.SORT_MODES = ["alphabetical", "by_status", "by_value"]
        mock_tui.focused_session_index = 0
        mock_tui._get_widgets_in_session_order.return_value = [widget1]

        ViewActionsMixin.action_cycle_sort_mode(mock_tui)

        mock_cycle.assert_called_once_with("alphabetical", ["alphabetical", "by_status", "by_value"])
        assert mock_tui._prefs.sort_mode == "by_status"
        mock_tui._save_prefs.assert_called_once()
        mock_tui._sort_sessions.assert_called_once()
        mock_tui.update_session_widgets.assert_called_once()
        mock_tui._update_subtitle.assert_called_once()
        assert "By Status" in mock_tui.notify.call_args[0][0]

    @patch("overcode.tui_logic.get_sort_mode_display_name", return_value="By Status")
    @patch("overcode.tui_logic.cycle_sort_mode", return_value="by_status")
    def test_follows_focused_session_after_sort(self, mock_cycle, mock_display_name):
        """Should update focused_session_index to track the same session."""
        from overcode.tui_actions.view import ViewActionsMixin

        widget_a = MagicMock()
        widget_a.session.id = "s-alpha"
        widget_b = MagicMock()
        widget_b.session.id = "s-beta"

        mock_tui = MagicMock()
        mock_tui._prefs = MagicMock()
        mock_tui._prefs.sort_mode = "alphabetical"
        mock_tui.SORT_MODES = ["alphabetical", "by_status", "by_value"]
        mock_tui.focused_session_index = 0

        # Before sort: [alpha, beta]; after sort: [beta, alpha]
        mock_tui._get_widgets_in_session_order.side_effect = [
            [widget_a, widget_b],  # First call (before sort)
            [widget_b, widget_a],  # Second call (after sort)
        ]

        ViewActionsMixin.action_cycle_sort_mode(mock_tui)

        # Originally focused on s-alpha at index 0, now at index 1
        assert mock_tui.focused_session_index == 1


class TestBaselineBack:
    """Test action_baseline_back method."""

    def test_increments_by_15(self):
        """Should increment baseline_minutes by 15."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui.baseline_minutes = 30
        mock_tui._prefs = MagicMock()

        ViewActionsMixin.action_baseline_back(mock_tui)

        assert mock_tui.baseline_minutes == 45
        assert mock_tui._prefs.baseline_minutes == 45
        mock_tui._save_prefs.assert_called_once()
        mock_tui._notify_baseline_change.assert_called_once()

    def test_caps_at_180(self):
        """Should not exceed 180 minutes (3 hours)."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui.baseline_minutes = 170
        mock_tui._prefs = MagicMock()

        ViewActionsMixin.action_baseline_back(mock_tui)

        assert mock_tui.baseline_minutes == 180

    def test_already_at_max(self):
        """Should stay at 180 when already at max."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui.baseline_minutes = 180
        mock_tui._prefs = MagicMock()

        ViewActionsMixin.action_baseline_back(mock_tui)

        assert mock_tui.baseline_minutes == 180


class TestBaselineForward:
    """Test action_baseline_forward method."""

    def test_decrements_by_15(self):
        """Should decrement baseline_minutes by 15."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui.baseline_minutes = 45
        mock_tui._prefs = MagicMock()

        ViewActionsMixin.action_baseline_forward(mock_tui)

        assert mock_tui.baseline_minutes == 30
        assert mock_tui._prefs.baseline_minutes == 30
        mock_tui._save_prefs.assert_called_once()
        mock_tui._notify_baseline_change.assert_called_once()

    def test_caps_at_zero(self):
        """Should not go below 0."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui.baseline_minutes = 10
        mock_tui._prefs = MagicMock()

        ViewActionsMixin.action_baseline_forward(mock_tui)

        assert mock_tui.baseline_minutes == 0


class TestBaselineReset:
    """Test action_baseline_reset method."""

    def test_resets_to_zero(self):
        """Should reset baseline_minutes to 0."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui.baseline_minutes = 90
        mock_tui._prefs = MagicMock()

        ViewActionsMixin.action_baseline_reset(mock_tui)

        assert mock_tui.baseline_minutes == 0
        assert mock_tui._prefs.baseline_minutes == 0
        mock_tui._save_prefs.assert_called_once()
        mock_tui._notify_baseline_change.assert_called_once()


class TestNotifyBaselineChange:
    """Test _notify_baseline_change method."""

    def test_label_now(self):
        """Should show 'now' when baseline is 0."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui.baseline_minutes = 0

        ViewActionsMixin._notify_baseline_change(mock_tui)

        assert "now" in mock_tui.notify.call_args[0][0]
        mock_tui.update_daemon_status.assert_called_once()
        mock_tui.update_timeline.assert_called_once()

    def test_label_minutes(self):
        """Should show '-30m' for 30 minutes."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui.baseline_minutes = 30

        ViewActionsMixin._notify_baseline_change(mock_tui)

        assert "-30m" in mock_tui.notify.call_args[0][0]

    def test_label_exact_hours(self):
        """Should show '-2h' for 120 minutes."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui.baseline_minutes = 120

        ViewActionsMixin._notify_baseline_change(mock_tui)

        assert "-2h" in mock_tui.notify.call_args[0][0]
        # Ensure it does NOT show minutes portion
        assert "m" not in mock_tui.notify.call_args[0][0].replace("Baseline:", "").replace("-2h", "")

    def test_label_hours_and_minutes(self):
        """Should show '-1h30m' for 90 minutes."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui.baseline_minutes = 90

        ViewActionsMixin._notify_baseline_change(mock_tui)

        assert "-1h30m" in mock_tui.notify.call_args[0][0]

    def test_label_three_hours(self):
        """Should show '-3h' for 180 minutes."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui.baseline_minutes = 180

        ViewActionsMixin._notify_baseline_change(mock_tui)

        assert "-3h" in mock_tui.notify.call_args[0][0]


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
