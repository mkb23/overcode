"""Is anyone watching the fleet? The monitor daemon's presence and attendance.

``PresenceComponent`` samples the user's presence (richer on macOS with
Quartz) for the published state; ``attendance`` decides whether the daemon
runs its attended cadences or the unattended ones.
"""

import time
from datetime import datetime
from typing import Optional

from .clock import to_epoch
from .settings import (
    get_tui_heartbeat_path,
    tui_attended_age_seconds,
    TUI_ATTENDED_TOUCH_SECONDS,
)

# Check for macOS presence APIs (optional)
try:
    from .presence_logger import (
        MACOS_APIS_AVAILABLE,
        get_current_presence_state,
        PresenceLogger,
        PresenceLoggerConfig,
    )
except ImportError:
    MACOS_APIS_AVAILABLE = False
    get_current_presence_state = None
    PresenceLogger = None
    PresenceLoggerConfig = None

# The API server touches the attended file when it serves a sister's status
# request (TUIs report on engine.sock instead); three missed touches and the
# sister is not watching.
TUI_ATTENDED_FRESHNESS = 3 * TUI_ATTENDED_TOUCH_SECONDS

class PresenceComponent:
    """Presence tracking (works on all platforms; richer on macOS with Quartz)."""

    # TUI heartbeat is considered fresh if within this many seconds
    TUI_HEARTBEAT_FRESHNESS = 60

    def __init__(self, tmux_session: str = "agents"):
        self.available = True
        self._logger: Optional[PresenceLogger] = None
        self._tmux_session = tmux_session
        self._last_publish_time: Optional[datetime] = None

        if PresenceLogger is not None:
            heartbeat_path = str(get_tui_heartbeat_path(tmux_session))
            config = PresenceLoggerConfig(
                tui_heartbeat_path=heartbeat_path,
                tui_heartbeat_freshness=self.TUI_HEARTBEAT_FRESHNESS,
            )
            self._logger = PresenceLogger(config)
            self._logger.start()

    def _is_tui_active(self) -> bool:
        """Check if TUI heartbeat file has a recent timestamp."""
        try:
            heartbeat_path = get_tui_heartbeat_path(self._tmux_session)
            if not heartbeat_path.exists():
                return False
            written_at = to_epoch(heartbeat_path.read_text().strip())
            if written_at is None:
                return False
            # Epoch seconds: the stamp may be naive (local) or aware
            return time.time() - written_at <= self.TUI_HEARTBEAT_FRESHNESS
        except (ValueError, OSError):
            return False

    def _detect_sleep(self) -> bool:
        """Detect if the machine likely slept since last publish.

        Returns True if the gap since last publish exceeds 2x the daemon interval.
        """
        now = datetime.now()
        if self._last_publish_time is None:
            self._last_publish_time = now
            return False
        gap = (now - self._last_publish_time).total_seconds()
        slept = gap > 20  # Machine sleep produces gaps of 30s+; absolute threshold avoids false positives
        self._last_publish_time = now
        return slept

    def get_current_state(self) -> tuple:
        """Get current presence state.

        Returns:
            Tuple of (state, idle_seconds, locked) or (None, None, None) if unavailable.
            Without Quartz, idle is None (unmeasured) and locked is always
            False, but sleep detection and TUI heartbeat still produce
            meaningful state values.
        """
        try:
            from .presence_logger import classify_state, DEFAULT_IDLE_THRESHOLD

            tui_active = self._is_tui_active()
            slept = self._detect_sleep()

            if slept:
                # Machine just woke — override to asleep state for this sample
                state = classify_state(
                    locked=False,
                    idle_seconds=0.0,
                    slept=True,
                    idle_threshold=DEFAULT_IDLE_THRESHOLD,
                    tui_active=False,
                )
                return state, 0.0, False

            if MACOS_APIS_AVAILABLE and get_current_presence_state is not None:
                return get_current_presence_state(tui_active=tui_active)

            # Non-macOS: locked=False; TUI heartbeat still works. Idle time
            # is unknown (None), not 0: a 0 would publish "idle since now",
            # a value that moves every tick and makes every quiet tick a delta
            state = classify_state(
                locked=False,
                idle_seconds=0.0,
                slept=False,
                idle_threshold=DEFAULT_IDLE_THRESHOLD,
                tui_active=tui_active,
            )
            return state, None, False
        except Exception:
            return None, None, None

    def stop(self):
        """Stop the presence logger if running."""
        if self._logger is not None:
            self._logger.stop()

class NullPresence:
    """Presence that knows nothing: no logger thread, no host idle time.

    ``MonitorDaemon.for_test`` uses it, so no test tick depends on whether
    someone is at the keyboard.
    """

    available = False

    def _is_tui_active(self) -> bool:
        return False

    def get_current_state(self) -> tuple:
        return None, None, None

    def stop(self) -> None:
        pass

def attendance(*, engine, session_attached: Optional[int], presence, tmux_session: str) -> str:
    """``"attended"`` or ``"unattended"``: is anyone watching this fleet?

    Attended when a view on engine.sock says it is visible (the
    split's TUI does, from its tmux attached-client check). Otherwise
    unattended only when the fallbacks agree nobody is: this tick's
    pane listing counted no client attached to the agents tmux session
    (``session_attached``; an unknown count — listing failed — counts
    as attended), the TUI keypress heartbeat is not fresh
    (PresenceComponent's 60 s window), and the attended file is older
    than TUI_ATTENDED_FRESHNESS: the API server touches it when it
    serves a status request (a sister TUI's poll), so the daemon never
    slows while a sister is watching this fleet.
    """
    if engine is not None and engine.attended:
        return "attended"  # a view on engine.sock says it is being looked at
    attached = session_attached
    if attached is None or attached > 0:
        return "attended"
    if presence._is_tui_active():
        return "attended"
    age = tui_attended_age_seconds(tmux_session)
    if age is not None and age <= TUI_ATTENDED_FRESHNESS:
        return "attended"
    return "unattended"
