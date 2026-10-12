"""JsonStore: sessions.json / jobs.json can't be lost or rolled back by a crash.

Quality review 2026-10, item 1. The old in-place truncate+dump left an empty
file after a crash, which parsed as ``{}`` and was written back as an empty
fleet; the ``.bak`` was months stale and was copied over the live file
without a lock.
"""

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent.parent / "src"
sys.path.insert(0, str(SRC))

from overcode import json_store  # noqa: E402
from overcode.exceptions import (  # noqa: E402
    StateCorruptError,
    StateLockTimeout,
    StateWriteError,
)
from overcode.job_manager import JobManager  # noqa: E402
from overcode.json_store import JsonStore  # noqa: E402
from overcode.session_manager import SessionManager  # noqa: E402


def _store(tmp_path, **kw) -> JsonStore:
    return JsonStore(tmp_path / "state.json", **kw)


def _json(path: Path):
    return json.loads(path.read_text())


def _leftovers(tmp_path):
    return sorted(p.name for p in tmp_path.iterdir() if ".tmp." in p.name)


class TestReadWrite:
    def test_missing_file_is_empty_and_uncached(self, tmp_path):
        assert _store(tmp_path).read() == (None, {})

    def test_round_trip(self, tmp_path):
        store = _store(tmp_path)
        store.save({"a": {"n": 1}})
        sig, state = store.read()
        assert state == {"a": {"n": 1}}
        assert sig is not None and sig.ino == os.stat(store.path).st_ino

    def test_transaction_writes_only_when_dirty(self, tmp_path):
        store = _store(tmp_path)
        store.save({"a": 1})
        before = os.stat(store.path).st_ino
        with store.transaction() as txn:
            txn.state["a"] = 2
        assert _json(store.path) == {"a": 1}
        assert os.stat(store.path).st_ino == before
        with store.transaction() as txn:
            txn.state["a"] = 2
            txn.dirty = True
        assert _json(store.path) == {"a": 2}

    def test_exception_in_the_body_writes_nothing(self, tmp_path):
        store = _store(tmp_path)
        store.save({"a": 1})
        with pytest.raises(RuntimeError):
            with store.transaction() as txn:
                txn.state["a"] = 2
                txn.dirty = True
                raise RuntimeError("boom")
        assert _json(store.path) == {"a": 1}

    def test_each_write_is_a_new_file_never_an_in_place_rewrite(self, tmp_path):
        store = _store(tmp_path)
        store.save({"a": 1})
        with open(store.path) as reader:  # a reader that opened version 1
            store.save({"a": 2})
            assert json.load(reader) == {"a": 1}
        assert _json(store.path) == {"a": 2}


class TestAtomicity:
    def test_crash_before_replace_leaves_the_old_file(self, tmp_path, monkeypatch):
        store = _store(tmp_path)
        store.save({"fleet": ["a", "b"]})

        def crash(src, dst):
            raise OSError("killed between write and rename")

        monkeypatch.setattr(json_store.os, "replace", crash)
        with pytest.raises(StateWriteError):
            with store.transaction() as txn:
                txn.state["fleet"].append("c")
                txn.dirty = True
        monkeypatch.undo()
        assert _json(store.path) == {"fleet": ["a", "b"]}
        assert _leftovers(tmp_path) == []

    def test_crash_mid_dump_leaves_the_old_file(self, tmp_path, monkeypatch):
        store = _store(tmp_path)
        store.save({"fleet": ["a", "b"]})
        real_dump = json.dump

        def half_dump(obj, f, **kw):
            f.write('{"fleet": [')
            raise OSError("disk full")

        monkeypatch.setattr(json_store.json, "dump", half_dump)
        with pytest.raises(StateWriteError):
            store.save({"fleet": []})
        monkeypatch.setattr(json_store.json, "dump", real_dump)
        assert _json(store.path) == {"fleet": ["a", "b"]}
        assert store.read()[1] == {"fleet": ["a", "b"]}
        assert _leftovers(tmp_path) == []

    def test_write_fsyncs_file_and_directory(self, tmp_path, monkeypatch):
        store = _store(tmp_path)
        synced = []
        real_fsync = os.fsync

        def recording_fsync(fd):
            synced.append(os.fstat(fd).st_mode)
            return real_fsync(fd)

        monkeypatch.setattr(json_store.os, "fsync", recording_fsync)
        store.save({"a": 1})
        import stat

        assert any(stat.S_ISREG(m) for m in synced)
        if json_store.HAS_FCNTL:
            assert any(stat.S_ISDIR(m) for m in synced)


class TestBackup:
    def test_first_write_has_no_backup(self, tmp_path):
        store = _store(tmp_path)
        store.save({"v": 1})
        assert not store.backup_path.exists()

    def test_backup_is_always_the_previous_version(self, tmp_path):
        store = _store(tmp_path)
        for v in range(1, 6):
            with store.transaction() as txn:
                txn.state["v"] = v
                txn.dirty = True
            if v > 1:
                assert _json(store.backup_path) == {"v": v - 1}
        assert _json(store.path) == {"v": 5}

    def test_a_clean_transaction_leaves_the_backup_alone(self, tmp_path):
        store = _store(tmp_path)
        store.save({"v": 1})
        store.save({"v": 2})
        with store.transaction():
            pass
        assert _json(store.backup_path) == {"v": 1}

    def test_backup_falls_back_to_a_copy_without_hard_links(self, tmp_path, monkeypatch):
        store = _store(tmp_path)
        store.save({"v": 1})

        def no_links(src, dst):
            raise OSError("links not supported")

        monkeypatch.setattr(json_store.os, "link", no_links)
        store.save({"v": 2})
        assert _json(store.backup_path) == {"v": 1}


class TestLock:
    def test_lock_file_is_separate_and_survives_writes(self, tmp_path):
        store = _store(tmp_path)
        store.save({"a": 1})
        assert store.lock_path.name == "state.json.lock"
        if json_store.HAS_FCNTL:
            assert store.lock_path.exists()

    @pytest.mark.skipif(not json_store.HAS_FCNTL, reason="no flock")
    def test_held_lock_times_out_with_a_clear_error(self, tmp_path):
        holder = _store(tmp_path)
        waiter = _store(tmp_path, lock_timeout=0.2)
        held, release = threading.Event(), threading.Event()

        def hold():
            with holder.lock():
                held.set()
                release.wait(5)

        t = threading.Thread(target=hold)
        t.start()
        try:
            assert held.wait(5)
            start = time.monotonic()
            with pytest.raises(StateLockTimeout) as exc:
                with waiter.transaction():
                    pass
            elapsed = time.monotonic() - start
            assert 0.15 <= elapsed < 2
            assert "state.json.lock" in str(exc.value)
            assert isinstance(exc.value, StateWriteError)
        finally:
            release.set()
            t.join()
        with waiter.transaction():  # free again
            pass

    @pytest.mark.skipif(not json_store.HAS_FCNTL, reason="no flock")
    def test_lock_held_by_another_process_times_out(self, tmp_path):
        store = _store(tmp_path, lock_timeout=0.2)
        store.save({})
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import fcntl, os, sys, time\n"
             "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT)\n"
             "fcntl.flock(fd, fcntl.LOCK_EX)\n"
             "print('held', flush=True)\n"
             "time.sleep(30)\n",
             str(store.lock_path)],
            stdout=subprocess.PIPE, text=True,
        )
        try:
            assert holder.stdout.readline().strip() == "held"
            with pytest.raises(StateLockTimeout):
                store.save({"a": 1})
            assert _json(store.path) == {}
        finally:
            holder.kill()
            holder.wait()
        store.save({"a": 1})
        assert _json(store.path) == {"a": 1}

    def test_threads_do_not_lose_updates(self, tmp_path):
        store = _store(tmp_path)
        store.save({"n": 0})

        def bump():
            s = _store(tmp_path)  # own descriptor, like another process
            for _ in range(25):
                with s.transaction() as txn:
                    txn.state["n"] += 1
                    txn.dirty = True

        threads = [threading.Thread(target=bump) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert _json(store.path) == {"n": 100}

    @pytest.mark.skipif(not json_store.HAS_FCNTL, reason="no flock")
    def test_processes_do_not_lose_updates(self, tmp_path):
        path = tmp_path / "state.json"
        JsonStore(path).save({"n": 0})
        script = (
            "import sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "from overcode.json_store import JsonStore\n"
            "s = JsonStore(sys.argv[2])\n"
            "for _ in range(40):\n"
            "    with s.transaction() as txn:\n"
            "        txn.state['n'] += 1\n"
            "        txn.dirty = True\n"
        )
        procs = [
            subprocess.Popen([sys.executable, "-c", script, str(SRC), str(path)])
            for _ in range(4)
        ]
        assert [p.wait(60) for p in procs] == [0, 0, 0, 0]
        assert _json(path) == {"n": 160}

    def test_readers_never_see_a_partial_file(self, tmp_path):
        store = _store(tmp_path)
        big = {f"s{i}": {"name": f"agent-{i}", "pad": "x" * 200} for i in range(300)}
        store.save(big)
        stop = threading.Event()
        bad = []

        def write():
            w = _store(tmp_path)
            i = 0
            while not stop.is_set():
                i += 1
                w.save({**big, "i": i})

        t = threading.Thread(target=write)
        t.start()
        try:
            reader = _store(tmp_path)
            deadline = time.monotonic() + 0.5
            while time.monotonic() < deadline:
                sig, state = reader.read()
                if sig is None or len(state) < 300:
                    bad.append(len(state))
        finally:
            stop.set()
            t.join()
        assert bad == []


class TestCorruption:
    def _with_backup(self, tmp_path):
        store = _store(tmp_path)
        store.save({"a": 1})
        store.save({"a": 1, "b": 2})  # .bak = {"a": 1}
        return store

    def test_corrupt_file_reads_the_backup_read_only(self, tmp_path, caplog):
        store = self._with_backup(tmp_path)
        store.path.write_text('{"a": 1, "b": 2')  # truncated mid-write
        before = store.path.read_bytes()
        with caplog.at_level(logging.WARNING, logger="overcode.json_store"):
            sig, state = store.read()
            assert store.read() == (None, {"a": 1})
        assert (sig, state) == (None, {"a": 1})
        # Read-only: neither file was touched, nothing copied over the live one.
        assert store.path.read_bytes() == before
        assert _json(store.backup_path) == {"a": 1}
        assert not list(tmp_path.glob("state.json.corrupt-*"))
        warnings = [r for r in caplog.records if "corrupt" in r.getMessage()]
        assert len(warnings) == 1  # once per corrupt version, not per read

    def test_empty_file_with_a_backup_is_corrupt(self, tmp_path):
        store = self._with_backup(tmp_path)
        store.path.write_text("")  # crash between truncate and dump (pre-0.6 writer)
        assert store.read() == (None, {"a": 1})

    def test_empty_file_without_a_backup_is_a_first_run(self, tmp_path, caplog):
        store = _store(tmp_path)
        store.path.write_text("")  # what a pre-0.6 first run left
        with caplog.at_level(logging.WARNING, logger="overcode.json_store"):
            sig, state = store.read()
        assert state == {} and sig is not None
        assert caplog.records == []
        with store.transaction() as txn:
            txn.state["a"] = 1
            txn.dirty = True
        assert _json(store.path) == {"a": 1}

    def test_empty_file_with_an_empty_backup_is_empty(self, tmp_path):
        store = _store(tmp_path)
        store.save({})
        store.save({})
        store.path.write_text("")
        assert store.read()[1] == {}

    def test_non_object_json_is_corrupt(self, tmp_path):
        store = self._with_backup(tmp_path)
        store.path.write_text("[]")
        assert store.read() == (None, {"a": 1})

    def test_transaction_continues_from_backup_and_keeps_the_corrupt_file(self, tmp_path):
        store = self._with_backup(tmp_path)
        store.path.write_text("garbage")
        with store.transaction() as txn:
            assert txn.state == {"a": 1}
            txn.state["c"] = 3
            txn.dirty = True
        assert _json(store.path) == {"a": 1, "c": 3}
        aside = list(tmp_path.glob("state.json.corrupt-*"))
        assert len(aside) == 1 and aside[0].read_text() == "garbage"
        # The corrupt file never becomes the backup.
        assert _json(store.backup_path) == {"a": 1}

    def test_clean_transaction_on_a_corrupt_file_changes_nothing(self, tmp_path):
        store = self._with_backup(tmp_path)
        store.path.write_text("garbage")
        with store.transaction() as txn:
            assert txn.state == {"a": 1}
        assert store.path.read_text() == "garbage"
        assert not list(tmp_path.glob("state.json.corrupt-*"))

    def test_corrupt_without_backup_refuses_to_write(self, tmp_path):
        store = _store(tmp_path)
        store.path.write_text("garbage")
        assert store.read() == (None, {})
        with pytest.raises(StateCorruptError):
            with store.transaction() as txn:
                txn.dirty = True
        assert store.path.read_text() == "garbage"

    def test_save_over_a_corrupt_file_keeps_it_aside(self, tmp_path):
        store = self._with_backup(tmp_path)
        store.path.write_text("garbage")
        store.save({"z": 1})
        assert _json(store.path) == {"z": 1}
        assert [p.read_text() for p in tmp_path.glob("state.json.corrupt-*")] == ["garbage"]
        assert _json(store.backup_path) == {"a": 1}

    def test_restore_from_backup_is_locked_and_keeps_the_current_file(self, tmp_path):
        store = self._with_backup(tmp_path)
        assert store.restore_from_backup() is True
        assert _json(store.path) == {"a": 1}
        aside = list(tmp_path.glob("state.json.pre-restore-*"))
        assert [_json(p) for p in aside] == [{"a": 1, "b": 2}]

    def test_restore_without_backup_changes_nothing(self, tmp_path):
        store = _store(tmp_path)
        store.save({"a": 1})
        assert store.restore_from_backup() is False
        assert _json(store.path) == {"a": 1}


class TestWithoutFcntl:
    """Windows: no lock, but the replace is still atomic and backed up."""

    def test_transactions_and_backup_work(self, tmp_path, monkeypatch):
        monkeypatch.setattr(json_store, "HAS_FCNTL", False)
        store = _store(tmp_path)
        for v in (1, 2):
            with store.transaction() as txn:
                txn.state["v"] = v
                txn.dirty = True
        assert _json(store.path) == {"v": 2}
        assert _json(store.backup_path) == {"v": 1}
        assert not store.lock_path.exists()


class TestManagers:
    """The bug as users hit it: a truncated sessions.json must not empty the fleet."""

    def _fleet(self, tmp_path):
        sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        a = sm.create_session(name="a", tmux_session="agents", tmux_window="a", command=["claude"])
        b = sm.create_session(name="b", tmux_session="agents", tmux_window="b", command=["claude"])
        return sm, a, b

    def test_truncated_sessions_json_is_not_written_back_empty(self, tmp_path):
        sm, a, b = self._fleet(tmp_path)
        # .bak holds the version before `b` was created; corrupt the live file.
        sm.state_file.write_text("")
        names = {s.name for s in SessionManager(state_dir=tmp_path, skip_git_detection=True).list_sessions()}
        assert names == {"a"}
        sm2 = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        sm2.create_session(name="c", tmux_session="agents", tmux_window="c", command=["claude"])
        on_disk = {e["name"] for e in _json(sm.state_file).values()}
        assert on_disk == {"a", "c"}
        assert len(list(tmp_path.glob("sessions.json.corrupt-*"))) == 1

    def test_session_manager_files_are_beside_sessions_json(self, tmp_path):
        sm, a, b = self._fleet(tmp_path)
        assert {e["name"] for e in _json(tmp_path / "sessions.json.bak").values()} == {"a"}
        assert sm.restore_from_backup() is True
        assert [s.name for s in sm.list_sessions()] == ["a"]

    def test_job_manager_uses_the_store(self, tmp_path):
        jm = JobManager(state_dir=tmp_path)
        j1 = jm.create_job("sleep 1", tmux_window="w1")
        jm.create_job("sleep 2", tmux_window="w2")
        assert {e["id"] for e in _json(tmp_path / "jobs.json.bak").values()} == {j1.id}
        (tmp_path / "jobs.json").write_text('{"trunc')
        assert [j.id for j in JobManager(state_dir=tmp_path).list_jobs(include_completed=True)] == [j1.id]
