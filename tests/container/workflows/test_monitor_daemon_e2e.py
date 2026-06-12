"""Monitor daemon lifecycle and state publication (coverage matrix rows 7 & 16)."""

import os
import signal

import pytest

pytestmark = pytest.mark.timeout(180)


def test_daemon_start_status_stop(oc, oc_wait):
    proc = oc.start_monitor_daemon(interval=1)
    oc_wait(lambda: (oc.session_dir / "monitor_daemon.pid").exists(),
            desc="daemon writes pid file")

    status = oc.ok("monitor-daemon", "status")
    assert "running" in status.stdout.lower()

    stop = oc.ok("monitor-daemon", "stop")
    assert "stopped" in stop.stdout.lower()
    oc_wait(lambda: proc.poll() is not None, desc="daemon process exits after stop")


def test_double_start_refused(oc, oc_wait):
    oc.start_monitor_daemon(interval=1)
    oc_wait(lambda: (oc.session_dir / "monitor_daemon.pid").exists(),
            desc="first daemon up")

    second = oc.run("monitor-daemon", "start", "--interval", "1", timeout=15)
    assert second.returncode != 0
    assert "already running" in (second.stdout + second.stderr).lower()


def test_state_file_schema(oc, oc_wait):
    oc.launch("observed", scenario="startup_idle")
    oc.start_monitor_daemon(interval=1)

    state = oc_wait(
        lambda: oc.daemon_state() if oc.daemon_state().get("sessions") else None,
        timeout=60,
        desc="daemon state lists the agent",
    )
    # Fields every consumer (TUI, web, supervisor) relies on
    for field in ("pid", "status", "loop_count", "sessions", "last_loop_time"):
        assert field in state, f"missing {field} in daemon state"
    agent_state = state["sessions"][0]
    for field in ("name", "current_status", "input_tokens", "estimated_cost_usd"):
        assert field in agent_state, f"missing {field} in session state"


def test_sigkilled_daemon_recovers_on_restart(oc, oc_wait):
    """Stale PID file from a kill -9 must not block a fresh daemon (row 16)."""
    proc = oc.start_monitor_daemon(interval=1)
    pid_file = oc.session_dir / "monitor_daemon.pid"
    oc_wait(lambda: pid_file.exists(), desc="daemon pid file")
    pid = int(pid_file.read_text().strip())

    os.kill(pid, signal.SIGKILL)
    # poll() reaps the zombie; os.kill(pid, 0) alone would still "see" it
    oc_wait(lambda: proc.poll() is not None, desc="daemon reaped after SIGKILL")

    # Restart must succeed despite the stale pid file
    oc.start_monitor_daemon(interval=1)
    oc_wait(
        lambda: pid_file.exists() and pid_file.read_text().strip() != str(pid),
        timeout=30,
        desc="new daemon writes fresh pid",
    )


def test_corrupt_sessions_json_handled(oc, oc_wait):
    """Corrupt registry must produce a graceful error, not a stacktrace (row 16)."""
    oc.launch("victim", scenario="startup_idle")
    oc_wait(lambda: oc.agent("victim"), desc="agent in registry")

    registry = oc.state_dir / "sessions" / "sessions.json"
    registry.write_text("{ this is not json")

    result = oc.run("list", timeout=15)
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined


