"""
Status timeline widget for TUI.

Shows historical status timelines for user presence and agents.
"""

from datetime import datetime
from typing import Dict, List, Mapping, Tuple

from textual.widgets import Static
from rich.cells import cell_len
from rich.text import Text

from .. import swarm_history
from ..presence_logger import read_presence_history
from ..settings import get_agent_history_path, get_swarm_history_path
from ..status_constants import emoji_or_ascii
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
from ..status_constants import is_green_status


def read_history_data(tmux_session: str, hours: float) -> tuple:
    """Read the timeline's history from log files. Safe off the main thread.

    Returns:
        (presence_history, agent_histories) for StatusTimeline.apply_history_data().
    """
    presence_history = read_presence_history(hours=hours)
    agent_histories = {}

    # The carry is each agent's state at the window's left edge; rows
    # are written on change plus a keepalive, and build_timeline_slots
    # forward-fills from it.
    history_path = get_agent_history_path(tmux_session)
    all_history = read_agent_status_history(
        hours=hours, history_file=history_path, carry=True
    )
    for ts, agent, status, activity, *_ in all_history:
        if agent not in agent_histories:
            agent_histories[agent] = []
        agent_histories[agent].append((ts, status))

    return presence_history, agent_histories


def read_swarm_data(tmux_session: str, hours: float) -> dict:
    """The swarm density rows' history (``swarm_history.read_swarm_history``). Off the main thread."""
    return swarm_history.read_swarm_history(get_swarm_history_path(tmux_session), hours)


# Light -> the strip's colour (design §5: 🟠 > 🟢 > 🟡)
STRIP_COLOURS = {"orange": "orange1", "green": "green", "yellow": "yellow", "idle": "grey50"}

Strip = Tuple[str, str, list]  # (marker, title, points)


def swarm_strips(session_id: str, histories: Mapping[str, Mapping[str, list]],
                 expanded: bool) -> List[Strip]:
    """The density strips under one agent's timeline row, in the list's fold state.

    Collapsed: one strip for the whole swarm. Expanded (→ in the list):
    one per group, workflows first (oldest first), then the team, then
    loose subagents, as the list orders its group rows. Only groups with
    something live inside the window get a strip.
    """
    groups = histories.get(session_id) or {}
    if not expanded:
        points = groups.get(swarm_history.TOTAL) or []
        return [("👥", "swarm", points)] if swarm_history.has_live(points) else []

    def order(item):
        group, points = item
        rank = 0 if group.startswith("wf/") else (1 if group == "tm" else 2)
        return (rank, points[0].ts if points else datetime.min, group)

    out: List[Strip] = []
    for group, points in sorted(((g, p) for g, p in groups.items() if g != swarm_history.TOTAL),
                                key=order):
        if not swarm_history.has_live(points):
            continue
        last = points[-1]
        if group.startswith("wf/"):
            out.append(("🧩", last.title or "workflow", points))
        else:
            out.append(("👥", last.title or ("team" if group == "tm" else "agents"), points))
    return out


def _strip_label(marker: str, title: str, emoji_free: bool) -> str:
    if marker == "🧩" and emoji_free:
        marker = "Wf"  # as the list's workflow rows
    else:
        marker = emoji_or_ascii(marker, emoji_free)
    return f"└ {marker} {title}"


def _fit(text: str, width: int) -> str:
    """``text`` cut and padded to ``width`` terminal cells (emoji are two)."""
    out, used = "", 0
    for ch in text:
        w = cell_len(ch)
        if used + w > width:
            break
        out += ch
        used += w
    return out + " " * (width - used)


class StatusTimeline(Static):
    """Widget displaying historical status timelines for user presence and agents.

    Shows the last N hours with each character representing a time slice.
    - User presence: green=TUI active, yellow=active, orange=idle, red=locked
    - Agent status: green=running, red=waiting, grey=terminated

    Timeline hours configurable via ~/.overcode/config.yaml (timeline.hours).
    """

    TIMELINE_HOURS = 3.0  # Default hours
    MIN_NAME_WIDTH = 6    # Minimum width for agent names
    MAX_NAME_WIDTH = 30   # Maximum width for agent names
    MIN_TIMELINE = 20     # Minimum timeline width
    DEFAULT_TIMELINE = 60 # Fallback if can't detect width

    # Swarm density rows (0.6.1 phase 4): {agent id: {group: [Point]}} from
    # swarm_history.csv, and the parents expanded in the list (the fold state
    # the strips follow). Class defaults, so a widget built without __init__
    # (unit tests) draws none.
    _swarm_histories: Dict[str, dict] = {}
    swarm_expanded: frozenset = frozenset()

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
        if self._swarm_histories:
            # The density strips' labels ("└ 🧩 review") share the column
            emoji_free = self._emoji_free()
            for session in self._strip_sessions():
                for marker, title, _ in swarm_strips(session.id, self._swarm_histories,
                                                     session.id in self.swarm_expanded):
                    longest = max(longest, cell_len(_strip_label(marker, title, emoji_free)))
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

    def set_hours(self, hours: float, sessions: list) -> None:
        """Change timeline scope and refresh (#191)."""
        self.timeline_hours = hours
        self.update_history(sessions)

    def fetch_history_data(self, sessions: list) -> tuple:
        """read_history_data for this widget's session and scope (blocking).

        Returns:
            (presence_history, agent_histories) tuple for apply_history_data().
        """
        return read_history_data(self.tmux_session, self.timeline_hours)

    def apply_history_data(self, sessions: list, presence_history: list, agent_histories: dict,
                           swarm_histories: dict = None) -> None:
        """Apply pre-fetched history data to the widget. Call on main thread."""
        self.sessions = sessions
        self._presence_history = presence_history
        self._agent_histories = agent_histories
        if swarm_histories is not None:
            self._swarm_histories = swarm_histories
        self.refresh(layout=True)

    def set_swarm_fold(self, expanded) -> bool:
        """Follow the list's expanded parents; repaint only if a drawn strip changes."""
        expanded = frozenset(expanded)
        if expanded == self.swarm_expanded:
            return False
        changed = expanded ^ self.swarm_expanded
        self.swarm_expanded = expanded
        shown = {getattr(s, "id", None) for s in self.sessions
                 if not getattr(s, "is_remote", False)}
        if any(sid in shown and sid in self._swarm_histories for sid in changed):
            self.refresh(layout=True)
            return True
        return False

    def update_history(self, sessions: list) -> None:
        """Refresh history data from log files (blocking, legacy).

        For non-blocking updates, use fetch_history_data() in a worker
        thread then apply_history_data() on the main thread.
        """
        presence_history, agent_histories = self.fetch_history_data(sessions)
        self.apply_history_data(sessions, presence_history, agent_histories)

    def render(self) -> Text:
        """Render the timeline visualization."""
        content = Text()
        now = datetime.now()
        width = self.timeline_width

        # Calculate baseline slot position if baseline > 0
        try:
            baseline_minutes = getattr(self.app, 'baseline_minutes', 0)
        except Exception:
            baseline_minutes = 0  # No app context (e.g., in tests)
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
        else:
            # Empty timeline but still show baseline marker
            for i in range(width):
                if i == baseline_slot:
                    content.append("|", style="bold cyan")
                else:
                    content.append("─", style="dim")
        content.append("\n")

        # Agent timelines
        drew_strips = False
        for session in self.sessions:
            agent_name = session.name
            # Remote sessions are keyed with hostname prefix for disambiguation
            if getattr(session, 'is_remote', False) and getattr(session, 'source_host', ''):
                history_key = f"{session.source_host}/{agent_name}"
            else:
                history_key = agent_name
            history = self._agent_histories.get(history_key, [])

            # Use dynamic label width (#75)
            display_name = truncate_name(agent_name, max_len=label_w)
            content.append(f"  {display_name} ", style="cyan")

            green_slots = 0
            total_slots = 0
            if history:
                slot_states = build_timeline_slots(history, width, self.timeline_hours, now)
                # Render timeline with colors, including baseline marker
                skip_next = False
                for i in range(width):
                    if skip_next:
                        skip_next = False
                        # Still count the underlying slot for percentage
                        if i in slot_states:
                            total_slots += 1
                            if is_green_status(slot_states[i]):
                                green_slots += 1
                        continue
                    if i == baseline_slot:
                        content.append("|", style="bold cyan")
                        # Still count the underlying slot for percentage
                        if i in slot_states:
                            total_slots += 1
                            if is_green_status(slot_states[i]):
                                green_slots += 1
                    elif i in slot_states:
                        status = slot_states[i]
                        if status == "heartbeat_start":
                            # 💚 is 2 cells wide — skip next slot to keep alignment
                            content.append("💚")
                            skip_next = True
                        else:
                            char = agent_status_to_char(status)
                            color = get_agent_timeline_color(status)
                            content.append(char, style=color)
                        total_slots += 1
                        if is_green_status(status):
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
            if self._swarm_histories and not getattr(session, 'is_remote', False):
                drew_strips |= self._render_strips(content, session, width, label_w, now,
                                                   baseline_slot)

        # Legend (combined on one line to save space)
        content.append(f"  {'Legend:':<14} ", style="dim")
        content.append("█", style="green")
        content.append("TUI/running ", style="dim")
        content.append("▓", style="yellow")
        content.append("active ", style="dim")
        content.append("💚", style="")
        content.append("heartbeat ", style="dim")
        content.append("▒", style="orange1")
        content.append("idle ", style="dim")
        content.append("░", style="red")
        content.append("locked ", style="dim")
        content.append("×", style="dim")
        content.append("terminated", style="dim")
        if drew_strips:
            content.append(" ▁▄█", style="green")
            content.append("delegated work (mean live)", style="dim")

        return content

    def _strip_sessions(self) -> list:
        """The rows that can have strips: this machine's agents with an id."""
        return [s for s in self.sessions
                if not getattr(s, "is_remote", False) and isinstance(getattr(s, "id", None), str)]

    def _emoji_free(self) -> bool:
        try:
            return getattr(self.app, "emoji_free", False) is True
        except Exception:
            return False  # no app context (tests)

    def _render_strips(self, content: Text, session, width: int, label_w: int,
                       now: datetime, baseline_slot) -> bool:
        """The density strips under ``session``'s row (see ``swarm_strips``)."""
        sid = getattr(session, "id", None)
        if not isinstance(sid, str):
            return False
        strips = swarm_strips(sid, self._swarm_histories, sid in self.swarm_expanded)
        emoji_free = self._emoji_free()
        for marker, title, points in strips:
            label = _fit(_strip_label(marker, title, emoji_free), label_w)
            content.append(f"  {label} ", style="dim cyan")
            slots = swarm_history.density(points, width, self.timeline_hours, now)
            for i, (slot, glyph) in enumerate(zip(slots, swarm_history.glyphs(slots))):
                if i == baseline_slot:
                    content.append("|", style="bold cyan")
                elif slot is None:
                    content.append(glyph)
                elif slot.failed:
                    content.append(glyph if glyph.strip() else "▁", style="bold red underline")
                elif slot.light is None:
                    content.append(glyph, style="dim")
                else:
                    content.append(glyph, style=STRIP_COLOURS[slot.light])
            peak = swarm_history.peak_live(points)
            content.append(f" ↑{peak:<3}"[:5], style="dim")
            content.append("\n")
        return bool(strips)
