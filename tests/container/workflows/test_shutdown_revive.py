"""Shutdown -> revive round trip (#509, #481) and restart keeping the model
(#505), over every mock backend; opencode revive resuming its conversation
(#534)."""

import pytest

from tests.container.harness import OPENCODE

pytestmark = pytest.mark.timeout(240)

# Two models each backend accepts, as `--model` takes them
MODELS = {
    "claude-code": ("claude-opus-5", "claude-opus-5-5"),
    "opencode": ("openai/gpt-5", "openai/gpt-5.1"),
}


def _relaunch(backend, key="startup_idle"):
    return {"MOCK_SCENARIO": backend.scenario(key)}


def test_shutdown_then_revive_all(oc, oc_wait, sandbox, mock_backend):
    ready = mock_backend.ready_text
    oc.launch_on(mock_backend, "boss", scenario="startup_idle")
    oc_wait(lambda: ready in oc.pane("boss"), desc="boss running")
    oc.launch_on(mock_backend, "worker", "--parent", "boss", scenario="startup_idle")
    oc_wait(lambda: ready in oc.pane("worker"), desc="worker running")
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

    oc.ok("revive", "--all", extra_env=_relaunch(mock_backend), timeout=120)
    oc_wait(lambda: ready in oc.pane("boss"), timeout=60, desc="boss revived")
    oc_wait(lambda: ready in oc.pane("worker"), timeout=60, desc="worker revived")
    assert (oc.agent("worker") or {}).get("parent_session_id") == oc.agent("boss")["id"]


def test_opencode_revive_resumes_its_conversation(oc, oc_wait, sandbox):
    """Each opencode agent comes back on `--session <its id>`: the id the
    daemon learned from the plugin, for the conversation the mock wrote.
    The mock refuses an id the store lacks, so a wrong one fails the revive."""
    oc.launch_on(OPENCODE, "boss", scenario="one_turn")
    oc.launch_on(OPENCODE, "worker", "--parent", "boss", scenario="one_turn")
    oc.start_monitor_daemon(interval=1)

    def ids():
        boss, worker = oc.agent("boss") or {}, oc.agent("worker") or {}
        pair = (boss.get("active_agent_session_id"), worker.get("active_agent_session_id"))
        return pair if all(pair) else None

    boss_sid, worker_sid = oc_wait(ids, timeout=60, desc="both conversation ids adopted")
    store = oc.opencode_sessions()
    assert {boss_sid, worker_sid} == set(store), "ids must be the mock's conversations"
    assert boss_sid != worker_sid

    oc.ok("shutdown", "--yes", "--timeout", "5", timeout=90)
    oc_wait(lambda: not sandbox.has_session(oc.session), desc="agents tmux session gone")

    oc.ok("revive", "--all", extra_env=_relaunch(OPENCODE), timeout=120)
    for name, sid in (("boss", boss_sid), ("worker", worker_sid)):
        oc_wait(lambda name=name, sid=sid:
                f"--session {sid}" in oc.agent_argv(name, OPENCODE.script),
                timeout=60, desc=f"{name} relaunched on --session {sid}")
        oc_wait(lambda name=name: OPENCODE.ready_text in oc.pane(name),
                timeout=60, desc=f"{name} revived")
        assert "--fork" not in oc.agent_argv(name, OPENCODE.script)
        assert oc.agent(name)["active_agent_session_id"] == sid
    assert set(oc.opencode_sessions()) == {boss_sid, worker_sid}, "revive started fresh"


def test_restart_keeps_and_changes_model(oc, oc_wait, mock_backend):
    old, new = MODELS[mock_backend.name]
    script = mock_backend.script
    oc.launch_on(mock_backend, "modeler", "--model", old, scenario="startup_idle")
    oc_wait(lambda: old in oc.agent_argv("modeler", script), desc=f"launched on {old}")

    oc.ok("restart", "modeler", "--model", new,
          extra_env=_relaunch(mock_backend), timeout=90)
    oc_wait(lambda: f"--model {new}" in oc.agent_argv("modeler", script),
            timeout=60, desc=f"restarted on {new}")
    assert oc.agent("modeler")["model"] == new

    # A plain restart keeps the new model
    oc.ok("restart", "modeler", extra_env=_relaunch(mock_backend), timeout=90)
    oc_wait(lambda: f"--model {new}" in oc.agent_argv("modeler", script),
            timeout=60, desc=f"plain restart still on {new}")


def test_restart_model_not_overridden_by_passthrough_arg(oc, oc_wait, mock_backend):
    """The #505 hole: a --model passed via --backend-arg won on every relaunch."""
    old, new = MODELS[mock_backend.name]
    script = mock_backend.script
    oc.launch_on(mock_backend, "pinned", "--backend-arg", f"--model {old}",
                 scenario="startup_idle")
    oc_wait(lambda: old in oc.agent_argv("pinned", script), desc="launched")

    oc.ok("restart", "pinned", "--model", new,
          extra_env=_relaunch(mock_backend), timeout=90)

    def on_new_model():
        argv = oc.agent_argv("pinned", script)
        return f"--model {new}" in argv and f"{old} " not in argv + " "

    oc_wait(on_new_model, timeout=60, desc=f"only {new} on the relaunch")


def test_opencode_restart_drops_short_model_passthrough(oc, oc_wait):
    """opencode (like codex, grok and hermes) also takes the model as `-m`;
    a `-m` pass-through must not outvote `restart --model` either (#505)."""
    old, new = MODELS["opencode"]
    script = OPENCODE.script
    oc.launch_on(OPENCODE, "short", "--backend-arg", f"-m {old}", scenario="startup_idle")
    oc_wait(lambda: old in oc.agent_argv("short", script), desc="launched")

    oc.ok("restart", "short", "--model", new, extra_env=_relaunch(OPENCODE), timeout=90)

    def on_new_model():
        argv = oc.agent_argv("short", script)
        return f"--model {new}" in argv and f"{old} " not in argv + " "

    oc_wait(on_new_model, timeout=60, desc=f"only {new} on the relaunch")
