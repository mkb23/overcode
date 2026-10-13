"""The append-only session archive and the daemon's terminated-session pass.

archive.jsonl replaces the whole-file-rewritten archive.json: a legacy file
is migrated once, every reader sees the same records, an archive is one
appended line, and a read after an append parses only that line. The
monitor daemon moves terminated entries out of sessions.json after a
configurable grace, through the same record ``overcode cleanup`` writes.
"""

import gzip
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from overcode import config
from overcode.session_archive import slim_record
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


def _archived_lines(sm):
    """Every archive record, every month, oldest first (raw dicts)."""
    return list(sm.iter_archived_records())


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
        expected = slim_record(dict(before, status="archived", end_time=record["end_time"]))
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
        (tmp_path / "archive.json.migrated").write_text("{}")  # an earlier copy
        # What the reader produces from the slim records
        expected = []
        for record in legacy.values():
            data = slim_record(record)
            end_time = data.pop("end_time")
            expected.append((Session.from_dict(data).to_dict(), end_time))

        archived = sm.list_archived_sessions()
        assert _dicts(archived) == expected
        assert not sm.legacy_archive_file.exists()
        assert not (tmp_path / "archive.json.migrated").exists()  # secrets: not kept
        # Filed under the month each ended, closed months gzipped
        assert [m for m, _ in sm.archive_months()] == ["2026-01"]
        gz = sm.archive_dir / "2026-01.jsonl.gz"
        assert gz.exists() and not (sm.archive_dir / "2026-01.jsonl").exists()
        assert [r["id"] for r in _archived_lines(sm)] == list(legacy)

        # Idempotent: a second access changes nothing
        sig = os.stat(gz)
        assert _dicts(sm.list_archived_sessions()) == expected
        assert os.stat(gz).st_mtime_ns == sig.st_mtime_ns

        # A fresh manager sees the same records, and an append goes after them
        fresh = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        assert _dicts(fresh.list_archived_sessions()) == expected
        s = fresh.create_session(name="new", tmux_session="agents", tmux_window="w", command=[])
        fresh.delete_session(s.id)
        assert [r["id"] for r in _archived_lines(sm)] == [*legacy, s.id]

    def test_the_single_jsonl_archive_is_split_slimmed_and_deleted(self, tmp_path):
        """archive.jsonl held every launch command and sisters' API keys."""
        sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        a = _session("a", "a", standing_instructions="keep going", source_api_key="sk-secret")
        b = _session("b", "b")
        with open(sm.old_archive_file, "w") as f:
            f.write(json.dumps(_legacy_record(a, end_time="2026-08-30T10:00:00")) + "\n")
            f.write(json.dumps(_legacy_record(b, end_time="2026-09-02T10:00:00")) + "\n")
            f.write('{"id": "torn", "na')  # a crashed append
        assert [s.id for s in sm.list_archived_sessions()] == ["a", "b"]
        assert not sm.old_archive_file.exists()
        assert [m for m, _ in sm.archive_months()] == ["2026-08", "2026-09"]
        raw = b"".join(gzip.open(p).read() for _, p in sm.archive_months())
        assert b"sk-secret" not in raw and b"keep going" not in raw and b'"claude"' not in raw
        (rec_a,) = [r for r in _archived_lines(sm) if r["id"] == "a"]
        assert rec_a["has_standing_instructions"] is True and rec_a["command"] == []

    def test_migration_also_runs_before_the_first_append(self, tmp_path):
        sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        old = _session("old-1", "old-1")
        sm.legacy_archive_file.write_text(json.dumps({old.id: _legacy_record(old)}))
        s = sm.create_session(name="new", tmux_session="agents", tmux_window="w", command=[])
        sm.delete_session(s.id)
        assert [r["id"] for r in _archived_lines(sm)] == ["old-1", s.id]
        assert not sm.legacy_archive_file.exists()

    def test_unreadable_legacy_archive_is_set_aside(self, tmp_path, caplog):
        sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        sm.legacy_archive_file.write_text("{not json")
        with caplog.at_level("WARNING", logger="overcode.session_archive"):
            assert sm.list_archived_sessions() == []
        assert not sm.legacy_archive_file.exists()
        assert (tmp_path / "archive.json.unreadable").read_text() == "{not json"
        assert "could not migrate" in caplog.text
        assert sm.list_archived_sessions() == []  # and it is not retried

    def test_damaged_and_unfinished_lines_are_skipped_and_repaired(self, tmp_path):
        sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        a, b = _session("a", "a"), _session("b", "b")
        sm.archive_dir.mkdir(parents=True)
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
        assert raw.endswith(json.dumps(slim_record(_legacy_record(c))).encode() + b"\n")
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
        assert [(s.id, s.name) for s in sm.iter_archived_sessions()] == [
            ("x", "x-first"),
            ("y", "y"),
            ("x", "x-second"),
        ]

    def _months(self, sm, months):
        """Archive records filed under given months: {month: [records]}."""
        with sm._archive_lock():
            for month, records in months.items():
                sm._append_month_records(month, [slim_record(r) for r in records])

    def test_lookup_is_newest_first_by_name_former_name_or_id(self, tmp_path):
        sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        self._months(sm, {
            "2026-08": [_legacy_record(_session("x1", "shirka")),
                        _legacy_record(_session("y", "other", previous_names=["old-name"]))],
            "2026-09": [_legacy_record(_session("x2", "shirka"))],
        })
        sm.gzip_closed_months(current="2026-09")
        assert sm.find_archived("shirka").id == "x2"  # the newest run of the name
        assert sm.find_archived("x1").id == "x1"
        assert sm.find_archived("old-name").id == "y"
        assert sm.find_archived("nobody") is None
        assert sm.is_archived("x1") and not sm.is_archived("nobody")

    def test_a_repeated_id_reads_as_its_newest_record(self, tmp_path):
        """A crash between the archive and the live file archives twice."""
        sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        self._months(sm, {
            "2026-08": [_legacy_record(_session("x", "first"))],
            "2026-09": [_legacy_record(_session("x", "second"))],
        })
        assert [s.name for s in sm.iter_archived_sessions(newest_first=True)] == ["second"]
        assert [s.name for s in sm.list_archived_sessions()] == ["second"]
        assert [s.name for s in sm.iter_archived_sessions()] == ["first", "second"]

    def test_since_skips_older_months(self, tmp_path):
        sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        self._months(sm, {"2026-07": [_legacy_record(_session("a", "a"))],
                          "2026-09": [_legacy_record(_session("b", "b"))]})
        assert [s.id for s in sm.list_archived_sessions(since=datetime(2026, 9, 1))] == ["b"]

    def test_rollover_gzips_closed_months_once(self, tmp_path):
        sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        sm.rollover_in_background = False
        self._months(sm, {"2026-07": [_legacy_record(_session("a", "a"))]})
        assert (sm.archive_dir / "2026-07.jsonl").exists()
        s = sm.create_session(name="now", tmux_session="agents", tmux_window="w", command=[])
        sm.delete_session(s.id)  # the first archive of a later month rolls over
        assert (sm.archive_dir / "2026-07.jsonl.gz").exists()
        assert not (sm.archive_dir / "2026-07.jsonl").exists()
        assert [x.id for x in sm.list_archived_sessions()] == ["a", s.id]
        assert sm.gzip_closed_months() == []  # nothing left to do

    def test_retention_is_off_by_default_and_drops_old_months(self, tmp_path):
        sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        self._months(sm, {"2025-01": [_legacy_record(_session("a", "a"))],
                          "2026-09": [_legacy_record(_session("b", "b"))]})
        assert sm.prune_archive(0) == []
        assert sm.prune_archive(6, now=datetime(2026, 10, 12)) == ["2025-01"]
        assert [s.id for s in sm.list_archived_sessions()] == ["b"]
        assert config.get_archive_config()["retention_months"] == 0

    def test_slim_record_keeps_what_readers_use(self):
        full = _legacy_record(_session("a", "a", standing_instructions="be brief",
                                       source_api_key="sk-x", tags=["t"]))
        full["bundle"] = {"day": "2026-10-12", "off": 0, "len": 10}
        slim = slim_record(full)
        assert slim["stats"] == full["stats"] and slim["tags"] == ["t"]
        assert slim["bundle"] == full["bundle"] and slim["has_standing_instructions"] is True
        assert "source_api_key" not in slim and "standing_instructions" not in slim
        assert slim["command"] == [] and "agent_session_ids" not in slim
        assert len(json.dumps(slim)) < len(json.dumps(full))
        assert slim_record(slim) == slim  # idempotent (migration of a slim archive)

    def test_journey_counts_and_history_read_the_same_from_slim(self, tmp_path):
        """The journey's counters and the history listing see no difference."""
        from overcode.journey import _SESSION_COUNTERS, _count_sessions
        from overcode.session_archive import archived_session

        full = _legacy_record(_session(
            "a", "a", standing_instructions="be brief", tags=["t"], parent_session_id="p",
            cost_budget_usd=5.0, wrapper="w.sh", heartbeat_enabled=True,
            human_annotation="note", agent_value=2000, skill_profile="prof", backend="codex"))

        def counted(record):
            counts = dict.fromkeys(_SESSION_COUNTERS, 0)
            tags, backends, backend_agents, profiles = set(), set(), {}, set()
            _count_sessions([archived_session(record)], counts, tags, backends,
                            backend_agents, profiles)
            return counts, tags, backends, backend_agents, profiles

        assert counted(full) == counted(slim_record(full))
        one, two = archived_session(full), archived_session(slim_record(full))
        for attr in ("name", "start_time", "_end_time", "start_directory", "repo_name", "branch"):
            assert getattr(one, attr) == getattr(two, attr)
        assert one.stats == two.stats

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
        # One read more than a plain commit: the agents' directories are
        # packed first, outside the lock, from the (stat-gated) snapshot
        assert (io.reads, io.writes) == (2, 1)
        raw = json.loads(sm.state_file.read_text())
        assert list(raw) == ["live"] and raw["live"]["stats"]["current_task"] == "still here"
        records = _archived_lines(sm)
        assert [r["id"] for r in records] == ["t1", "t2"]
        for record, sid in zip(records, ["t1", "t2"]):
            assert record == slim_record(
                dict(state[sid], status="archived", end_time=record["end_time"]))

    def test_only_archive_ids_still_writes_once(self, tmp_path):
        sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        sm._save_state({"t": _session("t", "t", status="terminated").to_dict()})
        pending = PendingUpdates()
        pending.archive_session("t")
        assert sm.commit_pending(pending) is True
        assert json.loads(sm.state_file.read_text()) == {}
        assert [r["id"] for r in _archived_lines(sm)] == ["t"]
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
            daemon.cadence.housekeeping.last = now - timedelta(seconds=HOUSEKEEPING_INTERVAL_SECONDS)
        else:
            daemon.cadence.housekeeping.last = now
        with FrozenClock(now).installed():
            daemon._tick(now)
        settle(daemon.session_manager.state_file)

    def test_terminated_sessions_leave_after_the_grace(self, env):
        daemon, sm, sessions, start = self._setup(env)
        t4, t5 = sessions[4].id, sessions[5].id
        # Loop 60: first sighting starts the clock; nothing moves
        self._tick(daemon, 60, start)
        assert set(daemon.housekeeping.terminated_since) == {t4, t5, "old-0000", "old-0001"}
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
        archived = {r["id"]: r for r in _archived_lines(sm)}
        assert set(archived) == {t4, t5, "old-0000", "old-0001"}
        assert all(r["status"] == "archived" and r["end_time"] for r in archived.values())
        assert archived[t4]["name"] == "agent-04"
        assert daemon.housekeeping.terminated_since == {}
        # That tick published before its housekeeping staged the move; the
        # next tick's snapshot, and so its published state, no longer has them
        assert {s.session_id for s in daemon.state.sessions} >= {t4, t5}
        self._tick(daemon, 181, start + timedelta(minutes=62, seconds=2))
        assert {s.session_id for s in daemon.state.sessions} == {s.id for s in sessions[:4]}

    def test_a_child_that_never_reported_leaves_once_its_window_is_gone(self, env):
        """It stays waiting_oversight (the timeout still applies) but can never
        report: it used to stay in sessions.json, and its follower waiting, for ever."""
        daemon, sm, sessions, start = self._setup(env)
        parent, gone, alive = sessions[0], sessions[4], sessions[1]
        for child in (gone, alive):
            sm.update_session(child.id, parent_session_id=parent.id)
            sm.update_session_status(child.id, "waiting_oversight")
        settle(sm.state_file)
        stopped = {gone.name, alive.name}
        daemon.detector.hooks._read_hook_state = (
            lambda name: {"event": "Stop"} if name in stopped else None)
        self._tick(daemon, 60, start)
        assert sm.get_session(gone.id).status == "waiting_oversight"
        assert gone.id in daemon.housekeeping.terminated_since
        assert alive.id not in daemon.housekeeping.terminated_since
        self._tick(daemon, 180, start + timedelta(minutes=62))
        assert sm.get_session(gone.id) is None
        assert sm.get_session(alive.id).status == "waiting_oversight"
        assert gone.id in {r["id"] for r in _archived_lines(sm)}

    def test_a_revived_session_drops_its_clock(self, env):
        daemon, sm, sessions, start = self._setup(env)
        t4 = sessions[4].id
        self._tick(daemon, 60, start)
        assert t4 in daemon.housekeeping.terminated_since
        sm.update_session_status(t4, "running")
        settle(sm.state_file)
        daemon.detector.script = lambda tick, s: ("waiting_user", "Waiting", "pane")
        self._tick(daemon, 120, start + timedelta(hours=2))
        assert t4 not in daemon.housekeeping.terminated_since
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
        assert {r["id"] for r in _archived_lines(sm)} == {
            sessions[4].id,
            sessions[5].id,
            "old-0000",
            "old-0001",
        }
