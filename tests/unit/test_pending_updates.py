"""PendingUpdates and SessionManager.commit_pending (audit R5).

The daemon stages a tick's mutations and commits them in one
read-modify-write; these tests pin the staged view, the compare-before-write
commit, its byte-for-byte agreement with the per-call writers it replaces,
and the setters that no longer open the file when nothing changed.
"""

import json
import os
from pathlib import Path

import pytest

from overcode.session_manager import PendingUpdates, Session, SessionManager, SessionStats
from tests.daemon_tick_harness import count_sessions_io, settle


def _manager(tmp_path, n=3) -> SessionManager:
    sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
    state = {}
    for i in range(n):
        s = Session(
            id=f"s{i}",
            name=f"agent-{i}",
            tmux_session="agents",
            tmux_window=f"w{i}",
            command=["claude"],
            start_directory=None,
            start_time="2026-01-01T00:00:00",
            branch="main",
            agent_session_ids=["sid-a"],
            active_agent_session_id="sid-a",
            stats=SessionStats(interaction_count=i, current_task="Initializing..."),
        )
        state[s.id] = s.to_dict()
    sm._save_state(state)
    settle(sm.state_file)
    sm.list_sessions()  # warm the snapshot: the setters compare against it
    return sm


def _signature(path: Path):
    st = os.stat(path)
    return st.st_mtime_ns, st.st_size, st.st_ino


class TestPendingView:
    def test_view_is_the_same_object_when_nothing_is_staged(self, tmp_path):
        sm = _manager(tmp_path)
        s = sm.get_session("s0")
        pending = PendingUpdates()
        assert not pending
        assert pending.view(s) is s
        pending.update_session("s1", branch="x")
        assert pending.view(s) is s  # staged for another session

    def test_view_applies_fields_and_stats_without_touching_the_snapshot(self, tmp_path):
        sm = _manager(tmp_path)
        s = sm.get_session("s0")
        pending = PendingUpdates()
        pending.update_session("s0", branch="feature", pr_number=12, loaded_skills=["a"])
        pending.update_stats("s0", current_task="Active: Read", green_time_seconds=5.5)
        pending.update_session_status("s0", "terminated")
        v = pending.view(s)
        assert v is not s
        assert (v.branch, v.pr_number, v.loaded_skills, v.status) == (
            "feature",
            12,
            ["a"],
            "terminated",
        )
        assert (v.stats.current_task, v.stats.green_time_seconds) == ("Active: Read", 5.5)
        assert v.stats.interaction_count == s.stats.interaction_count
        assert v.id == s.id and v.name == s.name
        # The snapshot object is untouched
        assert (s.branch, s.pr_number, s.status, s.stats.current_task) == (
            "main",
            None,
            "running",
            "Initializing...",
        )
        assert s.stats is not v.stats

    def test_view_ignores_legacy_twins_and_accepts_legacy_names(self, tmp_path):
        sm = _manager(tmp_path)
        s = sm.get_session("s0")
        pending = PendingUpdates()
        pending.update_session("s0", claude_session_ids=["sid-a", "sid-b"])
        assert pending.fields["s0"] == {
            "agent_session_ids": ["sid-a", "sid-b"],
            "claude_session_ids": ["sid-a", "sid-b"],
        }
        assert pending.view(s).agent_session_ids == ["sid-a", "sid-b"]

    def test_later_stage_overrides_earlier_for_the_same_key(self):
        pending = PendingUpdates()
        pending.update_stats("s0", current_task="one", green_time_seconds=1.0)
        pending.update_stats("s0", current_task="two")
        assert pending.stats["s0"] == {"current_task": "two", "green_time_seconds": 1.0}


class TestCommitPending:
    def test_one_write_for_many_sessions(self, tmp_path):
        sm = _manager(tmp_path, n=5)
        pending = PendingUpdates()
        for i in range(5):
            pending.update_stats(f"s{i}", current_task=f"task {i}", green_time_seconds=float(i))
            pending.update_session(f"s{i}", branch=f"b{i}")
        pending.update_session_status("s2", "terminated")
        with count_sessions_io(sm.state_file) as io:
            assert sm.commit_pending(pending) is True
        assert (io.reads, io.writes) == (1, 1)
        raw = json.loads(sm.state_file.read_text())
        assert [raw[f"s{i}"]["stats"]["current_task"] for i in range(5)] == [
            f"task {i}" for i in range(5)
        ]
        assert raw["s2"]["status"] == "terminated" and raw["s2"]["branch"] == "b2"
        assert raw["s3"]["stats"]["green_time_seconds"] == 3.0

    def test_no_write_when_every_staged_value_is_already_on_disk(self, tmp_path):
        sm = _manager(tmp_path)
        pending = PendingUpdates()
        pending.update_stats("s1", current_task="Initializing...", interaction_count=1)
        pending.update_session("s1", branch="main", status="running")
        pending.update_session("s0", agent_session_ids=["sid-a"])
        before = _signature(sm.state_file)
        with count_sessions_io(sm.state_file) as io:
            assert sm.commit_pending(pending) is False
        assert (io.reads, io.writes) == (1, 0)
        assert _signature(sm.state_file) == before

    def test_empty_pending_does_not_open_the_file(self, tmp_path):
        sm = _manager(tmp_path)
        with count_sessions_io(sm.state_file) as io:
            assert sm.commit_pending(PendingUpdates()) is False
        assert (io.reads, io.writes) == (0, 0)

    def test_one_differing_value_among_equal_ones_writes(self, tmp_path):
        sm = _manager(tmp_path)
        pending = PendingUpdates()
        pending.update_stats("s1", current_task="Initializing...", interaction_count=1)
        pending.update_stats("s2", interaction_count=99)
        with count_sessions_io(sm.state_file) as io:
            assert sm.commit_pending(pending) is True
        assert io.writes == 1
        assert sm.get_session("s2").stats.interaction_count == 99

    def test_vanished_entry_is_skipped_and_missing_stats_dict_is_created(self, tmp_path):
        sm = _manager(tmp_path)
        raw = json.loads(sm.state_file.read_text())
        del raw["s1"]["stats"]
        sm._save_state(raw)
        pending = PendingUpdates()
        pending.update_stats("gone", current_task="x")
        pending.update_session("gone", branch="x")
        pending.update_stats("s1", current_task="created")
        assert sm.commit_pending(pending) is True
        raw = json.loads(sm.state_file.read_text())
        assert "gone" not in raw
        assert raw["s1"]["stats"]["current_task"] == "created"
        assert raw["s1"]["stats"]["interaction_count"] == 0  # the rest are defaults

    def test_commit_matches_the_per_call_writers_byte_for_byte(self, tmp_path):
        """The file after commit_pending equals the file after the same
        changes made through update_session / update_stats /
        update_session_status one by one, legacy twins included."""
        per_call = _manager(tmp_path / "a")
        batched = _manager(tmp_path / "b")

        per_call.update_session("s0", branch="feature", claude_session_ids=["sid-a", "sid-z"])
        per_call.update_stats("s0", current_task="Active: Edit", operation_times=[1.5, 2.0])
        per_call.update_session_status("s1", "terminated")
        per_call.update_session(
            "s2", active_agent_session_id="sid-q", pr_number=None, pr_branch=None
        )

        pending = PendingUpdates()
        pending.update_session("s0", branch="feature", claude_session_ids=["sid-a", "sid-z"])
        pending.update_stats("s0", current_task="Active: Edit", operation_times=[1.5, 2.0])
        pending.update_session_status("s1", "terminated")
        pending.update_session(
            "s2", active_agent_session_id="sid-q", pr_number=None, pr_branch=None
        )
        batched.commit_pending(pending)

        assert per_call.state_file.read_bytes() == batched.state_file.read_bytes()
        raw = json.loads(batched.state_file.read_text())
        assert raw["s0"]["claude_session_ids"] == ["sid-a", "sid-z"]
        assert raw["s2"]["active_claude_session_id"] == "sid-q"

    def test_external_write_between_staging_and_commit_is_preserved(self, tmp_path):
        sm = _manager(tmp_path)
        sm.list_sessions()
        pending = PendingUpdates()
        pending.update_stats("s0", current_task="mine")
        other = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        other.update_session("s0", human_annotation="from elsewhere")
        sm.commit_pending(pending)
        settle(sm.state_file)
        s = sm.get_session("s0")
        assert (s.stats.current_task, s.human_annotation) == ("mine", "from elsewhere")


class TestStateTransaction:
    def test_clean_transaction_does_not_write(self, tmp_path):
        sm = _manager(tmp_path)
        before = _signature(sm.state_file)
        with count_sessions_io(sm.state_file) as io:
            with sm._state_transaction() as txn:
                assert txn.state["s0"]["name"] == "agent-0"
                txn.state["s0"]["name"] = "changed but not marked"
        assert (io.reads, io.writes) == (1, 0)
        assert _signature(sm.state_file) == before
        assert sm.get_session("s0").name == "agent-0"

    def test_locked_state_always_writes(self, tmp_path):
        sm = _manager(tmp_path)
        with count_sessions_io(sm.state_file) as io:
            with sm._locked_state() as state:
                pass
        assert (io.reads, io.writes) == (1, 1)

    def test_exception_in_the_body_writes_nothing(self, tmp_path):
        sm = _manager(tmp_path)
        before = _signature(sm.state_file)
        with pytest.raises(RuntimeError):
            with sm._locked_state() as state:
                state["s0"]["name"] = "half done"
                raise RuntimeError("boom")
        assert _signature(sm.state_file) == before


class TestSettersCompareBeforeWrite:
    def test_set_active_agent_session_id_is_a_no_op_when_unchanged(self, tmp_path):
        sm = _manager(tmp_path)
        before = _signature(sm.state_file)
        with count_sessions_io(sm.state_file) as io:
            sm.set_active_agent_session_id("s0", "sid-a")
        assert (io.reads, io.writes) == (0, 0)
        assert _signature(sm.state_file) == before
        sm.set_active_agent_session_id("s0", "sid-b")
        raw = json.loads(sm.state_file.read_text())["s0"]
        assert raw["active_agent_session_id"] == raw["active_claude_session_id"] == "sid-b"

    def test_add_agent_session_id_is_a_no_op_when_present(self, tmp_path):
        sm = _manager(tmp_path)
        with count_sessions_io(sm.state_file) as io:
            assert sm.add_agent_session_id("s0", "sid-a") is False
        assert (io.reads, io.writes) == (0, 0)
        assert sm.add_agent_session_id("s0", "sid-b") is True
        assert sm.get_session("s0").agent_session_ids == ["sid-a", "sid-b"]

    def test_update_session_status_is_a_no_op_when_unchanged(self, tmp_path):
        sm = _manager(tmp_path)
        before = _signature(sm.state_file)
        with count_sessions_io(sm.state_file) as io:
            sm.update_session_status("s0", "running")
        assert (io.reads, io.writes) == (0, 0)
        assert _signature(sm.state_file) == before
        sm.update_session_status("s0", "terminated")
        assert sm.get_session("s0").status == "terminated"

    def test_update_session_status_of_unknown_id_still_takes_the_old_path(self, tmp_path):
        sm = _manager(tmp_path)
        sm.update_session_status("nope", "terminated")  # no error, as before
        assert sm.get_session("nope") is None
