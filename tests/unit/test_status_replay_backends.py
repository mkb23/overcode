"""Colour-level replay scenarios for codex, grok and hermes (#507).

The counterpart of ``test_status_replay.py`` (Claude) and
``test_status_replay_opencode.py`` for the three backends whose telemetry
reaches overcode through the hook handler. Each scenario is replayed by
``tests/status_replay.py``:

* **codex** — ``codex_ev`` payloads (snake_case, Claude-shaped, plus
  ``turn_id`` and codex's own ``Interrupt``/``SessionStart`` events), kept
  to the events codex is registered for (``CODEX_HOOK_EVENTS``).
* **grok** — ``grok_ev`` payloads in its camelCase dialect (``hookEventName``
  with snake_case values, ``promptId``, ``notificationType``, grok tool
  names), kept to its registrations (``GROK_HOOK_EVENTS`` plus the two
  Notification matchers). ``_apply_grok_semantics``, the stale-turn
  ``promptId`` check and ``_settle_idle_prompt`` all run for real.
* **hermes** — ``hermes`` steps are Hermes observer hooks with their
  kwargs, translated by the real bundled plugin and piped to the handler.

Pane frames are the verbatim captures in ``tests/fixtures_<backend>_panes``.
None of these backends has ``pane_confirms_idle`` on and the harness only
draws Claude's busy marker, so each frame carries its CLI's own busy
marker (codex "esc to interrupt", grok "[stop]"/"Esc:cancel", hermes
"Ctrl+C cancel") or none, exactly as captured.

A scenario marked ``xfail(strict=True)`` documents a known detection bug.

Colours follow the #507 model — what happens if you do nothing?
  green  working right now
  orange blocked on a quick yes/no
  red    stalled: nothing happens until you act
None of these CLIs reports background work, so there is no yellow.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.status_replay import (
    GREEN, ORANGE, RED,
    Scenario, codex_ev, colour_changes, frame, grok_ev, hermes, mismatches, replay,
    timeline,
)

TESTS = Path(__file__).parent.parent


def pane(backend: str, name: str) -> str:
    return (TESTS / f"fixtures_{backend}_panes" / f"{name}.txt").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# codex (CLI 0.150.1 panes)
# ---------------------------------------------------------------------------

def codex(name: str) -> str:
    return pane("codex", name)


def _codex_reprompted() -> str:
    """interrupted.txt after a new prompt: the marker stays in scrollback
    above the new turn's "• Working (… esc to interrupt)" line."""
    lines = codex("interrupted").split("\n")
    at = next(i for i, ln in enumerate(lines) if ln.startswith("› Ask Codex"))
    turn = ["› run just the unit tests", "", "• Working (2s • esc to interrupt)", "", ""]
    return "\n".join(lines[:at] + turn + lines[at:])


CODEX_PLAIN_TURN = Scenario(
    "codex_plain_turn",
    steps=[
        codex_ev(0.0, "SessionStart", source="startup"),
        frame(1.0, codex("busy")),
        codex_ev(1.0, "UserPromptSubmit", prompt="run the tests"),
        codex_ev(2.0, "PreToolUse", "Bash", "c1", command="make test"),
        codex_ev(5.0, "PostToolUse", "Bash", "c1", command="make test"),
        codex_ev(8.0, "Stop"),
        frame(8.0, codex("idle_after_response")),
    ],
    initial_pane=codex("idle_fresh"),
    expect=[(0.0, 0.75, RED), (1.0, 7.75, GREEN), (8.0, 20.0, RED)],
    backend="codex",
)

# Approved with "y": nothing fires until the tool's PostToolUse, so the
# dialog going away is what turns it green again.
CODEX_PERMISSION = Scenario(
    "codex_permission",
    steps=[
        frame(0.0, codex("busy")),
        codex_ev(0.0, "UserPromptSubmit", prompt="touch a file in home"),
        codex_ev(2.0, "PreToolUse", "Bash", "c1", command="touch ~/x.txt"),
        codex_ev(2.1, "PermissionRequest", "Bash", "c1", command="touch ~/x.txt"),
        frame(2.1, codex("permission_required")),
        frame(10.0, codex("busy")),
        codex_ev(11.0, "PostToolUse", "Bash", "c1", command="touch ~/x.txt"),
        codex_ev(13.0, "Stop"),
        frame(13.0, codex("idle_after_response")),
    ],
    initial_pane=codex("idle_after_response"),
    expect=[(0.0, 2.0, GREEN), (2.25, 9.75, ORANGE), (10.0, 12.75, GREEN), (13.0, 20.0, RED)],
    backend="codex",
)

# Esc on the dialog: codex prints its interrupt marker and fires Interrupt
CODEX_PERMISSION_REJECTED = Scenario(
    "codex_permission_rejected",
    steps=[
        frame(0.0, codex("busy")),
        codex_ev(0.0, "UserPromptSubmit", prompt="touch a file in home"),
        codex_ev(2.0, "PreToolUse", "Bash", "c1", command="touch ~/x.txt"),
        codex_ev(2.1, "PermissionRequest", "Bash", "c1", command="touch ~/x.txt"),
        frame(2.1, codex("permission_required")),
        frame(8.0, codex("interrupted")),
        codex_ev(8.0, "Interrupt"),
    ],
    initial_pane=codex("idle_after_response"),
    expect=[(0.0, 2.0, GREEN), (2.25, 7.75, ORANGE), (8.0, 20.0, RED)],
    backend="codex",
)

# Esc mid-tool: Interrupt ends the turn at once; the next prompt is green
# even though the marker is still on screen above it.
CODEX_INTERRUPT_THEN_REPROMPT = Scenario(
    "codex_interrupt_then_reprompt",
    steps=[
        frame(0.0, codex("busy")),
        codex_ev(0.0, "UserPromptSubmit", prompt="count slowly"),
        codex_ev(1.0, "PreToolUse", "Bash", "c1", command="sleep 300"),
        frame(5.0, codex("interrupted")),
        codex_ev(5.0, "Interrupt"),
        frame(20.0, _codex_reprompted()),
        codex_ev(20.0, "UserPromptSubmit", turn="turn-2", prompt="run just the unit tests"),
        codex_ev(25.0, "Stop", turn="turn-2"),
        frame(25.0, codex("idle_after_response")),
    ],
    initial_pane=codex("idle_after_response"),
    expect=[(0.0, 4.75, GREEN), (5.0, 19.75, RED), (20.0, 24.75, GREEN), (25.0, 30.0, RED)],
    backend="codex",
)


# ---------------------------------------------------------------------------
# grok (Grok Build 1.0.5 panes)
# ---------------------------------------------------------------------------

def grok(name: str) -> str:
    return pane("grok", name)


def _grok_reprompted() -> str:
    """interrupted.txt's "Turn cancelled" line in scrollback above a new,
    busy turn (busy.txt's spinner and Esc:cancel footer)."""
    busy = grok("busy").split("\n")
    at = next(i for i, ln in enumerate(busy) if "Waiting for response" in ln)
    return "\n".join(busy[:at] + ["     Turn cancelled by user in 4.3s."] + busy[at:])


GROK_PLAIN_TURN = Scenario(
    "grok_plain_turn",
    steps=[
        frame(0.0, grok("busy")),
        grok_ev(0.0, "user_prompt_submit", prompt_id="p1", prompt="run the tests"),
        grok_ev(1.0, "pre_tool_use", "run_terminal_command", "g1", prompt_id="p1",
                tool_input={"command": "make test"}),
        grok_ev(3.0, "post_tool_use", "run_terminal_command", "g1", prompt_id="p1",
                tool_input={"command": "make test"}, toolResult={"output": "ok"}),
        grok_ev(6.0, "stop", prompt_id="p1", reason="end_turn", stopHookActive=False,
                backgroundTasks=[], sessionCrons=[]),
        frame(6.0, grok("idle_after_response")),
        # Killing the session fires a second, teardown Stop: not a turn end
        grok_ev(15.0, "stop", reason="shutdown", stopHookActive=False),
    ],
    initial_pane=grok("idle_fresh"),
    expect=[(0.0, 5.75, GREEN), (6.0, 20.0, RED)],
    backend="grok",
)

# grok's dialog arrives as Notification(permission_prompt); approving with
# "2" sends nothing until the tool's PostToolUse.
GROK_PERMISSION = Scenario(
    "grok_permission",
    steps=[
        frame(0.0, grok("busy")),
        grok_ev(0.0, "user_prompt_submit", prompt_id="p1", prompt="echo hello"),
        grok_ev(1.0, "pre_tool_use", "run_terminal_command", "g1", prompt_id="p1",
                tool_input={"command": "echo hello"}),
        grok_ev(1.2, "notification", notificationType="permission_prompt",
                message="Tool permission requested", level="info"),
        frame(1.2, grok("permission_required")),
        frame(9.0, grok("busy")),
        grok_ev(10.0, "post_tool_use", "run_terminal_command", "g1", prompt_id="p1",
                tool_input={"command": "echo hello"}, toolResult={"output": "hello"}),
        grok_ev(12.0, "stop", prompt_id="p1", reason="end_turn"),
        frame(12.0, grok("idle_after_response")),
    ],
    initial_pane=grok("idle_after_response"),
    expect=[(0.0, 1.0, GREEN), (1.25, 8.75, ORANGE), (9.0, 11.75, GREEN), (12.0, 20.0, RED)],
    backend="grok",
)

# Esc: StopCancelled (read as a Stop) and "Turn cancelled by user" on screen
GROK_INTERRUPT_THEN_REPROMPT = Scenario(
    "grok_interrupt_then_reprompt",
    steps=[
        frame(0.0, grok("busy")),
        grok_ev(0.0, "user_prompt_submit", prompt_id="p1", prompt="write haikus"),
        grok_ev(1.0, "pre_tool_use", "run_terminal_command", "g1", prompt_id="p1",
                tool_input={"command": "sleep 300"}),
        grok_ev(4.0, "stop_cancelled", prompt_id="p1", reason="user_interrupt",
                cancelledBy="user", cancelTrigger="esc"),
        frame(4.0, grok("interrupted")),
        frame(15.0, _grok_reprompted()),
        grok_ev(15.0, "user_prompt_submit", prompt_id="p2", prompt="just one haiku"),
        grok_ev(18.0, "stop", prompt_id="p2", reason="end_turn"),
        frame(18.0, grok("idle_after_response")),
    ],
    initial_pane=grok("idle_after_response"),
    expect=[(0.0, 3.75, GREEN), (4.0, 14.75, RED), (15.0, 17.75, GREEN), (18.0, 25.0, RED)],
    backend="grok",
)

# A StopFailure for turn p1 is dispatched off grok's command loop and lands
# after p2's UserPromptSubmit: it must not settle p2.
GROK_STALE_STOP_FAILURE = Scenario(
    "grok_stale_stop_failure",
    steps=[
        frame(0.0, grok("busy")),
        grok_ev(0.0, "user_prompt_submit", prompt_id="p1", prompt="first"),
        grok_ev(4.0, "user_prompt_submit", prompt_id="p2", prompt="second"),
        grok_ev(4.5, "stop_failure", prompt_id="p1", error="rate_limit",
                errorDetails="429 from provider"),
        grok_ev(10.0, "stop", prompt_id="p2", reason="end_turn"),
        frame(10.0, grok("idle_after_response")),
    ],
    initial_pane=grok("idle_after_response"),
    expect=[(0.0, 9.75, GREEN), (10.0, 20.0, RED)],
    backend="grok",
)

# The same race for an Esc: p1's StopCancelled arrives after p2 began, with
# p1's "Turn cancelled" still in scrollback above p2's busy spinner.
GROK_STALE_STOP_CANCELLED = Scenario(
    "grok_stale_stop_cancelled",
    steps=[
        frame(0.0, grok("busy")),
        grok_ev(0.0, "user_prompt_submit", prompt_id="p1", prompt="first"),
        frame(3.0, _grok_reprompted()),
        grok_ev(3.0, "user_prompt_submit", prompt_id="p2", prompt="second"),
        grok_ev(3.5, "stop_cancelled", prompt_id="p1", reason="user_interrupt",
                cancelledBy="user", cancelTrigger="esc"),
        grok_ev(10.0, "stop", prompt_id="p2", reason="end_turn"),
        frame(10.0, grok("idle_after_response")),
    ],
    initial_pane=grok("idle_after_response"),
    expect=[(0.0, 9.75, GREEN), (10.0, 20.0, RED)],
    backend="grok",
)

# Left on the dialog, grok's idle_prompt backstop fires: it settles only a
# running turn, so the pending permission stays orange.
GROK_IDLE_PROMPT_DURING_PERMISSION = Scenario(
    "grok_idle_prompt_during_permission",
    steps=[
        frame(0.0, grok("busy")),
        grok_ev(0.0, "user_prompt_submit", prompt_id="p1", prompt="echo hello"),
        grok_ev(1.0, "pre_tool_use", "run_terminal_command", "g1", prompt_id="p1",
                tool_input={"command": "echo hello"}),
        grok_ev(1.2, "notification", notificationType="permission_prompt",
                message="Tool permission requested", level="info"),
        frame(1.2, grok("permission_required")),
        grok_ev(61.2, "notification", notificationType="idle_prompt",
                message="Session idle", level="info"),
        frame(90.0, grok("busy")),
        grok_ev(91.0, "post_tool_use", "run_terminal_command", "g1", prompt_id="p1",
                tool_input={"command": "echo hello"}),
        grok_ev(93.0, "stop", prompt_id="p1", reason="end_turn"),
        frame(93.0, grok("idle_after_response")),
    ],
    initial_pane=grok("idle_after_response"),
    expect=[(1.25, 89.75, ORANGE), (90.0, 92.75, GREEN), (93.0, 100.0, RED)],
    backend="grok",
)

# A turn that reports none of Stop/StopFailure/StopCancelled: idle_prompt
# is the backstop that settles it. (Until then grok, with no
# pane_confirms_idle, stays green: that stretch is left unchecked.)
GROK_IDLE_PROMPT_SETTLES_LOST_STOP = Scenario(
    "grok_idle_prompt_settles_lost_stop",
    steps=[
        frame(0.0, grok("busy")),
        grok_ev(0.0, "user_prompt_submit", prompt_id="p1", prompt="hi"),
        frame(2.0, grok("idle_after_response")),
        grok_ev(62.0, "notification", notificationType="idle_prompt",
                message="Session idle", level="info"),
    ],
    initial_pane=grok("idle_after_response"),
    expect=[(0.0, 1.75, GREEN), (62.0, 70.0, RED)],
    backend="grok",
)


# ---------------------------------------------------------------------------
# hermes (v0.21.3 panes; kwargs as pinned in test_hermes_plugin.py)
# ---------------------------------------------------------------------------

def herm(name: str) -> str:
    return pane("hermes", name)


HSID = "20260101_000000_abc123"
HTURN = f"{HSID}:{HSID}:00000001"


def hk(t: float, hook: str, **kwargs):
    """A Hermes hook with the base kwargs every observer call carries."""
    base = {"session_id": HSID, "platform": "cli",
            "telemetry_schema_version": "hermes.observer.v1"}
    return hermes(t, hook, **{**base, **kwargs})


def h_prompt(t: float, text: str = "go", turn: str = HTURN):
    return hk(t, "pre_llm_call", task_id=HSID, turn_id=turn, user_message=text,
              is_first_turn=False)


def h_tool(t: float, hook: str, command: str, call_id: str = "call_1", **extra):
    return hk(t, hook, tool_name="terminal", args={"command": command, "timeout": 180},
              tool_call_id=call_id, turn_id=HTURN, **extra)


def h_done(t: float):
    """A completed turn: post_llm_call, then the turn-scoped on_session_end."""
    return [
        hk(t, "post_llm_call", assistant_response="done"),
        hk(t + 0.05, "on_session_end", completed=True, failed=False, interrupted=False,
           turn_exit_reason="text_response(finish_reason=stop)"),
    ]


def h_approval(t: float, hook: str, command: str, surface: str = "cli", **extra):
    return hk(t, hook, command=command, description="recursive delete",
              pattern_key="recursive delete", session_key=HSID, surface=surface, **extra)


HERMES_PLAIN_TURN = Scenario(
    "hermes_plain_turn",
    steps=[
        hk(0.0, "on_session_start", model="gpt-test"),
        frame(1.0, herm("busy")),
        h_prompt(1.0),
        frame(2.0, herm("busy_tool")),
        h_tool(2.0, "pre_tool_call", "sleep 6 && echo done"),
        h_tool(8.0, "post_tool_call", "sleep 6 && echo done", status="ok", result="{}",
               duration_ms=6000),
        frame(8.0, herm("busy")),
        *h_done(10.0),
        frame(10.0, herm("idle_after_approval")),
    ],
    initial_pane=herm("idle_fresh"),
    expect=[(0.0, 0.75, RED), (1.0, 9.75, GREEN), (10.0, 20.0, RED)],
    backend="hermes",
)

# approvals.mode manual: pre_approval_request is the dialog, an "Allow once"
# reports the command as the running tool until post_tool_call.
HERMES_PERMISSION = Scenario(
    "hermes_permission",
    steps=[
        frame(0.0, herm("busy")),
        h_prompt(0.0, "clean up junkdir_a"),
        h_tool(1.0, "pre_tool_call", "rm -rf ./junkdir_a"),
        frame(1.0, herm("busy_tool")),
        h_approval(1.5, "pre_approval_request", "rm -rf ./junkdir_a"),
        frame(1.5, herm("permission_required")),
        h_approval(9.0, "post_approval_response", "rm -rf ./junkdir_a", choice="once"),
        frame(9.0, herm("busy_tool")),
        h_tool(10.0, "post_tool_call", "rm -rf ./junkdir_a", status="ok"),
        frame(10.0, herm("busy")),
        *h_done(12.0),
        frame(12.0, herm("idle_after_approval")),
    ],
    initial_pane=herm("idle_after_approval"),
    expect=[(0.0, 1.25, GREEN), (1.75, 8.75, ORANGE), (9.0, 11.75, GREEN), (12.0, 20.0, RED)],
    backend="hermes",
)

# Deny: the plugin drops the denial; post_tool_call (blocked) follows and
# the model carries on.
HERMES_PERMISSION_DENIED = Scenario(
    "hermes_permission_denied",
    steps=[
        frame(0.0, herm("busy")),
        h_prompt(0.0, "clean up junkdir_b"),
        h_tool(1.0, "pre_tool_call", "rm -rf ./junkdir_b"),
        h_approval(1.5, "pre_approval_request", "rm -rf ./junkdir_b"),
        frame(1.5, herm("permission_required")),
        h_approval(9.0, "post_approval_response", "rm -rf ./junkdir_b", choice="deny"),
        frame(9.0, herm("busy")),
        h_tool(9.05, "post_tool_call", "rm -rf ./junkdir_b", status="blocked"),
        *h_done(12.0),
        frame(12.0, herm("idle_after_deny")),
    ],
    initial_pane=herm("idle_after_approval"),
    expect=[(0.0, 1.25, GREEN), (1.75, 8.75, ORANGE), (9.0, 11.75, GREEN), (12.0, 20.0, RED)],
    backend="hermes",
)

# The default approvals.mode smart: the aux model pre-screens the command
# in a few seconds with no human; the plugin drops it, so no orange flash.
HERMES_SMART_APPROVAL = Scenario(
    "hermes_smart_approval",
    steps=[
        frame(0.0, herm("busy")),
        h_prompt(0.0, "clean up junkdir_a"),
        h_tool(1.0, "pre_tool_call", "rm -rf ./junkdir_a"),
        frame(1.0, herm("busy_tool")),
        h_approval(1.5, "pre_approval_request", "rm -rf ./junkdir_a", surface="smart"),
        h_approval(4.0, "post_approval_response", "rm -rf ./junkdir_a", surface="smart",
                   choice="smart_approve"),
        h_tool(6.0, "post_tool_call", "rm -rf ./junkdir_a", status="ok"),
        *h_done(8.0),
        frame(8.0, herm("idle_after_approval")),
    ],
    initial_pane=herm("idle_after_approval"),
    expect=[(0.0, 7.75, GREEN), (8.0, 15.0, RED)],
    backend="hermes",
)

# C-c while waiting on the model: on_session_end(interrupted) → Interrupt
HERMES_INTERRUPT_THEN_REPROMPT = Scenario(
    "hermes_interrupt_then_reprompt",
    steps=[
        frame(0.0, herm("busy")),
        h_prompt(0.0, "write an essay"),
        hk(3.0, "on_session_end", completed=False, failed=False, interrupted=True,
           turn_exit_reason="interrupted_by_user"),
        frame(3.0, herm("interrupted_api")),
        frame(15.0, herm("busy")),
        h_prompt(15.0, "a short one", turn=f"{HSID}:{HSID}:00000002"),
        *h_done(18.0),
        frame(18.0, herm("idle_after_approval")),
    ],
    initial_pane=herm("idle_after_approval"),
    expect=[(0.0, 2.75, GREEN), (3.0, 14.75, RED), (15.0, 17.75, GREEN), (18.0, 25.0, RED)],
    backend="hermes",
)

# C-c mid-tool: the tool exits 130 (no interrupt text on screen), then the
# turn ends interrupted.
HERMES_INTERRUPT_MID_TOOL = Scenario(
    "hermes_interrupt_mid_tool",
    steps=[
        frame(0.0, herm("busy")),
        h_prompt(0.0, "sleep a while"),
        h_tool(1.0, "pre_tool_call", "sleep 40 && echo late"),
        frame(1.0, herm("busy_tool")),
        h_tool(5.0, "post_tool_call", "sleep 40 && echo late", status="cancelled"),
        hk(5.05, "on_session_end", completed=False, failed=False, interrupted=True,
           turn_exit_reason="interrupted_by_user"),
        frame(5.0, herm("interrupted")),
    ],
    initial_pane=herm("idle_after_approval"),
    expect=[(0.0, 4.75, GREEN), (5.25, 20.0, RED)],
    backend="hermes",
)


SCENARIOS = [
    CODEX_PLAIN_TURN,
    CODEX_PERMISSION,
    CODEX_PERMISSION_REJECTED,
    CODEX_INTERRUPT_THEN_REPROMPT,
    GROK_PLAIN_TURN,
    GROK_PERMISSION,
    GROK_INTERRUPT_THEN_REPROMPT,
    GROK_STALE_STOP_FAILURE,
    GROK_STALE_STOP_CANCELLED,
    GROK_IDLE_PROMPT_DURING_PERMISSION,
    GROK_IDLE_PROMPT_SETTLES_LOST_STOP,
    HERMES_PLAIN_TURN,
    HERMES_PERMISSION,
    HERMES_PERMISSION_DENIED,
    HERMES_SMART_APPROVAL,
    HERMES_INTERRUPT_THEN_REPROMPT,
    HERMES_INTERRUPT_MID_TOOL,
]


@pytest.mark.parametrize(
    "scenario", SCENARIOS,
    ids=lambda s: s.name if isinstance(s, Scenario) else None,
)
def test_scenario_shows_the_expected_colour(scenario, tmp_path):
    samples = replay(scenario, tmp_path)
    bad = mismatches(scenario, samples)
    assert not bad, f"{len(bad)} wrong samples, first: {bad[0]}\n{timeline(samples)}"


@pytest.mark.parametrize(
    "scenario", [CODEX_PLAIN_TURN, GROK_PLAIN_TURN, HERMES_PLAIN_TURN],
    ids=lambda s: s.name,
)
def test_a_plain_turn_does_not_flicker(scenario, tmp_path):
    samples = [s for s in replay(scenario, tmp_path) if s.t >= 1.0]
    assert colour_changes(samples) == 1, timeline(samples)


def test_grok_stale_stop_failure_shows_no_error(tmp_path):
    """The dropped report leaves no error badge behind on the new turn."""
    samples = replay(GROK_STALE_STOP_FAILURE, tmp_path)
    assert all("error" not in s.badges for s in samples)


def test_grok_unregistered_notification_is_not_delivered(tmp_path):
    """grok has only two Notification registrations; others never fire the hook."""
    scenario = Scenario(
        "grok_other_notification",
        steps=[
            frame(0.0, grok("busy")),
            grok_ev(0.0, "user_prompt_submit", prompt_id="p1"),
            grok_ev(2.0, "notification", notificationType="task_complete"),
        ],
        initial_pane=grok("idle_after_response"),
        expect=[(0.0, 5.0, GREEN)],
        backend="grok",
    )
    assert mismatches(scenario, replay(scenario, tmp_path)) == []


def test_hermes_permission_shows_a_permission_badge(tmp_path):
    samples = replay(HERMES_PERMISSION, tmp_path)
    assert all(s.badges == ("permission",) for s in samples if 2.0 <= s.t <= 8.0)
