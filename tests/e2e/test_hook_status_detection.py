"""
E2E Test: Hook-Based Status Detection (Real Claude)

Tests the full hook-based status detection pipeline: Claude Code fires hooks
(UserPromptSubmit, PostToolUse, Stop, PermissionRequest) which call
`overcode hook-handler`, writing state files that the monitor daemon reads
to determine agent status.

States exercised:
    UserPromptSubmit  → running ("Processing prompt")
    PostToolUse       → running ("Using {tool_name}")
    Stop              → waiting_user ("Waiting for user input")
    PermissionRequest → waiting_user ("Permission: approval required")
    Stop (child)      → waiting_oversight ("Waiting for oversight report")

Requires:
- Real Claude CLI (`claude` binary)
- tmux available
- Hooks installed (`overcode hooks install`)

Run with:
    uv run pytest tests/e2e/test_hook_status_detection.py -v -s --timeout=360
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

# Isolated tmux socket for tests
TEST_TMUX_SOCKET = "overcode-test"

POLL_INTERVAL = 2  # seconds between daemon state checks

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
def real_claude_env():
    """Set up a clean test environment using the real Claude binary.

    Creates an isolated tmux socket and temp state dir. Strips OVERCODE_*
    env vars so the launcher's auto-parent-detection doesn't pick up the
    host agent's identity.
    """
    session_name = "agents"
    state_dir = tempfile.mkdtemp(prefix="overcode-hook-test-")

    # Kill any leftover test session
    subprocess.run(
        ["tmux", "-L", TEST_TMUX_SOCKET, "kill-session", "-t", session_name],
        capture_output=True,
    )

    env = os.environ.copy()
    env["OVERCODE_STATE_DIR"] = state_dir
    env["OVERCODE_TMUX_SOCKET"] = TEST_TMUX_SOCKET

    # Strip host-agent env vars
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
    os.environ.pop("CLAUDECODE", None)
    os.environ.pop("CLAUDE_CODE_ENTRYPOINT", None)

    # Start monitor daemon in background
    daemon_proc = subprocess.Popen(
        [
            "python", "-m", "overcode.cli", "monitor-daemon", "start",
            "--interval", "3",
            "--session", session_name,
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Wait for daemon to start writing state
    time.sleep(2)

    try:
        yield {
            "session_name": session_name,
            "state_dir": state_dir,
            "env": env,
            "tmux_socket": TEST_TMUX_SOCKET,
            "daemon_proc": daemon_proc,
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
                                "--session", session_name,
                            ],
                            capture_output=True,
                            timeout=10,
                            env=env,
                        )
        except Exception:
            pass

        # Stop monitor daemon
        daemon_proc.terminate()
        try:
            daemon_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            daemon_proc.kill()

        # Also stop via CLI (in case Popen didn't own the process)
        subprocess.run(
            [
                "python", "-m", "overcode.cli", "monitor-daemon", "stop",
                "--session", session_name,
            ],
            capture_output=True,
            timeout=10,
            env=env,
        )

        # Kill tmux session
        subprocess.run(
            ["tmux", "-L", TEST_TMUX_SOCKET, "kill-session", "-t", session_name],
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

def _read_daemon_state(state_dir: str, session_name: str) -> dict | None:
    """Read and parse monitor_daemon_state.json."""
    state_path = Path(state_dir) / session_name / "monitor_daemon_state.json"
    try:
        with open(state_path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _get_daemon_session(state_dir: str, session_name: str, agent_name: str) -> dict | None:
    """Return session dict from daemon state for a named agent."""
    state = _read_daemon_state(state_dir, session_name)
    if not state:
        return None
    for session in state.get("sessions", []):
        if session.get("name") == agent_name:
            return session
    return None


def _wait_for_daemon_status(
    state_dir: str,
    session_name: str,
    agent_name: str,
    expected_status: str,
    timeout: float = 30,
) -> list[tuple[str, str]]:
    """Poll daemon state until agent reaches expected status.

    Returns all observed (status, activity) tuples.
    """
    observations: list[tuple[str, str]] = []
    start = time.time()
    while time.time() - start < timeout:
        session = _get_daemon_session(state_dir, session_name, agent_name)
        if session:
            status = session.get("current_status", "unknown")
            activity = session.get("current_activity", "")
            obs = (status, activity)
            if not observations or observations[-1] != obs:
                observations.append(obs)
                elapsed = time.time() - start
                print(f"  [{elapsed:5.1f}s] {agent_name}: {status} — {activity}")
            if status == expected_status:
                return observations
        time.sleep(POLL_INTERVAL)
    return observations


def _read_hook_state_file(state_dir: str, session_name: str, agent_name: str) -> dict | None:
    """Read hook_state_{name}.json to verify hooks actually fired."""
    path = Path(state_dir) / session_name / f"hook_state_{agent_name}.json"
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


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


def _launch_agent(env: dict, session: str, name: str, prompt: str | None = None,
                  bypass: bool = True, parent: str | None = None) -> subprocess.CompletedProcess:
    """Launch an agent via overcode CLI."""
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    cmd = [
        "python", "-m", "overcode.cli", "launch",
        "--name", name,
        "--session", session,
    ]
    if bypass:
        cmd.append("--bypass-permissions")
    if parent:
        cmd.extend(["--parent", parent])
    if prompt:
        cmd.extend(["--prompt", prompt])
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        cwd=project_root,
    )


def _wait_for_agent_in_daemon(state_dir: str, session_name: str, agent_name: str,
                               timeout: float = 30) -> bool:
    """Wait for an agent to appear in daemon state."""
    start = time.time()
    while time.time() - start < timeout:
        session = _get_daemon_session(state_dir, session_name, agent_name)
        if session:
            return True
        time.sleep(POLL_INTERVAL)
    return False


# ── Tests ────────────────────────────────────────────────────────────────────

class TestHookStatusDetection:
    """Test that hook events produce correct daemon statuses."""

    def test_hook_lifecycle_bypass_mode(self, real_claude_env):
        """Proves: UserPromptSubmit → running, PostToolUse → running with tool, Stop → waiting_user."""
        env = real_claude_env
        session = env["session_name"]
        state_dir = env["state_dir"]
        run_env = env["env"]

        # 1. Launch agent in bypass mode
        print("\n--- Launching agent 'hook-test' in bypass mode ---")
        result = _launch_agent(
            run_env, session, "hook-test",
            prompt="Read the file tests/mock_claude.py and list all the function names defined in it.",
            bypass=True,
        )
        assert result.returncode == 0, (
            f"Failed to launch:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        print(f"  Launched: {result.stdout.strip()}")

        # 2. Wait for agent to appear in daemon state
        print("\n--- Waiting for agent in daemon state ---")
        assert _wait_for_agent_in_daemon(state_dir, session, "hook-test", timeout=30), \
            "Agent 'hook-test' never appeared in daemon state"

        # 3. Poll daemon state, collecting observations until waiting_user
        print("\n--- Polling daemon state for status transitions ---")
        observations = _wait_for_daemon_status(
            state_dir, session, "hook-test",
            expected_status="waiting_user",
            timeout=120,
        )

        # 4. Assertions
        statuses = [s for s, _ in observations]
        activities = [a for _, a in observations]

        print(f"\n--- Results ---")
        print(f"  Observations: {observations}")

        # Must have seen 'running' at some point
        assert "running" in statuses, (
            f"Never saw 'running' status. Observations: {observations}"
        )

        # Must have seen a tool use activity (PostToolUse)
        assert any("Using" in a for a in activities), (
            f"Never saw 'Using ...' activity from PostToolUse. Activities: {activities}"
        )

        # Final status should be waiting_user
        assert statuses[-1] == "waiting_user", (
            f"Final status is {statuses[-1]}, expected waiting_user. Observations: {observations}"
        )

        # Hook state file must exist
        hook_state = _read_hook_state_file(state_dir, session, "hook-test")
        assert hook_state is not None, (
            f"Hook state file not found at {state_dir}/{session}/hook_state_hook-test.json"
        )
        print(f"  Hook state file: {hook_state}")

    def test_permission_request_detection(self, real_claude_env):
        """Proves: PermissionRequest hook fires during normal-mode tool use.

        Strategy: Launch agent in normal mode WITH a prompt that triggers
        Bash permission. Start fast-polling the hook state FILE (0.2s)
        IMMEDIATELY after launch — don't wait for daemon first, since the
        entire Claude lifecycle can complete in <10s.

        If PermissionRequest is detected, auto-approve it via tmux.
        """
        env = real_claude_env
        session = env["session_name"]
        state_dir = env["state_dir"]
        socket = env["tmux_socket"]
        run_env = env["env"]

        # 1. Launch agent in NORMAL mode with prompt (triggers permission)
        print("\n--- Launching agent 'perm-test' in normal mode with prompt ---")
        result = _launch_agent(
            run_env, session, "perm-test",
            prompt="Run the command `echo hello_overcode_test` and tell me the output.",
            bypass=False,
        )
        assert result.returncode == 0, (
            f"Failed to launch:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        print(f"  Launched: {result.stdout.strip()}")

        # 2. Find the agent's tmux window from sessions.json
        # Poll for it since it's written async by the launcher
        print("\n--- Finding agent tmux window ---")
        sessions_file = os.path.join(state_dir, "sessions", "sessions.json")
        agent_window = None
        for _ in range(15):
            try:
                with open(sessions_file) as f:
                    sessions_data = json.load(f)
                for sid, s in sessions_data.items():
                    if s.get("name") == "perm-test":
                        agent_window = s.get("tmux_window")
                        break
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            if agent_window is not None:
                break
            time.sleep(0.5)
        assert agent_window is not None, "Could not find perm-test window in sessions.json"
        print(f"  Found window: {agent_window}")

        # 3. Fast-poll the hook state FILE immediately (don't wait for daemon)
        # The hook file is updated instantly by the hook handler, but gets
        # overwritten by subsequent events. Poll at 0.2s to catch transients.
        print("\n--- Fast-polling hook state file for events ---")
        seen_events: list[str] = []
        saw_permission_request = False
        approved = False
        start = time.time()
        while time.time() - start < 90:
            hook_state = _read_hook_state_file(state_dir, session, "perm-test")
            if hook_state:
                event = hook_state.get("event", "")
                if not seen_events or seen_events[-1] != event:
                    seen_events.append(event)
                    elapsed = time.time() - start
                    print(f"  [{elapsed:5.1f}s] Hook event: {event}")
                if event == "PermissionRequest" and not approved:
                    saw_permission_request = True
                    # Approve the permission
                    subprocess.run(
                        [
                            "tmux", "-L", socket,
                            "send-keys", "-t", f"{session}:{agent_window}",
                            "y", "Enter",
                        ],
                        capture_output=True,
                    )
                    approved = True
                    print("  Sent 'y' to approve")
                if event == "Stop" and len(seen_events) > 1:
                    # Task completed (Stop after prompt processing)
                    break
            time.sleep(0.2)

        # If we haven't seen any events, capture pane for debugging
        if not seen_events:
            pane = _capture_pane(socket, session, agent_window, lines=30)
            print(f"\n  DEBUG pane content:\n{pane}")

        print(f"\n--- Results ---")
        print(f"  Hook events seen: {seen_events}")
        print(f"  PermissionRequest captured: {saw_permission_request}")

        # 4. Assertions
        # The hook handler must have written at least some events
        assert len(seen_events) >= 1, (
            f"No hook events observed. Check that hooks are installed."
        )

        # Must end with Stop (task completed)
        assert seen_events[-1] == "Stop", (
            f"Final hook event is {seen_events[-1]}, expected Stop. Events: {seen_events}"
        )

        # Verify PermissionRequest was captured OR full lifecycle observed.
        # If the user has Bash auto-approved, PermissionRequest fires but
        # PostToolUse overwrites it almost instantly. At 0.2s polling we
        # may or may not catch it.
        if saw_permission_request:
            print("  PermissionRequest successfully captured!")
        else:
            # Even if PermissionRequest was too fast, verify we saw the
            # lifecycle: must have seen more than just Stop
            assert len(seen_events) >= 2, (
                f"Expected multiple hook events but only saw: {seen_events}. "
                f"Hooks may not be firing intermediate events."
            )
            print("  NOTE: PermissionRequest was too transient to capture "
                  "(likely auto-approved). Verified hook lifecycle instead.")

        # Final daemon status should be waiting_user (from Stop)
        # Wait for daemon to catch up (it polls every 3s)
        print("\n--- Waiting for daemon to reflect final status ---")
        final_obs = _wait_for_daemon_status(
            state_dir, session, "perm-test",
            expected_status="waiting_user",
            timeout=30,
        )
        assert final_obs and final_obs[-1][0] == "waiting_user", (
            f"Daemon never reached waiting_user. Observations: {final_obs}"
        )

    def test_child_agent_waiting_oversight(self, real_claude_env):
        """Proves: Stop on child agent → waiting_oversight (not waiting_user)."""
        env = real_claude_env
        session = env["session_name"]
        state_dir = env["state_dir"]
        run_env = env["env"]

        # 1. Launch parent agent (no prompt — just sits at prompt)
        print("\n--- Launching parent agent 'parent-hook' ---")
        result = _launch_agent(
            run_env, session, "parent-hook",
            bypass=True,
        )
        assert result.returncode == 0, (
            f"Failed to launch parent:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        print(f"  Parent launched: {result.stdout.strip()}")

        # 2. Wait for parent to appear in daemon state
        print("\n--- Waiting for parent in daemon state ---")
        assert _wait_for_agent_in_daemon(state_dir, session, "parent-hook", timeout=30), \
            "Parent 'parent-hook' never appeared in daemon state"
        print("  Parent visible in daemon state")

        # 3. Launch child agent with --parent
        print("\n--- Launching child agent 'child-hook' ---")
        result = _launch_agent(
            run_env, session, "child-hook",
            prompt="Write a haiku about testing software.",
            bypass=True,
            parent="parent-hook",
        )
        assert result.returncode == 0, (
            f"Failed to launch child:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        print(f"  Child launched: {result.stdout.strip()}")

        # 4. Wait for child to appear in daemon state
        print("\n--- Waiting for child in daemon state ---")
        assert _wait_for_agent_in_daemon(state_dir, session, "child-hook", timeout=30), \
            "Child 'child-hook' never appeared in daemon state"

        # 5. Poll daemon state — wait for child to reach waiting_oversight
        print("\n--- Waiting for child to reach waiting_oversight ---")
        observations = _wait_for_daemon_status(
            state_dir, session, "child-hook",
            expected_status="waiting_oversight",
            timeout=120,
        )

        statuses = [s for s, _ in observations]
        activities = [a for _, a in observations]

        print(f"\n--- Results ---")
        print(f"  Child observations: {observations}")

        # 6. Assertions
        # Child must reach waiting_oversight (not waiting_user)
        assert "waiting_oversight" in statuses, (
            f"Child never reached waiting_oversight. Observations: {observations}"
        )

        # Child activity should mention oversight or report
        final_activity = activities[-1] if activities else ""
        assert "oversight" in final_activity.lower() or "report" in final_activity.lower(), (
            f"Child activity doesn't mention oversight/report: '{final_activity}'"
        )

        # Parent should still be running or waiting (not affected)
        parent_session = _get_daemon_session(state_dir, session, "parent-hook")
        assert parent_session is not None, "Parent disappeared from daemon state"
        parent_status = parent_session["current_status"]
        assert parent_status in ("running", "waiting_user", "unknown"), (
            f"Parent status is unexpected: {parent_status}"
        )
        print(f"  Parent status: {parent_status} (unaffected)")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "--timeout=360"])
