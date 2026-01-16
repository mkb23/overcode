"""
Custom exception hierarchy for Overcode.

Provides domain-specific exceptions for better error handling and debugging.
All exceptions inherit from OvercodeError for easy catching of any
overcode-related error.
"""


class OvercodeError(Exception):
    """Base exception for all Overcode errors."""

    pass


# =============================================================================
# State Management Errors
# =============================================================================


class StateError(OvercodeError):
    """Error related to state file operations."""

    pass


class StateReadError(StateError):
    """Error reading state from file."""

    pass


class StateWriteError(StateError):
    """Error writing state to file."""

    pass


class StateCorruptedError(StateError):
    """State file is corrupted or invalid."""

    pass


# =============================================================================
# Tmux Errors
# =============================================================================


class TmuxError(OvercodeError):
    """Error related to tmux operations."""

    pass


class TmuxSessionError(TmuxError):
    """Error with tmux session operations."""

    pass


class TmuxWindowError(TmuxError):
    """Error with tmux window operations."""

    pass


class TmuxPaneError(TmuxError):
    """Error with tmux pane operations."""

    pass


class TmuxNotFoundError(TmuxError):
    """Tmux is not installed or not found."""

    pass


# =============================================================================
# Session/Agent Errors
# =============================================================================


class SessionError(OvercodeError):
    """Error related to agent session operations."""

    pass


class InvalidSessionNameError(SessionError):
    """Session name is invalid."""

    # Valid session name pattern: alphanumeric, underscore, hyphen, 1-64 chars
    VALID_PATTERN = r"^[a-zA-Z0-9_-]{1,64}$"

    def __init__(self, name: str, reason: str = None):
        self.name = name
        if reason:
            msg = f"Invalid session name '{name}': {reason}"
        else:
            msg = f"Invalid session name '{name}'. Use only letters, numbers, underscore, hyphen (1-64 chars)"
        super().__init__(msg)


class SessionNotFoundError(SessionError):
    """Agent session was not found."""

    def __init__(self, name: str):
        self.name = name
        super().__init__(f"Session '{name}' not found")


class SessionAlreadyExistsError(SessionError):
    """Agent session already exists."""

    def __init__(self, name: str):
        self.name = name
        super().__init__(f"Session '{name}' already exists")


class SessionLaunchError(SessionError):
    """Error launching an agent session."""

    pass


class SessionKillError(SessionError):
    """Error killing an agent session."""

    pass


# =============================================================================
# Claude Errors
# =============================================================================


class ClaudeError(OvercodeError):
    """Error related to Claude Code operations."""

    pass


class ClaudeNotFoundError(ClaudeError):
    """Claude Code is not installed or not found."""

    pass


class ClaudeStartupError(ClaudeError):
    """Error starting Claude Code process."""

    pass


# =============================================================================
# Configuration Errors
# =============================================================================


class ConfigError(OvercodeError):
    """Error related to configuration."""

    pass


class ConfigReadError(ConfigError):
    """Error reading configuration file."""

    pass


class ConfigValidationError(ConfigError):
    """Configuration validation failed."""

    pass


# =============================================================================
# Daemon Errors
# =============================================================================


class DaemonError(OvercodeError):
    """Error related to daemon operations."""

    pass


class DaemonAlreadyRunningError(DaemonError):
    """Daemon is already running."""

    def __init__(self, pid: int):
        self.pid = pid
        super().__init__(f"Daemon already running (PID {pid})")


class DaemonNotRunningError(DaemonError):
    """Daemon is not running."""

    pass


# =============================================================================
# Presence Logger Errors
# =============================================================================


class PresenceError(OvercodeError):
    """Error related to presence logging."""

    pass


class PresenceApiUnavailableError(PresenceError):
    """macOS presence APIs are not available."""

    pass
