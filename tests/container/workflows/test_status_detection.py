"""Status detection through the monitor daemon (coverage matrix rows 3 & 7).

Launches mock agents in distinct scenarios and asserts the daemon-detected
status published in monitor_daemon_state.json.
"""

import pytest

pytestmark = pytest.mark.timeout(180)


@pytest.fixture
def monitored(oc, oc_wait):
    """Monitor daemon running for this test's session.

    Gate on the PID file too: the state file appears first, and a stop issued
    in that window is a silent no-op (is_running reads the PID file).
    """
    oc.start_monitor_daemon(interval=1)
    oc_wait(
        lambda: (oc.session_dir / "monitor_daemon.pid").exists()
        and oc.daemon_state().get("status") not in ("", "stopped"),
        desc="monitor daemon up (pid + state)",
    )
    return oc


def test_idle_agent_detected_waiting(monitored, oc_wait):
    monitored.launch("idler", scenario="startup_idle")
    oc_wait(
        lambda: monitored.agent_status("idler") in ("waiting_user", "waiting_approval"),
        timeout=60,
        desc="idle agent detected as waiting",
    )


def test_running_agent_detected_running(monitored, oc_wait):
    monitored.launch("runner", scenario="task_running")
    oc_wait(
        lambda: monitored.agent_status("runner") == "running",
        timeout=60,
        desc="busy agent detected as running",
    )


def test_permission_prompt_detected(monitored, oc_wait):
    """Permission dialogs are waiting_user with a 'Permission: ...' activity
    (waiting_approval is reserved for plan approval, status_detector.py:286)."""
    monitored.launch("asker", scenario="permission_bash")

    def detected():
        state = monitored.agent_daemon_state("asker")
        return (
            state.get("current_status") == "waiting_user"
            and "permission" in state.get("current_activity", "").lower()
        )

    oc_wait(detected, timeout=60, desc="permission prompt detected")


def test_exited_agent_detected_terminated(monitored, oc_wait, sandbox):
    monitored.launch("quitter", scenario="task_complete")
    # task_complete finishes its work then exits, leaving the shell prompt
    oc_wait(
        lambda: monitored.agent_status("quitter") in ("terminated", "waiting_user", "done"),
        timeout=90,
        desc="completed agent reaches a settled status",
    )


def test_status_history_csv_grows(monitored, oc_wait):
    monitored.launch("hist", scenario="task_running")
    csv = monitored.session_dir / "agent_status_history.csv"
    oc_wait(lambda: csv.exists() and "hist" in csv.read_text(),
            timeout=60, desc="status history CSV records agent")


def test_daemon_stop_reports_stopped(monitored, oc, oc_wait):
    assert oc.run("monitor-daemon", "stop").returncode == 0

    def stopped():
        # Retry stop: a stop that raced daemon startup is a silent no-op
        oc.run("monitor-daemon", "stop")
        return "stopped" in oc.ok("monitor-daemon", "status").stdout.lower()

    oc_wait(stopped, timeout=30, desc="status reports stopped after stop")
