"""The monitor daemon publishes its state on engine.sock (0.6.0, step 2)."""

import shutil
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from overcode.engine_socket import EngineClient, EngineServer
from overcode.monitor_daemon_state import MonitorDaemonState, SessionDaemonState

pytestmark = pytest.mark.unit


def _state(**fleet):
    state = MonitorDaemonState(pid=1, status="active", **fleet)
    state.sessions = [
        SessionDaemonState(session_id="s1", name="one", current_status="running"),
        SessionDaemonState(session_id="s2", name="two", current_status="waiting_user"),
    ]
    return state


class TestSnapshot:
    def test_agents_are_keyed_by_session_id(self):
        snap = _state().to_engine_snapshot()
        assert set(snap.agents) == {"s1", "s2"}
        assert snap.agents["s2"]["current_status"] == "waiting_user"
        assert "sessions" not in snap.fleet

    def test_per_tick_liveness_fields_stay_out(self):
        """A quiet tick must publish an empty delta, so the fields that move
        every tick (loop count, loop time) are not part of the snapshot."""
        from overcode.engine_protocol import diff

        a = _state(loop_count=1, last_loop_time="t1", last_tick_duration_seconds=0.1)
        b = _state(loop_count=2, last_loop_time="t2", last_tick_duration_seconds=0.2)
        assert diff(a.to_engine_snapshot(), b.to_engine_snapshot()).empty
        assert not MonitorDaemonState.ENGINE_TICK_FIELDS & set(a.to_engine_snapshot().fleet)

    def test_idle_time_is_published_as_a_start_not_a_growing_count(self, monkeypatch):
        """Idle seconds grow every tick; idleness's start does not."""
        from overcode.engine_protocol import diff

        monkeypatch.setattr("time.time", lambda: 1000.0)
        a = _state(presence_idle_seconds=30.0).to_engine_snapshot()
        monkeypatch.setattr("time.time", lambda: 1002.0)
        b = _state(presence_idle_seconds=32.0).to_engine_snapshot()
        assert a.fleet["presence_idle_since"] == 970
        assert diff(a, b).empty
        assert "presence_idle_seconds" not in a.fleet

    def test_sessions_without_an_id_are_skipped(self):
        state = _state()
        state.sessions.append(SessionDaemonState(session_id="", name="ghost"))
        assert "" not in state.to_engine_snapshot().agents


class TestDaemonPublishes:
    def _daemon(self, tmp_path, monkeypatch):
        from overcode.monitor_daemon import MonitorDaemon

        monkeypatch.setattr("overcode.monitor_daemon.ensure_session_dir", lambda x: tmp_path)
        monkeypatch.setattr("overcode.monitor_daemon.get_monitor_daemon_pid_path",
                            lambda x: tmp_path / "pid")
        monkeypatch.setattr("overcode.monitor_daemon.get_monitor_daemon_state_path",
                            lambda x: tmp_path / "state.json")
        monkeypatch.setattr("overcode.monitor_daemon.get_agent_history_path",
                            lambda x: tmp_path / "history.csv")
        daemon = MonitorDaemon(tmux_session="test", session_manager=MagicMock(), tmux=MagicMock())
        daemon.presence = MagicMock()
        daemon.presence.get_current_state.return_value = (None, None, None)
        daemon.presence.available = False
        return daemon

    def test_a_daemon_built_for_a_test_binds_nothing(self, tmp_path, monkeypatch):
        daemon = self._daemon(tmp_path, monkeypatch)
        assert daemon._engine is None
        daemon._publish_state([SessionDaemonState(session_id="s1")])  # no socket, no error
        assert (tmp_path / "state.json").exists()

    def test_publish_state_pushes_the_snapshot_to_views(self, monkeypatch):
        d = Path(tempfile.mkdtemp(prefix="oc-dp-", dir="/tmp"))
        try:
            daemon = self._daemon(d, monkeypatch)
            daemon._start_engine_socket()
            assert daemon._engine is not None
            got = []
            client = EngineClient(daemon._engine.path, lambda snap: got.append(snap.copy()))
            client.start()
            try:
                daemon._publish_state([SessionDaemonState(session_id="s1", current_status="running")])
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and not (
                        got and got[-1].agents.get("s1", {}).get("current_status") == "running"):
                    time.sleep(0.01)
                assert got[-1].agents["s1"]["current_status"] == "running"
            finally:
                client.stop()
                daemon._engine.stop()
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_a_publish_failure_never_stops_the_tick(self, tmp_path, monkeypatch):
        daemon = self._daemon(tmp_path, monkeypatch)
        daemon._engine = MagicMock()
        daemon._engine.publish.side_effect = RuntimeError("boom")
        daemon._publish_state([])
        assert (tmp_path / "state.json").exists()

    def test_an_unbindable_socket_leaves_the_daemon_on_the_state_file(self, tmp_path, monkeypatch):
        daemon = self._daemon(tmp_path, monkeypatch)
        monkeypatch.setattr(EngineServer, "start", lambda self: (_ for _ in ()).throw(OSError("nope")))
        daemon._start_engine_socket()
        assert daemon._engine is None
