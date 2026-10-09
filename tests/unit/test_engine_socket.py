"""engine.sock end to end: a real server thread and real client threads.

Unix socket paths are limited to ~104 bytes on macOS, so these tests use a
short directory under /tmp rather than pytest's tmp_path.
"""

import os
import shutil
import socket
import stat
import tempfile
import threading
import time
from pathlib import Path

import pytest

from overcode import engine_protocol as proto
from overcode import engine_socket
from overcode.engine_socket import EngineClient, EngineServer

pytestmark = pytest.mark.unit


@pytest.fixture
def sock_path():
    d = tempfile.mkdtemp(prefix="oc-eng-", dir="/tmp")
    yield Path(d) / "engine.sock"
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def server(sock_path):
    srv = EngineServer(sock_path)
    srv.start()
    yield srv
    srv.stop()


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class Recorder:
    def __init__(self):
        self.snapshots = []
        self.bells = []
        self.connections = []
        self.lock = threading.Lock()

    def on_change(self, snap):
        with self.lock:
            self.snapshots.append(snap.copy())

    def on_bell(self, agent, episode):
        with self.lock:
            self.bells.append((agent, episode))

    def on_connection(self, up):
        self.connections.append(up)

    @property
    def latest(self):
        with self.lock:
            return self.snapshots[-1] if self.snapshots else None


@pytest.fixture
def client_factory(sock_path):
    made = []

    def make():
        rec = Recorder()
        client = EngineClient(sock_path, rec.on_change, rec.on_bell, rec.on_connection)
        client.start()
        made.append(client)
        return client, rec

    yield make
    for client in made:
        client.stop()


def snap(**agents):
    return proto.Snapshot(agents={k: dict(v) for k, v in agents.items()})


class TestServerClient:
    def test_socket_is_private(self, server, sock_path):
        assert stat.S_IMODE(os.stat(sock_path).st_mode) & 0o077 == 0

    def test_a_view_gets_the_current_snapshot_on_connect(self, server, client_factory):
        server.publish(snap(a={"colour": "green"}))
        client, rec = client_factory()
        assert wait_for(lambda: rec.latest and rec.latest.agents == {"a": {"colour": "green"}})
        assert wait_for(lambda: client.connected)

    def test_changes_arrive_as_deltas(self, server, client_factory):
        server.publish(snap(a={"colour": "green", "tokens": 1}))
        client, rec = client_factory()
        assert wait_for(lambda: rec.latest is not None)
        server.publish(snap(a={"colour": "red", "tokens": 1}, b={"colour": "yellow"}))
        assert wait_for(lambda: rec.latest.agents == {
            "a": {"colour": "red", "tokens": 1}, "b": {"colour": "yellow"}})

    def test_a_quiet_tick_sends_nothing(self, server, client_factory):
        server.publish(snap(a={"colour": "green"}))
        client, rec = client_factory()
        assert wait_for(lambda: rec.latest is not None)
        seq = server._snapshot.seq
        delta = server.publish(snap(a={"colour": "green"}))
        assert delta.empty and server._snapshot.seq == seq
        time.sleep(0.1)
        assert len(rec.snapshots) == 1

    def test_a_steady_view_never_needs_to_resync(self, server, client_factory):
        """seq advances by exactly one per change, so deltas always apply in
        order; a view that had to reconnect would hide a seq bug behind a
        fresh snapshot."""
        client, rec = client_factory()
        assert wait_for(lambda: client.connected)
        for i in range(1, 51):
            server.publish(snap(a={"n": i}))
            assert server._snapshot.seq == i
        assert wait_for(lambda: rec.latest and rec.latest.agents == {"a": {"n": 50}})
        assert rec.connections == [True]
        assert [s.seq for s in rec.snapshots] == sorted({s.seq for s in rec.snapshots})

    def test_many_views_see_the_same_state(self, server, client_factory):
        views = [client_factory() for _ in range(5)]
        assert wait_for(lambda: server.view_count == 5)
        for i in range(20):
            server.publish(snap(a={"n": i}))
        for _client, rec in views:
            assert wait_for(lambda rec=rec: rec.latest and rec.latest.agents == {"a": {"n": 19}})

    def test_bells_are_delivered(self, server, client_factory):
        client, rec = client_factory()
        assert wait_for(lambda: server.view_count == 1)
        server.ring("a", {"colour": "red", "start": 123.0})
        assert wait_for(lambda: rec.bells == [("a", {"colour": "red", "start": 123.0})])


class TestAttendance:
    def test_no_views_is_unattended(self, server):
        assert server.attended is False

    def test_a_hidden_view_is_unattended_until_it_says_visible(self, server, client_factory):
        client, _rec = client_factory()
        assert wait_for(lambda: server.view_count == 1)
        assert server.attended is False
        client.set_visible(True)
        assert wait_for(lambda: server.attended)
        client.set_visible(False)
        assert wait_for(lambda: not server.attended)

    def test_a_closed_view_stops_counting(self, server, client_factory):
        client, _rec = client_factory()
        client.set_visible(True)
        assert wait_for(lambda: server.attended)
        client.stop()
        assert wait_for(lambda: not server.attended and server.view_count == 0)

    def test_focus_is_reported_for_visible_views(self, server, client_factory):
        client, _rec = client_factory()
        client.set_focus("agent-7")
        client.set_visible(True)
        assert wait_for(lambda: server.focused_agents == {"agent-7"})

    def test_burn_windows_come_from_visible_views(self, server, client_factory):
        a, _ = client_factory()
        b, _ = client_factory()
        a.set_burn_window(1.0)
        b.set_burn_window(3.0)
        a.set_visible(True)
        assert wait_for(lambda: server.burn_windows == {1.0})
        b.set_visible(True)
        assert wait_for(lambda: server.burn_windows == {1.0, 3.0})
        a.set_burn_window(None)
        assert wait_for(lambda: server.burn_windows == {3.0})

    def test_visibility_and_focus_are_restated_after_a_reconnect(self, sock_path, client_factory):
        srv = EngineServer(sock_path)
        srv.start()
        client, _rec = client_factory()
        client.set_visible(True)
        client.set_focus("x")
        client.set_burn_window(2.0)
        assert wait_for(lambda: srv.attended)
        srv.stop()
        srv2 = EngineServer(sock_path)
        srv2.start()
        try:
            assert wait_for(lambda: srv2.attended and srv2.focused_agents == {"x"}
                            and srv2.burn_windows == {2.0}, timeout=5)
        finally:
            srv2.stop()


class TestResilience:
    def test_the_client_reconnects_and_resyncs_after_an_engine_restart(
        self, sock_path, client_factory
    ):
        srv = EngineServer(sock_path)
        srv.start()
        srv.publish(snap(a={"v": 1}))
        client, rec = client_factory()
        assert wait_for(lambda: rec.latest and rec.latest.agents == {"a": {"v": 1}})
        srv.stop()
        assert wait_for(lambda: not client.connected)
        srv2 = EngineServer(sock_path)
        srv2.start()
        try:
            srv2.publish(snap(a={"v": 2}))
            assert wait_for(lambda: rec.latest.agents == {"a": {"v": 2}}, timeout=5)
            assert rec.connections[-1] is True
        finally:
            srv2.stop()

    def test_a_client_started_before_the_engine_connects_when_it_appears(
        self, sock_path, client_factory
    ):
        client, rec = client_factory()
        time.sleep(0.2)
        assert not client.connected
        srv = EngineServer(sock_path)
        srv.start()
        try:
            srv.publish(snap(a={"v": 1}))
            assert wait_for(lambda: rec.latest and rec.latest.agents == {"a": {"v": 1}}, timeout=5)
        finally:
            srv.stop()

    def test_a_view_that_stops_reading_is_dropped_not_buffered(
        self, server, sock_path, monkeypatch
    ):
        monkeypatch.setattr(engine_socket, "MAX_QUEUED_BYTES", 64 * 1024)
        stuck = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stuck.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        stuck.connect(str(sock_path))
        try:
            assert wait_for(lambda: server.view_count == 1)
            big = "x" * 2000
            for i in range(2000):
                server.publish(snap(a={"n": i, "pad": big}))
            assert wait_for(lambda: server.view_count == 0)
            assert server.dropped == 1
        finally:
            stuck.close()

    def test_publish_from_many_threads_keeps_seq_consistent(self, server, client_factory):
        client, rec = client_factory()
        assert wait_for(lambda: server.view_count == 1)

        def worker(k):
            for i in range(50):
                server.publish(snap(**{f"t{k}": {"n": i}}))

        threads = [threading.Thread(target=worker, args=(k,)) for k in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        final = server._snapshot
        assert wait_for(lambda: rec.latest and rec.latest.seq == final.seq)
        assert rec.latest.agents == final.agents
        assert not client.state.needs_resync

    def test_a_stale_socket_file_is_replaced_on_start(self, sock_path):
        sock_path.parent.mkdir(parents=True, exist_ok=True)
        sock_path.write_text("left over")
        srv = EngineServer(sock_path)
        srv.start()
        try:
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            probe.connect(str(sock_path))
            probe.close()
        finally:
            srv.stop()
        assert not sock_path.exists()


class TestLiveness:
    """A daemon whose main loop stops must not look live (2026-10-09: a
    26-hour freeze showed as current status because pings kept coming)."""

    def test_a_beating_engine_is_not_stalled(self, server, client_factory, monkeypatch):
        monkeypatch.setattr(engine_socket, "PING_SECONDS", 0.05)
        monkeypatch.setattr(engine_socket, "STALL_SECONDS", 0.5)
        server.publish(proto.Snapshot(agents={"a": {"status": "running"}}))
        client, _ = client_factory()
        stop = threading.Event()

        def beat():
            while not stop.is_set():
                server.beat()
                time.sleep(0.02)

        t = threading.Thread(target=beat, daemon=True)
        t.start()
        try:
            assert wait_for(lambda: client.connected)
            time.sleep(0.8)
            assert client.stalled_for() is None
        finally:
            stop.set()
            t.join()

    def test_a_frozen_main_loop_is_reported_then_clears(self, server, client_factory, monkeypatch):
        monkeypatch.setattr(engine_socket, "PING_SECONDS", 0.05)
        monkeypatch.setattr(engine_socket, "STALL_SECONDS", 0.3)
        server.publish(proto.Snapshot(agents={"a": {"status": "running"}}))
        client, _ = client_factory()
        assert wait_for(lambda: client.connected)
        # Nothing beats: the socket thread keeps pinging, but with a growing age
        assert wait_for(lambda: client.stalled_for() is not None)
        assert client.stalled_for() >= 0.3
        # The loop comes back: it beats again (every step, as the daemon does)
        stop = threading.Event()

        def beat():
            while not stop.is_set():
                server.beat()
                time.sleep(0.02)

        t = threading.Thread(target=beat, daemon=True)
        t.start()
        try:
            assert wait_for(lambda: client.stalled_for() is None)
        finally:
            stop.set()
            t.join()

    def test_a_silent_connection_is_stalled(self, server, client_factory, monkeypatch):
        monkeypatch.setattr(engine_socket, "PING_SECONDS", 60.0)
        monkeypatch.setattr(engine_socket, "SILENT_SECONDS", 0.3)
        server.publish(proto.Snapshot(agents={"a": {}}))
        client, _ = client_factory()
        assert wait_for(lambda: client.connected)
        assert wait_for(lambda: client.stalled_for() is not None)

    def test_an_engine_without_beat_age_is_never_called_stalled(self, server, client_factory, monkeypatch):
        """An older daemon's pings carry no beat age: no false alarm."""
        monkeypatch.setattr(engine_socket, "PING_SECONDS", 0.05)
        monkeypatch.setattr(engine_socket, "STALL_SECONDS", 0.1)
        monkeypatch.setattr(proto, "ping", lambda seq, beat_age=None: proto.encode({"t": "ping", "seq": seq}))
        server.publish(proto.Snapshot(agents={"a": {}}))
        client, _ = client_factory()
        assert wait_for(lambda: client.connected)
        time.sleep(0.4)
        assert client.stalled_for() is None

    def test_on_stall_is_called_while_the_beat_is_old(self, server, monkeypatch):
        monkeypatch.setattr(engine_socket, "STALL_SECONDS", 0.2)
        ages = []
        server.on_stall = ages.append
        server._poke()
        assert wait_for(lambda: ages, timeout=4.0)
        assert ages[0] >= 0.2

    def test_a_failing_on_stall_does_not_kill_the_socket_thread(self, server, client_factory, monkeypatch):
        monkeypatch.setattr(engine_socket, "STALL_SECONDS", 0.0)

        def boom(age):
            raise RuntimeError("watchdog bug")

        server.on_stall = boom
        time.sleep(0.2)
        server.publish(proto.Snapshot(agents={"a": {"status": "running"}}))
        client, rec = client_factory()
        assert wait_for(lambda: rec.latest is not None and "a" in rec.latest.agents)
