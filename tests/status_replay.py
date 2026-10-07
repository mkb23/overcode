"""Replay agent hook timelines through overcode's real status pipeline (#507).

A scenario is a timed list of what an agent CLI does — the telemetry it
sends and the pane frames it draws — plus the colour a person *should* see
over each stretch of time. ``replay`` feeds the telemetry through the real
writer for that backend and samples the real detector stack
(``StatusDetectorDispatcher`` → ``HookStatusDetector``, or the polling
detector when the fleet runs in polling mode) on a fixed cadence against a
fake clock, so a scenario exercises everything from hook-state writes and
obligation tracking to the sticky-green window and pane scraping.

Two telemetry paths are supported:

* **Claude Code** (``ev`` steps): each payload goes through
  ``hook_handler.handle_hook_event``, stdin JSON and all. Events overcode
  does not register for Claude (``SubagentStart``, ``SubagentStop``,
  ``Notification``, ...) are dropped before delivery, exactly as Claude
  Code would never call the hook for them. Scenarios still list them, so
  registering a new event later makes it flow through with no scenario
  edit.
* **opencode / opencode2** (``rec`` steps): each record is a plugin-hook
  invocation in the spy-capture format of ``tests/fixtures_opencode_events``.
  Before sampling, every record is run through the REAL bundled plugin
  (v1: ``overcode-telemetry.js`` via its exported factory; opencode2: the
  ``createTelemetry`` reducer in ``overcode-telemetry-core.mjs``) by
  ``tests/js/opencode_plugin_replay.mjs``, with the plugin's clock pinned
  to the scenario's. The hook-state file and event-log lines the plugin
  wrote for each record are then laid down at that record's time. opencode
  writes its hook files from inside the plugin — there is no hook handler
  process — so this is the whole of its telemetry path.

Claude scenarios are synthetic: their shapes follow live captures of Claude
Code 2.1.286 (Oct 2026), but every command, path and name is made up.
opencode scenarios reuse the verbatim live captures in
``tests/fixtures_opencode_events`` and ``tests/fixtures_opencode_panes``.
"""

from __future__ import annotations

import copy
import io
import json
import os
import shutil
import subprocess
import tempfile
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable, Optional
from unittest.mock import patch

from overcode import hook_handler
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

# Backends whose telemetry comes from a bundled opencode plugin
PLUGIN_BACKENDS = ("opencode", "opencode2")
_NODE_REPLAY = Path(__file__).parent / "js" / "opencode_plugin_replay.mjs"


# ---------------------------------------------------------------------------
# Scenario vocabulary
# ---------------------------------------------------------------------------

@dataclass
class Step:
    t: float
    payload: Optional[dict] = None  # a Claude hook payload
    pane: Optional[str] = None      # a pane frame
    record: Optional[dict] = None   # an opencode plugin-hook record
    write: Optional[dict] = None    # what the plugin wrote for a record


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


def rec(t: float, record: dict) -> Step:
    """One opencode plugin-hook invocation (spy-capture format) at time ``t``."""
    return Step(t, record=record)


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
    # The session's backend: picks the detector's StatusPatterns and, for
    # opencode/opencode2, which bundled plugin turns ``rec`` steps into
    # hook-file writes.
    backend: str = "claude-code"
    # The fleet's detection mode ("hooks" or "polling"); a backend without
    # HOOK_EVENTS is always polled whatever this says.
    fleet_mode: str = "hooks"

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


def node_available() -> bool:
    return shutil.which("node") is not None


def plugin_module(backend: str, scratch: Path) -> tuple[Path, Optional[str]]:
    """The real bundled plugin for ``backend``, loadable by node as ESM."""
    if backend == "opencode":
        from overcode.backends.opencode import bundled_plugin_path
        target = scratch / "overcode-telemetry.mjs"
        target.write_text(bundled_plugin_path().read_text(encoding="utf-8"))
        return target, None
    if backend == "opencode2":
        from overcode.backends.opencode2_plugin_install import bundled_plugin_dir_v2
        return bundled_plugin_dir_v2() / "overcode-telemetry-core.mjs", "v2"
    raise ValueError(f"no bundled plugin for backend {backend!r}")


def run_plugin(backend: str, records: list[dict]) -> list[dict]:
    """Run ``records`` through the real plugin; one ``{state, lines}`` per record.

    Each record's ``t`` (epoch seconds) is what the plugin's clock reads
    while it handles that record.
    """
    with tempfile.TemporaryDirectory(prefix="oc-replay-") as tmp:
        scratch = Path(tmp)
        module, flavor = plugin_module(backend, scratch)
        job = {
            "plugin": str(module),
            "env": {
                "OVERCODE_SESSION_NAME": AGENT,
                "OVERCODE_TMUX_SESSION": TMUX_SESSION,
                "OVERCODE_STATE_DIR": str(scratch / "state"),
                "HOME": str(scratch / "home"),
            },
            "clock": "record",
            "snapshots": True,
            "streams": [{"name": "s", "records": records}],
        }
        if flavor:
            job["flavor"] = flavor
        result = subprocess.run(
            ["node", str(_NODE_REPLAY)], input=json.dumps(job),
            capture_output=True, text=True, timeout=120,
        )
    if not result.stdout:
        raise RuntimeError(f"plugin replay produced no output: {result.stderr}")
    out = json.loads(result.stdout)
    if not out.get("ok"):
        raise RuntimeError(out.get("error"))
    return out["streams"][0]["writes"]


def _compile_records(steps: list[Step], backend: str) -> list[Step]:
    """Replace ``rec`` steps with the hook-file writes the real plugin makes."""
    recs = [s for s in steps if s.record is not None]
    if not recs:
        return steps
    if backend not in PLUGIN_BACKENDS:
        raise ValueError(f"rec steps need an opencode backend, not {backend!r}")
    records = []
    for s in recs:
        r = copy.deepcopy(s.record)
        r["t"] = EPOCH + s.t
        records.append(r)
    writes = run_plugin(backend, records)
    by_id = {id(s): w for s, w in zip(recs, writes)}
    out = []
    for s in steps:
        if s.record is None:
            out.append(s)
            continue
        w = by_id[id(s)]
        if w["lines"]:
            out.append(Step(s.t, write=w))
    return out


def _apply_write(state_dir: Path, write: dict) -> None:
    """Lay down what the plugin wrote: the state snapshot and the log lines."""
    d = state_dir / TMUX_SESSION
    d.mkdir(parents=True, exist_ok=True)
    if write.get("state") is not None:
        tmp = d / f"hook_state_{AGENT}.json.tmp"
        tmp.write_text(json.dumps(write["state"]))
        os.replace(tmp, d / f"hook_state_{AGENT}.json")
    with open(d / f"hook_events_{AGENT}.jsonl", "a") as f:
        for line in write["lines"]:
            f.write(line + "\n")


def replay(scenario: Scenario, state_dir: Path, cadence: float = 0.25) -> list[Sample]:
    """Run ``scenario`` and return one Sample every ``cadence`` seconds."""
    from overcode.status_detector_factory import StatusDetectorDispatcher

    clock = _Clock()
    tmux = _Tmux(scenario.initial_pane)
    session = SimpleNamespace(
        id="replay-id", name=AGENT, tmux_window=AGENT,
        parent_session_id="parent-id" if scenario.child else None,
        backend=scenario.backend,
    )
    steps = sorted(scenario.steps, key=lambda s: s.t)
    steps = _compile_records(steps, scenario.backend)
    env = {
        "OVERCODE_STATE_DIR": str(state_dir),
        "OVERCODE_SESSION_NAME": AGENT,
        "OVERCODE_TMUX_SESSION": TMUX_SESSION,
    }
    samples: list[Sample] = []
    with patch.dict(os.environ, env), patch("time.time", clock.time):
        # Built inside the env patch: hook detectors resolve their state
        # directory from OVERCODE_STATE_DIR when they are created.
        detector = StatusDetectorDispatcher(
            TMUX_SESSION, tmux=tmux, mode=scenario.fleet_mode,
        )
        i = 0
        n = int(round(scenario.end / cadence))
        for k in range(n + 1):
            t = k * cadence
            while i < len(steps) and steps[i].t <= t:
                step = steps[i]
                clock.now = EPOCH + step.t
                if step.pane is not None:
                    tmux.pane = step.pane
                elif step.write is not None:
                    _apply_write(state_dir, step.write)
                elif step.payload is not None and _delivered(step.payload):
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
