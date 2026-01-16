"""
Monitor Daemon state management.

This module defines the official interface between the Monitor Daemon
and its consumers (TUI, Supervisor Daemon).

The Monitor Daemon is the single source of truth for:
- Agent status detection
- Time tracking (green_time_seconds, non_green_time_seconds)
- Claude Code stats (tokens, interactions)
- User presence state (macOS only)
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .settings import (
    PATHS,
    DAEMON,
    get_monitor_daemon_state_path,
)


@dataclass
class SessionDaemonState:
    """Per-session state published by Monitor Daemon.

    This is the authoritative source for session metrics.
    The TUI and Supervisor Daemon should read from here,
    not from Claude Code files directly.
    """

    # Session identity
    session_id: str = ""
    name: str = ""
    tmux_window: int = 0

    # Status (from StatusDetector)
    current_status: str = "unknown"  # running, waiting_user, waiting_supervisor, no_instructions, terminated
    current_activity: str = ""
    status_since: Optional[str] = None  # ISO timestamp

    # Time tracking (authoritative - only Monitor Daemon updates these)
    green_time_seconds: float = 0.0
    non_green_time_seconds: float = 0.0

    # Claude Code stats (synced from ~/.claude/projects/)
    interaction_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    estimated_cost_usd: float = 0.0
    median_work_time: float = 0.0

    # Session metadata
    repo_name: Optional[str] = None
    branch: Optional[str] = None
    standing_instructions: str = ""
    standing_orders_complete: bool = False
    steers_count: int = 0

    # Additional session info (for web dashboard parity with TUI)
    start_time: Optional[str] = None  # ISO timestamp when session started
    permissiveness_mode: str = "normal"  # normal, permissive, bypass
    start_directory: Optional[str] = None  # For git diff stats

    # Activity summary (from SummarizerComponent)
    activity_summary: str = ""
    activity_summary_updated: Optional[str] = None  # ISO timestamp

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "session_id": self.session_id,
            "name": self.name,
            "tmux_window": self.tmux_window,
            "current_status": self.current_status,
            "current_activity": self.current_activity,
            "status_since": self.status_since,
            "green_time_seconds": self.green_time_seconds,
            "non_green_time_seconds": self.non_green_time_seconds,
            "interaction_count": self.interaction_count,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "median_work_time": self.median_work_time,
            "repo_name": self.repo_name,
            "branch": self.branch,
            "standing_instructions": self.standing_instructions,
            "standing_orders_complete": self.standing_orders_complete,
            "steers_count": self.steers_count,
            "start_time": self.start_time,
            "permissiveness_mode": self.permissiveness_mode,
            "start_directory": self.start_directory,
            "activity_summary": self.activity_summary,
            "activity_summary_updated": self.activity_summary_updated,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SessionDaemonState":
        """Create from dictionary."""
        return cls(
            session_id=data.get("session_id", ""),
            name=data.get("name", ""),
            tmux_window=data.get("tmux_window", 0),
            current_status=data.get("current_status", "unknown"),
            current_activity=data.get("current_activity", ""),
            status_since=data.get("status_since"),
            green_time_seconds=data.get("green_time_seconds", 0.0),
            non_green_time_seconds=data.get("non_green_time_seconds", 0.0),
            interaction_count=data.get("interaction_count", 0),
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            cache_creation_tokens=data.get("cache_creation_tokens", 0),
            cache_read_tokens=data.get("cache_read_tokens", 0),
            estimated_cost_usd=data.get("estimated_cost_usd", 0.0),
            median_work_time=data.get("median_work_time", 0.0),
            repo_name=data.get("repo_name"),
            branch=data.get("branch"),
            standing_instructions=data.get("standing_instructions", ""),
            standing_orders_complete=data.get("standing_orders_complete", False),
            steers_count=data.get("steers_count", 0),
            start_time=data.get("start_time"),
            permissiveness_mode=data.get("permissiveness_mode", "normal"),
            start_directory=data.get("start_directory"),
            activity_summary=data.get("activity_summary", ""),
            activity_summary_updated=data.get("activity_summary_updated"),
        )


@dataclass
class MonitorDaemonState:
    """State published by Monitor Daemon for TUI and Supervisor Daemon.

    This is the official interface for reading monitoring data.
    Consumers should use MonitorDaemonState.load() to get current state.
    """

    # Daemon metadata
    pid: int = 0
    status: str = "stopped"  # starting, active, idle, sleeping, stopped
    loop_count: int = 0
    current_interval: int = field(default_factory=lambda: DAEMON.interval_fast)
    last_loop_time: Optional[str] = None  # ISO timestamp
    started_at: Optional[str] = None  # ISO timestamp
    daemon_version: int = 0  # Version of daemon code

    # Session states (one per agent)
    sessions: List[SessionDaemonState] = field(default_factory=list)

    # Presence state (optional, macOS only)
    presence_available: bool = False
    presence_state: Optional[int] = None  # 1=locked/sleep, 2=inactive, 3=active
    presence_idle_seconds: Optional[float] = None

    # Summary metrics (computed from sessions)
    total_green_time: float = 0.0
    total_non_green_time: float = 0.0
    green_sessions: int = 0
    non_green_sessions: int = 0

    # Supervisor aggregates (from SupervisorStats + sessions)
    total_supervisions: int = 0      # Sum of steers_count across sessions
    supervisor_launches: int = 0     # Times daemon claude was launched
    supervisor_tokens: int = 0       # Total tokens used by daemon claude

    # Daemon Claude run status (from SupervisorStats)
    supervisor_claude_running: bool = False
    supervisor_claude_started_at: Optional[str] = None  # ISO timestamp
    supervisor_claude_total_run_seconds: float = 0.0   # Cumulative run time

    # Relay status (for remote monitoring)
    relay_enabled: bool = False
    relay_last_push: Optional[str] = None  # ISO timestamp of last successful push
    relay_last_status: str = "disabled"  # "ok", "error", "disabled"

    # Summarizer status
    summarizer_enabled: bool = False
    summarizer_available: bool = False  # True if OPENAI_API_KEY is set
    summarizer_calls: int = 0
    summarizer_cost_usd: float = 0.0

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "pid": self.pid,
            "status": self.status,
            "loop_count": self.loop_count,
            "current_interval": self.current_interval,
            "last_loop_time": self.last_loop_time,
            "started_at": self.started_at,
            "daemon_version": self.daemon_version,
            "sessions": [s.to_dict() for s in self.sessions],
            "presence_available": self.presence_available,
            "presence_state": self.presence_state,
            "presence_idle_seconds": self.presence_idle_seconds,
            "total_green_time": self.total_green_time,
            "total_non_green_time": self.total_non_green_time,
            "green_sessions": self.green_sessions,
            "non_green_sessions": self.non_green_sessions,
            "total_supervisions": self.total_supervisions,
            "supervisor_launches": self.supervisor_launches,
            "supervisor_tokens": self.supervisor_tokens,
            "supervisor_claude_running": self.supervisor_claude_running,
            "supervisor_claude_started_at": self.supervisor_claude_started_at,
            "supervisor_claude_total_run_seconds": self.supervisor_claude_total_run_seconds,
            "relay_enabled": self.relay_enabled,
            "relay_last_push": self.relay_last_push,
            "relay_last_status": self.relay_last_status,
            "summarizer_enabled": self.summarizer_enabled,
            "summarizer_available": self.summarizer_available,
            "summarizer_calls": self.summarizer_calls,
            "summarizer_cost_usd": self.summarizer_cost_usd,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MonitorDaemonState":
        """Create from dictionary."""
        sessions = [
            SessionDaemonState.from_dict(s)
            for s in data.get("sessions", [])
        ]

        return cls(
            pid=data.get("pid", 0),
            status=data.get("status", "stopped"),
            loop_count=data.get("loop_count", 0),
            current_interval=data.get("current_interval", DAEMON.interval_fast),
            last_loop_time=data.get("last_loop_time"),
            started_at=data.get("started_at"),
            daemon_version=data.get("daemon_version", 0),
            sessions=sessions,
            presence_available=data.get("presence_available", False),
            presence_state=data.get("presence_state"),
            presence_idle_seconds=data.get("presence_idle_seconds"),
            total_green_time=data.get("total_green_time", 0.0),
            total_non_green_time=data.get("total_non_green_time", 0.0),
            green_sessions=data.get("green_sessions", 0),
            non_green_sessions=data.get("non_green_sessions", 0),
            total_supervisions=data.get("total_supervisions", 0),
            supervisor_launches=data.get("supervisor_launches", 0),
            supervisor_tokens=data.get("supervisor_tokens", 0),
            supervisor_claude_running=data.get("supervisor_claude_running", False),
            supervisor_claude_started_at=data.get("supervisor_claude_started_at"),
            supervisor_claude_total_run_seconds=data.get("supervisor_claude_total_run_seconds", 0.0),
            relay_enabled=data.get("relay_enabled", False),
            relay_last_push=data.get("relay_last_push"),
            relay_last_status=data.get("relay_last_status", "disabled"),
            summarizer_enabled=data.get("summarizer_enabled", False),
            summarizer_available=data.get("summarizer_available", False),
            summarizer_calls=data.get("summarizer_calls", 0),
            summarizer_cost_usd=data.get("summarizer_cost_usd", 0.0),
        )

    def update_summaries(self) -> None:
        """Recompute summary metrics from session data."""
        self.total_green_time = sum(s.green_time_seconds for s in self.sessions)
        self.total_non_green_time = sum(s.non_green_time_seconds for s in self.sessions)
        self.green_sessions = sum(1 for s in self.sessions if s.current_status == "running")
        self.non_green_sessions = len(self.sessions) - self.green_sessions
        self.total_supervisions = sum(s.steers_count for s in self.sessions)

    def get_session(self, session_id: str) -> Optional[SessionDaemonState]:
        """Get session state by ID."""
        for session in self.sessions:
            if session.session_id == session_id:
                return session
        return None

    def get_session_by_name(self, name: str) -> Optional[SessionDaemonState]:
        """Get session state by name."""
        for session in self.sessions:
            if session.name == name:
                return session
        return None

    def save(self, state_file: Optional[Path] = None) -> None:
        """Save state to file for consumers to read.

        Args:
            state_file: Optional path override (for testing)
        """
        path = state_file or PATHS.monitor_daemon_state
        path.parent.mkdir(parents=True, exist_ok=True)

        # Update summaries before saving
        self.update_summaries()

        with open(path, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, state_file: Optional[Path] = None) -> Optional["MonitorDaemonState"]:
        """Load state from file.

        Args:
            state_file: Optional path override (for testing)

        Returns:
            MonitorDaemonState if file exists and is valid, None otherwise
        """
        path = state_file or PATHS.monitor_daemon_state
        if not path.exists():
            return None

        try:
            with open(path) as f:
                data = json.load(f)
            return cls.from_dict(data)
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            return None

    def is_stale(self, buffer_seconds: float = 30.0) -> bool:
        """Check if the state is stale (daemon may have crashed).

        Uses current_interval + buffer to determine staleness. This way, a daemon
        sleeping for 300s won't be considered stale after just 30s.

        Args:
            buffer_seconds: Extra time beyond current_interval before considered stale

        Returns:
            True if state is older than (current_interval + buffer_seconds)
        """
        if not self.last_loop_time:
            return True

        try:
            last_time = datetime.fromisoformat(self.last_loop_time)
            age = (datetime.now() - last_time).total_seconds()
            # Allow current_interval + buffer before considering stale
            max_age = self.current_interval + buffer_seconds
            return age > max_age
        except (ValueError, TypeError):
            return True


def get_monitor_daemon_state(session: Optional[str] = None) -> Optional[MonitorDaemonState]:
    """Get the current monitor daemon state from file.

    Convenience function for TUI and other consumers.

    Args:
        session: tmux session name. If None, uses default from config.

    Returns:
        MonitorDaemonState if daemon is running and state file exists, None otherwise
    """
    if session is None:
        session = DAEMON.default_tmux_session
    state_path = get_monitor_daemon_state_path(session)
    return MonitorDaemonState.load(state_path)
