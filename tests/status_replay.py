"""Replay Claude Code hook timelines through overcode's real status pipeline (#507).

A scenario is a timed list of what Claude Code does — hook payloads it sends
and pane frames it draws — plus the colour a person *should* see over each
stretch of time. ``replay`` feeds the events through the real entry point
(``hook_handler.handle_hook_event``, stdin JSON and all) and samples the real
``HookStatusDetector`` on a fixed cadence against a fake clock, so a scenario
exercises everything from hook-state writes and obligation tracking to the
sticky-green window and pane scraping.

Events overcode does not register for Claude (``SubagentStart``,
``SubagentStop``, ``Notification``, ...) are dropped before delivery, exactly
as Claude Code would never call the hook for them. Scenarios still list them,
so registering a new event later makes it flow through with no scenario edit.

Scenarios are synthetic: their shapes follow live captures of Claude Code
2.1.286 (Oct 2026), but every command, path and name is made up.
"""

from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable, Optional
from unittest.mock import patch

from overcode import hook_handler
from overcode.hook_status_detector import HookStatusDetector
from overcode.status_constants import (
    STATUS_COLOR_GREEN,
    STATUS_COLOR_ORANGE,
    STATUS_COLOR_RED,
    STATUS_COLOR_YELLOW,
)

GREEN, YELLOW, ORANGE, RED = (
    STATUS_COLOR_GREEN, STATUS_COLOR_YELLOW, STATUS_COLOR_ORANGE, STATUS_COLOR_RED,
)

TMUX_SESSION = "replay"
AGENT = "agent"
EPOCH = 1_800_000_000.0  # fake-clock origin; scenario times are offsets from it

REGISTERED_EVENTS = frozenset(e for e, _ in hook_handler.OVERCODE_HOOKS)
RESET_SOURCES = frozenset(hook_handler.CLAUDE_SESSION_START_MATCHER.split("|"))


# ---------------------------------------------------------------------------
# Scenario vocabulary
# ---------------------------------------------------------------------------

@dataclass
class Step:
    t: float
    payload: Optional[dict] = None  # a hook payload, or None for a pane frame
    pane: Optional[str] = None


def ev(t: float, event: str, tool: str = None, tool_use_id: str = None,
       agent_id: str = None, **tool_input) -> Step:
    """A hook payload as Claude Code sends it."""
    payload: dict = {"hook_event_name": event}
    if tool is not None:
        payload["tool_name"] = tool
        payload["tool_input"] = tool_input
    if tool_use_id is not None:
        payload["tool_use_id"] = tool_use_id
    if agent_id is not None:
        payload["agent_id"] = agent_id
    return Step(t, payload=payload)


def frame(t: float, pane: str) -> Step:
    """The pane changes to ``pane`` at time ``t``."""
    return Step(t, pane=pane)


def claude_pane(footer: str = "", body: str = "", status_line: str = "") -> str:
    """A Claude Code screen: transcript body, prompt box and status bar.

    ``footer`` is appended to the status bar the way Claude Code 2.1.286 adds
    segments: ``⏵⏵ bypass permissions on · 2 shells · ← 1 agent``.
    ``status_line`` is the ``✻ ...`` line above the prompt box.
    """
    rule = "─" * 80
    bar = "  ⏵⏵ bypass permissions on (shift+tab to cycle)"
    if footer:
        bar = f"  ⏵⏵ bypass permissions on · {footer}"
    lines = [body or "⏺ Working on it.", ""]
    if status_line:
        lines.append(status_line)
    lines += [rule, "❯ ", rule, bar]
    return "\n".join(lines)


@dataclass
class Scenario:
    name: str
    steps: list[Step]
    # (from_t, to_t, colour): every sample in [from_t, to_t] must show colour.
    # Stretches not covered are transition slack and go unchecked.
    expect: list[tuple[float, float, str]]
    end: float = 0.0
    child: bool = False
    initial_pane: str = field(default_factory=claude_pane)

    def __post_init__(self) -> None:
        if not self.end:
            self.end = max(to for _, to, _ in self.expect)


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------

@dataclass
class Sample:
    t: float
    status: str
    colour: Optional[str]
    badges: tuple[str, ...]


class _Clock:
    def __init__(self) -> None:
        self.now = EPOCH

    def time(self) -> float:
        return self.now


class _Tmux:
    def __init__(self, pane: str) -> None:
        self.pane = pane

    def capture_pane(self, session, window, lines=0):
        return self.pane


def _delivered(payload: dict) -> bool:
    event = payload["hook_event_name"]
    if event == "SessionStart":
        return payload.get("source") in RESET_SOURCES
    return event in REGISTERED_EVENTS


def replay(scenario: Scenario, state_dir: Path, cadence: float = 0.25) -> list[Sample]:
    """Run ``scenario`` and return one Sample every ``cadence`` seconds."""
    clock = _Clock()
    tmux = _Tmux(scenario.initial_pane)
    detector = HookStatusDetector(
        TMUX_SESSION, tmux=tmux, state_dir=state_dir / TMUX_SESSION,
    )
    session = SimpleNamespace(
        id="replay-id", name=AGENT, tmux_window=AGENT,
        parent_session_id="parent-id" if scenario.child else None,
    )
    steps = sorted(scenario.steps, key=lambda s: s.t)
    env = {
        "OVERCODE_STATE_DIR": str(state_dir),
        "OVERCODE_SESSION_NAME": AGENT,
        "OVERCODE_TMUX_SESSION": TMUX_SESSION,
    }
    samples: list[Sample] = []
    with patch.dict(os.environ, env), patch("time.time", clock.time):
        i = 0
        n = int(round(scenario.end / cadence))
        for k in range(n + 1):
            t = k * cadence
            while i < len(steps) and steps[i].t <= t:
                step = steps[i]
                clock.now = EPOCH + step.t
                if step.pane is not None:
                    tmux.pane = step.pane
                elif _delivered(step.payload):
                    _deliver(step.payload)
                i += 1
            clock.now = EPOCH + t
            status, _, _ = detector.detect_status(session)
            detail = detector.get_status_detail(AGENT)
            samples.append(Sample(
                t, status,
                detail.color if detail else None,
                tuple(b.kind for b in detail.badges) if detail else (),
            ))
    return samples


def _deliver(payload: dict) -> None:
    with patch("sys.stdin", io.StringIO(json.dumps(payload))), \
            redirect_stdout(io.StringIO()):
        hook_handler.handle_hook_event()


# ---------------------------------------------------------------------------
# Checks and metrics
# ---------------------------------------------------------------------------

def mismatches(scenario: Scenario, samples: Iterable[Sample]) -> list[str]:
    """Every sample that contradicts the scenario's expected colours."""
    out = []
    for s in samples:
        for lo, hi, colour in scenario.expect:
            if lo <= s.t <= hi and s.colour != colour:
                out.append(
                    f"t={s.t:6.2f}s expected {colour}, got {s.colour} "
                    f"({s.status}, badges={list(s.badges)})"
                )
    return out


def colour_changes(samples: Iterable[Sample]) -> int:
    """How many times the shown colour changed — the flicker count."""
    changes, prev = 0, None
    for s in samples:
        if prev is not None and s.colour != prev:
            changes += 1
        prev = s.colour
    return changes


def timeline(samples: Iterable[Sample]) -> str:
    """Run-length summary for failure messages: ``green 0.0-8.0 | red 8.25-20.0``."""
    runs: list[list] = []
    for s in samples:
        if runs and runs[-1][0] == s.colour:
            runs[-1][2] = s.t
        else:
            runs.append([s.colour, s.t, s.t])
    return " | ".join(f"{c} {a:g}-{b:g}" for c, a, b in runs)
