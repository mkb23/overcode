"""
Job summary widget for TUI.

Displays a single-line summary of a tracked bash job.
"""

from datetime import datetime
from typing import List

from textual.widgets import Static
from textual.reactive import reactive
from textual.message import Message
from rich.text import Text

from ..job_manager import Job
from ..tui_helpers import format_duration


class JobSummary(Static, can_focus=True):
    """Widget displaying a single job's summary line."""

    class Selected(Message):
        """Posted when this job is focused/selected."""
        def __init__(self, job_id: str) -> None:
            self.job_id = job_id
            super().__init__()

    pane_content: reactive[List[str]] = reactive(list)

    def __init__(self, job: Job, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.job = job
        self.monochrome: bool = False
        self.emoji_free: bool = False

    def render(self) -> Text:
        """Render the job summary line."""
        job = self.job

        # Status icon
        if self.emoji_free:
            icon_map = {
                "running": "*",
                "completed": "+",
                "failed": "x",
                "killed": "x",
            }
        else:
            icon_map = {
                "running": "●",
                "completed": "✓",
                "failed": "✗",
                "killed": "✗",
            }
        icon = icon_map.get(job.status, "?")

        # Status color
        color_map = {
            "running": "green",
            "completed": "green",
            "failed": "red",
            "killed": "yellow",
        }
        color = color_map.get(job.status, "white")
        if self.monochrome:
            color = "white"

        # Start time (HH:MM)
        start_str = ""
        if job.start_time:
            try:
                start = datetime.fromisoformat(job.start_time)
                start_str = start.strftime("%H:%M")
            except ValueError:
                pass

        # Duration using shared formatter
        duration = ""
        if job.start_time:
            try:
                start = datetime.fromisoformat(job.start_time)
                end = datetime.fromisoformat(job.end_time) if job.end_time else datetime.now()
                dur_sec = (end - start).total_seconds()
                duration = format_duration(dur_sec)
            except ValueError:
                pass

        # Exit code
        exit_str = ""
        if job.exit_code is not None:
            exit_str = f" ({job.exit_code})"

        # Agent link
        agent_str = f" ← {job.agent_name}" if job.agent_name else ""

        # Truncate command for display
        cmd = job.command
        max_cmd = 80
        if len(cmd) > max_cmd:
            cmd = cmd[:max_cmd - 1] + "…"

        text = Text()
        text.append(icon, style=color)
        text.append(f" {job.name:<16} ", style="bold" if job.status == "running" else "")
        text.append(f"{cmd:<80} ", style="dim" if job.status != "running" else "")
        text.append(f"{start_str:>5} ", style="dim")
        text.append(f"{duration:>6} ", style="")
        text.append(f"  {job.status}{exit_str}", style=color)
        if agent_str:
            text.append(agent_str, style="dim cyan")

        return text

    def on_focus(self, event) -> None:
        """Post Selected message when focused."""
        self.post_message(self.Selected(self.job.id))

    def refresh_job(self, job: Job) -> None:
        """Update the job data and re-render."""
        self.job = job
        self.refresh()
