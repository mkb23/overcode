"""
Status timeline widget for TUI.

Shows historical status timelines for user presence and agents.
"""

from datetime import datetime, timedelta

from textual.widgets import Static
from rich.text import Text

from ..presence_logger import read_presence_history, MACOS_APIS_AVAILABLE
from ..settings import get_agent_history_path
from ..status_history import read_agent_status_history
from ..config import get_timeline_config
from ..tui_helpers import (
    presence_state_to_char,
    get_presence_color,
    agent_status_to_char,
    get_agent_timeline_color,
    truncate_name,
    build_timeline_slots,
)


class StatusTimeline(Static):
    """Widget displaying historical status timelines for user presence and agents.

    Shows the last N hours with each character representing a time slice.
    - User presence: green=active, yellow=inactive, red/gray=locked/away
    - Agent status: green=running, red=waiting, grey=terminated

    Timeline hours configurable via ~/.overcode/config.yaml (timeline.hours).
    """

    TIMELINE_HOURS = 3.0  # Default hours
    MIN_NAME_WIDTH = 6    # Minimum width for agent names
    MAX_NAME_WIDTH = 30   # Maximum width for agent names
    MIN_TIMELINE = 20     # Minimum timeline width
    DEFAULT_TIMELINE = 60 # Fallback if can't detect width

    def __init__(self, sessions: list, tmux_session: str = "agents", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sessions = sessions
        self.tmux_session = tmux_session
        self._presence_history = []
        self._agent_histories = {}
        # Get timeline hours from config (config file > env var > default)
        timeline_config = get_timeline_config()
        self.timeline_hours = timeline_config["hours"]

    @property
    def label_width(self) -> int:
        """Calculate label width based on longest agent name (#75)."""
        if not self.sessions:
            return self.MIN_NAME_WIDTH
        longest = max(len(s.name) for s in self.sessions)
        # Clamp to min/max and add padding for "  " prefix and " " suffix
        return min(self.MAX_NAME_WIDTH, max(self.MIN_NAME_WIDTH, longest))

    @property
    def timeline_width(self) -> int:
        """Calculate timeline width based on available space after labels (#75)."""
        import shutil
        try:
            # Try to get terminal size directly - most reliable
            term_width = shutil.get_terminal_size().columns
            # Subtract:
            #   - label_width (agent name)
            #   - 3 for "  " prefix and " " suffix around label
            #   - 5 for percentage display " XXX%"
            #   - 2 for CSS padding (padding: 0 1 = 1 char each side)
            available = term_width - self.label_width - 3 - 5 - 2
            return max(self.MIN_TIMELINE, min(available, 200))
        except (OSError, ValueError):
            # No terminal available or invalid size
            return self.DEFAULT_TIMELINE

    def update_history(self, sessions: list) -> None:
        """Refresh history data from log files."""
        self.sessions = sessions
        self._presence_history = read_presence_history(hours=self.timeline_hours)
        self._agent_histories = {}

        # Get agent names from sessions
        agent_names = [s.name for s in sessions]

        # Read agent history from session-specific file and group by agent
        history_path = get_agent_history_path(self.tmux_session)
        all_history = read_agent_status_history(hours=self.timeline_hours, history_file=history_path)
        for ts, agent, status, activity in all_history:
            if agent not in self._agent_histories:
                self._agent_histories[agent] = []
            self._agent_histories[agent].append((ts, status))

        # Force layout refresh when content changes (agent count may have changed)
        self.refresh(layout=True)

    def _build_timeline(self, history: list, state_to_char: callable) -> str:
        """Build a timeline string from history data.

        Args:
            history: List of (timestamp, state) tuples
            state_to_char: Function to convert state to display character

        Returns:
            String of timeline_width characters representing the timeline
        """
        width = self.timeline_width
        if not history:
            return "─" * width

        now = datetime.now()
        start_time = now - timedelta(hours=self.timeline_hours)
        slot_duration = timedelta(hours=self.timeline_hours) / width

        # Initialize timeline with empty slots
        timeline = ["─"] * width

        # Fill in slots based on history
        for ts, state in history:
            if ts < start_time:
                continue
            # Calculate which slot this belongs to
            elapsed = ts - start_time
            slot_idx = int(elapsed / slot_duration)
            if 0 <= slot_idx < width:
                timeline[slot_idx] = state_to_char(state)

        return "".join(timeline)

    def render(self) -> Text:
        """Render the timeline visualization."""
        content = Text()
        now = datetime.now()
        width = self.timeline_width

        # Calculate baseline slot position if baseline > 0
        try:
            baseline_minutes = getattr(self.app, 'baseline_minutes', 0)
        except Exception:
            baseline_minutes = 0
        baseline_slot = None
        if baseline_minutes > 0:
            baseline_hours = baseline_minutes / 60.0
            if baseline_hours <= self.timeline_hours:
                # Position from right (now = width-1, -3h = 0)
                baseline_slot = width - 1 - int((baseline_hours / self.timeline_hours) * (width - 1))

        # Time scale header
        label_w = self.label_width
        content.append("Timeline: ", style="bold")
        content.append(f"-{self.timeline_hours:.0f}h", style="dim")
        header_padding = max(0, width - 10)
        content.append(" " * header_padding, style="dim")
        content.append("now", style="dim")
        content.append("\n")

        # User presence timeline - group by time slots like agent timelines
        # Align with agent names using dynamic label width (#75)
        content.append(f"  {'User:':<{label_w}} ", style="cyan")
        if self._presence_history:
            slot_states = build_timeline_slots(
                self._presence_history, width, self.timeline_hours, now
            )
            # Render timeline with colors, including baseline marker
            for i in range(width):
                if i == baseline_slot:
                    content.append("|", style="bold cyan")
                elif i in slot_states:
                    state = slot_states[i]
                    char = presence_state_to_char(state)
                    color = get_presence_color(state)
                    content.append(char, style=color)
                else:
                    content.append("─", style="dim")
        elif not MACOS_APIS_AVAILABLE:
            # Show install instructions when presence deps not installed (macOS only)
            msg = "macOS only - pip install overcode[presence]"
            content.append(msg[:width], style="dim italic")
        else:
            # Empty timeline but still show baseline marker
            for i in range(width):
                if i == baseline_slot:
                    content.append("|", style="bold cyan")
                else:
                    content.append("─", style="dim")
        content.append("\n")

        # Agent timelines
        for session in self.sessions:
            agent_name = session.name
            history = self._agent_histories.get(agent_name, [])

            # Use dynamic label width (#75)
            display_name = truncate_name(agent_name, max_len=label_w)
            content.append(f"  {display_name} ", style="cyan")

            green_slots = 0
            total_slots = 0
            if history:
                slot_states = build_timeline_slots(history, width, self.timeline_hours, now)
                # Render timeline with colors, including baseline marker
                for i in range(width):
                    if i == baseline_slot:
                        content.append("|", style="bold cyan")
                        # Still count the underlying slot for percentage
                        if i in slot_states:
                            total_slots += 1
                            if slot_states[i] == "running":
                                green_slots += 1
                    elif i in slot_states:
                        status = slot_states[i]
                        char = agent_status_to_char(status)
                        color = get_agent_timeline_color(status)
                        content.append(char, style=color)
                        total_slots += 1
                        if status == "running":
                            green_slots += 1
                    else:
                        content.append("─", style="dim")
            else:
                # Empty timeline but still show baseline marker
                for i in range(width):
                    if i == baseline_slot:
                        content.append("|", style="bold cyan")
                    else:
                        content.append("─", style="dim")

            # Show percentage green in last 3 hours
            if total_slots > 0:
                pct = green_slots / total_slots * 100
                pct_style = "bold green" if pct >= 50 else "bold red"
                content.append(f" {pct:>3.0f}%", style=pct_style)
            else:
                content.append("   - ", style="dim")

            content.append("\n")

        # Legend (combined on one line to save space)
        content.append(f"  {'Legend:':<14} ", style="dim")
        content.append("█", style="green")
        content.append("active/running ", style="dim")
        content.append("▒", style="yellow")
        content.append("inactive ", style="dim")
        content.append("░", style="red")
        content.append("waiting/away ", style="dim")
        content.append("░", style="dim")
        content.append("asleep ", style="dim")
        content.append("×", style="dim")
        content.append("terminated", style="dim")

        return content
