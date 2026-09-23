"""
Live tier: opencode status transitions against the REAL CLI (#474).

``test_live_backends.py`` proves one turn and one permission dialog per
backend. This walk goes further for opencode, driving the scenarios that
were mis-reported before #474 — a ``task`` sub-agent (with and without a
permission ask of its own), a double-Escape interrupt, ``/new``, a queued
prompt, ``overcode restart`` (resume), ``/exit``, and a provider error —
while a spy plugin (``tests/js/opencode_spy_plugin.js``) records every hook
opencode fires. Three things are checked:

1. **The plugin agrees with the oracle.** Every publish the bundled
   telemetry plugin made is compared record-by-record with
   ``tests.opencode_oracle`` over the spy's verbatim stream — the same check
   the unit corpus runs, now on whatever opencode is installed.
2. **The daemon shows the right thing.** At each settle point the daemon's
   ``current_state`` (what the TUI renders) must match.
3. **Exit is noticed.** After ``/exit`` the daemon must reach ``terminated``.

Opt in with ``OVERCODE_LIVE_BACKENDS=opencode`` (spends a few cents of
``openai/gpt-4o-mini``). The spy log is left in the state dir named in the
failure message so a new opencode build's vocabulary can be diffed against
``tests/fixtures_opencode_events/`` and, if it moved, re-captured from here.
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

import pytest

from tests.opencode_oracle import diff_publishes

pytestmark = [pytest.mark.e2e, pytest.mark.live]

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
SPY_PLUGIN = _PROJECT_ROOT / "tests" / "js" / "opencode_spy_plugin.js"
TEST_TMUX_SOCKET = "overcode-live-oc"
SESSION = "live-oc-states"
AGENT = "oc-states"
TURN_TIMEOUT = 90.0
MODEL = os.environ.get("OVERCODE_LIVE_OPENCODE_MODEL", "openai/gpt-4o-mini")


def _requested() -> bool:
    return "opencode" in {
        b.strip() for b in os.environ.get("OVERCODE_LIVE_BACKENDS", "").split(",") if b.strip()
    }


def _skip_reason() -> Optional[str]:
    if not _requested():
        return "opencode not in OVERCODE_LIVE_BACKENDS"
    if shutil.which("tmux") is None:
        return "tmux not available"
    binary = os.environ.get("OVERCODE_LIVE_OPENCODE_COMMAND") or "opencode"
    if not shutil.which(binary):
        return f"{binary} not on PATH"
    if not os.environ.get("OPENAI_API_KEY") and not (
        Path.home() / ".local" / "share" / "opencode" / "auth.json"
    ).exists():
        return "no opencode credentials (OPENAI_API_KEY or opencode auth store)"
    return None


@pytest.fixture
def live():
    reason = _skip_reason()
    if reason:
        pytest.skip(reason)
    state_dir = Path(tempfile.mkdtemp(prefix="overcode-live-oc-states-"))
    workdir = Path(tempfile.mkdtemp(prefix="overcode-live-oc-proj-"))
    spy_log = state_dir / "spy.jsonl"

    (workdir / "README.md").write_text("# live-project\n\nThe first line is the title.\n")
    (workdir / "opencode.json").write_text(json.dumps({
        "$schema": "https://opencode.ai/config.json",
        "model": MODEL,
        "permission": {"bash": "ask"},
    }, indent=2))
    subprocess.run(["git", "init", "-q", str(workdir)], capture_output=True)
    plugins = workdir / ".opencode" / "plugins"
    plugins.mkdir(parents=True)
    (plugins / "overcode-spy.js").write_text(
        SPY_PLUGIN.read_text(encoding="utf-8").replace("__SPY_LOG_PATH__", str(spy_log)),
        encoding="utf-8",
    )

    env = os.environ.copy()
    for key in list(env):
        if key.endswith("_COMMAND") and key.split("_COMMAND")[0] in (
            "CLAUDE", "OPENCODE", "OPENCODE2", "CODEX", "GROK", "HERMES",
        ):
            env.pop(key)
    override = os.environ.get("OVERCODE_LIVE_OPENCODE_COMMAND")
    if override:
        env["OPENCODE_COMMAND"] = override
        env["PATH"] = str(Path(override).absolute().parent) + os.pathsep + env.get("PATH", "")
    for key in [
        "OVERCODE_SESSION_NAME", "OVERCODE_SESSION_ID", "OVERCODE_TMUX_SESSION",
        "OVERCODE_PARENT_SESSION_ID", "OVERCODE_PARENT_NAME",
        "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "PYTEST_CURRENT_TEST", "PYTEST_ADDOPTS",
    ]:
        env.pop(key, None)
    env["OVERCODE_STATE_DIR"] = str(state_dir)
    env["OVERCODE_TMUX_SOCKET"] = TEST_TMUX_SOCKET
    env["PYTHONPATH"] = str(_PROJECT_ROOT / "src")

    subprocess.run(["tmux", "-L", TEST_TMUX_SOCKET, "kill-session", "-t", SESSION], capture_output=True)
    daemon = subprocess.Popen(
        ["python", "-m", "overcode.cli", "monitor-daemon", "start",
         "--interval", "1", "--session", SESSION],
        env=env, cwd=_PROJECT_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        yield {"state_dir": state_dir, "workdir": workdir, "env": env, "spy_log": spy_log}
    finally:
        for name in (AGENT, AGENT + "-err"):
            subprocess.run(["python", "-m", "overcode.cli", "kill", name, "--session", SESSION],
                           env=env, capture_output=True, cwd=_PROJECT_ROOT, timeout=60)
        subprocess.run(["python", "-m", "overcode.cli", "monitor-daemon", "stop", "--session", SESSION],
                       env=env, capture_output=True, cwd=_PROJECT_ROOT, timeout=60)
        try:
            daemon.wait(timeout=10)
        except subprocess.TimeoutExpired:
            daemon.kill()
        subprocess.run(["tmux", "-L", TEST_TMUX_SOCKET, "kill-session", "-t", SESSION], capture_output=True)
        shutil.rmtree(workdir, ignore_errors=True)
        # The state dir (spy log, hook files, daemon log) is kept for post-mortem.


class Driver:
    def __init__(self, live):
        self.live = live
        self.env = live["env"]
        self.state_dir = live["state_dir"]

    # ── overcode / tmux plumbing ─────────────────────────────────────────
    def cli(self, *args, timeout=120):
        return subprocess.run(
            ["python", "-m", "overcode.cli", *args, "--session", SESSION],
            capture_output=True, text=True, timeout=timeout, env=self.env, cwd=_PROJECT_ROOT,
        )

    def tmux(self, *args):
        return subprocess.run(["tmux", "-L", TEST_TMUX_SOCKET, *args], capture_output=True, text=True)

    def window(self, agent=AGENT):
        out = self.tmux("list-windows", "-t", SESSION, "-F", "#{window_name}").stdout
        for w in out.splitlines():
            if w.startswith(f"{agent}-"):
                return w
        raise AssertionError(f"no window for {agent!r}: {out!r}")

    def pane(self, agent=AGENT, lines=80):
        return self.tmux("capture-pane", "-p", "-t", f"{SESSION}:{self.window(agent)}", "-S", f"-{lines}").stdout

    def send(self, text, agent=AGENT):
        w = self.window(agent)
        self.tmux("send-keys", "-t", f"{SESSION}:{w}", "-l", text)
        time.sleep(0.15)
        self.tmux("send-keys", "-t", f"{SESSION}:{w}", "Enter")

    def key(self, name, agent=AGENT):
        self.tmux("send-keys", "-t", f"{SESSION}:{self.window(agent)}", name)

    def hook_state(self, agent=AGENT):
        try:
            return json.loads((self.state_dir / SESSION / f"hook_state_{agent}.json").read_text())
        except (OSError, ValueError):
            return None

    def hook_events(self, agent=AGENT):
        try:
            lines = (self.state_dir / SESSION / f"hook_events_{agent}.jsonl").read_text().splitlines()
        except OSError:
            return []
        return [json.loads(l) for l in lines if l.strip()]

    def daemon_state(self, agent=AGENT):
        try:
            rows = json.loads((self.state_dir / "sessions" / "sessions.json").read_text())
        except (OSError, ValueError):
            return None
        rows = rows.values() if isinstance(rows, dict) else rows
        for row in rows:
            if isinstance(row, dict) and row.get("name") == agent:
                return (row.get("stats") or {}).get("current_state")
        return None

    # ── waits ────────────────────────────────────────────────────────────
    def wait_pane(self, *markers, agent=AGENT, timeout=TURN_TIMEOUT, absent=False):
        deadline = time.monotonic() + timeout
        content = ""
        while time.monotonic() < deadline:
            content = self.pane(agent)
            if all((m in content) != absent for m in markers):
                return content
            time.sleep(0.2)
        raise AssertionError(f"pane never showed {markers!r} (absent={absent}):\n{content}")

    def wait_hook(self, *events, after, agent=AGENT, timeout=TURN_TIMEOUT):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            state = self.hook_state(agent)
            if state and state.get("event") in events and float(state.get("timestamp", 0)) > after:
                return state
            last = state
            time.sleep(0.2)
        raise AssertionError(
            f"hook state never showed {events!r} after {after}; last={last}; spy log: {self.live['spy_log']}\n{self.pane(agent)}"
        )

    def wait_daemon(self, expected, agent=AGENT, timeout=30.0):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            last = self.daemon_state(agent)
            if last == expected:
                return last
            time.sleep(0.5)
        raise AssertionError(f"daemon never reported {expected!r} for {agent}; last={last!r}")

    def settle(self, after, agent=AGENT, timeout=TURN_TIMEOUT):
        self.wait_pane("esc interrupt", agent=agent, timeout=timeout, absent=True)
        state = self.wait_hook("Stop", "StopFailure", after=after, agent=agent, timeout=timeout)
        return state


def _spy_records(path: Path):
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [json.loads(l) for l in lines if l.strip()]


class TestLiveOpencodeStates:

    def test_state_walk(self, live):
        d = Driver(live)

        # Launch through overcode so the real telemetry plugin is installed
        # alongside the spy, with the real env prefix.
        res = d.cli("launch", "--name", AGENT, "--backend", "opencode",
                    "--directory", str(live["workdir"]), timeout=120)
        assert res.returncode == 0 and "Cannot launch" not in res.stdout, res.stdout + res.stderr
        d.tmux("resize-window", "-t", f"{SESSION}:{d.window()}", "-x", "160", "-y", "50")
        d.wait_pane("╹▀", timeout=60)
        time.sleep(1.5)

        # 1. Text-only turn.
        t = time.time()
        d.send("Reply with the single word pong and nothing else.")
        d.settle(t)
        assert d.wait_daemon("waiting_user") == "waiting_user"

        # 2. Sub-agent: the whole child turn happens inside the parent's Task
        #    call. No Stop may land between PreToolUse[Task] and PostToolUse[Task].
        n_before = len(d.hook_events())
        t = time.time()
        d.send("Use the task tool with subagent_type general to read README.md and report its "
               "first line to me. Do not read the file yourself; delegate it.")
        d.settle(t, timeout=150)
        turn = d.hook_events()[n_before:]
        names = [e["event"] for e in turn]
        tools = [e.get("tool_name") for e in turn if e.get("tool_name")]
        assert "Task" in tools, turn
        first_task = next(i for i, e in enumerate(turn) if e.get("tool_name") == "Task")
        last_task = max(i for i, e in enumerate(turn) if e.get("tool_name") == "Task")
        assert "Stop" not in names[first_task:last_task], turn
        assert names[-1] == "Stop"
        assert "Read" not in tools and "Glob" not in tools, "child tool calls leaked to the parent"
        state = d.hook_state()
        assert len(state.get("agent_session_ids") or []) == 1, "child session id leaked into agent_session_ids"

        # 3. Sub-agent asking permission: must surface, then settle.
        t = time.time()
        d.send("Use the task tool with subagent_type general to run the bash command `echo child-ok` "
               "and report its output to me. Do not run it yourself; delegate it.")
        d.wait_pane("Permission required", timeout=120)
        d.wait_hook("PermissionRequest", after=t)
        assert d.wait_daemon("waiting_approval") == "waiting_approval"
        d.key("Enter")
        d.settle(t, timeout=120)
        assert d.wait_daemon("waiting_user") == "waiting_user"

        # 4. Interrupt: double-Escape mid-generation is Stop, never StopFailure.
        n_before = len(d.hook_events())
        t = time.time()
        d.send("Write a 300 line poem about the sea, one short line per line, no headings.")
        d.wait_pane("esc interrupt")
        time.sleep(2.0)
        d.key("Escape")
        time.sleep(0.4)
        d.key("Escape")
        d.settle(t)
        assert "StopFailure" not in [e["event"] for e in d.hook_events()[n_before:]]
        assert d.wait_daemon("waiting_user") == "waiting_user"

        # 5. /new then a turn, 6. a queued prompt.
        d.send("/new")
        time.sleep(2.0)
        t = time.time()
        d.send("Reply with the single word pong and nothing else.")
        d.settle(t)
        t = time.time()
        d.send("Count from 1 to 30, one number per line.")
        time.sleep(1.0)
        d.send("Now reply with the single word done.")
        d.settle(t)
        d.wait_pane("done")
        assert d.wait_daemon("waiting_user") == "waiting_user"

        # 7. Restart (resume): a fresh process with no session.created.
        res = d.cli("restart", AGENT, timeout=120)
        assert res.returncode == 0, res.stdout + res.stderr
        d.wait_pane("╹▀", timeout=60)
        time.sleep(1.5)
        t = time.time()
        d.send("Reply with the single word pong-again and nothing else.")
        d.settle(t)
        assert d.wait_daemon("waiting_user") == "waiting_user"

        # 8. The whole stream vs the oracle. Spy records from both processes
        #    (before and after the restart) are one file; the second process
        #    is a fresh plugin, so check the two halves separately.
        records = _spy_records(live["spy_log"])
        loads = [i for i, r in enumerate(records) if r.get("hook") == "__load__"]
        assert len(loads) == 2, f"expected two plugin loads (launch + restart), saw {len(loads)}"
        halves = [records[loads[0]:loads[1]], records[loads[1]:]]
        published = d.hook_events()
        self._check_against_oracle(halves, published, live["spy_log"])

        # 9. Exit: opencode emits no bus event; the daemon must still notice.
        d.send("/exit")
        assert d.wait_daemon("terminated", timeout=40) == "terminated"

    def test_provider_error_is_visible(self, live):
        d = Driver(live)
        errdir = Path(tempfile.mkdtemp(prefix="overcode-live-oc-err-"))
        try:
            (errdir / "opencode.json").write_text(json.dumps({
                "$schema": "https://opencode.ai/config.json",
                "model": MODEL,
                "provider": {"openai": {"options": {"apiKey": "sk-invalid-key-for-overcode-live-test"}}},
            }))
            subprocess.run(["git", "init", "-q", str(errdir)], capture_output=True)
            name = AGENT + "-err"
            res = d.cli("launch", "--name", name, "--backend", "opencode", "--directory", str(errdir), timeout=120)
            assert res.returncode == 0 and "Cannot launch" not in res.stdout, res.stdout + res.stderr
            d.wait_pane("╹▀", agent=name, timeout=60)
            time.sleep(1.5)
            t = time.time()
            d.send("Reply with the single word pong.", agent=name)
            state = d.wait_hook("StopFailure", "Stop", after=t, agent=name, timeout=60)
            assert state["event"] == "StopFailure", f"error was overwritten: {d.hook_events(name)}"
            assert "API" in (state.get("error") or ""), state
            assert d.wait_daemon("error", agent=name) == "error"
        finally:
            shutil.rmtree(errdir, ignore_errors=True)

    @staticmethod
    def _check_against_oracle(halves, published, spy_log):
        """Align the plugin's event log with the oracle over each process's stream.

        The plugin's log carries no record index, so alignment is by
        sequence: the oracle's flattened expected publishes must equal the
        plugin's flattened publishes, and the per-record diff pinpoints the
        first divergence.
        """
        from tests.opencode_oracle import OpencodeOracle
        expected = []
        for half in halves:
            oracle = OpencodeOracle()
            for rec in half:
                expected.extend((p.event, p.tool) for p in oracle.step(rec))
        got = [(e["event"], e.get("tool_name")) for e in published]
        if expected != got:
            # Rebuild a per-record view for the message.
            per_record = []
            cursor = 0
            for half in halves:
                oracle = OpencodeOracle()
                for rec in half:
                    want = oracle.step(rec)
                    take = got[cursor:cursor + len(want)]
                    cursor += len(want)
                    per_record.append([{"event": ev, "tool_name": tool} for ev, tool in take])
            problems = diff_publishes([r for h in halves for r in h], per_record)
            raise AssertionError(
                f"plugin diverged from the oracle (spy log: {spy_log}):\n  " + "\n  ".join(problems[:10])
            )
