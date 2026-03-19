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

import dataclasses
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
    interval_fast: int = 2       # When active or agents working
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
class ModelPricing:
    """Per-million-token pricing for a model."""
    input: float
    output: float
    cache_write: float
    cache_read: float


# Built-in pricing for known Claude model families.
# Keys are checked as prefixes against model names, so "opus" matches
# "opus", "claude-opus-4-6", etc.  Order matters: longer prefixes first.
MODEL_PRICING: dict[str, ModelPricing] = {
    "opus":   ModelPricing(input=15.0, output=75.0, cache_write=18.75, cache_read=1.50),
    "sonnet": ModelPricing(input=3.0,  output=15.0, cache_write=3.75,  cache_read=0.30),
    "haiku":  ModelPricing(input=0.80, output=4.0,  cache_write=1.0,   cache_read=0.08),
}


def get_model_pricing(model: str | None, fallback: "UserConfig") -> ModelPricing:
    """Look up pricing for a model name, falling back to global config.

    Matches against MODEL_PRICING keys as substrings of the model name
    (e.g. "claude-sonnet-4-6" matches "sonnet").  User overrides in
    config.yaml under ``model_pricing:`` take precedence over built-ins.
    """
    if model:
        # Check user overrides first, then built-ins
        all_pricing = {**MODEL_PRICING, **fallback.model_pricing}
        model_lower = model.lower()
        for key, pricing in all_pricing.items():
            if key in model_lower:
                return pricing
    return ModelPricing(
        input=fallback.price_input,
        output=fallback.price_output,
        cache_write=fallback.price_cache_write,
        cache_read=fallback.price_cache_read,
    )


@dataclass
class UserConfig:
    """User-configurable settings from config.yaml."""

    default_standing_instructions: str = ""
    tmux_session: str = "agents"

    # Token pricing (per million tokens) - defaults to Sonnet
    price_input: float = 3.0        # $/MTok for input tokens
    price_output: float = 15.0      # $/MTok for output tokens
    price_cache_write: float = 3.75  # $/MTok for cache creation
    price_cache_read: float = 0.30   # $/MTok for cache reads

    # Per-model pricing overrides (loaded from config.yaml model_pricing section)
    model_pricing: dict = field(default_factory=dict)

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

                # Load pricing config (nested under 'pricing' key)
                pricing = data.get("pricing", {})

                # Parse per-model pricing overrides
                model_pricing_raw = data.get("model_pricing", {})
                model_pricing_parsed: dict[str, ModelPricing] = {}
                if isinstance(model_pricing_raw, dict):
                    for key, vals in model_pricing_raw.items():
                        if isinstance(vals, dict):
                            model_pricing_parsed[key] = ModelPricing(
                                input=vals.get("input", 3.0),
                                output=vals.get("output", 15.0),
                                cache_write=vals.get("cache_write", 3.75),
                                cache_read=vals.get("cache_read", 0.30),
                            )

                return cls(
                    default_standing_instructions=data.get(
                        "default_standing_instructions", ""
                    ),
                    tmux_session=data.get("tmux_session", "agents"),
                    price_input=pricing.get("input", 3.0),
                    price_output=pricing.get("output", 15.0),
                    price_cache_write=pricing.get("cache_write", 3.75),
                    price_cache_read=pricing.get("cache_read", 0.30),
                    model_pricing=model_pricing_parsed,
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


def get_diagnostics_dir(session: str) -> Path:
    """Get the diagnostics directory for a specific session."""
    return get_session_dir(session) / "diagnostics"


def get_event_loop_timing_path(session: str) -> Path:
    """Get the event loop timing CSV path for a specific session."""
    return get_diagnostics_dir(session) / "event_loop_timing.csv"


def get_status_changes_path(session: str) -> Path:
    """Get the status changes diagnostic CSV path for a specific session."""
    return get_diagnostics_dir(session) / "status_changes.csv"


def ensure_session_dir(session: str) -> Path:
    """Ensure session directory exists and return it."""
    session_dir = get_session_dir(session)
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


# =============================================================================
# Convenience Functions
# =============================================================================

def get_default_standing_instructions() -> str:
    """Get default standing instructions from config.

    Delegates to config.get_default_standing_instructions() — kept here
    for backwards compatibility with existing imports.
    """
    from .config import get_default_standing_instructions as _get
    return _get()


def get_default_tmux_session() -> str:
    """Get default tmux session name from config."""
    return get_user_config().tmux_session


# =============================================================================
# TUI Preferences (persisted between launches)
# =============================================================================

def get_tui_heartbeat_path(session: str) -> Path:
    """Get TUI heartbeat file path for a specific session."""
    return get_session_dir(session) / "tui_heartbeat"


def write_tui_heartbeat(session: str) -> None:
    """Write current ISO timestamp to TUI heartbeat file.

    Called by TUI on keypress (throttled) so the monitor daemon
    can distinguish TUI-active from computer-active presence.
    """
    from datetime import datetime
    heartbeat_path = get_tui_heartbeat_path(session)
    try:
        heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        heartbeat_path.write_text(datetime.now().isoformat())
    except OSError:
        pass  # Best effort


def get_tui_preferences_path(session: str) -> Path:
    """Get TUI preferences file path for a specific session."""
    return get_session_dir(session) / "tui_preferences.json"


@dataclass
class TUIPreferences:
    """TUI preferences that persist between launches."""

    # Fields excluded from persistence — always use dataclass default (#319)
    _SKIP_PERSIST = frozenset({"show_done"})
    # Load defaults that differ from dataclass defaults (migration compat)
    _LOAD_DEFAULTS = {"baseline_minutes": 0}

    summary_detail: str = "full"  # low, med, full
    timeline_visible: bool = True
    daemon_panel_visible: bool = False
    preview_visible: bool = False  # preview pane visibility
    tmux_sync: bool = False  # sync navigation to external tmux pane
    show_terminated: bool = False  # keep killed sessions visible in timeline
    hide_asleep: bool = False  # hide sleeping agents from display
    show_done: bool = False  # show "done" child agents (#244)
    sort_mode: str = "alphabetical"  # alphabetical, by_status, by_value (#61)
    summary_content_mode: str = "ai_short"  # ai_short, ai_long, orders, annotation, heartbeat (#98, #171)
    baseline_minutes: int = 60  # 0=now (instantaneous), 15/30/.../180 = minutes back for mean spin
    monochrome: bool = False  # B&W mode for terminals with ANSI issues (#138)
    emoji_free: bool = False  # ASCII fallbacks for terminals without emoji (#315)
    show_cost: str = "tokens"  # "tokens", "cost", "joules" — cycle with $
    timeline_hours: float = 3.0  # 1, 3, 6, 12, 24 — timeline scope (#191)
    notifications: str = "off"  # "off", "sound", "banner", "both" — macOS notifications (#235)
    # Session IDs of stalled agents that have been visited by the user
    visited_stalled_agents: Set[str] = field(default_factory=set)
    # Per-level column overrides: {"low": {"uptime": true, ...}, "med": {...}, "high": {...}}
    # Only stores explicit user overrides. Missing = use default from detail_levels.
    column_config: dict = field(default_factory=dict)
    # Show abbreviated column headers above summary lines
    show_column_headers: bool = False
    # Sister instances hidden from agent list (#323)
    disabled_sisters: Set[str] = field(default_factory=set)
    # Log every status change to diagnostics CSV (off by default)
    status_change_logging: bool = False

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

                kwargs = {}
                for fld in dataclasses.fields(cls):
                    if fld.name in cls._SKIP_PERSIST:
                        continue
                    # Use migration default if specified, else dataclass default
                    if fld.name in cls._LOAD_DEFAULTS:
                        default = cls._LOAD_DEFAULTS[fld.name]
                    elif fld.default is not dataclasses.MISSING:
                        default = fld.default
                    else:
                        default = fld.default_factory()
                    value = data.get(fld.name, default)
                    # Convert JSON lists back to sets
                    if fld.default_factory is set:
                        value = set(value) if value else set()
                    kwargs[fld.name] = value

                # Migration: map "custom" detail level to "full"
                if kwargs.get("summary_detail") == "custom":
                    kwargs["summary_detail"] = "full"

                # Migration: show_cost bool → str ("tokens"/"cost"/"joules")
                sc = kwargs.get("show_cost")
                if sc is True:
                    kwargs["show_cost"] = "cost"
                elif sc is False:
                    kwargs["show_cost"] = "tokens"

                return cls(**kwargs)
        except (json.JSONDecodeError, IOError):
            return cls()

    def save(self, session: str) -> None:
        """Save TUI preferences to file."""
        import json
        prefs_path = get_tui_preferences_path(session)

        try:
            prefs_path.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            for fld in dataclasses.fields(self):
                if fld.name in self._SKIP_PERSIST:
                    continue
                value = getattr(self, fld.name)
                if isinstance(value, set):
                    value = sorted(value)
                data[fld.name] = value
            with open(prefs_path, 'w') as f:
                json.dump(data, f, indent=2)
        except (IOError, OSError):
            pass  # Best effort
