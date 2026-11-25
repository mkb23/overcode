"""Status detection through the monitor daemon (coverage matrix rows 3 & 7).

Launches mock agents in distinct scenarios and asserts the daemon-detected
status published in monitor_daemon_state.json. Each agent test runs over
every mock backend (``mock_backend``): for opencode the mock also feeds the
installed telemetry plugin, so its status comes from hook state the way a
real opencode agent's does (#534).
"""

import json

import pytest

from tests.container.harness import OPENCODE

pytestmark = pytest.mark.timeout(240)


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


def test_idle_agent_detected_waiting(monitored, oc_wait, mock_backend):
    monitored.launch_on(mock_backend, "idler", scenario="startup_idle")
    oc_wait(
        lambda: monitored.agent_status("idler") in ("waiting_user", "waiting_approval"),
        timeout=60,
        desc="idle agent detected as waiting",
    )


def test_running_agent_detected_running(monitored, oc_wait, mock_backend):
    monitored.launch_on(mock_backend, "runner", scenario="task_running")
    oc_wait(
        lambda: monitored.agent_status("runner") == "running",
        timeout=60,
        desc="busy agent detected as running",
    )


def test_permission_prompt_detected(monitored, oc_wait, mock_backend):
    """Permission dialogs are waiting_approval (orange) with a
    'Permission: ...' activity, in polling mode as in hooks mode."""
    monitored.launch_on(mock_backend, "asker", scenario="permission_bash")

    def detected():
        state = monitored.agent_daemon_state("asker")
        return (
            state.get("current_status") == "waiting_approval"
            and "permission" in state.get("current_activity", "").lower()
        )

    oc_wait(detected, timeout=60, desc="permission prompt detected")


def test_exited_agent_detected_terminated(monitored, oc_wait, sandbox, mock_backend):
    monitored.launch_on(mock_backend, "quitter", scenario="task_complete")
    # task_complete finishes its work then exits, leaving the shell prompt
    oc_wait(
        lambda: monitored.agent_status("quitter") in ("terminated", "waiting_user", "done"),
        timeout=90,
        desc="completed agent reaches a settled status",
    )


def test_status_history_csv_grows(monitored, oc_wait, mock_backend):
    monitored.launch_on(mock_backend, "hist", scenario="task_running")
    csv = monitored.session_dir / "agent_status_history.csv"
    oc_wait(lambda: csv.exists() and "hist" in csv.read_text(),
            timeout=60, desc="status history CSV records agent")


def test_opencode_conversation_adopted_from_plugin(monitored, oc_wait):
    """The daemon records the ``ses_…`` id the plugin reported for the
    conversation the mock wrote, and prices it from the store's tokens."""
    monitored.launch_on(OPENCODE, "talker", scenario="one_turn")
    store = oc_wait(monitored.opencode_sessions, timeout=60,
                    desc="mock opencode wrote its conversation")
    (sid,) = store
    oc_wait(lambda: (monitored.agent("talker") or {}).get("active_agent_session_id") == sid,
            timeout=60, desc="daemon adopted the conversation id")
    assert sid in monitored.agent("talker")["agent_session_ids"]
    # ...reported by the installed plugin, not guessed from the directory
    hook_state = (monitored.session_dir / "agents" / monitored.agent("talker")["id"]
                  / "hook_state.json")
    assert json.loads(hook_state.read_text())["agent_session_id"] == sid
    # Unattended, the daemon syncs transcript stats once a minute
    oc_wait(lambda: (monitored.agent("talker") or {}).get("stats", {}).get("input_tokens"),
            timeout=150, desc="tokens synced from the store")
    assert monitored.agent("talker")["stats"]["input_tokens"] == 7000
    oc_wait(lambda: monitored.agent_status("talker") == "waiting_user",
            timeout=60, desc="settled turn detected as waiting")


def test_daemon_stop_reports_stopped(monitored, oc, oc_wait):
    assert oc.run("monitor-daemon", "stop").returncode == 0

    def stopped():
        # Retry stop: a stop that raced daemon startup is a silent no-op
        oc.run("monitor-daemon", "stop")
        return "stopped" in oc.ok("monitor-daemon", "status").stdout.lower()

    oc_wait(stopped, timeout=30, desc="status reports stopped after stop")
