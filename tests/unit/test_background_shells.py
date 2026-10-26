"""Background shells: services, batch work and watchers (#515)."""

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode import swarm
from overcode.background_shells import (
    KIND_BATCH, KIND_SERVICE, KIND_WATCHER, STUCK_GRACE_SECONDS, TARGET_ALIVE, TARGET_ENDED,
    TARGET_MISSING, ShellMemory, ShellProc, classify, shell_command, shell_procs, survey,
    watch_target,
)
from overcode.daemon_shell_probe import ShellProbe
from overcode.hook_status_detector import HookStatusDetector, TickContext, decide
from overcode.status_constants import STATUS_COLOR_YELLOW
from tests.fixtures import create_mock_session, create_mock_tmux_with_content


def _wrapper(command: str) -> str:
    """The argv Claude Code gives a Bash-tool shell (shape seen live, 2.1.310)."""
    quoted = "'" + command.replace("'", "'\\''") + "'"
    return ("/bin/zsh -c source /home/u/.claude/shell-snapshots/snapshot-zsh-1-ab.sh 2>/dev/null"
            " || true && setopt NO_EXTENDED_GLOB 2>/dev/null || true && { \\builtin unalias -- "
            "'unsetenv'; } >/dev/null 2>&1 || true && eval " + quoted
            + " < /dev/null && pwd -P >| /tmp/claude-e932-cwd")


POLL = "until overcode jobs tail build -n 40 | grep -qE 'ok|Error'; do sleep 3; done"


class TestShellCommand:
    def test_reads_the_evald_command_back(self):
        assert shell_command(_wrapper(POLL)) == POLL
        assert shell_command(_wrapper("npm run dev")) == "npm run dev"

    def test_other_processes_are_not_shells(self):
        assert shell_command("/usr/bin/python -m pytest") is None
        assert shell_command("claude bg-spare --bg-spare /tmp/x.sock") is None


class TestClassify:
    @pytest.mark.parametrize("command,kind", [
        ("npm run dev", KIND_SERVICE),
        ("pnpm dev", KIND_SERVICE),
        ("python -m http.server 8000", KIND_SERVICE),
        ("uvicorn app:main --reload", KIND_SERVICE),
        ("tail -f server.log", KIND_SERVICE),
        ("tsc --watch", KIND_SERVICE),
        ("docker compose up", KIND_SERVICE),
        ("pytest -q tests", KIND_BATCH),
        ("make build 2>&1 | tail -5", KIND_BATCH),
        ("docker compose up -d && sleep 5", KIND_BATCH),  # detached: it returns
        (POLL, KIND_WATCHER),
        ("overcode follow kid", KIND_WATCHER),
        ("overcode jobs wait build --until ready", KIND_WATCHER),
        ("gh run watch 123", KIND_WATCHER),
        ("while ! curl -s localhost:3000; do sleep 1; done", KIND_WATCHER),
        ("sleep 30 && gh pr checks 5", KIND_WATCHER),
        ("tail -f log | grep -m1 Started", KIND_WATCHER),
    ])
    def test_rules(self, command, kind):
        assert classify(command) == kind

    def test_runtime_signals_place_what_the_rules_cannot(self):
        assert classify("./run.sh") == KIND_BATCH
        assert classify("./run.sh", polls=True) == KIND_WATCHER
        assert classify("./run.sh", listens=True) == KIND_SERVICE

    def test_the_description_is_the_last_word(self):
        assert classify("./ci.sh", "Wait for CI to finish") == KIND_WATCHER
        assert classify("./go", "Start the API server") == KIND_SERVICE


class TestWatchTarget:
    @pytest.mark.parametrize("command,target", [
        (POLL, ("job", "build")),
        ("overcode jobs wait -t 5m build", ("job", "build")),
        ("overcode follow --timeout 5m kid", ("agent", "kid")),
        ("gh run watch 1", None),
    ])
    def test_targets(self, command, target):
        assert watch_target(command) == target


def _procs(*commands, polls=False):
    return [ShellProc(100 + i, c, (100 + i,), polls) for i, c in enumerate(commands)]


def _survey(listed, procs, memory=None, now=1000.0, ended=None, target=None, listening=None):
    return survey(listed, procs, ended or (lambda i: None),
                  target or (lambda k, n: (TARGET_ALIVE, None)),
                  memory if memory is not None else ShellMemory(), now,
                  listening=listening or (lambda p: False))


class TestSurvey:
    def test_matches_listed_shells_to_processes(self):
        listed = {"b1": {"command": "pytest", "since": 10.0}, "b2": {"command": "npm run dev", "since": 5.0}}
        out = _survey(listed, _procs("npm run dev", "pytest"))
        assert [(s["id"], s["kind"], s["pid"]) for s in out] == [
            ("b2", KIND_SERVICE, 100), ("b1", KIND_BATCH, 101)]

    def test_a_shell_whose_output_says_it_ended_is_gone(self):
        out = _survey({"b1": {"command": "pytest"}}, _procs("pytest"), ended=lambda i: 5.0)
        assert out == []

    def test_a_shell_whose_process_went_away_is_gone(self):
        memory = ShellMemory()
        listed = {"b1": {"command": "pytest"}}
        assert _survey(listed, _procs("pytest"), memory)
        assert _survey(listed, [], memory) == []

    def test_no_process_table_keeps_what_was_listed(self):
        out = _survey({"b1": {"command": "pytest"}}, None)
        assert [s["id"] for s in out] == ["b1"] and "pid" not in out[0]

    def test_a_poll_loop_seen_once_stays_a_watcher(self):
        memory = ShellMemory()
        listed = {"b1": {"command": "./check.sh"}}
        _survey(listed, _procs("./check.sh", polls=True), memory)
        assert _survey(listed, _procs("./check.sh"), memory)[0]["kind"] == KIND_WATCHER

    def test_sockets_are_asked_about_only_for_unplaced_shells(self):
        asked = []
        listening = lambda p: asked.append(p.command) or True
        out = _survey({"b1": {"command": "./app"}, "b2": {"command": "npm run dev"},
                       "b3": {"command": "overcode follow kid"}},
                      _procs("./app", "npm run dev", "overcode follow kid"), listening=listening)
        assert asked == ["./app"]
        assert out[0]["kind"] == KIND_SERVICE

    def test_a_poll_loop_on_an_ended_job_is_stuck_after_the_grace(self):
        listed = {"b1": {"command": POLL, "since": 0.0}}
        memory = ShellMemory()
        ended = lambda k, n: (TARGET_ENDED, 500.0)
        early = _survey(listed, _procs(POLL), memory, now=500.0 + STUCK_GRACE_SECONDS - 1, target=ended)
        assert early[0]["target"] == "job build" and "stuck_since" not in early[0]
        late = _survey(listed, _procs(POLL), memory, now=500.0 + STUCK_GRACE_SECONDS, target=ended)
        assert late[0]["stuck_since"] == 500.0 + STUCK_GRACE_SECONDS
        assert late[0]["target_state"] == TARGET_ENDED

    def test_a_job_that_never_existed_counts_from_when_it_was_first_seen_missing(self):
        listed = {"b1": {"command": POLL}}
        memory = ShellMemory()
        missing = lambda k, n: (TARGET_MISSING, None)
        _survey(listed, _procs(POLL), memory, now=1000.0, target=missing)
        out = _survey(listed, _procs(POLL), memory, now=1000.0 + STUCK_GRACE_SECONDS, target=missing)
        assert out[0]["stuck_since"] == 1000.0 + STUCK_GRACE_SECONDS

    def test_a_watcher_that_ends_with_its_target_is_never_stuck(self):
        for command in ("overcode jobs wait build", "overcode follow kid", "overcode jobs tail build"):
            out = _survey({"b1": {"command": command}}, _procs(command), now=10_000.0,
                          target=lambda k, n: (TARGET_ENDED, 0.0))
            assert "stuck_since" not in out[0], command

    def test_a_target_back_alive_clears_the_clock(self):
        listed = {"b1": {"command": POLL}}
        memory = ShellMemory()
        _survey(listed, _procs(POLL), memory, now=0.0, target=lambda k, n: (TARGET_MISSING, None))
        _survey(listed, _procs(POLL), memory, now=50.0, target=lambda k, n: (TARGET_ALIVE, None))
        out = _survey(listed, _procs(POLL), memory, now=200.0, target=lambda k, n: (TARGET_MISSING, None))
        assert "stuck_since" not in out[0]


class TestShellProcs:
    def test_finds_shells_and_their_sleep_children(self):
        argv = {1: "claude", 2: "claude bg-spare", 3: _wrapper(POLL), 4: "sleep 3",
                5: _wrapper("npm run dev"), 6: "node vite"}
        children = {1: [2], 2: [3, 5], 3: [4], 5: [6]}
        procs = {p.pid: p for p in shell_procs(argv, children, argv.keys())}
        assert set(procs) == {3, 5}
        assert procs[3].polls and not procs[5].polls
        assert procs[5].pids == (5, 6)


class TestHookSide:
    def _stop(self, sw, tasks, now):
        return swarm.apply_parent_event({}, sw, "Stop", None, None, {"background_tasks": tasks}, now)[1]

    def test_stop_keeps_the_running_shells_and_when_each_was_first_listed(self):
        sw = self._stop({}, [{"id": "b1", "type": "shell", "status": "running", "command": "pytest",
                              "description": "Run tests"},
                             {"id": "a1", "type": "subagent", "status": "running"}], 100.0)
        assert sw["bg_shells"] == {"b1": {"since": 100.0, "command": "pytest", "description": "Run tests"}}
        sw = self._stop(sw, [{"id": "b1", "type": "shell", "status": "running", "command": "pytest"},
                             {"id": "b2", "type": "shell", "status": "running", "command": "npm run dev"}], 200.0)
        assert sw["bg_shells"]["b1"]["since"] == 100.0 and sw["bg_shells"]["b2"]["since"] == 200.0
        assert "bg_shells" not in self._stop(sw, [], 300.0)

    def test_a_task_notification_drops_the_shell(self):
        sw = self._stop({}, [{"id": "b1", "type": "shell", "status": "running", "command": "x"}], 1.0)
        prompt = ("<task-notification><task-id>b1</task-id><status>completed</status>"
                  "</task-notification>")
        _, sw = swarm.apply_parent_event({}, sw, "UserPromptSubmit", None, None, {"prompt": prompt}, 2.0)
        assert not sw.get("bg_shells")


def _armed(shells, bar=0):
    ctx = TickContext({"event": "Stop", "timestamp": time.time()}, now=time.time(), shells=bar)
    ctx.bg_shells = shells
    return decide(ctx)[0]


class TestArmedRule:
    def test_classified_shells_get_their_own_badges(self):
        v = _armed([{"kind": "batch", "pid": 1}, {"kind": "service", "pid": 2},
                    {"kind": "service", "pid": 3}], bar=4)
        assert v.color == STATUS_COLOR_YELLOW
        assert [(b.kind, b.count) for b in v.badges] == [
            ("bg_batch", 1), ("bg_service", 2), ("bg_task", 1)]
        assert v.activity == "Waiting on 1 background shell, 2 services, 1 other shell"

    def test_a_service_alone_still_arms_the_agent(self):
        v = _armed([{"kind": "service", "pid": 1}])
        assert v.color == STATUS_COLOR_YELLOW
        assert [b.kind for b in v.badges] == ["bg_service"]

    def test_shells_with_no_process_count_only_up_to_the_status_bar(self):
        assert _armed([{"kind": "batch"}, {"kind": "batch"}], bar=0).color != STATUS_COLOR_YELLOW
        v = _armed([{"kind": "batch"}, {"kind": "watcher"}], bar=1)
        assert [(b.kind, b.count) for b in v.badges] == [("bg_batch", 1)]


class _FakeProbe:
    def __init__(self, procs, target):
        self._procs, self._target = procs, target

    def processes(self, session):
        return self._procs

    def target_state(self, kind, name):
        return self._target

    def listening(self, proc):
        return False


class TestDetector:
    def test_a_stuck_watcher_gets_a_warning_beside_the_yellow(self, tmp_path):
        state_dir = tmp_path / "sessions" / "agents"
        state_dir.mkdir(parents=True)
        now = time.time()
        (state_dir / "hook_state_a1.json").write_text(json.dumps({
            "event": "Stop", "timestamp": now,
            "swarm": {"bg_at": now, "bg_shells": {"b1": {"command": POLL, "since": now - 600}}},
        }))
        detector = HookStatusDetector("agents", tmux=create_mock_tmux_with_content("agents", 1, ""),
                                      state_dir=state_dir)
        detector.shell_ended = lambda shell_id: None
        detector.shell_probe = _FakeProbe(_procs(POLL, polls=True), (TARGET_ENDED, now - 300))
        detector.detect_status(create_mock_session(tmux_window=1, name="a1"))

        detail = detector.get_status_detail("a1")
        assert detail.color == STATUS_COLOR_YELLOW
        assert [b.kind for b in detail.badges] == ["bg_watcher", "bg_stuck"]
        assert detail.badges[1].label == "job build"
        [shell] = detector.get_background_shells("a1")
        assert shell["kind"] == KIND_WATCHER and shell["stuck_since"] == now - 300 + STUCK_GRACE_SECONDS

    def test_no_listed_shells_costs_no_probe(self, tmp_path):
        state_dir = tmp_path / "sessions" / "agents"
        state_dir.mkdir(parents=True)
        (state_dir / "hook_state_a1.json").write_text(json.dumps({"event": "Stop", "timestamp": time.time()}))
        detector = HookStatusDetector("agents", tmux=create_mock_tmux_with_content("agents", 1, ""),
                                      state_dir=state_dir)
        detector.shell_probe = None  # would raise if asked
        detector.detect_status(create_mock_session(tmux_window=1, name="a1"))
        assert detector.get_background_shells("a1") is None


class TestShellProbe:
    def _table(self):
        argv = {10: "claude --session-id x", 11: _wrapper("pytest"), 20: "claude daemon",
                21: _wrapper("npm run dev"), 22: _wrapper("make"), 23: _wrapper("make")}
        children = {10: [11], 20: [21, 22, 23]}
        return SimpleNamespace(argv_by_pid=argv, children=children)

    def test_shells_under_the_agent_then_unique_ones_elsewhere(self):
        table = self._table()
        probe = ShellProbe(table=lambda: table, agent_pid=lambda t, s: 10)
        procs = probe.processes(SimpleNamespace(name="a"))
        # 'make' runs twice outside any agent: whose it is can't be told
        assert sorted(p.command for p in procs) == ["npm run dev", "pytest"]

    def test_no_table_is_unknown(self):
        assert ShellProbe(table=lambda: None, agent_pid=lambda t, s: 1).processes(None) is None

    def test_targets(self):
        jobs = {"build": {"status": "failed", "end_time": "2026-10-26T10:00:00"},
                "srv": {"status": "running"}}
        probe = ShellProbe(table=lambda: None, agent_pid=lambda t, s: None, load_jobs=lambda: jobs)
        probe.begin_tick({"kid": "done", "other": "running"})
        assert probe.target_state("agent", "kid") == (TARGET_ENDED, None)
        assert probe.target_state("agent", "other") == (TARGET_ALIVE, None)
        assert probe.target_state("agent", "gone") == (TARGET_MISSING, None)
        state, at = probe.target_state("job", "build")
        assert state == TARGET_ENDED and at is not None
        assert probe.target_state("job", "srv") == (TARGET_ALIVE, None)
        assert probe.target_state("job", "nope") == (TARGET_MISSING, None)

    def test_listening_is_cached(self):
        calls = []
        clock = [0.0]
        probe = ShellProbe(table=lambda: None, agent_pid=lambda t, s: None,
                           listening_pids=lambda pids: calls.append(pids) or set(),
                           clock=lambda: clock[0])
        proc = ShellProc(5, "x", (5, 6))
        assert not probe.listening(proc) and not probe.listening(proc)
        clock[0] = 61.0
        probe.listening(proc)
        assert len(calls) == 2


class TestShellsCommand:
    runner = CliRunner()

    def _state(self, shells):
        from overcode.monitor_daemon_state import MonitorDaemonState, SessionDaemonState
        return MonitorDaemonState(sessions=[SessionDaemonState(session_id="i", name="a1",
                                                               background_shells=shells)])

    def _run(self, shells, args, stop=None):
        from overcode.cli._shared import app
        import overcode.cli  # noqa: F401  (registers the commands)
        with patch("overcode.monitor_daemon_state.get_monitor_daemon_state",
                   return_value=self._state(shells)), \
             patch("overcode.cli.shells._stop_tree", stop or (lambda pid, cmd: None)) as _:
            return self.runner.invoke(app, ["shells", *args])

    def test_lists_shells_and_flags_stuck_ones(self):
        out = self._run([{"id": "b1", "kind": "watcher", "command": POLL, "since": time.time() - 60,
                          "pid": 42, "target": "job build", "target_state": "ended",
                          "stuck_since": time.time()}], [])
        assert out.exit_code == 0
        assert "a1" in out.output and "stuck" in out.output and "pid 42" in out.output

    def test_kill_stops_the_shell_it_names(self):
        stopped = []
        out = self._run([{"id": "b1", "kind": "watcher", "command": POLL, "pid": 42}], ["--kill", "b1"],
                        stop=lambda pid, cmd: stopped.append((pid, cmd)))
        assert out.exit_code == 0 and stopped == [(42, POLL)]

    def test_kill_refuses_an_unknown_shell(self):
        assert self._run([], ["--kill", "42"]).exit_code == 1


class TestStopTree:
    def test_refuses_a_pid_that_is_no_longer_that_shell(self):
        from overcode.cli.shells import _stop_tree
        from overcode.process_resources import ProcInfo
        table = {42: ProcInfo(ppid=1, cpu_pct=0, rss_kb=0, argv=_wrapper("something else"))}
        with patch("overcode.process_resources.snapshot_processes", return_value=table), \
             patch("os.kill") as kill:
            assert "no longer" in _stop_tree(42, POLL)
        kill.assert_not_called()

    def test_stops_children_first(self):
        from overcode.cli.shells import _stop_tree
        from overcode.process_resources import ProcInfo
        table = {42: ProcInfo(1, 0, 0, _wrapper(POLL)), 43: ProcInfo(42, 0, 0, "sleep 3")}
        with patch("overcode.process_resources.snapshot_processes", return_value=table), \
             patch("os.kill") as kill:
            assert _stop_tree(42, POLL) is None
        assert [c.args[0] for c in kill.call_args_list] == [43, 42]
