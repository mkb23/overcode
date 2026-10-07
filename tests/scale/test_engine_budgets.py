"""The engine's idle cost: an unattended fleet must be nearly free (0.6.0).

docs/design/engine-0.6.md: unattended, the engine stats each agent's hook
state file every 2 s and runs a full tick every 10 s; everything else
(git, burn, the 5 s stats sweep, pane captures of settled agents) stops.
The wake scan is the only per-second cost that grows with the fleet, so it
gets a budget here. Budget: 50 idle agents, ≤ 0.5% of a core.

Run with ``OVERCODE_SCALE_TESTS=1 uv run pytest tests/scale -q``.
"""

import time
from unittest.mock import MagicMock

import pytest

from overcode.monitor_daemon import WAKE_SCAN_ATTENDED_SECONDS, WAKE_SCAN_UNATTENDED_SECONDS
from overcode.monitor_daemon_state import MonitorDaemonState, SessionDaemonState

pytestmark = [pytest.mark.scale]

AGENTS = 50


@pytest.fixture
def daemon(tmp_path):
    from overcode.monitor_daemon import MonitorDaemon

    d = MonitorDaemon.__new__(MonitorDaemon)
    d.state_path = tmp_path / "monitor_daemon_state.json"
    d.state = MonitorDaemonState(pid=1)
    d.state.sessions = [SessionDaemonState(session_id=f"s{i}", name=f"agent{i}")
                        for i in range(AGENTS)]
    d._hook_signatures = {}
    d.log = MagicMock()
    for i in range(AGENTS):
        (tmp_path / f"hook_state_agent{i}.json").write_text('{"event": "Stop"}')
    d._hook_changes()  # first sight
    return d


def _scan_ms(daemon, runs=200):
    start = time.perf_counter()
    for _ in range(runs):
        assert daemon._hook_changes() == set()
    return (time.perf_counter() - start) / runs * 1000


def test_unattended_wake_scan_of_an_idle_fleet_is_nearly_free(daemon):
    per_scan = _scan_ms(daemon)
    share_of_core = per_scan / 1000 / WAKE_SCAN_UNATTENDED_SECONDS
    # today: ~0.15 ms per scan of 50 agents -> ~0.008% of a core
    assert share_of_core <= 0.005, f"{per_scan:.3f} ms per scan"


def test_attended_wake_scan_stays_cheap(daemon):
    per_scan = _scan_ms(daemon)
    share_of_core = per_scan / 1000 / WAKE_SCAN_ATTENDED_SECONDS
    # today: ~0.15 ms per scan -> ~0.06% of a core at 4 scans a second
    assert share_of_core <= 0.01, f"{per_scan:.3f} ms per scan"
