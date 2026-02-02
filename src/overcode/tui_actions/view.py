"""
View action methods for TUI.

Handles display settings, toggles, and visual modes.
"""

from typing import TYPE_CHECKING

from textual.css.query import NoMatches

if TYPE_CHECKING:
    from ..tui_widgets import SessionSummary, StatusTimeline, HelpOverlay


class ViewActionsMixin:
    """Mixin providing view/display actions for SupervisorTUI."""

    def action_toggle_timeline(self) -> None:
        """Toggle timeline visibility."""
        from ..tui_widgets import StatusTimeline
        try:
            timeline = self.query_one("#timeline", StatusTimeline)
            timeline.display = not timeline.display
            self._prefs.timeline_visible = timeline.display
            self._save_prefs()
            state = "shown" if timeline.display else "hidden"
            self.notify(f"Timeline {state}", severity="information")
        except NoMatches:
            pass

    def action_toggle_help(self) -> None:
        """Toggle help overlay visibility."""
        from ..tui_widgets import HelpOverlay
        try:
            help_overlay = self.query_one("#help-overlay", HelpOverlay)
            if help_overlay.has_class("visible"):
                help_overlay.remove_class("visible")
            else:
                help_overlay.add_class("visible")
        except NoMatches:
            pass

    def action_manual_refresh(self) -> None:
        """Manually trigger a full refresh (useful in diagnostics mode)."""
        self.refresh_sessions()
        self.update_all_statuses()
        self.update_daemon_status()
        self.update_timeline()
        self.notify("Refreshed", severity="information", timeout=2)

    def action_toggle_expand_all(self) -> None:
        """Toggle expand/collapse all sessions."""
        from ..tui_widgets import SessionSummary
        widgets = list(self.query(SessionSummary))
        if not widgets:
            return
        # If any are expanded, collapse all; otherwise expand all
        any_expanded = any(w.expanded for w in widgets)
        new_state = not any_expanded
        for widget in widgets:
            widget.expanded = new_state
            self.expanded_states[widget.session.id] = new_state

    def action_cycle_detail(self) -> None:
        """Cycle through detail levels (5, 10, 20, 50 lines)."""
        from ..tui_widgets import SessionSummary
        self.detail_level_index = (self.detail_level_index + 1) % len(self.DETAIL_LEVELS)
        new_level = self.DETAIL_LEVELS[self.detail_level_index]

        # Update all session widgets
        for widget in self.query(SessionSummary):
            widget.detail_lines = new_level

        # Save preference
        self._prefs.detail_lines = new_level
        self._save_prefs()

        self.notify(f"Detail: {new_level} lines", severity="information")

    def action_cycle_summary(self) -> None:
        """Cycle through summary detail levels (low, med, full)."""
        from ..tui_widgets import SessionSummary
        self.summary_level_index = (self.summary_level_index + 1) % len(self.SUMMARY_LEVELS)
        new_level = self.SUMMARY_LEVELS[self.summary_level_index]

        # Update all session widgets
        for widget in self.query(SessionSummary):
            widget.summary_detail = new_level

        # Save preference
        self._prefs.summary_detail = new_level
        self._save_prefs()

        self.notify(f"Summary: {new_level}", severity="information")

    def action_cycle_summary_content(self) -> None:
        """Cycle through summary content modes (ai_short, ai_long, orders, annotation) (#74)."""
        from ..tui_widgets import SessionSummary
        modes = self.SUMMARY_CONTENT_MODES
        current_idx = modes.index(self.summary_content_mode) if self.summary_content_mode in modes else 0
        new_idx = (current_idx + 1) % len(modes)
        self.summary_content_mode = modes[new_idx]

        # Save preference (#98)
        self._prefs.summary_content_mode = self.summary_content_mode
        self._save_prefs()

        # Update all session widgets
        for widget in self.query(SessionSummary):
            widget.summary_content_mode = self.summary_content_mode

        mode_names = {
            "ai_short": "AI Summary (short)",
            "ai_long": "AI Summary (context)",
            "orders": "Standing Orders",
            "annotation": "Human Annotation",
        }
        self.notify(f"{mode_names.get(self.summary_content_mode, self.summary_content_mode)}", severity="information")

    def action_toggle_view_mode(self) -> None:
        """Toggle between tree and list+preview view modes."""
        if self.view_mode == "tree":
            self.view_mode = "list_preview"
        else:
            self.view_mode = "tree"

        # Save preference
        self._prefs.view_mode = self.view_mode
        self._save_prefs()

    def action_toggle_tmux_sync(self) -> None:
        """Toggle tmux pane sync - syncs navigation to external tmux pane."""
        self.tmux_sync = not self.tmux_sync

        # Save preference
        self._prefs.tmux_sync = self.tmux_sync
        self._save_prefs()

        # Update subtitle to show sync state
        self._update_subtitle()

        # If enabling, sync to currently focused session immediately
        if self.tmux_sync:
            self._sync_tmux_window()

    def action_toggle_show_terminated(self) -> None:
        """Toggle showing killed/terminated sessions in the timeline."""
        self.show_terminated = not self.show_terminated

        # Save preference
        self._prefs.show_terminated = self.show_terminated
        self._save_prefs()

        # Refresh session widgets to show/hide terminated sessions
        self.update_session_widgets()

        # Notify user
        status = "visible" if self.show_terminated else "hidden"
        count = len(self._terminated_sessions)
        if count > 0:
            self.notify(f"Killed sessions: {status} ({count})", severity="information")
        else:
            self.notify(f"Killed sessions: {status}", severity="information")

    def action_toggle_hide_asleep(self) -> None:
        """Toggle hiding sleeping agents from display."""
        self.hide_asleep = not self.hide_asleep

        # Save preference
        self._prefs.hide_asleep = self.hide_asleep
        self._save_prefs()

        # Update subtitle to show state
        self._update_subtitle()

        # Refresh session widgets to show/hide sleeping agents
        self.update_session_widgets()

        # Count sleeping agents
        asleep_count = sum(1 for s in self.sessions if s.is_asleep)
        if self.hide_asleep:
            self.notify(f"Sleeping agents hidden ({asleep_count})", severity="information")
        else:
            self.notify(f"Sleeping agents visible ({asleep_count})", severity="information")

    def action_cycle_sort_mode(self) -> None:
        """Cycle through sort modes (#61)."""
        from ..tui_logic import cycle_sort_mode, get_sort_mode_display_name

        # Remember the currently focused session before sorting
        widgets = self._get_widgets_in_session_order()
        focused_session_id = None
        if widgets and 0 <= self.focused_session_index < len(widgets):
            focused_session_id = widgets[self.focused_session_index].session.id

        # Use extracted logic for cycling
        self._prefs.sort_mode = cycle_sort_mode(self._prefs.sort_mode, self.SORT_MODES)
        self._save_prefs()

        # Re-sort and refresh
        self._sort_sessions()
        self.update_session_widgets()
        self._update_subtitle()

        # Update focused_session_index to follow the same session at its new position
        if focused_session_id:
            widgets = self._get_widgets_in_session_order()
            for i, widget in enumerate(widgets):
                if widget.session.id == focused_session_id:
                    self.focused_session_index = i
                    break

        self.notify(f"Sort: {get_sort_mode_display_name(self._prefs.sort_mode)}", severity="information")

    def action_toggle_copy_mode(self) -> None:
        """Toggle mouse capture to allow native terminal text selection.

        When copy mode is ON:
        - Mouse events pass through to terminal
        - You can select text and Cmd+C to copy
        - Press 'y' again to exit copy mode
        """
        if not hasattr(self, '_copy_mode'):
            self._copy_mode = False

        self._copy_mode = not self._copy_mode

        if self._copy_mode:
            # Write escape sequences directly to the driver's file (stderr)
            driver_file = self._driver._file

            # Disable all mouse tracking modes
            driver_file.write("\x1b[?1000l")  # Disable basic mouse tracking
            driver_file.write("\x1b[?1002l")  # Disable cell motion tracking
            driver_file.write("\x1b[?1003l")  # Disable all motion tracking
            driver_file.write("\x1b[?1015l")  # Disable urxvt extended mode
            driver_file.write("\x1b[?1006l")  # Disable SGR extended mode
            driver_file.flush()

            self.notify("COPY MODE - select with mouse, Cmd+C to copy, 'y' to exit", severity="warning")
        else:
            # Re-enable mouse support using driver's method
            self._driver._mouse = True
            self._driver._enable_mouse_support()
            self.refresh()
            self.notify("Copy mode OFF", severity="information")

    def action_baseline_back(self) -> None:
        """Move baseline back by 15 minutes (max 180 = 3 hours)."""
        new_baseline = min(self.baseline_minutes + 15, 180)
        self.baseline_minutes = new_baseline
        self._prefs.baseline_minutes = new_baseline
        self._save_prefs()
        self._notify_baseline_change()

    def action_baseline_forward(self) -> None:
        """Move baseline forward by 15 minutes (min 0 = now)."""
        new_baseline = max(self.baseline_minutes - 15, 0)
        self.baseline_minutes = new_baseline
        self._prefs.baseline_minutes = new_baseline
        self._save_prefs()
        self._notify_baseline_change()

    def action_baseline_reset(self) -> None:
        """Reset baseline to now (instantaneous)."""
        self.baseline_minutes = 0
        self._prefs.baseline_minutes = 0
        self._save_prefs()
        self._notify_baseline_change()

    def _notify_baseline_change(self) -> None:
        """Notify user and trigger UI updates after baseline change."""
        if self.baseline_minutes == 0:
            label = "now"
        elif self.baseline_minutes < 60:
            label = f"-{self.baseline_minutes}m"
        else:
            hours = self.baseline_minutes // 60
            mins = self.baseline_minutes % 60
            if mins == 0:
                label = f"-{hours}h"
            else:
                label = f"-{hours}h{mins}m"
        self.notify(f"Baseline: {label}", severity="information")
        # Trigger status bar refresh to show updated mean spin
        self.update_daemon_status()
        # Trigger timeline refresh to show baseline marker
        self.update_timeline()

    def action_toggle_monochrome(self) -> None:
        """Toggle monochrome (B&W) mode for terminals with ANSI issues (#138).

        When enabled:
        - Strips ANSI color codes from preview pane content
        - Uses plain text rendering for terminal output
        - Helps with terminals that garble ANSI color codes
        """
        from ..tui_widgets import PreviewPane

        self.monochrome = not self.monochrome

        # Save preference
        self._prefs.monochrome = self.monochrome
        self._save_prefs()

        # Toggle CSS class on the app for any CSS-based styling changes
        if self.monochrome:
            self.add_class("monochrome")
        else:
            self.remove_class("monochrome")

        # Update preview pane to use monochrome rendering
        try:
            preview = self.query_one("#preview-pane", PreviewPane)
            preview.monochrome = self.monochrome
            preview.refresh()
        except NoMatches:
            pass

        self.notify(
            "Monochrome mode ON" if self.monochrome else "Monochrome mode OFF",
            severity="information"
        )

    def action_toggle_cost_display(self) -> None:
        """Toggle between showing token counts and dollar costs.

        When enabled:
        - Shows estimated cost in USD instead of token counts
        - Format: $X.XX for small amounts, $X.XK/$X.XM for large
        - Uses Sonnet 3.5 pricing model
        """
        from ..tui_widgets import SessionSummary, DaemonStatusBar

        self.show_cost = not self.show_cost

        # Save preference
        self._prefs.show_cost = self.show_cost
        self._save_prefs()

        # Update all session widgets
        for widget in self.query(SessionSummary):
            widget.show_cost = self.show_cost
            widget.refresh()

        # Update daemon status bar
        try:
            status_bar = self.query_one(DaemonStatusBar)
            status_bar.show_cost = self.show_cost
            status_bar.refresh()
        except NoMatches:
            pass

        self.notify(
            "Showing $ cost" if self.show_cost else "Showing tokens",
            severity="information"
        )
