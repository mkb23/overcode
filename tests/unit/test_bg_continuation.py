"""An agent's own conversation moved into Claude's background worker is still the agent.

Claude Code 2.1.304 (live, 2026-10-20): a message to an interactive session
from another device (Remote Control), or ``/background``, moves its
conversation into Claude's background daemon. The job record says
``template: "bg"``, ``backend: "daemon"``, ``interactiveLineage: true``; the
conversation gets a new ``sessionId`` whose transcript opens with a full copy
of the old one, which stops; the worker (``claude bg-spare``) inherited the
agent's OVERCODE_* environment and hooks. ``/fork`` writes the same kind of
copy but keeps working here, and records the fork in the job.

Everything here is synthetic: the transcripts are ``tests/fixtures/bg_fork``
(ORIGINAL holds the ALPHA and BRAVO replies, FORK a copy of both and then its
own CHARLIE reply), the jobs directory and Claude home are under
``tmp_path``. The process tree is real: the pytest process plays the agent's
CLI in its pane, ``sleep`` children play the worker and a nested Claude.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from overcode import claude_bg, conversation_warning, history_reader, hook_handler
from overcode.claude_bg import FleetAgent
from overcode.history_reader import HistoryFile, encode_project_path, get_session_stats
from overcode.session_manager import Session

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "bg_fork"
PROJECT = "/work/repo"
MINE = "0f0f0f0f-0000-4000-8000-00000000a1fa"    # the agent's conversation (ORIGINAL)
MOVED = "0f0f0f0f-0000-4000-8000-0000000c4a71"   # the same, moved to the worker (FORK)
OTHER = "22222222-bbbb-4bbb-8bbb-000000000002"
KEYS = ("input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens")
ALPHA = (2, 7, 10288, 11228)
BRAVO = (2, 7, 24157, 14314)
CHARLIE = (2, 8, 16981, 21537)
START = datetime(2026, 10, 16, 0, 0, 0)


def usage_of(*messages) -> dict:
    return {key: sum(m[i] for m in messages) for i, key in enumerate(KEYS)}


@pytest.fixture(autouse=True)
def _fresh_caches():
    history_reader.clear_transcript_caches()
    yield
    history_reader.clear_transcript_caches()


def project_dir(claude_home: Path) -> Path:
    path = claude_home / "projects" / encode_project_path(PROJECT)
    path.mkdir(parents=True, exist_ok=True)
    return path


def transcript(claude_home: Path, fixture: str, sid: str) -> Path:
    path = project_dir(claude_home) / f"{sid}.jsonl"
    shutil.copy(FIXTURES / f"{fixture}.jsonl", path)
    return path


def job(sid: str, path: Path, **extra) -> dict:
    """A jobs/<short>/state.json shaped like Claude Code 2.1.304's for a moved REPL."""
    data = {"state": "working", "tempo": "active", "detail": "", "template": "bg",
            "backend": "daemon", "interactiveLineage": True, "bgIsolation": "none",
            "sessionId": sid, "resumeSessionId": sid, "daemonShort": sid[:8],
            "name": "auto title", "nameSource": "auto", "intent": "<message>",
            "bridgeSessionId": "cse_synthetic", "cwd": PROJECT,
            "linkScanPath": str(path), "respawnFlags": ["--model", "haiku"],
            "inFlight": {"tasks": 0, "queued": 0},
            "createdAt": "2026-10-16T23:56:27Z", "updatedAt": "2026-10-16T23:56:34Z"}
    data.update(extra)
    return data


def register(claude_home: Path, data: dict) -> None:
    path = claude_home / "jobs" / data["sessionId"][:8] / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


# ── Telling a continuation from background work ────────────────────────


class TestContinues:
    def test_a_full_copy_of_the_agent_s_conversation_is_the_agent(self, tmp_path):
        transcript(tmp_path, "original", MINE)
        moved = transcript(tmp_path, "fork", MOVED)
        assert claude_bg.continues(job(MOVED, moved), MINE) is True

    def test_claude_bg_and_agent_background_sessions_are_not(self, tmp_path):
        """No interactive lineage: work the agent started, even with the history."""
        transcript(tmp_path, "original", MINE)
        moved = transcript(tmp_path, "fork", MOVED)
        data = job(MOVED, moved)
        del data["interactiveLineage"]
        assert claude_bg.continues(data, MINE) is False

    def test_fork_is_not(self, tmp_path):
        """/fork copies the conversation and keeps working here."""
        transcript(tmp_path, "original", MINE)
        moved = transcript(tmp_path, "fork", MOVED)
        data = job(MOVED, moved, forkSourceAlive=True, forkSessionId=MINE,
                   forkBoundaryAt="2026-10-16T23:56:20.000Z")
        assert claude_bg.continues(data, MINE) is False

    def test_a_nested_claude_s_own_conversation_is_not(self, tmp_path):
        """A Claude run in the agent's shell, moved to the worker: not the agent's history."""
        mine = project_dir(tmp_path) / f"{MINE}.jsonl"
        mine.write_text(json.dumps({"type": "user", "uuid": "agent-only-uuid",
                                    "sessionId": MINE}) + "\n")
        theirs = transcript(tmp_path, "fork", MOVED)
        assert claude_bg.continues(job(MOVED, theirs), MINE) is False

    def test_unknowable_until_both_transcripts_are_there(self, tmp_path):
        moved = transcript(tmp_path, "fork", MOVED)
        assert claude_bg.continues(job(MOVED, moved), MINE) is None  # the agent's is gone
        transcript(tmp_path, "original", MINE)
        missing = job(MOVED, project_dir(tmp_path) / "not-yet.jsonl")
        assert claude_bg.continues(missing, MINE) is None
        assert claude_bg.continues(job(MOVED, moved), None) is None

    def test_parked_mid_turn_the_unfinished_reply_is_not_copied(self, tmp_path):
        """#559: the reply being streamed when it was parked stays behind."""
        mine = transcript(tmp_path, "original", MINE)
        with open(mine, "a") as f:
            f.write(json.dumps({"type": "assistant", "uuid": "cut-off-reply",
                                "sessionId": MINE}) + "\n")
        moved = transcript(tmp_path, "fork", MOVED)
        assert claude_bg.continues(job(MOVED, moved), MINE) is True

    def test_the_cli_that_parked_it_says_so(self, tmp_path, monkeypatch):
        """Claude's registry: the agent's CLI parked this job (``parkedJobId``)."""
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        mine = project_dir(tmp_path) / f"{MINE}.jsonl"
        mine.write_text(json.dumps({"type": "user", "uuid": "only-here", "sessionId": MINE}) + "\n")
        moved = transcript(tmp_path, "fork", MOVED)
        assert claude_bg.continues(job(MOVED, moved), MINE) is False
        (tmp_path / "sessions").mkdir()
        (tmp_path / "sessions" / "41.json").write_text(json.dumps(
            {"sessionId": MINE, "kind": "interactive", "parkedJobId": MOVED[:8]}))
        assert claude_bg.continues(job(MOVED, moved), MINE) is True
        # Parked by another conversation's CLI (a nested Claude): not the agent's
        (tmp_path / "sessions" / "41.json").write_text(json.dumps(
            {"sessionId": OTHER, "kind": "interactive", "parkedJobId": MOVED[:8]}))
        assert claude_bg.continues(job(MOVED, moved), MINE) is False

    def test_handed_off_under_the_same_id(self, tmp_path):
        mine = transcript(tmp_path, "original", MINE)
        assert claude_bg.continues(job(MINE, mine), MINE) is True

    def test_the_last_message_is_found_across_read_chunks(self, tmp_path, monkeypatch):
        monkeypatch.setattr(claude_bg, "_SCAN_CHUNK", 7)
        transcript(tmp_path, "original", MINE)
        moved = transcript(tmp_path, "fork", MOVED)
        assert claude_bg.continues(job(MOVED, moved), MINE) is True


# ── The hook handler ───────────────────────────────────────────────────


@pytest.fixture(params=["by-name", "by-id"])
def agent(request, monkeypatch, tmp_path):
    """An agent in the pre-0.6 layout (name-keyed files) or the 0.6 one (agents/<id>/)."""
    from overcode.session_manager import SessionManager

    monkeypatch.setenv("OVERCODE_SESSION_NAME", "test-agent")
    monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.delenv("CLAUDE_PID", raising=False)
    monkeypatch.delenv("OVERCODE_SESSION_ID", raising=False)
    sm = SessionManager(skip_git_detection=True)
    created = sm.create_session(name="test-agent", tmux_session="agents",
                                tmux_window="w", command=["claude"])
    sm.add_agent_session_id(created.id, MINE)
    sm.set_active_agent_session_id(created.id, MINE)
    if request.param == "by-id":
        monkeypatch.setenv("OVERCODE_SESSION_ID", created.id)
    return _Agent(monkeypatch, tmp_path, created.id)


class _Agent:
    def __init__(self, monkeypatch, tmp_path, sid):
        self.monkeypatch = monkeypatch
        self.claude = tmp_path / "claude"
        self.id = sid
        self.mine = transcript(self.claude, "original", MINE)

    def send(self, payload, pid=None):
        if pid is None:
            self.monkeypatch.delenv("CLAUDE_PID", raising=False)
        else:
            self.monkeypatch.setenv("CLAUDE_PID", str(pid))
        with patch("sys.stdin") as stdin:
            stdin.read.return_value = json.dumps(payload)
            hook_handler.handle_hook_event()

    def state(self):
        return json.loads(hook_handler._get_hook_state_path("agents", "test-agent").read_text())

    def record(self):
        from overcode.session_manager import SessionManager

        return SessionManager(skip_git_detection=True).get_session(self.id)

    def working(self, pid):
        self.send({"hook_event_name": "PreToolUse", "session_id": MINE,
                   "transcript_path": str(self.mine), "tool_name": "Bash",
                   "tool_input": {"command": "make"}, "tool_use_id": "t1"}, pid=pid)

    def move_to_worker(self, **extra) -> Path:
        """Claude moves the conversation: a copy under a new id, a registered job."""
        moved = transcript(self.claude, "fork", MOVED)
        register(self.claude, job(MOVED, moved, **extra))
        return moved


@pytest.fixture
def worker():
    """A real process outside the agent's CLI (the background worker)."""
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    yield proc.pid
    proc.kill()
    proc.wait()


class TestAdopted:
    def test_the_worker_s_turns_are_the_agent_s(self, agent, worker):
        agent.working(os.getpid())
        moved = agent.move_to_worker()
        agent.send({"hook_event_name": "UserPromptSubmit", "session_id": MOVED,
                    "transcript_path": str(moved)}, pid=worker)
        state = agent.state()
        assert state["event"] == "UserPromptSubmit"
        assert state["agent_session_id"] == MOVED
        assert state["agent_session_ids"][-1] == MOVED
        assert MOVED not in state.get("foreign_sessions", {})
        assert "conversation_warning" not in state  # not a surprise: no 🔀
        assert state["bg_worker"]["session_id"] == MOVED
        assert state["bg_worker"]["pane_pid"] == os.getpid()
        assert state["cli_pid"] == worker
        record = agent.record()
        assert record.active_agent_session_id == MOVED  # restart resumes it
        assert MOVED in record.agent_session_ids
        # Its later events keep steering: the light follows the worker
        for event in ("PreToolUse", "Stop"):
            agent.send({"hook_event_name": event, "session_id": MOVED,
                        "transcript_path": str(moved), "tool_name": "Read",
                        "tool_use_id": "w1"}, pid=worker)
            assert agent.state()["event"] == event
        assert agent.state()["bg_worker"]["session_id"] == MOVED

    def test_an_entry_filed_as_background_work_before_is_placed_again(self, agent, worker):
        """Mike's fleet on upgrade: the moved conversation is already ``kind: bg``."""
        agent.working(os.getpid())
        moved = agent.move_to_worker()
        path = hook_handler._get_hook_state_path("agents", "test-agent")
        state = agent.state()
        state["foreign_sessions"] = {MOVED: {"bg": True, "kind": "bg", "pid": worker,
                                             "checked": 1.0, "at": 1.0}}
        path.write_text(json.dumps(state))
        agent.send({"hook_event_name": "Stop", "session_id": MOVED,
                    "transcript_path": str(moved)}, pid=worker)
        state = agent.state()
        assert state["event"] == "Stop"
        assert state["agent_session_id"] == MOVED
        assert MOVED not in state.get("foreign_sessions", {})

    def test_back_in_the_pane_under_the_same_id(self, agent, worker):
        agent.working(os.getpid())
        moved = agent.move_to_worker()
        agent.send({"hook_event_name": "UserPromptSubmit", "session_id": MOVED,
                    "transcript_path": str(moved)}, pid=worker)
        agent.send({"hook_event_name": "UserPromptSubmit", "session_id": MOVED,
                    "transcript_path": str(moved)}, pid=os.getpid())
        state = agent.state()
        assert "bg_worker" not in state
        assert state["cli_pid"] == os.getpid()

    def test_back_in_the_pane_under_another_id(self, agent, worker):
        agent.working(os.getpid())
        moved = agent.move_to_worker()
        agent.send({"hook_event_name": "UserPromptSubmit", "session_id": MOVED,
                    "transcript_path": str(moved)}, pid=worker)
        agent.send({"hook_event_name": "SessionStart", "source": "resume",
                    "session_id": OTHER}, pid=os.getpid())
        state = agent.state()
        assert state["event"] == "SessionStart"
        assert state["agent_session_id"] == OTHER
        assert "bg_worker" not in state
        assert "conversation_warning" not in state
        assert agent.record().active_agent_session_id == OTHER


class TestWorkerWithoutTheAgentsEnvironment:
    """#559, Claude Code 2.1.311/312 (live, 2026-10-27): the conversation was
    parked in a ``bg-spare`` that ``claude daemon`` had started before, with
    the agent's ``--settings`` hooks but none of its OVERCODE_* environment
    and no tmux pane. The pane's CLI records ``parkedJobId`` in Claude's
    registry (``sessions/<pid>.json``), its ``sessionId`` still the old one."""

    def park(self, agent, worker) -> Path:
        # Parked mid-turn, as live: the reply it was streaming is not copied
        with open(agent.mine, "a") as f:
            f.write(json.dumps({"type": "assistant", "uuid": "cut-off-reply",
                                "sessionId": MINE}) + "\n")
        moved = agent.move_to_worker()
        registry = agent.claude / "sessions"
        registry.mkdir(parents=True, exist_ok=True)
        (registry / f"{os.getpid()}.json").write_text(json.dumps({
            "pid": os.getpid(), "sessionId": MINE, "kind": "interactive",
            "parkedJobId": MOVED[:8]}))
        (registry / f"{worker}.json").write_text(json.dumps({
            "pid": worker, "sessionId": MOVED, "kind": "bg", "jobId": MOVED[:8]}))
        for name in ("OVERCODE_SESSION_NAME", "OVERCODE_TMUX_SESSION",
                     "OVERCODE_SESSION_ID", "TMUX_PANE"):
            # Set first so monkeypatch restores it: the handler sets
            # OVERCODE_SESSION_ID itself, as a hook process may
            agent.monkeypatch.setenv(name, "")
            agent.monkeypatch.delenv(name)
        return moved

    def test_its_turns_are_still_the_agent_s(self, agent, worker):
        agent.working(os.getpid())
        moved = self.park(agent, worker)
        agent.send({"hook_event_name": "UserPromptSubmit", "session_id": MOVED,
                    "transcript_path": str(moved)}, pid=worker)
        state = agent.state()
        assert state["event"] == "UserPromptSubmit"
        assert state["agent_session_id"] == MOVED
        assert state["bg_worker"]["session_id"] == MOVED
        assert agent.record().active_agent_session_id == MOVED
        # Later events find the agent by its adopted conversation
        agent.monkeypatch.delenv("OVERCODE_SESSION_ID", raising=False)
        agent.send({"hook_event_name": "PreToolUse", "session_id": MOVED,
                    "transcript_path": str(moved), "tool_name": "Read",
                    "tool_use_id": "w1"}, pid=worker)
        assert agent.state()["event"] == "PreToolUse"

    def test_subagents_it_left_running_end_on_its_stop(self, agent, worker):
        agent.working(os.getpid())
        for sub in ("a1", "a2"):
            agent.send({"hook_event_name": "SubagentStart", "session_id": MINE,
                        "agent_id": sub, "agent_type": "general-purpose"}, pid=os.getpid())
        assert set(agent.state()["subagents"]) == {"a1", "a2"}
        moved = self.park(agent, worker)
        agent.send({"hook_event_name": "Stop", "session_id": MOVED,
                    "transcript_path": str(moved), "background_tasks": []}, pid=worker)
        state = agent.state()
        assert state["event"] == "Stop"
        assert not state.get("subagents")

    def test_a_conversation_no_agent_parked_is_left_alone(self, agent, worker):
        agent.working(os.getpid())
        path = hook_handler._get_hook_state_path("agents", "test-agent")
        before = path.read_text()
        moved = self.park(agent, worker)
        (agent.claude / "sessions" / f"{os.getpid()}.json").unlink()
        agent.send({"hook_event_name": "UserPromptSubmit", "session_id": MOVED,
                    "transcript_path": str(moved)}, pid=worker)
        assert path.read_text() == before

    def test_parked_from_reads_claude_s_registry(self, tmp_path):
        moved = transcript(tmp_path, "fork", MOVED)
        register(tmp_path, job(MOVED, moved))
        registry = tmp_path / "sessions"
        registry.mkdir()
        assert claude_bg.parked_from(MOVED, tmp_path / "jobs", registry) is None
        (registry / "41.json").write_text(json.dumps(
            {"sessionId": MINE, "kind": "interactive", "parkedJobId": MOVED[:8]}))
        assert claude_bg.parked_from(MOVED, tmp_path / "jobs", registry) == MINE
        data = job(MOVED, moved)
        del data["interactiveLineage"]  # work the agent started, not its conversation
        register(tmp_path, data)
        assert claude_bg.parked_from(MOVED, tmp_path / "jobs", registry) is None


class TestStillBackgroundWork:
    """#500 and phase 3: what the agent started keeps its 🌙 row and stays off its light."""

    def _stays_off(self, agent, sid, pid, kind="bg"):
        state = agent.state()
        assert state["event"] == "PreToolUse"
        assert state["tool_use_id"] == "t1"
        assert state["agent_session_id"] == MINE
        assert state["foreign_sessions"][sid]["kind"] == kind
        assert "bg_worker" not in state
        assert agent.record().active_agent_session_id == MINE

    def test_claude_bg(self, agent, worker):
        agent.working(os.getpid())
        moved = agent.move_to_worker(interactiveLineage=None)
        agent.send({"hook_event_name": "Stop", "session_id": MOVED,
                    "transcript_path": str(moved)}, pid=worker)
        self._stays_off(agent, MOVED, worker)
        assert agent.state()["foreign_sessions"][MOVED]["continues"] is False

    def test_fork(self, agent, worker):
        agent.working(os.getpid())
        moved = agent.move_to_worker(forkSourceAlive=True, forkSessionId=MINE,
                                     forkBoundaryAt="2026-10-16T23:56:20.000Z")
        agent.send({"hook_event_name": "Stop", "session_id": MOVED,
                    "transcript_path": str(moved)}, pid=worker)
        self._stays_off(agent, MOVED, worker)

    def test_a_nested_claude_moved_to_the_worker(self, agent, worker):
        """A Claude run in the agent's shell, then /background: its history is not the agent's."""
        agent.working(os.getpid())
        theirs = project_dir(agent.claude) / f"{OTHER}.jsonl"
        theirs.write_text(json.dumps({"type": "user", "uuid": "nested-only",
                                      "sessionId": OTHER}) + "\n")
        register(agent.claude, job(OTHER, theirs))
        agent.send({"hook_event_name": "Stop", "session_id": OTHER,
                    "transcript_path": str(theirs)}, pid=worker)
        self._stays_off(agent, OTHER, worker)

    def test_a_nested_claude_cli(self, agent):
        """#500: ``claude -p`` in the agent's Bash tool never takes the record over."""
        agent.working(os.getpid())
        child = subprocess.Popen(["sleep", "30"])
        try:
            for event in ("SessionStart", "UserPromptSubmit", "Stop"):
                agent.send({"hook_event_name": event, "session_id": OTHER,
                            "source": "startup"}, pid=child.pid)
        finally:
            child.kill()
            child.wait()
        self._stays_off(agent, OTHER, child.pid, kind="nested")


# ── What the person sees ───────────────────────────────────────────────


class TestBadge:
    def test_shown_while_the_worker_has_the_current_conversation(self):
        state = {"agent_session_id": MOVED,
                 "bg_worker": {"session_id": MOVED, "pane_pid": 1, "at": 5.0}}
        note = conversation_warning.bg_worker(state)
        badge = conversation_warning.bg_worker_badge(note)
        assert (badge.kind, badge.label) == ("bg_worker", MOVED[:8])
        assert conversation_warning.bg_worker(dict(state, agent_session_id=OTHER)) is None
        assert conversation_warning.bg_worker({"agent_session_id": MOVED}) is None

    def test_the_detector_puts_it_beside_the_light(self, tmp_path):
        from overcode.hook_status_detector import HookStatusDetector
        from tests.fixtures import create_mock_session, create_mock_tmux_with_content
        import time

        now = time.time()
        state_dir = tmp_path / "sessions" / "agents"
        state_dir.mkdir(parents=True)
        (state_dir / "hook_state_test-agent.json").write_text(json.dumps(
            {"event": "PreToolUse", "tool_name": "Read", "timestamp": now - 1,
             "agent_session_id": MOVED,
             "bg_worker": {"session_id": MOVED, "pane_pid": 1, "at": now - 1}}))
        tmux = create_mock_tmux_with_content("agents", 1, "pane")
        detector = HookStatusDetector("agents", tmux=tmux, state_dir=state_dir)
        session = create_mock_session(name="test-agent", tmux_session="agents", tmux_window=1)
        detector.detect_status(session)
        kinds = [b.kind for b in detector.get_status_detail("test-agent").badges]
        assert "bg_worker" in kinds

    @pytest.mark.parametrize("emoji_free", [False, True])
    def test_it_fits_the_status_detail_cell(self, emoji_free):
        from types import SimpleNamespace

        from overcode.columns.render_identity import (
            _STATUS_DETAIL_WIDTH,
            _display_width,
            _render_status_detail,
        )
        from overcode.status_constants import STATUS_COLOR_GREEN, StatusBadge, StatusDetail

        ctx = SimpleNamespace(bg="", emoji_free=emoji_free)
        for badges in ([StatusBadge("bg_worker", label=MOVED[:8])],
                       [StatusBadge("tool"), StatusBadge("bg_worker", label=MOVED[:8]),
                        StatusBadge("conv_adopted", label="abc")]):
            out = _render_status_detail(ctx, StatusDetail(STATUS_COLOR_GREEN, badges))
            text = "".join(seg[0] for seg in out) if isinstance(out, list) else str(out)
            assert _display_width(text) <= _STATUS_DETAIL_WIDTH


# ── The engine: no 🌙 row, no second bill ──────────────────────────────


NOW = datetime(2026, 10, 17, 1, 0).timestamp()


class TestNoRow:
    def test_once_adopted_it_is_one_of_the_agent_s_conversations(self, tmp_path):
        moved = transcript(tmp_path, "fork", MOVED)
        parent = FleetAgent(id="a1", name="alpha", directory=PROJECT,
                            conversations=frozenset({MINE, MOVED}), active=MOVED)
        rows, _ = claude_bg.build_rows({MOVED[:8]: job(MOVED, moved)}, [parent], NOW, 30.0)
        assert rows == []

    def test_before_the_hooks_adopt_it_the_engine_knows(self, tmp_path):
        """No 🌙 flash between the job appearing and its first hook event."""
        transcript(tmp_path, "original", MINE)
        moved = transcript(tmp_path, "fork", MOVED)
        register(tmp_path, job(MOVED, moved))
        source = claude_bg.ClaudeBgSource(tmp_path / "jobs", keep_seconds=lambda: 30.0,
                                          is_enabled=lambda: True)
        source.set_agents([FleetAgent(id="a1", name="alpha", directory=PROJECT,
                                      conversations=frozenset({MINE}), active=MINE)])
        source.refresh(0.0, NOW, 60.0)
        assert source.rows == []
        assert source.continued == {MOVED}
        assert MOVED in source.owned()

    def test_background_work_beside_it_keeps_its_row(self, tmp_path):
        transcript(tmp_path, "original", MINE)
        moved = transcript(tmp_path, "fork", MOVED)
        register(tmp_path, job(MOVED, moved, interactiveLineage=None))
        source = claude_bg.ClaudeBgSource(tmp_path / "jobs", keep_seconds=lambda: 30.0,
                                          is_enabled=lambda: True)
        source.set_agents([FleetAgent(id="a1", name="alpha", directory=PROJECT,
                                      conversations=frozenset({MINE}), active=MINE)])
        source.refresh(0.0, NOW, 60.0)
        assert [r.state.session_id for r in source.rows] == [claude_bg.row_id(MOVED)]
        # Billed once, without the copy (phase 3's fork dedup)
        assert source.rows[0].state.cache_read_tokens == CHARLIE[3]

    def test_fleet_agents_carry_the_active_conversation(self):
        from types import SimpleNamespace

        s = SimpleNamespace(id="1", name="a", start_directory="/w", agent_session_ids=[MINE],
                            active_agent_session_id=MOVED)
        [agent] = claude_bg.fleet_agents([s])
        assert agent.active == MOVED
        assert agent.conversations == {MINE, MOVED}


class TestLedger:
    def test_a_segment_opened_before_it_was_known_is_restated_to_nothing(self, tmp_path):
        """The 🌙 row billed before the fix; the agent's own spend counts it now."""
        from overcode.claude_bg_ledger import BgLedger
        from overcode.ledger import Ledger

        transcript(tmp_path, "original", MINE)
        moved = transcript(tmp_path, "fork", MOVED)
        before = FleetAgent(id="a1", name="alpha", directory=PROJECT,
                            conversations=frozenset({MINE}), billing_code="proj-x")
        jobs = {MOVED[:8]: job(MOVED, moved, interactiveLineage=None)}
        spend = claude_bg.BgSpend()
        rows, _ = claude_bg.build_rows(jobs, [before], NOW, 30.0)
        spend.sync(claude_bg.row_reads(rows, {MOVED: jobs[MOVED[:8]]}, [before]), NOW, 60.0)
        rows, _ = claude_bg.build_rows(jobs, [before], NOW, 30.0, spend=spend.values)
        ledger = Ledger(tmp_path / "reg")
        writer = BgLedger(lambda: ledger.dir, owner="agents")
        assert writer.sync(rows, [before], ledger, NOW) == 1

        after = FleetAgent(id="a1", name="alpha", directory=PROJECT,
                           conversations=frozenset({MINE, MOVED}), active=MOVED,
                           billing_code="proj-x")
        rows, _ = claude_bg.build_rows(jobs, [after], NOW + 5, 30.0)
        assert rows == []
        writer.sync(rows, [after], ledger, NOW + 5, reread=lambda rid: pytest.fail("billed"))
        bg = [r for r in ledger.iter_rows() if r.get("claude_bg")]
        assert [r["kind"] for r in bg] == ["checkpoint", "correction"]
        assert bg[-1]["tokens"] == 0 and bg[-1]["cost"] == 0
        # Settled: nothing more on the next pass
        assert writer.sync(rows, [after], ledger, NOW + 10) == 0


class TestSpend:
    """The copy is billed once, by the conversation it came from; the new turns once."""

    def _claude(self, tmp_path):
        projects = tmp_path / "projects"
        history = tmp_path / "history.jsonl"
        transcript(tmp_path, "original", MINE)
        transcript(tmp_path, "fork", MOVED)
        history.write_text(json.dumps({"display": "go", "project": PROJECT, "sessionId": MINE,
                                       "timestamp": int(START.timestamp() * 1000) + 60_000})
                           + "\n")
        return projects, history

    def _agent(self):
        return Session(id="id-alpha", name="alpha", tmux_session="agents", tmux_window="1",
                       command=[], start_directory=PROJECT, start_time=START.isoformat(),
                       agent_session_ids=[MINE, MOVED], active_agent_session_id=MOVED)

    def test_the_agent_s_spend(self, tmp_path):
        projects, history = self._claude(tmp_path)
        stats = get_session_stats(self._agent(), history_path=history, projects_path=projects,
                                  history_file=HistoryFile(history))
        assert {k: getattr(stats, k) for k in KEYS} == usage_of(ALPHA, BRAVO, CHARLIE)
        assert stats.per_conversation[MOVED]["cr"] == CHARLIE[3]
        assert stats.per_conversation[MINE]["cr"] == ALPHA[3] + BRAVO[3]

    def test_the_burn_rate_window(self, tmp_path):
        projects, _ = self._claude(tmp_path)
        window = history_reader.get_session_window_token_usage(self._agent(), START, projects)
        assert {k: window[k] for k in KEYS} == usage_of(ALPHA, BRAVO, CHARLIE)
        # A window that starts after the copied messages: only the new turn
        since = datetime.fromisoformat("2026-10-16T23:56:25+00:00").astimezone().replace(
            tzinfo=None)
        window = history_reader.get_session_window_token_usage(self._agent(), since, projects)
        assert {k: window[k] for k in KEYS} == usage_of(CHARLIE)


class TestBackInThePaneElsewhere:
    def test_another_agent_s_conversation_is_still_only_shared(self, agent, worker):
        from overcode.session_manager import SessionManager

        sm = SessionManager(skip_git_detection=True)
        other = sm.create_session(name="bravo", tmux_session="agents",
                                  tmux_window="b", command=["claude"])
        sm.add_agent_session_id(other.id, OTHER)
        agent.working(os.getpid())
        moved = agent.move_to_worker()
        agent.send({"hook_event_name": "UserPromptSubmit", "session_id": MOVED,
                    "transcript_path": str(moved)}, pid=worker)
        agent.send({"hook_event_name": "SessionStart", "source": "resume",
                    "session_id": OTHER}, pid=os.getpid())
        state = agent.state()
        assert state["conversation_warning"]["kind"] == "shared"
        assert state["conversation_warning"]["owner"] == "bravo"
        assert "bg_worker" not in state
        assert agent.record().active_agent_session_id == MOVED
