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
        # An Esc fires no hook; the status bar drops "esc to interrupt"
        frame(3.0, claude_pane(body="⏺ Bash(make test)\n  ⎿  Interrupted by user"), busy=False),
        ev(10.0, "UserPromptSubmit"),
        ev(11.0, "PreToolUse", "Read", "i2", file_path="Makefile"),
        ev(11.1, "PostToolUse", "Read", "i2", file_path="Makefile"),
        frame(11.2, claude_pane(
            body="⏺ Bash(make test)\n  ⎿  Interrupted by user\n\n❯ run just the unit tests\n\n⏺ Read(Makefile)")),
    ],
    expect=[(0.0, 2.75, GREEN), (3.25, 9.75, RED), (10.0, 20.0, GREEN)],
)


# The person picks "No" (Esc): Claude prints the interrupt marker and no hook
# fires. The dialog going away must not read as an approval.
PERMISSION_DENIED = Scenario(
    "permission_denied",
    steps=[
        ev(0.0, "UserPromptSubmit"),
        ev(1.0, "PreToolUse", "Bash", "p1", command="rm -rf build"),
        ev(1.1, "PermissionRequest", "Bash", "p1", command="rm -rf build"),
        frame(1.2, claude_pane(body="Do you want to proceed?\n❯ 1. Yes\n  2. No")),
        frame(4.0, claude_pane(
            body="⏺ Bash(rm -rf build)\n  ⎿  Interrupted · What should Claude do instead?"),
            busy=False),
    ],
    expect=[(1.25, 3.75, ORANGE), (4.0, 15.0, RED)],
)

# Two approvals in one turn: the second dialog must show orange again, even
# though the first was seen and answered — including the moment before it
# renders, when the pane still shows the first approved tool.
TWO_PERMISSION_PROMPTS = Scenario(
    "two_permission_prompts",
    steps=[
        ev(0.0, "UserPromptSubmit"),
        ev(1.0, "PreToolUse", "Bash", "p1", command="git push"),
        ev(1.1, "PermissionRequest", "Bash", "p1", command="git push"),
        frame(1.2, claude_pane(body="Do you want to proceed?\n❯ 1. Yes\n  2. No")),
        frame(3.0, claude_pane(body="⏺ Bash(git push)\n  ⎿  Running…")),
        ev(5.0, "PostToolUse", "Bash", "p1", command="git push"),
        ev(6.0, "PreToolUse", "Bash", "p2", command="gh pr create"),
        ev(6.1, "PermissionRequest", "Bash", "p2", command="gh pr create"),
        frame(6.6, claude_pane(body="Do you want to proceed?\n❯ 1. Yes\n  2. No")),
        frame(9.0, claude_pane(body="⏺ Bash(gh pr create)\n  ⎿  Running…")),
        ev(12.0, "PostToolUse", "Bash", "p2", command="gh pr create"),
        ev(13.0, "Stop"),
    ],
    expect=[
        (1.25, 2.75, ORANGE), (3.25, 5.75, GREEN), (6.25, 8.75, ORANGE),
        (9.25, 12.75, GREEN), (15.0, 18.0, RED),
    ],
)

# A dialog the patterns don't recognise (another wording, a restart mid-run)
# proves nothing either way, so the agent stays orange until PostToolUse.
UNRECOGNISED_PERMISSION_DIALOG = Scenario(
    "unrecognised_permission_dialog",
    steps=[
        ev(0.0, "UserPromptSubmit"),
        ev(1.0, "PreToolUse", "Bash", "p1", command="git push"),
        ev(1.1, "PermissionRequest", "Bash", "p1", command="git push"),
        frame(1.2, claude_pane(body="Run git push?  [y/N]")),
        frame(3.0, claude_pane(body="⏺ Bash(git push)\n  ⎿  Running…")),
        ev(9.0, "PostToolUse", "Bash", "p1", command="git push"),
    ],
    expect=[(1.25, 8.75, ORANGE), (9.0, 10.0, GREEN)],
)

# Covers are brief: past the hold, an unseen count reads as 0 again.
MONITOR_STATUS_BAR_COVERED_LONG = Scenario(
    "monitor_status_bar_covered_long",
    steps=MONITOR_ARMED.steps + [
        frame(10.0, "⏺ Done.\n\n  /model  Choose a model\n  /help   Show help"),
    ],
    expect=[(5.0, 39.75, YELLOW), (40.25, 50.0, RED)],
)

# A visible bar without the count means the stream really ended.
MONITOR_ENDS = Scenario(
    "monitor_ends",
    steps=MONITOR_ARMED.steps + [frame(10.0, claude_pane())],
    expect=[(5.0, 9.75, YELLOW), (10.0, 20.0, RED)],
)

BACKGROUND_SHELL_STATUS_BAR_COVERED = Scenario(
    "background_shell_status_bar_covered",
    steps=[
        ev(0.0, "UserPromptSubmit"),
        ev(1.0, "PreToolUse", "Bash", "b1", command="npm run build", run_in_background=True),
        ev(1.05, "PostToolUse", "Bash", "b1", command="npm run build", run_in_background=True),
        frame(1.1, claude_pane(footer="1 shell · ↓ to manage")),
        ev(2.0, "Stop"),
        frame(10.0, "⏺ Done.\n\n  /model  Choose a model\n  /help   Show help"),
        frame(12.0, claude_pane(footer="1 shell · ↓ to manage")),
    ],
    expect=[(4.0, 20.0, YELLOW)],
)

# A TUI started (t=0) while the marker is already on screen: the turn's
# events all predate it, so the marker still counts.
INTERRUPT_SEEN_AFTER_RESTART = Scenario(
    "interrupt_seen_after_restart",
    steps=[
        ev(-30.0, "UserPromptSubmit"),
        ev(-29.0, "PreToolUse", "Bash", "i1", command="make test"),
        frame(-28.0, claude_pane(body="⏺ Bash(make test)\n  ⎿  Interrupted by user"), busy=False),
        ev(12.0, "UserPromptSubmit"),
    ],
    initial_pane=claude_pane(body="⏺ Bash(make test)\n  ⎿  Interrupted by user"),
    expect=[(0.0, 11.75, RED), (12.0, 15.0, GREEN)],
)


# 2026-10-08: a prompt, then the turn ends with no Stop and no interrupt
# marker on screen (a slash command, an Esc whose marker scrolled away). The
# status bar loses "esc to interrupt"; after the idle wait the agent is red,
# not "Processing prompt" for a day and a half.
TURN_ENDS_WITHOUT_STOP = Scenario(
    "turn_ends_without_stop",
    steps=[
        ev(0.0, "UserPromptSubmit"),
        frame(2.0, claude_pane(body="⏺ Done."), busy=False),
    ],
    expect=[(0.0, 16.75, GREEN), (18.0, 40.0, RED)],
)

# The same, but Claude's idle_prompt Notification arrives (a minute after the
# prompt goes idle) and settles it as a Stop for good.
TURN_ENDS_WITHOUT_STOP_IDLE_PROMPT = Scenario(
    "turn_ends_without_stop_idle_prompt",
    steps=[
        ev(0.0, "UserPromptSubmit"),
        ev(1.0, "PreToolUse", "Bash", "t1", command="sleep 300"),
        frame(2.0, claude_pane(body="⏺ Bash(sleep 300)\n  ⎿  Interrupted"), busy=False),
        ev(62.0, "Notification", notification_type="idle_prompt"),
    ],
    expect=[(0.0, 16.75, GREEN), (18.0, 90.0, RED)],
)

# #536: Claude routes a question and a plan through PermissionRequest (seen
# in live hook logs). A question is red (it needs an answer), a plan orange.
ASK_USER_QUESTION = Scenario(
    "ask_user_question",
    steps=[
        ev(0.0, "UserPromptSubmit"),
        ev(1.0, "PreToolUse", "AskUserQuestion", "q1", questions=[{"question": "Which?"}]),
        ev(1.05, "PermissionRequest", "AskUserQuestion", "q1", questions=[{"question": "Which?"}]),
        frame(1.1, claude_pane(body="☐ Which?\n❯ 1. This\n  2. That"), busy=False),
        ev(20.0, "PostToolUse", "AskUserQuestion", "q1", questions=[{"question": "Which?"}]),
        frame(20.05, claude_pane(body="⏺ User answered")),
        ev(22.0, "Stop"),
    ],
    expect=[(0.0, 0.75, GREEN), (1.25, 19.75, RED), (20.25, 21.75, GREEN), (24.0, 30.0, RED)],
)

EXIT_PLAN_MODE = Scenario(
    "exit_plan_mode",
    steps=[
        ev(0.0, "UserPromptSubmit"),
        ev(1.0, "PreToolUse", "ExitPlanMode", "x1", plan="..."),
        ev(1.05, "PermissionRequest", "ExitPlanMode", "x1", plan="..."),
        frame(1.1, claude_pane(body="Would you like to proceed?\n❯ 1. Yes\n  2. No"), busy=False),
    ],
    expect=[(0.0, 0.75, GREEN), (1.25, 10.0, ORANGE)],
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
    MONITOR_STATUS_BAR_COVERED,
    SCHEDULE_WAKEUP,
    PERMISSION_PROMPT,
    CHILD_REPORTS_BACK,
    INTERRUPT_THEN_REPROMPT,
    PERMISSION_DENIED,
    TWO_PERMISSION_PROMPTS,
    UNRECOGNISED_PERMISSION_DIALOG,
    MONITOR_STATUS_BAR_COVERED_LONG,
    MONITOR_ENDS,
    BACKGROUND_SHELL_STATUS_BAR_COVERED,
    INTERRUPT_SEEN_AFTER_RESTART,
    TURN_ENDS_WITHOUT_STOP,
    TURN_ENDS_WITHOUT_STOP_IDLE_PROMPT,
    ASK_USER_QUESTION,
    EXIT_PLAN_MODE,
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



def test_question_and_plan_badges(tmp_path):
    """#536: the badges say which kind of wait it is."""
    q = replay(ASK_USER_QUESTION, tmp_path / "q")
    assert any(s.badges == ("ask_question",) for s in q if 2.0 <= s.t <= 19.0)
    p = replay(EXIT_PLAN_MODE, tmp_path / "p")
    assert all(s.badges == ("plan_approval",) for s in p if 2.0 <= s.t <= 10.0)
