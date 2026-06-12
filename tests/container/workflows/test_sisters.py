"""Sister instance aggregation (coverage matrix row 10).

Two fully isolated OverCode instances run in this container (own tmux server,
state dir, HOME). The local instance polls the remote's web /api/status —
the same HTTP path used across real machines (sister_poller.py).
"""

import pytest

pytestmark = pytest.mark.timeout(240)


@pytest.fixture
def remote(make_oc, oc_wait):
    """The 'remote machine': an agent, a monitor daemon, and a web server."""
    remote = make_oc("remote")
    remote.launch("far-agent", scenario="task_running")
    remote.start_monitor_daemon(interval=1)

    def published():
        sessions = remote.daemon_state().get("sessions", [])
        return any(s.get("name") == "far-agent" for s in sessions)

    oc_wait(published, timeout=60, desc="remote daemon publishing far-agent")
    return remote, remote.start_web()


def _wire_sister(local, remote_url, remote_session, api_key=""):
    config = (
        "sisters:\n"
        f"  - name: machine2\n"
        f"    url: \"{remote_url}\"\n"
        f"    tmux_session: \"{remote_session}\"\n"
        f"    ssh: \"user@machine2.test\"\n"
    )
    if api_key:
        config += f"    api_key: \"{api_key}\"\n"
    local.config_file.write_text(config)


def test_sister_listed_and_reachable(make_oc, remote):
    remote_oc, remote_url = remote
    local = make_oc("local")
    _wire_sister(local, remote_url, remote_oc.session)

    listing = local.run("sister", "list", session_arg=False)
    assert listing.returncode == 0, listing.stdout + listing.stderr
    assert "machine2" in listing.stdout

    status = local.run("sister", "status", session_arg=False, timeout=30)
    assert status.returncode == 0, status.stdout + status.stderr
    combined = status.stdout + status.stderr
    assert "machine2" in combined
    assert "unreachable" not in combined.lower() or "far-agent" in combined


def test_sister_agents_aggregate_into_list(make_oc, remote, oc_wait):
    remote_oc, remote_url = remote
    local = make_oc("local")
    _wire_sister(local, remote_url, remote_oc.session)

    def far_agent_visible():
        result = local.run("list", "--sisters", timeout=30)
        return result.returncode == 0 and "far-agent" in result.stdout

    oc_wait(far_agent_visible, timeout=60, desc="remote agent in local list --sisters")


def test_sister_respects_api_key(make_oc, remote, oc_wait):
    remote_oc, _ = remote
    # Restart remote web with an API key required
    remote_oc.run("web", "--stop", timeout=15)
    keyed_url = remote_oc.start_web("web:\n  api_key: sister-secret\n")

    local = make_oc("local")
    _wire_sister(local, keyed_url, remote_oc.session, api_key="sister-secret")

    def far_agent_visible():
        result = local.run("list", "--sisters", timeout=30)
        return result.returncode == 0 and "far-agent" in result.stdout

    oc_wait(far_agent_visible, timeout=60,
            desc="keyed sister aggregates with correct api_key")
