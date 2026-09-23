"""SessionIndex: the daemon's once-per-tick parent/child lookups (audit R5).

Pins the index to the per-call methods it replaces — ``get_children``,
``compute_depth`` / ``get_parent_chain`` and the daemon's parent-name
lookup — over the same snapshot, including a missing parent and a cycle,
and checks a real tick publishes the same hierarchy fields without calling
any of them per session.
"""

import os
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from overcode.session_manager import Session, SessionIndex, SessionManager
from tests.daemon_tick_harness import (
    FrozenClock,
    ScriptedDetector,
    make_daemon,
    seed_sessions,
    seed_steady_state,
    settle,
)


def _session(sid, name, parent=None, tmux="agents"):
    return Session(
        id=sid,
        name=name,
        tmux_session=tmux,
        tmux_window=name,
        command=["claude"],
        start_directory=None,
        start_time="2026-01-01T00:00:00",
        parent_session_id=parent,
    )


def _tree(tmp_path) -> SessionManager:
    sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
    entries = [
        _session("root-a", "a"),
        _session("root-b", "b"),
        _session("child-a1", "a1", parent="root-a"),
        _session("child-a2", "a2", parent="root-a"),
        _session("grand-a1x", "a1x", parent="child-a1"),
        _session("great-a1xy", "a1xy", parent="grand-a1x"),
        _session("orphan", "orphan", parent="gone"),  # parent not in the table
        _session("cyc-1", "cyc-1", parent="cyc-2"),  # a two-node cycle
        _session("cyc-2", "cyc-2", parent="cyc-1"),
        _session("elsewhere", "elsewhere", parent="root-b", tmux="other"),
    ]
    sm._save_state({s.id: s.to_dict() for s in entries})
    settle(sm.state_file)
    return sm


class TestSessionIndexMatchesPerCallMethods:
    def test_depth_children_and_parent_name_match_over_the_whole_table(self, tmp_path):
        sm = _tree(tmp_path)
        index = SessionIndex(sm.sessions_by_id())
        for s in sm.list_sessions():
            assert index.depth(s) == sm.compute_depth(s), s.id
            assert index.children_count(s.id) == len(sm.get_children(s.id)), s.id
            assert [p.id for p in index.parent_chain(s.id)] == [
                p.id for p in sm.get_parent_chain(s.id)
            ]
            parent = sm.get_session(s.parent_session_id) if s.parent_session_id else None
            assert index.parent_name(s) == (parent.name if parent else None)

    def test_specific_answers(self, tmp_path):
        sm = _tree(tmp_path)
        by_id = sm.sessions_by_id()
        index = SessionIndex(by_id)
        assert index.depth(by_id["root-a"]) == 0
        assert index.depth(by_id["great-a1xy"]) == 3
        assert index.children_count("root-a") == 2
        assert index.children_count("root-b") == 1  # the child in another tmux session counts
        assert index.children_count("great-a1xy") == 0
        assert index.parent_name(by_id["orphan"]) is None
        assert index.depth(by_id["orphan"]) == 0
        # A cycle terminates once a member repeats: each member is visited once
        assert index.depth(by_id["cyc-1"]) == 2
        assert [p.id for p in index.parent_chain("cyc-1")] == ["cyc-2", "cyc-1"]
        assert index.children_count("cyc-1") == 1

    def test_of_builds_from_a_list_and_counts_lazily(self, tmp_path):
        sm = _tree(tmp_path)
        index = SessionIndex.of(sm.list_sessions())
        assert index._children_count is None
        assert index.children_count("child-a1") == 1
        assert index._children_count is not None
        assert index.by_id["root-a"] is sm.get_session("root-a")

    def test_sessions_by_id_is_the_shared_snapshot(self, tmp_path):
        sm = _tree(tmp_path)
        by_id = sm.sessions_by_id()
        assert list(by_id) == [s.id for s in sm.list_sessions()]
        assert by_id["root-a"] is sm.get_session("root-a")
        assert sm.sessions_by_id() is by_id  # unchanged file, same mapping


class TestDaemonUsesTheTickIndex:
    @pytest.fixture
    def env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "state" / "sessions"))
        (tmp_path / "state" / "sessions").mkdir(parents=True)
        return tmp_path

    def test_tick_publishes_hierarchy_without_per_session_lookups(self, env):
        start = datetime(2026, 9, 23, 12, 0, 0)
        sm = SessionManager(state_dir=env / "state" / "sessions", skip_git_detection=True)
        sessions = seed_sessions(sm, 25, "agents", env / "work", start)
        detector = ScriptedDetector(lambda tick, s: ("running", "Active: Read(x.py)", "pane"))
        daemon = make_daemon(env / "state", "agents", detector, session_manager=sm)
        seed_steady_state(daemon, sessions, start)
        expected = {
            s.id: (
                (sm.get_session(s.parent_session_id).name if s.parent_session_id else None),
                sm.compute_depth(s),
                len(sm.get_children(s.id)),
            )
            for s in sessions
        }
        with (
            FrozenClock(start).installed(),
            patch.object(
                SessionManager, "get_children", side_effect=AssertionError("per-session scan")
            ),
            patch.object(
                SessionManager, "compute_depth", side_effect=AssertionError("per-session walk")
            ),
            patch.object(
                SessionManager, "get_parent_chain", side_effect=AssertionError("per-session walk")
            ),
        ):
            daemon._tick(start)
        published = {
            s.session_id: (s.parent_name, s.depth, s.children_count) for s in daemon.state.sessions
        }
        assert published == expected
        assert {v[1] for v in published.values()} == {0, 1, 2}
        assert sum(v[2] for v in published.values()) == 12

    def test_track_session_stats_without_an_index_builds_one(self, env):
        start = datetime(2026, 9, 23, 12, 0, 0)
        sm = SessionManager(state_dir=env / "state" / "sessions", skip_git_detection=True)
        sessions = seed_sessions(sm, 22, "agents", env / "work", start)
        daemon = make_daemon(
            env / "state",
            "agents",
            ScriptedDetector(lambda t, s: ("running", "", "")),
            session_manager=sm,
        )
        grandchild = sessions[21]
        state = daemon.track_session_stats(grandchild, "running")
        assert (state.parent_name, state.depth, state.children_count) == ("agent-11", 2, 0)
        assert daemon._get_parent_name(sessions[11]) == "agent-01"
        assert daemon._get_parent_name(sessions[0]) is None
