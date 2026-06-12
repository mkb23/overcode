"""Supervisor daemon intervention loop (coverage matrix row 8).

A stalled agent (permission prompt) with standing orders should cause the
supervisor to spawn a daemon-claude window (mocked via CLAUDE_COMMAND) and
record the intervention.
"""

import pytest

pytestmark = pytest.mark.timeout(300)


def test_supervisor_intervenes_on_stalled_agent(oc, oc_wait, sandbox):
    # A non-green agent with actionable standing orders
    oc.launch("stuck", scenario="permission_bash")
    oc_wait(lambda: oc.agent("stuck"), desc="agent in registry")
    oc.ok("instruct", "stuck", "approve", "safe", "commands")

    oc.start_monitor_daemon(interval=1)
    # Permission dialogs detect as waiting_user with a Permission activity —
    # non-green either way, which is what the supervisor keys on.
    oc_wait(
        lambda: oc.agent_status("stuck") in ("waiting_user", "waiting_approval"),
        timeout=90,
        desc="agent detected as stalled (non-green)",
    )

    # The daemon-claude window the supervisor spawns inherits MOCK_SCENARIO
    # from tmux global env; have it complete quickly.
    sandbox.set_global_env("MOCK_SCENARIO", "task_complete")

    oc.start_supervisor_daemon(interval=1)

    # The supervisor should spawn its hidden daemon-claude window...
    oc_wait(
        lambda: any(
            "_daemon_claude" in w for w in sandbox.list_windows(oc.session)
        ),
        timeout=120,
        desc="daemon claude window spawned",
    )

    # ...and record evidence of the intervention. Stats are saved on a
    # successful daemon-claude launch; the supervisor's own log records the
    # attempt either way (startup-prompt acceptance can race the mock).
    stats_file = oc.session_dir / "supervisor_stats.json"
    sup_log = oc.session_dir / "supervisor_daemon.log"

    def intervention_recorded():
        if stats_file.exists():
            return True
        for log in (sup_log, oc.log_dir / "supervisor-daemon.log"):
            if log.exists() and "daemon claude" in log.read_text().lower():
                return True
        return False

    oc_wait(intervention_recorded, timeout=120, desc="intervention recorded")
