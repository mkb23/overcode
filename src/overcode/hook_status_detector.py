"""
Hook-based status detector for Claude sessions (#5).

Reads hook state files written by Claude Code hooks (UserPromptSubmit, Stop,
PermissionRequest, PostToolUse, SessionEnd) to determine agent status without
tmux pane scraping.

Design:
- Hook state is authoritative for STATUS when fresh (< stale_threshold_seconds).
- Pane content is read for enrichment (activity description) but never for status.
- Falls back to PollingStatusDetector when hook state is missing or stale.
- One source of truth at any given moment: either hooks or polling, never both.
"""

import json
import os
import time
from pathlib import Path
from typing import Optional, Tuple, TYPE_CHECKING

from .status_constants import (
    STATUS_RUNNING,
    STATUS_WAITING_USER,
    STATUS_WAITING_OVERSIGHT,
    STATUS_TERMINATED,
)

if TYPE_CHECKING:
    from .interfaces import TmuxInterface
    from .status_patterns import StatusPatterns
    from .session_manager import Session


# Hook event → status mapping
_HOOK_STATUS_MAP = {
    "UserPromptSubmit": STATUS_RUNNING,
    "Stop": STATUS_WAITING_USER,
    "PermissionRequest": STATUS_WAITING_USER,
    "PostToolUse": STATUS_RUNNING,
    "SessionEnd": STATUS_TERMINATED,
}

DEFAULT_STALE_THRESHOLD = 120  # seconds


class HookStatusDetector:
    """Detects session status from hook state files.

    Hook state files are JSON files written by Claude Code hooks at:
        ~/.overcode/sessions/{tmux_session}/hook_state_{session_name}.json

    Format:
        {
            "event": "UserPromptSubmit",
            "timestamp": 1234567890.123,
            "tool_name": "Read"  // optional, for PostToolUse
        }

    When hook state is missing or stale (>120s), falls back to polling.
    """

    # Re-export status constants for backward compat (same interface as PollingStatusDetector)
    STATUS_RUNNING = STATUS_RUNNING
    STATUS_WAITING_USER = STATUS_WAITING_USER
    STATUS_TERMINATED = STATUS_TERMINATED

    def __init__(
        self,
        tmux_session: str,
        tmux: "TmuxInterface" = None,
        patterns: "StatusPatterns" = None,
        state_dir: Optional[Path] = None,
        stale_threshold_seconds: float = DEFAULT_STALE_THRESHOLD,
    ):
        self.tmux_session = tmux_session
        self._tmux = tmux
        self._patterns = patterns
        self._stale_threshold = stale_threshold_seconds

        # Resolve state directory
        if state_dir is not None:
            self._state_dir = state_dir
        else:
            env_dir = os.environ.get("OVERCODE_STATE_DIR")
            if env_dir:
                self._state_dir = Path(env_dir) / "sessions" / tmux_session
            else:
                self._state_dir = Path.home() / ".overcode" / "sessions" / tmux_session

        # Lazy-created polling fallback (avoids import cycle at module level)
        self._polling_fallback = None

    def _get_polling_fallback(self):
        """Lazily create a PollingStatusDetector for fallback."""
        if self._polling_fallback is None:
            from .status_detector import PollingStatusDetector
            self._polling_fallback = PollingStatusDetector(
                self.tmux_session, tmux=self._tmux, patterns=self._patterns
            )
        return self._polling_fallback

    def _hook_state_path(self, session_name: str) -> Path:
        """Get the hook state file path for a session."""
        return self._state_dir / f"hook_state_{session_name}.json"

    def _read_hook_state(self, session_name: str) -> Optional[dict]:
        """Read and parse hook state file.

        Returns:
            Parsed dict with 'event', 'timestamp', optional 'tool_name',
            or None if file is missing, corrupt, or stale.
        """
        path = self._hook_state_path(session_name)
        try:
            with open(path) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, IOError):
            return None

        # Validate required fields
        if not isinstance(data, dict):
            return None
        if "event" not in data or "timestamp" not in data:
            return None

        # Check staleness
        try:
            ts = float(data["timestamp"])
        except (TypeError, ValueError):
            return None

        age = time.time() - ts
        if age > self._stale_threshold:
            return None

        return data

    def get_pane_content(self, window: int, num_lines: int = 50) -> Optional[str]:
        """Get pane content via the polling detector's tmux interface."""
        return self._get_polling_fallback().get_pane_content(window, num_lines)

    def detect_status(self, session: "Session") -> Tuple[str, str, str]:
        """Detect session status using hook state files.

        When fresh hook state exists, uses that for status determination.
        Still reads pane content for activity enrichment.
        Falls back to full polling when hook state is missing or stale.

        Returns:
            Tuple of (status, current_activity, pane_content)
        """
        hook_state = self._read_hook_state(session.name)

        if hook_state is None:
            # No hook state or stale → full polling fallback
            return self._get_polling_fallback().detect_status(session)

        # Hook state is fresh → use it for status
        event = hook_state.get("event", "")
        status = _HOOK_STATUS_MAP.get(event, STATUS_WAITING_USER)

        # For child agents, Stop → waiting_oversight instead of waiting_user
        if event == "Stop" and session.parent_session_id is not None:
            status = STATUS_WAITING_OVERSIGHT

        # Read pane for activity enrichment and content return value
        pane_content = self.get_pane_content(session.tmux_window) or ""

        # Build activity description
        activity = self._build_activity(event, hook_state, pane_content, session)

        return status, activity, pane_content

    def _build_activity(self, event: str, hook_state: dict, pane_content: str, session: "Session" = None) -> str:
        """Build an activity description from hook event and pane content."""
        if event == "PostToolUse":
            tool_name = hook_state.get("tool_name", "")
            if tool_name:
                return f"Using {tool_name}"
            return "Running tool"

        if event == "UserPromptSubmit":
            return "Processing prompt"

        if event == "Stop":
            if session and session.parent_session_id is not None:
                return "Waiting for oversight report"
            return "Waiting for user input"

        if event == "PermissionRequest":
            return "Permission: approval required"

        if event == "SessionEnd":
            return "Claude exited"

        return "Unknown state"
