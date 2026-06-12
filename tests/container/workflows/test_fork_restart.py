"""Fork, restart, cleanup, sleep (coverage matrix rows 2 & 4)."""

import pytest

pytestmark = pytest.mark.timeout(180)


def test_restart_recovers_crashed_agent(oc, oc_wait):
    oc.launch("phoenix", scenario="crash_mid_task")
    oc_wait(lambda: oc.agent("phoenix"), desc="agent in registry")

    # crash_mid_task exits abruptly, leaving the shell prompt
    oc_wait(
        lambda: "$" in oc.pane("phoenix").splitlines()[-1]
        if oc.pane("phoenix").strip() else False,
        timeout=90,
        desc="mock claude crashed back to shell",
    )

    restart = oc.run("restart", "phoenix",
                     extra_env={"MOCK_SCENARIO": "startup_idle"}, timeout=60)
    assert restart.returncode == 0, restart.stdout + restart.stderr
    oc_wait(lambda: "Claude" in oc.pane("phoenix"), timeout=60,
            desc="agent running again after restart")


def test_fork_creates_sibling(oc, oc_wait, sandbox):
    oc.launch("origin", scenario="startup_idle")
    oc_wait(lambda: oc.agent("origin"), desc="source agent in registry")

    fork = oc.run("fork", "origin", "-n", "copy",
                  extra_env={"MOCK_SCENARIO": "startup_idle"}, timeout=60)
    assert fork.returncode == 0, fork.stdout + fork.stderr
    oc_wait(lambda: oc.agent("copy"), timeout=60, desc="fork in registry")
    assert any(w.startswith("copy") for w in sandbox.list_windows(oc.session))


def test_cleanup_archives_dead_agents(oc, oc_wait, sandbox):
    """cleanup deletes sessions in registry status 'terminated' — which the
    monitor daemon assigns when it finds the tmux window gone."""
    oc.launch("mortal", scenario="startup_idle")
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
