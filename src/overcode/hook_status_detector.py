"""
Hook-based status detector for Claude sessions (#5).

Reads hook state files written by Claude Code hooks (UserPromptSubmit,
PreToolUse, PostToolUse, Stop, PermissionRequest, SessionEnd) to determine
agent status without tmux pane scraping.

Design:
- Hook state is the sole authority for status. No polling fallback.
- Running-state hooks (UserPromptSubmit, PreToolUse, PostToolUse) are
  trusted indefinitely — Claude will send Stop or SessionEnd when done.
- Pane content is read only for activity enrichment, never for status.
"""

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Dict, Optional, Tuple, TYPE_CHECKING

from .status_constants import (
    DEFAULT_CAPTURE_LINES,
    STATUS_CAPTURE_LINES,
    STATUS_RUNNING,
    STATUS_BUSY_SLEEPING,
    STATUS_WAITING_APPROVAL,
    STATUS_WAITING_USER,
    STATUS_WAITING_OVERSIGHT,
    STATUS_WATCHING,
    STATUS_TERMINATED,
    STATUS_ERROR,
)
from .status_patterns import (
    extract_active_monitor_count,
    is_sleep_command,
    extract_sleep_duration,
    strip_ansi,
    is_shell_prompt,
)
from .tui_helpers import format_duration

if TYPE_CHECKING:
    from .interfaces import TmuxInterface
    from .status_patterns import StatusPatterns
    from .session_manager import Session


# Escape-interrupt prompt that Claude Code prints when the user hits
# Escape to interrupt an in-flight turn (#431). When we see this text
# in the pane, the agent is effectively waiting for user input even
# though no Stop hook fires.
_INTERRUPT_PROMPT_MARKERS = (
    "Interrupted · What should Claude do instead?",
    "Interrupted by user",
)


def _pane_shows_interrupt_prompt(pane_content: str) -> bool:
    """Return True if the pane looks like Claude is showing the interrupt prompt (#431)."""
    if not pane_content:
        return False
    clean = strip_ansi(pane_content)
    # Only look at the tail — older interrupt prompts may linger in scrollback
    tail = "\n".join(clean.splitlines()[-40:])
    return any(marker in tail for marker in _INTERRUPT_PROMPT_MARKERS)


# Hook event → status mapping
_HOOK_STATUS_MAP = {
    "UserPromptSubmit": STATUS_RUNNING,
    "PreToolUse": STATUS_RUNNING,
    "PostToolUse": STATUS_RUNNING,
    "PostToolUseFailure": STATUS_RUNNING,  # Tool failed but agent is still working
    "Stop": STATUS_WAITING_USER,
    "StopFailure": STATUS_ERROR,  # API error ended the turn (purple indicator)
    "UserPromptSubmitRejected": STATUS_ERROR,  # Hook blocked prompt e.g. budget exceeded (#428)
    "PermissionRequest": STATUS_WAITING_APPROVAL,
    "SessionEnd": STATUS_TERMINATED,
}


class HookStatusDetector:
    """Detects session status from hook state files.

    Hook state files are JSON files written by Claude Code hooks at:
        ~/.overcode/sessions/{tmux_session}/hook_state_{session_name}.json

    Format:
        {
            "event": "UserPromptSubmit",
            "timestamp": 1234567890.123,
            "tool_name": "Read"  // optional, for PostToolUse/PreToolUse
        }

    No polling fallback. If no hook state file exists, the detector checks
    whether the tmux window is alive and returns a sensible default.
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
        # Legacy params kept for API compat — ignored
        stale_threshold_seconds: float = 0,
        polling_fallback=None,
    ):
        self.tmux_session = tmux_session
        self.capture_lines = DEFAULT_CAPTURE_LINES
        self._tmux = tmux
        self._patterns = patterns
        # Diagnostic phase tracking (same interface as PollingStatusDetector)
        self._last_detect_phase: Dict[str, str] = {}
        self._content_changed: Dict[str, bool] = {}
        # Skills observed via Skill tool_use events, keyed by session name (#252)
        self._loaded_skills: Dict[str, set] = {}

        # Resolve state directory — must match hook_handler._get_hook_state_path()
        if state_dir is not None:
            self._state_dir = state_dir
        else:
            env_dir = os.environ.get("OVERCODE_STATE_DIR")
            if env_dir:
                self._state_dir = Path(env_dir) / tmux_session
            else:
                self._state_dir = Path.home() / ".overcode" / "sessions" / tmux_session

    def _hook_state_path(self, session_name: str) -> Path:
        """Get the hook state file path for a session."""
        return self._state_dir / f"hook_state_{session_name}.json"

    def _read_hook_state(self, session_name: str) -> Optional[dict]:
        """Read and parse hook state file.

        Returns:
            Parsed dict with 'event', 'timestamp', optional 'tool_name',
            or None if file is missing or corrupt.
            No staleness check — running hooks are trusted indefinitely.
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

        # Validate timestamp is a number
        try:
            float(data["timestamp"])
        except (TypeError, ValueError):
            return None

        return data

    def get_pane_content(self, window: str, num_lines: int = 0) -> Optional[str]:
        """Get pane content via tmux capture-pane."""
        if self._tmux:
            return self._tmux.capture_pane(
                self.tmux_session, window,
                lines=num_lines or self.capture_lines
            )
        # Direct tmux subprocess fallback
        lines_arg = num_lines or self.capture_lines
        try:
            result = subprocess.run(
                ["tmux", "capture-pane", "-t", f"{self.tmux_session}:{window}",
                 "-p", "-S", f"-{lines_arg}"],
                capture_output=True, text=True, timeout=5,
            )
            return result.stdout if result.returncode == 0 else None
        except (subprocess.TimeoutExpired, OSError):
            return None

    def detect_status(self, session: "Session", num_lines: int = 0) -> Tuple[str, str, str]:
        """Detect session status using hook state files.

        No polling fallback. When no hook state exists, checks if the
        tmux window is alive and returns a sensible default.

        Returns:
            Tuple of (status, current_activity, pane_content)
        """
        hook_state = self._read_hook_state(session.name)

        if hook_state is None:
            # No hook state file — agent hasn't triggered a hook yet.
            # Check if the window exists to distinguish fresh-start from terminated.
            pane_content = self.get_pane_content(session.tmux_window, num_lines=num_lines)
            if pane_content is None:
                self._last_detect_phase[session.id] = "hook:no_state+no_window"
                return STATUS_TERMINATED, "Window no longer exists", ""
            # Window alive, no hooks yet — assume waiting for input
            self._last_detect_phase[session.id] = "hook:no_state"
            return STATUS_WAITING_USER, "Waiting for first hook event", pane_content

        # Track loaded skills from persisted hook state (#252)
        # hook_handler.py accumulates skills in the "loaded_skills" field,
        # so we always read the full list — no race with polling interval.
        persisted_skills = hook_state.get("loaded_skills", [])
        if persisted_skills:
            if session.name not in self._loaded_skills:
                self._loaded_skills[session.name] = set()
            self._loaded_skills[session.name].update(persisted_skills)

        # Hook state exists — use it for status
        event = hook_state.get("event", "")

        if event == "SessionEnd":
            self._last_detect_phase[session.id] = "hook:SessionEnd"
            return self._detect_session_end_status(session, num_lines)

        status = _HOOK_STATUS_MAP.get(event, STATUS_WAITING_USER)

        # For child agents, Stop → waiting_oversight instead of waiting_user
        if event == "Stop" and session.parent_session_id is not None:
            status = STATUS_WAITING_OVERSIGHT

        # Read pane for activity enrichment and content return value
        pane_content = self.get_pane_content(session.tmux_window, num_lines=num_lines) or ""

        # Check for busy-sleeping: agent is "running" but executing a sleep command (#289)
        sleep_dur = None
        if status == STATUS_RUNNING:
            sleep_dur = self._find_sleep_duration(hook_state)
            if sleep_dur is not None:
                status = STATUS_BUSY_SLEEPING

        # Claude Code does not fire a Stop/SessionEnd hook when the user
        # hits Escape to interrupt the turn, so status can stay stuck as
        # RUNNING indefinitely. Detect the interrupt prompt that Claude
        # Code prints ("Interrupted · What should Claude do instead?") in
        # the pane and downgrade to waiting_user in that case (#431).
        if status == STATUS_RUNNING and _pane_shows_interrupt_prompt(pane_content):
            status = STATUS_WAITING_USER
            self._last_detect_phase[session.id] = f"hook:{event}+interrupt"

        # Monitor tool leaves a persistent stream that can wake the agent
        # after Stop/SessionEnd has fired. When the pane still shows live
        # monitors, upgrade the "waiting" status to STATUS_WATCHING so the
        # TUI reflects that the agent is externally trigger-able (#441).
        monitor_count = extract_active_monitor_count(pane_content) if pane_content else 0
        if monitor_count > 0 and status in (STATUS_WAITING_USER, STATUS_WAITING_OVERSIGHT):
            status = STATUS_WATCHING
            self._last_detect_phase[session.id] = f"hook:{event}+monitors={monitor_count}"

        # Build activity description
        activity = self._build_activity(event, hook_state, pane_content, session)

        # Enrich activity for busy_sleeping with parsed duration (#289)
        if status == STATUS_BUSY_SLEEPING:
            activity = f"Sleeping {format_duration(sleep_dur)}" if sleep_dur else "Sleeping"

        # Enrich activity for watching with monitor count (#441)
        if status == STATUS_WATCHING:
            plural = "s" if monitor_count != 1 else ""
            activity = f"Watching {monitor_count} monitor{plural}"

        # Record hook phase for diagnostics
        self._last_detect_phase[session.id] = f"hook:{event}"

        return status, activity, pane_content

    def _detect_session_end_status(self, session: "Session", num_lines: int = 0) -> Tuple[str, str, str]:
        """Determine status after a SessionEnd hook event.

        SessionEnd fires both on actual exit AND on /clear. We distinguish
        by checking the last line of the pane:
        - Shell prompt (user@host path %) → actual exit → TERMINATED
        - Claude's prompt (› or >) → /clear was used → WAITING_USER
        """
        pane_content = self.get_pane_content(session.tmux_window, num_lines=num_lines) or ""
        clean = strip_ansi(pane_content)
        lines = [l.strip() for l in clean.strip().split('\n') if l.strip()]

        if not lines:
            return STATUS_TERMINATED, "Claude exited", pane_content

        last_line = lines[-1]

        if is_shell_prompt(last_line):
            return STATUS_TERMINATED, "Claude exited - shell prompt", pane_content

        # No shell prompt → likely /clear, agent is waiting for input
        return STATUS_WAITING_USER, "Waiting for user input", pane_content

    def _find_sleep_duration(self, hook_state: dict) -> int | None:
        """Find sleep duration from hook state's tool_input (#289).

        PreToolUse and PostToolUse include tool_input with the Bash command.
        Parse the command directly — no pane scraping needed.
        """
        tool_input = hook_state.get("tool_input")
        if isinstance(tool_input, dict):
            command = tool_input.get("command", "")
            dur = extract_sleep_duration(command)
            if dur is not None:
                return dur
        return None

    @staticmethod
    def _parse_bash_activity(hook_state: dict) -> str | None:
        """Parse a Bash tool_input command into a concise activity string.

        Returns a human-readable summary of what the Bash command does,
        or None if the command isn't parseable or isn't Bash.
        """
        if hook_state.get("tool_name") != "Bash":
            return None
        tool_input = hook_state.get("tool_input")
        if not isinstance(tool_input, dict):
            return None
        command = tool_input.get("command", "")
        if not command:
            return None
        # Truncate long commands
        if len(command) > 80:
            command = command[:77] + "..."
        return f"Bash: {command}"

    def _build_activity(self, event: str, hook_state: dict, pane_content: str, session: "Session" = None) -> str:
        """Build an activity description from hook event and pane content."""
        if event in ("PreToolUse", "PostToolUse"):
            # For Bash, show the actual command for better visibility
            bash_activity = self._parse_bash_activity(hook_state)
            if bash_activity:
                return bash_activity
            tool_name = hook_state.get("tool_name", "")
            if tool_name:
                return f"Using {tool_name}"
            return "Running tool"

        if event == "PostToolUseFailure":
            tool_name = hook_state.get("tool_name", "")
            if tool_name:
                return f"Tool failed: {tool_name}"
            return "Tool failed"

        if event == "UserPromptSubmit":
            return "Processing prompt"

        if event == "UserPromptSubmitRejected":
            return "Prompt blocked by hook"

        if event == "Stop":
            if session and session.parent_session_id is not None:
                return "Waiting for oversight report"
            return "Waiting for user input"

        if event == "StopFailure":
            return "API error"

        if event == "PermissionRequest":
            return "Permission: approval required"

        if event == "SessionEnd":
            return "Claude exited"

        return "Unknown state"

    def get_loaded_skills(self, session_name: str) -> list[str]:
        """Return skills observed via Skill tool_use for a session (#252)."""
        skills = self._loaded_skills.get(session_name, set())
        return sorted(skills)
