"""Month files of the ledger and the archive: never a closed month, never a lost row.

A write meant for a month that is already gzipped (a tick's ``now`` from
before midnight, a lock held across it) goes to the newest open month; a
plain file beside a gzip is folded in, atomically and durably, not
deleted; a torn gzip member (an older in-place append) hides nothing
before it. Synthetic rows only.
"""

import gzip
import json
import os
from datetime import datetime

from overcode import month_files
from overcode.ledger import Ledger
from overcode.session_archive import slim_record
from overcode.session_manager import SessionManager


def _row(sid, cost, seq, kind="checkpoint", t="2026-09-30T23:59:58"):
    return {"sid": sid, "billing_code": "x", "cost": cost, "tokens": 0, "tok_in": 0,
            "tok_out": 0, "tok_cache": 0, "seg": 0, "base_cost": 0.0, "base_tokens": 0,
            "kind": kind, "seq": seq, "t": t}


def _names(directory):
    return sorted(n for n in os.listdir(directory) if not n.startswith("."))


class TestHelpers:
    def test_next_month(self):
        assert month_files.next_month("2026-09") == "2026-10"
        assert month_files.next_month("2026-12") == "2027-01"

    def test_writable_month_skips_closed_and_older_months(self, tmp_path):
        assert month_files.writable_month(tmp_path, "2026-09") == "2026-09"
        (tmp_path / "2026-09.jsonl.gz").write_bytes(gzip.compress(b""))
        assert month_files.writable_month(tmp_path, "2026-09") == "2026-10"
        (tmp_path / "2026-11.jsonl").write_bytes(b"")
        assert month_files.writable_month(tmp_path, "2026-09") == "2026-11"

    def test_a_torn_member_hides_nothing_before_it(self, tmp_path):
        path = tmp_path / "2026-09.jsonl.gz"
        whole = gzip.compress(b"a\nb\n")
        torn = gzip.compress(b"c\n" * 1000)[:20]
        path.write_bytes(whole + torn + gzip.compress(b"d\n"))
        content, complete = month_files.decompress(path.read_bytes())
        assert content.startswith(b"a\nb\n") and not complete
        assert month_files.read_gzip(path).startswith(b"a\nb\n")

    def test_fold_is_fsynced_before_the_rename_and_the_unlink(self, tmp_path, monkeypatch):
        plain, target = tmp_path / "2026-09.jsonl", tmp_path / "2026-09.jsonl.gz"
        plain.write_bytes(b"x\n")
        calls = []
        real_fsync, real_replace, real_dir = os.fsync, os.replace, month_files.fsync_dir

        def fsync(fd):
            calls.append("fsync")
            return real_fsync(fd)

        def replace(a, b):
            calls.append("replace")
            return real_replace(a, b)

        def fsync_dir(d):
            calls.append("dir")
            return real_dir(d)

        monkeypatch.setattr(month_files.os, "fsync", fsync)
        monkeypatch.setattr(month_files.os, "replace", replace)
        monkeypatch.setattr(month_files, "fsync_dir", fsync_dir)
        month_files.fold_into_gzip(plain, target)
        # The data, the rename, the rename made durable, and only then the unlink
        assert calls[:3] == ["fsync", "replace", "dir"]
        assert not plain.exists() and gzip.decompress(target.read_bytes()) == b"x\n"

    def test_a_damaged_gzip_is_kept_beside_the_rewritten_one(self, tmp_path):
        plain, target = tmp_path / "2026-09.jsonl", tmp_path / "2026-09.jsonl.gz"
        target.write_bytes(gzip.compress(b"a\n") + gzip.compress(b"b\n" * 1000)[:20])
        plain.write_bytes(b"c\n")
        month_files.fold_into_gzip(plain, target)
        content, complete = month_files.decompress(target.read_bytes())
        assert complete and content.startswith(b"a\n") and content.endswith(b"c\n")
        assert any(".damaged-" in n for n in os.listdir(tmp_path))


class TestLedgerMonths:
    def test_a_late_row_for_a_gzipped_month_is_kept(self, tmp_path):
        ledger = Ledger(tmp_path)
        ledger.append([_row("a", 1.0, 1)], now=datetime(2026, 9, 30, 23))
        ledger.append([_row("b", 2.0, 2, t="2026-10-01T00:00:01")],
                      now=datetime(2026, 10, 1, 0, 0, 1))  # gzips September
        # A daemon tick that began before midnight writes after it
        ledger.append([_row("a", 9.0, 3)], now=datetime(2026, 9, 30, 23, 59, 58))
        assert [r["cost"] for r in ledger.iter_rows() if r["sid"] == "a"] == [1.0, 9.0]
        ledger.append([_row("c", 1.0, 4, t="2026-10-01T01:00:00")], now=datetime(2026, 10, 1, 1))
        ledger.append([_row("d", 1.0, 5, t="2026-11-01T01:00:00")],
                      now=datetime(2026, 11, 1, 1))  # gzips October
        assert [r["cost"] for r in ledger.iter_rows() if r["sid"] == "a"] == [1.0, 9.0]
        assert ledger.totals()["x"]["cost"] == 9.0 + 2.0 + 1.0 + 1.0
        # A row keeps its own time: the period report files it under September
        sept = ledger.report(since=datetime(2026, 9, 1), until=datetime(2026, 10, 1))
        assert sept["x"]["cost"] == 9.0

    def test_a_plain_month_beside_its_gzip_is_folded_in_not_deleted(self, tmp_path):
        ledger = Ledger(tmp_path)
        ledger.append([_row("a", 1.0, 1)], now=datetime(2026, 9, 2))
        ledger.append([_row("b", 2.0, 2)], now=datetime(2026, 10, 2))
        # What an older overcode left: rows in a closed month's plain file
        (ledger.dir / "2026-09.jsonl").write_text(json.dumps(_row("c", 3.0, 3)) + "\n")
        ledger.append([_row("d", 4.0, 4)], now=datetime(2026, 10, 3))
        assert _names(ledger.dir) == ["2026-09.jsonl.gz", "2026-10.jsonl"]
        assert sorted(r["sid"] for r in ledger.iter_rows()) == ["a", "b", "c", "d"]


class TestArchiveMonths:
    def _sm(self, tmp_path):
        sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        sm.rollover_in_background = False
        return sm

    def _record(self, sid):
        return {"id": sid, "name": sid, "tmux_session": "agents", "tmux_window": sid,
                "command": [], "start_directory": None, "start_time": "2026-09-01T00:00:00",
                "end_time": "2026-09-30T23:59:59", "status": "archived"}

    def test_a_late_record_for_a_gzipped_month_is_visible_at_once(self, tmp_path, monkeypatch):
        sm = self._sm(tmp_path)
        with sm._archive_lock():
            sm._append_month_records("2026-09", [slim_record(self._record("old"))])
        sm.gzip_closed_months(current="2026-10")

        class StaleClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 9, 30, 23, 59, 59)

        monkeypatch.setattr("overcode.session_archive.datetime", StaleClock)
        sm._append_archive_records([self._record("late")])
        assert sm.find_archived("late") is not None
        assert sm.is_archived("late") and sm.is_archived("old")
        assert "2026-09.jsonl" not in _names(sm.archive_dir)

    def test_a_plain_month_beside_its_gzip_rolls_over(self, tmp_path):
        sm = self._sm(tmp_path)
        with sm._archive_lock():
            sm._append_month_records("2026-07", [slim_record(self._record("a"))])
        sm.gzip_closed_months(current="2026-08")
        with sm._archive_lock():
            sm._append_month_records("2026-07", [slim_record(self._record("b"))])
        s = sm.create_session(name="now", tmux_session="agents", tmux_window="w", command=[])
        sm.delete_session(s.id)
        assert "2026-07.jsonl" not in _names(sm.archive_dir)
        assert sm.is_archived("b") and sm.is_archived("a")

    def test_a_torn_member_neither_hides_the_month_nor_survives_the_retry(self, tmp_path):
        sm = self._sm(tmp_path)
        with sm._archive_lock():
            sm._append_month_records("2026-07", [slim_record(self._record(f"s{i}"))
                                                 for i in range(50)])
        sm.gzip_closed_months(current="2026-08")
        gz = sm.archive_dir / "2026-07.jsonl.gz"
        torn = gzip.compress(json.dumps(slim_record(self._record("t"))).encode() * 50)[:30]
        with open(gz, "ab") as f:  # what an interrupted in-place append left
            f.write(torn)
        assert sm.is_archived("s49") and len(sm.list_archived_sessions()) == 50
        with sm._archive_lock():
            sm._append_month_records("2026-07", [slim_record(self._record("t"))])
        assert sm.gzip_closed_months(current="2026-08") == ["2026-07"]
        assert len(sm.list_archived_sessions()) == 51
        assert month_files.decompress(gz.read_bytes())[1]  # whole again
