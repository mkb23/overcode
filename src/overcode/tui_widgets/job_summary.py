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

        # Duration
        duration = ""
        if job.start_time:
            try:
                start = datetime.fromisoformat(job.start_time)
                end = datetime.fromisoformat(job.end_time) if job.end_time else datetime.now()
                dur_sec = (end - start).total_seconds()
                mins, secs = divmod(int(dur_sec), 60)
                hours, mins = divmod(mins, 60)
                if hours > 0:
                    duration = f"{hours}h{mins:02d}m"
                else:
                    duration = f"{mins}m{secs:02d}s"
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
        text.append(f"{duration:>8} ", style="")
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
