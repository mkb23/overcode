"""Which CLI may steer an agent: nested Claudes, /resume, unplaced conversations.

Owner decision (0.6.1 follow-ups): only the agent's own CLI process moves
its status, obligations and swarm. Claude Code gives every hook
``CLAUDE_PID`` (its own process id), and ``/resume`` / ``/clear`` switch
conversations inside the same process, so the hook handler compares it with
the agent's own CLI (``cli_pid`` in its hook state).

Payloads are synthetic, shaped like the spike captures
(``docs/design/spike-agent-types/captures``). The process tree is real:
the pytest process plays the agent's CLI and a ``sleep`` child plays a
``claude -p`` its Bash tool started.
"""

import json
import os
import subprocess
from unittest.mock import patch

import pytest

from overcode import hook_handler

pytestmark = pytest.mark.unit

MINE = "11111111-aaaa-4aaa-8aaa-000000000001"
OTHER = "22222222-bbbb-4bbb-8bbb-000000000002"


@pytest.fixture
def agent(monkeypatch, tmp_path):
    from overcode.session_manager import SessionManager

    monkeypatch.setenv("OVERCODE_SESSION_NAME", "test-agent")
    monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.delenv("CLAUDE_PID", raising=False)
    sm = SessionManager(skip_git_detection=True)
    created = sm.create_session(name="test-agent", tmux_session="agents",
                                tmux_window="w", command=["claude"])
    sm.add_agent_session_id(created.id, MINE)
    sm.set_active_agent_session_id(created.id, MINE)
    return _Agent(monkeypatch, tmp_path, created.id)


class _Agent:
    def __init__(self, monkeypatch, tmp_path, sid):
        self.monkeypatch = monkeypatch
        self.tmp_path = tmp_path
        self.id = sid
        self.state_path = tmp_path / "agents" / "hook_state_test-agent.json"

    def send(self, payload, pid=None):
        if pid is None:
            self.monkeypatch.delenv("CLAUDE_PID", raising=False)
        else:
            self.monkeypatch.setenv("CLAUDE_PID", str(pid))
        with patch("sys.stdin") as stdin:
            stdin.read.return_value = json.dumps(payload)
            hook_handler.handle_hook_event()

    def state(self):
        return json.loads(self.state_path.read_text())

    def record(self):
        from overcode.session_manager import SessionManager

        return SessionManager(skip_git_detection=True).get_session(self.id)

    def working(self, pid):
        """The agent's own CLI mid-tool: its pid becomes the agent's."""
        self.send({"hook_event_name": "PreToolUse", "session_id": MINE,
                   "tool_name": "Bash", "tool_input": {"command": "claude -p hi"},
                   "tool_use_id": "t1"}, pid=pid)


@pytest.fixture
def child():
    """A real process the pytest process (the agent's CLI) started."""
    proc = subprocess.Popen(["sleep", "30"])
    yield proc.pid
    proc.kill()
    proc.wait()


def _dead_pid():
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


class TestOwnPid:
    def test_the_agent_s_own_events_record_its_cli(self, agent):
        agent.working(os.getpid())
        assert agent.state()["cli_pid"] == os.getpid()

    def test_a_foreign_event_does_not_move_it(self, agent, child):
        agent.working(os.getpid())
        agent.send({"hook_event_name": "Stop", "session_id": OTHER}, pid=child)
        assert agent.state()["cli_pid"] == os.getpid()


class TestNested:
    def test_a_nested_claude_does_not_steer_the_agent(self, agent, child):
        """`claude -p` in the agent's Bash: its turn ending is not the agent's."""
        agent.working(os.getpid())
        for event in ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SessionEnd"):
            agent.send({"hook_event_name": event, "session_id": OTHER,
                        "tool_name": "Read", "tool_use_id": "n1"}, pid=child)
        state = agent.state()
        assert state["event"] == "PreToolUse"
        assert state["tool_use_id"] == "t1"
        assert state["agent_session_id"] == MINE
        assert state["foreign_sessions"][OTHER]["kind"] == "nested"
        assert "conversation_warning" not in state
        assert agent.record().active_agent_session_id == MINE

    def test_its_obligations_and_subagents_stay_out(self, agent, child):
        agent.working(os.getpid())
        agent.send({"hook_event_name": "PreToolUse", "session_id": OTHER, "tool_name": "Bash",
                    "tool_input": {"command": "sleep 99", "run_in_background": True},
                    "tool_use_id": "bg1"}, pid=child)
        agent.send({"hook_event_name": "SubagentStart", "session_id": OTHER,
                    "agent_id": "a9", "agent_type": "Explore"}, pid=child)
        state = agent.state()
        assert not any(o.get("tool_use_id") == "bg1" for o in state.get("pending_obligations", []))
        assert "a9" not in (state.get("subagents") or {})

    def test_once_placed_the_record_is_not_asked_again(self, agent, child, monkeypatch):
        agent.working(os.getpid())
        agent.send({"hook_event_name": "UserPromptSubmit", "session_id": OTHER}, pid=child)
        calls = []
        monkeypatch.setattr(hook_handler, "_sync_agent_session_id",
                            lambda *a, **k: calls.append(a) or (MINE, True))
        monkeypatch.setattr(hook_handler, "_descends_from",
                            lambda *a: pytest.fail("placed again"))
        agent.send({"hook_event_name": "Stop", "session_id": OTHER}, pid=child)
        assert calls == []


class TestResume:
    def test_resume_into_an_unknown_conversation_keeps_steering(self, agent):
        """The risk case: /resume in the agent's own pane IS the agent."""
        agent.working(os.getpid())
        agent.send({"hook_event_name": "SessionStart", "source": "resume",
                    "session_id": OTHER}, pid=os.getpid())
        state = agent.state()
        assert state["event"] == "SessionStart"
        assert state["agent_session_id"] == OTHER
        assert OTHER not in state.get("foreign_sessions", {})
        warning = state["conversation_warning"]
        assert warning["kind"] == "adopted"
        assert warning["session_id"] == OTHER
        assert warning["previous"] == MINE
        record = agent.record()
        assert record.active_agent_session_id == OTHER  # restart resumes it
        # Its later turns are the agent's
        agent.send({"hook_event_name": "UserPromptSubmit", "session_id": OTHER},
                   pid=os.getpid())
        assert agent.state()["event"] == "UserPromptSubmit"

    def test_an_old_conversation_is_not_called_superseded(self, agent, tmp_path):
        """A resumed conversation's transcript is older than the current one's."""
        new_t = tmp_path / "mine.jsonl"
        old_t = tmp_path / "old.jsonl"
        old_t.write_text("{}\n")
        new_t.write_text("{}\n")
        os.utime(old_t, (1_000_000, 1_000_000))
        agent.send({"hook_event_name": "PreToolUse", "session_id": MINE,
                    "transcript_path": str(new_t), "tool_name": "Read",
                    "tool_use_id": "t1"}, pid=os.getpid())
        agent.send({"hook_event_name": "SessionStart", "source": "resume",
                    "session_id": OTHER, "transcript_path": str(old_t)}, pid=os.getpid())
        assert agent.record().active_agent_session_id == OTHER

    def test_a_cli_started_by_hand_after_the_agent_s_exited_is_the_agent(self, agent):
        agent.working(_dead_pid())
        agent.send({"hook_event_name": "UserPromptSubmit", "session_id": OTHER},
                   pid=os.getpid())
        state = agent.state()
        assert state["event"] == "UserPromptSubmit"
        assert state["agent_session_id"] == OTHER
        assert state["cli_pid"] == os.getpid()

    def test_another_agent_s_conversation_steers_but_is_not_adopted(self, agent):
        from overcode.session_manager import SessionManager

        sm = SessionManager(skip_git_detection=True)
        other = sm.create_session(name="bravo", tmux_session="agents",
                                  tmux_window="b", command=["claude"])
        sm.add_agent_session_id(other.id, OTHER)
        agent.working(os.getpid())
        agent.send({"hook_event_name": "SessionStart", "source": "resume",
                    "session_id": OTHER}, pid=os.getpid())
        state = agent.state()
        assert state["event"] == "SessionStart"
        assert state["agent_session_id"] == MINE
        assert state["conversation_warning"]["kind"] == "shared"
        assert state["conversation_warning"]["owner"] == "bravo"
        assert agent.record().active_agent_session_id == MINE


class TestUnplaced:
    def test_a_cli_that_is_neither_is_ignored_with_a_warning(self, agent):
        """Alive agent CLI, sender not inside it (nor a registered background session)."""
        agent.working(os.getpid())
        agent.send({"hook_event_name": "Stop", "session_id": OTHER}, pid=os.getppid())
        state = agent.state()
        assert state["event"] == "PreToolUse"
        assert state["foreign_sessions"][OTHER]["kind"] == "unplaced"
        assert state["conversation_warning"]["kind"] == "unplaced"
        assert state["conversation_warning"]["session_id"] == OTHER

    def test_before_the_agent_s_own_cli_is_known_nothing_is_placed_as_own(self, agent):
        agent.send({"hook_event_name": "Stop", "session_id": OTHER}, pid=os.getpid())
        state = agent.state()
        assert state["foreign_sessions"][OTHER]["kind"] == "unplaced"
        assert agent.record().active_agent_session_id == MINE


class TestWithoutAProcessId:
    def test_other_backends_behave_as_before(self, agent):
        """codex, opencode, grok, hermes: no CLAUDE_PID, so no placement (#500)."""
        agent.send({"hook_event_name": "PreToolUse", "session_id": MINE,
                    "tool_name": "Bash", "tool_use_id": "t1"})
        agent.send({"hook_event_name": "Stop", "session_id": OTHER})
        state = agent.state()
        assert state["event"] == "Stop"
        assert state["agent_session_id"] == MINE
        assert "kind" not in state["foreign_sessions"][OTHER]
        assert "cli_pid" not in state


class TestPlace:
    def test_unreadable_process_table_is_unplaced(self, monkeypatch, child):
        monkeypatch.setattr("overcode.process_resources.snapshot_processes", lambda: {})
        assert hook_handler._place({}, child, os.getpid()) == "unplaced"

    def test_descends_from_walks_the_real_tree(self, child):
        assert hook_handler._descends_from(child, os.getpid()) is True
        assert hook_handler._descends_from(os.getpid(), child) is False

    def test_a_registered_background_session_wins(self):
        assert hook_handler._place({"bg": True}, 123, 123) == "bg"


class TestWarningSurfaces:
    """What the person sees: a 🔀 badge beside the light, its hover, a doctor line."""

    def _detector(self, tmp_path, warning, now):
        from overcode.hook_status_detector import HookStatusDetector
        from tests.fixtures import create_mock_session, create_mock_tmux_with_content

        state_dir = tmp_path / "sessions" / "agents"
        state_dir.mkdir(parents=True)
        (state_dir / "hook_state_test-agent.json").write_text(json.dumps(
            {"event": "Stop", "timestamp": now - 5, "conversation_warning": warning}))
        tmux = create_mock_tmux_with_content("agents", 1, "pane")
        detector = HookStatusDetector("agents", tmux=tmux, state_dir=state_dir)
        session = create_mock_session(tmux_window=1, name="test-agent")
        detector.detect_status(session, now=now)
        return detector.get_status_detail("test-agent")

    def test_a_recent_warning_is_a_badge_beside_the_light(self, tmp_path):
        from overcode.status_constants import STATUS_COLOR_RED

        now = 1_800_000_000.0
        detail = self._detector(tmp_path, {"kind": "unplaced", "session_id": OTHER,
                                           "at": now - 60}, now)
        assert detail.color == STATUS_COLOR_RED  # the light is the agent's own
        assert [b.kind for b in detail.badges][-1] == "conv_unplaced"
        assert detail.badges[-1].label == OTHER[:8]

    def test_it_goes_after_half_an_hour(self, tmp_path):
        from overcode.conversation_warning import SHOW_SECONDS

        now = 1_800_000_000.0
        detail = self._detector(tmp_path, {"kind": "adopted", "session_id": OTHER,
                                           "at": now - SHOW_SECONDS}, now)
        assert all(not b.kind.startswith("conv_") for b in detail.badges)

    def test_text_says_what_happened(self):
        from overcode.conversation_warning import text

        assert text({"kind": "adopted", "session_id": OTHER, "previous": MINE}) == (
            "resumed conversation 22222222, which overcode did not start: "
            "now tracked as this agent's (was 11111111)")
        assert "belongs to agent bravo: not adopted; restart returns to 11111111" in text(
            {"kind": "shared", "session_id": OTHER, "owner": "bravo", "previous": MINE})
        assert text({"kind": "unplaced", "session_id": OTHER}).endswith(
            "its events are ignored")

    def test_badge_published_with_its_label(self):
        from overcode.conversation_warning import badge
        from overcode.episodes import status_detail_view
        from overcode.status_constants import StatusDetail

        view = status_detail_view(StatusDetail("color_red", [badge(
            {"kind": "shared", "session_id": OTHER, "owner": "bravo"})]), 0.0)
        assert view["badges"] == [{"kind": "conv_shared", "label": "bravo's 22222222"}]

    def test_doctor_lists_it(self, agent):
        from datetime import datetime

        from overcode.doctor import FINDING_CONVERSATION, conversation_findings

        agent.working(os.getpid())
        agent.send({"hook_event_name": "SessionStart", "source": "resume",
                    "session_id": OTHER}, pid=os.getpid())
        findings = conversation_findings(agent.record(), datetime.now())
        assert [f.code for f in findings] == [FINDING_CONVERSATION]
        assert findings[0].message.startswith("resumed conversation 22222222")
