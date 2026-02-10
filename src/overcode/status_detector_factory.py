"""
Factory for creating status detector instances.

Selects between PollingStatusDetector (default) and HookStatusDetector
based on a strategy string. The caller decides which strategy to use
based on session.hook_status_detection (#5).
"""

from typing import Optional, TYPE_CHECKING

from .protocols import StatusDetectorProtocol

if TYPE_CHECKING:
    from .protocols import TmuxInterface
    from .status_patterns import StatusPatterns


def create_status_detector(
    tmux_session: str,
    strategy: str = "polling",
    tmux: Optional["TmuxInterface"] = None,
    patterns: Optional["StatusPatterns"] = None,
) -> StatusDetectorProtocol:
    """Create a status detector for the given strategy.

    Args:
        tmux_session: Name of the tmux session to monitor
        strategy: "polling" or "hooks"
        tmux: Optional TmuxInterface for dependency injection
        patterns: Optional StatusPatterns for polling detector

    Returns:
        A StatusDetectorProtocol implementation
    """
    if strategy == "hooks":
        from .hook_status_detector import HookStatusDetector
        return HookStatusDetector(tmux_session, tmux=tmux, patterns=patterns)

    from .status_detector import PollingStatusDetector
    return PollingStatusDetector(tmux_session, tmux=tmux, patterns=patterns)
