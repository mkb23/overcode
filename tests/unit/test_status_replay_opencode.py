"""Colour-level replay scenarios for opencode and opencode2 (#507).

The opencode counterpart of ``test_status_replay.py``: what colour should a
person see, and when, for an opencode agent? Each scenario is replayed by
``tests/status_replay.py`` — telemetry records go through the REAL bundled
plugin (the node replay harness), the hook files it writes are laid down on
a fake clock, and the real ``StatusDetectorDispatcher`` /
``HookStatusDetector`` with opencode's ``StatusPatterns`` reads them back
alongside pane frames.

Grounding:

* **opencode v1** scenarios replay the verbatim live captures in
  ``tests/fixtures_opencode_events/`` (opencode v1.18.29, Sep 2026) with
  their real record-to-record timing, and draw the verbatim panes in
  ``tests/fixtures_opencode_panes/v1.18.29/``. The only liberty taken is
  ``hold``: delaying one record (and everything after it) to model a
  person taking longer to answer a dialog than the capture did, or a
  subagent running longer — the event *sequence* is untouched.
* **opencode2** has no timed event corpus. Its scenarios use the live v2
  SSE envelope shapes pinned in ``tests/unit/test_opencode2_plugin.py``
  (captured from v0.0.0-dev-19272, Sep 2026) on a synthetic timeline, and
  the panes in ``tests/fixtures_opencode2_panes/``. Events never observed
  live are called out where used.

A scenario marked ``xfail(strict=True)`` documents a known detection bug,
exactly as in the Claude file: it fails today and XPASS-fails once fixed.

Colours follow the #507 model — what happens if you do nothing?
  green  working right now
  yellow idle at the prompt, but work in flight elsewhere will wake it
  orange blocked on a quick yes/no
  red    stalled: nothing happens until you act

opencode has no yellow: no background shells, Monitor streams, wakeups or
background subagents were observed in either version (the v1.18.29
``task`` tool's captured args are ``description``/``prompt``/
``subagent_type`` only), and the plugin never arms an obligation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.status_replay import (
    AGENT, GREEN, ORANGE, RED, TMUX_SESSION,
    Scenario, Step, colour_changes, frame, mismatches, node_available, rec, replay,
    timeline,
)

TESTS = Path(__file__).parent.parent
EVENTS = TESTS / "fixtures_opencode_events"
PANES_V1 = TESTS / "fixtures_opencode_panes" / "v1.18.29"
PANES_V2 = TESTS / "fixtures_opencode2_panes"

pytestmark = pytest.mark.skipif(
    not node_available(), reason="node is required to run the opencode plugin",
)


def _bug(reason):
    return pytest.mark.xfail(strict=True, reason=reason)


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------

def pane(name: str, v2: bool = False) -> str:
    return ((PANES_V2 if v2 else PANES_V1) / f"{name}.txt").read_text(encoding="utf-8")


def _key(record: dict) -> str:
    """``session.status`` for a bus event, ``tool.execute.before:read`` for a hook."""
    if record.get("hook") == "event":
        return record["event"].get("type", "")
    tool = (record.get("input") or {}).get("tool")
    return f"{record.get('hook')}:{tool}" if tool else str(record.get("hook"))


def capture(name: str, at: float = 0.0, hold: dict | None = None) -> list[Step]:
    """A live capture's records as ``rec`` steps, first record at ``at``.

    Real spacing is kept. ``hold`` maps a record key (see ``_key``) to extra
    seconds: the first matching record and everything after it happen that
    much later.
    """
    lines = (EVENTS / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines if line.strip()]
    if records and "_fixture" in records[0]:
        records = records[1:]
    pending = dict(hold or {})
    t0 = records[0]["t"]
    shift = 0.0
    steps = []
    for r in records:
        k = _key(r)
        if k in pending:
            shift += pending.pop(k)
        steps.append(rec(at + (r["t"] - t0) + shift, r))
    assert not pending, f"{name}: no record matched hold keys {list(pending)}"
    return steps


def v2(t: float, type_: str, **data) -> Step:
    """One opencode2 SSE envelope, as the TUI plugin receives it."""
    return rec(t, {"hook": "event", "event": {"type": type_, "data": data}})


V2_SESSION = "ses_f598f1212ffelIQor6hak3Q78d"
V2_CHILD = "ses_child00000000task000000000000"


def v2_prompt(t: float, inbox: str = "msg_u1") -> list[Step]:
    return [
        v2(t, "session.inbox.enqueued", sessionID=V2_SESSION, inboxID=inbox,
           item={"type": "user", "payload": {"text": "go"}}),
        v2(t + 0.01, "session.execution.started", sessionID=V2_SESSION),
    ]


def v2_shell(t0: float, t1: float, call: str = "call_1", command: str = "make test") -> list[Step]:
    return [
        v2(t0, "session.tool.input.started", sessionID=V2_SESSION, id=call, name="shell"),
        v2(t0 + 0.01, "session.tool.called", sessionID=V2_SESSION, id=call,
           input={"command": command}),
        v2(t1, "session.tool.success", sessionID=V2_SESSION, id=call),
    ]


def v2_end(t: float, how: str = "succeeded") -> Step:
    return v2(t, f"session.execution.{how}", sessionID=V2_SESSION)


def v1_marker_above_busy() -> str:
    """The interrupted turn still on screen above a new, busy turn.

    opencode leaves the ``· interrupted`` pill in the transcript forever;
    after a re-prompt it scrolls up but stays within the detector's
    40-line tail. Built from the two verbatim captures.
    """
    old = pane("interrupted").rstrip("\n").splitlines()
    cut = max(i for i, line in enumerate(old) if "· interrupted" in line)
    busy = [line for line in pane("busy").splitlines() if line.strip()]
    return "\n".join(old[:cut + 1] + busy[-12:])


# ---------------------------------------------------------------------------
# opencode v1 scenarios (live captures, v1.18.29)
# ---------------------------------------------------------------------------

# read_tool: UserPromptSubmit 0.0, Read 2.46–2.47, Stop 4.84.
PLAIN_TURN = Scenario(
    "oc_plain_turn",
    backend="opencode",
    initial_pane=pane("idle_after_response"),
    steps=capture("read_tool") + [
        frame(0.0, pane("busy")),
        frame(4.85, pane("idle_after_response")),
    ],
    expect=[(0.0, 4.75, GREEN), (5.0, 15.0, RED)],
)

# simple_turn: a fresh launch (no hook file until the first prompt at 2.87),
# then a 7 s text-only generation that fires no tool hooks; Stop at 10.02.
FRESH_LAUNCH_TEXT_TURN = Scenario(
    "oc_fresh_launch_text_turn",
    backend="opencode",
    initial_pane=pane("idle_fresh"),
    steps=capture("simple_turn") + [
        frame(2.87, pane("busy")),
        frame(10.02, pane("idle_after_response")),
    ],
    expect=[(0.0, 2.75, RED), (3.0, 10.0, GREEN), (10.25, 20.0, RED)],
)

# new_session's turn is a 2 s text reply: UserPromptSubmit 0.0, Stop 1.96.
QUICK_REPLY = Scenario(
    "oc_quick_reply",
    backend="opencode",
    initial_pane=pane("idle_after_response"),
    steps=capture("new_session") + [
        frame(0.0, pane("busy")),
        frame(1.96, pane("idle_after_response")),
    ],
    expect=[(0.25, 1.75, GREEN), (2.0, 10.0, RED)],
)

# permission_allow with the person answering 10 s later than in the capture:
# PermissionRequest 1.70, permission.replied once 13.44 (→ PreToolUse),
# PostToolUse 13.45, Stop 15.54.
PERMISSION_APPROVED = Scenario(
    "oc_permission_approved",
    backend="opencode",
    initial_pane=pane("idle_after_response"),
    steps=capture("permission_allow", hold={"permission.replied": 10.0}) + [
        frame(0.0, pane("busy")),
        frame(1.70, pane("permission_required")),
        frame(13.44, pane("busy")),
        frame(15.55, pane("idle_after_response")),
    ],
    expect=[(0.0, 1.5, GREEN), (1.75, 13.25, ORANGE), (13.5, 15.5, GREEN), (15.75, 25.0, RED)],
)

# permission_reject (Escape = Reject), answered 10 s later than captured:
# PermissionRequest 1.06, permission.replied reject 12.85 (→ PostToolUse),
# Stop 12.92 — the turn ends at once.
PERMISSION_DENIED = Scenario(
    "oc_permission_denied",
    backend="opencode",
    initial_pane=pane("idle_after_response"),
    steps=capture("permission_reject", hold={"permission.replied": 10.0}) + [
        frame(0.0, pane("busy")),
        frame(1.07, pane("permission_required")),
        frame(12.85, pane("idle_after_response")),
    ],
    expect=[(0.0, 1.0, GREEN), (1.25, 12.75, ORANGE), (14.5, 25.0, RED)],
)

# subagent_task, with the child's read held 20 s (a longer-running child).
# The parent sits inside its Task call (PreToolUse[Task] 2.04) while the
# child runs a whole turn of its own — chat.message, glob, read, idle —
# none of which may stop the parent. PostToolUse[Task] 26.02, Stop 27.16.
SUBAGENT_FOREGROUND = Scenario(
    "oc_subagent_foreground",
    backend="opencode",
    initial_pane=pane("idle_after_response"),
    steps=capture("subagent_task", hold={"tool.execute.before:read": 20.0}) + [
        frame(0.0, pane("busy")),
        frame(27.17, pane("subagent_settled")),
    ],
    expect=[(0.0, 27.5, GREEN), (27.75, 35.0, RED)],
)

# child_permission: the subagent's bash asks; the dialog is answered in the
# parent's pane 8 s later than captured. Fresh launch first (prompt 2.86),
# PreToolUse[Task] 4.61, PermissionRequest 5.82, replied 15.39,
# PostToolUse[Task] 16.29, Stop 17.65.
SUBAGENT_PERMISSION = Scenario(
    "oc_subagent_permission",
    backend="opencode",
    initial_pane=pane("idle_fresh"),
    steps=capture("child_permission", hold={"permission.replied": 8.0}) + [
        frame(2.86, pane("busy")),
        frame(5.82, pane("permission_required_subagent")),
        frame(15.39, pane("busy")),
        frame(17.65, pane("subagent_settled")),
    ],
    expect=[
        (0.0, 2.75, RED), (3.0, 5.75, GREEN), (6.0, 15.25, ORANGE),
        (15.5, 17.5, GREEN), (18.0, 25.0, RED),
    ],
)

# interrupt: a double-Escape at 3.17 (session.error MessageAbortedError,
# then idle → Stop). The "· interrupted" pill stays on screen; the person
# re-prompts at 12 (read_tool), and the old pill must not hold the new
# turn red.
INTERRUPT_THEN_REPROMPT = Scenario(
    "oc_interrupt_then_reprompt",
    backend="opencode",
    initial_pane=pane("idle_after_response"),
    steps=capture("interrupt") + capture("read_tool", at=12.0) + [
        frame(0.0, pane("busy")),
        frame(3.17, pane("interrupted")),
        frame(12.0, v1_marker_above_busy()),
    ],
    expect=[(0.0, 3.0, GREEN), (3.25, 11.75, RED), (12.0, 16.75, GREEN), (17.0, 25.0, RED)],
)

# provider_error: a bad API key. Prompt at 2.83, session.error APIError at
# 3.65 → StopFailure; the idle in the same millisecond must not hide it.
PROVIDER_ERROR = Scenario(
    "oc_provider_error",
    backend="opencode",
    initial_pane=pane("idle_fresh"),
    steps=capture("provider_error") + [
        frame(2.83, pane("busy")),
        frame(3.65, pane("error_api_key")),
    ],
    expect=[(0.0, 2.75, RED), (3.0, 3.5, GREEN), (3.75, 20.0, RED)],
)

# read_tool, then /new and a turn on the new root (new_session) at 10.
NEW_SESSION_MID_RUN = Scenario(
    "oc_new_session_mid_run",
    backend="opencode",
    initial_pane=pane("idle_after_response"),
    steps=capture("read_tool") + capture("new_session", at=10.0) + [
        frame(0.0, pane("busy")),
        frame(4.85, pane("idle_after_response")),
        frame(10.0, pane("busy")),
        frame(11.96, pane("idle_after_response")),
    ],
    expect=[(0.0, 4.75, GREEN), (5.0, 10.0, RED), (10.25, 11.75, GREEN), (12.0, 20.0, RED)],
)

# Two prompts, one idle: the second chat.message lands mid-turn and the turn
# settles once.
QUEUED_PROMPTS = Scenario(
    "oc_queued_prompts",
    backend="opencode",
    initial_pane=pane("idle_after_response"),
    steps=capture("queued_prompts") + [frame(0.0, pane("busy"))],
    expect=[(0.0, 3.25, GREEN), (3.75, 12.0, RED)],
)

# overcode restart --session: a fresh process that never sees
# session.created must adopt the conversation and still report.
RESUMED = Scenario(
    "oc_resumed",
    backend="opencode",
    initial_pane=pane("idle_fresh"),
    steps=capture("resume"),
    expect=[(0.0, 3.75, RED), (4.0, 5.25, GREEN), (5.5, 12.0, RED)],
)

# A child agent's Stop means "ready to report back": oversight orange.
CHILD_REPORTS_BACK = Scenario(
    "oc_child_reports_back",
    backend="opencode",
    child=True,
    initial_pane=pane("idle_after_response"),
    steps=capture("read_tool") + [
        frame(0.0, pane("busy")),
        frame(4.85, pane("idle_after_response")),
    ],
    expect=[(0.0, 4.75, GREEN), (5.0, 15.0, ORANGE)],
)

# A child interrupted from the keyboard has not finished: the Stop the
# interrupt settles to must read as "needs you", not "ready to report".
CHILD_INTERRUPTED = Scenario(
    "oc_child_interrupted",
    backend="opencode",
    child=True,
    initial_pane=pane("idle_after_response"),
    steps=capture("interrupt") + [
        frame(0.0, pane("busy")),
        frame(3.17, pane("interrupted")),
    ],
    expect=[(0.0, 3.0, GREEN), (3.25, 15.0, RED)],
)

# The plugin is missing (deleted, a user-owned file in its place, or an
# older project) so no hook file is ever written; only the pane says what
# is going on. Busy from 2 to 10.
_PLUGIN_MISSING_FRAMES = [
    frame(2.0, pane("busy")),
    frame(10.0, pane("idle_after_response")),
]

PLUGIN_MISSING_POLLING_FLEET = Scenario(
    "oc_plugin_missing_polling_fleet",
    backend="opencode",
    fleet_mode="polling",
    initial_pane=pane("idle_fresh"),
    steps=list(_PLUGIN_MISSING_FRAMES),
    expect=[(0.0, 1.75, RED), (2.25, 9.75, GREEN), (10.25, 20.0, RED)],
)

# Same agent in a fleet whose default is hooks (any Claude agent with hooks
# makes it so — settings.resolve_detection_mode). The opencode agent is then
# read by HookStatusDetector, which with no hook file at all reports
# "Waiting for first hook event" — red — however busy the pane is.
# `overcode doctor` says this case "falls back to pane polling"
# (OpencodeBackend.refine_health_verdict); it does not.
PLUGIN_MISSING_HOOKS_FLEET = Scenario(
    "oc_plugin_missing_hooks_fleet",
    backend="opencode",
    fleet_mode="hooks",
    initial_pane=pane("idle_fresh"),
    steps=list(_PLUGIN_MISSING_FRAMES),
    expect=[(0.0, 1.75, RED), (2.25, 9.75, GREEN), (10.25, 20.0, RED)],
)


# ---------------------------------------------------------------------------
# opencode2 scenarios (live v2 shapes, synthetic timing)
# ---------------------------------------------------------------------------

V2_PLAIN_TURN = Scenario(
    "oc2_plain_turn",
    backend="opencode2",
    initial_pane=pane("idle_after_response", v2=True),
    steps=[
        v2(0.0, "session.created", sessionID=V2_SESSION, agent="build"),
        *v2_prompt(1.0),
        *v2_shell(3.0, 7.0),
        v2_end(9.0),
        frame(1.0, pane("busy", v2=True)),
        frame(9.0, pane("idle_after_response", v2=True)),
    ],
    expect=[(0.0, 0.75, RED), (1.0, 8.75, GREEN), (9.25, 20.0, RED)],
)

V2_QUICK_REPLY = Scenario(
    "oc2_quick_reply",
    backend="opencode2",
    initial_pane=pane("idle_after_response", v2=True),
    steps=[
        v2(0.0, "session.created", sessionID=V2_SESSION, agent="build"),
        *v2_prompt(1.0),
        v2_end(1.5),
    ],
    expect=[(1.0, 2.5, GREEN), (2.75, 10.0, RED)],
)

# Live shape: flat `action`, the command in `resources`, the call id in
# `source.id`. Answered (Allow once) 8 s later.
_V2_ASK = dict(
    id="per_1", sessionID=V2_SESSION, action="shell", resources=["echo hi2"],
    source={"type": "tool", "messageID": "msg_a1", "id": "call_1"},
)

V2_PERMISSION_APPROVED = Scenario(
    "oc2_permission_approved",
    backend="opencode2",
    initial_pane=pane("idle_after_response", v2=True),
    steps=[
        v2(0.0, "session.created", sessionID=V2_SESSION, agent="build"),
        *v2_prompt(1.0),
        v2(2.0, "session.tool.input.started", sessionID=V2_SESSION, id="call_1", name="shell"),
        v2(2.1, "permission.asked", **_V2_ASK),
        v2(10.0, "permission.replied", sessionID=V2_SESSION, requestID="per_1", reply="once"),
        v2(10.1, "session.tool.called", sessionID=V2_SESSION, id="call_1",
           input={"command": "echo hi2"}),
        v2(10.2, "session.tool.success", sessionID=V2_SESSION, id="call_1"),
        v2_end(12.0),
        frame(1.0, pane("busy", v2=True)),
        frame(2.1, pane("permission_required", v2=True)),
        frame(10.0, pane("busy", v2=True)),
        frame(12.0, pane("idle_after_response", v2=True)),
    ],
    expect=[(1.0, 2.0, GREEN), (2.25, 9.75, ORANGE), (10.0, 11.75, GREEN), (14.0, 20.0, RED)],
)

V2_PERMISSION_DENIED = Scenario(
    "oc2_permission_denied",
    backend="opencode2",
    initial_pane=pane("idle_after_response", v2=True),
    steps=[
        v2(0.0, "session.created", sessionID=V2_SESSION, agent="build"),
        *v2_prompt(1.0),
        v2(2.0, "session.tool.input.started", sessionID=V2_SESSION, id="call_1", name="shell"),
        v2(2.1, "permission.asked", **_V2_ASK),
        v2(10.0, "permission.replied", sessionID=V2_SESSION, requestID="per_1", reply="reject"),
        v2_end(10.1),
        frame(1.0, pane("busy", v2=True)),
        frame(2.1, pane("permission_required", v2=True)),
        frame(10.0, pane("idle_after_response", v2=True)),
    ],
    expect=[(1.0, 2.0, GREEN), (2.25, 9.75, ORANGE), (12.0, 20.0, RED)],
)

# The live child-session shape (test_opencode2_plugin.py::
# test_child_subagent_turn_fully_ignored_live_shape), stretched: the
# `subagent` tool's child runs for 20 s with its own inbox/execution events.
_V2_CALL = "chatcmpl-tool-a5b70ac4319d7835"
V2_SUBAGENT_FOREGROUND = Scenario(
    "oc2_subagent_foreground",
    backend="opencode2",
    initial_pane=pane("idle_after_response", v2=True),
    steps=[
        v2(0.0, "session.created", sessionID=V2_SESSION, agent="build"),
        *v2_prompt(1.0),
        v2(2.0, "session.tool.input.started", sessionID=V2_SESSION,
           assistantMessageID="msg_a1", id=_V2_CALL, name="subagent"),
        v2(2.1, "session.tool.called", sessionID=V2_SESSION, id=_V2_CALL, executed=True,
           input={"agent": "general", "prompt": "answer done"}),
        v2(2.2, "session.created", sessionID=V2_CHILD, parentID=V2_SESSION,
           slug="shiny-tiger", version="0.0.0-dev-19272"),
        v2(2.3, "session.inbox.enqueued", sessionID=V2_CHILD, inboxID="msg_c1",
           item={"type": "user", "payload": {"text": "answer done"}}),
        v2(2.4, "session.execution.started", sessionID=V2_CHILD),
        v2(22.0, "session.execution.succeeded", sessionID=V2_CHILD),
        v2(22.1, "session.tool.success", sessionID=V2_SESSION,
           assistantMessageID="msg_a1", id=_V2_CALL, executed=True),
        v2_end(24.0),
        frame(1.0, pane("busy", v2=True)),
        frame(24.0, pane("idle_after_response", v2=True)),
    ],
    expect=[(1.0, 23.75, GREEN), (26.0, 30.0, RED)],
)

# session.execution.interrupted is in the live v2 turn vocabulary; the
# pane's "· interrupted" pill is from interrupted.txt.
V2_INTERRUPT = Scenario(
    "oc2_interrupt",
    backend="opencode2",
    initial_pane=pane("idle_after_response", v2=True),
    steps=[
        v2(0.0, "session.created", sessionID=V2_SESSION, agent="build"),
        *v2_prompt(1.0),
        v2_end(4.0, "interrupted"),
        frame(1.0, pane("busy", v2=True)),
        frame(4.0, pane("interrupted", v2=True)),
    ],
    expect=[(1.0, 3.75, GREEN), (4.25, 15.0, RED)],
)

# UNVERIFIED: session.execution.failed was never observed live (the plugin
# handles it defensively). What v2 emits for error_api_key.txt's provider
# 404 is unknown — if it is execution.succeeded, the colour is still red
# but the badge says awaiting_input, not error.
V2_EXECUTION_FAILED = Scenario(
    "oc2_execution_failed",
    backend="opencode2",
    initial_pane=pane("idle_after_response", v2=True),
    steps=[
        v2(0.0, "session.created", sessionID=V2_SESSION, agent="build"),
        *v2_prompt(1.0),
        v2(1.5, "session.execution.failed", sessionID=V2_SESSION,
           error={"message": "Provider request failed with HTTP 404"}),
        frame(1.0, pane("busy", v2=True)),
        frame(1.5, pane("error_api_key", v2=True)),
    ],
    expect=[(1.0, 1.25, GREEN), (1.75, 15.0, RED)],
)

V2_CHILD_REPORTS_BACK = Scenario(
    "oc2_child_reports_back",
    backend="opencode2",
    child=True,
    initial_pane=pane("idle_after_response", v2=True),
    steps=[
        v2(0.0, "session.created", sessionID=V2_SESSION, agent="build"),
        *v2_prompt(1.0),
        *v2_shell(2.0, 4.0),
        v2_end(6.0),
    ],
    expect=[(1.0, 5.75, GREEN), (8.0, 15.0, ORANGE)],
)

V2_PLUGIN_MISSING_HOOKS_FLEET = Scenario(
    "oc2_plugin_missing_hooks_fleet",
    backend="opencode2",
    fleet_mode="hooks",
    initial_pane=pane("idle_fresh", v2=True),
    steps=[frame(2.0, pane("busy", v2=True)), frame(10.0, pane("idle_after_response", v2=True))],
    expect=[(0.0, 1.75, RED), (2.25, 9.75, GREEN), (10.25, 20.0, RED)],
)



SCENARIOS = [
    PLAIN_TURN,
    FRESH_LAUNCH_TEXT_TURN,
    QUICK_REPLY,
    PERMISSION_APPROVED,
    PERMISSION_DENIED,
    SUBAGENT_FOREGROUND,
    SUBAGENT_PERMISSION,
    INTERRUPT_THEN_REPROMPT,
    PROVIDER_ERROR,
    NEW_SESSION_MID_RUN,
    QUEUED_PROMPTS,
    RESUMED,
    CHILD_REPORTS_BACK,
    CHILD_INTERRUPTED,
    PLUGIN_MISSING_POLLING_FLEET,
    PLUGIN_MISSING_HOOKS_FLEET,
    V2_PLAIN_TURN,
    V2_QUICK_REPLY,
    V2_PERMISSION_APPROVED,
    V2_PERMISSION_DENIED,
    V2_SUBAGENT_FOREGROUND,
    V2_INTERRUPT,
    V2_EXECUTION_FAILED,
    V2_CHILD_REPORTS_BACK,
    V2_PLUGIN_MISSING_HOOKS_FLEET,
]


@pytest.mark.parametrize(
    "scenario", SCENARIOS,
    ids=lambda s: s.name if isinstance(s, Scenario) else None,
)
def test_scenario_shows_the_expected_colour(scenario, tmp_path):
    samples = replay(scenario, tmp_path)
    bad = mismatches(scenario, samples)
    assert not bad, f"{len(bad)} wrong samples, first: {bad[0]}\n{timeline(samples)}"


# ---------------------------------------------------------------------------
# What the colour alone doesn't show
# ---------------------------------------------------------------------------

def _final_state(tmp_path):
    path = tmp_path / TMUX_SESSION / f"hook_state_{AGENT}.json"
    return json.loads(path.read_text())


class TestBeyondColour:
    def test_a_plain_turn_changes_colour_once(self, tmp_path):
        assert colour_changes(replay(PLAIN_TURN, tmp_path)) == 1

    def test_the_provider_error_is_badged_as_an_error(self, tmp_path):
        samples = replay(PROVIDER_ERROR, tmp_path)
        late = [s for s in samples if s.t >= 4.0]
        assert late and all(s.badges == ("error",) for s in late), timeline(samples)

    def test_the_permission_badge_names_the_tool(self, tmp_path):
        samples = replay(PERMISSION_APPROVED, tmp_path)
        by_t = {s.t: s for s in samples}
        assert by_t[5.0].badges == ("permission",)
        assert by_t[14.0].badges == ("tool",)

    def test_new_session_switches_the_tracked_conversation(self, tmp_path):
        replay(NEW_SESSION_MID_RUN, tmp_path)
        state = _final_state(tmp_path)
        ids = state["agent_session_ids"]
        assert len(ids) == 2, ids
        assert state["agent_session_id"] == ids[-1]

    def test_a_subagent_never_joins_the_tracked_conversations(self, tmp_path):
        replay(SUBAGENT_FOREGROUND, tmp_path)
        state = _final_state(tmp_path)
        assert len(state["agent_session_ids"]) == 1, state

    def test_exit_reads_as_terminated(self, tmp_path):
        """/exit emits no bus event; the shell prompt in the pane is the signal."""
        scenario = Scenario(
            "oc_exit", backend="opencode", initial_pane=pane("idle_after_response"),
            steps=capture("read_tool") + [frame(8.0, pane("exited_shell"))],
            expect=[(0.0, 4.75, GREEN)], end=12.0,
        )
        samples = replay(scenario, tmp_path)
        assert all(s.status == "terminated" and s.colour is None
                   for s in samples if s.t >= 8.0), timeline(samples)
