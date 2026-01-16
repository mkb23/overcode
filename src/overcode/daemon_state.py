"""
Daemon state management.

Tracks and persists daemon state for communication with the TUI.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from .settings import PATHS, DAEMON


# Daemon operation modes
MODE_OFF = "off"           # Daemon not running
MODE_MONITOR = "monitor"   # Track stats/state but never launch supervisor claude
MODE_SUPERVISE = "supervise"  # Full supervision (monitor + launch claude when needed)


@dataclass
class DaemonState:
    """Tracks daemon state for TUI display.

    This class is used by:
    - The daemon to persist its current state
    - The TUI to read and display daemon status
    """

    loop_count: int = 0
    current_interval: int = field(default_factory=lambda: DAEMON.interval_fast)
    last_loop_time: Optional[datetime] = None
    started_at: Optional[datetime] = None
    status: str = "starting"  # starting, active, idle, sleeping, waiting, supervising, no_agents, stopped
    last_activity: Optional[datetime] = None
    daemon_claude_launches: int = 0
    mode: str = MODE_SUPERVISE  # off, monitor, supervise

    def to_dict(self) -> dict:
        """Convert state to dictionary for JSON serialization."""
        return {
            "loop_count": self.loop_count,
            "current_interval": self.current_interval,
            "last_loop_time": self.last_loop_time.isoformat() if self.last_loop_time else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "status": self.status,
            "last_activity": self.last_activity.isoformat() if self.last_activity else None,
            "daemon_claude_launches": self.daemon_claude_launches,
            "mode": self.mode,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DaemonState":
        """Create state from dictionary."""
        state = cls()
        state.loop_count = data.get("loop_count", 0)
        state.current_interval = data.get("current_interval", DAEMON.interval_fast)
        state.status = data.get("status", "unknown")
        state.daemon_claude_launches = data.get("daemon_claude_launches", 0)
        state.mode = data.get("mode", MODE_SUPERVISE)

        if data.get("last_loop_time"):
            state.last_loop_time = datetime.fromisoformat(data["last_loop_time"])
        if data.get("started_at"):
            state.started_at = datetime.fromisoformat(data["started_at"])
        if data.get("last_activity"):
            state.last_activity = datetime.fromisoformat(data["last_activity"])

        return state

    def save(self, state_file: Optional[Path] = None) -> None:
        """Save state to file for TUI to read.

        Args:
            state_file: Optional path override (for testing)
        """
        path = state_file or PATHS.daemon_state
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, state_file: Optional[Path] = None) -> Optional["DaemonState"]:
        """Load state from file (used by TUI).

        Args:
            state_file: Optional path override (for testing)

        Returns:
            DaemonState if file exists and is valid, None otherwise
        """
        path = state_file or PATHS.daemon_state
        if not path.exists():
            return None

        try:
            with open(path) as f:
                data = json.load(f)
            return cls.from_dict(data)
        except (json.JSONDecodeError, KeyError, ValueError):
            return None


def get_daemon_state() -> Optional[DaemonState]:
    """Get the current daemon state from file.

    Convenience function for TUI and other consumers.

    Returns:
        DaemonState if daemon is running and state file exists, None otherwise
    """
    return DaemonState.load()
