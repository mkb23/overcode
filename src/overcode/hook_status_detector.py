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
    STATUS_TERMINATED,
    STATUS_ERROR,
    STATUS_RUNNING_HEARTBEAT,
    STATUS_WAITING_HEARTBEAT,
    STATUS_COLOR_GREEN,
    STATUS_COLOR_ORANGE,
    STATUS_COLOR_YELLOW,
    STATUS_COLOR_RED,
    StatusBadge,
    StatusDetail,
    color_priority,
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


# Events that mean Claude is in the middle of an active turn — drives the
# GREEN "acting" bucket in compute_status_detail.
_ACTING_EVENTS = frozenset({
    "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure",
})


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


# Window used for the sticky-green upgrade (#448). A Stop event whose last
# RUNNING-class predecessor fired within this many seconds is treated as
# part of an ongoing burst rather than a real stall — otherwise fast
# turns (quick text replies, sub-250ms tool uses) flicker yellow because
# the reader's poll window lands after Stop has overwritten the snapshot.
_RECENT_ACTIVITY_WINDOW_SECONDS = 1.5

# How many log lines to keep in memory per read — plenty for a 1.5s window.
_RECENT_EVENTS_LIMIT = 50


def _badges_from_obligations(obligations: list[dict]) -> list[StatusBadge]:
    """Convert raw obligation dicts into stacked StatusBadge entries.

    Identical kinds stack: monitor×2 is one badge with count=2 rather than
    two badges. ETAs collapse to the earliest. Labels collapse to the first
    one seen — for stacked kinds the label is less important than the count.

    For wake-time obligations we prefer the *remaining* seconds (from the
    stored absolute wake time) over the original delay, so the column
    counts down rather than showing a static "in Ns".
    """
    now = time.time()
    by_kind: dict[str, StatusBadge] = {}
    for obl in obligations:
        if not isinstance(obl, dict):
            continue
        kind = obl.get("kind")
        if not kind:
            continue
        eta_abs = obl.get("eta_absolute")
        if isinstance(eta_abs, (int, float)):
            eta = max(0.0, eta_abs - now)
        else:
            eta = obl.get("eta_seconds")
        label = obl.get("label")
        if kind in by_kind:
            b = by_kind[kind]
            b.count += 1
            if isinstance(eta, (int, float)):
                if b.eta_seconds is None or eta < b.eta_seconds:
                    b.eta_seconds = float(eta)
        else:
            by_kind[kind] = StatusBadge(
                kind=kind,
                label=label if isinstance(label, str) else None,
                count=1,
                eta_seconds=float(eta) if isinstance(eta, (int, float)) else None,
            )
    # Stable order: YELLOW kinds in a canonical order, then anything else.
    order = ["schedule_wakeup", "cron", "monitor", "bg_task", "heartbeat"]
    rank = {k: i for i, k in enumerate(order)}
    return sorted(by_kind.values(), key=lambda b: rank.get(b.kind, 99))


def _green_badges(
    event: str,
    hook_state: dict,
    sleep_duration_seconds: Optional[int],
) -> list[StatusBadge]:
    """Build the column-2 badges for the GREEN (acting) bucket."""
    tool_name = hook_state.get("tool_name") or ""
    foreground = hook_state.get("foreground") or {}
    blocked_on = foreground.get("blocked_on") if isinstance(foreground, dict) else None

    if event == "UserPromptSubmit":
        return [StatusBadge(kind="generating")]

    if blocked_on == "ci":
        return [StatusBadge(kind="blocked_ci", label=tool_name or None)]
    if blocked_on == "process":
        return [StatusBadge(kind="blocked_process", label=tool_name or None)]
    if blocked_on == "sleep" or sleep_duration_seconds is not None:
        return [StatusBadge(
            kind="blocked_sleep",
            eta_seconds=float(sleep_duration_seconds) if sleep_duration_seconds else None,
        )]

    if tool_name:
        return [StatusBadge(kind="tool", label=tool_name)]
    return [StatusBadge(kind="tool")]


def compute_status_detail(
    hook_state: Optional[dict],
    event: str,
    session: "Session",
    pane_content: str,
    monitor_count: int,
    has_interrupt: bool,
    sleep_duration_seconds: Optional[int],
    legacy_status: str,
) -> StatusDetail:
    """Reduce hook state + side signals into a 4-color StatusDetail.

    Pure-ish: reads `session.parent_session_id` and counts already-extracted
    monitor streams, but otherwise just consumes the inputs. The reducer
    builds candidate (color, badges) tuples for each bucket the agent is in,
    then picks the highest-priority color. Badges from the winning bucket
    are returned; losing-bucket badges drop on the floor (we'll surface them
    as sidecars in a follow-up).
    """
    if hook_state is None:
        return StatusDetail(
            color=STATUS_COLOR_RED,
            badges=[StatusBadge(kind="awaiting_input")],
            legacy_status=legacy_status,
        )

    obligations = hook_state.get("pending_obligations") or []
    # Monitor streams seen in the pane but never registered as obligations
    # (Claude installed Monitor before overcode's hook started, or the
    # PostToolUse already cleared it). Synthesize a badge so the user still
    # sees the active stream.
    obligation_monitor_count = sum(
        1 for o in obligations if isinstance(o, dict) and o.get("kind") == "monitor"
    )
    synthetic_monitors = max(0, monitor_count - obligation_monitor_count)

    # --- Candidate buckets -------------------------------------------------
    candidates: list[tuple[str, list[StatusBadge]]] = []

    # RED — needs substantive input
    if has_interrupt:
        candidates.append((STATUS_COLOR_RED, [StatusBadge(kind="awaiting_input")]))
    if event == "StopFailure" or legacy_status == STATUS_ERROR:
        candidates.append((STATUS_COLOR_RED, [StatusBadge(kind="error")]))
    if event == "UserPromptSubmitRejected":
        candidates.append((STATUS_COLOR_RED, [StatusBadge(kind="error", label="rejected")]))

    # ORANGE — quick yes/no approval
    if event == "PermissionRequest":
        tool = hook_state.get("tool_name") or ""
        candidates.append((
            STATUS_COLOR_ORANGE,
            [StatusBadge(kind="permission", label=tool or None)],
        ))
    if event == "Stop" and session is not None and session.parent_session_id is not None:
        candidates.append((STATUS_COLOR_ORANGE, [StatusBadge(kind="oversight")]))

    # GREEN — actively working
    if event in _ACTING_EVENTS:
        candidates.append((STATUS_COLOR_GREEN, _green_badges(event, hook_state, sleep_duration_seconds)))
    elif legacy_status == STATUS_RUNNING or (
        legacy_status == STATUS_BUSY_SLEEPING and sleep_duration_seconds is not None
    ):
        # Sticky-green burst (event got overwritten) or a foreground sleep
        # that lifted Stop back to RUNNING. Monitor-driven BUSY_SLEEPING is
        # *armed*, not acting — that case falls through to YELLOW below.
        if sleep_duration_seconds is not None:
            candidates.append((STATUS_COLOR_GREEN, [
                StatusBadge(kind="blocked_sleep", eta_seconds=float(sleep_duration_seconds)),
            ]))
        else:
            tool = hook_state.get("tool_name") or ""
            candidates.append((
                STATUS_COLOR_GREEN,
                [StatusBadge(kind="tool", label=tool or None)],
            ))

    # YELLOW — armed
    yellow_badges = _badges_from_obligations(obligations)
    if synthetic_monitors > 0:
        # Add or bump the monitor badge
        for b in yellow_badges:
            if b.kind == "monitor":
                b.count += synthetic_monitors
                break
        else:
            yellow_badges.append(StatusBadge(kind="monitor", count=synthetic_monitors))
    if yellow_badges:
        candidates.append((STATUS_COLOR_YELLOW, yellow_badges))

    # --- Resolve ----------------------------------------------------------
    if not candidates:
        # Stop fired (or unknown event) with nothing pending → genuine RED idle.
        return StatusDetail(
            color=STATUS_COLOR_RED,
            badges=[StatusBadge(kind="awaiting_input")],
            legacy_status=legacy_status,
        )

    candidates.sort(key=lambda c: color_priority(c[0]), reverse=True)
    winning_color, winning_badges = candidates[0]
    return StatusDetail(
        color=winning_color,
        badges=winning_badges,
        legacy_status=legacy_status,
    )


def augment_with_legacy_heartbeat(
    detail: Optional[StatusDetail],
    legacy_status: str,
) -> Optional[StatusDetail]:
    """Project the legacy heartbeat status enum onto the two-column model (#TBD task 6).

    Until the daemon and TUI stop minting `STATUS_RUNNING_HEARTBEAT` and
    `STATUS_WAITING_HEARTBEAT`, we bridge them at the column boundary by
    surfacing a `heartbeat` badge alongside (or in place of) whatever the
    hook reducer produced.

    Mapping:
      WAITING_HEARTBEAT — the agent is idle but a heartbeat instruction will
        re-prompt it. That's YELLOW armed → add a heartbeat badge. If the
        reducer said RED awaiting_input (no obligations seen), upgrade to
        YELLOW. If YELLOW already, append. ORANGE/GREEN take precedence
        (the user is more interested in the approval/work than the
        heartbeat) so we leave them.
      RUNNING_HEARTBEAT — the agent IS working, the heartbeat just kicked it
        off. Stay GREEN, append a heartbeat badge so the row reads "tool
        … 💓".
    """
    if legacy_status == STATUS_WAITING_HEARTBEAT:
        heartbeat = StatusBadge(kind="heartbeat")
        if detail is None or not detail.badges:
            return StatusDetail(STATUS_COLOR_YELLOW, [heartbeat], legacy_status)
        if detail.color == STATUS_COLOR_RED:
            return StatusDetail(STATUS_COLOR_YELLOW, [heartbeat], legacy_status)
        if detail.color == STATUS_COLOR_YELLOW:
            badges = [b for b in detail.badges if b.kind != "heartbeat"]
            badges.append(heartbeat)
            return StatusDetail(STATUS_COLOR_YELLOW, badges, legacy_status)
        return detail

    if legacy_status == STATUS_RUNNING_HEARTBEAT:
        heartbeat = StatusBadge(kind="heartbeat")
        if detail is None:
            return StatusDetail(STATUS_COLOR_GREEN, [heartbeat], legacy_status)
        if detail.color == STATUS_COLOR_GREEN:
            badges = [b for b in detail.badges if b.kind != "heartbeat"]
            badges.append(heartbeat)
            return StatusDetail(STATUS_COLOR_GREEN, badges, legacy_status)
        return detail

    return detail


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
        # Structured 2-column status detail, populated by detect_status and
        # consumed by the ⏰ column. Keyed by session name (#TBD).
        self._status_details: Dict[str, StatusDetail] = {}

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

    def _hook_event_log_path(self, session_name: str) -> Path:
        """Get the hook event log path for a session (#448)."""
        return self._state_dir / f"hook_events_{session_name}.jsonl"

    def _read_recent_events(self, session_name: str, limit: int = _RECENT_EVENTS_LIMIT) -> list:
        """Return recent event records from the append-only log (#448).

        Events are oldest→newest. Partial/corrupt tail lines are skipped
        silently — rotation or a mid-write read can leave one such line.
        """
        path = self._hook_event_log_path(session_name)
        try:
            with open(path) as f:
                lines = f.readlines()
        except (FileNotFoundError, OSError):
            return []

        events: list = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            if "event" not in entry or "timestamp" not in entry:
                continue
            try:
                float(entry["timestamp"])
            except (TypeError, ValueError):
                continue
            events.append(entry)
        return events

    def _most_recent_running_event_age(
        self, session_name: str, now: Optional[float] = None
    ) -> Optional[float]:
        """Seconds since the most recent RUNNING-class event, or None (#448)."""
        if now is None:
            now = time.time()
        for entry in reversed(self._read_recent_events(session_name)):
            if _HOOK_STATUS_MAP.get(entry.get("event", "")) == STATUS_RUNNING:
                try:
                    return now - float(entry["timestamp"])
                except (TypeError, ValueError):
                    return None
        return None

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
        has_interrupt = bool(pane_content) and _pane_shows_interrupt_prompt(pane_content)
        if status == STATUS_RUNNING and has_interrupt:
            status = STATUS_WAITING_USER
            self._last_detect_phase[session.id] = f"hook:{event}+interrupt"

        # Sticky-green upgrade (#448). A Stop hook firing between bursts of
        # RUNNING-class events would otherwise flash yellow on every poll
        # that lands after Stop but before the next UserPromptSubmit. If
        # the event log shows a RUNNING-class event within the last
        # _RECENT_ACTIVITY_WINDOW_SECONDS, treat the agent as still
        # running. Skip when the pane shows a real interrupt prompt — that
        # is a genuine pause, not a burst.
        if (
            event == "Stop"
            and status in (STATUS_WAITING_USER, STATUS_WAITING_OVERSIGHT)
            and not has_interrupt
        ):
            age = self._most_recent_running_event_age(session.name)
            if age is not None and age <= _RECENT_ACTIVITY_WINDOW_SECONDS:
                status = STATUS_RUNNING
                self._last_detect_phase[session.id] = (
                    f"hook:{event}+sticky_green({age:.2f}s)"
                )

        # Monitor tool leaves a persistent stream that can wake the agent
        # after Stop/SessionEnd has fired. Treat that as STATUS_BUSY_SLEEPING
        # — same "idle but externally trigger-able" category as a bash sleep
        # (#441 reuses the #289 state instead of minting a new one).
        monitor_count = extract_active_monitor_count(pane_content) if pane_content else 0
        if monitor_count > 0 and status in (STATUS_WAITING_USER, STATUS_WAITING_OVERSIGHT):
            status = STATUS_BUSY_SLEEPING
            self._last_detect_phase[session.id] = f"hook:{event}+monitors={monitor_count}"

        # Build activity description
        activity = self._build_activity(event, hook_state, pane_content, session)

        # Enrich activity for busy_sleeping: either a parsed sleep duration (#289)
        # or a live Monitor count (#441). Monitor count wins if both apply.
        if status == STATUS_BUSY_SLEEPING:
            if monitor_count > 0:
                plural = "s" if monitor_count != 1 else ""
                activity = f"Watching {monitor_count} monitor{plural}"
            else:
                activity = f"Sleeping {format_duration(sleep_dur)}" if sleep_dur else "Sleeping"

        # Record hook phase for diagnostics
        self._last_detect_phase[session.id] = f"hook:{event}"

        # Cache the structured 2-column detail for the ⏰ column to read.
        # Parallel to the legacy status — does not affect the tuple return.
        self._status_details[session.name] = compute_status_detail(
            hook_state=hook_state,
            event=event,
            session=session,
            pane_content=pane_content,
            monitor_count=monitor_count,
            has_interrupt=has_interrupt,
            sleep_duration_seconds=sleep_dur,
            legacy_status=status,
        )

        return status, activity, pane_content

    def get_status_detail(self, session_name: str) -> Optional[StatusDetail]:
        """Return the most recent StatusDetail for a session, or None.

        Populated as a side effect of detect_status. The ⏰ column reads this
        to render column-2 badges; absent → render nothing.
        """
        return self._status_details.get(session_name)

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
