"""tmux command budget of the monitor daemon's periodic paths (audit R7, R11).

The tmux server is single-threaded and shared by every overcode process on
the host, so what these tests pin is *how many commands* a tick issues, on
the harness's counting ``FakeTmux``: one persistent client on the daemon,
one ``list-panes -s`` per tick for every window's pane pid — shared by the
5 s process-resources sync and the 15 s sandbox sync — instead of a fresh
``RealTmux`` per sync and a three-command ``get_pane_pid`` per agent.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from overcode.process_resources import ProcInfo
from overcode.session_manager import SessionManager
from tests.daemon_tick_harness import (
    FakeTmux,
    FrozenClock,
    ScriptedDetector,
    make_daemon,
    run_ticks,
    seed_sessions,
    seed_steady_state,
)

TMUX = "agents"
START = datetime(2026, 9, 23, 12, 0, 0)


@pytest.fixture
def root(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".overcode" / "sessions").mkdir(parents=True)
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(home / ".overcode" / "sessions"))
    return tmp_path


def _idle(tick, session):
    return "waiting_user", "Waiting for input", "pane"


def _fleet(root, n, *, pids=True):
    """``n`` live sessions, a FakeTmux with a pane (and pid) per window, a daemon on both."""
    sm = SessionManager(state_dir=root / "home" / ".overcode" / "sessions", skip_git_detection=True)
    sessions = seed_sessions(sm, n, TMUX, root / "work", START)
    tmux = FakeTmux(
        TMUX,
        {s.tmux_window: "pane" for s in sessions},
        {s.tmux_window: 10_000 + i for i, s in enumerate(sessions)} if pids else {},
    )
    detector = ScriptedDetector(_idle)
    daemon = make_daemon(root / "home" / ".overcode", TMUX, detector, session_manager=sm, tmux=tmux)
    seed_steady_state(daemon, sessions, START)
    return sm, sessions, tmux, detector, daemon


def _process_doubles(sessions, tmux, cpu_of):
    """Synthetic ``ps`` (a shell per pane with a claude child) and a silent ``lsof``."""

    pane_of = {s.id: tmux.pids[s.tmux_window] for s in sessions}

    def snapshot_processes():
        table = {1: ProcInfo(ppid=0, cpu_pct=0.0, rss_kb=100, argv="/sbin/launchd")}
        for s in sessions:
            pane = pane_of[s.id]
            cpu, rss = cpu_of(s)
            table[pane] = ProcInfo(ppid=1, cpu_pct=0.0, rss_kb=96, argv="-zsh")
            table[pane + 10_000] = ProcInfo(
                ppid=pane,
                cpu_pct=cpu,
                rss_kb=rss // 1024,
                argv=f"claude --session-id {s.active_agent_session_id}",
            )
        return table

    def process_table():
        return [(pid, info.ppid, info.argv) for pid, info in snapshot_processes().items()]

    return (
        patch("overcode.process_resources.snapshot_processes", snapshot_processes),
        patch("overcode.doctor._snapshot_process_table", process_table),
        patch("overcode.sandbox_detect._run_lsof", lambda pids, timeout=3.0: ""),
    )


class TestPidMapSharedByTheSyncs:
    N = 50

    def test_one_listing_per_tick_feeds_both_syncs(self, root):
        """A tick with the 5 s and 15 s syncs due: one tmux command, no get_pane_pid,
        and the readings reached every session through the map."""
        sm, sessions, tmux, detector, daemon = _fleet(root, self.N)
        daemon._last_resources_sync = None
        daemon._last_sandbox_sync = None
        tmux.attached = 2
        p1, p2, p3 = _process_doubles(
            sessions, tmux, lambda s: (50.0 + int(s.name[-2:]), (600 + int(s.name[-2:])) << 20)
        )
        with p1, p2, p3, FrozenClock(START).installed() as clock:
            run_ticks(daemon, detector, clock, 1)

        assert tmux.calls["list_panes"] == 1
        assert tmux.calls["get_pane_pid"] == 0
        assert sum(tmux.calls.values()) == 1, dict(tmux.calls)
        assert daemon.session_attached == 2
        by_name = {s.name: s for s in sm.list_sessions()}
        for s in sessions:
            i = int(s.name[-2:])
            assert by_name[s.name].cpu_percent == 50.0 + i
            assert by_name[s.name].rss_bytes == (600 + i) << 20

    def test_standalone_syncs_list_once_per_now(self, root):
        """Called on their own (the bench does), each sync costs at most one command;
        two phases sharing a ``now`` share the listing."""
        sm, sessions, tmux, detector, daemon = _fleet(root, self.N)
        p1, p2, p3 = _process_doubles(sessions, tmux, lambda s: (s.cpu_percent, s.rss_bytes))
        t1, t2 = START, START + timedelta(seconds=5)
        with p1, p2, p3:
            daemon._last_resources_sync = None
            daemon._sync_process_resources(sessions, t1)
            assert sum(tmux.calls.values()) == 1
            daemon._last_sandbox_sync = None
            daemon._sync_sandbox_state(sessions, t1)
            assert sum(tmux.calls.values()) == 1  # same tick: shared
            daemon._last_resources_sync = None
            daemon._sync_process_resources(sessions, t2)
            assert sum(tmux.calls.values()) == 2  # a new tick: listed again
        assert tmux.calls["list_panes"] == 2 and tmux.calls["get_pane_pid"] == 0
        # Unchanged readings stage nothing
        for s in sessions:
            view = daemon._pending.view(s)
            assert (view.cpu_percent, view.rss_bytes) == (s.cpu_percent, s.rss_bytes)

    def test_failed_listing_skips_every_session_without_staging(self, root):
        """tmux down / session gone: no pids, so no session is sampled and nothing
        is written — what a None from get_pane_pid did per session."""
        sm, sessions, _tmux, detector, daemon = _fleet(root, 5)
        daemon._tmux = FakeTmux("another-session", {})
        p1, p2, p3 = _process_doubles(sessions, _tmux, lambda s: (99.0, 1 << 30))
        with p1, p2, p3:
            daemon._last_resources_sync = None
            daemon._sync_process_resources(sessions, START)
            daemon._last_sandbox_sync = None
            daemon._sync_sandbox_state(sessions, START)
        assert daemon._tmux.calls["list_panes"] == 1
        assert not daemon._pending
        assert daemon.session_attached is None
        assert daemon._last_resources_sync == START and daemon._last_sandbox_sync == START

    def test_a_window_gone_from_the_listing_is_skipped(self, root):
        """Absent from ``list-panes`` = no pane pid, the same as a dead window's
        None from get_pane_pid: the session is skipped, its entry untouched."""
        sm, sessions, tmux, detector, daemon = _fleet(root, 3)
        gone = sessions[1]
        del tmux.panes[gone.tmux_window]
        p1, p2, p3 = _process_doubles(
            [s for s in sessions if s is not gone], tmux, lambda s: (90.0, 1 << 30)
        )
        with p1, p2, p3:
            daemon._last_resources_sync = None
            daemon._sync_process_resources(sessions, START)
        view = daemon._pending.view(gone)
        assert (view.cpu_percent, view.rss_bytes) == (gone.cpu_percent, gone.rss_bytes)
        assert daemon._pending.view(sessions[0]).cpu_percent == 90.0

    def test_pane_without_a_claude_child_resets_cpu(self, root):
        sm, sessions, tmux, detector, daemon = _fleet(root, 2)
        dead = sessions[0]

        def snapshot():
            pane = tmux.pids[dead.tmux_window]
            return {pane: ProcInfo(ppid=1, cpu_pct=0.0, rss_kb=96, argv="-zsh")}

        with patch("overcode.process_resources.snapshot_processes", snapshot):
            daemon._last_resources_sync = None
            daemon._sync_process_resources([dead], START)
        assert daemon._pending
        view = daemon._pending.view(dead)
        assert (view.cpu_percent, view.rss_bytes) == (0.0, 0)

    def test_legacy_digit_window_resolves_by_index(self, root):
        """A pre-migration ``tmux_window`` like "2" finds its pane by index, as
        ``RealTmux._get_window`` fell back to."""
        sm, sessions, tmux, detector, daemon = _fleet(root, 3)
        legacy = sessions[1]  # FakeTmux numbers windows from 1 in insertion order: index 2
        p1, p2, p3 = _process_doubles(sessions, tmux, lambda s: (77.0, 5 << 20))
        legacy.tmux_window = "2"
        with p1, p2, p3:
            daemon._last_resources_sync = None
            daemon._sync_process_resources([legacy], START)
        assert daemon._pending.view(legacy).cpu_percent == 77.0


class TestOnePersistentClient:
    PERIODIC = (
        "_sync_process_resources",
        "_sync_sandbox_state",
        "_count_untracked_windows",
        "_migrate_legacy_window_ids",
        "_panes_at",
        "_detect_and_enrich",
        "_tick_phases",
    )

    def test_no_periodic_method_builds_its_own_client(self):
        from overcode.monitor_daemon import MonitorDaemon

        for name in self.PERIODIC:
            assert "RealTmux(" not in inspect.getsource(getattr(MonitorDaemon, name)), name

    def test_daemon_builds_one_real_client_when_none_is_injected(self, root):
        with patch("overcode.implementations.RealTmux") as real_tmux:
            _fleet(root, 1)
        real_tmux.assert_called_once_with()

    def test_injected_client_serves_the_untracked_count_and_the_migration(self, root):
        sm, sessions, tmux, detector, daemon = _fleet(root, 2)
        tmux.panes["rogue"] = "a shell"
        assert daemon._count_untracked_windows(sessions) == 1
        assert tmux.calls["has_session"] == 1 and tmux.calls["list_windows"] == 1
        daemon._migrate_legacy_window_ids(sessions)
        assert tmux.calls["list_windows"] == 2
