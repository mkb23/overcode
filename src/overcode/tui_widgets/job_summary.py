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
        self.name_width: int = 20  # updated by _apply_jobs

    def render(self) -> Text:
        """Render the job summary line."""
        job = self.job
        nw = self.name_width

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
            "completed": "dim green",
            "failed": "red",
            "killed": "yellow",
        }
        color = color_map.get(job.status, "white")
        if self.monochrome:
            color = "white"

        # Start time (ISO date + HH:MM)
        start_str = ""
        if job.start_time:
            try:
                start = datetime.fromisoformat(job.start_time)
                start_str = start.strftime("%Y-%m-%d %H:%M")
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

        # Agent name (truncate to 16)
        agent = job.agent_name or ""
        if len(agent) > 16:
            agent = agent[:15] + "…"

        # Truncate name if needed
        name = job.name
        if len(name) > nw:
            name = name[:nw - 1] + "…"

        # Truncate command for display
        cmd = job.command
        max_cmd = 80
        if len(cmd) > max_cmd:
            cmd = cmd[:max_cmd - 1] + "…"

        text = Text()
        text.append(icon, style=color)
        text.append(f" {name:<{nw}} ", style="bold" if job.status == "running" else "")
        text.append(f"{agent:<16} ", style="dim cyan")
        text.append(f"{cmd:<80} ", style="dim" if job.status != "running" else "")
        text.append(f"{start_str:>16} ", style="dim")
        text.append(f"{duration:>6} ", style="")
        text.append(f"  {job.status}{exit_str}", style=color)

        return text

    def on_focus(self, event) -> None:
        """Post Selected message when focused."""
        self.post_message(self.Selected(self.job.id))

    def refresh_job(self, job: Job) -> None:
        """Update the job data and re-render."""
        self.job = job
        self.refresh()
