"""Status replay scenarios (#507): what colour should a person see, and when?

Each scenario replays a Claude Code hook/pane timeline through the real hook
handler and detector (see ``tests/status_replay.py``) and checks the colour
over each stretch of time. A scenario marked ``xfail(strict=True)`` documents
a known detection bug: it fails today and starts passing (then XPASS-fails,
prompting removal of the mark) once the bug is fixed.

Colours follow the #507 model — what happens if you do nothing?
  green  working right now
  yellow idle at the prompt, but work in flight elsewhere will wake it
  orange blocked on a quick yes/no
  red    stalled: nothing happens until you act
"""

import pytest

from tests.status_replay import (
    GREEN, ORANGE, RED, YELLOW,
    Scenario, claude_pane, colour_changes, ev, frame, mismatches, replay, timeline,
)


def _bug(reason):
    return pytest.mark.xfail(strict=True, reason=reason)


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

PLAIN_TURN = Scenario(
    "plain_turn",
    steps=[
        ev(0.0, "UserPromptSubmit"),
        ev(1.0, "PreToolUse", "Read", "t1", file_path="src/app.py"),
        ev(1.1, "PostToolUse", "Read", "t1", file_path="src/app.py"),
        ev(2.0, "PreToolUse", "Bash", "t2", command="make test"),
        ev(6.0, "PostToolUse", "Bash", "t2", command="make test"),
        ev(8.0, "Stop"),
    ],
    expect=[(0.0, 7.75, GREEN), (8.0, 20.0, RED)],
)

QUICK_TEXT_REPLY = Scenario(
    "quick_text_reply",
    steps=[ev(0.0, "UserPromptSubmit"), ev(0.5, "Stop")],
    expect=[(0.0, 1.5, GREEN), (1.75, 10.0, RED)],
)

# Live shape (2.1.286): Pre/Post for a background Bash land 70 ms apart and
# nothing fires when the shell exits — completion arrives as a fresh
# UserPromptSubmit. Meanwhile the status bar reads "· 1 shell ·".
BACKGROUND_SHELL = Scenario(
    "background_shell",
    steps=[
        ev(0.0, "UserPromptSubmit"),
        ev(4.0, "PreToolUse", "Bash", "b1", command="npm run build", run_in_background=True),
        ev(4.07, "PostToolUse", "Bash", "b1", command="npm run build", run_in_background=True),
        frame(4.1, claude_pane(footer="1 shell · ↓ to manage")),
        ev(5.8, "Stop"),
        frame(64.0, claude_pane()),
        ev(64.1, "UserPromptSubmit"),
        ev(65.8, "Stop"),
    ],
    expect=[(0.0, 5.75, GREEN), (8.0, 63.75, YELLOW), (64.25, 65.75, GREEN), (68.0, 75.0, RED)],
)

# Live shape (2.1.286): a background Agent call carries no run_in_background
# flag; PostToolUse returns status "async_launched" at once. SubagentStart /
# SubagentStop bracket the subagent, whose own tool calls carry agent_id. The
# parent sits at "✻ Waiting for 1 background agent to finish", then a
# UserPromptSubmit delivers the result.
BACKGROUND_AGENT = Scenario(
    "background_agent",
    steps=[
        ev(0.0, "UserPromptSubmit"),
        ev(4.0, "PreToolUse", "Agent", "a1", description="Survey the API", prompt="..."),
        ev(4.03, "SubagentStart", agent_id="sub1"),
        ev(4.03, "PostToolUse", "Agent", "a1", description="Survey the API", prompt="..."),
        ev(5.0, "Stop"),
        frame(5.1, claude_pane(
            footer="← 1 agent · ↓ to manage",
            status_line="✻ Waiting for 1 background agent to finish")),
        ev(10.0, "PreToolUse", "Grep", "s1", agent_id="sub1", pattern="route"),
        ev(10.1, "PostToolUse", "Grep", "s1", agent_id="sub1", pattern="route"),
        ev(40.0, "SubagentStop", agent_id="sub1"),
        frame(40.0, claude_pane(footer="← 1 agent")),
        ev(40.05, "UserPromptSubmit"),
        ev(42.0, "Stop"),
    ],
    expect=[(0.0, 4.75, GREEN), (7.0, 39.75, YELLOW), (40.25, 41.75, GREEN), (44.0, 50.0, RED)],
)

# Claude Code before 2.1.286 said "N bashes" where it now says "N shells"
OLDER_BASHES_STATUS_BAR = Scenario(
    "older_bashes_status_bar",
    steps=[
        ev(0.0, "UserPromptSubmit"),
        ev(1.0, "PreToolUse", "Bash", "b1", command="npm run dev", run_in_background=True),
        ev(1.05, "PostToolUse", "Bash", "b1", command="npm run dev", run_in_background=True),
        ev(1.1, "PreToolUse", "Bash", "b2", command="npm run api", run_in_background=True),
        ev(1.15, "PostToolUse", "Bash", "b2", command="npm run api", run_in_background=True),
        frame(1.2, claude_pane(footer="2 bashes")),
        ev(2.0, "Stop"),
    ],
    expect=[(0.0, 1.75, GREEN), (4.0, 20.0, YELLOW)],
)

# The parent waits inside the Agent call; the subagent's tool calls must not
# replace the parent's activity with theirs.
FOREGROUND_AGENT = Scenario(
    "foreground_agent",
    steps=[
        ev(0.0, "UserPromptSubmit"),
        ev(1.0, "PreToolUse", "Agent", "a1", description="Read the docs", prompt="..."),
        ev(1.05, "SubagentStart", agent_id="sub1"),
        ev(3.0, "PreToolUse", "Read", "s1", agent_id="sub1", file_path="README.md"),
        ev(3.1, "PostToolUse", "Read", "s1", agent_id="sub1", file_path="README.md"),
        ev(20.0, "SubagentStop", agent_id="sub1"),
        ev(20.05, "PostToolUse", "Agent", "a1", description="Read the docs", prompt="..."),
        ev(22.0, "Stop"),
    ],
    expect=[(0.0, 21.75, GREEN), (24.0, 30.0, RED)],
)

# A background subagent asks permission after the parent's Stop. The prompt
# is answered in the parent's pane, so the parent shows orange; once the
# subagent moves on, the parent is back to waiting on it.
SUBAGENT_PERMISSION = Scenario(
    "subagent_permission",
    steps=[
        ev(0.0, "UserPromptSubmit"),
        ev(1.0, "PreToolUse", "Agent", "a1", description="Deploy", prompt="..."),
        ev(1.03, "SubagentStart", agent_id="sub1"),
        ev(1.05, "PostToolUse", "Agent", "a1", description="Deploy", prompt="..."),
        ev(2.0, "Stop"),
        ev(10.0, "PreToolUse", "Bash", "s1", agent_id="sub1", command="git push"),
        ev(10.1, "PermissionRequest", "Bash", "s1", agent_id="sub1", command="git push"),
        ev(15.0, "PostToolUse", "Bash", "s1", agent_id="sub1", command="git push"),
        ev(30.0, "SubagentStop", agent_id="sub1"),
        ev(30.05, "UserPromptSubmit"),
        ev(32.0, "Stop"),
    ],
    expect=[
        (0.0, 1.75, GREEN), (4.0, 9.75, YELLOW), (10.25, 14.75, ORANGE),
        (15.0, 29.75, YELLOW), (30.25, 31.75, GREEN), (34.0, 40.0, RED),
    ],
)

# A child agent that has stopped but still waits on its own background agent
# isn't ready to report back yet.
CHILD_WITH_BACKGROUND_AGENT = Scenario(
    "child_with_background_agent",
    steps=[
        ev(0.0, "UserPromptSubmit"),
        ev(1.0, "PreToolUse", "Agent", "a1", description="Benchmark", prompt="..."),
        ev(1.03, "SubagentStart", agent_id="sub1"),
        ev(1.05, "PostToolUse", "Agent", "a1", description="Benchmark", prompt="..."),
        ev(2.0, "Stop"),
        ev(20.0, "SubagentStop", agent_id="sub1"),
        ev(20.05, "UserPromptSubmit"),
        ev(22.0, "Stop"),
    ],
    expect=[(0.0, 2.5, GREEN), (2.75, 19.75, YELLOW), (24.0, 30.0, ORANGE)],
    child=True,
)

MONITOR_ARMED = Scenario(
    "monitor_armed",
    steps=[
        ev(0.0, "UserPromptSubmit"),
        ev(2.0, "PreToolUse", "Monitor", "m1", command="tail -f deploy.log"),
        ev(2.04, "PostToolUse", "Monitor", "m1", command="tail -f deploy.log"),
        frame(2.1, claude_pane(footer="1 monitor")),
        ev(3.0, "Stop"),
    ],
    expect=[(0.0, 2.75, GREEN), (5.0, 30.0, YELLOW)],
)

# A menu or dialog drawn over the status bar hides "1 monitor" for a moment;
# the monitor is still live.
MONITOR_STATUS_BAR_COVERED = Scenario(
    "monitor_status_bar_covered",
    steps=MONITOR_ARMED.steps + [
        frame(10.0, "⏺ Done.\n\n  /model  Choose a model\n  /help   Show help"),
        frame(10.5, claude_pane(footer="1 monitor")),
    ],
    expect=[(5.0, 30.0, YELLOW)],
)

SCHEDULE_WAKEUP = Scenario(
    "schedule_wakeup",
    steps=[
        ev(0.0, "UserPromptSubmit"),
        ev(2.0, "PreToolUse", "ScheduleWakeup", "w1", delaySeconds=60, reason="poll CI"),
        ev(2.05, "PostToolUse", "ScheduleWakeup", "w1", delaySeconds=60, reason="poll CI"),
        ev(3.0, "Stop"),
        ev(62.0, "UserPromptSubmit"),
        ev(64.0, "Stop"),
    ],
    expect=[(0.0, 2.75, GREEN), (5.0, 61.75, YELLOW), (62.0, 63.75, GREEN), (66.0, 70.0, RED)],
)

# The approval prompt is answered at t=3 and the push then runs for 6 s.
PERMISSION_PROMPT = Scenario(
    "permission_prompt",
    steps=[
        ev(0.0, "UserPromptSubmit"),
        ev(1.0, "PreToolUse", "Bash", "p1", command="git push"),
        ev(1.1, "PermissionRequest", "Bash", "p1", command="git push"),
        frame(1.2, claude_pane(body="Do you want to proceed?\n❯ 1. Yes\n  2. No")),
        frame(3.0, claude_pane(body="⏺ Bash(git push)\n  ⎿  Running…")),  # approved
        ev(9.0, "PostToolUse", "Bash", "p1", command="git push"),
        ev(10.0, "Stop"),
    ],
    expect=[(0.0, 1.0, GREEN), (1.25, 2.75, ORANGE), (3.25, 9.75, GREEN), (12.0, 15.0, RED)],
)

CHILD_REPORTS_BACK = Scenario(
    "child_reports_back",
    steps=[ev(0.0, "UserPromptSubmit"), ev(5.0, "Stop")],
    expect=[(0.0, 4.75, GREEN), (7.0, 15.0, ORANGE)],
    child=True,
)

# Escape mid-tool prints the marker and fires no hook; the person then types a
# new prompt. The old marker is still within the last 40 pane lines.
INTERRUPT_THEN_REPROMPT = Scenario(
    "interrupt_then_reprompt",
    steps=[
        ev(0.0, "UserPromptSubmit"),
        ev(1.0, "PreToolUse", "Bash", "i1", command="make test"),
        frame(3.0, claude_pane(body="⏺ Bash(make test)\n  ⎿  Interrupted by user")),
        ev(10.0, "UserPromptSubmit"),
        ev(11.0, "PreToolUse", "Read", "i2", file_path="Makefile"),
        ev(11.1, "PostToolUse", "Read", "i2", file_path="Makefile"),
        frame(11.2, claude_pane(
            body="⏺ Bash(make test)\n  ⎿  Interrupted by user\n\n❯ run just the unit tests\n\n⏺ Read(Makefile)")),
    ],
    expect=[(0.0, 2.75, GREEN), (3.25, 9.75, RED), (10.0, 20.0, GREEN)],
)


SCENARIOS = [
    PLAIN_TURN,
    QUICK_TEXT_REPLY,
    BACKGROUND_SHELL,
    OLDER_BASHES_STATUS_BAR,
    BACKGROUND_AGENT,
    FOREGROUND_AGENT,
    SUBAGENT_PERMISSION,
    CHILD_WITH_BACKGROUND_AGENT,
    MONITOR_ARMED,
    pytest.param(MONITOR_STATUS_BAR_COVERED, marks=_bug(
        "#507: Monitor's obligation is disarmed by PostToolUse, so the status "
        "bar count is the only signal and a covered bar drops to red")),
    SCHEDULE_WAKEUP,
    pytest.param(PERMISSION_PROMPT, marks=_bug(
        "#507: nothing fires on approval, so orange lasts until PostToolUse "
        "while the approved tool runs")),
    CHILD_REPORTS_BACK,
    pytest.param(INTERRUPT_THEN_REPROMPT, marks=_bug(
        "#507: the interrupt marker in the last 40 lines keeps a re-prompted, "
        "working agent red")),
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
# Harness behaviour
# ---------------------------------------------------------------------------

class TestHarness:
    def test_unregistered_events_never_reach_overcode(self, tmp_path):
        """SubagentStop alone must not change anything while it is unregistered."""
        scenario = Scenario(
            "unregistered",
            steps=[ev(0.0, "UserPromptSubmit"), ev(5.0, "SubagentStop", agent_id="x")],
            expect=[(0.0, 8.0, GREEN)],
        )
        assert mismatches(scenario, replay(scenario, tmp_path)) == []

    def test_the_sticky_green_window_is_measured_on_the_fake_clock(self, tmp_path):
        samples = replay(QUICK_TEXT_REPLY, tmp_path)
        by_t = {s.t: s.colour for s in samples}
        assert by_t[1.5] == GREEN  # 1.5 s after the last working event
        assert by_t[1.75] == RED

    def test_a_plain_turn_changes_colour_once(self, tmp_path):
        assert colour_changes(replay(PLAIN_TURN, tmp_path)) == 1

    def test_timeline_summarises_runs(self, tmp_path):
        text = timeline(replay(QUICK_TEXT_REPLY, tmp_path))
        assert text.startswith(f"{GREEN} 0-")
        assert f"| {RED} " in text
