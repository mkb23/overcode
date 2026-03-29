"""
View action methods for TUI.

Handles display settings, toggles, and visual modes.
"""

from typing import TYPE_CHECKING

from textual.css.query import NoMatches

from ..status_constants import DEFAULT_CAPTURE_LINES

if TYPE_CHECKING:
    from ..tui_widgets import SessionSummary, StatusTimeline, HelpOverlay, FullscreenPreview


def _toggle_widget(tui, widget_id: str, widget_class, pref_attr: str, label: str, on_show=None) -> None:
    """Toggle a widget's display visibility, save preference, and notify.

    Args:
        tui: The SupervisorTUI instance
        widget_id: CSS selector ID (e.g. "timeline")
        widget_class: The widget class for query_one type check
        pref_attr: Attribute name on tui._prefs to persist the state
        label: Human-readable label for the notification (e.g. "Timeline")
        on_show: Optional callback(widget) called when widget becomes visible
    """
    try:
        widget = tui.query_one(f"#{widget_id}", widget_class)
        widget.display = not widget.display
        setattr(tui._prefs, pref_attr, widget.display)
        tui._save_prefs()
        if widget.display and on_show:
            on_show(widget)
        state = "shown" if widget.display else "hidden"
        tui.notify(f"{label} {state}", severity="information")
    except NoMatches:
        pass


class ViewActionsMixin:
    """Mixin providing view/display actions for SupervisorTUI."""

    def action_toggle_timeline(self) -> None:
        """Toggle timeline visibility."""
        from ..tui_widgets import StatusTimeline
        _toggle_widget(self, "timeline", StatusTimeline, "timeline_visible", "Timeline")

    def action_toggle_help(self) -> None:
        """Toggle help overlay visibility."""
        from ..tui_widgets import HelpOverlay
        try:
            help_overlay = self.query_one("#help-overlay", HelpOverlay)
            if help_overlay.has_class("visible"):
                help_overlay.remove_class("visible")
                self._dialog_did_close()
            else:
                self._dialog_will_open()
                help_overlay.add_class("visible")
        except NoMatches:
            pass

    def action_resize_focused_window(self) -> None:
        """Resize the focused agent's tmux window to match the bottom pane size.

        Useful when nested tmux windows get the wrong size after terminal resizes.
        Only works in compact (tmux split) mode.
        For remote agents with SSH, sends resize via the sister API.
        """
        if not self.compact:
            self.notify("Resize only works in tmux split mode", severity="warning", timeout=2)
            return
        try:
            import subprocess

            widget = self._get_focused_widget()
            if not widget:
                return
            session = widget.session

            # Get the bottom pane's actual dimensions
            result = subprocess.run(
                ["tmux", "display-message", "-t", self._bottom_pane_target(),
                 "-p", "#{pane_width} #{pane_height}"],
                capture_output=True, text=True,
            )
            if result.returncode != 0 or not result.stdout.strip():
                self.notify("Could not read pane size", severity="warning", timeout=2)
                return
            parts = result.stdout.strip().split()
            if len(parts) != 2:
                return
            pane_width, pane_height = int(parts[0]), int(parts[1])

            if session.is_remote and session.source_ssh:
                # Remote agent: resize via sister API
                from ..sister_controller import SisterController
                ctrl_result = SisterController(timeout=3).resize_agent(
                    session.source_url, session.source_api_key,
                    session.name, pane_width, pane_height,
                )
                if ctrl_result.ok:
                    self.notify(f"Resized {session.name} to {pane_width}x{pane_height}", severity="information", timeout=2)
                else:
                    self.notify(f"Resize failed: {ctrl_result.error}", severity="error", timeout=3)
            else:
                # Local agent: resize via tmux directly
                window_name = session.tmux_window
                if not window_name:
                    return
                sync_session = self.tmux_sync_target or self.tmux_session
                target = f"{sync_session}:{window_name}"
                result = subprocess.run(
                    ["tmux", "resize-window", "-t", target,
                     "-x", str(pane_width), "-y", str(pane_height)],
                    capture_output=True, text=True,
                )
                if result.returncode != 0:
                    self.notify(f"Resize failed: {result.stderr.strip()}", severity="error", timeout=3)
                else:
                    self.notify(f"Resized {session.name} to {pane_width}x{pane_height}", severity="information", timeout=2)
        except Exception as e:
            self.notify(f"Resize failed: {e}", severity="error", timeout=3)

    def action_cycle_summary(self) -> None:
        """Cycle through summary detail levels (low, med, high, full)."""
        from ..tui_widgets import SessionSummary
        new_idx = (self.summary_level_index + 1) % len(self.SUMMARY_LEVELS)
        new_level = self.SUMMARY_LEVELS[new_idx]
        self.summary_level_index = new_idx

        # Push the right per-level overrides to each widget
        overrides = self._prefs.column_config.get(new_level, {})
        for widget in self.query(SessionSummary):
            widget.summary_detail = new_level
            widget.column_overrides = overrides

        # Save preference
        self._prefs.summary_detail = new_level
        self._save_prefs()

        # Update footer and column headers
        self._update_footer()
        self._recompute_cell_column_widths()

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
            "heartbeat": "Heartbeat Instruction",
        }
        self.notify(f"{mode_names.get(self.summary_content_mode, self.summary_content_mode)}", severity="information")

    def action_toggle_preview(self) -> None:
        """Toggle preview pane visibility."""
        self.preview_visible = not self.preview_visible

        # Save preference
        self._prefs.preview_visible = self.preview_visible
        self._save_prefs()

        state = "shown" if self.preview_visible else "hidden"
        self.notify(f"Preview {state}", severity="information")

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
        self._prefs.show_terminated = self.show_terminated
        self._save_prefs()
        self.update_session_widgets()
        self.update_timeline()

        status = "visible" if self.show_terminated else "hidden"
        count = len(self._terminated_sessions)
        if count > 0:
            self.notify(f"Killed sessions: {status} ({count})", severity="information")
        else:
            self.notify(f"Killed sessions: {status}", severity="information")

    def action_toggle_show_done(self) -> None:
        """Toggle showing 'done' child agents (#244).

        This is an ephemeral toggle (not persisted) to match the CLI's
        --show-done flag behavior: done agents are always hidden on
        launch and must be explicitly revealed (#319).
        """
        self.show_done = not self.show_done
        self.update_session_widgets()
        self.update_timeline()

        status = "visible" if self.show_done else "hidden"
        done_count = sum(1 for s in self.sessions if getattr(s, 'status', None) == 'done')
        if done_count > 0:
            self.notify(f"Done agents: {status} ({done_count})", severity="information")
        else:
            self.notify(f"Done agents: {status}", severity="information")

    def action_toggle_hide_asleep(self) -> None:
        """Toggle hiding sleeping agents from display."""
        self.hide_asleep = not self.hide_asleep
        self._prefs.hide_asleep = self.hide_asleep
        self._save_prefs()
        self._update_subtitle()
        self.update_session_widgets()
        self.update_timeline()

        # Count sleeping agents
        asleep_count = sum(1 for s in self.sessions if s.is_asleep)
        if self.hide_asleep:
            self.notify(f"Sleeping agents hidden ({asleep_count})", severity="information")
        else:
            self.notify(f"Sleeping agents visible ({asleep_count})", severity="information")

    def action_cycle_sort_mode(self) -> None:
        """Cycle through sort modes (#61)."""
        from ..tui_logic import cycle_sort_mode, get_sort_mode_display_name

        # Use extracted logic for cycling
        self._prefs.sort_mode = cycle_sort_mode(self._prefs.sort_mode, self.SORT_MODES)
        self._save_prefs()

        # Re-sort and refresh (update_session_widgets handles focus preservation)
        self._sort_sessions()
        self.update_session_widgets()
        self._update_subtitle()

        self.notify(f"Sort: {get_sort_mode_display_name(self._prefs.sort_mode)}", severity="information")

    def action_toggle_collapse_children(self) -> None:
        """Toggle collapse/expand children for the focused parent in tree view (#244)."""
        if self._prefs.sort_mode != "by_tree":
            self.notify("Collapse only works in tree sort mode (press S)", severity="warning")
            return

        focused_widget = self._get_focused_widget()
        if not focused_widget:
            return

        session_id = focused_widget.session.id

        # Check if this session has children — if not, walk up to the parent
        # Use self.sessions (not session_manager) so remote sessions are included
        children = [s for s in self.sessions if s.parent_session_id == session_id]
        if not children:
            parent_id = focused_widget.session.parent_session_id
            if parent_id:
                # Focused on a child — collapse/expand the parent's children instead
                session_id = parent_id
                children = [s for s in self.sessions if s.parent_session_id == session_id]

        if not children:
            self.notify("No children to collapse", severity="warning")
            return

        # Resolve parent name for notification
        if session_id == focused_widget.session.id:
            parent_name = focused_widget.session.name
        else:
            parent_session = next((s for s in self.sessions if s.id == session_id), None)
            parent_name = parent_session.name if parent_session else session_id[:8]

        if session_id in self.collapsed_parents:
            self.collapsed_parents.discard(session_id)
            self.notify(f"Expanded children of {parent_name}", severity="information")
        else:
            self.collapsed_parents.add(session_id)
            # Move focus to the parent so the user isn't left on a now-hidden widget
            widgets = self._get_widgets_in_session_order()
            for i, w in enumerate(widgets):
                if w.session.id == session_id:
                    self.focused_session_index = i
                    break
            self.notify(f"Collapsed children of {parent_name}", severity="information")

        # Re-sort and refresh (update_session_widgets handles focus preservation)
        self._sort_sessions()
        self.update_session_widgets()
        self.update_timeline()

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

    def action_cycle_timeline_hours(self) -> None:
        """Cycle timeline scope through presets: 1h → 3h → 6h → 12h → 24h (#191)."""
        from ..tui_widgets.status_timeline import StatusTimeline
        presets = self.TIMELINE_PRESETS
        current = self._prefs.timeline_hours
        # Find current index (snap to nearest preset if not exact)
        try:
            idx = presets.index(current)
        except ValueError:
            idx = 0
        new_hours = presets[(idx + 1) % len(presets)]
        self._prefs.timeline_hours = new_hours
        self._save_prefs()
        try:
            timeline = self.query_one("#timeline", StatusTimeline)
            from ..tui_logic import filter_visible_sessions
            display_sessions = filter_visible_sessions(
                active_sessions=self.sessions,
                terminated_sessions=list(self._terminated_sessions.values()),
                hide_asleep=self.hide_asleep,
                show_terminated=self.show_terminated,
                show_done=self.show_done,
                collapsed_parents=self.collapsed_parents if self._prefs.sort_mode == "by_tree" else None,
            )
            timeline.set_hours(new_hours, display_sessions)
        except Exception:
            pass
        self.notify(f"Timeline: {new_hours:.0f}h", severity="information")

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

    def action_toggle_emoji_free(self) -> None:
        """Toggle emoji-free mode for terminals without emoji fonts (#315).

        When enabled:
        - Replaces all emoji with ASCII text equivalents
        - Helps with terminals where emoji render as tofu or misaligned
        """
        self.emoji_free = not self.emoji_free

        self._prefs.emoji_free = self.emoji_free
        self._save_prefs()

        # Force all session widgets to repaint with new emoji setting
        self.update_session_widgets()
        self._update_footer()

        self.notify(
            "Emoji-free mode ON" if self.emoji_free else "Emoji-free mode OFF",
            severity="information"
        )

    # Cost display modes: tokens → cost → joules
    _COST_DISPLAY_MODES = ["tokens", "cost", "joules"]
    _COST_DISPLAY_LABELS = {
        "tokens": "Showing token counts",
        "cost": "Showing cost/budget",
        "joules": "Showing energy (joules)",
    }

    def action_toggle_cost_display(self) -> None:
        """Cycle between token counts, dollar costs, and energy (joules).

        Modes:
        - tokens: Show Σ123K token counts
        - cost: Show $X.XX estimated cost in USD
        - joules: Show ⚡51MJ estimated energy consumption
        """
        from ..tui_widgets import SessionSummary, DaemonStatusBar

        modes = self._COST_DISPLAY_MODES
        current_idx = modes.index(self.show_cost) if self.show_cost in modes else 0
        self.show_cost = modes[(current_idx + 1) % len(modes)]

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
            self._COST_DISPLAY_LABELS.get(self.show_cost, "Showing token counts"),
            severity="information"
        )

    def action_expand_preview(self) -> None:
        """Expand the preview pane into a fullscreen scrollable overlay (#190)."""
        from ..tui_widgets import FullscreenPreview, SessionSummary

        # Only works when preview pane is visible
        if not self.preview_visible:
            self.notify("Fullscreen preview requires preview pane (press m)", severity="information")
            return

        # Get the focused session widget
        focused = self.focused
        if not isinstance(focused, SessionSummary):
            self.notify("No agent focused", severity="information")
            return

        session = focused.session
        if session.tmux_window is None:
            self.notify("No tmux window for this agent", severity="warning")
            return

        # Do a fresh deep capture for scrollback review
        capture_depth = max(DEFAULT_CAPTURE_LINES, self.detector.capture_lines)
        raw = self.detector.polling.tmux.capture_pane(
            self.tmux_session, session.tmux_window, lines=capture_depth
        )
        if raw is None:
            self.notify("Could not capture pane output", severity="warning")
            return

        lines = raw.split("\n")

        try:
            fs_preview = self.query_one("#fullscreen-preview", FullscreenPreview)
            fs_preview.show(lines, session.name, self.monochrome)
        except NoMatches:
            pass

    def action_cycle_notifications(self) -> None:
        """Cycle macOS notification mode: off → sound → banner → both → off (#235)."""
        from ..notifier import MacNotifier

        modes = MacNotifier.MODES
        current_idx = modes.index(self._notifier.mode) if self._notifier.mode in modes else 0
        new_mode = modes[(current_idx + 1) % len(modes)]

        self._notifier.mode = new_mode
        self._prefs.notifications = new_mode
        self._save_prefs()

        labels = {"off": "Off", "sound": "Sound only", "banner": "Banner only", "both": "Sound + Banner"}
        self.notify(f"Notifications: {labels[new_mode]}", severity="information")

    def action_toggle_column_headers(self) -> None:
        """Toggle column headers row above summary lines."""
        self._prefs.show_column_headers = not self._prefs.show_column_headers
        self._save_prefs()
        self._update_column_headers()
        state = "shown" if self._prefs.show_column_headers else "hidden"
        self.notify(f"Column headers {state}", severity="information")
