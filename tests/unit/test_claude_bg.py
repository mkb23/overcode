"""Claude background sessions as 🌙 rows (agent-observability-0.6.1, phase 3).

Every jobs directory here is synthetic, under ``tmp_path``: nothing reads
the real ``~/.claude/jobs``.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from overcode import claude_bg
from overcode.claude_bg import (
    VIA_CONVERSATION,
    VIA_DIRECTORY,
    VIA_HOOKS,
    FleetAgent,
    JobsScanner,
    attribute,
    build_rows,
    find_job,
    job_light,
)
from overcode.status_constants import (
    STATUS_BUSY_SLEEPING,
    STATUS_COLOR_GREEN,
    STATUS_COLOR_RED,
    STATUS_COLOR_REST,
    STATUS_COLOR_YELLOW,
    STATUS_DONE,
    STATUS_RUNNING,
    STATUS_UNKNOWN,
    STATUS_WAITING_USER,
)

pytestmark = pytest.mark.unit

NOW = 1_800_000_000.0
SID = "b32fbee4-2c5e-435f-a55e-a2a6da6185ba"


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def job(state="working", tempo="active", sid=SID, cwd="/work/repo", **extra):
    """A jobs/<short>/state.json as Claude Code 2.1.300 writes it (capture 10)."""
    data = {"state": state, "tempo": tempo, "detail": "Running the tests",
            "inFlight": {"tasks": 0, "queued": 0}, "sessionId": sid,
            "resumeSessionId": sid, "daemonShort": sid[:8], "name": "probe", "cwd": cwd,
            "respawnFlags": ["-n", "probe", "--model", "haiku"],
            "createdAt": _iso(NOW - 100), "updatedAt": _iso(NOW - 50)}
    data.update(extra)
    return data


def write_job(directory: Path, data: dict) -> Path:
    path = directory / data["sessionId"][:8] / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


AGENT = FleetAgent(id="id-alpha", name="alpha", directory="/work/repo",
                   conversations=frozenset({"alpha-conv"}), repo_name="repo", branch="main")


class TestLights:
    """§3.4's background-session column."""

    @pytest.mark.parametrize("data, status, colour", [
        (job("working", "active"), STATUS_RUNNING, STATUS_COLOR_GREEN),
        (job("starting", None), STATUS_RUNNING, STATUS_COLOR_GREEN),
        (job("working", "idle", inFlight={"tasks": 2, "queued": 0}),
         STATUS_BUSY_SLEEPING, STATUS_COLOR_YELLOW),
        (job("blocked", "blocked", needs="Allow rm -rf build?"),
         STATUS_WAITING_USER, STATUS_COLOR_RED),
        (job("working", "blocked"), STATUS_WAITING_USER, STATUS_COLOR_RED),
        (job("done", "idle", output={"result": "bg-probe-done"}), STATUS_DONE, STATUS_COLOR_REST),
        (job("mystery"), STATUS_UNKNOWN, None),
        (None, STATUS_UNKNOWN, None),
    ])
    def test_state_to_light(self, data, status, colour):
        light = job_light(data)
        assert (light.status, light.colour) == (status, colour)

    def test_blocked_shows_what_it_needs(self):
        light = job_light(job("blocked", "blocked", needs="Allow\n  rm -rf build?"))
        assert light.activity == "Allow rm -rf build?"
        assert [b.kind for b in light.badges] == ["awaiting_input"]

    def test_waiting_on_its_own_work_counts_it(self):
        light = job_light(job("working", "idle", detail="", inFlight={"tasks": 2, "queued": 1}))
        assert light.activity == "Waiting on 3 background tasks"
        assert light.badges[0].count == 3

    @pytest.mark.parametrize("state, result", [
        ("done", "success"), ("failed", "failure"), ("crashed", "failure"),
        ("stopped", "unreported")])
    def test_finished_jobs_have_a_result(self, state, result):
        light = job_light(job(state, "idle", output={"result": "ok"},
                              lastTerminalAt=_iso(NOW - 3)))
        assert light.result == result
        assert light.ended_at == pytest.approx(NOW - 3)

    def test_done_result_text_is_the_activity(self):
        light = job_light(job("done", "idle", output={"result": "ran migration; PR #512"}))
        assert light.activity == "ran migration; PR #512"


class TestAttribution:
    def test_outside_every_agent_s_directory_it_is_not_the_fleet_s(self):
        assert attribute(job(cwd="/elsewhere"), [AGENT]) is None

    def test_inside_a_directory_it_nests_under_that_agent(self):
        assert attribute(job(cwd="/work/repo/sub"), [AGENT]) == (AGENT, VIA_DIRECTORY)
        assert attribute(job(cwd="/work/repository"), [AGENT]) is None  # a prefix is not inside

    def test_the_deepest_directory_wins(self):
        inner = FleetAgent(id="id-inner", name="inner", directory="/work/repo/pkg")
        assert attribute(job(cwd="/work/repo/pkg/x"), [AGENT, inner])[0] is inner

    def test_continuing_an_agent_s_conversation_beats_the_directory(self):
        other = FleetAgent(id="id-b", name="bravo", directory="/work/repo/deeper")
        found = attribute(job(cwd="/work/repo/deeper", resumeSessionId="alpha-conv"),
                          [other, AGENT])
        assert found == (AGENT, VIA_CONVERSATION)

    def test_hooks_that_fired_under_an_agent_s_name_beat_everything(self):
        """The real-world case: /bg from an agent's Claude inherited its env."""
        heir = FleetAgent(id="id-c", name="charlie", directory="/other", foreign=frozenset({SID}))
        assert attribute(job(cwd="/work/repo"), [AGENT, heir]) == (heir, VIA_HOOKS)

    def test_fleet_agents_from_sessions(self):
        from types import SimpleNamespace

        sessions = [
            SimpleNamespace(id="1", name="a", start_directory="/w", agent_session_ids=["c1"],
                            active_agent_session_id="c2", repo_name="w", branch="main",
                            status="running"),
            SimpleNamespace(id="2", name="gone", start_directory="/w", status="terminated"),
        ]
        agents = claude_bg.fleet_agents(sessions, lambda name: {SID: {"bg": True}})
        assert [a.name for a in agents] == ["a"]
        assert agents[0].conversations == {"c1", "c2"}
        assert agents[0].foreign == {SID}


class TestScanner:
    def test_reads_a_state_file_only_when_it_changed(self, tmp_path):
        path = write_job(tmp_path, job())
        (tmp_path / "pins.json").write_text("{}")  # not a job
        scanner = JobsScanner(tmp_path)
        assert scanner.scan() is True
        assert list(scanner.jobs) == [SID[:8]] and scanner.reads == 1
        assert scanner.scan() is False and scanner.reads == 1  # stat only
        path.write_text(json.dumps(job("blocked", "blocked", needs="ok?")))
        assert scanner.scan() is True
        assert scanner.jobs[SID[:8]]["state"] == "blocked"

    def test_rm_drops_the_job(self, tmp_path):
        path = write_job(tmp_path, job())
        scanner = JobsScanner(tmp_path)
        scanner.scan()
        path.unlink()
        path.parent.rmdir()
        assert scanner.scan() is True and scanner.jobs == {}

    def test_a_half_written_file_keeps_the_last_good_read(self, tmp_path):
        path = write_job(tmp_path, job())
        scanner = JobsScanner(tmp_path)
        scanner.scan()
        path.write_text('{"state": "wor')
        assert scanner.scan() is False
        assert scanner.jobs[SID[:8]]["state"] == "working"

    def test_no_jobs_directory_is_no_jobs(self, tmp_path):
        scanner = JobsScanner(tmp_path / "missing")
        assert scanner.scan() is False and scanner.jobs == {}

    def test_find_job_by_short_id_then_by_scan(self, tmp_path):
        write_job(tmp_path, job())
        assert find_job(SID, tmp_path)["sessionId"] == SID
        copy = job(sid="feedface-0000-4000-8000-000000000000")
        path = tmp_path / "abcdef12" / "state.json"
        path.parent.mkdir()
        path.write_text(json.dumps(copy))
        assert find_job(copy["sessionId"], tmp_path) is not None
        assert find_job(copy["sessionId"], tmp_path, scan_all=False) is None
        assert find_job("nope", tmp_path) is None

    def test_the_default_directory_follows_claude_config_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        assert claude_bg.jobs_dir() == tmp_path / "jobs"
        monkeypatch.delenv("CLAUDE_CONFIG_DIR")
        assert claude_bg.jobs_dir() == Path.home() / ".claude" / "jobs"


class TestRows:
    def test_a_fleet_job_is_an_agent_view_nested_under_its_agent(self):
        rows, deadline = build_rows({SID[:8]: job()}, [AGENT], NOW, 30.0)
        assert deadline is None
        (row,) = rows
        state = row.state
        assert state.session_id == f"claude-bg:{SID}"
        assert state.kind == "claude_bg"
        assert state.name == "probe"
        assert state.parent_name == "alpha"
        assert state.claude_bg == {"id": SID[:8], "session_id": SID, "parent_id": "id-alpha",
                                   "via": VIA_DIRECTORY, "cwd": "/work/repo"}
        assert state.model == "haiku"
        assert (state.repo_name, state.branch) == ("repo", "main")
        assert state.current_status == STATUS_RUNNING

    def test_jobs_outside_the_fleet_are_not_rows(self):
        rows, _ = build_rows({SID[:8]: job(cwd="/tmp/x")}, [AGENT], NOW, 30.0)
        assert rows == []

    def test_a_finished_row_stays_thirty_seconds_then_goes(self):
        jobs = {SID[:8]: job("done", "idle", lastTerminalAt=_iso(NOW - 10))}
        rows, deadline = build_rows(jobs, [AGENT], NOW, 30.0)
        assert len(rows) == 1 and deadline == pytest.approx(NOW + 20)
        assert rows[0].state.task_result == "success"
        rows, deadline = build_rows(jobs, [AGENT], NOW + 20, 30.0)
        assert rows == [] and deadline is None

    def test_rows_hold_still_between_changes(self):
        """No countdowns: a quiet job builds the same view at every refresh."""
        since: dict = {}
        jobs = {SID[:8]: job()}
        frames = [build_rows(jobs, [AGENT], t, 30.0, since)[0][0].state.to_dict()
                  for t in (NOW, NOW + 7, NOW + 600)]
        assert frames[0] == frames[1] == frames[2]

    def test_status_since_moves_only_when_the_light_does(self):
        since: dict = {}
        first = build_rows({SID[:8]: job()}, [AGENT], NOW, 30.0, since)[0][0].state
        busy = build_rows({SID[:8]: job(detail="other step")}, [AGENT], NOW + 5, 30.0,
                          since)[0][0].state
        assert busy.status_since == first.status_since
        blocked = build_rows({SID[:8]: job("blocked", "blocked")}, [AGENT], NOW + 9, 30.0,
                             since)[0][0].state
        assert blocked.status_since != first.status_since

    def test_spend_reaches_the_view(self):
        spend = {SID: {"input_tokens": 10, "output_tokens": 5, "cache_creation_tokens": 0,
                       "cache_read_tokens": 100, "cost_usd": 0.31, "model": "claude-haiku-5-5"}}
        state = build_rows({SID[:8]: job()}, [AGENT], NOW, 30.0, spend=spend)[0][0].state
        assert state.estimated_cost_usd == 0.31
        assert state.model == "claude-haiku-5-5"
        assert state.stats_available is True


def _transcript(path: Path, model="claude-haiku-5-5"):
    lines = [
        {"type": "user", "timestamp": "2026-10-15T10:37:00Z", "message": {"content": "go"}},
        {"type": "assistant", "timestamp": "2026-10-15T10:37:01Z",
         "message": {"id": "m1", "model": model, "content": [{"type": "text", "text": "a"}],
                     "usage": {"input_tokens": 100, "output_tokens": 20,
                               "cache_creation_input_tokens": 0,
                               "cache_read_input_tokens": 1000}}},
        # a second content block of the same message repeats its usage (F6)
        {"type": "assistant", "timestamp": "2026-10-15T10:37:02Z",
         "message": {"id": "m1", "model": model, "content": [{"type": "text", "text": "b"}],
                     "usage": {"input_tokens": 100, "output_tokens": 20,
                               "cache_creation_input_tokens": 0,
                               "cache_read_input_tokens": 1000}}},
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")


class TestSpend:
    def test_a_session_counts_its_own_transcript_once_per_message(self, tmp_path):
        transcript = tmp_path / "proj" / f"{SID}.jsonl"
        _transcript(transcript)
        spend = claude_bg.read_spend(job(linkScanPath=str(transcript)))
        assert spend["input_tokens"] == 100 and spend["output_tokens"] == 20
        assert spend["cache_read_tokens"] == 1000
        assert spend["cost_usd"] > 0
        assert spend["model"] == "claude-haiku-5-5"

    def test_no_transcript_no_spend(self, tmp_path):
        assert claude_bg.read_spend(job(linkScanPath=str(tmp_path / "none.jsonl"))) is None
        assert claude_bg.read_spend(job()) is None

    def test_read_at_the_stats_cadence_then_frozen_when_finished(self, tmp_path):
        transcript = tmp_path / "proj" / f"{SID}.jsonl"
        _transcript(transcript)
        data = job(linkScanPath=str(transcript))
        spend = claude_bg.BgSpend()
        assert spend.sync([(SID, data, False)], NOW, 5.0) is True
        assert spend.sync([(SID, data, False)], NOW + 1, 5.0) is False
        assert spend.reads == 1
        spend.sync([(SID, data, False)], NOW + 5, 5.0)
        assert spend.reads == 2
        spend.sync([(SID, data, True)], NOW + 6, 5.0)  # finished: one last read
        spend.sync([(SID, data, True)], NOW + 600, 5.0)
        assert spend.reads == 3
        spend.sync([], NOW + 601, 5.0)  # gone: forgotten
        assert spend.values == {}


class _Clock:
    def __init__(self, t=NOW):
        self.t = t

    def time(self):
        return self.t

    def monotonic(self):
        return self.t

    def wall(self):
        return datetime.fromtimestamp(self.t).astimezone()


from overcode.engine_socket import EngineServer


class _Engine(EngineServer):
    """A real EngineServer (diff, seq) that never binds; records deltas and rings."""

    def __init__(self, path):
        super().__init__(path)
        self.deltas = []
        self.rings = []

    def publish(self, snapshot):
        delta = super().publish(snapshot)
        self.deltas.append(delta)
        return delta

    def ring(self, agent, episode):
        self.rings.append((agent, episode))

    @property
    def agents(self):
        return self._snapshot.agents


@pytest.fixture
def fleet(tmp_path):
    """A test daemon with one agent in /work/repo and a synthetic jobs directory."""
    from overcode.monitor_daemon import MonitorDaemon
    from overcode.session_manager import SessionManager

    state_dir = tmp_path / "state"
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    sm = SessionManager(state_dir=state_dir, skip_git_detection=True)
    agent = sm.create_session(name="alpha", tmux_session="agents", tmux_window="alpha-1",
                              command=["claude"], start_directory=str(tmp_path / "repo"))
    clock = _Clock()
    daemon = MonitorDaemon.for_test(state_dir, session_manager=sm, clock=clock,
                                    claude_jobs_dir=jobs)
    daemon._engine = _Engine(tmp_path / "e.sock")
    daemon.claude_bg.keep_seconds = lambda: 30.0
    daemon.claude_bg.is_enabled = lambda: True
    return daemon, sm, agent, jobs, clock, tmp_path


def _bg_views(daemon):
    return {k: v for k, v in daemon._engine.agents.items() if v.get("kind") == "claude_bg"}


class TestEngine:
    def test_a_tick_publishes_the_fleet_s_background_sessions(self, fleet):
        daemon, sm, agent, jobs, clock, tmp = fleet
        write_job(jobs, job(cwd=str(tmp / "repo" / "sub")))
        write_job(jobs, job(sid="00000000-aaaa-4aaa-8aaa-000000000000", cwd="/not/fleet"))
        daemon._tick(clock.wall())
        views = _bg_views(daemon)
        assert list(views) == [f"claude-bg:{SID}"]
        view = views[f"claude-bg:{SID}"]
        assert view["parent_name"] == "alpha"
        assert view["claude_bg"]["parent_id"] == agent.id
        assert view["live_colour"] == STATUS_COLOR_GREEN

    def test_they_never_reach_the_state_file(self, fleet):
        daemon, sm, agent, jobs, clock, tmp = fleet
        write_job(jobs, job(cwd=str(tmp / "repo")))
        daemon._tick(clock.wall())
        assert _bg_views(daemon)
        assert "claude-bg:" not in daemon.state_path.read_text()

    def test_a_quiet_jobs_directory_sends_no_delta(self, fleet):
        from overcode.engine_wake import WakeLoop

        daemon, sm, agent, jobs, clock, tmp = fleet
        write_job(jobs, job(cwd=str(tmp / "repo")))
        daemon._tick(clock.wall())
        loop = WakeLoop(daemon)
        daemon._engine.deltas.clear()
        builds = daemon.claude_bg.builds
        published = 0
        for _ in range(30):  # 30 s of wake steps, nothing happening
            clock.t += 1.0
            published += loop.publish_claude_bg_if_due()
        assert published == 0
        assert daemon._engine.deltas == []
        assert daemon.claude_bg.scanner.reads == 1  # one stat per scan, no re-read
        # no rebuild either, but for the spend read at the 5 s stats cadence
        assert daemon.claude_bg.builds - builds <= 30 // 5

    def test_a_change_reaches_views_at_the_next_scan(self, fleet):
        from overcode.engine_wake import WakeLoop

        daemon, sm, agent, jobs, clock, tmp = fleet
        write_job(jobs, job(cwd=str(tmp / "repo")))
        daemon._tick(clock.wall())
        loop = WakeLoop(daemon)
        write_job(jobs, job("blocked", "blocked", cwd=str(tmp / "repo"), needs="Proceed?"))
        clock.t += claude_bg.SCAN_ATTENDED_SECONDS
        assert loop.publish_claude_bg_if_due() is True
        view = _bg_views(daemon)[f"claude-bg:{SID}"]
        assert view["current_status"] == STATUS_WAITING_USER
        assert view["current_activity"] == "Proceed?"

    def test_a_finished_row_leaves_when_its_time_is_up(self, fleet):
        from overcode.engine_wake import WakeLoop

        daemon, sm, agent, jobs, clock, tmp = fleet
        write_job(jobs, job("done", "idle", cwd=str(tmp / "repo"),
                            lastTerminalAt=_iso(clock.t - 1)))
        daemon._tick(clock.wall())
        assert _bg_views(daemon)
        loop = WakeLoop(daemon)
        clock.t += 28.5
        loop.publish_claude_bg_if_due()
        assert _bg_views(daemon)
        clock.t += 1.0
        assert loop.publish_claude_bg_if_due() is True
        assert _bg_views(daemon) == {}

    def test_a_blocked_session_rings_once(self, fleet):
        """Owner decision 4: a blocked background session rings the bell."""
        from overcode.engine_wake import WakeLoop

        daemon, sm, agent, jobs, clock, tmp = fleet
        daemon._tick(clock.wall())  # the engine is observing before the session starts
        clock.t += 5
        write_job(jobs, job("blocked", "blocked", cwd=str(tmp / "repo"),
                            createdAt=_iso(clock.t), needs="Allow?"))
        loop = WakeLoop(daemon)
        for _ in range(40):
            clock.t += 1.0
            loop.publish_claude_bg_if_due()
        assert [agent for agent, _ in daemon._engine.rings] == [f"claude-bg:{SID}"]

    def test_no_fleet_agent_no_scan(self, fleet):
        daemon, sm, agent, jobs, clock, tmp = fleet
        sm.delete_session(agent.id, archive=False)
        write_job(jobs, job(cwd=str(tmp / "repo")))
        daemon._tick(clock.wall())
        assert _bg_views(daemon) == {}
        assert daemon.claude_bg.scanner.reads == 0

    def test_switched_off_by_config(self, fleet):
        daemon, sm, agent, jobs, clock, tmp = fleet
        daemon.claude_bg.is_enabled = lambda: False
        write_job(jobs, job(cwd=str(tmp / "repo")))
        daemon._tick(clock.wall())
        assert _bg_views(daemon) == {}


class TestHooks:
    """A background session that inherited an agent's overcode env (/bg)."""

    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch, tmp_path):
        from overcode.session_manager import SessionManager

        monkeypatch.setenv("OVERCODE_SESSION_NAME", "test-agent")
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
        sm = SessionManager(skip_git_detection=True)
        created = sm.create_session(name="test-agent", tmux_session="agents",
                                    tmux_window="w", command=["claude"])
        sm.add_agent_session_id(created.id, "mine")
        sm.set_active_agent_session_id(created.id, "mine")
        self.state_path = tmp_path / "agents" / "hook_state_test-agent.json"
        self.jobs = tmp_path / "claude" / "jobs"

    def _send(self, payload):
        from overcode.hook_handler import handle_hook_event

        with patch("sys.stdin") as stdin:
            stdin.read.return_value = json.dumps(payload)
            handle_hook_event()

    def _state(self):
        return json.loads(self.state_path.read_text())

    def test_its_turns_no_longer_move_the_agent_s_status(self):
        write_job(self.jobs, job())
        self._send({"hook_event_name": "PreToolUse", "session_id": "mine",
                    "tool_name": "Read", "tool_input": {}, "tool_use_id": "t1"})
        for event in ("SessionStart", "UserPromptSubmit", "Stop"):
            self._send({"hook_event_name": event, "session_id": SID, "source": "startup"})
        state = self._state()
        assert state["event"] == "PreToolUse"
        assert state["agent_session_id"] == "mine"
        assert state["foreign_sessions"][SID]["bg"] is True

    def test_its_background_tasks_do_not_reconcile_the_agent_s_subagents(self):
        write_job(self.jobs, job())
        self._send({"hook_event_name": "SubagentStart", "session_id": "mine",
                    "agent_id": "a1", "agent_type": "Explore"})
        self._send({"hook_event_name": "Stop", "session_id": SID, "background_tasks": []})
        assert list(self._state()["subagents"]) == ["a1"]

    def test_a_cli_without_a_process_id_is_noted_but_behaves_as_before(self):
        """No CLAUDE_PID (another backend): not placed (test_conversation_placement)."""
        self._send({"hook_event_name": "PreToolUse", "session_id": "mine",
                    "tool_name": "Bash", "tool_input": {}, "tool_use_id": "t1"})
        self._send({"hook_event_name": "Stop", "session_id": "nested"})
        state = self._state()
        assert state["event"] == "Stop"  # unchanged behaviour (#500)
        assert state["foreign_sessions"]["nested"]["bg"] is False
        self._send({"hook_event_name": "PostToolUse", "session_id": "mine",
                    "tool_name": "Bash", "tool_use_id": "t1"})
        assert "nested" in self._state()["foreign_sessions"]  # carried forward

    def test_the_foreign_list_is_capped(self):
        from overcode import hook_handler

        state = {}
        for i in range(hook_handler._MAX_FOREIGN_SESSIONS + 5):
            state = {"foreign_sessions": hook_handler._note_foreign_session(
                state, f"s{i}", float(i))}
        assert len(state["foreign_sessions"]) == hook_handler._MAX_FOREIGN_SESSIONS
        assert "s0" not in state["foreign_sessions"]


def _git(*args, cwd):
    import subprocess

    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                    "-c", "init.defaultBranch=main", *args],
                   cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo_with_worktree(tmp_path):
    """A synthetic repo at tmp/main and a linked worktree outside it, at tmp/elsewhere/wt."""
    main = tmp_path / "main"
    main.mkdir()
    _git("init", "-q", cwd=main)
    _git("commit", "-q", "--allow-empty", "-m", "start", cwd=main)
    (tmp_path / "elsewhere").mkdir()
    _git("worktree", "add", "-q", "-b", "feature", str(tmp_path / "elsewhere" / "wt"), cwd=main)
    return main, tmp_path / "elsewhere" / "wt"


class TestWorktreePlacement:
    """Owner decision (0.6.1 follow-ups): a worktree outside every agent's
    directory nests under the agent working in its repository."""

    def test_a_worktree_outside_every_directory_nests_under_its_repo_s_agent(
            self, repo_with_worktree, tmp_path):
        main, wt = repo_with_worktree
        alpha = FleetAgent(id="a", name="alpha", directory=str(main), repo_name="main")
        other = FleetAgent(id="o", name="other", directory=str(tmp_path / "elsewhere"))
        (tmp_path / "unrelated").mkdir()
        unrelated = FleetAgent(id="u", name="unrelated", directory=str(tmp_path / "unrelated"))
        assert attribute(job(cwd=str(wt / "src")), [unrelated, alpha]) == (alpha, claude_bg.VIA_REPO)
        # a directory that holds it still wins (rule 3 before rule 4)
        assert attribute(job(cwd=str(wt)), [alpha, other]) == (other, VIA_DIRECTORY)

    def test_the_main_checkout_s_agent_beats_one_in_another_worktree(
            self, repo_with_worktree, tmp_path):
        main, wt = repo_with_worktree
        _git("worktree", "add", "-q", "-b", "second", str(tmp_path / "w2"), cwd=main)
        _git("worktree", "add", "-q", "-b", "third", str(tmp_path / "w3"), cwd=main)
        in_main = FleetAgent(id="m", name="zulu", directory=str(main))
        in_w2 = FleetAgent(id="w", name="alpha", directory=str(tmp_path / "w2"))
        assert attribute(job(cwd=str(tmp_path / "w3")), [in_w2, in_main])[0] is in_main
        assert attribute(job(cwd=str(tmp_path / "w3")), [in_w2])[0] is in_w2

    def test_not_a_repository_is_not_the_fleet_s(self, tmp_path):
        (tmp_path / "plain").mkdir()
        (tmp_path / "agent").mkdir()
        agent = FleetAgent(id="a", name="a", directory=str(tmp_path / "agent"))
        assert attribute(job(cwd=str(tmp_path / "plain")), [agent]) is None

    def test_the_repository_is_found_once(self, repo_with_worktree, monkeypatch):
        main, wt = repo_with_worktree
        alpha = FleetAgent(id="a", name="alpha", directory=str(main))
        assert attribute(job(cwd=str(wt)), [alpha])[1] == claude_bg.VIA_REPO
        monkeypatch.setattr(claude_bg.subprocess, "run",
                            lambda *a, **k: pytest.fail("git asked again"))
        for _ in range(3):
            assert attribute(job(cwd=str(wt)), [alpha])[1] == claude_bg.VIA_REPO

    def test_the_row_takes_its_repo_s_name(self, repo_with_worktree):
        main, wt = repo_with_worktree
        alpha = FleetAgent(id="a", name="alpha", directory=str(main), repo_name="main",
                           branch="main")
        rows, _ = build_rows({SID[:8]: job(cwd=str(wt))}, [alpha], NOW, 30.0)
        assert rows[0].state.repo_name == "main"
        assert rows[0].state.claude_bg["via"] == claude_bg.VIA_REPO
        assert rows[0].state.branch is None  # the worktree's own branch is not known
