"""Heartbeats falling due together must not hold the daemon tick (#528, audit R16).

A heartbeat send takes ~0.12 s (copy-mode probe, load/paste per 10 lines
with a 0.1 s pause, Enter). Sent inline, 15 due together held the 2 s tick
past its interval and 50 held it 6.3 s (measured on this Mac against a real
tmux server); the sender overlaps them and the tick waits a fixed 0.3 s.

Run with ``OVERCODE_SCALE_TESTS=1 uv run pytest tests/scale -q``.
"""

import threading
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock, patch

import pytest

from tests.scale.budget import budget

pytestmark = [pytest.mark.scale]

AGENTS = 50
SEND_SECONDS = 0.12


def _sessions():
    out = []
    for i in range(AGENTS):
        s = Mock(id=f"s{i}", heartbeat_enabled=True, heartbeat_paused=False, is_asleep=False,
                 heartbeat_instruction="continue", heartbeat_frequency_seconds=300,
                 last_heartbeat_time=(datetime.now() - timedelta(hours=1)).isoformat(),
                 start_time=None, tmux_session="agents", tmux_window=f"w{i}", stats=None)
        s.name = f"agent{i}"
        out.append(s)
    return out


def test_fifty_due_heartbeats_hold_the_tick_for_about_one_send(tmp_path):
    from overcode.monitor_daemon import MonitorDaemon

    daemon = MonitorDaemon.for_test(tmp_path, log=MagicMock())
    sessions = _sessions()
    calls, lock = [], threading.Lock()

    def send(session, window, text, send_enter=True):
        time.sleep(SEND_SECONDS)
        with lock:
            calls.append(window)
        return True

    try:
        with patch("overcode.monitor_daemon.send_text_to_tmux_window", send):
            t0 = time.perf_counter()
            delivered = daemon.check_and_send_heartbeats(sessions)
            tick = time.perf_counter() - t0
            # later ticks (here back to back) collect the rest, resending nothing
            while len(delivered) < AGENTS and time.perf_counter() - t0 < 5:
                time.sleep(0.05)
                delivered |= daemon.check_and_send_heartbeats(sessions)
            all_in = time.perf_counter() - t0
    finally:
        daemon._heartbeats.shutdown()

    # today: 0.30 s (the fixed wait); inline it was 50 x 0.12 = 6 s
    assert tick <= budget(0.5), f"tick held {tick:.2f} s"
    # today: ~0.8 s for the last of 50 to go out (8 workers)
    assert all_in <= budget(2.0), f"last heartbeat delivered after {all_in:.2f} s"
    assert sorted(calls) == sorted(s.tmux_window for s in sessions)  # each exactly once
