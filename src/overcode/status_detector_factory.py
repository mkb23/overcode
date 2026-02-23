"""
Factory for creating status detector instances.

Provides both a single-strategy factory (create_status_detector) and a
dispatcher (StatusDetectorDispatcher) that holds both detector types and
auto-dispatches per-session based on session.hook_status_detection (#5).
"""

from typing import Optional, Tuple, TYPE_CHECKING

from .protocols import StatusDetectorProtocol

if TYPE_CHECKING:
    from .protocols import TmuxInterface
    from .session_manager import Session
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


class StatusDetectorDispatcher:
    """Holds both detector types and dispatches per-session.

    Implements StatusDetectorProtocol so it can be used anywhere a single
    detector is expected. The detect_status() method checks
    session.hook_status_detection to pick the right strategy.
    """

    def __init__(
        self,
        tmux_session: str,
        tmux: Optional["TmuxInterface"] = None,
        patterns: Optional["StatusPatterns"] = None,
        polling_detector: Optional[StatusDetectorProtocol] = None,
        hook_detector: Optional[StatusDetectorProtocol] = None,
    ):
        self.tmux_session = tmux_session
        from .status_detector import PollingStatusDetector
        from .hook_status_detector import HookStatusDetector
        self.polling = polling_detector or PollingStatusDetector(tmux_session, tmux=tmux, patterns=patterns)
        self.hooks = hook_detector or HookStatusDetector(tmux_session, tmux=tmux, patterns=patterns)

    @property
    def capture_lines(self) -> int:
        return self.polling.capture_lines

    @capture_lines.setter
    def capture_lines(self, value: int) -> None:
        self.polling.capture_lines = value
        self.hooks.capture_lines = value

    def detect_status(self, session: "Session") -> Tuple[str, str, str]:
        """Detect status using the appropriate detector for the session."""
        detector = self.hooks if session.hook_status_detection else self.polling
        return detector.detect_status(session)

    def get_pane_content(self, window: int, num_lines: int = 0) -> Optional[str]:
        """Get pane content (delegates to polling detector)."""
        return self.polling.get_pane_content(window, num_lines)
