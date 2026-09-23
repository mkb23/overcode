"""The append-only session archive and the daemon's terminated-session pass.

archive.jsonl replaces the whole-file-rewritten archive.json: a legacy file
is migrated once, every reader sees the same records, an archive is one
appended line, and a read after an append parses only that line. The
monitor daemon moves terminated entries out of sessions.json after a
configurable grace, through the same record ``overcode cleanup`` writes.
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from overcode import config
from overcode.session_manager import PendingUpdates, Session, SessionManager, SessionStats
from tests.daemon_tick_harness import (
    FrozenClock,
    ScriptedDetector,
    count_sessions_io,
    make_daemon,
    seed_sessions,
    seed_steady_state,
    settle,
)


def _session(sid, name, tmux="agents", status="running", **kw) -> Session:
    return Session(
        id=sid,
        name=name,
        tmux_session=tmux,
        tmux_window=name,
        command=["claude"],
        start_directory=None,
        start_time="2026-01-01T00:00:00",
        status=status,
        stats=SessionStats(interaction_count=3, current_task="done stuff"),
        agent_session_ids=["sid-a"],
        **kw,
    )


def _legacy_record(session: Session, end_time="2026-01-02T00:00:00") -> dict:
    """What archive.json held for a session: its dict plus end_time and status."""
    record = session.to_dict()
    record["end_time"] = end_time
    record["status"] = "archived"
    return record


def _lines(path: Path):
    return [json.loads(line) for line in path.read_bytes().split(b"\n") if line.strip()]


def _dicts(sessions):
    return [(s.to_dict(), getattr(s, "_end_time", None)) for s in sessions]


class TestJsonlArchive:
    def test_delete_session_appends_the_cleanup_record(self, tmp_path):
        sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        s = sm.create_session(name="a", tmux_session="agents", tmux_window="w", command=["claude"])
        before = json.loads(sm.state_file.read_text())[s.id]
        sm.delete_session(s.id)
        lines = _lines(sm.archive_file)
        assert len(lines) == 1
        record = lines[0]
        assert record["status"] == "archived" and record["end_time"]
        expected = dict(before, status="archived", end_time=record["end_time"])
        assert record == expected
        assert sm.get_session(s.id) is None
        archived = sm.list_archived_sessions()
        assert [a.id for a in archived] == [s.id]
        assert archived[0].status == "archived" and archived[0]._end_time == record["end_time"]

    def test_each_archive_is_one_appended_line(self, tmp_path):
        sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        ids = []
        for i in range(4):
            s = sm.create_session(
                name=f"a{i}", tmux_session="agents", tmux_window=f"w{i}", command=[]
            )
            ids.append(s.id)
        sizes = []
        for sid in ids:
            sm.delete_session(sid)
            sizes.append(sm.archive_file.stat().st_size)
        assert [r["id"] for r in _lines(sm.archive_file)] == ids
        growth = [b - a for a, b in zip(sizes, sizes[1:])]
        assert max(growth) - min(growth) < 64  # each append adds one record, not the file again

    def test_legacy_archive_json_is_migrated_once_on_first_access(self, tmp_path):
        sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        legacy_sessions = [_session(f"old-{i}", f"old-{i}") for i in range(3)]
        legacy = {s.id: _legacy_record(s) for s in legacy_sessions}
        sm.legacy_archive_file.write_text(json.dumps(legacy, indent=2))
        # What the old reader produced from that file
        expected = []
        for record in legacy.values():
            data = dict(record)
            end_time = data.pop("end_time")
            session = Session.from_dict(data)
            expected.append((session.to_dict(), end_time))

        archived = sm.list_archived_sessions()
        assert _dicts(archived) == expected
        assert not sm.legacy_archive_file.exists()
        assert (tmp_path / "archive.json.migrated").exists()
        assert [r["id"] for r in _lines(sm.archive_file)] == list(legacy)
        assert _lines(sm.archive_file) == list(legacy.values())

        # Idempotent: a second access changes nothing
        sig = os.stat(sm.archive_file)
        assert _dicts(sm.list_archived_sessions()) == expected
        assert os.stat(sm.archive_file).st_mtime_ns == sig.st_mtime_ns
        assert len(_lines(sm.archive_file)) == 3

        # A fresh manager sees the same records, and an append goes after them
        fresh = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        assert _dicts(fresh.list_archived_sessions()) == expected
        s = fresh.create_session(name="new", tmux_session="agents", tmux_window="w", command=[])
        fresh.delete_session(s.id)
        assert [r["id"] for r in _lines(sm.archive_file)] == [*legacy, s.id]

    def test_migration_also_runs_before_the_first_append(self, tmp_path):
        sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        old = _session("old-1", "old-1")
        sm.legacy_archive_file.write_text(json.dumps({old.id: _legacy_record(old)}))
        s = sm.create_session(name="new", tmux_session="agents", tmux_window="w", command=[])
        sm.delete_session(s.id)
        assert [r["id"] for r in _lines(sm.archive_file)] == ["old-1", s.id]
        assert not sm.legacy_archive_file.exists()

    def test_unreadable_legacy_archive_is_set_aside(self, tmp_path, capsys):
        sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        sm.legacy_archive_file.write_text("{not json")
        assert sm.list_archived_sessions() == []
        assert not sm.legacy_archive_file.exists()
        assert (tmp_path / "archive.json.unreadable").read_text() == "{not json"
        assert "could not migrate" in capsys.readouterr().out
        assert sm.list_archived_sessions() == []  # and it is not retried

    def test_damaged_and_unfinished_lines_are_skipped_and_repaired(self, tmp_path):
        sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        a, b = _session("a", "a"), _session("b", "b")
        with open(sm.archive_file, "wb") as f:
            f.write(json.dumps(_legacy_record(a)).encode() + b"\n")
            f.write(b"this is not json\n")
            f.write(b'{"id": 5}\n')  # a record without a string id
            f.write(json.dumps(_legacy_record(b)).encode()[:-10])  # a crashed append
        settle(sm.archive_file)
        assert [s.id for s in sm.list_archived_sessions()] == ["a"]
        assert [s.id for s in sm.iter_archived_sessions()] == ["a"]

        c = _session("c", "c")
        sm._archive_session(_legacy_record(c))
        raw = sm.archive_file.read_bytes()
        assert raw.endswith(json.dumps(_legacy_record(c)).encode() + b"\n")
        assert raw.count(b"\n") == 5  # the unfinished line was terminated first
        settle(sm.archive_file)
        assert [s.id for s in sm.list_archived_sessions()] == ["a", "c"]

    def test_repeated_id_keeps_the_last_record_at_the_first_position(self, tmp_path):
        sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        first = _legacy_record(_session("x", "x-first"))
        other = _legacy_record(_session("y", "y"))
        second = _legacy_record(_session("x", "x-second"))
        sm._append_archive_records([first, other, second])
        settle(sm.archive_file)
        archived = sm.list_archived_sessions()
        assert [(s.id, s.name) for s in archived] == [("x", "x-second"), ("y", "y")]
        assert sm.get_archived_session("x").name == "x-second"
        assert [(s.id, s.name) for s in sm.iter_archived_sessions()] == [
            ("x", "x-first"),
            ("y", "y"),
            ("x", "x-second"),
        ]

    def test_parse_after_append_reads_only_the_appended_bytes(self, tmp_path):
        sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        sm._append_archive_records([_legacy_record(_session(f"s{i}", f"s{i}")) for i in range(3)])
        settle(sm.archive_file)
        first = sm.list_archived_sessions()
        assert sm._archive_tail[1] == sm.archive_file.stat().st_size

        reads = []
        original = sm._read_archive_bytes

        def counting():
            st, data, offset = original()
            reads.append((len(data), offset))
            return st, data, offset

        sm._read_archive_bytes = counting
        size_before = sm.archive_file.stat().st_size
        sm._append_archive_records([_legacy_record(_session("s3", "s3"))])
        settle(sm.archive_file)
        second = sm.list_archived_sessions()
        assert reads == [(sm.archive_file.stat().st_size - size_before, size_before)]
        assert [s.id for s in second] == ["s0", "s1", "s2", "s3"]
        assert second[0] is first[0] and second[2] is first[2]  # earlier records reused

        # A replaced file (new inode) is parsed from the start
        tmp = sm.archive_file.with_name("archive.rewrite")
        tmp.write_bytes(sm.archive_file.read_bytes())
        tmp.rename(sm.archive_file)
        settle(sm.archive_file)
        third = sm.list_archived_sessions()
        assert reads[-1] == (sm.archive_file.stat().st_size, 0)
        assert [s.id for s in third] == ["s0", "s1", "s2", "s3"]
        assert third[0] is not first[0]

        # A shrunk file too
        with open(sm.archive_file, "wb") as f:
            f.write(json.dumps(_legacy_record(_session("only", "only"))).encode() + b"\n")
        settle(sm.archive_file)
        assert [s.id for s in sm.list_archived_sessions()] == ["only"]
        assert reads[-1][1] == 0

    def test_iter_is_lazy_and_in_file_order(self, tmp_path):
        sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        assert list(sm.iter_archived_sessions()) == []  # no file yet
        sm._append_archive_records([_legacy_record(_session(f"s{i}", f"n{i}")) for i in range(5)])
        it = sm.iter_archived_sessions()
        assert next(it).id == "s0"
        assert next(s for s in it if s.name == "n3").id == "s3"
        assert sm.list_archived_sessions()[3] is not None  # the snapshot is separate

    def test_readers_across_managers_agree(self, tmp_path):
        a = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        b = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        s = a.create_session(name="x", tmux_session="agents", tmux_window="w", command=[])
        a.delete_session(s.id)
        settle(a.archive_file)
        assert _dicts(a.list_archived_sessions()) == _dicts(b.list_archived_sessions())
        assert [x.id for x in b.list_archived_sessions()] == [s.id]
        t = a.create_session(name="y", tmux_session="agents", tmux_window="w2", command=[])
        b.delete_session(t.id)  # appended by the other manager
        settle(a.archive_file)
        assert [x.id for x in a.list_archived_sessions()] == [s.id, t.id]


class TestCommitPendingArchive:
    def test_staged_ids_leave_the_live_file_and_join_the_archive(self, tmp_path):
        sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        state = {
            s.id: s.to_dict()
            for s in [
                _session("live", "live"),
                _session("t1", "t1", status="terminated"),
                _session("t2", "t2", tmux="other", status="terminated"),
            ]
        }
        sm._save_state(state)
        settle(sm.state_file)
        pending = PendingUpdates()
        pending.archive_session("t1")
        pending.archive_session("t2")
        pending.archive_session("t1")  # staged twice, archived once
        pending.archive_session("gone")  # not in the file: skipped
        pending.update_stats("live", current_task="still here")
        assert pending
        with count_sessions_io(sm.state_file) as io:
            assert sm.commit_pending(pending) is True
        assert (io.reads, io.writes) == (1, 1)
        raw = json.loads(sm.state_file.read_text())
        assert list(raw) == ["live"] and raw["live"]["stats"]["current_task"] == "still here"
        records = _lines(sm.archive_file)
        assert [r["id"] for r in records] == ["t1", "t2"]
        for record, sid in zip(records, ["t1", "t2"]):
            assert record == dict(state[sid], status="archived", end_time=record["end_time"])

    def test_only_archive_ids_still_writes_once(self, tmp_path):
        sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        sm._save_state({"t": _session("t", "t", status="terminated").to_dict()})
        pending = PendingUpdates()
        pending.archive_session("t")
        assert sm.commit_pending(pending) is True
        assert json.loads(sm.state_file.read_text()) == {}
        assert [r["id"] for r in _lines(sm.archive_file)] == ["t"]
        # Nothing to do the second time: no write, no archive append
        pending = PendingUpdates()
        pending.archive_session("t")
        size = sm.archive_file.stat().st_size
        assert sm.commit_pending(pending) is False
        assert sm.archive_file.stat().st_size == size


class TestSessionArchiveConfig:
    @pytest.fixture(autouse=True)
    def _fresh_config(self):
        config._clear_config_cache()
        yield
        config._clear_config_cache()

    def test_default_is_the_daemon_setting(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "nonexistent.yaml")
        assert config.get_session_archive_config() == {"terminated_grace_seconds": 3600.0}

    def test_override_and_disable(self, tmp_path, monkeypatch):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("session_archive:\n  terminated_grace_seconds: 600\n")
        monkeypatch.setattr(config, "CONFIG_PATH", cfg)
        assert config.get_session_archive_config()["terminated_grace_seconds"] == 600.0
        config._clear_config_cache()
        cfg.write_text("session_archive:\n  terminated_grace_seconds: -1\n")
        assert config.get_session_archive_config()["terminated_grace_seconds"] == -1.0

    def test_invalid_values_fall_back(self, tmp_path, monkeypatch):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("session_archive:\n  terminated_grace_seconds: soon\n")
        monkeypatch.setattr(config, "CONFIG_PATH", cfg)
        assert config.get_session_archive_config()["terminated_grace_seconds"] == 3600.0
        config._clear_config_cache()
        cfg.write_text("session_archive: 12\n")
        assert config.get_session_archive_config()["terminated_grace_seconds"] == 3600.0


class TestDaemonArchivesTerminatedSessions:
    @pytest.fixture
    def env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "state" / "sessions"))
        (tmp_path / "state" / "sessions").mkdir(parents=True)
        config._clear_config_cache()
        monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.yaml")
        yield tmp_path
        config._clear_config_cache()

    def _setup(self, env, grace=None):
        if grace is not None:
            (env / "config.yaml").write_text(
                f"session_archive:\n  terminated_grace_seconds: {grace}\n"
            )
        start = datetime(2026, 9, 23, 12, 0, 0)
        sm = SessionManager(state_dir=env / "state" / "sessions", skip_git_detection=True)
        sessions = seed_sessions(sm, 6, "agents", env / "work", start, other_tmux_sessions=2)
        sm.update_session_status(sessions[4].id, "terminated")
        sm.update_session_status(sessions[5].id, "terminated")
        settle(sm.state_file)

        def script(tick, s):
            if s.id in (sessions[4].id, sessions[5].id):
                return "terminated", "Window no longer exists", ""
            return "waiting_user", "Waiting for input", "pane"

        daemon = make_daemon(env / "state", "agents", ScriptedDetector(script), session_manager=sm)
        seed_steady_state(daemon, sessions, start)
        return daemon, sm, sessions, start

    def _tick(self, daemon, loop, now):
        """A tick at ``now``; a multiple-of-60 ``loop`` is a housekeeping tick.

        Housekeeping is wall-clock now (every HOUSEKEEPING_INTERVAL_SECONDS,
        so the unattended loop keeps its cadence); the loop number only
        chooses whether this tick's clock says the pass is due.
        """
        from overcode.monitor_daemon import HOUSEKEEPING_INTERVAL_SECONDS

        daemon.state.loop_count = loop
        if loop % 60 == 0:
            daemon._last_housekeeping = now - timedelta(seconds=HOUSEKEEPING_INTERVAL_SECONDS)
        else:
            daemon._last_housekeeping = now
        with FrozenClock(now).installed():
            daemon._tick(now)
        settle(daemon.session_manager.state_file)

    def test_terminated_sessions_leave_after_the_grace(self, env):
        daemon, sm, sessions, start = self._setup(env)
        t4, t5 = sessions[4].id, sessions[5].id
        # Loop 60: first sighting starts the clock; nothing moves
        self._tick(daemon, 60, start)
        assert set(daemon._terminated_since) == {t4, t5, "old-0000", "old-0001"}
        assert {s.id for s in sm.list_sessions()} >= {t4, t5, "old-0000", "old-0001"}
        # Loop 120, 59 minutes later: still within the grace
        self._tick(daemon, 120, start + timedelta(minutes=59))
        assert sm.get_session(t4) is not None and not sm.archive_file.exists()
        # A non-housekeeping loop past the grace does nothing
        self._tick(daemon, 121, start + timedelta(minutes=61))
        assert sm.get_session(t4) is not None
        # The next housekeeping loop past the grace archives all four, in one write
        with count_sessions_io(sm.state_file) as io:
            self._tick(daemon, 180, start + timedelta(minutes=62))
        assert io.writes == 1
        assert {s.id for s in sm.list_sessions()} == {s.id for s in sessions[:4]}
        archived = {r["id"]: r for r in _lines(sm.archive_file)}
        assert set(archived) == {t4, t5, "old-0000", "old-0001"}
        assert all(r["status"] == "archived" and r["end_time"] for r in archived.values())
        assert archived[t4]["name"] == "agent-04"
        assert daemon._terminated_since == {}
        assert sm.get_archived_session(t5).status == "archived"
        # That tick published before its housekeeping staged the move; the
        # next tick's snapshot, and so its published state, no longer has them
        assert {s.session_id for s in daemon.state.sessions} >= {t4, t5}
        self._tick(daemon, 181, start + timedelta(minutes=62, seconds=2))
        assert {s.session_id for s in daemon.state.sessions} == {s.id for s in sessions[:4]}

    def test_a_revived_session_drops_its_clock(self, env):
        daemon, sm, sessions, start = self._setup(env)
        t4 = sessions[4].id
        self._tick(daemon, 60, start)
        assert t4 in daemon._terminated_since
        sm.update_session_status(t4, "running")
        settle(sm.state_file)
        daemon.detector.script = lambda tick, s: ("waiting_user", "Waiting", "pane")
        self._tick(daemon, 120, start + timedelta(hours=2))
        assert t4 not in daemon._terminated_since
        assert sm.get_session(t4).status == "running"

    def test_negative_grace_disables_archiving(self, env):
        daemon, sm, sessions, start = self._setup(env, grace=-1)
        self._tick(daemon, 60, start)
        self._tick(daemon, 120, start + timedelta(days=2))
        assert not sm.archive_file.exists()
        assert sm.get_session(sessions[4].id).status == "terminated"

    def test_zero_grace_archives_on_first_sight(self, env):
        daemon, sm, sessions, start = self._setup(env, grace=0)
        self._tick(daemon, 60, start)
        assert sm.get_session(sessions[4].id) is None
        assert {r["id"] for r in _lines(sm.archive_file)} == {
            sessions[4].id,
            sessions[5].id,
            "old-0000",
            "old-0001",
        }
