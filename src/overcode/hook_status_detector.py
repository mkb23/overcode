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
import re
import os
import subprocess
import time
from pathlib import Path
from typing import Dict, Optional, Tuple, TYPE_CHECKING

from .status_constants import (
    DEFAULT_CAPTURE_LINES,
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
    extract_background_bash_count,
    get_patterns,
    strip_ansi,
    is_shell_prompt,
    pane_shows_idle,
    shows_permission_prompt,
    status_bar_visible,
)
from .tui_helpers import format_duration

if TYPE_CHECKING:
    from .protocols import TmuxInterface
    from .status_patterns import StatusPatterns
    from .session_manager import Session


def _pane_shows_interrupt_prompt(
    pane_content: str, patterns: "StatusPatterns" = None
) -> bool:
    """Return True if the pane shows the escape-interrupt prompt (#431).

    The markers live on the backend's StatusPatterns
    (``interrupt_prompt_markers``) — Claude Code prints this text when the
    user hits Escape mid-turn, and no Stop hook fires for it, so status
    would otherwise stay stuck as RUNNING.
    """
    if not pane_content:
        return False
    if patterns is None:
        patterns = get_patterns()
    clean = strip_ansi(pane_content)
    # Only look at the tail — older interrupt prompts may linger in scrollback
    tail = "\n".join(clean.splitlines()[-40:])
    return patterns.shows_interrupt_prompt(tail)


def _interrupt_marker_count(pane_content: str, patterns: "StatusPatterns" = None) -> int:
    """Interrupt markers in the pane's tail: one more than before is a new Esc."""
    if not pane_content:
        return 0
    if patterns is None:
        patterns = get_patterns()
    tail = "\n".join(strip_ansi(pane_content).splitlines()[-40:])
    return sum(tail.count(marker) for marker in patterns.interrupt_prompt_markers)


def _pane_shows_dead_shell(pane_content: str, patterns: "StatusPatterns" = None) -> bool:
    """True when the agent process is gone and the pane sits at a bare shell prompt (#474).

    Claude Code fires SessionEnd on exit, but opencode emits nothing at all
    when it quits (``/exit``, a crash, Ctrl-C) — verified live on v1.18.29 —
    so the last hook state (usually ``Stop``) would say *waiting_user*
    forever. A live TUI always draws its own chrome at the bottom of the
    pane; only a dead one leaves the shell prompt as the last line with no
    input-hint marker anywhere near it.
    """
    if not pane_content:
        return False
    if patterns is None:
        patterns = get_patterns()
    clean = strip_ansi(pane_content)
    lines = [line.strip() for line in clean.splitlines() if line.strip()]
    if not lines or not is_shell_prompt(lines[-1]):
        return False
    tail = "\n".join(lines[-_DEAD_SHELL_TAIL_LINES:])
    return not patterns.shows_input_hint(tail)


# How many trailing pane lines a live TUI's input-hint marker must appear in
# for the pane to count as alive despite a shell-prompt-looking last line.
_DEAD_SHELL_TAIL_LINES = 12


# Events that mean Claude is in the middle of an active turn — drives the
# GREEN "acting" bucket in compute_status_detail.
_ACTING_EVENTS = frozenset({
    "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure",
})


# Tools whose PermissionRequest is a question or a plan, not a permission (#536)
_QUESTION_TOOLS = frozenset({"AskUserQuestion"})
_PLAN_TOOLS = frozenset({"ExitPlanMode"})


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
    # Codex-only event (design doc §2.3): fires when the user hits Escape
    # mid-turn. Unlike Claude Code, which prints no Stop/SessionEnd hook on
    # interrupt and relies entirely on the pane-scraped
    # `interrupt_prompt_markers` fallback above, codex's hook stdin says so
    # directly — the map alone downgrades a stuck RUNNING straight to
    # waiting_user without needing a pane read.
    "Interrupt": STATUS_WAITING_USER,
    # Codex-only event: session start. Not itself status-bearing (a
    # UserPromptSubmit typically follows immediately), so it falls through
    # to the same STATUS_WAITING_USER default unmapped events get — listed
    # explicitly here so the mapping is self-documenting rather than
    # silently relying on the .get() default.
    "SessionStart": STATUS_WAITING_USER,
}


# Window used for the sticky-green upgrade (#448). A Stop event whose last
# RUNNING-class predecessor fired within this many seconds is treated as
# part of an ongoing burst rather than a real stall — otherwise fast
# turns (quick text replies, sub-250ms tool uses) flicker yellow because
# the reader's poll window lands after Stop has overwritten the snapshot.
_RECENT_ACTIVITY_WINDOW_SECONDS = 1.5

# How many log lines to keep in memory per read — plenty for a 1.5s window.
_RECENT_EVENTS_LIMIT = 50

# A subagent unheard from for this long is assumed gone (#507). SubagentStop
# can be lost (a crash, an interrupt), and an entry left behind would hold
# the parent yellow forever. A live subagent blocked on a long wait sends no
# events, so this is generous.
_SUBAGENT_STALE_SECONDS = 3600.0


# A foreground sleep: the command is ``sleep N``, perhaps after a ``cd``
_FOREGROUND_SLEEP_RE = re.compile(r"^\s*(?:cd\s+\S+\s*&&\s*)?sleep\s+(\d+)(?![.\d])")
# A sleep still "in flight" this long past its end did not end the usual way
# (an Esc, a crash): stop calling it sleeping and let the other checks speak
_SLEEP_OVERRUN_SECONDS = 30.0

# Hooks say a turn is running but the pane has shown the CLI idle (chrome,
# no busy marker) this long with no hook since: the turn ended without a
# Stop (an Esc whose marker scrolled away, a crash, a slash command), so the
# agent is waiting, not working. Long enough to ride over a redraw.
_IDLE_CONFIRM_SECONDS = 15.0


# How long the last status-bar counts (monitors, shells) stand in while a
# menu or dialog covers the bar (#507). Covers are brief; past this the
# counts are unknown and read as 0.
_STATUS_BAR_HOLD_SECONDS = 30.0


def _live_subagent_count(hook_state: dict, now: float) -> int:
    """Subagents the parent is waiting on, per the hook state's map (#507)."""
    subagents = hook_state.get("subagents")
    if not isinstance(subagents, dict):
        return 0
    live = 0
    for entry in subagents.values():
        seen = entry.get("last_seen") if isinstance(entry, dict) else None
        if isinstance(seen, (int, float)) and now - seen <= _SUBAGENT_STALE_SECONDS:
            live += 1
    return live


# A wakeup fires as a prompt at its time. One this long past it with no
# prompt is overdue: the session moved on, was cleared, or dropped it, and
# the agent is not armed any more (engine-0.6.md "Overdue yellow").
_WAKEUP_OVERDUE_SECONDS = 120.0


def _is_overdue(obl, now: float) -> bool:
    if not isinstance(obl, dict) or obl.get("kind") != "schedule_wakeup":
        return False
    eta = obl.get("eta_absolute")
    return isinstance(eta, (int, float)) and now > eta + _WAKEUP_OVERDUE_SECONDS


def _live_obligations(obligations: list, now: float) -> list:
    """The obligations that can still wake the agent."""
    return [o for o in obligations if not _is_overdue(o, now)]


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
    background_shells: int = 0,
    subagent_count: int = 0,
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

    raw_obligations = hook_state.get("pending_obligations") or []
    obligations = _live_obligations(raw_obligations, time.time())
    overdue = len(raw_obligations) - len(obligations)
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

    # Claude asks a question and offers a plan through PermissionRequest
    # too (live hook logs, #536): a question needs a real answer (red), a
    # plan a yes/no (orange, its own badge)
    tool = (hook_state.get("tool_name") or "") if event == "PermissionRequest" else ""
    if tool in _QUESTION_TOOLS:
        candidates.append((STATUS_COLOR_RED, [StatusBadge(kind="ask_question")]))

    # ORANGE — quick yes/no approval
    if event == "PermissionRequest" and tool not in _QUESTION_TOOLS:
        kind = "plan_approval" if tool in _PLAN_TOOLS else "permission"
        candidates.append((
            STATUS_COLOR_ORANGE,
            [StatusBadge(kind=kind, label=(tool or None) if kind == "permission" else None)],
        ))
    if legacy_status == STATUS_WAITING_OVERSIGHT:
        # A child's Stop — unless the sticky-green window or its own
        # background work (#507) already lifted it out of waiting: a child
        # still mid-burst or waiting on a subagent isn't ready to report.
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
    # Background shells and subagents (#507). A background Bash's own
    # PostToolUse lands as soon as it launches and nothing fires when it
    # exits, so the status bar's shell count is the live signal for those.
    obligation_bg_count = sum(
        1 for o in obligations if isinstance(o, dict) and o.get("kind") == "bg_task"
    )
    synthetic_shells = max(0, background_shells - obligation_bg_count)
    if synthetic_shells > 0:
        for b in yellow_badges:
            if b.kind == "bg_task":
                b.count += synthetic_shells
                break
        else:
            yellow_badges.append(StatusBadge(kind="bg_task", count=synthetic_shells))
    if subagent_count > 0:
        yellow_badges.append(StatusBadge(kind="subagent", count=subagent_count))
    if yellow_badges:
        candidates.append((STATUS_COLOR_YELLOW, yellow_badges))
    elif overdue and not candidates:
        # Yellow only on the strength of a wakeup that never came: red, and
        # saying why, so red is trustworthy in both directions
        candidates.append((STATUS_COLOR_RED, [StatusBadge(kind="overdue", count=overdue)]))

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


def synthesize_status_detail_from_legacy(
    status: str,
    activity: str = "",
) -> Optional[StatusDetail]:
    """Build a StatusDetail from a legacy status enum alone (#TBD).

    Used in polling mode and when overcode hooks aren't installed — there's
    no obligation tracking or foreground classification, so we can only
    show the bucket color and a generic badge. Returns None for lifecycle
    states (TERMINATED / ASLEEP) which keep their existing display.
    """
    from .status_constants import (
        STATUS_RUNNING, STATUS_BUSY_SLEEPING, STATUS_WAITING_APPROVAL,
        STATUS_WAITING_USER, STATUS_WAITING_OVERSIGHT, STATUS_ERROR,
        STATUS_RUNNING_HEARTBEAT, STATUS_WAITING_HEARTBEAT,
        STATUS_HEARTBEAT_START, STATUS_TERMINATED, STATUS_ASLEEP, STATUS_DONE,
    )
    if status in (STATUS_TERMINATED, STATUS_ASLEEP, STATUS_DONE):
        return None
    if status in (STATUS_RUNNING_HEARTBEAT, STATUS_HEARTBEAT_START):
        return StatusDetail(STATUS_COLOR_GREEN, [StatusBadge(kind="heartbeat")], status)
    if status == STATUS_RUNNING:
        # Try to extract a tool name from the activity string (e.g. "Using Read")
        tool_label = None
        if activity:
            if activity.startswith("Using "):
                tool_label = activity[len("Using "):].split()[0] if len(activity) > 6 else None
            elif activity.startswith("Bash: "):
                tool_label = "Bash"
        return StatusDetail(STATUS_COLOR_GREEN, [StatusBadge(kind="tool", label=tool_label)], status)
    if status == STATUS_BUSY_SLEEPING:
        return StatusDetail(STATUS_COLOR_GREEN, [StatusBadge(kind="blocked_sleep")], status)
    if status == STATUS_WAITING_APPROVAL:
        return StatusDetail(STATUS_COLOR_ORANGE, [StatusBadge(kind="permission")], status)
    if status == STATUS_WAITING_OVERSIGHT:
        return StatusDetail(STATUS_COLOR_ORANGE, [StatusBadge(kind="oversight")], status)
    if status == STATUS_WAITING_HEARTBEAT:
        return StatusDetail(STATUS_COLOR_YELLOW, [StatusBadge(kind="heartbeat")], status)
    if status == STATUS_ERROR:
        return StatusDetail(STATUS_COLOR_RED, [StatusBadge(kind="error")], status)
    if status == STATUS_WAITING_USER:
        return StatusDetail(STATUS_COLOR_RED, [StatusBadge(kind="awaiting_input")], status)
    return None


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

    def __init__(
        self,
        tmux_session: str,
        tmux: "TmuxInterface" = None,
        patterns: "StatusPatterns" = None,
        state_dir: Optional[Path] = None,
    ):
        self.tmux_session = tmux_session
        self.capture_lines = DEFAULT_CAPTURE_LINES
        self._tmux = tmux
        # Optional pane_capture_gate.PaneCaptureGate: serves the last captured
        # text when the caller's loop found the pane unchanged (get_pane_content).
        self.capture_gate = None
        # Pane-scraping side signals (interrupt prompt, monitor count) are
        # backend-specific, so the detector always holds a pattern set.
        self._patterns = patterns or get_patterns()
        # Diagnostic phase tracking (same interface as PollingStatusDetector)
        self._last_detect_phase: Dict[str, str] = {}
        self._content_changed: Dict[str, bool] = {}
        # Skills observed via Skill tool_use events, keyed by session name (#252)
        self._loaded_skills: Dict[str, set] = {}
        # Structured 2-column status detail, populated by detect_status and
        # consumed by the ⏰ column. Keyed by session name (#TBD).
        self._status_details: Dict[str, StatusDetail] = {}
        # Parsed tail of each session's event log, keyed by the log's
        # (st_mtime_ns, st_size, limit): an unchanged log costs one stat.
        self._events_cache: Dict[str, tuple] = {}
        # When the interrupt marker was first seen in each pane (#507): the
        # marker lingers on screen after the person re-prompts, so it only
        # counts while no working event has arrived since.
        self._interrupt_seen_at: Dict[str, float] = {}
        # How many interrupt markers the pane's tail held last time
        self._interrupt_counts: Dict[str, int] = {}
        # When each pane was first seen idle while hooks said running
        self._idle_seen_at: Dict[str, float] = {}
        # The timestamp of the hook state behind each session's last status
        self._last_hook_at: Dict[str, float] = {}
        # Last visible status-bar counts: (seen_at, monitors, shells) (#507)
        self._status_bar_counts: Dict[str, Tuple[float, int, int]] = {}
        # The PermissionRequest (by hook-state timestamp) whose dialog has
        # been seen in the pane (#507). Nothing fires when it is answered,
        # so the dialog going away is the signal.
        self._permission_prompt_seen: Dict[str, float] = {}

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

        Events are oldest→newest. Only the tail of the file is read — a bounded
        window large enough to cover the last ``limit`` lines — so a long-lived
        agent's multi-MB hook-event log is not slurped whole on every detection
        cycle (that read was pinning a CPU core). Partial/corrupt tail lines are
        skipped silently — rotation, a mid-write read, or seeking into the
        middle of a line can leave one such line.
        """
        path = self._hook_event_log_path(session_name)
        # The log changes only when a hook fires; between hooks the tail is
        # re-read up to 4 Hz for the focused agent, so serve the parse from
        # the last read when the file's stat signature is unchanged (R12).
        try:
            st = os.stat(path)
        except OSError:
            self._events_cache.pop(session_name, None)
            return []
        signature = (st.st_mtime_ns, st.st_size, limit)
        cached = self._events_cache.get(session_name)
        if cached is not None and cached[0] == signature:
            return list(cached[1])
        # Read a bounded tail and grow it until it actually holds `limit` lines,
        # so the result is identical to reading the whole file while normally
        # staying O(window). Hook-event lines are usually a few hundred bytes,
        # but some carry large payloads, so the initial window can fall short —
        # doubling handles those without ever slurping a multi-MB log up front.
        window = max(limit * 4096, 128 * 1024)
        try:
            with open(path, "rb") as f:
                size = f.seek(0, os.SEEK_END)
                while True:
                    start = max(0, size - window)
                    f.seek(start)
                    lines = f.read().decode("utf-8", errors="replace").splitlines()
                    if start > 0 and lines:
                        # Seeked into the middle of a line — drop the partial head.
                        lines = lines[1:]
                    if len(lines) >= limit or start == 0:
                        break
                    window *= 2
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
        self._events_cache[session_name] = (signature, events)
        return list(events)

    def _most_recent_running_event_age(
        self, session_name: str, now: Optional[float] = None
    ) -> Optional[float]:
        """Seconds since the most recent RUNNING-class event, or None (#448)."""
        if now is None:
            now = time.time()
        for entry in reversed(self._read_recent_events(session_name)):
            if entry.get("agent_id"):
                continue  # a subagent's event, not the parent's turn (#507)
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
            or None if file is missing or corrupt. Never judged stale here:
            detect_status cross-checks a running state against the pane.
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
        lines = num_lines or self.capture_lines
        gate = self.capture_gate
        if gate is not None:
            return gate.capture(window, lines, self._capture_raw)
        return self._capture_raw(window, lines)

    def _capture_raw(self, window: str, lines: int) -> Optional[str]:
        """One capture-pane: the tmux interface if given, else a plain subprocess."""
        if self._tmux:
            return self._tmux.capture_pane(self.tmux_session, window, lines=lines)
        # Direct tmux subprocess fallback
        try:
            from .tmux_utils import _build_tmux_cmd

            result = subprocess.run(
                [*_build_tmux_cmd(), "capture-pane",
                 "-t", f"{self.tmux_session}:{window}",
                 "-p", "-S", f"-{lines}"],
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
        # This call decides the detail afresh: a path that returns early
        # leaves none, so a previous tick's colour can never stand in
        self._status_details.pop(session.name, None)
        self._last_hook_at.pop(session.name, None)

        if hook_state is None:
            # No hook state file — agent hasn't triggered a hook yet.
            # Check if the window exists to distinguish fresh-start from terminated.
            pane_content = self.get_pane_content(session.tmux_window, num_lines=num_lines)
            if pane_content is None:
                self._last_detect_phase[session.id] = "hook:no_state+no_window"
                return STATUS_TERMINATED, "Window no longer exists", ""
            if _pane_shows_dead_shell(pane_content, self._patterns):
                # The CLI died before its first hook (bad flag, missing
                # binary) and the window is back at the shell.
                self._last_detect_phase[session.id] = "hook:no_state+dead_shell"
                return STATUS_TERMINATED, "Agent exited - shell prompt", pane_content
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
        self._last_hook_at[session.name] = float(hook_state["timestamp"])

        if event == "SessionEnd":
            self._last_detect_phase[session.id] = "hook:SessionEnd"
            return self._detect_session_end_status(session, num_lines)

        status = _HOOK_STATUS_MAP.get(event, STATUS_WAITING_USER)

        # A question wants an answer, not an approval (#536)
        if event == "PermissionRequest" and hook_state.get("tool_name") in _QUESTION_TOOLS:
            status = STATUS_WAITING_USER

        # For child agents, Stop → waiting_oversight instead of waiting_user
        if event == "Stop" and session.parent_session_id is not None:
            status = STATUS_WAITING_OVERSIGHT

        # Read pane for activity enrichment and content return value
        pane_content = self.get_pane_content(session.tmux_window, num_lines=num_lines) or ""

        # A CLI that exits without a SessionEnd (opencode's /exit, any
        # crash) leaves its last hook state behind; the bare shell prompt
        # in the pane is the only evidence it is gone (#474).
        if _pane_shows_dead_shell(pane_content, self._patterns):
            self._last_detect_phase[session.id] = f"hook:{event}+dead_shell"
            self._status_details.pop(session.name, None)
            return STATUS_TERMINATED, "Agent exited - shell prompt", pane_content

        # An answered permission prompt: nothing fires on approval, so the
        # approved tool would show orange until its PostToolUse (#507).
        if event == "PermissionRequest" and self._permission_answered(
            session.name, hook_state, pane_content
        ):
            event = "PreToolUse"
            status = STATUS_RUNNING
            self._last_detect_phase[session.id] = "hook:PermissionRequest+answered"

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
        has_interrupt = bool(pane_content) and _pane_shows_interrupt_prompt(
            pane_content, self._patterns
        )
        # A marker above a live busy marker is an old one: the turn is running
        if has_interrupt and pane_shows_idle(pane_content, self._patterns) is False:
            has_interrupt = False
        has_interrupt = self._interrupt_still_current(
            session.name, has_interrupt, _interrupt_marker_count(pane_content, self._patterns),
        )
        if status in (STATUS_RUNNING, STATUS_BUSY_SLEEPING) and has_interrupt:
            # An Esc during a foreground sleep ends the sleep too
            status = STATUS_WAITING_USER
            sleep_dur = None
            self._last_detect_phase[session.id] = f"hook:{event}+interrupt"

        # A turn that ended with no Stop and no interrupt marker: the pane
        # itself has been idle for a while with no hook since. Trust it.
        ended_without_stop = (
            status in (STATUS_RUNNING, STATUS_BUSY_SLEEPING)
            and event in _ACTING_EVENTS
            and self._pane_idle_confirmed(session.name, hook_state, pane_content)
        )
        if not (status in (STATUS_RUNNING, STATUS_BUSY_SLEEPING) and event in _ACTING_EVENTS):
            self._idle_seen_at.pop(session.name, None)
        if ended_without_stop:
            status = (STATUS_WAITING_OVERSIGHT if session.parent_session_id is not None
                      else STATUS_WAITING_USER)
            sleep_dur = None
            has_interrupt = True  # the detail's red "awaiting input", as for an Esc

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
        monitor_count = (
            extract_active_monitor_count(pane_content, self._patterns) if pane_content else 0
        )
        shell_count = (
            extract_background_bash_count(pane_content, self._patterns) if pane_content else 0
        )
        monitor_count, shell_count = self._hold_status_bar_counts(
            session.name, pane_content, monitor_count, shell_count
        )
        if monitor_count > 0 and status in (STATUS_WAITING_USER, STATUS_WAITING_OVERSIGHT):
            status = STATUS_BUSY_SLEEPING
            self._last_detect_phase[session.id] = f"hook:{event}+monitors={monitor_count}"

        # Background shells and subagents will wake the agent too (#507)
        subagent_count = _live_subagent_count(hook_state, time.time())
        if (shell_count or subagent_count) and status in (
            STATUS_WAITING_USER, STATUS_WAITING_OVERSIGHT,
        ):
            status = STATUS_BUSY_SLEEPING

        # Build activity description
        activity = self._build_activity(event, hook_state, pane_content, session)

        # Enrich activity for busy_sleeping: either a parsed sleep duration (#289)
        # or a live Monitor count (#441). Monitor count wins if both apply.
        if status == STATUS_BUSY_SLEEPING:
            if monitor_count > 0:
                plural = "s" if monitor_count != 1 else ""
                activity = f"Watching {monitor_count} monitor{plural}"
            elif subagent_count > 0:
                plural = "s" if subagent_count != 1 else ""
                activity = f"Waiting on {subagent_count} background agent{plural}"
            elif shell_count > 0 and sleep_dur is None:
                plural = "s" if shell_count != 1 else ""
                activity = f"Waiting on {shell_count} background shell{plural}"
            else:
                activity = f"Sleeping {format_duration(sleep_dur)}" if sleep_dur else "Sleeping"

        # Record hook phase for diagnostics
        self._last_detect_phase[session.id] = f"hook:{event}"
        if ended_without_stop and status in (STATUS_WAITING_USER, STATUS_WAITING_OVERSIGHT):
            activity = "Waiting for user input (turn ended without a Stop)"
            self._last_detect_phase[session.id] = f"hook:{event}+idle_pane"

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
            background_shells=shell_count,
            subagent_count=subagent_count,
        )

        return status, activity, pane_content

    def _pane_idle_confirmed(
        self, session_name: str, hook_state: dict, pane_content: str
    ) -> bool:
        """True once the pane has read idle for _IDLE_CONFIRM_SECONDS with no hook since."""
        if not self._patterns.pane_confirms_idle:
            return False
        idle = pane_shows_idle(pane_content, self._patterns)
        if idle is False:
            self._idle_seen_at.pop(session_name, None)
            return False
        if idle is None:
            return False  # covered or unreadable: neither starts nor ends the wait
        now = time.time()
        try:
            hook_at = float(hook_state.get("timestamp", 0))
        except (TypeError, ValueError):
            hook_at = 0.0
        first = self._idle_seen_at.get(session_name)
        if first is None or first < hook_at:
            # Idle from now; a hook since the pane went idle restarts the clock
            first = self._idle_seen_at[session_name] = now
        return now - first >= _IDLE_CONFIRM_SECONDS

    def _interrupt_still_current(self, session_name: str, shown: bool, count: int = 1) -> bool:
        """Whether an on-screen interrupt marker still means "interrupted" (#507).

        The marker stays in the pane's tail after the person types a new
        prompt; once a working event arrives after the marker first showed,
        the agent has resumed and the marker is history. One more marker in
        the tail than last time is a new Esc, so it counts from now.
        """
        if not shown:
            self._interrupt_seen_at.pop(session_name, None)
            self._interrupt_counts.pop(session_name, None)
            return False
        now = time.time()
        if count > self._interrupt_counts.get(session_name, count):
            self._interrupt_seen_at[session_name] = now
        self._interrupt_counts[session_name] = count
        first_seen = self._interrupt_seen_at.setdefault(session_name, now)
        age = self._most_recent_running_event_age(session_name, now)
        return age is None or now - age <= first_seen

    def _permission_answered(
        self, session_name: str, hook_state: dict, pane_content: str
    ) -> bool:
        """True once this PermissionRequest's dialog was seen and has gone (#507).

        A dialog never recognised in the pane (another backend's wording, a
        restart mid-run) proves nothing, so that case stays orange.
        """
        request = float(hook_state.get("timestamp", 0))
        if not pane_content:
            return False
        if shows_permission_prompt(pane_content, self._patterns):
            self._permission_prompt_seen[session_name] = request
            return False
        return self._permission_prompt_seen.get(session_name) == request

    def _hold_status_bar_counts(
        self, session_name: str, pane_content: str, monitors: int, shells: int
    ) -> Tuple[int, int]:
        """Status-bar counts, held briefly while the bar is covered (#507).

        A menu or dialog drawn over the bar hides "1 monitor" / "2 shells"
        without anything having stopped; reading that as 0 would drop a
        waiting agent to red for as long as the cover stays.
        """
        now = time.time()
        if pane_content and status_bar_visible(pane_content, self._patterns):
            self._status_bar_counts[session_name] = (now, monitors, shells)
            return monitors, shells
        held = self._status_bar_counts.get(session_name)
        if held is not None and now - held[0] <= _STATUS_BAR_HOLD_SECONDS:
            return max(monitors, held[1]), max(shells, held[2])
        return monitors, shells

    def get_last_hook_at(self, session_name: str) -> Optional[float]:
        """Epoch seconds of the hook event behind the last detect_status, or None."""
        return self._last_hook_at.get(session_name)

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
        lines = [ln.strip() for ln in clean.strip().split('\n') if ln.strip()]

        if not lines:
            return STATUS_TERMINATED, "Agent exited", pane_content

        last_line = lines[-1]

        if is_shell_prompt(last_line):
            return STATUS_TERMINATED, "Agent exited - shell prompt", pane_content

        # No shell prompt → likely /clear, agent is waiting for input
        return STATUS_WAITING_USER, "Waiting for user input", pane_content

    def _find_sleep_duration(self, hook_state: dict) -> int | None:
        """The foreground ``sleep N`` in flight, from hook state's tool_input (#289).

        Only a PreToolUse whose command *is* a sleep (``sleep N``, perhaps
        after a ``cd``) counts: a PostToolUse means the sleep is over, and a
        command that merely mentions one ("grep 'sleep 30'", "make; sleep 5")
        is not sleeping. Past its end (plus slack) it is not sleeping either.
        """
        if hook_state.get("event") != "PreToolUse":
            return None
        tool_input = hook_state.get("tool_input")
        if not isinstance(tool_input, dict):
            return None
        command = tool_input.get("command", "")
        m = _FOREGROUND_SLEEP_RE.match(command) if isinstance(command, str) else None
        if m is None:
            return None
        dur = int(m.group(1))
        try:
            started = float(hook_state.get("timestamp", 0))
        except (TypeError, ValueError):
            return dur
        if time.time() > started + dur + _SLEEP_OVERRUN_SECONDS:
            return None
        return dur

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
            # The opencode/opencode2 plugins record a bounded reason (#474).
            reason = hook_state.get("error")
            if isinstance(reason, str) and reason.strip():
                return f"API error: {reason.strip()}"
            return "API error"

        if event == "PermissionRequest":
            tool = hook_state.get("tool_name")
            if tool in _QUESTION_TOOLS:
                return "Question: waiting for your answer"
            if tool in _PLAN_TOOLS:
                return "Plan: waiting for approval"
            return "Permission: approval required"

        if event == "SessionEnd":
            return "Agent exited"

        return "Unknown state"

    def get_loaded_skills(self, session_name: str) -> list[str]:
        """Return skills observed via Skill tool_use for a session (#252)."""
        skills = self._loaded_skills.get(session_name, set())
        return sorted(skills)
