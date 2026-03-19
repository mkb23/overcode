"""
Monitor Daemon state management.

This module defines the official interface between the Monitor Daemon
and its consumers (TUI, Supervisor Daemon).

The Monitor Daemon is the single source of truth for:
- Agent status detection
- Time tracking (green_time_seconds, non_green_time_seconds)
- Claude Code stats (tokens, interactions)
- User presence state
"""

import json
import logging
import os
import tempfile
import dataclasses
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .settings import (
    PATHS,
    DAEMON,
    get_monitor_daemon_state_path,
)

logger = logging.getLogger(__name__)


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
    tmux_window: str = ""

    # Status (from StatusDetector)
    current_status: str = "unknown"  # running, waiting_user, waiting_approval, waiting_heartbeat, terminated
    current_activity: str = ""
    status_since: Optional[str] = None  # ISO timestamp

    # Time tracking (authoritative - only Monitor Daemon updates these)
    green_time_seconds: float = 0.0
    non_green_time_seconds: float = 0.0
    sleep_time_seconds: float = 0.0

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
    is_asleep: bool = False  # Agent is paused and excluded from stats (#70)
    time_context_enabled: bool = False  # Per-agent time awareness toggle

    # Agent priority value (#61)
    agent_value: int = 1000  # Default 1000, higher = more important

    # Activity summaries (from SummarizerComponent)
    # Short: current activity - what's happening right now (~50 chars)
    activity_summary: str = ""
    activity_summary_updated: Optional[str] = None  # ISO timestamp
    # Context: wider context - what's being worked on overall (~80 chars)
    activity_summary_context: str = ""
    activity_summary_context_updated: Optional[str] = None  # ISO timestamp

    # Heartbeat state (#171)
    heartbeat_enabled: bool = False
    heartbeat_frequency_seconds: int = 300
    heartbeat_paused: bool = False
    last_heartbeat_time: Optional[str] = None
    next_heartbeat_due: Optional[str] = None  # Computed
    running_from_heartbeat: bool = False  # True if agent started running from heartbeat
    waiting_for_heartbeat: bool = False  # True if waiting but heartbeat will auto-resume

    # Cost budget (#173)
    cost_budget_usd: float = 0.0  # 0 = unlimited
    budget_exceeded: bool = False  # True when cost >= budget
    subtree_cost_usd: float = 0.0  # self + all descendants (0 = leaf or not computed)

    # Agent hierarchy (#244)
    parent_name: Optional[str] = None  # Name of parent agent (None = root)
    depth: int = 0  # Computed by daemon each cycle
    children_count: int = 0  # Computed by daemon each cycle

    # Model
    model: Optional[str] = None  # Claude model (e.g. "sonnet", "opus")

    # Oversight system
    oversight_policy: str = "wait"
    oversight_timeout_seconds: float = 0.0
    oversight_deadline: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SessionDaemonState":
        """Create from dictionary, ignoring unknown keys."""
        fields = {f.name for f in dataclasses.fields(cls)}
        known = {k: v for k, v in data.items() if k in fields}
        return cls(**known)


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

    # Presence state
    presence_available: bool = False
    presence_state: Optional[int] = None  # 0=asleep, 1=locked, 2=idle, 3=active, 4=tui_active
    presence_idle_seconds: Optional[float] = None

    # Summary metrics (computed from sessions)
    total_green_time: float = 0.0
    total_non_green_time: float = 0.0
    total_sleep_time: float = 0.0
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

    # Summarizer status
    summarizer_enabled: bool = False
    summarizer_available: bool = False
    summarizer_calls: int = 0
    summarizer_cost_usd: float = 0.0

    # Relay status (for remote monitoring)
    relay_enabled: bool = False
    relay_last_push: Optional[str] = None  # ISO timestamp of last successful push
    relay_last_status: str = "disabled"  # "ok", "error", "disabled"

    # Untracked tmux windows (#344)
    untracked_window_count: int = 0

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "MonitorDaemonState":
        """Create from dictionary."""
        sessions = [
            SessionDaemonState.from_dict(s)
            for s in data.get("sessions", [])
        ]
        fields = {f.name for f in dataclasses.fields(cls)}
        known = {k: v for k, v in data.items() if k in fields and k != "sessions"}
        return cls(sessions=sessions, **known)

    def update_summaries(self) -> None:
        """Recompute summary metrics from session data."""
        self.total_green_time = sum(s.green_time_seconds for s in self.sessions)
        self.total_non_green_time = sum(s.non_green_time_seconds for s in self.sessions)
        self.total_sleep_time = sum(s.sleep_time_seconds for s in self.sessions)
        self.green_sessions = sum(1 for s in self.sessions if s.current_status in ("running", "running_heartbeat"))
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

        # Atomic write: temp file + fsync + rename
        fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(self.to_dict(), f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            Path(tmp_path).rename(path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError as e:
                logger.debug("Failed to clean up temp file %s: %s", tmp_path, e)
            raise

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
