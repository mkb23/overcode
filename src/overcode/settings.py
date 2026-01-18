"""
Centralized configuration and settings for Overcode.

This module consolidates all configuration constants, paths, and settings
that were previously scattered across multiple modules.

Configuration hierarchy:
1. Environment variables (highest priority)
2. Config file (~/.overcode/config.yaml)
3. Default values (lowest priority)

TODO: Make INTERVAL_FAST/SLOW/IDLE configurable via config.yaml
"""

import os

# =============================================================================
# Version - increment when daemon code changes significantly
# =============================================================================

DAEMON_VERSION = 2  # Increment when daemon behavior changes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Set

import yaml


# =============================================================================
# Base Paths
# =============================================================================

def get_overcode_dir() -> Path:
    """Get the overcode data directory.

    Can be overridden with OVERCODE_DIR environment variable.
    """
    env_dir = os.environ.get("OVERCODE_DIR")
    if env_dir:
        return Path(env_dir)
    return Path.home() / ".overcode"


def get_state_dir() -> Path:
    """Get the state directory for session files.

    Can be overridden with OVERCODE_STATE_DIR environment variable.
    """
    env_dir = os.environ.get("OVERCODE_STATE_DIR")
    if env_dir:
        return Path(env_dir)
    return get_overcode_dir() / "sessions"


def get_log_dir() -> Path:
    """Get the log directory."""
    return get_overcode_dir() / "logs"


# =============================================================================
# File Paths
# =============================================================================

@dataclass
class OvercodePaths:
    """All file paths used by Overcode."""

    # Base directory
    base_dir: Path = field(default_factory=get_overcode_dir)

    @property
    def config_file(self) -> Path:
        """Configuration file path."""
        return self.base_dir / "config.yaml"

    @property
    def state_dir(self) -> Path:
        """Session state directory."""
        return get_state_dir()

    @property
    def sessions_file(self) -> Path:
        """Sessions state file."""
        return self.state_dir / "sessions.json"

    @property
    def log_dir(self) -> Path:
        """Log directory."""
        return get_log_dir()

    @property
    def daemon_log(self) -> Path:
        """Daemon log file."""
        return self.base_dir / "daemon.log"

    @property
    def daemon_pid(self) -> Path:
        """Daemon PID file."""
        return self.base_dir / "daemon.pid"

    @property
    def daemon_state(self) -> Path:
        """Daemon state file (legacy - supervisor daemon)."""
        return self.base_dir / "daemon_state.json"

    @property
    def monitor_daemon_state(self) -> Path:
        """Monitor daemon state file (new - single source of truth)."""
        return self.base_dir / "monitor_daemon_state.json"

    @property
    def monitor_daemon_pid(self) -> Path:
        """Monitor daemon PID file."""
        return self.base_dir / "monitor_daemon.pid"

    @property
    def supervisor_daemon_pid(self) -> Path:
        """Supervisor daemon PID file."""
        return self.base_dir / "supervisor_daemon.pid"

    @property
    def presence_pid(self) -> Path:
        """Presence logger PID file."""
        return self.base_dir / "presence.pid"

    @property
    def presence_log(self) -> Path:
        """Presence log file."""
        return self.base_dir / "presence_log.csv"

    @property
    def activity_signal(self) -> Path:
        """Activity signal file for daemon."""
        return self.base_dir / "activity_signal"

    @property
    def agent_history(self) -> Path:
        """Agent status history CSV."""
        return self.base_dir / "agent_status_history.csv"

    @property
    def supervisor_log(self) -> Path:
        """Supervisor log file."""
        return self.base_dir / "supervisor.log"


# Global paths instance
PATHS = OvercodePaths()


# =============================================================================
# Daemon Settings
# =============================================================================

@dataclass
class DaemonSettings:
    """Settings for the daemon."""

    # Polling intervals (seconds)
    interval_fast: int = 10      # When active or agents working
    interval_slow: int = 300     # When all agents need user input (5 min)
    interval_idle: int = 3600    # When no agents at all (1 hour)

    # Daemon Claude settings
    daemon_claude_timeout: int = 300  # Max wait for daemon claude (5 min)
    daemon_claude_poll: int = 5       # Poll interval for daemon claude

    # Default tmux session name
    default_tmux_session: str = "agents"


# Global daemon settings
DAEMON = DaemonSettings()


# =============================================================================
# Presence Logger Settings
# =============================================================================

@dataclass
class PresenceSettings:
    """Settings for the presence logger."""

    sample_interval: int = 60   # Seconds between samples
    idle_threshold: int = 60    # Seconds before considered idle


# Global presence settings
PRESENCE = PresenceSettings()


# =============================================================================
# TUI Settings
# =============================================================================

@dataclass
class TUISettings:
    """Settings for the TUI monitor."""

    default_timeline_width: int = 60
    refresh_interval: float = 1.0  # Seconds


# Global TUI settings
TUI = TUISettings()


# =============================================================================
# Config File Loading
# =============================================================================

@dataclass
class UserConfig:
    """User-configurable settings from config.yaml."""

    default_standing_instructions: str = ""
    tmux_session: str = "agents"

    @classmethod
    def load(cls) -> "UserConfig":
        """Load configuration from config file."""
        config_path = PATHS.config_file

        if not config_path.exists():
            return cls()

        try:
            with open(config_path) as f:
                data = yaml.safe_load(f)
                if not isinstance(data, dict):
                    return cls()

                return cls(
                    default_standing_instructions=data.get(
                        "default_standing_instructions", ""
                    ),
                    tmux_session=data.get("tmux_session", "agents"),
                )
        except (yaml.YAMLError, IOError):
            return cls()


# Cached user config (lazy loaded)
_user_config: Optional[UserConfig] = None


def get_user_config() -> UserConfig:
    """Get the user configuration (cached)."""
    global _user_config
    if _user_config is None:
        _user_config = UserConfig.load()
    return _user_config


def reload_user_config() -> UserConfig:
    """Reload the user configuration from disk."""
    global _user_config
    _user_config = UserConfig.load()
    return _user_config


# =============================================================================
# Session-Specific Paths
# =============================================================================

def get_session_dir(session: str) -> Path:
    """Get the directory for session-specific files.

    Each overcode session (tmux session) gets its own subdirectory
    for isolation. This allows running multiple overcode instances
    (e.g., one for work, one for development).

    Respects OVERCODE_STATE_DIR environment variable for test isolation.
    """
    # Use get_state_dir() as base to respect OVERCODE_STATE_DIR
    state_dir = get_state_dir()
    # state_dir is already the sessions directory
    return state_dir / session


def get_monitor_daemon_pid_path(session: str) -> Path:
    """Get monitor daemon PID file path for a specific session."""
    return get_session_dir(session) / "monitor_daemon.pid"


def get_monitor_daemon_state_path(session: str) -> Path:
    """Get monitor daemon state file path for a specific session."""
    return get_session_dir(session) / "monitor_daemon_state.json"


def get_supervisor_daemon_pid_path(session: str) -> Path:
    """Get supervisor daemon PID file path for a specific session."""
    return get_session_dir(session) / "supervisor_daemon.pid"


def get_agent_history_path(session: str) -> Path:
    """Get agent status history CSV path for a specific session."""
    return get_session_dir(session) / "agent_status_history.csv"


def get_activity_signal_path(session: str) -> Path:
    """Get activity signal file path for a specific session."""
    return get_session_dir(session) / "activity_signal"


def signal_activity(session: str = None) -> None:
    """Signal user activity to the daemon (called by TUI on keypress).

    Creates a signal file that the daemon checks each loop.
    When it sees this file, it wakes up and runs immediately.
    This provides responsiveness when users interact with TUI.
    """
    if session is None:
        session = DAEMON.default_tmux_session
    signal_path = get_activity_signal_path(session)
    try:
        signal_path.parent.mkdir(parents=True, exist_ok=True)
        signal_path.touch()
    except OSError:
        pass  # Best effort


def get_supervisor_stats_path(session: str) -> Path:
    """Get supervisor stats file path for a specific session."""
    return get_session_dir(session) / "supervisor_stats.json"


def get_supervisor_log_path(session: str) -> Path:
    """Get supervisor log file path for a specific session."""
    return get_session_dir(session) / "supervisor.log"


def get_web_server_pid_path(session: str) -> Path:
    """Get web server PID file path for a specific session."""
    return get_session_dir(session) / "web_server.pid"


def get_web_server_port_path(session: str) -> Path:
    """Get web server port file path for a specific session."""
    return get_session_dir(session) / "web_server.port"


def ensure_session_dir(session: str) -> Path:
    """Ensure session directory exists and return it."""
    session_dir = get_session_dir(session)
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


# =============================================================================
# Convenience Functions
# =============================================================================

def get_default_standing_instructions() -> str:
    """Get default standing instructions from config."""
    return get_user_config().default_standing_instructions


def get_default_tmux_session() -> str:
    """Get default tmux session name from config."""
    return get_user_config().tmux_session


# =============================================================================
# TUI Preferences (persisted between launches)
# =============================================================================

def get_tui_preferences_path(session: str) -> Path:
    """Get TUI preferences file path for a specific session."""
    return get_session_dir(session) / "tui_preferences.json"


@dataclass
class TUIPreferences:
    """TUI preferences that persist between launches."""

    summary_detail: str = "low"  # low, med, full
    detail_lines: int = 5  # 5, 10, 20, 50
    timeline_visible: bool = True
    daemon_panel_visible: bool = False
    view_mode: str = "tree"  # tree, list_preview
    tmux_sync: bool = False  # sync navigation to external tmux pane
    # Session IDs of stalled agents that have been visited by the user
    visited_stalled_agents: Set[str] = field(default_factory=set)

    @classmethod
    def load(cls, session: str) -> "TUIPreferences":
        """Load TUI preferences from file."""
        import json
        prefs_path = get_tui_preferences_path(session)

        if not prefs_path.exists():
            return cls()

        try:
            with open(prefs_path) as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    return cls()

                return cls(
                    summary_detail=data.get("summary_detail", "low"),
                    detail_lines=data.get("detail_lines", 5),
                    timeline_visible=data.get("timeline_visible", True),
                    daemon_panel_visible=data.get("daemon_panel_visible", False),
                    view_mode=data.get("view_mode", "tree"),
                    tmux_sync=data.get("tmux_sync", False),
                    visited_stalled_agents=set(data.get("visited_stalled_agents", [])),
                )
        except (json.JSONDecodeError, IOError):
            return cls()

    def save(self, session: str) -> None:
        """Save TUI preferences to file."""
        import json
        prefs_path = get_tui_preferences_path(session)

        try:
            prefs_path.parent.mkdir(parents=True, exist_ok=True)
            with open(prefs_path, 'w') as f:
                json.dump({
                    "summary_detail": self.summary_detail,
                    "detail_lines": self.detail_lines,
                    "timeline_visible": self.timeline_visible,
                    "daemon_panel_visible": self.daemon_panel_visible,
                    "view_mode": self.view_mode,
                    "tmux_sync": self.tmux_sync,
                    "visited_stalled_agents": list(self.visited_stalled_agents),
                }, f, indent=2)
        except (IOError, OSError):
            pass  # Best effort
