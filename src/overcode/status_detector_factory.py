"""
Factory for creating status detector instances.

Provides a dispatcher (StatusDetectorDispatcher) that holds both detector
types and switches globally between hooks and polling mode. The mode is
set once at startup and toggled via the K hotkey — no per-agent dispatch.
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
    """Holds both detector types and dispatches based on global mode.

    The mode ("hooks" or "polling") is set globally — all agents use the
    same detection strategy. No per-agent dispatch or fallback between
    the two systems.
    """

    def __init__(
        self,
        tmux_session: str,
        tmux: Optional["TmuxInterface"] = None,
        patterns: Optional["StatusPatterns"] = None,
        polling_detector: Optional[StatusDetectorProtocol] = None,
        hook_detector: Optional[StatusDetectorProtocol] = None,
        mode: str = "polling",
    ):
        self.tmux_session = tmux_session
        from .status_detector import PollingStatusDetector
        from .hook_status_detector import HookStatusDetector
        self.polling = polling_detector or PollingStatusDetector(tmux_session, tmux=tmux, patterns=patterns)
        self.hooks = hook_detector or HookStatusDetector(tmux_session, tmux=tmux, patterns=patterns)
        self._mode = mode

    @property
    def mode(self) -> str:
        """Current detection mode: 'hooks' or 'polling'."""
        return self._mode

    @mode.setter
    def mode(self, value: str) -> None:
        if value not in ("hooks", "polling"):
            raise ValueError(f"Invalid detection mode: {value!r}")
        self._mode = value

    @property
    def capture_lines(self) -> int:
        return self.polling.capture_lines

    @capture_lines.setter
    def capture_lines(self, value: int) -> None:
        self.polling.capture_lines = value
        self.hooks.capture_lines = value

    def detect_status(self, session: "Session", num_lines: int = 0) -> Tuple[str, str, str]:
        """Detect status using the globally configured mode."""
        detector = self.hooks if self._mode == "hooks" else self.polling
        return detector.detect_status(session, num_lines=num_lines)

    def get_pane_content(self, window: str, num_lines: int = 0) -> Optional[str]:
        """Get pane content (delegates to active detector)."""
        if self._mode == "hooks":
            return self.hooks.get_pane_content(window, num_lines)
        return self.polling.get_pane_content(window, num_lines)

    def get_loaded_skills(self, session_name: str) -> list[str]:
        """Return skills observed via hook events (#252)."""
        if hasattr(self.hooks, 'get_loaded_skills'):
            return self.hooks.get_loaded_skills(session_name)
        return []
