"""
E2E Test: Child Agent Delegation via Skill Discovery

Tests the full skill activation pipeline: a parent agent receives generic
high-level instructions (with NO overcode hints), discovers the
delegating-to-agents skill on its own, learns the overcode CLI syntax
(including overcode report), and executes the delegation correctly.

Requires:
- Real Claude CLI (`claude` binary)
- ANTHROPIC_API_KEY env var (for Claude-as-judge)
- tmux available

Run with:
    uv run pytest tests/e2e/test_child_delegation.py -v -s --timeout=660
"""

import json
import os
import shutil
import subprocess
import tempfile
import time

import pytest

# Isolated tmux socket for tests
TEST_TMUX_SOCKET = "overcode-test"

# ── Skip conditions ──────────────────────────────────────────────────────────

pytestmark = [
    pytest.mark.skipif(
        shutil.which("claude") is None,
        reason="claude CLI not available",
    ),
    pytest.mark.skipif(
        shutil.which("tmux") is None,
        reason="tmux not available",
    ),
]


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def test_session_name() -> str:
    # Use "agents" — the default overcode session name. The parent agent will
    # run `overcode launch` for children without --session, which defaults to
    # "agents". If we used a custom name, children would end up in a separate
    # "agents" session. Using "agents" + an isolated tmux socket keeps things
    # self-contained.
    return "agents"


@pytest.fixture
def real_claude_env(test_session_name):
    """Set up a clean test environment using the real Claude binary.

    Unlike clean_test_env, does NOT set CLAUDE_COMMAND — uses the real
    ``claude`` binary.  Strips OVERCODE_* env vars so the launcher's
    auto-parent-detection doesn't pick up the host agent's identity.
    """
    state_dir = tempfile.mkdtemp(prefix="overcode-delegation-test-")

    # Kill any leftover test session
    subprocess.run(
        ["tmux", "-L", TEST_TMUX_SOCKET, "kill-session", "-t", test_session_name],
        capture_output=True,
    )

    env = os.environ.copy()
    # Use real claude — do NOT set CLAUDE_COMMAND
    env["OVERCODE_STATE_DIR"] = state_dir
    env["OVERCODE_TMUX_SOCKET"] = TEST_TMUX_SOCKET

    # Strip host-agent env vars to prevent auto-parent detection and
    # to avoid "cannot launch inside another Claude Code session" error
    for key in [
        "OVERCODE_SESSION_NAME",
        "OVERCODE_TMUX_SESSION",
        "OVERCODE_PARENT_SESSION_ID",
        "OVERCODE_PARENT_NAME",
        "CLAUDECODE",
        "CLAUDE_CODE_ENTRYPOINT",
    ]:
        env.pop(key, None)

    # Save originals for restore
    orig_state_dir = os.environ.get("OVERCODE_STATE_DIR")
    orig_tmux_socket = os.environ.get("OVERCODE_TMUX_SOCKET")
    orig_claudecode = os.environ.get("CLAUDECODE")
    orig_entrypoint = os.environ.get("CLAUDE_CODE_ENTRYPOINT")
    os.environ["OVERCODE_STATE_DIR"] = state_dir
    os.environ["OVERCODE_TMUX_SOCKET"] = TEST_TMUX_SOCKET
    # Must strip from os.environ too — tmux server inherits from it
    os.environ.pop("CLAUDECODE", None)
    os.environ.pop("CLAUDE_CODE_ENTRYPOINT", None)

    try:
        yield {
            "session_name": test_session_name,
            "state_dir": state_dir,
            "env": env,
            "tmux_socket": TEST_TMUX_SOCKET,
        }
    finally:
        # Kill agents via overcode kill (best-effort)
        try:
            sessions_file = os.path.join(state_dir, "sessions", "sessions.json")
            if os.path.exists(sessions_file):
                with open(sessions_file) as f:
                    sessions = json.load(f)
                for sess in sessions.values():
                    name = sess.get("name", "")
                    if name:
                        subprocess.run(
                            [
                                "python", "-m", "overcode.cli", "kill", name,
                                "--session", test_session_name,
                            ],
                            capture_output=True,
                            timeout=10,
                            env=env,
                        )
        except Exception:
            pass

        # Kill tmux session
        subprocess.run(
            ["tmux", "-L", TEST_TMUX_SOCKET, "kill-session", "-t", test_session_name],
            capture_output=True,
        )

        # Restore env
        if orig_state_dir is None:
            os.environ.pop("OVERCODE_STATE_DIR", None)
        else:
            os.environ["OVERCODE_STATE_DIR"] = orig_state_dir
        if orig_tmux_socket is None:
            os.environ.pop("OVERCODE_TMUX_SOCKET", None)
        else:
            os.environ["OVERCODE_TMUX_SOCKET"] = orig_tmux_socket
        if orig_claudecode is not None:
            os.environ["CLAUDECODE"] = orig_claudecode
        if orig_entrypoint is not None:
            os.environ["CLAUDE_CODE_ENTRYPOINT"] = orig_entrypoint

        shutil.rmtree(state_dir, ignore_errors=True)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _read_sessions(state_dir: str) -> dict:
    """Read sessions.json, returning {} on any error."""
    sessions_file = os.path.join(state_dir, "sessions", "sessions.json")
    try:
        with open(sessions_file) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _capture_pane(socket: str, session: str, window: int, lines: int = 50) -> str:
    """Capture tmux pane content for a window."""
    result = subprocess.run(
        [
            "tmux", "-L", socket,
            "capture-pane", "-t", f"{session}:{window}",
            "-p", "-S", f"-{lines}",
        ],
        capture_output=True,
        text=True,
    )
    return result.stdout if result.returncode == 0 else ""


def _last_n_lines(text: str, n: int = 3) -> str:
    """Return last n non-empty lines of text."""
    lines = [l for l in text.splitlines() if l.strip()]
    return "\n".join(lines[-n:])


def _judge_outputs(all_outputs: dict[str, str]) -> dict:
    """Use Claude CLI as judge to evaluate agent outputs.

    Routes through the user's Claude Max subscription instead of requiring
    an API key.  Calls ``claude`` in non-interactive (print) mode.

    Returns a dict with boolean verdicts and an explanation.
    """
    # Truncate each output to avoid blowing context
    truncated = {}
    for name, output in all_outputs.items():
        lines = output.splitlines()
        if len(lines) > 200:
            truncated[name] = "\n".join(lines[:100] + ["... (truncated) ..."] + lines[-100:])
        else:
            truncated[name] = output

    captures_text = ""
    for name, output in truncated.items():
        captures_text += f"\n\n=== Agent: {name} ===\n{output}"

    prompt = f"""You are evaluating an automated test of an AI agent delegation system called "overcode".

A parent agent was given this prompt:
"You need to research jokes by delegating to 3 child agents. Each child should research jokes about a different theme: animals, food, and technology. Each child should think of 3 original jokes, pausing 5 seconds between each joke to simulate research time. After all children complete, summarize all the jokes you collected."

The parent agent needed to autonomously discover a "delegating-to-agents" skill, learn the `overcode launch` command syntax, launch child agents, and collect their results using `overcode report`.

Below are the captured terminal outputs from all agents found in the test.

{captures_text}

Evaluate each criterion as true or false:

1. skill_activated: Did the parent discover and use the delegating-to-agents skill? (Look for /skill or skill activation messages)
2. children_launched: Did the parent launch child agents using `overcode launch`? (Look for overcode launch commands)
3. jokes_produced: Did children produce jokes about their assigned topics?
4. report_protocol_followed: Did agents use `overcode report` to signal completion?
5. results_collected: Did the parent collect or summarize results from children?

Respond with ONLY a JSON object (no markdown fencing):
{{
  "skill_activated": true/false,
  "children_launched": true/false,
  "jokes_produced": true/false,
  "report_protocol_followed": true/false,
  "results_collected": true/false,
  "explanation": "brief explanation of what happened"
}}"""

    result = subprocess.run(
        ["claude", "-p", prompt, "--output-format", "text"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"Claude judge failed: {result.stderr}"

    text = result.stdout.strip()
    # Parse JSON from response (handle potential markdown fencing)
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


# ── The Test ─────────────────────────────────────────────────────────────────

PARENT_PROMPT = """\
Use the /delegating-to-agents skill to delegate joke research to 3 child agents. \
Each child should research jokes about a different theme: animals, food, and \
technology. Each child should think of 3 original jokes, pausing 5 seconds between \
each joke to simulate research time. After all children complete, summarize all \
the jokes you collected.\
"""

POLL_INTERVAL = 10  # seconds
TIMEOUT = 600  # 10 minutes
NO_CHILD_TIMEOUT = 120  # fail if no children appear after 2 minutes


class TestChildDelegation:
    """Test that a parent agent autonomously discovers the delegation skill,
    launches children, and collects results."""

    def test_parent_delegates_to_children(self, real_claude_env):
        env = real_claude_env
        session = env["session_name"]
        state_dir = env["state_dir"]
        socket = env["tmux_socket"]
        run_env = env["env"]

        # ── 1. Launch parent agent ───────────────────────────────────────
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        launch_result = subprocess.run(
            [
                "python", "-m", "overcode.cli", "launch",
                "--name", "parent-jokes",
                "--session", session,
                "--bypass-permissions",
                "--prompt", PARENT_PROMPT,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=run_env,
            cwd=project_root,
        )
        assert launch_result.returncode == 0, (
            f"Failed to launch parent:\nstdout: {launch_result.stdout}\n"
            f"stderr: {launch_result.stderr}"
        )
        print(f"\n[  0s] Parent launched: {launch_result.stdout.strip()}")

        # ── 2. Poll until parent reaches 'done' ─────────────────────────
        start = time.time()
        seen_agents: set[str] = set()
        milestones: dict[str, float] = {}  # milestone -> elapsed time
        first_child_time: float | None = None
        consecutive_all_idle: int = 0  # polls where ALL agents are idle

        while True:
            elapsed = time.time() - start
            if elapsed > TIMEOUT:
                # Capture final state for diagnostics
                sessions = _read_sessions(state_dir)
                print(f"\n[TIMEOUT] {elapsed:.0f}s elapsed. Final state:")
                for sid, s in sessions.items():
                    print(f"  {s.get('name', '?')}: {s.get('status', '?')}")
                pytest.fail(f"Timed out after {TIMEOUT}s waiting for parent to finish")

            sessions = _read_sessions(state_dir)
            agent_summary = []
            parent_status = None
            parent_terminated = False

            for sid, s in sessions.items():
                name = s.get("name", "?")
                status = s.get("status", "?")
                agent_summary.append(f"{name}={status}")

                if name not in seen_agents:
                    seen_agents.add(name)

                if name == "parent-jokes":
                    parent_status = status
                    if status == "terminated":
                        parent_terminated = True

            # Print progress
            summary_str = ", ".join(agent_summary) if agent_summary else "no agents yet"
            print(f"[{elapsed:4.0f}s] {len(sessions)} agents: {summary_str}")

            # Print last lines of each agent's pane
            for sid, s in sessions.items():
                name = s.get("name", "?")
                window = s.get("tmux_window")
                tmux_session = s.get("tmux_session", session)
                if window is not None:
                    snippet = _last_n_lines(
                        _capture_pane(socket, tmux_session, window, lines=20), n=3
                    )
                    if snippet:
                        for line in snippet.splitlines():
                            print(f"        {name}: {line}")

            # Track milestones
            children = {
                s.get("name"): s
                for sid, s in sessions.items()
                if s.get("name") != "parent-jokes"
            }
            if children and "first_child" not in milestones:
                milestones["first_child"] = elapsed
                first_child_time = elapsed
                print(f"        ** MILESTONE: first child appeared ({list(children.keys())[0]}) **")

            for cname, cdata in children.items():
                key = f"{cname}_done"
                if cdata.get("status") == "done" and key not in milestones:
                    milestones[key] = elapsed
                    print(f"        ** MILESTONE: {cname} reached 'done' **")

            # Detect parent completion. A root agent won't get "done" in
            # sessions.json (that only happens for child agents via overcode
            # report + list_sessions). The parent is considered finished when:
            #   a) sessions.json says "done", OR
            #   b) Claude exited (no status bar in pane), OR
            #   c) All children completed AND parent Claude is idle, OR
            #   d) ALL agents idle for 3+ consecutive polls (fallback for
            #      children that don't call overcode report).
            parent_finished = parent_status == "done"
            any_agent_active = False

            if not parent_finished and first_child_time is not None:
                all_children_done = children and all(
                    c.get("status") in ("done", "waiting_oversight", "terminated")
                    for c in children.values()
                )

                # Check each agent's pane for activity
                for sid, s in sessions.items():
                    name = s.get("name", "?")
                    window = s.get("tmux_window")
                    tmux_sess = s.get("tmux_session", session)
                    if window is not None:
                        pane = _capture_pane(socket, tmux_sess, window, lines=10)
                        if "esc to interrupt" in pane:
                            any_agent_active = True

                    if name == "parent-jokes" and window is not None:
                        pane = _capture_pane(socket, tmux_sess, window, lines=10)
                        has_claude_bar = "bypass permissions" in pane or "shift+tab" in pane
                        is_processing = "esc to interrupt" in pane
                        if not has_claude_bar and pane.strip():
                            parent_finished = True
                            print(f"        ** MILESTONE: parent Claude exited (no status bar) **")
                        elif all_children_done and has_claude_bar and not is_processing:
                            parent_finished = True
                            print(f"        ** MILESTONE: parent idle at prompt, all children done **")

                # Fallback: if ALL agents have been idle for 3+ consecutive
                # polls (30s), consider the whole run complete. This handles
                # children that finish work but don't call overcode report.
                if not parent_finished and not any_agent_active and children:
                    consecutive_all_idle += 1
                    if consecutive_all_idle >= 3:
                        parent_finished = True
                        print(f"        ** MILESTONE: all agents idle for {consecutive_all_idle} polls — assuming complete **")
                else:
                    consecutive_all_idle = 0

            if parent_finished:
                milestones["all_done"] = elapsed
                print(f"        ** MILESTONE: parent-jokes finished — test complete **")
                break

            # Early abort: parent terminated
            if parent_terminated:
                # Capture parent output for diagnostics
                for sid, s in sessions.items():
                    if s.get("name") == "parent-jokes":
                        output = _capture_pane(socket, s.get("tmux_session", session), s["tmux_window"], lines=50)
                        print(f"\n[ABORT] Parent terminated. Last output:\n{output}")
                        break
                pytest.fail("Parent agent terminated unexpectedly")

            # Early abort: no children have EVER appeared after 2 minutes
            # (Use first_child_time, not current children — parent may kill
            # children after collecting results, removing them from sessions.json)
            if elapsed > NO_CHILD_TIMEOUT and first_child_time is None:
                for sid, s in sessions.items():
                    if s.get("name") == "parent-jokes":
                        output = _capture_pane(socket, s.get("tmux_session", session), s["tmux_window"], lines=50)
                        print(f"\n[ABORT] No children after {NO_CHILD_TIMEOUT}s. Parent output:\n{output}")
                        break
                pytest.fail(f"No child agents appeared after {NO_CHILD_TIMEOUT}s")

            # Warn if any child terminated (parent may still recover)
            for cname, cdata in children.items():
                warn_key = f"{cname}_terminated_warned"
                if cdata.get("status") == "terminated" and warn_key not in milestones:
                    milestones[warn_key] = elapsed
                    print(f"        ** WARNING: {cname} terminated **")

            time.sleep(POLL_INTERVAL)

        # ── 3. Capture all agent outputs ─────────────────────────────────
        print("\n--- Capturing final outputs for judge ---")
        all_outputs: dict[str, str] = {}
        sessions = _read_sessions(state_dir)
        for sid, s in sessions.items():
            name = s.get("name", "?")
            window = s.get("tmux_window")
            tmux_session = s.get("tmux_session", session)
            if window is not None:
                output = _capture_pane(socket, tmux_session, window, lines=200)
                all_outputs[name] = output
                print(f"  {name}: {len(output)} chars captured")

        assert all_outputs, "No agent outputs captured"

        # ── 4. Claude-as-judge ───────────────────────────────────────────
        print("\n--- Running Claude-as-judge ---")
        verdict = _judge_outputs(all_outputs)
        print(f"Verdict: {json.dumps(verdict, indent=2)}")

        # All 5 criteria required to pass.
        # The point of this test is the skill discovery → report pipeline,
        # not just "can agents produce jokes".
        assert verdict.get("children_launched"), (
            f"FAIL: Children were not launched.\n{verdict.get('explanation')}"
        )
        assert verdict.get("jokes_produced"), (
            f"FAIL: Jokes were not produced.\n{verdict.get('explanation')}"
        )
        assert verdict.get("skill_activated"), (
            f"FAIL: Parent did not discover/activate the delegating-to-agents skill.\n"
            f"{verdict.get('explanation')}"
        )
        assert verdict.get("report_protocol_followed"), (
            f"FAIL: Children did not use 'overcode report' to signal completion.\n"
            f"{verdict.get('explanation')}"
        )
        assert verdict.get("results_collected"), (
            f"FAIL: Parent did not collect/summarize results from children.\n"
            f"{verdict.get('explanation')}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "--timeout=660"])
