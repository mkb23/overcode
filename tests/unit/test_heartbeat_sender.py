"""Heartbeats are sent off the daemon tick, each exactly once (#528, audit R16)."""

import os
import shutil
import threading
import time
import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock, patch

import pytest

from overcode.heartbeat_sender import HeartbeatSender


def _session(i, last=None):
    s = Mock()
    s.id = f"sess-{i}"
    s.name = f"agent-{i}"
    s.heartbeat_enabled = True
    s.heartbeat_paused = False
    s.is_asleep = False
    s.heartbeat_instruction = "continue"
    s.heartbeat_frequency_seconds = 300
    s.last_heartbeat_time = last or (datetime.now() - timedelta(hours=1)).isoformat()
    s.start_time = (datetime.now() - timedelta(hours=2)).isoformat()
    s.tmux_session = "agents"
    s.tmux_window = f"w{i}"
    s.stats = None
    return s


@pytest.fixture
def daemon(tmp_path):
    from overcode.monitor_daemon import MonitorDaemon

    d = MonitorDaemon.for_test(tmp_path, log=MagicMock())
    yield d
    d._heartbeats.shutdown()


class _Sends:
    """A send_text_to_tmux_window double: counts calls, takes ``seconds``."""

    def __init__(self, seconds=0.0, ok=True):
        self.seconds = seconds
        self.ok = ok
        self.calls = []
        self.lock = threading.Lock()
        self.release = threading.Event()
        self.release.set()

    def __call__(self, session, window, text, send_enter=True):
        self.release.wait(5)
        time.sleep(self.seconds)
        with self.lock:
            self.calls.append(window)
        return self.ok


class TestTickStaysBounded:

    def test_a_burst_of_due_heartbeats_costs_about_one_send(self, daemon):
        """20 heartbeats at ~0.12 s each held the tick 2.4 s; now they overlap."""
        sessions = [_session(i) for i in range(20)]
        sends = _Sends(seconds=0.12)
        with patch("overcode.monitor_daemon.send_text_to_tmux_window", sends):
            t0 = time.perf_counter()
            triggered = daemon.check_and_send_heartbeats(sessions)
            elapsed = time.perf_counter() - t0
            # whatever outlasted the tick's wait is collected by the next ticks
            for _ in range(10):
                if len(triggered) == 20:
                    break
                time.sleep(0.1)
                triggered |= daemon.check_and_send_heartbeats(sessions)
        assert elapsed < 0.6, f"tick held {elapsed:.2f} s"
        assert sorted(sends.calls) == sorted(s.tmux_window for s in sessions)
        assert triggered == {s.id for s in sessions}
        assert all(daemon._pending.fields[s.id]["last_heartbeat_time"] for s in sessions)


class TestExactlyOnce:

    def test_a_send_outlasting_the_wait_is_neither_repeated_nor_lost(self, daemon):
        session = _session(1)
        sends = _Sends()
        sends.release.clear()  # the send hangs until released
        with patch("overcode.monitor_daemon.send_text_to_tmux_window", sends):
            assert daemon.check_and_send_heartbeats([session]) == set()
            # Still in flight: the next tick does not send it again
            assert daemon.check_and_send_heartbeats([session]) == set()
            sends.release.set()
            time.sleep(0.1)
            # Collected now; the session this tick read still has the old
            # stamp (the new one is only staged), yet it is not resent
            assert daemon.check_and_send_heartbeats([session]) == {session.id}
        assert sends.calls == ["w1"]
        assert daemon._pending.fields[session.id]["last_heartbeat_time"]

    def test_a_failed_send_is_not_stamped_and_is_tried_again(self, daemon):
        session = _session(1)
        sends = _Sends(ok=False)
        with patch("overcode.monitor_daemon.send_text_to_tmux_window", sends):
            assert daemon.check_and_send_heartbeats([session]) == set()
            assert session.id not in daemon._pending.fields
            sends.ok = True
            assert daemon.check_and_send_heartbeats([session]) == {session.id}
        assert sends.calls == ["w1", "w1"]

    def test_a_send_that_raises_counts_as_failed(self):
        sender = HeartbeatSender()
        sender.submit("s", "n", "t", lambda: 1 / 0)
        [result] = sender.collect(1.0)
        assert result.ok is False and not sender.in_flight("s")
        sender.shutdown()


class TestNamedBuffers:

    def test_each_send_pastes_its_own_buffer_and_deletes_it(self):
        from overcode.tmux_utils import send_text_to_tmux_window

        with patch("overcode.tmux_utils.subprocess.run") as run, \
                patch("overcode.tmux_utils.time.sleep"):
            run.return_value = MagicMock(returncode=0, stdout="0")
            send_text_to_tmux_window("agents", "w", "\n".join(["x"] * 15))
        args = [c.args[0] for c in run.call_args_list]
        loads = [a for a in args if "load-buffer" in a]
        pastes = [a for a in args if "paste-buffer" in a]
        names = {a[a.index("-b") + 1] for a in loads + pastes}
        assert len(loads) == len(pastes) == 2 and len(names) == 1
        assert all("-d" in a for a in pastes)

    def test_concurrent_sends_do_not_paste_each_others_text(self, monkeypatch):
        """tmux's default buffer is shared: two sends at once crossed texts."""
        if shutil.which("tmux") is None:
            pytest.skip("tmux not installed")
        import libtmux
        from overcode.tmux_utils import send_text_to_tmux_window

        socket = f"oc-hb-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        monkeypatch.setenv("OVERCODE_TMUX_SOCKET", socket)
        server = libtmux.Server(socket_name=socket)
        server.cmd("new-session", "-d", "-s", "agents", "-n", "a", "cat")
        server.cmd("new-window", "-d", "-t", "agents", "-n", "b", "cat")
        try:
            def send(window):
                for i in range(5):
                    send_text_to_tmux_window("agents", window, f"to-{window}-{i}")

            threads = [threading.Thread(target=send, args=(w,)) for w in "ab"]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)
            time.sleep(0.3)
            for window, other in (("a", "b"), ("b", "a")):
                text = server.cmd("capture-pane", "-p", "-t", f"agents:={window}").stdout
                joined = "\n".join(text)
                assert all(f"to-{window}-{i}" in joined for i in range(5))
                assert f"to-{other}-" not in joined
            buffers = server.cmd("list-buffers", "-F", "#{buffer_name}").stdout
            assert not [b for b in buffers if b.startswith("overcode-")]
        finally:
            server.kill()
