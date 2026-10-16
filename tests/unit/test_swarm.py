"""Work a Claude agent delegates in its session: the 👥 rollup (0.6.1, phase 1).

docs/design/agent-observability-0.6.1.md. The hook side folds subagent,
workflow and teammate events into the parent's hook state; the daemon
publishes the counts (``SessionDaemonState.swarm_rollup``); the parent stays
yellow while ``background_tasks`` (Stop / SubagentStop) still lists work in
flight. The capture replays run the verbatim hook payloads from the
2.1.300 spike (``docs/design/spike-agent-types/captures``) through the real
hook handler and detector.
"""

from datetime import datetime

import pytest

from overcode import swarm
from overcode.episodes import EpisodeRecorder
from overcode.hook_handler import _obligation_kind, _update_obligations
from tests.status_replay import (
    GREEN, ORANGE, RED, YELLOW,
    Scenario, capture_steps, ev, mismatches, replay, timeline,
)

pytestmark = pytest.mark.unit


def _rollup(**counts):
    base = {"total": 0, "running": 0, "waiting": 0, "idle": 0, "ok": 0, "failed": 0,
            "workflows": 0, "workflows_running": 0}
    base.update(counts)
    return base


def _parent(subs, sw, event, tool=None, tool_input=None, now=0.0, **data):
    return swarm.apply_parent_event(subs, sw, event, tool, tool_input, data, now)


def _sub(subs, sw, event, agent_id, now=0.0, **data):
    return swarm.apply_subagent_event(subs, sw, event, agent_id, data, now)


def _state(subs, sw):
    return {"event": "Stop", "timestamp": 0.0, "subagents": subs, "swarm": sw}


# ---------------------------------------------------------------------------
# Hook side
# ---------------------------------------------------------------------------

class TestKinds:
    @pytest.mark.parametrize("agent_id, agent_type, kind", [
        ("aa583020cc2dd031e", "general-purpose", "subagent"),
        ("a38c743f5b2753cd7", "fork", "fork"),
        ("afc08e0045116b938", "workflow-subagent", "workflow"),
        ("aalice-acd486ba2d22120c", "alice", "teammate"),
        # a dash alone is not a teammate: the name must be its agent_type
        ("aalice-acd486ba2d22120c", "general-purpose", "subagent"),
    ])
    def test_from_the_ids_and_types_claude_sends(self, agent_id, agent_type, kind):
        assert swarm.agent_kind(agent_id, agent_type, {}) == kind

    def test_a_spawned_teammate_is_known_by_its_id(self):
        sw = {"teammates": {"x1": {"name": "carol"}}}
        assert swarm.agent_kind("x1", "general-purpose", sw) == "teammate"


class TestTaskNotifications:
    def test_reads_each_notification(self):
        prompt = ("<task-notification>\n<task-id>aa1</task-id>\n<status>completed</status>\n"
                  "</task-notification>\n<task-notification><task-id>w0dq</task-id>"
                  "<status>failed</status></task-notification>")
        assert swarm.task_notifications(prompt) == [("aa1", "completed", False),
                                                    ("w0dq", "failed", False)]

    def test_a_cut_off_prompt_still_reads(self):
        prompt = ("<task-notification>\n<task-id>aa1</task-id>\n<tool-use-id>t</tool-use-id>"
                  "\n<status>completed</status>\n<summary>Agent \"a...")
        assert swarm.task_notifications(prompt) == [("aa1", "completed", False)]

    def test_an_agent_waiting_on_its_own_work_is_interim(self):
        prompt = ("<task-notification><task-id>aa1</task-id><status>completed</status>"
                  "<summary>Agent \"Sleep 50 seconds\" is waiting on its own background "
                  "work</summary></task-notification>")
        assert swarm.task_notifications(prompt) == [("aa1", "completed", True)]

    @pytest.mark.parametrize("prompt", [None, "", "fix the tests", 42])
    def test_anything_else_is_none(self, prompt):
        assert swarm.task_notifications(prompt) == []


class TestLifecycle:
    def test_start_then_stop_counts_one_finished(self):
        subs, sw = _sub({}, {}, "SubagentStart", "a1", 1.0, agent_type="Explore")
        assert swarm.rollup(_state(subs, sw), 1.0) == _rollup(total=1, running=1)
        subs, sw = _sub(subs, sw, "SubagentStop", "a1", 2.0)
        assert swarm.rollup(_state(subs, sw), 2.0) == _rollup(total=1, ok=1)

    def test_a_resumed_agent_counts_once(self):
        """SendMessage to a finished agent starts it again (F17)."""
        subs, sw = _sub({}, {}, "SubagentStart", "a1")
        subs, sw = _sub(subs, sw, "SubagentStop", "a1")
        subs, sw = _sub(subs, sw, "SubagentStart", "a1", 5.0)
        assert swarm.rollup(_state(subs, sw), 5.0) == _rollup(total=1, running=1)

    def test_a_permission_prompt_is_waiting_until_its_next_event(self):
        subs, sw = _sub({}, {}, "SubagentStart", "a1")
        subs, sw = _sub(subs, sw, "PermissionRequest", "a1", tool_name="Bash")
        assert swarm.rollup(_state(subs, sw), 1.0) == _rollup(total=1, waiting=1)
        subs, sw = _sub(subs, sw, "PostToolUse", "a1", tool_name="Bash")
        assert swarm.rollup(_state(subs, sw), 1.0) == _rollup(total=1, running=1)

    def test_a_failed_notification_marks_it_failed(self):
        subs, sw = _sub({}, {}, "SubagentStart", "a1")
        subs, sw = _sub(subs, sw, "SubagentStop", "a1")
        prompt = "<task-notification><task-id>a1</task-id><status>killed</status>"
        subs, sw = _parent(subs, sw, "UserPromptSubmit", prompt=prompt)
        assert swarm.rollup(_state(subs, sw), 1.0) == _rollup(total=1, failed=1)

    def test_a_notification_for_a_live_agent_finishes_it(self):
        """Its SubagentStop was lost; the notification still lands."""
        subs, sw = _sub({}, {}, "SubagentStart", "a1")
        prompt = "<task-notification><task-id>a1</task-id><status>completed</status>"
        subs, sw = _parent(subs, sw, "UserPromptSubmit", prompt=prompt)
        assert swarm.rollup(_state(subs, sw), 1.0) == _rollup(total=1, ok=1)

    def test_a_foreground_call_s_response_says_how_it_went(self):
        subs, sw = _sub({}, {}, "SubagentStart", "a1")
        subs, sw = _sub(subs, sw, "SubagentStop", "a1")
        subs, sw = _parent(subs, sw, "PostToolUse", "Agent", {"run_in_background": False},
                           tool_response={"status": "failed", "agentId": "a1"})
        assert swarm.rollup(_state(subs, sw), 1.0) == _rollup(total=1, failed=1)

    def test_session_end_drops_it_all(self):
        subs, sw = _sub({}, {}, "SubagentStart", "a1")
        assert _parent(subs, sw, "SessionEnd") == ({}, {})

    def test_done_agents_past_the_cap_fold_into_counts(self):
        subs, sw = {}, {}
        n = swarm.DONE_KEEP + 25
        for i in range(n):
            subs, sw = _sub(subs, sw, "SubagentStart", f"a{i}")
            subs, sw = _sub(subs, sw, "SubagentStop", f"a{i}")
        assert len(sw["done"]) == swarm.DONE_KEEP
        assert swarm.rollup(_state(subs, sw), 1.0) == _rollup(total=n, ok=n)

    def test_a_stop_never_seen_starting_is_one_of_claude_s_own_helpers(self):
        """The away summary and auto-compaction fire SubagentStop only (seen live)."""
        subs, sw = _sub({}, {}, "SubagentStop", "af208a238f5e49046", 2.0)
        assert swarm.rollup(_state(subs, sw), 2.0) is None

    def test_a_background_agent_whose_start_was_lost_still_counts(self):
        subs, sw = _parent({}, {}, "PostToolUse", "Agent", {},
                           tool_response={"status": "async_launched", "agentId": "a9"})
        subs, sw = _sub(subs, sw, "SubagentStop", "a9", 2.0)
        assert swarm.rollup(_state(subs, sw), 2.0) == _rollup(total=1, ok=1)

    def test_never_delegated_is_none(self):
        assert swarm.rollup({"event": "Stop"}, 1.0) is None
        assert swarm.rollup(None, 1.0) is None


class TestReconcile:
    """``background_tasks`` on Stop / SubagentStop is Claude's own in-flight list."""

    def test_the_parent_s_stop_ends_what_the_list_leaves_out(self):
        subs, sw = _sub({}, {}, "SubagentStart", "a1")
        subs, sw = _sub(subs, sw, "SubagentStart", "a2")
        subs, sw = _parent(subs, sw, "Stop", now=3.0, background_tasks=[
            {"id": "a2", "type": "subagent", "status": "running"}])
        assert set(subs) == {"a2"}
        assert swarm.rollup(_state(subs, sw), 3.0) == _rollup(total=2, running=1, ok=1)

    def test_a_stopped_agent_still_listed_is_running_again(self):
        """Seen live: a background subagent backgrounds its own shell and ends
        its turn (SubagentStop), and Claude keeps listing it until the shell ends."""
        subs, sw = _parent({}, {}, "PostToolUse", "Agent", {},
                           tool_response={"status": "async_launched", "agentId": "a1"})
        subs, sw = _sub(subs, sw, "SubagentStart", "a1", 1.0)
        subs, sw = _sub(subs, sw, "SubagentStop", "a1", 2.0, background_tasks=[
            {"id": "a1", "type": "subagent", "status": "running"}])
        subs, sw = _parent(subs, sw, "Stop", now=3.0, background_tasks=[
            {"id": "a1", "type": "subagent", "status": "running"}])
        assert swarm.rollup(_state(subs, sw), 3.0) == _rollup(total=1, running=1)
        subs, sw = _parent(subs, sw, "Stop", now=50.0, background_tasks=[])
        assert swarm.rollup(_state(subs, sw), 50.0) == _rollup(total=1, ok=1)

    def test_an_interim_notification_keeps_it_running_while_shells_run(self):
        note = ("<note>This agent stopped with background work of its own still "
                "running.</note>")
        subs, sw = _parent({}, {}, "PostToolUse", "Agent", {},
                           tool_response={"status": "async_launched", "agentId": "a1"})
        subs, sw = _sub(subs, sw, "SubagentStart", "a1", 1.0)
        subs, sw = _sub(subs, sw, "SubagentStop", "a1", 2.0, background_tasks=[
            {"id": "a1", "type": "subagent", "status": "running"},
            {"id": "b1", "type": "shell", "status": "running"}])
        subs, sw = _parent(subs, sw, "UserPromptSubmit", now=2.5, prompt=(
            f"<task-notification><task-id>a1</task-id><status>completed</status>{note}"))
        subs, sw = _parent(subs, sw, "Stop", now=3.0, background_tasks=[
            {"id": "b1", "type": "shell", "status": "running"}])
        assert swarm.rollup(_state(subs, sw), 3.0) == _rollup(total=1, running=1)
        assert swarm.in_flight(_state(subs, sw), 3.0) == (1, 0)
        # No shell left: whatever it waited on is over
        subs, sw = _parent(subs, sw, "Stop", now=60.0, background_tasks=[])
        assert swarm.rollup(_state(subs, sw), 60.0) == _rollup(total=1, ok=1)

    def test_an_interim_notification_for_an_unknown_task_adds_nothing(self):
        prompt = ("<task-notification><task-id>zz</task-id><status>completed</status>"
                  "<summary>Agent \"x\" is waiting on its own background work</summary>")
        subs, sw = _parent({}, {}, "UserPromptSubmit", prompt=prompt)
        assert swarm.rollup(_state(subs, sw), 1.0) is None

    def test_another_agent_s_stop_leaves_a_foreground_one_alone(self):
        """Two foreground agents in parallel (07): the first one's
        SubagentStop lists neither, and the second is still working."""
        subs, sw = _sub({}, {}, "SubagentStart", "a1")
        subs, sw = _sub(subs, sw, "SubagentStart", "a2")
        subs, sw = _sub(subs, sw, "SubagentStop", "a1", background_tasks=[])
        assert set(subs) == {"a2"}

    def test_another_agent_s_stop_ends_a_background_one_it_leaves_out(self):
        subs, sw = _sub({}, {}, "SubagentStart", "a1")
        subs, sw = _parent(subs, sw, "PostToolUse", "Agent", {},
                           tool_response={"status": "async_launched", "agentId": "a1"})
        subs, sw = _sub(subs, sw, "SubagentStart", "a2")
        subs, sw = _sub(subs, sw, "SubagentStop", "a2", background_tasks=[
            {"id": "a2", "type": "subagent", "status": "running"}])
        assert subs == {}

    def test_work_never_seen_starting_is_added(self):
        subs, sw = _parent({}, {}, "Stop", background_tasks=[
            {"id": "a9", "type": "subagent", "status": "running", "agent_type": "Explore"}])
        assert subs["a9"]["kind"] == "subagent"

    def test_shells_are_not_delegated_agents(self):
        subs, sw = _parent({}, {}, "Stop", background_tasks=[
            {"id": "b1", "type": "shell", "status": "running", "command": "sleep 9"}])
        assert subs == {} and swarm.in_flight(_state(subs, sw), 1.0) == (0, 0)

    def test_workflow_runs_end_when_the_list_drops_them(self):
        subs, sw = _parent({}, {}, "PostToolUse", "Workflow", {}, tool_response={
            "status": "async_launched", "taskId": "w1", "workflowName": "review",
            "runId": "wf_1"})
        subs, sw = _sub(subs, sw, "SubagentStart", "a1", agent_type="workflow-subagent")
        wf = [{"id": "w1", "type": "workflow", "status": "running", "name": "review"}]
        subs, sw = _parent(subs, sw, "Stop", background_tasks=wf)
        assert swarm.in_flight(_state(subs, sw), 1.0) == (0, 1)  # the run counts once
        subs, sw = _parent(subs, sw, "Stop", background_tasks=[])
        assert swarm.rollup(_state(subs, sw), 1.0) == _rollup(total=1, ok=1, workflows=1)

    def test_teammates_end_when_their_task_leaves_the_list(self):
        subs, sw = {}, {}
        for name, aid in (("alice", "aalice-0123456789ab"), ("bob", "abob-0123456789ab")):
            subs, sw = _sub(subs, sw, "SubagentStart", aid, agent_type=name)
            subs, sw = _parent(subs, sw, "PostToolUse", "Agent", {"name": name,
                               "description": f"{name} task"},
                               tool_response={"status": "teammate_spawned", "agentId": aid,
                                              "name": name})
        tasks = [{"id": "t1", "type": "teammate", "status": "running",
                  "description": "alice task"},
                 {"id": "t2", "type": "teammate", "status": "running",
                  "description": "bob task"}]
        # A teammate's SubagentStop is it going idle, not finishing
        subs, sw = _sub(subs, sw, "SubagentStop", "aalice-0123456789ab", background_tasks=tasks)
        assert swarm.rollup(_state(subs, sw), 1.0) == _rollup(total=2, running=1, idle=1)
        assert swarm.in_flight(_state(subs, sw), 1.0) == (1, 0)
        subs, sw = _sub(subs, sw, "SubagentStop", "abob-0123456789ab",
                        background_tasks=tasks[1:])
        assert swarm.rollup(_state(subs, sw), 1.0) == _rollup(total=2, idle=1, ok=1)
        assert swarm.in_flight(_state(subs, sw), 1.0) == (0, 0)


class TestNoTimerOnceTheListIsKnown:
    def test_a_long_quiet_agent_stays_in_flight(self):
        """A background agent with nothing to say for two hours is still
        running while the list says so (the 1 h timer is for old state)."""
        subs, sw = _sub({}, {}, "SubagentStart", "a1", 0.0)
        subs, sw = _parent(subs, sw, "Stop", now=1.0, background_tasks=[
            {"id": "a1", "type": "subagent", "status": "running"}])
        assert swarm.in_flight(_state(subs, sw), 7200.0) == (1, 0)

    def test_old_state_still_ages_out(self):
        state = {"subagents": {"a1": {"last_seen": 0.0}}}
        assert swarm.in_flight(state, 100.0) == (1, 0)
        assert swarm.in_flight(state, 4000.0) == (0, 0)
        assert swarm.rollup(state, 4000.0) == _rollup(total=1, ok=1)


class TestRollupHoldsStill:
    def test_the_counts_do_not_move_with_the_clock(self):
        subs, sw = _sub({}, {}, "SubagentStart", "a1", 0.0)
        subs, sw = _sub(subs, sw, "SubagentStart", "a2", 0.0)
        subs, sw = _sub(subs, sw, "SubagentStop", "a2", 1.0)
        subs, sw = _parent(subs, sw, "Stop", now=2.0, background_tasks=[
            {"id": "a1", "type": "subagent", "status": "running"}])
        state = _state(subs, sw)
        values = {repr(swarm.rollup(state, t)) for t in (2.0, 30.0, 3600.0, 86400.0)}
        assert len(values) == 1


class TestBackgroundObligation:
    """F18: Agent is background unless told otherwise; Workflow always is."""

    @pytest.mark.parametrize("tool, tool_input, kind", [
        ("Agent", {"prompt": "x"}, "bg_task"),
        ("Agent", {"prompt": "x", "run_in_background": True}, "bg_task"),
        ("Agent", {"prompt": "x", "run_in_background": False}, None),
        ("Task", {"prompt": "x"}, "bg_task"),
        ("Workflow", {"script": "..."}, "bg_task"),
        ("Bash", {"command": "make"}, None),
        ("Bash", {"command": "make", "run_in_background": True}, "bg_task"),
    ])
    def test_arming(self, tool, tool_input, kind):
        assert _obligation_kind(tool, tool_input) == kind

    def test_the_launch_hands_over_to_the_swarm(self):
        armed = _update_obligations([], "PreToolUse", "Agent", {"prompt": "x"}, "t1", 1.0)
        assert [o["kind"] for o in armed] == ["bg_task"]
        assert _update_obligations(armed, "PostToolUse", "Agent", {"prompt": "x"}, "t1",
                                   1.1) == []


# ---------------------------------------------------------------------------
# The spike captures, replayed
# ---------------------------------------------------------------------------

def _at(samples, t):
    return next(s for s in samples if s.t >= t)


def _capture(name, expect, end):
    return Scenario(name, steps=capture_steps(name), expect=expect, end=end)


def _check(scenario, samples):
    assert mismatches(scenario, samples) == [], timeline(samples)


class TestCaptures:
    def test_a_default_agent_call_is_counted(self, tmp_path):
        sc = _capture("01-default-agent-call", [(0.5, 5.5, GREEN), (5.75, 7.0, RED)], 7.0)
        samples = replay(sc, tmp_path)
        _check(sc, samples)
        assert _at(samples, 2.25).rollup == _rollup(total=1, running=1)
        assert _at(samples, 5.5).rollup == _rollup(total=1, ok=1)

    def test_a_foreground_agent_never_leaves_the_parent_waiting(self, tmp_path):
        sc = _capture("02-foreground-subagent", [(1.0, 5.75, GREEN), (6.0, 7.0, RED)], 7.0)
        samples = replay(sc, tmp_path)
        _check(sc, samples)
        assert YELLOW not in {s.colour for s in samples}
        assert _at(samples, 5.75).rollup == _rollup(total=1, ok=1)

    def test_a_background_agent_holds_the_parent_yellow_until_it_ends(self, tmp_path):
        """Stop lists the agent: yellow. It ends, the notification turn
        runs, and the next Stop lists only a shell (left to the pane)."""
        sc = _capture("03-background-subagent-with-SendMessage",
                      [(6.75, 10.5, YELLOW), (11.0, 12.75, GREEN), (13.25, 18.0, RED)], 18.0)
        samples = replay(sc, tmp_path)
        _check(sc, samples)
        assert _at(samples, 8.0).badges == ("subagent",)
        assert _at(samples, 8.0).rollup == _rollup(total=1, running=1)
        assert _at(samples, 17.0).rollup == _rollup(total=1, ok=1)

    def test_a_fork_is_a_background_agent(self, tmp_path):
        sc = _capture("04-fork", [(3.75, 4.75, YELLOW), (6.5, 11.0, RED)], 11.0)
        samples = replay(sc, tmp_path)
        _check(sc, samples)
        assert _at(samples, 6.25).rollup == _rollup(total=1, ok=1)

    def test_a_workflow_holds_the_parent_yellow_between_its_agents(self, tmp_path):
        sc = _capture("05-workflow-small",
                      [(6.5, 8.75, YELLOW), (9.0, 10.0, GREEN)], 10.0)
        samples = replay(sc, tmp_path)
        _check(sc, samples)
        assert _at(samples, 7.0).rollup == _rollup(total=2, running=1, ok=1, workflows=1,
                                                   workflows_running=1)
        assert _at(samples, 10.0).rollup == _rollup(total=3, ok=3, workflows=1)

    def test_a_slow_workflow_counts_every_agent(self, tmp_path):
        sc = _capture("06-workflow-slow-live-watch",
                      [(7.25, 25.0, YELLOW), (25.5, 26.25, GREEN), (26.75, 30.0, RED)], 30.0)
        samples = replay(sc, tmp_path)
        _check(sc, samples)
        assert _at(samples, 10.0).rollup == _rollup(total=3, running=3, workflows=1,
                                                    workflows_running=1)
        assert _at(samples, 26.25).rollup == _rollup(total=4, ok=4, workflows=1)

    def test_a_subagent_waiting_on_its_own_shell_is_still_running(self, tmp_path):
        """2.1.301: each subagent backgrounds its sleep and ends its turn.
        Claude drops it from the list (only the shells stay) and sends a
        "completed" notification marked interim; it resumes when the shell
        ends. Seen live as ✅ while both sleeps still ran."""
        sc = _capture("12-background-subagent-own-shell",
                      [(17.0, 63.5, YELLOW), (65.25, 105.25, YELLOW), (107.0, 110.0, RED)],
                      110.0)
        samples = replay(sc, tmp_path)
        _check(sc, samples)
        assert _at(samples, 40.0).rollup == _rollup(total=2, running=2)
        assert _at(samples, 80.0).rollup == _rollup(total=2, running=1, ok=1)
        assert _at(samples, 107.0).rollup == _rollup(total=2, ok=2)

    def test_a_team_waits_on_working_teammates_not_idle_ones(self, tmp_path):
        sc = _capture("08-team-in-process",
                      [(91.25, 92.25, YELLOW), (98.75, 150.0, RED)], 150.0)
        samples = replay(sc, tmp_path)
        _check(sc, samples)
        assert _at(samples, 95.0).rollup == _rollup(total=2, idle=2)
        assert _at(samples, 101.0).rollup == _rollup(total=2, ok=2)

    @pytest.mark.parametrize("name", ["03-background-subagent-with-SendMessage",
                                      "06-workflow-slow-live-watch", "08-team-in-process",
                                      "12-background-subagent-own-shell"])
    def test_the_rollup_moves_only_when_an_event_lands(self, name, tmp_path):
        """No countdowns: between two events every sample shows the same counts."""
        steps = capture_steps(name)
        end = max(s.t for s in steps)
        samples = replay(Scenario(name, steps=steps, expect=[(0.0, end, GREEN)], end=end),
                         tmp_path)
        times = sorted(s.t for s in steps)
        for prev, cur in zip(samples, samples[1:]):
            if not any(prev.t < t <= cur.t for t in times):
                assert prev.rollup == cur.rollup, (prev.t, cur.t)


# ---------------------------------------------------------------------------
# Synthetic timelines: the light, the timer it replaces, the bell
# ---------------------------------------------------------------------------

def _bg(*ids, kind="subagent"):
    return [{"id": i, "type": kind, "status": "running"} for i in ids]


# A background agent whose SubagentStop never arrives: the next Stop's list
# no longer has it, so the parent is red then, not an hour later.
LOST_SUBAGENT_STOP = Scenario(
    "lost_subagent_stop",
    steps=[
        ev(0.0, "UserPromptSubmit"),
        ev(1.0, "PreToolUse", "Agent", "a1", description="Survey", prompt="..."),
        ev(1.02, "SubagentStart", agent_id="sub1", agent_type="Explore"),
        ev(1.05, "PostToolUse", "Agent", "a1", description="Survey", prompt="..."),
        ev(2.0, "Stop", background_tasks=_bg("sub1")),
        ev(60.0, "UserPromptSubmit"),
        ev(62.0, "Stop", background_tasks=[]),
    ],
    expect=[(4.0, 59.75, YELLOW), (64.0, 80.0, RED)],
)

# A long-running background agent that says nothing for two hours: the
# list still has it, so the parent stays yellow (no 1 h timer).
QUIET_BACKGROUND_AGENT = Scenario(
    "quiet_background_agent",
    steps=[
        ev(0.0, "UserPromptSubmit"),
        ev(1.02, "SubagentStart", agent_id="sub1", agent_type="general-purpose"),
        ev(2.0, "Stop", background_tasks=_bg("sub1")),
    ],
    expect=[(4.0, 7300.0, YELLOW)],
)

# A busy swarm while the parent's turn is over: agents start, stop and ask
# permission; the parent is yellow throughout, orange only for the prompt.
BUSY_SWARM = Scenario(
    "busy_swarm",
    steps=[
        ev(0.0, "UserPromptSubmit"),
        ev(1.0, "SubagentStart", agent_id="s1", agent_type="Explore"),
        ev(1.1, "SubagentStart", agent_id="s2", agent_type="Explore"),
        ev(1.2, "SubagentStart", agent_id="s3", agent_type="Explore"),
        ev(2.0, "Stop", background_tasks=_bg("s1", "s2", "s3")),
        ev(30.0, "SubagentStop", agent_id="s1", background_tasks=_bg("s1", "s2", "s3")),
        ev(40.0, "SubagentStart", agent_id="s4", agent_type="Explore"),
        ev(50.0, "PermissionRequest", "Bash", "p1", agent_id="s2", command="git push"),
        ev(55.0, "PostToolUse", "Bash", "p1", agent_id="s2", command="git push"),
        ev(70.0, "SubagentStop", agent_id="s2", background_tasks=_bg("s2", "s3", "s4")),
        ev(90.0, "SubagentStop", agent_id="s3", background_tasks=_bg("s3", "s4")),
        ev(120.0, "SubagentStop", agent_id="s4", background_tasks=_bg("s4")),
        ev(120.05, "UserPromptSubmit"),
        ev(122.0, "Stop", background_tasks=[]),
    ],
    expect=[(4.0, 49.75, YELLOW), (50.25, 54.75, ORANGE), (55.25, 119.75, YELLOW),
            (124.0, 160.0, RED)],
)


class TestSyntheticTimelines:
    @pytest.mark.parametrize("scenario", [LOST_SUBAGENT_STOP, BUSY_SWARM],
                             ids=lambda s: s.name)
    def test_colours(self, scenario, tmp_path):
        _check(scenario, replay(scenario, tmp_path))

    def test_no_timer_once_the_list_is_known(self, tmp_path):
        samples = replay(QUIET_BACKGROUND_AGENT, tmp_path, cadence=60.0)
        _check(QUIET_BACKGROUND_AGENT, samples)

    def test_the_swarm_rolls_up(self, tmp_path):
        samples = replay(BUSY_SWARM, tmp_path)
        assert _at(samples, 45.0).rollup == _rollup(total=4, running=3, ok=1)
        assert _at(samples, 52.0).rollup == _rollup(total=4, running=2, waiting=1, ok=1)
        assert _at(samples, 130.0).rollup == _rollup(total=4, ok=4)

    def test_subagents_never_ring_the_bell(self, tmp_path):
        """The swarm's comings and goings ring nothing; the only bell is the
        parent's own, once its turn is over with nothing in flight."""
        samples = replay(BUSY_SWARM, tmp_path)
        recorder = EpisodeRecorder()
        bells = []
        for s in samples:
            obs = recorder.observe(s.colour, 1_800_000_000.0 + s.t)
            if obs.bell is not None:
                bells.append(s.t)
        assert all(t > 122.0 for t in bells), bells


# ---------------------------------------------------------------------------
# The daemon publishes it
# ---------------------------------------------------------------------------

class TestPublished:
    def test_the_detector_reads_it_with_the_status(self, tmp_path):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from overcode.hook_status_detector import HookStatusDetector
        import json

        subs, sw = _sub({}, {}, "SubagentStart", "a1", 0.0)
        subs, sw = _parent(subs, sw, "Stop", now=1.0, background_tasks=_bg("a1"))
        state_dir = tmp_path / "agents"
        state_dir.mkdir()
        (state_dir / "hook_state_p.json").write_text(json.dumps(
            {"event": "Stop", "timestamp": 1.0, "subagents": subs, "swarm": sw}))
        tmux = MagicMock()
        tmux.capture_pane.return_value = "⏺ Done.\n❯ \n  ⏵⏵ bypass permissions on"
        detector = HookStatusDetector("agents", tmux=tmux, state_dir=state_dir)
        session = SimpleNamespace(id="pid", name="p", tmux_window="p",
                                  parent_session_id=None, backend="claude-code")
        detector.detect_status(session, now=10.0)
        assert detector.get_swarm_rollup("p") == _rollup(total=1, running=1)
        assert detector.get_status_detail("p").color == "color_yellow"
        detector.detect_status(session, now=9000.0)
        assert detector.get_swarm_rollup("p") == _rollup(total=1, running=1)

    def test_a_quiet_swarm_publishes_no_delta(self, tmp_path, monkeypatch):
        """Ticks with nothing new: the published rollup is the same value,
        so the engine sends nothing; one event is one delta of it."""
        from overcode.engine_protocol import diff
        from overcode.session_manager import SessionManager
        from tests.daemon_tick_harness import (
            FrozenClock, ScriptedDetector, make_daemon, run_ticks, seed_sessions,
            seed_steady_state,
        )

        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "state" / "sessions"))
        (tmp_path / "state" / "sessions").mkdir(parents=True)
        start = datetime(2026, 10, 15, 12, 0, 0)
        sm = SessionManager(state_dir=tmp_path / "state" / "sessions", skip_git_detection=True)
        sessions = seed_sessions(sm, 3, "agents", tmp_path / "work", start)
        subs, sw = _sub({}, {}, "SubagentStart", "a1", 0.0)
        subs, sw = _sub(subs, sw, "SubagentStart", "a2", 0.0)
        subs, sw = _parent(subs, sw, "Stop", now=1.0, background_tasks=_bg("a1", "a2"))
        hook = {sessions[0].name: _state(subs, sw)}

        class Detector(ScriptedDetector):
            # Each tick reads the hook state an hour later than the last
            def get_swarm_rollup(self, name):
                return swarm.rollup(hook.get(name), clock.now.timestamp() + 3600 * self.tick)

        detector = Detector(lambda tick, s: ("waiting_user", "Waiting", ""))
        daemon = make_daemon(tmp_path / "state", "agents", detector, session_manager=sm)
        seed_steady_state(daemon, sessions, start)
        clock = FrozenClock(start)
        with clock.installed():
            run_ticks(daemon, detector, clock, 4)
            before = daemon.state.to_engine_snapshot(clock.now.timestamp())
            run_ticks(daemon, detector, clock, 6)
            after = daemon.state.to_engine_snapshot(clock.now.timestamp())
            assert diff(before, after).empty
            assert after.agents[sessions[0].id]["swarm_rollup"] == _rollup(total=2, running=2)
            assert after.agents[sessions[1].id]["swarm_rollup"] is None
            # One agent finishes: one delta, of that field alone
            hook[sessions[0].name] = _state(*_sub(subs, sw, "SubagentStop", "a1", 3.0))
            run_ticks(daemon, detector, clock, 1)
            later = daemon.state.to_engine_snapshot(clock.now.timestamp())
        delta = diff(after, later)
        assert delta.set == {sessions[0].id: {
            "swarm_rollup": _rollup(total=2, running=1, ok=1)}}


def test_finished_rows_keep_thirty_seconds_unless_configured(monkeypatch):
    """Owner decision 3: observability.finished_row_seconds, default 30."""
    from overcode import config

    monkeypatch.setattr(config, "_get_config_value", lambda key, default=None: default)
    assert config.get_observability_config()["finished_row_seconds"] == 30.0
    for value, expected in ((5, 5.0), (0, 0.0), (-1, 30.0), ("soon", 30.0)):
        monkeypatch.setattr(config, "_get_config_value", lambda key, default=None, v=value: (
            {"finished_row_seconds": v} if key == "observability" else default))
        assert config.get_observability_config()["finished_row_seconds"] == expected
