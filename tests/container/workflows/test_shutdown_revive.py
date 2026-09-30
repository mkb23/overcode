"""Shutdown -> revive round trip (#509, #481) and restart keeping the model (#505)."""

import pytest

pytestmark = pytest.mark.timeout(240)


def _agent_argv(oc, name: str) -> str:
    """The last agent command line typed into NAME's pane (wrapped lines joined)."""
    windows = [w for w in oc.sandbox.list_windows(oc.session) if w.startswith(name)]
    if not windows:
        return ""
    r = oc.sandbox.cmd("capture-pane", "-p", "-J", "-S", "-500",
                       "-t", f"{oc.session}:{windows[0]}")
    typed = [line for line in r.stdout.splitlines() if "mock_claude.py" in line]
    return typed[-1] if typed else ""


def test_shutdown_then_revive_all(oc, oc_wait, sandbox):
    oc.launch("boss", scenario="startup_idle")
    oc_wait(lambda: "Claude" in oc.pane("boss"), desc="boss running")
    oc.launch("worker", "--parent", "boss", scenario="startup_idle")
    oc_wait(lambda: "Claude" in oc.pane("worker"), desc="worker running")
    oc.start_monitor_daemon(interval=1)
    oc_wait(lambda: oc.daemon_state(), desc="monitor daemon publishing")

    dry = oc.ok("shutdown", "--dry-run")
    assert "boss" in dry.stdout and "worker" in dry.stdout, dry.stdout
    assert sandbox.has_session(oc.session), "dry run must change nothing"

    oc.ok("shutdown", "--yes", "--timeout", "5", timeout=90)
    oc_wait(lambda: not sandbox.has_session(oc.session), desc="agents tmux session gone")
    status = oc.run("monitor-daemon", "status")
    assert "stopped" in status.stdout, status.stdout
    # Records are kept so revive can bring them back
    assert oc.agent("boss") and oc.agent("worker")

    dry = oc.ok("revive", "--all", "--dry-run")
    assert "boss" in dry.stdout and "worker" in dry.stdout, dry.stdout

    oc.ok("revive", "--all", extra_env={"MOCK_SCENARIO": "startup_idle"}, timeout=120)
    oc_wait(lambda: "Claude" in oc.pane("boss"), timeout=60, desc="boss revived")
    oc_wait(lambda: "Claude" in oc.pane("worker"), timeout=60, desc="worker revived")
    assert (oc.agent("worker") or {}).get("parent_session_id") == oc.agent("boss")["id"]


def test_restart_keeps_and_changes_model(oc, oc_wait):
    oc.launch("modeler", "--model", "claude-opus-5", scenario="startup_idle")
    oc_wait(lambda: "claude-opus-5" in _agent_argv(oc, "modeler"),
            desc="launched on claude-opus-5")

    oc.ok("restart", "modeler", "--model", "claude-opus-5-5",
          extra_env={"MOCK_SCENARIO": "startup_idle"}, timeout=90)
    oc_wait(lambda: "--model claude-opus-5-5" in _agent_argv(oc, "modeler"),
            timeout=60, desc="restarted on claude-opus-5-5")
    assert oc.agent("modeler")["model"] == "claude-opus-5-5"

    # A plain restart keeps the new model
    oc.ok("restart", "modeler", extra_env={"MOCK_SCENARIO": "startup_idle"}, timeout=90)
    oc_wait(lambda: "--model claude-opus-5-5" in _agent_argv(oc, "modeler"),
            timeout=60, desc="plain restart still on claude-opus-5-5")


def test_restart_model_not_overridden_by_passthrough_arg(oc, oc_wait):
    """The #505 hole: a --model passed via --backend-arg won on every relaunch."""
    oc.launch("pinned", "--backend-arg", "--model claude-opus-5", scenario="startup_idle")
    oc_wait(lambda: "claude-opus-5" in _agent_argv(oc, "pinned"), desc="launched")

    oc.ok("restart", "pinned", "--model", "claude-opus-5-5",
          extra_env={"MOCK_SCENARIO": "startup_idle"}, timeout=90)

    def on_new_model():
        argv = _agent_argv(oc, "pinned")
        return "--model claude-opus-5-5" in argv and "claude-opus-5 " not in argv + " "

    oc_wait(on_new_model, timeout=60, desc="only claude-opus-5-5 on the relaunch")
