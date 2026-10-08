"""
Live: another Claude in the agent's repo must not become the agent's conversation.

The real-CLI counterpart of ``tests/unit/test_session_identity.py``. An agent
runs on haiku under overcode; then a plain ``claude`` on sonnet, started
outside overcode (as from VS Code or a terminal), takes a prompt in the same
directory. The monitor daemon used to adopt that conversation within one
session-id sync (10 s) and show sonnet on the agent's row after the next
stats sync (60 s). Watched for longer than both.

Opt-in like the rest of the live tier (two short prompts)::

    OVERCODE_LIVE_BACKENDS=claude-code pytest tests/e2e/test_live_session_identity.py -m e2e
"""

import json
import subprocess
import time
from pathlib import Path

import pytest

from conftest import TEST_TMUX_SOCKET, get_tmux_pane_content
from test_live_backends import _BY_NAME, _PROJECT_ROOT, live_env, spec  # noqa: F401 (fixtures)

pytestmark = pytest.mark.live

NAME = "live-claude-code"  # what live_env's teardown kills
AGENT_MODEL, INTRUDER_MODEL = "haiku", "sonnet"
WATCH_SECONDS = 90  # past one session-id sync (10 s) and one stats sync (60 s)


@pytest.mark.xfail(strict=True, reason="daemon adopts the newest history.jsonl entry "
                   "for the directory as the agent's conversation")
@pytest.mark.parametrize("spec", [_BY_NAME["claude-code"]], indirect=True, ids=["claude-code"])
def test_a_second_claude_in_the_repo_is_not_adopted(spec, live_env):
    env, session, workdir = live_env["env"], live_env["session"], live_env["workdir"]
    state_dir = Path(live_env["state_dir"])

    def cli(*args, timeout=120):
        return subprocess.run(["python", "-m", "overcode.cli", *args, "--session", session],
                              capture_output=True, text=True, timeout=timeout, env=env,
                              cwd=_PROJECT_ROOT)

    def record():
        try:
            rows = json.loads((state_dir / "sessions" / "sessions.json").read_text()).values()
        except (OSError, ValueError):
            return {}
        return next((r for r in rows if isinstance(r, dict) and r.get("name") == NAME), {})

    def hook_state():
        try:
            return json.loads((state_dir / session / f"hook_state_{NAME}.json").read_text())
        except (OSError, ValueError):
            return {}

    def wait(what, check, timeout=120.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = check()
            if value:
                return value
            time.sleep(1)
        raise AssertionError(f"timed out waiting for {what}; record={record()}")

    # The agent: haiku, one turn, its model synced from its own transcript.
    result = cli("launch", "--name", NAME, "--backend", "claude-code",
                 "--directory", str(workdir), "--model", AGENT_MODEL,
                 "--prompt", "Reply with the single word pong and nothing else.")
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    wait("the agent's first Stop", lambda: hook_state().get("event") == "Stop")
    own_id = hook_state()["agent_session_id"]
    wait("the agent's model to sync", lambda: AGENT_MODEL in (record().get("model") or ""),
         timeout=90)
    assert record().get("active_agent_session_id") == own_id

    # The intruder: plain claude, no overcode env or hooks, same directory.
    intruder_env = {k: v for k, v in env.items() if not k.startswith("OVERCODE_")}
    subprocess.run(["tmux", "-L", TEST_TMUX_SOCKET, "new-window", "-d", "-t", session,
                    "-n", "intruder", "-c", str(workdir)], check=True, env=intruder_env)
    subprocess.run(["tmux", "-L", TEST_TMUX_SOCKET, "send-keys", "-t", f"{session}:intruder",
                    "env -u OVERCODE_STATE_DIR -u OVERCODE_TMUX_SOCKET "
                    f"claude --model {INTRUDER_MODEL}", "Enter"], check=True)

    def intruder_pane():
        return get_tmux_pane_content(TEST_TMUX_SOCKET, session, "intruder", lines=60)

    def intruder_ready():
        pane = intruder_pane()
        if "❯ No, exit" in pane:  # trust screen (see ClaudeCodeBackend.startup_dialog_rules)
            subprocess.run(["tmux", "-L", TEST_TMUX_SOCKET, "send-keys", "-t",
                            f"{session}:intruder", "Down"])
            time.sleep(0.3)
            subprocess.run(["tmux", "-L", TEST_TMUX_SOCKET, "send-keys", "-t",
                            f"{session}:intruder", "Enter"])
            return False
        return "? for shortcuts" in pane or "Claude Code" in pane

    wait("the intruder's prompt", intruder_ready, timeout=60)
    time.sleep(2)
    subprocess.run(["tmux", "-L", TEST_TMUX_SOCKET, "send-keys", "-t", f"{session}:intruder",
                    "-l", "Reply with the single word ping and nothing else."], check=True)
    time.sleep(0.5)
    subprocess.run(["tmux", "-L", TEST_TMUX_SOCKET, "send-keys", "-t", f"{session}:intruder",
                    "Enter"], check=True)

    history = Path.home() / ".claude" / "history.jsonl"

    def intruder_prompted():
        # Its prompt is in history.jsonl for this directory: exactly what the
        # daemon's directory lookup reads.
        resolved = str(Path(workdir).resolve())
        for line in history.read_text().splitlines()[-50:]:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("project") in (str(workdir), resolved) and entry.get("sessionId") != own_id:
                return entry["sessionId"]
        return None

    intruder_id = wait("the intruder's history.jsonl entry", intruder_prompted, timeout=90)

    deadline = time.monotonic() + WATCH_SECONDS
    while time.monotonic() < deadline:
        row = record()
        assert row.get("active_agent_session_id") == own_id, (
            f"agent moved to {row.get('active_agent_session_id')} (intruder {intruder_id})"
        )
        assert intruder_id not in (row.get("agent_session_ids") or []), row
        assert AGENT_MODEL in (row.get("model") or ""), (
            f"agent shows {row.get('model')!r}, the intruder's model"
        )
        time.sleep(2)

    subprocess.run(["tmux", "-L", TEST_TMUX_SOCKET, "kill-window", "-t", f"{session}:intruder"],
                   capture_output=True)
