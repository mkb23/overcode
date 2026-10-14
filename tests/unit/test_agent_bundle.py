"""agent_bundle: archived agents' files packed into day bundles, write-ahead.

The order is pack (fsync) -> archive record (fsync) -> out of sessions.json
-> remove the directory. These tests crash the archive at each step and
check that what is left is a duplicate the next pass or the orphan sweep
finishes, never a loss.
"""

import gzip
import json
import os
import time
from datetime import datetime
from unittest.mock import patch

import pytest

from overcode import agent_bundle
from overcode.paths import agent_dir, agent_file, bundle_path
from overcode.session_manager import PendingUpdates, SessionManager


@pytest.fixture(autouse=True)
def state_root(tmp_path, monkeypatch):
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "sessions"))
    monkeypatch.delenv("OVERCODE_SESSION_ID", raising=False)
    agent_bundle._checked_tails.clear()
    return tmp_path / "sessions"


@pytest.fixture
def sm(state_root):
    return SessionManager(skip_git_detection=True)


def _agent(sm, name="kid", tmux="agents", **files):
    session = sm.create_session(name=name, tmux_session=tmux, tmux_window=f"{name}-1",
                                command=["claude"])
    d = agent_dir(tmux, session.id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "hook_state.json").write_text(json.dumps({"event": "Stop", "timestamp": 1.0}))
    (d / "hook_events.jsonl").write_text('{"event": "Stop", "timestamp": 1.0}\n')
    (d / "hook_state.json.lock").write_text("")
    for name_, text in files.items():
        (d / name_.replace("_json", ".json").replace("_jsonl", ".jsonl")).write_text(text)
    return session


def _archive(sm, *ids):
    pending = PendingUpdates()
    for sid in ids:
        pending.archive_session(sid)
    sm.commit_pending(pending)
    return pending


def _records(sm, sid):
    return [s for s in sm.iter_archived_sessions() if s.id == sid]


class TestPack:
    def test_round_trip(self, sm):
        session = _agent(sm, report_json='{"status": "success"}')
        pointer = agent_bundle.pack_agent_dir("agents", session.id, {"name": "kid"},
                                              keep_hook_events=True)
        packed = agent_bundle.read_agent("agents", pointer)
        assert packed["sid"] == session.id and packed["name"] == "kid"
        assert packed["files"]["hook_state.json"]["event"] == "Stop"
        assert packed["files"]["report.json"] == {"status": "success"}
        assert "hook_events.jsonl" in packed["files"]
        assert "hook_state.json.lock" not in packed["files"]
        assert list(agent_bundle.iter_day("agents", pointer["day"]))[0]["sid"] == session.id

    def test_no_directory_no_member(self, sm):
        assert agent_bundle.pack_agent_dir("agents", "nobody", keep_hook_events=True) is None

    def test_the_episode_log_is_packed_from_its_tail(self, sm):
        big = "".join(f'{{"n": {i}, "pad": "{"x" * 200}"}}\n' for i in range(5000))
        session = _agent(sm, episodes_jsonl=big)
        pointer = agent_bundle.pack_agent_dir("agents", session.id, keep_hook_events=True)
        kept = agent_bundle.read_agent("agents", pointer)["files"]["episodes.jsonl"]
        assert len(kept) <= agent_bundle.EPISODES_TAIL_BYTES
        assert kept.endswith('"n": 4999, "pad": "' + "x" * 200 + '"}\n')
        assert kept.startswith('{"n": ')  # whole lines only

    def test_hook_events_can_be_left_out(self, sm):
        session = _agent(sm)
        pointer = agent_bundle.pack_agent_dir("agents", session.id, keep_hook_events=False)
        assert "hook_events.jsonl" not in agent_bundle.read_agent("agents", pointer)["files"]

    def test_a_torn_tail_is_cut_before_the_next_append(self, sm):
        first = _agent(sm, name="a")
        p1 = agent_bundle.pack_agent_dir("agents", first.id, keep_hook_events=True)
        path = bundle_path("agents", p1["day"])
        whole = gzip.compress(b'{"sid": "x"}\n')
        with open(path, "ab") as f:
            f.write(whole[: len(whole) // 2])  # a crash mid-append
        assert [r["sid"] for r in agent_bundle.iter_day("agents", p1["day"])] == [first.id]
        agent_bundle._checked_tails.clear()  # a new run
        second = _agent(sm, name="b")
        p2 = agent_bundle.pack_agent_dir("agents", second.id, keep_hook_events=True)
        assert p2["off"] == p1["off"] + p1["len"]
        assert [r["sid"] for r in agent_bundle.iter_day("agents", p1["day"])] == [first.id, second.id]
        assert agent_bundle.read_agent("agents", p2)["sid"] == second.id

    def test_a_bad_pointer_reads_none(self, sm):
        assert agent_bundle.read_agent("agents", {"day": "1999-01-01", "off": 0, "len": 5}) is None
        assert agent_bundle.read_agent("agents", {}) is None


class TestWriteAhead:
    def test_archive_packs_records_and_removes(self, sm):
        session = _agent(sm)
        pending = _archive(sm, session.id)
        assert pending.archived == [session.id]
        assert not agent_dir("agents", session.id).exists()
        (record,) = _records(sm, session.id)
        raw = json.loads(sm.archive_file.read_text().splitlines()[-1])
        assert agent_bundle.read_agent("agents", raw["bundle"])["sid"] == session.id
        assert record.name == "kid"

    def test_crash_after_packing_loses_nothing(self, sm):
        session = _agent(sm)
        with patch.object(SessionManager, "_append_archive_records",
                          side_effect=OSError("disk full")):
            with pytest.raises(OSError):
                _archive(sm, session.id)
        # Still live, directory kept; the member is an unreferenced duplicate
        assert sm.get_session(session.id) is not None
        assert agent_dir("agents", session.id).is_dir()
        assert _records(sm, session.id) == []
        _archive(sm, session.id)  # the next pass
        assert sm.get_session(session.id) is None
        assert len(_records(sm, session.id)) == 1
        assert not agent_dir("agents", session.id).exists()

    def test_crash_after_the_archive_record_leaves_a_duplicate(self, sm):
        session = _agent(sm)
        with patch.object(sm._store, "_write", side_effect=OSError("crash")):
            with pytest.raises(OSError):
                _archive(sm, session.id)
        assert sm.get_session(session.id) is not None  # still live
        assert len(_records(sm, session.id)) == 1      # and archived
        assert agent_dir("agents", session.id).is_dir()
        _archive(sm, session.id)
        assert sm.get_session(session.id) is None
        # Two lines, one agent: readers keep the last per id
        assert len(_records(sm, session.id)) == 2
        assert session.id in {s.id for s in sm.list_archived_sessions()}

    def test_crash_before_the_directory_goes_is_swept(self, sm):
        session = _agent(sm)
        with patch("overcode.session_manager.remove_agent_dir", side_effect=OSError("crash")):
            with pytest.raises(OSError):
                _archive(sm, session.id)
        assert sm.get_session(session.id) is None
        assert agent_dir("agents", session.id).is_dir()  # an orphan
        counts = agent_bundle.sweep_orphans(
            "agents", lambda: set(sm.sessions_by_id()), archived=sm.archived_ids,
            record=lambda *a: pytest.fail("already archived"), min_age=0)
        assert counts["removed"] == 1
        assert not agent_dir("agents", session.id).exists()

    def test_two_daemons_archiving_the_same_agent(self, sm, state_root):
        session = _agent(sm)
        other = SessionManager(skip_git_detection=True)
        # Both pack before either commits
        theirs = other._pack_for_archive([session.id])
        _archive(sm, session.id)
        with patch.object(SessionManager, "_pack_for_archive", return_value=theirs):
            pending = _archive(other, session.id)
        assert pending.archived == []  # lost the race: moved nothing
        assert len(_records(sm, session.id)) == 1

    def test_a_late_hook_after_removal_is_swept(self, sm, monkeypatch):
        from overcode.hook_handler import write_hook_state

        session = _agent(sm)
        _archive(sm, session.id)
        monkeypatch.setenv("OVERCODE_SESSION_ID", session.id)
        write_hook_state("SessionEnd", "agents", "kid")  # the process's last word
        assert agent_dir("agents", session.id).is_dir()
        counts = sm.tidy_agent_storage("agents")  # too recent: kept
        assert counts.get("kept") == 1 and agent_dir("agents", session.id).is_dir()
        old = time.time() - 3600
        os.utime(agent_dir("agents", session.id), (old, old))
        assert sm.tidy_agent_storage("agents")["removed"] == 1
        assert not agent_dir("agents", session.id).exists()

    def test_delete_without_archive_removes_the_directory(self, sm):
        session = _agent(sm)
        sm.delete_session(session.id, archive=False)
        assert not agent_dir("agents", session.id).exists()
        assert _records(sm, session.id) == []

    def test_delete_sessions_is_one_commit(self, sm):
        ids = [_agent(sm, name=f"a{i}").id for i in range(5)]
        writes = []
        real = sm._store._write
        with patch.object(sm._store, "_write", side_effect=lambda *a, **k: writes.append(1) or real(*a, **k)):
            assert sorted(sm.delete_sessions(ids)) == sorted(ids)
        assert writes == [1]
        assert all(not agent_dir("agents", i).exists() for i in ids)


class TestOrphanSweep:
    def _old(self, path):
        old = time.time() - 3600
        os.utime(path, (old, old))

    def test_an_unarchived_orphan_is_packed_and_recorded(self, sm):
        d = agent_dir("agents", "lost-sid")
        d.mkdir(parents=True)
        (d / "report.json").write_text('{"status": "failure"}')
        self._old(d)
        counts = sm.tidy_agent_storage("agents")
        assert counts["packed"] == 1 and not d.exists()
        raw = [json.loads(line) for line in sm.archive_file.read_text().splitlines()]
        (orphan,) = [r for r in raw if r["id"] == "lost-sid"]
        assert orphan["orphan"] is True
        assert agent_bundle.read_agent("agents", orphan["bundle"])["files"]["report.json"] == \
            {"status": "failure"}

    def test_a_directory_whose_packing_failed_is_packed_not_deleted(self, sm):
        """A full disk at archive time: the record is written, the directory kept."""
        session = _agent(sm, report_json='{"status": "success"}')
        with patch("overcode.agent_bundle._append_member", side_effect=OSError(28, "ENOSPC")):
            sm.delete_session(session.id)
        assert sm.is_archived(session.id) and agent_dir("agents", session.id).is_dir()
        assert sm.find_archived(session.id)._bundle is None
        self._old(agent_dir("agents", session.id))
        counts = sm.tidy_agent_storage("agents")
        assert counts["packed"] == 1 and counts.get("removed", 0) == 0
        assert not agent_dir("agents", session.id).exists()
        archived = sm.find_archived(session.id)
        assert archived.name == "kid"  # the real record, now with its bundle
        assert agent_bundle.read_agent("agents", archived._bundle)["files"]["report.json"] == \
            {"status": "success"}

    def test_live_and_recent_directories_are_left_alone(self, sm):
        live = _agent(sm)
        self._old(agent_dir("agents", live.id))
        fresh = agent_dir("agents", "just-launched")
        fresh.mkdir(parents=True)
        counts = sm.tidy_agent_storage("agents")
        assert agent_dir("agents", live.id).is_dir() and fresh.is_dir()
        assert counts.get("packed", 0) == 0 and counts.get("removed", 0) == 0

    def test_a_launch_during_the_sweep_is_not_taken(self, sm):
        """The record is read after the scandir, so a launch between them is live."""
        d = agent_dir("agents", "racing-sid")
        d.mkdir(parents=True)
        self._old(d)  # an old directory, revived just now
        counts = agent_bundle.sweep_orphans(
            "agents", lambda: {"racing-sid"}, archived=lambda ids: set(ids),
            record=lambda *a: None)
        assert d.is_dir() and counts["removed"] == 0

    def test_bounded(self, sm):
        for i in range(5):
            d = agent_dir("agents", f"o{i}")
            d.mkdir(parents=True)
            self._old(d)
        counts = agent_bundle.sweep_orphans(
            "agents", set, archived=lambda ids: set(ids), record=lambda *a: None, limit=2)
        assert counts["removed"] == 2


class TestRetention:
    def _days(self, *days, size=100):
        for day in days:
            path = bundle_path("agents", day)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x" * size)

    def test_off_by_default(self, sm):
        self._days("2020-01-01")
        assert agent_bundle.prune() == []
        assert sm.tidy_agent_storage("agents", sweep_limit=0)["pruned"] == 0
        assert bundle_path("agents", "2020-01-01").exists()

    def test_by_days(self):
        self._days("2026-09-01", "2026-10-01", "2026-10-12")
        removed = agent_bundle.prune(retention_days=30, now=datetime(2026, 10, 12, 12))
        assert [p.name for p in removed] == ["2026-09-01.agents.gz"]

    def test_by_bytes_oldest_first_never_today(self):
        self._days("2026-10-10", "2026-10-11", "2026-10-12", size=1000)
        removed = agent_bundle.prune(max_bytes=1500, now=datetime(2026, 10, 12, 12))
        assert [p.name for p in removed] == ["2026-10-10.agents.gz", "2026-10-11.agents.gz"]
        assert bundle_path("agents", "2026-10-12").exists()

    def test_limit_and_every_tmux_session(self):
        self._days("2020-01-01", "2020-01-02")
        path = bundle_path("work", "2020-01-03")
        path.parent.mkdir(parents=True)
        path.write_bytes(b"x")
        removed = agent_bundle.prune(retention_days=1, limit=2, now=datetime(2026, 10, 12))
        assert len(removed) == 2
        assert len(agent_bundle.prune(retention_days=1, now=datetime(2026, 10, 12))) == 1

    def test_config(self, monkeypatch):
        from overcode import config

        monkeypatch.setattr(config, "_get_config_value",
                            lambda key, default=None: {"retention_days": 30, "max_bytes": "1GB",
                                                       "keep_hook_events": False}
                            if key == "agent_history" else default)
        cfg = config.get_agent_history_config()
        assert cfg == {"retention_days": 30.0, "max_bytes": 1024 ** 3, "keep_hook_events": False}
        monkeypatch.setattr(config, "_get_config_value", lambda key, default=None: default)
        assert config.get_agent_history_config() == {
            "retention_days": 0.0, "max_bytes": 0, "keep_hook_events": True}

    def test_parse_size(self):
        from overcode.config import parse_size

        assert parse_size("1GB") == 1024 ** 3
        assert parse_size("500 MB") == 500 * 1024 ** 2
        assert parse_size(2048) == 2048
        assert parse_size("nonsense") == 0
        assert parse_size(True) == 0


def test_agent_file_layout_matches_the_bundle(sm):
    session = _agent(sm)
    assert agent_file("agents", session.id, "hook_state").exists()
