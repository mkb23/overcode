"""
Factory for creating status detector instances.

Provides a dispatcher (StatusDetectorDispatcher) that resolves the
hooks-vs-polling choice per session: an explicit per-agent override wins,
then the session backend's capability, then the fleet default (the mode the
dispatcher was constructed with, which callers read from
``settings.resolve_detection_mode``).

Detector instances are held per backend so each session is scraped with its
own backend's StatusPatterns.
"""

from typing import Dict, Optional, Tuple, TYPE_CHECKING

from .protocols import StatusDetectorProtocol

if TYPE_CHECKING:
    from .protocols import TmuxInterface
    from .session_manager import Session
    from .status_patterns import StatusPatterns

VALID_MODES = ("hooks", "polling")


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


def resolve_session_detection_mode(session: "Session", fleet_mode: str = "polling") -> str:
    """Resolve the detection mode for one session.

    Priority:
      1. Explicit per-agent override (``Session.detection_mode_override``).
      2. Legacy per-agent opt-out (``Session.hook_status_detection`` False —
         the switch the web API's hook-detection endpoint flips).
      3. Backend capability — a backend without HOOK_EVENTS can never be
         watched via hook-state files.
      4. The fleet default, which callers derive from
         ``settings.resolve_detection_mode`` (explicit global mode file →
         recent hook-state activity → user-level Claude config).
    """
    override = getattr(session, "detection_mode_override", None)
    if override in VALID_MODES:
        return override

    if getattr(session, "hook_status_detection", True) is False:
        return "polling"

    from .backends import BackendCapability, session_supports
    if not session_supports(session, BackendCapability.HOOK_EVENTS):
        return "polling"

    return fleet_mode if fleet_mode in VALID_MODES else "polling"


class StatusDetectorDispatcher:
    """Holds detector pairs per backend and dispatches per session.

    ``mode`` is the fleet default — the mode a session uses when it has no
    per-agent override. Each session's status is detected with its own
    backend's detector pair, so a mixed fleet resolves correctly within a
    single daemon tick.
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
        self._tmux = tmux
        from .backends import DEFAULT_BACKEND
        from .status_detector import PollingStatusDetector
        from .hook_status_detector import HookStatusDetector
        self.polling = polling_detector or PollingStatusDetector(tmux_session, tmux=tmux, patterns=patterns)
        self.hooks = hook_detector or HookStatusDetector(tmux_session, tmux=tmux, patterns=patterns)
        # Detector pairs keyed by backend name. Injected/explicitly-patterned
        # detectors own the default backend slot.
        self._pairs: Dict[str, Tuple[StatusDetectorProtocol, StatusDetectorProtocol]] = {
            DEFAULT_BACKEND: (self.polling, self.hooks),
        }
        self._mode = mode
        # Cache last (status, activity) so get_status_detail can synthesize in polling mode (#TBD).
        self._last_seen: dict = {}

    @property
    def mode(self) -> str:
        """Fleet default detection mode: 'hooks' or 'polling'."""
        return self._mode

    @mode.setter
    def mode(self, value: str) -> None:
        if value not in VALID_MODES:
            raise ValueError(f"Invalid detection mode: {value!r}")
        self._mode = value

    @property
    def capture_lines(self) -> int:
        return self.polling.capture_lines

    @capture_lines.setter
    def capture_lines(self, value: int) -> None:
        for polling, hooks in self._pairs.values():
            polling.capture_lines = value
            hooks.capture_lines = value

    def _pair_for(self, backend_name: str) -> Tuple[StatusDetectorProtocol, StatusDetectorProtocol]:
        """Detector pair for a backend, created on first sight."""
        pair = self._pairs.get(backend_name)
        if pair is not None:
            return pair

        from .status_patterns import get_patterns
        from .status_detector import PollingStatusDetector
        from .hook_status_detector import HookStatusDetector

        patterns = get_patterns(backend_name)
        polling = PollingStatusDetector(self.tmux_session, tmux=self._tmux, patterns=patterns)
        hooks = HookStatusDetector(self.tmux_session, tmux=self._tmux, patterns=patterns)
        polling.capture_lines = self.capture_lines
        hooks.capture_lines = self.capture_lines
        pair = (polling, hooks)
        self._pairs[backend_name] = pair
        return pair

    def resolve_mode(self, session: "Session") -> str:
        """Detection mode this session will be watched with."""
        return resolve_session_detection_mode(session, self._mode)

    def detect_status(self, session: "Session", num_lines: int = 0) -> Tuple[str, str, str]:
        """Detect status using the mode and patterns resolved for this session."""
        from .backends import session_backend_name
        polling, hooks = self._pair_for(session_backend_name(session))
        detector = hooks if self.resolve_mode(session) == "hooks" else polling
        result = detector.detect_status(session, num_lines=num_lines)
        # Cache for get_status_detail synthesis (#TBD).
        status, activity, _ = result
        self._last_seen[session.name] = (status, activity)
        return result

    def get_pane_content(self, window: str, num_lines: int = 0) -> Optional[str]:
        """Get pane content (delegates to the fleet-default detector).

        Pane capture is backend-independent, so the default pair serves it.
        """
        if self._mode == "hooks":
            return self.hooks.get_pane_content(window, num_lines)
        return self.polling.get_pane_content(window, num_lines)

    def get_loaded_skills(self, session_name: str) -> list[str]:
        """Return skills observed via hook events (#252)."""
        for _polling, hooks in self._pairs.values():
            getter = getattr(hooks, 'get_loaded_skills', None)
            if getter is None:
                continue
            skills = getter(session_name)
            if skills:
                return skills
        return []

    def get_status_detail(self, session_name: str):
        """Return the structured 2-column status detail for a session (#TBD).

        Layered:
          1. If a hook detector has a fresh entry (hooks mode), use it
             — full obligation tracking + foreground classification.
          2. Otherwise (polling mode, or hooks installed but not yet
             fired) synthesize a minimal detail from the most recent
             legacy status enum so the column still shows a bucket color
             plus a generic badge.
          3. Falls back to None if neither is available — column hides.
        """
        for _polling, hooks in self._pairs.values():
            getter = getattr(hooks, 'get_status_detail', None)
            if getter is None:
                continue
            detail = getter(session_name)
            if detail is not None:
                return detail
        last = self._last_seen.get(session_name)
        if last is None:
            return None
        from .hook_status_detector import synthesize_status_detail_from_legacy
        status, activity = last
        return synthesize_status_detail_from_legacy(status, activity)
