"""Fork, restart, cleanup, sleep (coverage matrix rows 2 & 4), over every
mock backend. For opencode the conversation ids come from the mock's store
and the installed plugin, so a restart must resume the crashed conversation
and a fork must branch the source's into a new one (#534)."""

import pytest

from tests.container.harness import OPENCODE

pytestmark = pytest.mark.timeout(180)


def _active_id(oc, name):
    return (oc.agent(name) or {}).get("active_agent_session_id")


def test_restart_recovers_crashed_agent(oc, oc_wait, mock_backend):
    oc.launch_on(mock_backend, "phoenix", scenario="crash_mid_task")
    oc_wait(lambda: oc.agent("phoenix"), desc="agent in registry")
    oc.start_monitor_daemon(interval=1)

    # crash_mid_task exits abruptly, leaving the shell prompt
    oc_wait(
        lambda: "$" in oc.pane("phoenix").splitlines()[-1]
        if oc.pane("phoenix").strip() else False,
        timeout=90,
        desc="mock agent crashed back to shell",
    )
    # The conversation it crashed in is the one a restart resumes
    sid = oc_wait(lambda: _active_id(oc, "phoenix"), timeout=60,
                  desc="conversation id known")
    if mock_backend is OPENCODE:
        assert sid in oc.opencode_sessions()

    restart = oc.run("restart", "phoenix",
                     extra_env={"MOCK_SCENARIO": mock_backend.scenario("startup_idle")},
                     timeout=60)
    assert restart.returncode == 0, restart.stdout + restart.stderr
    oc_wait(lambda: mock_backend.fresh_text in oc.pane("phoenix"), timeout=60,
            desc="agent running again after restart")
    if mock_backend is OPENCODE:
        argv = oc.agent_argv("phoenix", OPENCODE.script)
        assert f"--session {sid}" in argv and "--fork" not in argv, argv
        assert list(oc.opencode_sessions()) == [sid], "restart started fresh"


def test_fork_creates_sibling(oc, oc_wait, sandbox, mock_backend):
    oc.launch_on(mock_backend, "origin", scenario="one_turn")
    oc_wait(lambda: oc.agent("origin"), desc="source agent in registry")
    oc.start_monitor_daemon(interval=1)
    source_sid = oc_wait(lambda: _active_id(oc, "origin"), timeout=60,
                         desc="source conversation id known")

    fork = oc.run("fork", "origin", "-n", "copy",
                  extra_env={"MOCK_SCENARIO": mock_backend.scenario("startup_idle")},
                  timeout=60)
    assert fork.returncode == 0, fork.stdout + fork.stderr
    oc_wait(lambda: oc.agent("copy"), timeout=60, desc="fork in registry")
    assert any(w.startswith("copy") for w in sandbox.list_windows(oc.session))

    if mock_backend is OPENCODE:
        argv = oc_wait(lambda: oc.agent_argv("copy", OPENCODE.script), timeout=30,
                       desc="fork launch line typed")
        assert f"--session {source_sid} --fork" in argv, argv
        # The fork's own conversation: new, recorded, and the source keeps its own
        fork_sid = oc_wait(lambda: _active_id(oc, "copy"), timeout=60,
                           desc="fork conversation id adopted")
        store = oc.opencode_sessions()
        assert fork_sid != source_sid
        assert fork_sid in store and store[fork_sid]["title"].endswith("(fork #1)")
        assert set(store) == {source_sid, fork_sid}
        assert _active_id(oc, "origin") == source_sid
        assert source_sid not in oc.agent("copy")["agent_session_ids"]


def test_cleanup_archives_dead_agents(oc, oc_wait, sandbox, mock_backend):
    """cleanup deletes sessions in registry status 'terminated' — which the
    monitor daemon assigns when it finds the tmux window gone."""
    oc.launch_on(mock_backend, "mortal", scenario="startup_idle")
    oc_wait(lambda: oc.agent("mortal"), desc="agent in registry")
    oc.start_monitor_daemon(interval=1)

    # Kill the tmux window behind overcode's back
    for w in sandbox.list_windows(oc.session):
        if w.startswith("mortal"):
            sandbox.cmd("kill-window", "-t", f"{oc.session}:{w}")
    oc_wait(
        lambda: not any(w.startswith("mortal") for w in sandbox.list_windows(oc.session)),
        desc="window gone",
    )
    oc_wait(
        lambda: (oc.agent("mortal") or {}).get("status") == "terminated",
        timeout=60,
        desc="daemon marks agent terminated",
    )

    def archived():
        result = oc.run("cleanup", timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        return oc.agent("mortal") is None

    oc_wait(archived, timeout=60, desc="agent archived after cleanup")
