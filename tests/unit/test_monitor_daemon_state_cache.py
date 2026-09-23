"""``MonitorDaemonState.load`` re-parses only when the state file changed (audit R4)."""

import json
import os
from datetime import datetime
from pathlib import Path

from overcode import monitor_daemon_state as mds
from overcode.monitor_daemon_state import (
    MonitorDaemonState,
    SessionDaemonState,
    get_monitor_daemon_state,
    reset_load_cache,
)


def _settle(path: Path, seconds_ago: float = 1.0) -> None:
    """Age ``path``'s mtime so the cache trusts its signature (see stat_gate)."""
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns - int(seconds_ago * 1e9)))


def _state(loop_count: int = 1, n: int = 3) -> MonitorDaemonState:
    return MonitorDaemonState(
        pid=4242,
        status="active",
        loop_count=loop_count,
        last_loop_time=datetime.now().isoformat(),
        sessions=[
            SessionDaemonState(session_id=f"s{i}", name=f"agent-{i}", green_time_seconds=i)
            for i in range(n)
        ],
    )


def _count_reads(monkeypatch) -> list:
    calls = []
    original = MonitorDaemonState._read_state_file

    def counting(cls, path):
        calls.append(1)
        return original(path)

    monkeypatch.setattr(MonitorDaemonState, "_read_state_file", classmethod(counting))
    return calls


def _uncached(path: Path) -> MonitorDaemonState:
    with open(path) as f:
        return MonitorDaemonState.from_dict(json.load(f))


class TestLoadCache:
    def test_unchanged_file_is_parsed_once(self, tmp_path, monkeypatch):
        path = tmp_path / "state.json"
        _state().save(path)
        _settle(path)
        reads = _count_reads(monkeypatch)
        first = MonitorDaemonState.load(path)
        for _ in range(5):
            assert MonitorDaemonState.load(path) is first
        assert len(reads) == 1
        assert first.loop_count == 1 and len(first.sessions) == 3

    def test_output_matches_an_uncached_parse_across_writes(self, tmp_path, monkeypatch):
        path = tmp_path / "state.json"
        _state(loop_count=1).save(path)
        _settle(path)
        reads = _count_reads(monkeypatch)
        assert MonitorDaemonState.load(path).to_dict() == _uncached(path).to_dict()

        # The daemon's own writer: mkstemp + rename, a new inode each save
        _state(loop_count=2).save(path)
        _settle(path)
        loaded = MonitorDaemonState.load(path)
        assert loaded.loop_count == 2
        assert loaded.to_dict() == _uncached(path).to_dict()

        # A same-size in-place rewrite: only mtime_ns moves
        data = json.loads(path.read_text())
        data["loop_count"] = 3
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        _settle(path)
        loaded = MonitorDaemonState.load(path)
        assert loaded.loop_count == 3
        assert loaded.to_dict() == _uncached(path).to_dict()
        assert len(reads) == 3

    def test_a_just_saved_file_is_reread_until_its_stamp_settles(self, tmp_path):
        path = tmp_path / "state.json"
        _state(loop_count=1).save(path)
        assert MonitorDaemonState.load(path).loop_count == 1
        _state(loop_count=2).save(path)
        assert MonitorDaemonState.load(path).loop_count == 2

    def test_missing_file_is_none_and_not_remembered(self, tmp_path, monkeypatch):
        path = tmp_path / "state.json"
        reads = _count_reads(monkeypatch)
        assert MonitorDaemonState.load(path) is None
        _state().save(path)
        _settle(path)
        assert MonitorDaemonState.load(path) is not None
        path.unlink()
        assert MonitorDaemonState.load(path) is None
        assert len(reads) == 3

    def test_corrupt_file_is_none_and_not_reparsed_every_call(self, tmp_path, monkeypatch):
        path = tmp_path / "state.json"
        path.write_text("{not json")
        _settle(path)
        reads = _count_reads(monkeypatch)
        assert MonitorDaemonState.load(path) is None
        assert MonitorDaemonState.load(path) is None
        assert len(reads) == 1
        _state().save(path)
        _settle(path)
        assert MonitorDaemonState.load(path).loop_count == 1

    def test_caches_are_per_path(self, tmp_path, monkeypatch):
        a, b = tmp_path / "a.json", tmp_path / "b.json"
        _state(loop_count=1).save(a)
        _state(loop_count=2).save(b)
        _settle(a)
        _settle(b)
        reads = _count_reads(monkeypatch)
        assert MonitorDaemonState.load(a).loop_count == 1
        assert MonitorDaemonState.load(b).loop_count == 2
        assert MonitorDaemonState.load(a).loop_count == 1
        assert MonitorDaemonState.load(b).loop_count == 2
        assert len(reads) == 2

    def test_reset_load_cache_forces_a_reparse(self, tmp_path, monkeypatch):
        path = tmp_path / "state.json"
        _state().save(path)
        _settle(path)
        reads = _count_reads(monkeypatch)
        MonitorDaemonState.load(path)
        reset_load_cache()
        MonitorDaemonState.load(path)
        assert len(reads) == 2

    def test_get_monitor_daemon_state_goes_through_the_cache(self, tmp_path, monkeypatch):
        path = tmp_path / "state.json"
        _state().save(path)
        _settle(path)
        monkeypatch.setattr(mds, "get_monitor_daemon_state_path", lambda session: path)
        reads = _count_reads(monkeypatch)
        first = get_monitor_daemon_state("agents")
        assert get_monitor_daemon_state("agents") is first
        assert len(reads) == 1
