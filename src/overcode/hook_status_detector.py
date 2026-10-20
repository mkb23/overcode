"""
Hook-based status detector for Claude sessions (#5).

Reads hook state files written by Claude Code hooks (UserPromptSubmit,
PreToolUse, PostToolUse, Stop, PermissionRequest, SessionEnd) to determine
agent status without tmux pane scraping.

Design:
- Hook state is the authority for status. No polling fallback.
- Running-state hooks (UserPromptSubmit, PreToolUse, PostToolUse) are
  trusted until the pane proves otherwise (an interrupt marker, a CLI idle
  for a while, a dead shell).
- One tick is one ``TickContext`` (hook state, pane, one ``now``, the
  counts) run through an ordered table of named rules (``RULES``). Each
  rule may replace the ``Verdict`` (colour, badges, activity); the last one
  that did names the phase. The legacy status enum is derived from the
  verdict through one table (``legacy_status_for``), so colour and enum
  cannot disagree.
"""

import json
import re
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, TYPE_CHECKING

from . import conversation_warning, swarm
from .clock import SYSTEM_CLOCK, Clock
from .paths import agent_path, session_dir
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
    STATUS_UNKNOWN,
    STATUS_COLOR_GREEN,
    STATUS_COLOR_ORANGE,
    STATUS_COLOR_YELLOW,
    STATUS_COLOR_RED,
    STATUS_COLOR_REST,
    StatusBadge,
    StatusDetail,
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


# Pane helpers take the pane already stripped of ANSI codes (once per tick)

def _pane_shows_interrupt_prompt(clean: str, patterns: "StatusPatterns" = None) -> bool:
    """Return True if the pane shows the escape-interrupt prompt (#431).

    The markers live on the backend's StatusPatterns
    (``interrupt_prompt_markers``) — Claude Code prints this text when the
    user hits Escape mid-turn, and no Stop hook fires for it, so status
    would otherwise stay stuck as RUNNING.
    """
    if not clean:
        return False
    if patterns is None:
        patterns = get_patterns()
    # Only look at the tail — older interrupt prompts may linger in scrollback
    tail = "\n".join(clean.splitlines()[-40:])
    return patterns.shows_interrupt_prompt(tail)


def _interrupt_marker_count(clean: str, patterns: "StatusPatterns" = None) -> int:
    """Interrupt markers in the pane's tail: one more than before is a new Esc."""
    if not clean:
        return 0
    if patterns is None:
        patterns = get_patterns()
    tail = "\n".join(clean.splitlines()[-40:])
    return sum(tail.count(marker) for marker in patterns.interrupt_prompt_markers)


def _pane_shows_dead_shell(clean: str, patterns: "StatusPatterns" = None) -> bool:
    """True when the agent process is gone and the pane sits at a bare shell prompt (#474).

    Claude Code fires SessionEnd on exit, but opencode emits nothing at all
    when it quits (``/exit``, a crash, Ctrl-C) — verified live on v1.18.29 —
    so the last hook state (usually ``Stop``) would say *waiting_user*
    forever. A live TUI always draws its own chrome at the bottom of the
    pane; only a dead one leaves the shell prompt as the last line with no
    input-hint marker anywhere near it.
    """
    if not clean:
        return False
    if patterns is None:
        patterns = get_patterns()
    lines = [line.strip() for line in clean.splitlines() if line.strip()]
    if not lines or not is_shell_prompt(lines[-1]):
        return False
    tail = "\n".join(lines[-_DEAD_SHELL_TAIL_LINES:])
    return not patterns.shows_input_hint(tail)


# How many trailing pane lines a live TUI's input-hint marker must appear in
# for the pane to count as alive despite a shell-prompt-looking last line.
_DEAD_SHELL_TAIL_LINES = 12


# Events that mean Claude is in the middle of an active turn: GREEN
_ACTING_EVENTS = frozenset({
    "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure",
})


# Tools whose PermissionRequest is a question or a plan, not a permission (#536)
_QUESTION_TOOLS = frozenset({"AskUserQuestion"})
_PLAN_TOOLS = frozenset({"ExitPlanMode"})


# Hook event → status mapping. The rules below decide the status; this map
# only says which events are RUNNING-class for the event log (sticky green),
# and documents what each event means on its own.
_HOOK_STATUS_MAP = {
    "UserPromptSubmit": STATUS_RUNNING,
    "PreToolUse": STATUS_RUNNING,
    "PostToolUse": STATUS_RUNNING,
    "PostToolUseFailure": STATUS_RUNNING,  # Tool failed but agent is still working
    "Stop": STATUS_WAITING_USER,
    "StopFailure": STATUS_ERROR,  # API error ended the turn
    "UserPromptSubmitRejected": STATUS_ERROR,  # Hook blocked prompt e.g. budget exceeded (#428)
    "PermissionRequest": STATUS_WAITING_APPROVAL,
    "SessionEnd": STATUS_TERMINATED,
    # Codex-only event (design doc §2.3): fires when the user hits Escape
    # mid-turn. Unlike Claude Code, which prints no Stop/SessionEnd hook on
    # interrupt and relies entirely on the pane-scraped
    # `interrupt_prompt_markers` fallback above, codex's hook stdin says so
    # directly: a turn that is over, like a Stop.
    "Interrupt": STATUS_WAITING_USER,
    # Codex-only event: session start. Not itself status-bearing (a
    # UserPromptSubmit typically follows immediately); listed so the
    # mapping is self-documenting.
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

# A foreground sleep: the command is ``sleep N``, perhaps after a ``cd``
_FOREGROUND_SLEEP_RE = re.compile(
    r"^\s*(?:cd\s+\S+\s*&&\s*)?sleep\s+(\d+(?:\.\d+)?)([smhd]?)(?![\w.])")
_SLEEP_UNIT_SECONDS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
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
    """Delegated work the parent is waiting on: agents plus workflow runs."""
    return sum(swarm.in_flight(hook_state, now))


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


def _foreground_sleep_seconds(hook_state: dict, now: float) -> Optional[int]:
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
    dur = max(1, round(float(m.group(1)) * _SLEEP_UNIT_SECONDS[m.group(2)]))
    try:
        started = float(hook_state.get("timestamp", 0))
    except (TypeError, ValueError):
        return dur
    if now > started + dur + _SLEEP_OVERRUN_SECONDS:
        return None
    return dur


# =============================================================================
# Badges
# =============================================================================

def _badges_from_obligations(obligations: list[dict], now: float) -> list[StatusBadge]:
    """Convert raw obligation dicts into stacked StatusBadge entries.

    Identical kinds stack: monitor×2 is one badge with count=2 rather than
    two badges. ETAs collapse to the earliest. Labels collapse to the first
    one seen — for stacked kinds the label is less important than the count.

    For wake-time obligations we prefer the *remaining* seconds (from the
    stored absolute wake time) over the original delay, so the column
    counts down rather than showing a static "in Ns".
    """
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
        at = float(eta_abs) if isinstance(eta_abs, (int, float)) else None
        if kind in by_kind:
            b = by_kind[kind]
            b.count += 1
            if isinstance(eta, (int, float)):
                if b.eta_seconds is None or eta < b.eta_seconds:
                    b.eta_seconds = float(eta)
                    b.eta_at = at
        else:
            by_kind[kind] = StatusBadge(
                kind=kind,
                label=label if isinstance(label, str) else None,
                count=1,
                eta_seconds=float(eta) if isinstance(eta, (int, float)) else None,
                eta_at=at,
            )
    # Stable order: YELLOW kinds in a canonical order, then anything else.
    order = ["schedule_wakeup", "cron", "monitor", "bg_task", "heartbeat"]
    rank = {k: i for i, k in enumerate(order)}
    return sorted(by_kind.values(), key=lambda b: rank.get(b.kind, 99))


def _sleep_badge(hook_state: dict, duration: Optional[int], now: float) -> StatusBadge:
    """A foreground sleep's badge, ending ``duration`` after its PreToolUse.

    Its end is fixed, so the badge carries it (``eta_at``) and counts down;
    ``now + duration`` would move forward every tick and never count.
    """
    if not duration:
        return StatusBadge(kind="blocked_sleep")
    try:
        end = float(hook_state.get("timestamp")) + duration
    except (TypeError, ValueError):
        return StatusBadge(kind="blocked_sleep", eta_seconds=float(duration))
    return StatusBadge(kind="blocked_sleep", eta_seconds=max(0.0, end - now), eta_at=end)


def _green_badges(event: str, hook_state: dict) -> list[StatusBadge]:
    """The badges of a turn in progress: generating, or the tool in flight.

    A foreground ``sleep`` gets its badge from the ``sleep`` rule, which
    alone knows whether the sleep is still running.
    """
    if event == "UserPromptSubmit":
        return [StatusBadge(kind="generating")]
    tool_name = hook_state.get("tool_name") or ""
    foreground = hook_state.get("foreground") or {}
    blocked_on = foreground.get("blocked_on") if isinstance(foreground, dict) else None
    if blocked_on == "ci":
        return [StatusBadge(kind="blocked_ci", label=tool_name or None)]
    if blocked_on == "process":
        return [StatusBadge(kind="blocked_process", label=tool_name or None)]
    return [StatusBadge(kind="tool", label=tool_name or None)]


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


# What an armed agent is waiting on, in words that hold still between ticks
_ARMED_WORDS = {
    "schedule_wakeup": "scheduled wakeup",
    "cron": "cron",
    "monitor": "monitor",
    "bg_task": "background task",
    "subagent": "background agent",
    "heartbeat": "heartbeat",
    "children": "child agents",
}


# =============================================================================
# The tick: context, verdict, rules
# =============================================================================

@dataclass
class TickContext:
    """Everything one detection tick knows, gathered once.

    The detector fills the pane-derived signals (its stateful probes run
    while gathering, never inside a rule); tests build one directly. All
    times are against the one ``now``.
    """

    hook_state: dict
    now: float
    event: str = ""
    pane: str = ""
    clean: str = ""                       # the pane stripped of ANSI codes, once
    is_child: bool = False
    patterns: Optional["StatusPatterns"] = None
    dead_shell: bool = False              # the CLI is gone: a bare shell prompt
    permission_answered: bool = False     # this PermissionRequest's dialog came and went
    sleep_seconds: Optional[int] = None   # a foreground ``sleep N`` still in flight
    interrupted: bool = False             # a current interrupt marker (an Esc)
    idle_confirmed: bool = False          # the pane has read idle long enough
    running_age: Optional[float] = None   # seconds since the last RUNNING-class event (Stop only)
    monitors: int = 0                     # status-bar counts, held while the bar is covered
    shells: int = 0
    subagents: Optional[int] = None       # default: delegated work in flight (swarm.in_flight)
    workflows: int = 0                    # of which workflow runs (for the words)
    children: int = 0                     # its child agents still working (last tick's)
    obligations: list = field(init=False)  # pending obligations that can still fire
    overdue: int = field(init=False)       # wakeups that never came

    def __post_init__(self):
        if not self.event:
            self.event = self.hook_state.get("event", "") or ""
        if self.subagents is None:
            agents, self.workflows = swarm.in_flight(self.hook_state, self.now)
            self.subagents = agents + self.workflows
        raw = self.hook_state.get("pending_obligations") or []
        self.obligations = _live_obligations(raw, self.now)
        self.overdue = len(raw) - len(self.obligations)

    @property
    def tool_name(self) -> str:
        return self.hook_state.get("tool_name") or ""


@dataclass
class Verdict:
    """What one tick says about an agent: colour, badges and activity.

    ``color`` None is a lifecycle end (terminated): no colour, no detail.
    ``armable``: the turn is over, so background work (monitors, shells,
    subagents, obligations) can still wake it and lift it to yellow.
    ``final``: no later rule may change it.
    ``rests_as``: the legacy status when it differs from the colour's, for
    a turn that is over and only scheduled to wake (a cron, a wakeup): it
    is yellow, but nothing is working, so it stays waiting (and its time
    is not green time), as before the rule table.
    """

    color: Optional[str]
    badges: List[StatusBadge]
    activity: str
    armable: bool = False
    final: bool = False
    rests_as: Optional[str] = None

    @property
    def lead(self) -> Optional[str]:
        return self.badges[0].kind if self.badges else None

    @property
    def legacy_status(self) -> str:
        return self.rests_as or legacy_status_for(self.color, self.lead)

    def detail(self) -> Optional[StatusDetail]:
        if self.color is None:
            return None
        return StatusDetail(color=self.color, badges=list(self.badges),
                            legacy_status=self.legacy_status)


# The legacy status enum, derived from the verdict: (colour, lead badge) →
# status, with (colour, None) as each colour's default. The one place the
# enum is decided, so it cannot disagree with the colour.
_LEGACY_BY_VERDICT: Dict[Tuple[Optional[str], Optional[str]], str] = {
    (None, None): STATUS_TERMINATED,
    (STATUS_COLOR_GREEN, "blocked_sleep"): STATUS_BUSY_SLEEPING,  # a foreground sleep
    (STATUS_COLOR_GREEN, None): STATUS_RUNNING,                   # generating, a tool
    (STATUS_COLOR_YELLOW, None): STATUS_BUSY_SLEEPING,            # armed: background work
    (STATUS_COLOR_ORANGE, None): STATUS_WAITING_APPROVAL,         # permission, question, plan
    (STATUS_COLOR_REST, None): STATUS_WAITING_OVERSIGHT,          # a child whose turn is over
    (STATUS_COLOR_RED, "error"): STATUS_ERROR,
    (STATUS_COLOR_RED, None): STATUS_WAITING_USER,                # awaiting input, overdue
}


def legacy_status_for(color: Optional[str], lead_badge: Optional[str]) -> str:
    """The legacy status enum for a verdict's colour and lead badge kind."""
    status = _LEGACY_BY_VERDICT.get((color, lead_badge))
    if status is None:
        status = _LEGACY_BY_VERDICT.get((color, None), STATUS_WAITING_USER)
    return status


def _awaiting(activity: str, armable: bool = True) -> Verdict:
    return Verdict(STATUS_COLOR_RED, [StatusBadge(kind="awaiting_input")], activity, armable=armable)


def _turn_over(ctx: TickContext, activity: str) -> Verdict:
    """A turn that ended: a child is at rest, anyone else needs input."""
    if ctx.is_child:
        return Verdict(STATUS_COLOR_REST, [], activity, armable=True)
    return _awaiting(activity)


def base_verdict(ctx: TickContext) -> Verdict:
    """What the hook event says on its own."""
    event, hook_state = ctx.event, ctx.hook_state
    activity = build_activity(event, hook_state, ctx.is_child)
    if event in _ACTING_EVENTS:
        return Verdict(STATUS_COLOR_GREEN, _green_badges(event, hook_state), activity)
    if event == "Stop":
        return _turn_over(ctx, activity)
    if event == "StopFailure":
        return Verdict(STATUS_COLOR_RED, [StatusBadge(kind="error")], activity)
    if event == "UserPromptSubmitRejected":
        return Verdict(STATUS_COLOR_RED, [StatusBadge(kind="error", label="rejected")], activity)
    if event == "PermissionRequest":
        tool = ctx.tool_name
        if tool in _PLAN_TOOLS:
            return Verdict(STATUS_COLOR_ORANGE, [StatusBadge(kind="plan_approval")], activity)
        return Verdict(STATUS_COLOR_ORANGE, [StatusBadge(kind="permission", label=tool or None)],
                       activity)
    # Interrupt (codex), SessionStart and anything unknown: nothing is running
    return _awaiting(activity)


# --- Rules ------------------------------------------------------------------
# Each takes the context and the verdict so far and returns a new verdict,
# or None to leave it. Order matters; a final verdict ends the run.

def rule_session_end(ctx: TickContext, v: Verdict) -> Optional[Verdict]:
    """SessionEnd fires on exit and on /clear: the pane's last line tells them apart."""
    if ctx.event != "SessionEnd":
        return None
    lines = [ln.strip() for ln in ctx.clean.strip().split("\n") if ln.strip()]
    if not lines:
        return Verdict(None, [], "Agent exited", final=True)
    if is_shell_prompt(lines[-1]):
        return Verdict(None, [], "Agent exited - shell prompt", final=True)
    # No shell prompt: /clear, and the CLI waits for input
    v = _awaiting("Waiting for user input", armable=False)
    v.final = True
    return v


def rule_dead_shell(ctx: TickContext, v: Verdict) -> Optional[Verdict]:
    """A CLI that exited without a SessionEnd (opencode's /exit, a crash) (#474)."""
    if not ctx.dead_shell:
        return None
    return Verdict(None, [], "Agent exited - shell prompt", final=True)


def rule_question(ctx: TickContext, v: Verdict) -> Optional[Verdict]:
    """A question wants an answer (#536), however it arrives.

    Asking is the tool's whole run: PreToolUse fires, the menu shows and
    nothing more until it is answered (PostToolUse). Under bypass
    permissions that is all there is; otherwise a PermissionRequest
    follows. Either way the agent is waiting on a person, not working.
    A question is an approval (orange): something to answer now, mid-turn;
    its ❓ badge tells it from a permission.
    """
    if (ctx.event not in ("PreToolUse", "PermissionRequest")
            or ctx.tool_name not in _QUESTION_TOOLS):
        return None
    return Verdict(STATUS_COLOR_ORANGE, [StatusBadge(kind="ask_question")], "Question: waiting for your answer")


def rule_permission_answered(ctx: TickContext, v: Verdict) -> Optional[Verdict]:
    """Nothing fires when a dialog is answered: once it has gone, the tool runs (#507)."""
    if ctx.event != "PermissionRequest" or not ctx.permission_answered:
        return None
    return Verdict(STATUS_COLOR_GREEN, _green_badges("PreToolUse", ctx.hook_state),
                   build_activity("PreToolUse", ctx.hook_state, ctx.is_child))


def rule_sleep(ctx: TickContext, v: Verdict) -> Optional[Verdict]:
    """A foreground ``sleep N`` in flight: working, but only waiting (#289)."""
    if ctx.sleep_seconds is None or v.color != STATUS_COLOR_GREEN:
        return None
    return Verdict(STATUS_COLOR_GREEN, [_sleep_badge(ctx.hook_state, ctx.sleep_seconds, ctx.now)],
                   f"Sleeping {format_duration(ctx.sleep_seconds)}" if ctx.sleep_seconds
                   else "Sleeping")


def rule_interrupt(ctx: TickContext, v: Verdict) -> Optional[Verdict]:
    """An Esc ends a working turn with no hook; the pane's marker says so (#431).

    It ends a working verdict, and a turn that is over (a Stop: opencode
    settles an interrupt to one): an interrupted agent, a child too, wants
    the person, not a report, so background work does not lift it. A
    permission, question or error keeps its own verdict; an Esc on a
    permission dialog arrives here as an answered permission.
    """
    if not ctx.interrupted or not (v.color == STATUS_COLOR_GREEN or v.armable):
        return None
    return _awaiting("Interrupted - waiting for user input", armable=False)


def rule_idle_confirmed(ctx: TickContext, v: Verdict) -> Optional[Verdict]:
    """A turn that ended without a Stop reads like a Stop (engine-0.6.md).

    A child's reads like a child's Stop: at rest.
    """
    if not ctx.idle_confirmed or v.color != STATUS_COLOR_GREEN:
        return None
    activity = ("At rest: no report yet (turn ended without a Stop)" if ctx.is_child
                else "Waiting for user input (turn ended without a Stop)")
    return _turn_over(ctx, activity)


def rule_sticky_green(ctx: TickContext, v: Verdict) -> Optional[Verdict]:
    """A Stop inside a burst of tool use is not a real stop yet (#448)."""
    if ctx.event != "Stop" or not v.armable or ctx.interrupted:
        return None
    if ctx.running_age is None or ctx.running_age > _RECENT_ACTIVITY_WINDOW_SECONDS:
        return None
    return Verdict(STATUS_COLOR_GREEN, [StatusBadge(kind="tool", label=ctx.tool_name or None)],
                   v.activity)


def _waiting_on_delegated(units: int, workflows: int) -> str:
    """The words for it: "Waiting on 2 background agents and 1 workflow"."""
    agents = max(0, units - workflows)
    parts = []
    if agents or not workflows:
        parts.append(_plural(agents, "background agent"))
    if workflows:
        parts.append(_plural(workflows, "workflow"))
    return "Waiting on " + " and ".join(parts)


# Obligations that wake an agent later without anything running meanwhile
_SCHEDULED_KINDS = frozenset({"schedule_wakeup", "cron", "heartbeat"})


def rule_armed(ctx: TickContext, v: Verdict) -> Optional[Verdict]:
    """A turn that is over, with something that will wake it: yellow (#441, #507).

    Obligations (wakeups, crons, registered monitors) and the status bar's
    live counts (monitors, background shells) plus subagents all arm it,
    and so do its own child agents still working: a parent whose turn ended
    while they work waits on them, not on you. A child is lifted only by
    its own background work: one with just a cron is at rest.
    """
    if not v.armable:
        return None
    if v.color == STATUS_COLOR_REST and not (
            ctx.monitors or ctx.shells or ctx.subagents or ctx.children):
        return None
    badges = _badges_from_obligations(ctx.obligations, ctx.now)
    # Monitor streams and background shells seen in the status bar but not
    # registered as obligations (installed before overcode's hook, or the
    # PostToolUse already cleared it; a background Bash's PostToolUse lands
    # at launch and nothing fires when it exits).
    for kind, seen in (("monitor", ctx.monitors), ("bg_task", ctx.shells)):
        registered = sum(1 for o in ctx.obligations if isinstance(o, dict) and o.get("kind") == kind)
        extra = max(0, seen - registered)
        if not extra:
            continue
        for b in badges:
            if b.kind == kind:
                b.count += extra
                break
        else:
            badges.append(StatusBadge(kind=kind, count=extra))
    if ctx.subagents:
        badges.append(StatusBadge(kind="subagent", count=ctx.subagents))
    if ctx.children:
        badges.append(StatusBadge(kind="children", count=ctx.children))
    if not badges:
        return None
    if ctx.monitors:
        activity = f"Watching {_plural(ctx.monitors, 'monitor')}"
    elif ctx.children:
        activity = f"Waiting on {ctx.children} child{'' if ctx.children == 1 else 'ren'}"
    elif ctx.subagents:
        activity = _waiting_on_delegated(ctx.subagents, ctx.workflows)
    elif ctx.shells:
        activity = f"Waiting on {_plural(ctx.shells, 'background shell')}"
    else:
        activity = "Armed: " + ", ".join(_ARMED_WORDS.get(b.kind, b.kind) for b in badges)
    # Only scheduled to wake (no monitor, shell, subagent, child or
    # background task running): the turn is still over, so the status stays
    # the one it rests in (waiting, or at rest for a child); only the colour arms
    working = any(b.kind not in _SCHEDULED_KINDS for b in badges)
    return Verdict(STATUS_COLOR_YELLOW, badges, activity,
                   rests_as=None if working else v.legacy_status)


def rule_overdue(ctx: TickContext, v: Verdict) -> Optional[Verdict]:
    """Waiting only on a wakeup that never came: red, saying why (engine-0.6.md)."""
    if not ctx.overdue or not v.armable or v.color != STATUS_COLOR_RED:
        return None
    return Verdict(STATUS_COLOR_RED, [StatusBadge(kind="overdue", count=ctx.overdue)],
                   "Waiting for user input (a scheduled wakeup never came)")


Rule = Callable[[TickContext, Verdict], Optional[Verdict]]

RULES: Tuple[Tuple[str, Rule], ...] = (
    ("session_end", rule_session_end),
    ("dead_shell", rule_dead_shell),
    ("question", rule_question),
    ("permission_answered", rule_permission_answered),
    ("sleep", rule_sleep),
    ("interrupt", rule_interrupt),
    ("idle_confirmed", rule_idle_confirmed),
    ("sticky_green", rule_sticky_green),
    ("armed", rule_armed),
    ("overdue", rule_overdue),
)


def decide(ctx: TickContext) -> Tuple[Verdict, str]:
    """Run the rules over the base verdict: (verdict, name of the rule that set it)."""
    verdict, winner = base_verdict(ctx), "base"
    for name, rule in RULES:
        if verdict.final:
            break
        new = rule(ctx, verdict)
        if new is not None:
            verdict, winner = new, name
    return verdict, winner


def phase_label(event: str, rule: str) -> str:
    """The diagnostic phase: the hook event, and the rule that decided, if not the event."""
    return f"hook:{event}" if rule == "base" else f"hook:{event}+{rule}"


def build_activity(event: str, hook_state: dict, is_child: bool = False) -> str:
    """The activity line for a hook event on its own."""
    if event in ("PreToolUse", "PostToolUse"):
        # For Bash, show the actual command for better visibility
        bash_activity = _parse_bash_activity(hook_state)
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
        if is_child:
            return "At rest: no report yet"
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


# BUSY_SLEEPING's activity says which kind it is: armed (yellow) or a
# foreground sleep (green), as the verdict table has it
_ARMED_ACTIVITY_RE = re.compile(
    r"^(?:Watching (\d+) monitor|Waiting on (\d+) background agent|Waiting on (\d+) background shell"
    r"|Waiting on (\d+) child)"
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
        STATUS_UNKNOWN,
    )
    if status in (STATUS_TERMINATED, STATUS_ASLEEP, STATUS_DONE, STATUS_UNKNOWN):
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
        # Armed work (yellow) says what it waits on; anything else, and
        # every polling-mode BUSY_SLEEPING, is a foreground sleep (green)
        m = _ARMED_ACTIVITY_RE.match(activity or "")
        if m:
            kind = ("monitor", "subagent", "bg_task", "children")[
                [g is not None for g in m.groups()].index(True)]
            count = int(next(g for g in m.groups() if g is not None))
            return StatusDetail(STATUS_COLOR_YELLOW, [StatusBadge(kind=kind, count=count)], status)
        if (activity or "").startswith("Armed: "):
            first = activity[len("Armed: "):].split(",")[0].strip()
            kind = next((k for k, w in _ARMED_WORDS.items() if w == first), "bg_task")
            return StatusDetail(STATUS_COLOR_YELLOW, [StatusBadge(kind=kind)], status)
        return StatusDetail(STATUS_COLOR_GREEN, [StatusBadge(kind="blocked_sleep")], status)
    if status == STATUS_WAITING_APPROVAL:
        kind = "ask_question" if (activity or "").startswith("Question:") else "permission"
        return StatusDetail(STATUS_COLOR_ORANGE, [StatusBadge(kind=kind)], status)
    if status == STATUS_WAITING_OVERSIGHT:
        return StatusDetail(STATUS_COLOR_REST, [], status)
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

    Hook state files are JSON files written by the agent's hooks at:
        ~/.overcode/sessions/{tmux_session}/agents/{session_id}/hook_state.json
    (or hook_state_{session_name}.json beside it, for an agent launched
    before per-agent directories: paths.agent_path)

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
        clock: Optional[Clock] = None,
    ):
        self.tmux_session = tmux_session
        # The wall clock every default ``now`` reads (the daemon's, injected)
        self.clock: Clock = clock if clock is not None else SYSTEM_CLOCK
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
        # Child agents still working, by parent session id: the monitor
        # daemon fills it from its last published tick (the dispatcher
        # shares one dict across every backend's detector)
        self.children_working: Dict[str, int] = {}
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
        # How many of the markers in each pane's tail were seen above a live
        # busy marker: those are history, even after the turn ends
        self._interrupt_stale: Dict[str, int] = {}
        # When each pane was first seen idle while hooks said running
        self._idle_seen_at: Dict[str, float] = {}
        # Whether this backend's busy marker has shown on any pane since
        # start. Until it has, a pane without it proves nothing: a CLI that
        # reworded its marker would otherwise turn every working agent red.
        self._busy_marker_seen = False
        # The timestamp of the hook state behind each session's last status
        self._last_hook_at: Dict[str, float] = {}
        # The 👥 rollup of each agent's delegated work, as of its last
        # detect_status (swarm.rollup); absent when it never delegated
        self._swarm_rollups: Dict[str, dict] = {}
        # The hook state behind each rollup: the engine builds an expanded
        # parent's virtual rows from it (swarm.virtual_rows)
        self._swarm_states: Dict[str, dict] = {}
        # Other conversations whose hooks ran under each agent's name
        # (hook_handler._note_foreign_session), for claude_bg.attribute
        self._foreign_sessions: Dict[str, dict] = {}
        # Last visible status-bar counts: (seen_at, monitors, shells) (#507)
        self._status_bar_counts: Dict[str, Tuple[float, int, int]] = {}
        # The PermissionRequest (by hook-state timestamp) whose dialog has
        # been seen in the pane (#507). Nothing fires when it is answered,
        # so the dialog going away is the signal.
        self._permission_prompt_seen: Dict[str, float] = {}
        # When a subagent's own background shell exited (swarm.settle_own_work)
        from .swarm_files import ShellEnds
        self.shell_ended = ShellEnds()

        # The session directory the hook handler writes into (paths.agent_path)
        self._state_dir = state_dir if state_dir is not None else session_dir(tmux_session)
        # Each agent's overcode session id, by name: its files live in its
        # own directory (agents/<id>/, item 35). Learnt from every session
        # detected, or told through note_agent.
        self._agent_ids: Dict[str, str] = {}

    def note_agent(self, session_name: str, session_id: Optional[str]) -> None:
        """Remember that ``session_name`` is agent ``session_id`` (whose files to read)."""
        if isinstance(session_id, str) and session_id:
            self._agent_ids[session_name] = session_id

    def _agent_file(self, session_name: str, kind: str) -> Path:
        return agent_path(self.tmux_session, self._agent_ids.get(session_name),
                          session_name, kind, base=self._state_dir)

    def _hook_state_path(self, session_name: str, session_id: Optional[str] = None) -> Path:
        """Get the hook state file path for a session."""
        self.note_agent(session_name, session_id)
        return self._agent_file(session_name, "hook_state")

    def _hook_event_log_path(self, session_name: str) -> Path:
        """Get the hook event log path for a session (#448)."""
        return self._agent_file(session_name, "hook_events")

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
            now = self.clock.time()
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

    def detect_status(
        self, session: "Session", num_lines: int = 0, *, now: Optional[float] = None
    ) -> Tuple[str, str, str]:
        """Detect session status using hook state files.

        No polling fallback. When no hook state exists, checks if the
        tmux window is alive and returns a sensible default. ``now`` is the
        tick's one clock reading (default: ``self.clock``).

        Returns:
            Tuple of (status, current_activity, pane_content)
        """
        if now is None:
            now = self.clock.time()
        self.note_agent(session.name, getattr(session, "id", None))
        hook_state = self._read_hook_state(session.name)
        # This call decides the detail afresh: a path that returns early
        # leaves none, so a previous tick's colour can never stand in
        self._status_details.pop(session.name, None)
        self._last_hook_at.pop(session.name, None)
        self._swarm_rollups.pop(session.name, None)
        self._swarm_states.pop(session.name, None)
        foreign = hook_state.get("foreign_sessions") if isinstance(hook_state, dict) else None
        if isinstance(foreign, dict) and foreign:
            self._foreign_sessions[session.name] = foreign
        else:
            self._foreign_sessions.pop(session.name, None)

        if hook_state is None:
            # No hook state file — agent hasn't triggered a hook yet.
            # Check if the window exists to distinguish fresh-start from terminated.
            pane_content = self.get_pane_content(session.tmux_window, num_lines=num_lines)
            if pane_content is None:
                self._last_detect_phase[session.id] = "hook:no_state+no_window"
                return STATUS_TERMINATED, "Window no longer exists", ""
            if _pane_shows_dead_shell(strip_ansi(pane_content), self._patterns):
                # The CLI died before its first hook (bad flag, missing
                # binary) and the window is back at the shell.
                self._last_detect_phase[session.id] = "hook:no_state+dead_shell"
                return STATUS_TERMINATED, "Agent exited - shell prompt", pane_content
            # Window alive, no hooks yet: starting up, not known yet
            self._last_detect_phase[session.id] = "hook:no_state"
            return STATUS_UNKNOWN, "Starting: waiting for first hook event", pane_content

        # Track loaded skills from persisted hook state (#252)
        # hook_handler.py accumulates skills in the "loaded_skills" field,
        # so we always read the full list — no race with polling interval.
        persisted_skills = hook_state.get("loaded_skills", [])
        if persisted_skills:
            if session.name not in self._loaded_skills:
                self._loaded_skills[session.name] = set()
            self._loaded_skills[session.name].update(persisted_skills)

        self._last_hook_at[session.name] = float(hook_state["timestamp"])
        hook_state = swarm.settle_own_work(hook_state, self.shell_ended)
        rollup = swarm.rollup(hook_state, now)
        if rollup is not None:
            self._swarm_rollups[session.name] = rollup
            self._swarm_states[session.name] = hook_state

        pane_content = self.get_pane_content(session.tmux_window, num_lines=num_lines) or ""
        ctx = self._tick_context(session, hook_state, pane_content, now)
        verdict, rule = decide(ctx)
        self._last_detect_phase[session.id] = phase_label(ctx.event, rule)
        detail = verdict.detail()
        if detail is not None:
            extra = []
            worker = conversation_warning.bg_worker(hook_state)
            if worker is not None:
                extra.append(conversation_warning.bg_worker_badge(worker))
            warning = conversation_warning.current(hook_state, now)
            if warning is not None:
                extra.append(conversation_warning.badge(warning))
            if extra:
                # Beside the light, never instead of it (hook_handler._place)
                detail = StatusDetail(detail.color, list(detail.badges) + extra,
                                      detail.legacy_status)
            self._status_details[session.name] = detail
        return verdict.legacy_status, verdict.activity, pane_content

    def _tick_context(
        self, session: "Session", hook_state: dict, pane_content: str, now: float
    ) -> TickContext:
        """Gather one tick's signals. The detector's stateful probes run here, in order.

        A CLI that has ended (SessionEnd, or a bare shell prompt) gets no
        probes: nothing about it is tracked across ticks.
        """
        name = session.name
        patterns = self._patterns
        event = hook_state.get("event", "") or ""
        clean = strip_ansi(pane_content) if pane_content else ""
        base = dict(
            hook_state=hook_state, now=now, event=event, pane=pane_content, clean=clean,
            is_child=session.parent_session_id is not None, patterns=patterns,
            children=self.children_working.get(session.id, 0),
        )
        if event == "SessionEnd":
            return TickContext(**base)
        if _pane_shows_dead_shell(clean, patterns):
            return TickContext(**base, dead_shell=True)

        answered = event == "PermissionRequest" and self._permission_answered(name, hook_state, clean)

        # An interrupt marker above a live busy marker is an old one
        shown = bool(clean) and _pane_shows_interrupt_prompt(clean, patterns)
        busy = shown and pane_shows_idle(clean, patterns, clean_content=clean) is False
        interrupted = self._interrupt_still_current(
            name, shown, _interrupt_marker_count(clean, patterns), now=now, busy=busy,
        )

        # The idle wait runs while hooks say a turn is working
        idle_confirmed = False
        if (event in _ACTING_EVENTS or answered) and not interrupted:
            idle_confirmed = self._pane_idle_confirmed(name, hook_state, clean, now=now)
        else:
            self._idle_seen_at.pop(name, None)

        running_age = None
        if event == "Stop" and not interrupted:
            running_age = self._most_recent_running_event_age(name, now)

        monitors = extract_active_monitor_count(clean, patterns, clean_content=clean) if clean else 0
        shells = extract_background_bash_count(clean, patterns, clean_content=clean) if clean else 0
        monitors, shells = self._hold_status_bar_counts(name, clean, monitors, shells, now=now)

        return TickContext(
            **base,
            permission_answered=answered,
            sleep_seconds=_foreground_sleep_seconds(hook_state, now),
            interrupted=interrupted,
            idle_confirmed=idle_confirmed,
            running_age=running_age,
            monitors=monitors,
            shells=shells,
        )

    def _pane_idle_confirmed(
        self, session_name: str, hook_state: dict, clean: str, now: Optional[float] = None
    ) -> bool:
        """True once the pane has read idle for _IDLE_CONFIRM_SECONDS with no hook since."""
        if not self._patterns.pane_confirms_idle:
            return False
        idle = pane_shows_idle(clean, self._patterns, clean_content=clean)
        if idle is False:
            self._busy_marker_seen = True
            self._idle_seen_at.pop(session_name, None)
            return False
        if idle is None:
            return False  # covered or unreadable: neither starts nor ends the wait
        if not self._busy_marker_seen:
            return False  # the marker is unproven on this CLI version: can't tell
        if now is None:
            now = self.clock.time()
        try:
            hook_at = float(hook_state.get("timestamp", 0))
        except (TypeError, ValueError):
            hook_at = 0.0
        first = self._idle_seen_at.get(session_name)
        if first is None or first < hook_at:
            # Idle from now; a hook since the pane went idle restarts the clock
            first = self._idle_seen_at[session_name] = now
        return now - first >= _IDLE_CONFIRM_SECONDS

    def _interrupt_still_current(
        self, session_name: str, shown: bool, count: int = 1, now: Optional[float] = None,
        busy: bool = False,
    ) -> bool:
        """Whether an on-screen interrupt marker still means "interrupted" (#507).

        The marker stays in the pane's tail after the person types a new
        prompt; once a working event arrives after the marker first showed,
        the agent has resumed and the marker is history. One more marker in
        the tail than last time is a new Esc, so it counts from now.

        Markers seen above a live busy marker (``busy``) are history for
        good: when that turn ends the pane still shows them, and only a
        marker added since is a new Esc.
        """
        if not shown:
            self._interrupt_seen_at.pop(session_name, None)
            self._interrupt_counts.pop(session_name, None)
            self._interrupt_stale.pop(session_name, None)
            return False
        if now is None:
            now = self.clock.time()
        if busy:
            self._interrupt_stale[session_name] = count
            self._interrupt_counts[session_name] = count
            self._interrupt_seen_at.pop(session_name, None)
            return False
        stale = self._interrupt_stale.get(session_name)
        if stale is not None:
            if count <= stale:
                # Old markers only (some may have scrolled out of the tail)
                self._interrupt_stale[session_name] = count
                self._interrupt_counts[session_name] = count
                return False
            del self._interrupt_stale[session_name]
        if count > self._interrupt_counts.get(session_name, count):
            self._interrupt_seen_at[session_name] = now
        self._interrupt_counts[session_name] = count
        first_seen = self._interrupt_seen_at.setdefault(session_name, now)
        age = self._most_recent_running_event_age(session_name, now)
        return age is None or now - age <= first_seen

    def _permission_answered(self, session_name: str, hook_state: dict, clean: str) -> bool:
        """True once this PermissionRequest's dialog was seen and has gone (#507).

        A dialog never recognised in the pane (another backend's wording, a
        restart mid-run) proves nothing, so that case stays orange.
        """
        request = float(hook_state.get("timestamp", 0))
        if not clean:
            return False
        if shows_permission_prompt(clean, self._patterns, clean_content=clean):
            self._permission_prompt_seen[session_name] = request
            return False
        return self._permission_prompt_seen.get(session_name) == request

    def _hold_status_bar_counts(
        self, session_name: str, clean: str, monitors: int, shells: int,
        now: Optional[float] = None,
    ) -> Tuple[int, int]:
        """Status-bar counts, held briefly while the bar is covered (#507).

        A menu or dialog drawn over the bar hides "1 monitor" / "2 shells"
        without anything having stopped; reading that as 0 would drop a
        waiting agent to red for as long as the cover stays.
        """
        if now is None:
            now = self.clock.time()
        if clean and status_bar_visible(clean, self._patterns, clean_content=clean):
            self._status_bar_counts[session_name] = (now, monitors, shells)
            return monitors, shells
        held = self._status_bar_counts.get(session_name)
        if held is not None and now - held[0] <= _STATUS_BAR_HOLD_SECONDS:
            return max(monitors, held[1]), max(shells, held[2])
        return monitors, shells

    def get_last_hook_at(self, session_name: str) -> Optional[float]:
        """Epoch seconds of the hook event behind the last detect_status, or None."""
        return self._last_hook_at.get(session_name)

    def get_swarm_rollup(self, session_name: str) -> Optional[dict]:
        """The 👥 counts from the last detect_status (swarm.rollup), or None."""
        return self._swarm_rollups.get(session_name)

    def get_swarm_state(self, session_name: str) -> Optional[dict]:
        """The hook state behind the last 👥 counts (for swarm.virtual_rows), or None."""
        return self._swarm_states.get(session_name)

    def get_foreign_sessions(self, session_name: str) -> Optional[dict]:
        """Other conversations the agent's hooks saw, by session id (None: none)."""
        return self._foreign_sessions.get(session_name)

    def get_status_detail(self, session_name: str) -> Optional[StatusDetail]:
        """Return the most recent StatusDetail for a session, or None.

        Populated as a side effect of detect_status. The ⏰ column reads this
        to render column-2 badges; absent → render nothing.
        """
        return self._status_details.get(session_name)

    def get_loaded_skills(self, session_name: str) -> list[str]:
        """Return skills observed via Skill tool_use for a session (#252)."""
        skills = self._loaded_skills.get(session_name, set())
        return sorted(skills)
