"""Which Claude conversation an agent is in: the agent's own, never a neighbour's.

Reported as "model and effort are right, but from a totally wrong session".
The daemon's 10 s session-id sync took the newest history.jsonl entry for
the agent's directory and made it the agent's active conversation. Claude's
history.jsonl says which directory a prompt was typed in, not which process
typed it, so any other Claude in the same repo (the IDE, a terminal, an
agent in another overcode tmux session) became this agent's conversation,
and its model, effort, context and PR were shown on this agent's row.

These tests run the real pieces end to end against a synthetic ~/.claude:
the hook handler (as Claude Code calls it), the session store, the
monitor daemon's sync and Claude's transcript reader. The only fakes are
the paths.
"""

import io
import json
import time
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from unittest.mock import patch

import pytest

from overcode import history_reader
from overcode.hook_handler import handle_hook_event
from overcode.session_manager import SessionManager

OWN_MODEL, OWN_EFFORT = "claude-opus-5-5", "high"
OTHER_MODEL, OTHER_EFFORT = "claude-haiku-4-5", "low"


class World:
    """A repo, a synthetic ~/.claude, a session store and a monitor daemon."""

    def __init__(self, tmp_path: Path, monkeypatch):
        self.monkeypatch = monkeypatch
        self.state_dir = tmp_path / "state"
        self.state_dir.mkdir()
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(self.state_dir))

        self.repo = (tmp_path / "repo").resolve()
        self.repo.mkdir()
        claude_home = tmp_path / "claude"
        self.projects = claude_home / "projects"
        self.history = claude_home / "history.jsonl"
        self.projects.mkdir(parents=True)
        self.history.touch()
        monkeypatch.setattr(history_reader, "_default_history",
                            history_reader.HistoryFile(self.history))
        monkeypatch.setattr(history_reader, "get_session_stats", partial(
            history_reader.get_session_stats,
            history_path=self.history, projects_path=self.projects,
        ))

        self.clock = datetime.now() + timedelta(seconds=1)
        self.sm = SessionManager()
        self._daemons = {}
        self._tmp = tmp_path

    # -- Claude's side ----------------------------------------------------

    def transcript(self, sid: str) -> Path:
        return history_reader.get_session_file_path(str(self.repo), sid, self.projects)

    def claude_turn(self, sid: str, model: str, effort: str) -> None:
        """One prompt and reply, written the way Claude Code writes them."""
        self.clock += timedelta(seconds=2)
        ts = self.clock.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        with self.history.open("a") as f:
            f.write(json.dumps({
                "display": "do the thing", "pastedContents": {},
                "timestamp": int(self.clock.timestamp() * 1000),
                "project": str(self.repo), "sessionId": sid,
            }) + "\n")
        path = self.transcript(sid)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps({
                "type": "user", "sessionId": sid, "timestamp": ts,
                "message": {"role": "user", "content": "do the thing"},
            }) + "\n")
            f.write(json.dumps({
                "type": "assistant", "sessionId": sid, "timestamp": ts, "effort": effort,
                "message": {"id": f"msg_{sid}", "model": model, "role": "assistant",
                            "usage": {"input_tokens": 120, "output_tokens": 30}},
            }) + "\n")

    def hook(self, agent: str, tmux_session: str, event: str, sid: str,
             source: str = None) -> None:
        """Claude Code calling `overcode hook-handler` inside the agent's pane."""
        payload = {"hook_event_name": event, "session_id": sid,
                   "transcript_path": str(self.transcript(sid)), "cwd": str(self.repo)}
        if source:
            payload["source"] = source
        self.monkeypatch.setenv("OVERCODE_SESSION_NAME", agent)
        self.monkeypatch.setenv("OVERCODE_TMUX_SESSION", tmux_session)
        with patch("sys.stdin", io.StringIO(json.dumps(payload))):
            handle_hook_event()

    # -- overcode's side --------------------------------------------------

    def launch(self, name: str, sid: str, tmux_session: str = "agents") -> str:
        """What AgentLauncher records for a launch with a prescribed --session-id."""
        session = self.sm.create_session(
            name=name, tmux_session=tmux_session, tmux_window=f"{name}-ab12",
            command=["claude"], start_directory=str(self.repo),
        )
        self.clock += timedelta(seconds=2)
        self.sm.update_session(session.id, agent_session_ids=[sid],
                               start_time=self.clock.isoformat())
        self.sm.set_active_agent_session_id(session.id, sid)
        return session.id

    def daemon(self, tmux_session: str = "agents"):
        if tmux_session not in self._daemons:
            from overcode.monitor_daemon import MonitorDaemon

            d = self._tmp / f"daemon-{tmux_session}"
            d.mkdir()
            mp = self.monkeypatch
            mp.setattr("overcode.monitor_daemon.ensure_session_dir", lambda _s: d)
            mp.setattr("overcode.monitor_daemon.get_monitor_daemon_pid_path", lambda _s: d / "pid")
            mp.setattr("overcode.monitor_daemon.get_monitor_daemon_state_path",
                       lambda _s: d / "state.json")
            mp.setattr("overcode.monitor_daemon.get_agent_history_path",
                       lambda _s: d / "history.csv")
            with patch("overcode.monitor_daemon.StatusDetectorDispatcher"):
                self._daemons[tmux_session] = MonitorDaemon(tmux_session=tmux_session)
        return self._daemons[tmux_session]

    def ticks(self, tmux_session: str = "agents", n: int = 2) -> None:
        """The daemon's session-id and stats sync, as its loop runs them."""
        daemon = self.daemon(tmux_session)
        for _ in range(n):
            for session in self.sm.list_sessions():
                if session.tmux_session == tmux_session:
                    daemon.sync_agent_stats(self.sm.get_session(session.id))
            daemon._flush_pending_writes()

    def agent(self, session_id: str):
        return self.sm.get_session(session_id)


@pytest.fixture
def world(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


def _assert_own_conversation(agent, sid):
    assert agent.active_agent_session_id == sid
    assert (agent.model, agent.effort) == (OWN_MODEL, OWN_EFFORT)


class TestAnotherClaudeInTheSameRepo:

    def test_an_ide_session_prompted_after_the_agent(self, world):
        """Claude in VS Code (or a terminal) in the agent's repo, outside overcode."""
        alpha = world.launch("alpha", "alpha-1")
        world.hook("alpha", "agents", "SessionStart", "alpha-1", source="startup")
        world.claude_turn("alpha-1", OWN_MODEL, OWN_EFFORT)
        world.hook("alpha", "agents", "Stop", "alpha-1")

        world.claude_turn("ide-1", OTHER_MODEL, OTHER_EFFORT)  # no overcode hooks
        world.ticks()

        agent = world.agent(alpha)
        _assert_own_conversation(agent, "alpha-1")
        assert "ide-1" not in agent.agent_session_ids

    def test_an_agent_in_another_tmux_session(self, world):
        """Two overcode sessions (`agents`, `work`) with agents in one repo."""
        alpha = world.launch("alpha", "alpha-1", tmux_session="agents")
        world.hook("alpha", "agents", "SessionStart", "alpha-1", source="startup")
        world.claude_turn("alpha-1", OWN_MODEL, OWN_EFFORT)

        world.launch("beta", "beta-1", tmux_session="work")
        world.hook("beta", "work", "SessionStart", "beta-1", source="startup")
        world.claude_turn("beta-1", OTHER_MODEL, OTHER_EFFORT)
        world.ticks("agents")

        _assert_own_conversation(world.agent(alpha), "alpha-1")


class TestTheAgentsOwnConversations:

    def test_clear_before_the_next_prompt_keeps_the_new_conversation(self, world):
        """After /clear the new conversation has no history.jsonl entry yet.

        The newest entry for the directory is still the conversation the
        agent just left, and taking it moved the agent back, so
        ``restart`` would resume the wrong conversation.
        """
        alpha = world.launch("alpha", "alpha-1")
        world.hook("alpha", "agents", "SessionStart", "alpha-1", source="startup")
        world.claude_turn("alpha-1", OTHER_MODEL, OTHER_EFFORT)
        world.hook("alpha", "agents", "SessionStart", "alpha-2", source="clear")
        world.ticks()

        assert world.agent(alpha).active_agent_session_id == "alpha-2"

        world.claude_turn("alpha-2", OWN_MODEL, OWN_EFFORT)
        world.ticks()
        _assert_own_conversation(world.agent(alpha), "alpha-2")

    def test_relaunch_under_a_dead_agents_name(self, world):
        """kill + launch under the same name leaves the old hook_state file.

        Until the new agent's first hook rewrites it, it names the dead
        agent's conversation; that must not become the new agent's.
        """
        old = world.launch("alpha", "old-1")
        world.hook("alpha", "agents", "SessionStart", "old-1", source="startup")
        world.claude_turn("old-1", OTHER_MODEL, OTHER_EFFORT)
        world.sm.delete_session(old)

        new = world.launch("alpha", "new-1")
        world.ticks()  # before the new process's first hook
        assert world.agent(new).active_agent_session_id == "new-1"

        world.hook("alpha", "agents", "SessionStart", "new-1", source="startup")
        world.claude_turn("new-1", OWN_MODEL, OWN_EFFORT)
        world.hook("alpha", "agents", "Stop", "new-1")
        world.ticks()
        _assert_own_conversation(world.agent(new), "new-1")

    def test_an_agent_without_hooks_still_follows_clear(self, world):
        """Polling mode has no hook state; history.jsonl is all there is."""
        alpha = world.launch("alpha", "alpha-1")
        world.claude_turn("alpha-1", OTHER_MODEL, OTHER_EFFORT)
        world.claude_turn("alpha-2", OWN_MODEL, OWN_EFFORT)  # after /clear
        world.ticks()

        _assert_own_conversation(world.agent(alpha), "alpha-2")
