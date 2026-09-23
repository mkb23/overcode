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
import json
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


# ── capture gating in the loop (audit R11) ────────────────────────────────

IDLE_PANE = "⏺ Done with the change.\n\n❯ \n  ? for shortcuts"
ACTIVE_PANE = "⏺ Running tests\n✻ Thinking… (esc to interrupt)\n❯ "


def _write_hook_state(root, name, event, stamp):
    """A hook_state file the daemon's default HookStatusDetector will find."""
    state_dir = root / "home" / ".overcode" / "sessions" / TMUX
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / f"hook_state_{name}.json").write_text(
        json.dumps({"event": event, "timestamp": stamp})
    )


def _gated_fleet(root, n, mode, *, gated=True, pane=IDLE_PANE):
    """A daemon over the real dispatcher (both detectors on a FakeTmux).

    ``gated=False`` builds the dispatcher without the daemon's capture gate:
    the loop still plans, nothing consults the plan — the pre-gating loop,
    for identity comparisons.
    """
    from overcode.status_detector_factory import StatusDetectorDispatcher

    sm = SessionManager(state_dir=root / "home" / ".overcode" / "sessions", skip_git_detection=True)
    sessions = seed_sessions(sm, n, TMUX, root / "work", START)
    tmux = FakeTmux(TMUX, {s.tmux_window: pane for s in sessions})
    if mode == "hooks":
        for s in sessions:
            _write_hook_state(root, s.name, "Stop", START.timestamp())
    daemon = make_daemon(
        root / "home" / ".overcode", TMUX, ScriptedDetector(_idle), session_manager=sm, tmux=tmux
    )
    daemon.detector = StatusDetectorDispatcher(
        TMUX, tmux=tmux, mode=mode, capture_gate=daemon._capture_gate if gated else None
    )
    seed_steady_state(daemon, sessions, START)
    return sm, sessions, tmux, daemon


def _run(daemon, tmux, clock, ticks, between=None, step=2.0):
    """Run ticks ``step`` seconds apart; return per-tick tmux call counts and the
    published (status, activity) per session after each."""
    from unittest.mock import patch

    counts, published = [], []
    daemon._loop_clock = lambda: clock.now.timestamp()

    def after_tick(i):
        counts.append(dict(tmux.calls))
        tmux.calls.clear()
        published.append(
            {s.session_id: (s.current_status, s.current_activity) for s in daemon.state.sessions}
        )
        if between is not None:
            between(i)

    with (
        patch("overcode.settings.resolve_detection_mode", lambda *a, **k: daemon.detector.mode),
        # The 5 s / 15 s syncs fall due as the clock advances: no real `ps`
        patch("overcode.process_resources.snapshot_processes", lambda: {}),
        patch("overcode.doctor._snapshot_process_table", lambda: []),
    ):
        run_ticks(
            daemon, daemon.detector, clock, ticks, step_seconds=step, between_ticks=after_tick
        )
    return counts, published


@pytest.mark.parametrize("mode", ["hooks", "polling"])
class TestCaptureGating:
    N = 50

    def test_idle_fleet_of_50_costs_one_command_per_loop(self, root, mode):
        """Loop 1 captures everything once; an unchanged fleet then costs the
        listing alone — until the keepalive re-reads every pane after 5 s."""
        sm, sessions, tmux, daemon = _gated_fleet(root, self.N, mode)
        with FrozenClock(START).installed() as clock:
            counts, published = _run(daemon, tmux, clock, 4)  # t = 0, 2, 4, 6 s
        assert counts[0] == {"list_panes": 1, "capture_pane": self.N}
        assert counts[1] == {"list_panes": 1}
        assert counts[2] == {"list_panes": 1}
        assert counts[3] == {"list_panes": 1, "capture_pane": self.N}  # 6 s >= keepalive
        assert published[3] == published[0]

    def test_all_active_fleet_of_50_costs_the_listing_plus_a_capture_each(self, root, mode):
        sm, sessions, tmux, daemon = _gated_fleet(root, self.N, mode)

        def churn(i):
            for k, s in enumerate(sessions):
                tmux.panes[s.tmux_window] = f"{ACTIVE_PANE}\n⏺ step {i} of {k}"

        with FrozenClock(START).installed() as clock:
            counts, _ = _run(daemon, tmux, clock, 3, between=churn)
        for c in counts:
            assert c == {"list_panes": 1, "capture_pane": self.N}

    def test_a_changed_pane_is_captured_on_the_very_next_loop(self, root, mode):
        """One pane gains a PR link; that loop captures it (and only it), the
        enrichment sees the new text, one follow-up capture, then quiet."""
        sm, sessions, tmux, daemon = _gated_fleet(root, self.N, mode)
        target = sessions[7]

        def change(i):
            if i == 0:
                tmux.panes[target.tmux_window] = (
                    IDLE_PANE + "\n⏺ Opened https://github.com/acme/repo/pull/4242"
                )

        with FrozenClock(START).installed() as clock:
            counts, _ = _run(
                daemon, tmux, clock, 4, between=change, step=1.0
            )  # under the keepalive
        assert counts[1] == {"list_panes": 1, "capture_pane": 1}
        assert counts[2] == {"list_panes": 1, "capture_pane": 1}  # follow-up
        assert counts[3] == {"list_panes": 1}
        assert sm.get_session(target.id).pr_number == 4242
        assert sum(s.pr_number == 4242 for s in sm.list_sessions()) == 1

    def test_listing_failure_falls_back_to_capturing_everything(self, root, mode):
        sm, sessions, tmux, daemon = _gated_fleet(root, self.N, mode)
        daemon._tmux = FakeTmux("some-other-session", {})
        with FrozenClock(START).installed() as clock:
            counts, _ = _run(daemon, tmux, clock, 3)
        for c in counts:
            assert c == {"capture_pane": self.N}
        assert daemon._tmux.calls["list_panes"] == 3

    def test_blind_spot_is_caught_by_the_keepalive(self, root, mode):
        """Documented blind spot: an in-place rewrite at the same cursor position
        within the same second leaves the signature unchanged (FakeTmux pins it
        here), so the loop skips the capture; the 5 s keepalive re-reads it."""
        sm, sessions, tmux, daemon = _gated_fleet(root, 5, mode)
        target = sessions[2]
        tmux.signatures[target.tmux_window] = (1_790_000_000, 3, 0, 2, "claude")

        def rewrite(i):
            if i == 0:
                tmux.panes[target.tmux_window] = (
                    IDLE_PANE + "\n⏺ https://github.com/acme/repo/pull/99"
                )

        with FrozenClock(START).installed() as clock:
            counts, _ = _run(daemon, tmux, clock, 4, between=rewrite)  # t = 0, 2, 4, 6
        assert counts[1] == {"list_panes": 1} and counts[2] == {"list_panes": 1}
        assert counts[3]["capture_pane"] == 5  # keepalive at 6 s
        assert sm.get_session(target.id).pr_number == 99


class TestHookStateGating:
    def test_a_hook_event_without_a_pane_change_is_captured_and_changes_status(self, root):
        sm, sessions, tmux, daemon = _gated_fleet(root, 10, "hooks")
        target = sessions[3]

        def hook_fires(i):
            if i == 0:
                _write_hook_state(root, target.name, "UserPromptSubmit", clock.now.timestamp())

        with FrozenClock(START).installed() as clock:
            counts, published = _run(daemon, tmux, clock, 3, between=hook_fires)
        assert published[0][target.id][0] == "waiting_user"
        assert counts[1] == {"list_panes": 1, "capture_pane": 1}
        assert published[1][target.id][0] == "running"
        assert counts[2] == {"list_panes": 1, "capture_pane": 1}  # follow-up
        others = {sid: v for sid, v in published[1].items() if sid != target.id}
        assert others == {sid: v for sid, v in published[0].items() if sid != target.id}

    def test_hook_state_stamp_reads_the_detectors_file(self, root):
        sm, sessions, tmux, daemon = _gated_fleet(root, 2, "hooks")
        name = sessions[0].name
        stamp = daemon._hook_state_stamp(name)
        assert stamp is not None
        _write_hook_state(root, name, "PostToolUse", START.timestamp() + 1)
        assert daemon._hook_state_stamp(name) != stamp
        assert daemon._hook_state_stamp("no-such-agent") is None


class TestGatedIdentity:
    """Gated and ungated loops publish the same statuses and enrichment over a
    scripted pane sequence: changes at different ticks, a hook event, a window
    that vanishes and comes back, an agent going active and idle again."""

    N = 12

    def _scenario(self, root, mode, gated):
        sm, sessions, tmux, daemon = _gated_fleet(root, self.N, mode, gated=gated)
        w = [s.tmux_window for s in sessions]

        def between(i):
            if i == 0:
                tmux.panes[w[0]] = IDLE_PANE + "\n⏺ see https://github.com/acme/repo/pull/7"
                tmux.panes[w[1]] = ACTIVE_PANE
            elif i == 1:
                if mode == "hooks":
                    _write_hook_state(root, sessions[2].name, "PostToolUse", START.timestamp() + 3)
                del tmux.panes[w[3]]
            elif i == 2:
                tmux.panes[w[3]] = IDLE_PANE
                tmux.panes[w[1]] = IDLE_PANE + "\n⏺ tests green"
            elif i == 4:
                tmux.panes[w[0]] = IDLE_PANE + "\n⏺ merged"
                tmux.panes[w[5]] = ACTIVE_PANE

        with FrozenClock(START).installed() as clock:
            counts, published = _run(daemon, tmux, clock, 7, between=between, step=1.0)
        from tests.daemon_tick_harness import state_without_timestamps

        # Each run has its own root, which start_directory embeds
        state = json.loads(sm.state_file.read_text().replace(str(root), "<root>"))
        return counts, published, state_without_timestamps(state)

    @pytest.mark.parametrize("mode", ["hooks", "polling"])
    def test_same_outputs_far_fewer_captures(self, tmp_path, monkeypatch, mode):
        runs = {}
        for gated in (False, True):
            root = tmp_path / ("gated" if gated else "plain")
            (root / "home" / ".overcode" / "sessions").mkdir(parents=True)
            monkeypatch.setenv("OVERCODE_STATE_DIR", str(root / "home" / ".overcode" / "sessions"))
            runs[gated] = self._scenario(root, mode, gated)
        counts_plain, published_plain, state_plain = runs[False]
        counts_gated, published_gated, state_gated = runs[True]
        assert published_gated == published_plain
        assert state_gated == state_plain
        # Something actually happened in the scenario (a vanished window reads as
        # terminated in polling mode; in hooks mode the Stop hook still speaks)
        seen = {s[0] for p in published_gated for s in p.values()}
        assert "running" in seen
        assert mode == "hooks" or "terminated" in seen
        plain = sum(c.get("capture_pane", 0) for c in counts_plain)
        gated = sum(c.get("capture_pane", 0) for c in counts_gated)
        assert plain == 7 * self.N
        assert gated < plain / 2
