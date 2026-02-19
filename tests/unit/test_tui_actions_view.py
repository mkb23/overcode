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
    def test_delegates_focus_tracking_to_update_session_widgets(self, mock_cycle, mock_display_name):
        """Should delegate focus tracking to update_session_widgets (preserve_focus)."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui._prefs = MagicMock()
        mock_tui._prefs.sort_mode = "alphabetical"
        mock_tui.SORT_MODES = ["alphabetical", "by_status", "by_value"]
        mock_tui.focused_session_index = 0

        ViewActionsMixin.action_cycle_sort_mode(mock_tui)

        # Focus tracking is now handled internally by update_session_widgets
        mock_tui.update_session_widgets.assert_called_once()
        mock_tui._sort_sessions.assert_called_once()


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


class TestToggleShowDone:
    """Test action_toggle_show_done method."""

    def test_enables_show_done(self):
        """Should toggle show_done on, save prefs, and refresh widgets."""
        from overcode.tui_actions.view import ViewActionsMixin

        session1 = MagicMock()
        session1.status = "done"
        session2 = MagicMock()
        session2.status = "active"
        session3 = MagicMock()
        session3.status = "done"

        mock_tui = MagicMock()
        mock_tui.show_done = False
        mock_tui.sessions = [session1, session2, session3]
        mock_tui._prefs = MagicMock()

        ViewActionsMixin.action_toggle_show_done(mock_tui)

        assert mock_tui.show_done is True
        assert mock_tui._prefs.show_done is True
        mock_tui._save_prefs.assert_called_once()
        mock_tui.update_session_widgets.assert_called_once()
        assert "visible" in mock_tui.notify.call_args[0][0]
        assert "2" in mock_tui.notify.call_args[0][0]

    def test_disables_show_done(self):
        """Should toggle show_done off, save prefs, and refresh widgets."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui.show_done = True
        mock_tui.sessions = []
        mock_tui._prefs = MagicMock()

        ViewActionsMixin.action_toggle_show_done(mock_tui)

        assert mock_tui.show_done is False
        assert mock_tui._prefs.show_done is False
        mock_tui._save_prefs.assert_called_once()
        mock_tui.update_session_widgets.assert_called_once()
        assert "hidden" in mock_tui.notify.call_args[0][0]

    def test_shows_count_when_done_agents_exist(self):
        """Should include the count of done agents in notification."""
        from overcode.tui_actions.view import ViewActionsMixin

        session1 = MagicMock()
        session1.status = "done"

        mock_tui = MagicMock()
        mock_tui.show_done = False
        mock_tui.sessions = [session1]
        mock_tui._prefs = MagicMock()

        ViewActionsMixin.action_toggle_show_done(mock_tui)

        notify_msg = mock_tui.notify.call_args[0][0]
        assert "(1)" in notify_msg

    def test_no_count_when_no_done_agents(self):
        """Should not include count in parentheses when no done agents exist."""
        from overcode.tui_actions.view import ViewActionsMixin

        session1 = MagicMock()
        session1.status = "active"

        mock_tui = MagicMock()
        mock_tui.show_done = False
        mock_tui.sessions = [session1]
        mock_tui._prefs = MagicMock()

        ViewActionsMixin.action_toggle_show_done(mock_tui)

        notify_msg = mock_tui.notify.call_args[0][0]
        assert "visible" in notify_msg
        assert "(" not in notify_msg


class TestToggleCollapseChildren:
    """Test action_toggle_collapse_children method."""

    def test_warns_when_not_tree_mode(self):
        """Should warn and return early if sort mode is not by_tree."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui._prefs = MagicMock()
        mock_tui._prefs.sort_mode = "alphabetical"

        ViewActionsMixin.action_toggle_collapse_children(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "tree sort mode" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "warning"
        mock_tui._get_focused_widget.assert_not_called()

    def test_returns_early_when_no_focused_widget(self):
        """Should return early if no widget is focused."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui._prefs = MagicMock()
        mock_tui._prefs.sort_mode = "by_tree"
        mock_tui._get_focused_widget.return_value = None

        ViewActionsMixin.action_toggle_collapse_children(mock_tui)

        mock_tui._get_focused_widget.assert_called_once()
        mock_tui.notify.assert_not_called()

    def test_collapse_parent_with_children(self):
        """Should collapse children when focused on a parent with children."""
        from overcode.tui_actions.view import ViewActionsMixin

        parent_session = MagicMock()
        parent_session.id = "parent-1"
        parent_session.name = "parent-agent"
        parent_session.parent_session_id = None

        child_session = MagicMock()
        child_session.id = "child-1"
        child_session.parent_session_id = "parent-1"

        focused_widget = MagicMock()
        focused_widget.session = parent_session

        parent_widget = MagicMock()
        parent_widget.session = parent_session

        mock_tui = MagicMock()
        mock_tui._prefs = MagicMock()
        mock_tui._prefs.sort_mode = "by_tree"
        mock_tui._get_focused_widget.return_value = focused_widget
        mock_tui.sessions = [parent_session, child_session]
        mock_tui.collapsed_parents = set()
        mock_tui._get_widgets_in_session_order.return_value = [parent_widget]

        ViewActionsMixin.action_toggle_collapse_children(mock_tui)

        assert "parent-1" in mock_tui.collapsed_parents
        assert "Collapsed" in mock_tui.notify.call_args[0][0]
        assert "parent-agent" in mock_tui.notify.call_args[0][0]
        mock_tui._sort_sessions.assert_called_once()
        mock_tui.update_session_widgets.assert_called_once()

    def test_expand_previously_collapsed_parent(self):
        """Should expand children when focused on a collapsed parent."""
        from overcode.tui_actions.view import ViewActionsMixin

        parent_session = MagicMock()
        parent_session.id = "parent-1"
        parent_session.name = "parent-agent"
        parent_session.parent_session_id = None

        child_session = MagicMock()
        child_session.id = "child-1"
        child_session.parent_session_id = "parent-1"

        focused_widget = MagicMock()
        focused_widget.session = parent_session

        mock_tui = MagicMock()
        mock_tui._prefs = MagicMock()
        mock_tui._prefs.sort_mode = "by_tree"
        mock_tui._get_focused_widget.return_value = focused_widget
        mock_tui.sessions = [parent_session, child_session]
        mock_tui.collapsed_parents = {"parent-1"}

        ViewActionsMixin.action_toggle_collapse_children(mock_tui)

        assert "parent-1" not in mock_tui.collapsed_parents
        assert "Expanded" in mock_tui.notify.call_args[0][0]
        assert "parent-agent" in mock_tui.notify.call_args[0][0]
        mock_tui._sort_sessions.assert_called_once()
        mock_tui.update_session_widgets.assert_called_once()

    def test_focused_on_child_walks_up_to_parent(self):
        """Should walk up to parent when focused on a child session."""
        from overcode.tui_actions.view import ViewActionsMixin

        parent_session = MagicMock()
        parent_session.id = "parent-1"
        parent_session.name = "parent-agent"
        parent_session.parent_session_id = None

        child_session = MagicMock()
        child_session.id = "child-1"
        child_session.parent_session_id = "parent-1"

        focused_widget = MagicMock()
        focused_widget.session = child_session

        parent_widget = MagicMock()
        parent_widget.session = parent_session

        mock_tui = MagicMock()
        mock_tui._prefs = MagicMock()
        mock_tui._prefs.sort_mode = "by_tree"
        mock_tui._get_focused_widget.return_value = focused_widget
        mock_tui.sessions = [parent_session, child_session]
        mock_tui.collapsed_parents = set()
        mock_tui._get_widgets_in_session_order.return_value = [parent_widget]

        ViewActionsMixin.action_toggle_collapse_children(mock_tui)

        # Should collapse the parent, not the child
        assert "parent-1" in mock_tui.collapsed_parents
        assert "parent-agent" in mock_tui.notify.call_args[0][0]

    def test_no_children_notifies_warning(self):
        """Should notify warning when focused session has no children and no parent."""
        from overcode.tui_actions.view import ViewActionsMixin

        lone_session = MagicMock()
        lone_session.id = "lone-1"
        lone_session.parent_session_id = None

        focused_widget = MagicMock()
        focused_widget.session = lone_session

        mock_tui = MagicMock()
        mock_tui._prefs = MagicMock()
        mock_tui._prefs.sort_mode = "by_tree"
        mock_tui._get_focused_widget.return_value = focused_widget
        mock_tui.sessions = [lone_session]

        ViewActionsMixin.action_toggle_collapse_children(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "No children" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "warning"
        mock_tui._sort_sessions.assert_not_called()

    def test_collapse_moves_focus_to_parent(self):
        """Should move focused_session_index to the parent widget when collapsing."""
        from overcode.tui_actions.view import ViewActionsMixin

        parent_session = MagicMock()
        parent_session.id = "parent-1"
        parent_session.name = "parent-agent"
        parent_session.parent_session_id = None

        child_session = MagicMock()
        child_session.id = "child-1"
        child_session.parent_session_id = "parent-1"

        focused_widget = MagicMock()
        focused_widget.session = parent_session

        # Parent is at index 2 in the widget list
        other_widget = MagicMock()
        other_widget.session.id = "other-1"
        parent_widget = MagicMock()
        parent_widget.session.id = "parent-1"

        mock_tui = MagicMock()
        mock_tui._prefs = MagicMock()
        mock_tui._prefs.sort_mode = "by_tree"
        mock_tui._get_focused_widget.return_value = focused_widget
        mock_tui.sessions = [parent_session, child_session]
        mock_tui.collapsed_parents = set()
        mock_tui._get_widgets_in_session_order.return_value = [other_widget, parent_widget]
        mock_tui.focused_session_index = 5

        ViewActionsMixin.action_toggle_collapse_children(mock_tui)

        assert mock_tui.focused_session_index == 1  # index of parent_widget

    def test_child_walks_up_and_collapses_siblings(self):
        """Should walk up to parent and collapse when child has parent_session_id set."""
        from overcode.tui_actions.view import ViewActionsMixin

        # Child whose parent_session_id points to a parent not in sessions,
        # but the child itself is a "child of" that parent, so it counts as children
        child_session = MagicMock()
        child_session.id = "child-1"
        child_session.parent_session_id = "missing-parent"

        focused_widget = MagicMock()
        focused_widget.session = child_session

        parent_widget = MagicMock()
        parent_widget.session.id = "missing-parent"

        mock_tui = MagicMock()
        mock_tui._prefs = MagicMock()
        mock_tui._prefs.sort_mode = "by_tree"
        mock_tui._get_focused_widget.return_value = focused_widget
        mock_tui.sessions = [child_session]
        mock_tui.collapsed_parents = set()
        mock_tui._get_widgets_in_session_order.return_value = [parent_widget]

        ViewActionsMixin.action_toggle_collapse_children(mock_tui)

        # Walks up to "missing-parent" and collapses it (child_session is its child)
        assert "missing-parent" in mock_tui.collapsed_parents
        assert "Collapsed" in mock_tui.notify.call_args[0][0]
        mock_tui._sort_sessions.assert_called_once()
        mock_tui.update_session_widgets.assert_called_once()


class TestCycleTimelineHours:
    """Test action_cycle_timeline_hours method."""

    def test_cycles_through_presets(self):
        """Should cycle from current preset to the next one."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_timeline = MagicMock()

        mock_tui = MagicMock()
        mock_tui.TIMELINE_PRESETS = [1, 3, 6, 12, 24]
        mock_tui._prefs = MagicMock()
        mock_tui._prefs.timeline_hours = 1
        mock_tui.query_one.return_value = mock_timeline
        mock_tui.sessions = []

        ViewActionsMixin.action_cycle_timeline_hours(mock_tui)

        assert mock_tui._prefs.timeline_hours == 3
        mock_tui._save_prefs.assert_called_once()
        mock_timeline.set_hours.assert_called_once_with(3, [])
        assert "3h" in mock_tui.notify.call_args[0][0]

    def test_wraps_around_at_end(self):
        """Should wrap from 24h back to 1h."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_timeline = MagicMock()

        mock_tui = MagicMock()
        mock_tui.TIMELINE_PRESETS = [1, 3, 6, 12, 24]
        mock_tui._prefs = MagicMock()
        mock_tui._prefs.timeline_hours = 24
        mock_tui.query_one.return_value = mock_timeline
        mock_tui.sessions = []

        ViewActionsMixin.action_cycle_timeline_hours(mock_tui)

        assert mock_tui._prefs.timeline_hours == 1
        assert "1h" in mock_tui.notify.call_args[0][0]

    def test_snaps_unknown_value_to_first_preset(self):
        """Should snap to index 0 and cycle to next if current is not a preset."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_timeline = MagicMock()

        mock_tui = MagicMock()
        mock_tui.TIMELINE_PRESETS = [1, 3, 6, 12, 24]
        mock_tui._prefs = MagicMock()
        mock_tui._prefs.timeline_hours = 7  # Not in presets
        mock_tui.query_one.return_value = mock_timeline
        mock_tui.sessions = []

        ViewActionsMixin.action_cycle_timeline_hours(mock_tui)

        # idx falls back to 0, next = 1 -> presets[1] = 3
        assert mock_tui._prefs.timeline_hours == 3

    def test_handles_timeline_query_exception(self):
        """Should not raise if timeline widget query fails."""
        from overcode.tui_actions.view import ViewActionsMixin
        from textual.css.query import NoMatches

        mock_tui = MagicMock()
        mock_tui.TIMELINE_PRESETS = [1, 3, 6, 12, 24]
        mock_tui._prefs = MagicMock()
        mock_tui._prefs.timeline_hours = 6
        mock_tui.query_one.side_effect = NoMatches()
        mock_tui.sessions = []

        ViewActionsMixin.action_cycle_timeline_hours(mock_tui)

        assert mock_tui._prefs.timeline_hours == 12
        mock_tui._save_prefs.assert_called_once()
        # Notification still sent even if widget not found
        mock_tui.notify.assert_called_once()

    def test_cycles_mid_preset(self):
        """Should cycle correctly from a mid-range preset (6h -> 12h)."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_timeline = MagicMock()

        mock_tui = MagicMock()
        mock_tui.TIMELINE_PRESETS = [1, 3, 6, 12, 24]
        mock_tui._prefs = MagicMock()
        mock_tui._prefs.timeline_hours = 6
        mock_tui.query_one.return_value = mock_timeline
        mock_tui.sessions = []

        ViewActionsMixin.action_cycle_timeline_hours(mock_tui)

        assert mock_tui._prefs.timeline_hours == 12
        assert "12h" in mock_tui.notify.call_args[0][0]


class TestToggleMonochrome:
    """Test action_toggle_monochrome method."""

    def test_enables_monochrome(self):
        """Should enable monochrome mode, save prefs, and update preview."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_preview = MagicMock()

        mock_tui = MagicMock()
        mock_tui.monochrome = False
        mock_tui._prefs = MagicMock()
        mock_tui.query_one.return_value = mock_preview

        ViewActionsMixin.action_toggle_monochrome(mock_tui)

        assert mock_tui.monochrome is True
        assert mock_tui._prefs.monochrome is True
        mock_tui._save_prefs.assert_called_once()
        mock_tui.add_class.assert_called_once_with("monochrome")
        mock_tui.remove_class.assert_not_called()
        assert mock_preview.monochrome is True
        mock_preview.refresh.assert_called_once()
        assert "ON" in mock_tui.notify.call_args[0][0]

    def test_disables_monochrome(self):
        """Should disable monochrome mode, save prefs, and update preview."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_preview = MagicMock()

        mock_tui = MagicMock()
        mock_tui.monochrome = True
        mock_tui._prefs = MagicMock()
        mock_tui.query_one.return_value = mock_preview

        ViewActionsMixin.action_toggle_monochrome(mock_tui)

        assert mock_tui.monochrome is False
        assert mock_tui._prefs.monochrome is False
        mock_tui._save_prefs.assert_called_once()
        mock_tui.remove_class.assert_called_once_with("monochrome")
        mock_tui.add_class.assert_not_called()
        assert mock_preview.monochrome is False
        mock_preview.refresh.assert_called_once()
        assert "OFF" in mock_tui.notify.call_args[0][0]

    def test_handles_no_preview_pane(self):
        """Should not raise if preview pane is not found."""
        from overcode.tui_actions.view import ViewActionsMixin
        from textual.css.query import NoMatches

        mock_tui = MagicMock()
        mock_tui.monochrome = False
        mock_tui._prefs = MagicMock()
        mock_tui.query_one.side_effect = NoMatches()

        ViewActionsMixin.action_toggle_monochrome(mock_tui)

        assert mock_tui.monochrome is True
        mock_tui._save_prefs.assert_called_once()
        mock_tui.notify.assert_called_once()


class TestToggleCostDisplay:
    """Test action_toggle_cost_display method."""

    def test_enables_cost_display(self):
        """Should enable cost display, save prefs, and update all widgets."""
        from overcode.tui_actions.view import ViewActionsMixin

        widget1 = MagicMock()
        widget2 = MagicMock()
        mock_status_bar = MagicMock()

        mock_tui = MagicMock()
        mock_tui.show_cost = False
        mock_tui._prefs = MagicMock()
        mock_tui.query.return_value = [widget1, widget2]
        mock_tui.query_one.return_value = mock_status_bar

        ViewActionsMixin.action_toggle_cost_display(mock_tui)

        assert mock_tui.show_cost is True
        assert mock_tui._prefs.show_cost is True
        mock_tui._save_prefs.assert_called_once()
        assert widget1.show_cost is True
        widget1.refresh.assert_called_once()
        assert widget2.show_cost is True
        widget2.refresh.assert_called_once()
        assert mock_status_bar.show_cost is True
        mock_status_bar.refresh.assert_called_once()
        assert "cost/budget" in mock_tui.notify.call_args[0][0]

    def test_disables_cost_display(self):
        """Should disable cost display and show token counts message."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui.show_cost = True
        mock_tui._prefs = MagicMock()
        mock_tui.query.return_value = []
        mock_tui.query_one.return_value = MagicMock()

        ViewActionsMixin.action_toggle_cost_display(mock_tui)

        assert mock_tui.show_cost is False
        assert mock_tui._prefs.show_cost is False
        assert "token counts" in mock_tui.notify.call_args[0][0]

    def test_handles_no_status_bar(self):
        """Should not raise if DaemonStatusBar is not found."""
        from overcode.tui_actions.view import ViewActionsMixin
        from textual.css.query import NoMatches

        mock_tui = MagicMock()
        mock_tui.show_cost = False
        mock_tui._prefs = MagicMock()
        mock_tui.query.return_value = []
        mock_tui.query_one.side_effect = NoMatches()

        ViewActionsMixin.action_toggle_cost_display(mock_tui)

        assert mock_tui.show_cost is True
        mock_tui._save_prefs.assert_called_once()
        mock_tui.notify.assert_called_once()


class TestExpandPreview:
    """Test action_expand_preview method."""

    def test_warns_when_not_list_preview_mode(self):
        """Should notify and return when not in list_preview mode."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui.view_mode = "tree"

        ViewActionsMixin.action_expand_preview(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "list+preview mode" in mock_tui.notify.call_args[0][0]

    def test_warns_when_no_agent_focused(self):
        """Should notify when focused widget is not a SessionSummary."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui.view_mode = "list_preview"
        # focused returns a non-SessionSummary object; isinstance check fails
        mock_tui.focused = MagicMock()

        # We need isinstance to return False for the focused widget
        # The code does isinstance(focused, SessionSummary), and since
        # mock_tui.focused is a MagicMock (not a SessionSummary), isinstance
        # will return False.
        ViewActionsMixin.action_expand_preview(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "No agent focused" in mock_tui.notify.call_args[0][0]

    def test_warns_when_no_tmux_window(self):
        """Should warn when focused session has no tmux window."""
        from overcode.tui_actions.view import ViewActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.tmux_window = None

        mock_focused = MagicMock(spec=SessionSummary)
        mock_focused.session = mock_session

        mock_tui = MagicMock()
        mock_tui.view_mode = "list_preview"
        mock_tui.focused = mock_focused

        ViewActionsMixin.action_expand_preview(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "No tmux window" in mock_tui.notify.call_args[0][0]
        assert mock_tui.notify.call_args[1]["severity"] == "warning"

    def test_warns_when_capture_returns_none(self):
        """Should warn when pane capture returns None."""
        from overcode.tui_actions.view import ViewActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.tmux_window = "window-1"

        mock_focused = MagicMock(spec=SessionSummary)
        mock_focused.session = mock_session

        mock_tui = MagicMock()
        mock_tui.view_mode = "list_preview"
        mock_tui.focused = mock_focused
        mock_tui.detector.capture_lines = 200
        mock_tui.detector.polling.tmux.capture_pane.return_value = None

        ViewActionsMixin.action_expand_preview(mock_tui)

        mock_tui.notify.assert_called_once()
        assert "Could not capture" in mock_tui.notify.call_args[0][0]

    def test_successful_expand(self):
        """Should capture pane and show fullscreen preview on success."""
        from overcode.tui_actions.view import ViewActionsMixin
        from overcode.tui_widgets import SessionSummary

        mock_session = MagicMock()
        mock_session.tmux_window = "window-1"
        mock_session.name = "test-agent"

        mock_focused = MagicMock(spec=SessionSummary)
        mock_focused.session = mock_session

        mock_fs_preview = MagicMock()

        mock_tui = MagicMock()
        mock_tui.view_mode = "list_preview"
        mock_tui.focused = mock_focused
        mock_tui.monochrome = False
        mock_tui.detector.capture_lines = 200
        mock_tui.detector.polling.tmux.capture_pane.return_value = "line1\nline2\nline3"
        mock_tui.query_one.return_value = mock_fs_preview

        ViewActionsMixin.action_expand_preview(mock_tui)

        mock_tui.detector.polling.tmux.capture_pane.assert_called_once()
        mock_fs_preview.show.assert_called_once_with(
            ["line1", "line2", "line3"], "test-agent", False
        )

    def test_uses_max_of_default_and_detector_capture_lines(self):
        """Should use the larger of DEFAULT_CAPTURE_LINES and detector.capture_lines."""
        from overcode.tui_actions.view import ViewActionsMixin
        from overcode.tui_widgets import SessionSummary
        from overcode.status_constants import DEFAULT_CAPTURE_LINES

        mock_session = MagicMock()
        mock_session.tmux_window = "window-1"
        mock_session.name = "test-agent"

        mock_focused = MagicMock(spec=SessionSummary)
        mock_focused.session = mock_session

        mock_tui = MagicMock()
        mock_tui.view_mode = "list_preview"
        mock_tui.focused = mock_focused
        mock_tui.monochrome = True
        # Set detector.capture_lines higher than DEFAULT_CAPTURE_LINES
        mock_tui.detector.capture_lines = DEFAULT_CAPTURE_LINES + 100
        mock_tui.detector.polling.tmux.capture_pane.return_value = "output"
        mock_tui.query_one.return_value = MagicMock()

        ViewActionsMixin.action_expand_preview(mock_tui)

        call_kwargs = mock_tui.detector.polling.tmux.capture_pane.call_args
        assert call_kwargs[1]["lines"] == DEFAULT_CAPTURE_LINES + 100


class TestCycleNotifications:
    """Test action_cycle_notifications method."""

    def test_cycles_from_off_to_sound(self):
        """Should cycle from off to sound mode."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui._notifier = MagicMock()
        mock_tui._notifier.mode = "off"
        mock_tui._prefs = MagicMock()

        with patch("overcode.notifier.MacNotifier") as MockNotifier:
            MockNotifier.MODES = ("off", "sound", "banner", "both")
            ViewActionsMixin.action_cycle_notifications(mock_tui)

        assert mock_tui._notifier.mode == "sound"
        assert mock_tui._prefs.notifications == "sound"
        mock_tui._save_prefs.assert_called_once()
        assert "Sound only" in mock_tui.notify.call_args[0][0]

    def test_cycles_from_sound_to_banner(self):
        """Should cycle from sound to banner mode."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui._notifier = MagicMock()
        mock_tui._notifier.mode = "sound"
        mock_tui._prefs = MagicMock()

        with patch("overcode.notifier.MacNotifier") as MockNotifier:
            MockNotifier.MODES = ("off", "sound", "banner", "both")
            ViewActionsMixin.action_cycle_notifications(mock_tui)

        assert mock_tui._notifier.mode == "banner"
        assert mock_tui._prefs.notifications == "banner"
        assert "Banner only" in mock_tui.notify.call_args[0][0]

    def test_cycles_from_banner_to_both(self):
        """Should cycle from banner to both mode."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui._notifier = MagicMock()
        mock_tui._notifier.mode = "banner"
        mock_tui._prefs = MagicMock()

        with patch("overcode.notifier.MacNotifier") as MockNotifier:
            MockNotifier.MODES = ("off", "sound", "banner", "both")
            ViewActionsMixin.action_cycle_notifications(mock_tui)

        assert mock_tui._notifier.mode == "both"
        assert mock_tui._prefs.notifications == "both"
        assert "Sound + Banner" in mock_tui.notify.call_args[0][0]

    def test_cycles_from_both_to_off(self):
        """Should wrap from both back to off mode."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui._notifier = MagicMock()
        mock_tui._notifier.mode = "both"
        mock_tui._prefs = MagicMock()

        with patch("overcode.notifier.MacNotifier") as MockNotifier:
            MockNotifier.MODES = ("off", "sound", "banner", "both")
            ViewActionsMixin.action_cycle_notifications(mock_tui)

        assert mock_tui._notifier.mode == "off"
        assert mock_tui._prefs.notifications == "off"
        assert "Off" in mock_tui.notify.call_args[0][0]

    def test_handles_unknown_current_mode(self):
        """Should fall back to index 0 if current mode is not recognized."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui._notifier = MagicMock()
        mock_tui._notifier.mode = "unknown"
        mock_tui._prefs = MagicMock()

        with patch("overcode.notifier.MacNotifier") as MockNotifier:
            MockNotifier.MODES = ("off", "sound", "banner", "both")
            ViewActionsMixin.action_cycle_notifications(mock_tui)

        # Unknown -> index 0 -> next = index 1 = "sound"
        assert mock_tui._notifier.mode == "sound"


class TestToggleExpandAllListPreview:
    """Test action_toggle_expand_all in list_preview mode."""

    def test_noop_in_list_preview_mode(self):
        """Should return immediately without querying widgets in list_preview mode."""
        from overcode.tui_actions.view import ViewActionsMixin

        mock_tui = MagicMock()
        mock_tui.view_mode = "list_preview"

        ViewActionsMixin.action_toggle_expand_all(mock_tui)

        # query should never be called because we return early
        mock_tui.query.assert_not_called()


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
