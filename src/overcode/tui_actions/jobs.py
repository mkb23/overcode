"""
Jobs view for the TUI: tracked bash jobs, their rows and the preview.

`J` swaps the agent list for the jobs list (zoomed, with the preview pane);
the job rows refresh every 5 s and the focused job's pane every second.
"""

from __future__ import annotations

from typing import List

from textual import work
from textual.containers import ScrollableContainer
from textual.css.query import NoMatches

from ..job_manager import Job
from ..tui_logic import running_job_counts
from ..tui_widgets import DaemonStatusBar, JobSummary, PreviewPane, SessionSummary
from ..worker_guard import single_flight


class JobsMixin:
    """Mixin providing the jobs view for SupervisorTUI."""

    def action_toggle_tui_mode(self) -> None:
        """Toggle between agents and jobs view."""
        self.tui_mode = "jobs" if self.tui_mode == "agents" else "agents"

    def watch_show_terminated(self, show_terminated: bool) -> None:
        """Immediately refresh jobs when ghost toggle changes."""
        if self.is_running and self.tui_mode == "jobs":
            self._refresh_jobs()

    def watch_tui_mode(self, mode: str) -> None:
        """React to tui_mode changes — swap visible containers."""
        if not self.is_running:
            return
        try:
            sessions_container = self.query_one("#sessions-container", ScrollableContainer)
            jobs_container = self.query_one("#jobs-container", ScrollableContainer)
            if mode == "jobs":
                sessions_container.display = False
                jobs_container.add_class("visible")
                jobs_container.display = True
                self._refresh_jobs()
                # Zoom the TUI and show the preview (as in sister view)
                self._dialog_will_open()
                self.preview_visible = True
                # The job chosen last time, else the first
                job_widgets = self._get_job_widgets()
                if job_widgets:
                    self.focused_job_index = self._chosen_job_row(job_widgets)
                    job_widgets[self.focused_job_index].focus()
            else:
                jobs_container.remove_class("visible")
                jobs_container.display = False
                sessions_container.display = True
                # Hide the preview and unzoom (as on leaving sister view)
                self.preview_visible = False
                self._dialog_did_close()
                # Refocus agents
                widget = self._get_focused_widget()
                if widget:
                    widget.focus()
        except NoMatches:
            pass
        self._update_subtitle()
        self._update_footer()
        self._update_column_headers()

    def watch_focused_job_index(self, new_index: int) -> None:
        """Focus the job widget at the new index and update preview."""
        if self.tui_mode != "jobs":
            return
        job_widgets = self._get_job_widgets()
        if not job_widgets:
            return
        if not (0 <= new_index < len(job_widgets)):
            new_index = max(0, min(new_index, len(job_widgets) - 1))
            self.focused_job_index = new_index
            return
        widget = job_widgets[new_index]
        widget.focus()
        # Remove .selected from all, add to focused
        for w in job_widgets:
            w.remove_class("selected")
        widget.add_class("selected")
        # Update preview pane
        if self.preview_visible:
            try:
                preview = self.query_one("#preview-pane", PreviewPane)
                preview.update_from_job_widget(widget)
            except NoMatches:
                pass

    def _apply_job_counts(self) -> None:
        """Running jobs into each agent's JOB column; orphans into the monitor bar (#463)."""
        local_ids = {s.id for s in self.sessions if not s.is_remote}
        self._job_counts, orphans = running_job_counts(self.jobs, local_ids)
        for widget in self.query(SessionSummary):
            count = self._job_counts.get(widget.session.id, 0)
            if widget.job_count != count:
                widget.job_count = count
                widget.refresh()
        try:
            bar = self.query_one("#daemon-status", DaemonStatusBar)
        except NoMatches:
            return
        if bar.orphan_job_count != orphans:
            bar.orphan_job_count = orphans
            bar.refresh()

    def _get_job_widgets(self) -> List[JobSummary]:
        """Get job widgets in order (not ones removed and waiting to be pruned)."""
        return [w for w in self.query(JobSummary) if not getattr(w, "_leaving", False)]

    def _choose_job(self, index: int) -> None:
        """j/k in the jobs view: highlight the job at ``index`` and remember
        it by id, so a refresh or a shown/hidden job never moves it."""
        widgets = self._get_job_widgets()
        if 0 <= index < len(widgets):
            self._chosen_job_id = widgets[index].job.id
        self.focused_job_index = index

    def _chosen_job_row(self, widgets: List[JobSummary]) -> int:
        """The chosen job's row now; while it is not listed, the nearest row."""
        chosen = getattr(self, "_chosen_job_id", None)
        for i, w in enumerate(widgets):
            if w.job.id == chosen:
                return i
        return max(0, min(self.focused_job_index, len(widgets) - 1))

    @work(thread=True, group="refresh_jobs")
    @single_flight("refresh_jobs")
    def _refresh_jobs(self) -> None:
        """Refresh jobs list from state file."""
        try:
            show_completed = self.show_terminated  # Reuse the ghost toggle
            jobs = self._job_launcher.list_jobs(include_completed=show_completed)
            self.call_from_thread(self._apply_jobs, jobs)
        except Exception:
            pass

    def _apply_jobs(self, jobs: List[Job]) -> None:
        """Apply refreshed job list to TUI (main thread)."""
        self.jobs = jobs
        self._apply_job_counts()
        try:
            container = self.query_one("#jobs-container", ScrollableContainer)
        except NoMatches:
            return

        # Compute dynamic name column width (min 12, max 30)
        name_width = max((len(j.name) for j in jobs), default=12)
        name_width = max(12, min(name_width, 30))

        existing = {w.job.id: w for w in self.query(JobSummary)}
        current_ids = {j.id for j in jobs}

        # Remove widgets for deleted jobs
        for job_id, widget in existing.items():
            if job_id not in current_ids:
                widget._leaving = True
                widget.remove()

        # Update existing or mount new
        for job in jobs:
            if job.id in existing:
                w = existing[job.id]
                w.name_width = name_width
                w.refresh_job(job)
            else:
                widget = JobSummary(job)
                widget.monochrome = self.monochrome
                widget.emoji_free = self.emoji_free
                widget.name_width = name_width
                container.mount(widget)

        # The highlight stays on the chosen job, by id (rows came and went)
        if self.tui_mode == "jobs":
            widgets = self._get_job_widgets()
            if widgets:
                row = self._chosen_job_row(widgets)
                if row != self.focused_job_index:
                    self.focused_job_index = row
            self._update_focused_job_pane_content()

    def _update_focused_job_pane_content(self) -> None:
        """Capture tmux pane content for the focused job widget."""
        job_widgets = self._get_job_widgets()
        if not job_widgets:
            return
        idx = self.focused_job_index
        if not (0 <= idx < len(job_widgets)):
            return
        widget = job_widgets[idx]
        job = widget.job
        if job.status == "running" and job.tmux_window:
            try:
                content = self._job_launcher.tmux.capture_pane(job.tmux_window)
                widget.pane_content = content.split("\n") if content else []
            except Exception:
                pass

    def _poll_focused_job_pane(self) -> None:
        """Poll focused job's tmux pane content (1s interval)."""
        if self.tui_mode != "jobs":
            return
        self._update_focused_job_pane_content()
        # Update preview pane
        job_widgets = self._get_job_widgets()
        idx = self.focused_job_index
        if job_widgets and 0 <= idx < len(job_widgets):
            widget = job_widgets[idx]
            if self.preview_visible:
                try:
                    preview = self.query_one("#preview-pane", PreviewPane)
                    preview.update_from_job_widget(widget)
                except NoMatches:
                    pass

    def _action_kill_focused_job(self) -> None:
        """Kill the focused job."""
        job_widgets = self._get_job_widgets()
        if not job_widgets:
            return
        idx = self.focused_job_index
        if not (0 <= idx < len(job_widgets)):
            return
        job = job_widgets[idx].job
        if job.status != "running":
            self.notify(f"Job '{job.name}' is not running", severity="warning")
            return
        if self._job_launcher.kill_job(job.name):
            self.notify(f"Killed job '{job.name}'")
            self._refresh_jobs()
        else:
            self.notify(f"Failed to kill job '{job.name}'", severity="error")

    def _action_clear_completed_jobs(self) -> None:
        """Clear all completed/failed/killed jobs."""
        self._job_manager.clear_completed()
        self.notify("Cleared completed jobs")
        self._refresh_jobs()

    def _action_send_enter_to_focused_job(self) -> None:
        """Send Enter to the focused job's tmux window."""
        job_widgets = self._get_job_widgets()
        if not job_widgets:
            return
        idx = self.focused_job_index
        if not (0 <= idx < len(job_widgets)):
            return
        job = job_widgets[idx].job
        if job.status == "running" and job.tmux_window:
            self._job_launcher.tmux.send_keys(job.tmux_window, "", enter=True)
