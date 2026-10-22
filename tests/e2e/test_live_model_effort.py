"""
Live: how fast, and how truthfully, a Claude agent's model and effort reach the view (#539).

A real Claude agent runs under overcode in an isolated state dir with the
monitor daemon up. The test connects to the engine's socket as a visible
view (what the TUI does), so it sees each value the moment the engine
publishes it, and times every change against the truth: what Claude
confirmed on screen for a setting, and what its transcript records for a
reply.

a. launch on haiku
f. ``/model sonnet`` with no reply after it; b. then one reply
c. ``/effort`` to another level with no reply; then one reply
d. ``/clear`` (a new conversation); then one reply
e. ``overcode restart`` (resume) while another agent's ``/effort`` has
   changed the user's saved default; then ``restart --model haiku``
g. after ``restart --model haiku``, ``/model sonnet`` back to the model the
   old transcript ended on

Opt-in like the rest of the live tier (eight one-word replies on haiku and
sonnet)::

    OVERCODE_LIVE_BACKENDS=claude-code pytest tests/e2e/test_live_model_effort.py -m e2e -s

``/model`` and ``/effort`` inside Claude also save the user's global default
(``model`` / ``effortLevel`` in ``~/.claude/settings.json``), and step e
sets ``effortLevel`` itself; the ``global_claude_settings`` fixture puts
both keys back afterwards.
"""

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import pytest

from conftest import TEST_TMUX_SOCKET, get_tmux_pane_content
from test_live_backends import _BY_NAME, _PROJECT_ROOT, live_env, spec  # noqa: F401 (fixtures)

pytestmark = pytest.mark.live

NAME = "live-claude-code"  # what live_env's teardown kills
SETTINGS = Path.home() / ".claude" / "settings.json"
_KEYS = ("model", "effortLevel")
# One attended stats sync is 5 s; a reply must show well within this
SHOW_TIMEOUT = 30.0
# A setting with no reply after it: watched this long
SETTLE = 15.0
PROMPT = "Reply with the single word pong and nothing else."


@pytest.fixture
def global_claude_settings():
    """Put ``model``/``effortLevel`` in ~/.claude/settings.json back as they were."""
    try:
        before = json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        before = None
    yield
    if before is None:
        return
    try:
        now = json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        return
    changed = False
    for key in _KEYS:
        if now.get(key) != before.get(key):
            changed = True
            if key in before:
                now[key] = before[key]
            else:
                now.pop(key, None)
    if changed:
        SETTINGS.write_text(json.dumps(now, indent=2) + "\n")


def set_global_effort(level: str) -> None:
    """What another agent's ``/effort <level>`` does to the saved default."""
    data = json.loads(SETTINGS.read_text())
    data["effortLevel"] = level
    SETTINGS.write_text(json.dumps(data, indent=2) + "\n")


class EngineWatch:
    """A visible view on engine.sock that timestamps the agent's published model and effort."""

    def __init__(self, tmux_session: str):
        from overcode.engine_socket import EngineClient
        from overcode.tui_engine import engine_socket_path

        self.lock = threading.Lock()
        self.events: List[Tuple[float, tuple]] = []
        self.current: Optional[tuple] = None
        self.client = EngineClient(engine_socket_path(tmux_session), self._on_change)
        self.client.set_visible(True)
        self.client.start()

    def _on_change(self, snapshot) -> None:
        for fields in snapshot.agents.values():
            if fields.get("name") != NAME:
                continue
            now = (fields.get("model"), fields.get("effort"))
            with self.lock:
                if now != self.current:
                    self.current = now
                    self.events.append((time.monotonic(), now))

    def value(self) -> tuple:
        with self.lock:
            return self.current or (None, None)

    def wait(self, check: Callable[[tuple], bool], timeout: float) -> Optional[float]:
        """Seconds until ``check`` holds on the published view, or None."""
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            if check(self.value()):
                return time.monotonic() - start
            time.sleep(0.1)
        return None

    def stop(self) -> None:
        self.client.stop()


@pytest.mark.parametrize("spec", [_BY_NAME["claude-code"]], indirect=True, ids=["claude-code"])
def test_model_and_effort_follow_the_agent(spec, live_env, global_claude_settings):
    env, session, workdir = live_env["env"], live_env["session"], live_env["workdir"]
    state_dir = Path(live_env["state_dir"])
    rows: List[Tuple[str, str, bool]] = []

    def cli(*args, timeout=120):
        return subprocess.run(["python", "-m", "overcode.cli", *args, "--session", session],
                              capture_output=True, text=True, timeout=timeout, env=env,
                              cwd=_PROJECT_ROOT)

    def window() -> str:
        out = subprocess.run(["tmux", "-L", TEST_TMUX_SOCKET, "list-windows", "-t", session,
                              "-F", "#{window_name}"], capture_output=True, text=True).stdout
        return next((w for w in out.splitlines() if w.startswith(f"{NAME}-")), "")

    def pane() -> str:
        return get_tmux_pane_content(TEST_TMUX_SOCKET, session, window(), lines=60) or ""

    def keys(*args, literal=False):
        cmd = ["tmux", "-L", TEST_TMUX_SOCKET, "send-keys", "-t", f"{session}:{window()}"]
        if literal:
            cmd.append("-l")
        subprocess.run([*cmd, *args], check=True)

    def agent_file(kind: str) -> Optional[Path]:
        from tests.agent_file_lookup import agent_state_file

        try:
            return agent_state_file(state_dir, session, NAME, kind)
        except Exception:  # noqa: BLE001 — not written yet
            return None

    def hook_state() -> dict:
        try:
            return json.loads(agent_file("hook_state").read_text())
        except (AttributeError, OSError, ValueError):
            return {}

    def stopped_after(t: float) -> bool:
        try:
            log = agent_file("hook_events").read_text()
        except (AttributeError, OSError):
            return False
        for line in log.splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("event") == "Stop" and float(row.get("timestamp") or 0) > t:
                return True
        return False

    def truth() -> Tuple[Optional[str], Optional[str]]:
        """The model and effort of the agent's latest reply, from its transcript."""
        sid = hook_state().get("agent_session_id")
        found = list((Path.home() / ".claude" / "projects").glob(f"*/{sid}.jsonl"))
        model = effort = None
        for line in (found[0].read_text().splitlines() if found else []):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("type") == "assistant" and (row.get("message") or {}).get("usage"):
                model, effort = row["message"].get("model"), row.get("effort")
        return model, effort

    def wait(what, check, timeout=120.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = check()
            if value:
                return value
            time.sleep(0.5)
        raise AssertionError(f"timed out waiting for {what}; view={watch.value()}\n{pane()}")

    def ready():
        """The CLI is up and idle at its prompt (after a launch or restart)."""
        wait("the prompt box", lambda: "❯" in pane() and "Claude Code" in pane(), 60)
        time.sleep(2)

    def slash(command: str, confirm: Optional[str]):
        """Type a slash command into the idle agent and wait for Claude to confirm it."""
        wait("the prompt box", lambda: "❯" in pane(), 30)
        seen = pane().lower().count(confirm.lower()) if confirm else 0
        keys(command, literal=True)
        time.sleep(0.8)
        keys("Enter")
        if not confirm:
            return
        deadline = time.monotonic() + 30
        nudged = False
        while time.monotonic() < deadline:
            text = pane()
            if text.lower().count(confirm.lower()) > seen:
                return
            if "1. Yes" in text:  # /effort asks before dropping the cache
                keys("Enter")
                time.sleep(1)
            elif not nudged and time.monotonic() > deadline - 20:
                keys("Enter")  # the completion menu took the first Enter
                nudged = True
            time.sleep(0.5)
        raise AssertionError(f"{command!r} never confirmed:\n{pane()}")

    def turn() -> None:
        t = time.time()
        result = cli("send", NAME, PROMPT)
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
        wait("the turn's Stop", lambda: stopped_after(t), 90)

    def record(label: str, ok: bool, detail: str) -> None:
        rows.append((label, detail, ok))
        print(f"[#539] {'ok  ' if ok else 'FAIL'} {label}: {detail}", flush=True)

    def after_reply(label: str) -> Tuple[Optional[str], Optional[str]]:
        """One reply, then: how long until the view shows that reply's model and effort."""
        turn()
        expected = truth()
        took = watch.wait(lambda v: v == expected, SHOW_TIMEOUT)
        record(label, took is not None,
               (f"{took:4.1f} s after Stop" if took is not None
                else f"not within {SHOW_TIMEOUT:.0f} s")
               + f"; reply ran {expected}, view {watch.value()}")
        return expected

    def setting(label: str, check: Callable[[tuple], bool], want: str) -> tuple:
        """A setting with no reply after it: does the view follow, and how fast?"""
        took = watch.wait(check, SETTLE)
        shown = watch.value()
        record(label, took is not None,
               (f"{took:4.1f} s" if took is not None else f"not within {SETTLE:.0f} s")
               + f"; want {want}, view {shown}")
        return shown

    def unknown_or(expected_effort):
        return lambda effort: effort is None or effort == expected_effort

    watch = EngineWatch(session)
    try:
        # a. Launch on haiku
        result = cli("launch", "--name", NAME, "--backend", "claude-code",
                     "--directory", str(workdir), "--model", "haiku", "--prompt", PROMPT)
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
        took = watch.wait(lambda v: "haiku" in (v[0] or ""), SHOW_TIMEOUT)
        record("a. launch --model haiku: shown at once", took is not None,
               f"view {watch.value()}")
        wait("the first Stop", lambda: stopped_after(0), 120)
        expected = truth()
        took = watch.wait(lambda v: v == expected, SHOW_TIMEOUT)
        record("a. launch: first reply's model/effort", took is not None,
               f"reply ran {expected}, view {watch.value()}")

        # f. /model sonnet with no reply; b. then one reply
        slash("/model sonnet", "Set model to")
        before_effort = watch.value()[1]
        shown = setting("f. /model sonnet, no reply", lambda v: "sonnet" in (v[0] or ""),
                        "a sonnet model")
        reply = after_reply("b. /model sonnet, after one reply")
        record("f. effort shown before that reply was not stale",
               unknown_or(reply[1])(shown[1]),
               f"before /model {before_effort}, shown {shown[1]}, reply ran {reply[1]}")

        # c. /effort to another level with no reply; then one reply
        level = "high" if reply[1] != "high" else "low"
        slash(f"/effort {level}", "Set effort level to")
        setting(f"c. /effort {level}, no reply", lambda v: v[1] == level, level)
        after_reply(f"c. /effort {level}, after one reply")

        # d. /clear: a new conversation in the same process
        before_id = hook_state().get("agent_session_id")
        before = watch.value()
        slash("/clear", None)
        wait("the new conversation id",
             lambda: hook_state().get("agent_session_id") not in (None, before_id), 30)
        time.sleep(SETTLE)
        record("d. /clear, no reply: view unchanged", watch.value() == before,
               f"before {before}, view {watch.value()}")
        after_reply("d. /clear, after one reply")

        # e. restart (resume) after another agent saved a different default effort
        before = watch.value()
        other = "low" if before[1] != "low" else "medium"
        set_global_effort(other)
        result = cli("restart", NAME)
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
        ready()
        record("e. restart, no reply: view unchanged", watch.value() == before,
               f"before {before}, view {watch.value()}")
        reply = after_reply("e. restart, after one reply")
        record("e. restart kept the agent's effort (saved default is another)",
               reply[1] == before[1], f"before {before[1]}, saved default {other}, "
               f"reply ran {reply[1]}")

        # e. restart --model haiku
        result = cli("restart", NAME, "--model", "haiku")
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
        took = watch.wait(lambda v: v[0] == "haiku", SHOW_TIMEOUT)
        record("e. restart --model haiku: shown at once", took is not None,
               f"view {watch.value()}")
        ready()
        after_reply("e. restart --model haiku, after one reply")

        # g. back to the model the pre-restart transcript ended on, no reply
        slash("/model sonnet", "Set model to")
        setting("g. /model sonnet after restart --model, no reply",
                lambda v: "sonnet" in (v[0] or ""), "a sonnet model")
    finally:
        watch.stop()
        print("\n[#539] timeline:", flush=True)
        for t, fields in watch.events:
            print(f"[#539]   +{t - watch.events[0][0]:6.1f} s  {fields}", flush=True)
        print("[#539] summary:", flush=True)
        for label, detail, ok in rows:
            print(f"[#539]   {'ok  ' if ok else 'FAIL'} {label:<58} {detail}", flush=True)
    failed = [label for label, _, ok in rows if not ok]
    assert not failed, f"model/effort wrong or late: {failed}"
