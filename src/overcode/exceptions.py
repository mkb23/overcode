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


class StateWriteError(StateError):
    """Error writing state to file."""

    pass


# =============================================================================
# Tmux Errors
# =============================================================================


class TmuxError(OvercodeError):
    """Error related to tmux operations."""

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


class AgentBusyError(SessionError):
    """The agent is mid-turn, so an operation that restarts it was refused (#478)."""

    def __init__(self, name: str, status: str):
        self.name = name
        self.status = status
        super().__init__(f"Agent '{name}' is busy ({status})")


# =============================================================================
# Agent CLI Errors
# =============================================================================


class AgentCliError(OvercodeError):
    """Error related to an agent CLI (Claude Code, opencode, …)."""

    pass


class AgentCliNotFoundError(AgentCliError):
    """The agent CLI binary is not installed or not found."""

    pass


class AgentCliStartupError(AgentCliError):
    """Error starting the agent CLI process."""

    pass


# Pre-backend names, kept so existing ``except ClaudeNotFoundError`` clauses
# (and third-party callers) keep catching the same exceptions. Aliases rather
# than subclasses so isinstance relationships are unchanged in both directions.
ClaudeError = AgentCliError
ClaudeNotFoundError = AgentCliNotFoundError
ClaudeStartupError = AgentCliStartupError
