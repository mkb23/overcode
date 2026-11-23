"""The sister API's ``/api/status`` on a 50-agent fleet (#528, re-audit item 2).

Sisters poll ``get_status_data`` every 10 s, and it sends each agent's pane
text. It used to build a fresh ``RealTmux`` per agent, whose object cache
never warmed: five tmux commands per agent on every request (250 at 50
agents). Now one shared client lists the session once per request and
captures only the panes whose listing moved since their last capture; an
unchanged pane's text is reused.

Run with ``OVERCODE_SCALE_TESTS=1 uv run pytest tests/scale -q``.
"""

import os
import shutil
import time
import uuid
from datetime import datetime
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.scale]

AGENTS = 50
REQUESTS = 6  # a minute of a sister's 10 s poll


@pytest.fixture
def server(monkeypatch):
    if shutil.which("tmux") is None:
        pytest.skip("tmux not installed")
    import libtmux

    socket = f"oc-webapi-scale-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    monkeypatch.setenv("OVERCODE_TMUX_SOCKET", socket)
    srv = libtmux.Server(socket_name=socket)
    script = "seq 1 200 | sed 's/$/ a line of agent output/'; sleep 600"
    srv.cmd("new-session", "-d", "-s", "agents", "-n", "w0", "sh", "-c", script)
    for i in range(1, AGENTS):
        srv.cmd("new-window", "-d", "-t", "agents", "-n", f"w{i}", "sh", "-c", script)
    time.sleep(1.5)  # output done; the activity second is now in the past
    from overcode import web_api
    reset = getattr(web_api, "_reset_pane_reader", None)
    if reset:
        reset()  # the shared client must address this test's socket
    try:
        yield srv
    finally:
        if reset:
            reset()
        srv.kill()


@pytest.fixture
def tmux_commands(monkeypatch):
    """Count every tmux command libtmux spawns (through both of its names)."""
    import libtmux
    from overcode import implementations

    counter = {"n": 0}

    class Counting(implementations._BoundedTmuxCmd):
        def __init__(self, *args, **kwargs):
            counter["n"] += 1
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(libtmux.server, "tmux_cmd", Counting)
    monkeypatch.setattr(libtmux.neo, "tmux_cmd", Counting)
    return counter


@pytest.fixture
def fleet_state():
    from overcode.monitor_daemon_state import MonitorDaemonState, SessionDaemonState

    state = MonitorDaemonState(sessions=[
        SessionDaemonState(session_id=f"s{i}", name=f"agent{i}", tmux_window=f"w{i}",
                           current_status="running")
        for i in range(AGENTS)
    ])
    state.last_loop_time = datetime.now().isoformat()
    with patch("overcode.web_api.get_monitor_daemon_state", return_value=state):
        yield state


def test_an_idle_fleet_costs_one_listing_per_request(server, tmux_commands, fleet_state):
    from overcode.web_api import get_status_data

    first = get_status_data("agents")
    assert all("a line of agent output" in a["pane_content"] for a in first["agents"])
    after_first = tmux_commands["n"]
    # the first request warms the client: the listing, then each pane looked
    # up and captured once (measured 202). Every request used to cost this
    # and more: 250 commands (5 per agent), cold client every time
    assert after_first <= 4 * AGENTS + 2, f"{after_first} tmux commands on the first request"

    for _ in range(REQUESTS - 1):
        again = get_status_data("agents")
        assert [a["pane_content"] for a in again["agents"]] == \
            [a["pane_content"] for a in first["agents"]]
    steady = (tmux_commands["n"] - after_first) / (REQUESTS - 1)
    # an unchanged fleet: the listing alone
    assert steady <= 1, f"{steady} tmux commands per idle request"


def test_a_pane_that_moved_is_captured_on_the_next_request(server, tmux_commands, fleet_state):
    from overcode.web_api import get_status_data

    get_status_data("agents")
    server.cmd("respawn-pane", "-k", "-t", "agents:=w3", "sh", "-c", "echo fresh-line; sleep 600")
    time.sleep(0.3)
    before = tmux_commands["n"]
    data = get_status_data("agents")
    assert "fresh-line" in data["agents"][3]["pane_content"]
    assert "fresh-line" not in data["agents"][4]["pane_content"]
    # the listing plus w3's capture (its pane object is still warm)
    assert tmux_commands["n"] - before <= 2, f"{tmux_commands['n'] - before} commands"
