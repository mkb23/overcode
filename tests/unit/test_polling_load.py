"""Tests for the polling load-shaping changes.

Background: users reported typing lag in tmux while overcode runs. The tmux
server is single-threaded, so every ``capture-pane`` / ``resize-window`` /
``list-windows`` the TUI and daemon issue is served in line with keystrokes.
These tests pin the behaviours that keep that command count low, plus the
subprocess-free git context reader and the lightweight hook entry point.
"""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.tui_logic import (  # noqa: E402
    NON_FOCUSED_CAPTURE_EVERY,
    NON_FOCUSED_CAPTURES_PER_TICK,
    capture_rotation_period,
    select_capture_sessions,
    should_scan_git,
    windows_needing_resize,
)
from overcode.launcher import (  # noqa: E402
    AgentLauncher,
    window_in_lookup,
    window_lookup,
)
from overcode.session_manager import (  # noqa: E402
    SessionManager,
    read_git_context_from_disk,
)
from overcode.tmux_manager import TmuxManager  # noqa: E402
from overcode.interfaces import MockTmux  # noqa: E402


# ── select_capture_sessions ──────────────────────────────────────────


class TestSelectCaptureSessions:
    IDS = [f"s{i}" for i in range(10)]

    def test_focused_session_captured_every_tick(self):
        for tick in range(20):
            assert "s7" in select_capture_sessions(self.IDS, "s7", tick)

    def test_rotation_is_independent_of_daemon_state(self):
        """No daemon parameter at all: the daemon only decides where a skipped
        session's status comes from, never how many panes a tick captures.
        The old function captured every session every tick once the daemon
        looked stale (4N capture-pane/s on the shared tmux server)."""
        every = NON_FOCUSED_CAPTURE_EVERY
        for tick in range(8):
            chosen = select_capture_sessions(self.IDS, "s0", tick)
            assert len(chosen) <= 1 + (len(self.IDS) + every - 1) // every

    def test_non_focused_rotate_once_per_window(self):
        """Over `every` consecutive ticks each non-focused session is captured exactly once."""
        every = NON_FOCUSED_CAPTURE_EVERY
        counts = {sid: 0 for sid in self.IDS}
        for tick in range(100, 100 + every):
            for sid in select_capture_sessions(self.IDS, "s0", tick, every=every):
                counts[sid] += 1
        assert counts["s0"] == every  # focused
        for sid in self.IDS[1:]:
            assert counts[sid] == 1, sid

    def test_per_tick_command_count_is_bounded(self):
        every = NON_FOCUSED_CAPTURE_EVERY
        for tick in range(every):
            chosen = select_capture_sessions(self.IDS, "s0", tick, every=every)
            # focused + at most ceil(N/every) rotating
            assert len(chosen) <= 1 + (len(self.IDS) + every - 1) // every

    def test_every_one_means_no_skipping(self):
        assert select_capture_sessions(self.IDS, None, 5, every=1) == set(self.IDS)

    def test_zero_every_is_clamped(self):
        assert select_capture_sessions(self.IDS, None, 5, every=0) == set(self.IDS)

    def test_always_ids_captured_every_tick(self):
        """Never-captured sessions get an immediate capture (first sight)."""
        for tick in range(20):
            chosen = select_capture_sessions(self.IDS, "s0", tick, always_ids={"s3", "s8"})
            assert {"s0", "s3", "s8"} <= chosen

    def test_identical_to_previous_selection_when_daemon_was_fresh(self):
        """Byte-identical choice to the old function for the case it handled well.

        The old rule with every session known to the daemon: focused, or
        index % every == tick % every. Up to 48 non-focused agents the
        adaptive period is still 4, so nothing changes for that fleet size.
        """

        def old(ids, focused, tick, every=NON_FOCUSED_CAPTURE_EVERY):
            slot = tick % every
            return {sid for i, sid in enumerate(ids) if sid == focused or i % every == slot}

        for n in (1, 2, 5, 10, 33, 48, 49):
            ids = [f"a{i}" for i in range(n)]
            for tick in range(0, 25):
                # 49 ids = focused + 48 non-focused: still period 4
                new = select_capture_sessions(ids, ids[0], tick)
                assert new == old(ids, ids[0], tick), (n, tick)
                if n <= 48:  # with no focused session all n are non-focused
                    assert select_capture_sessions(ids, None, tick) == old(ids, None, tick)


class TestCaptureRotationPeriod:
    def test_small_fleets_keep_one_hertz(self):
        for n in range(0, 49):
            assert capture_rotation_period(n) == NON_FOCUSED_CAPTURE_EVERY

    def test_period_grows_to_cap_captures_per_tick(self):
        assert capture_rotation_period(49) == 5
        assert capture_rotation_period(60) == 5
        assert capture_rotation_period(61) == 6
        assert capture_rotation_period(200) == 17
        assert capture_rotation_period(1000) == 84

    @pytest.mark.parametrize("n", [8, 48, 50, 51, 200, 1000])
    def test_captures_per_tick_capped_at_any_fleet_size(self, n):
        """<= 1 focused + NON_FOCUSED_CAPTURES_PER_TICK, stale daemon or not."""
        ids = [f"s{i}" for i in range(n)]
        every = capture_rotation_period(n - 1)
        worst = max(len(select_capture_sessions(ids, "s0", t)) for t in range(1, every + 41))
        assert worst <= 1 + NON_FOCUSED_CAPTURES_PER_TICK
        # and every non-focused session is still visited once per period
        seen = set()
        for t in range(100, 100 + every):
            seen |= select_capture_sessions(ids, "s0", t)
        assert seen == set(ids)

    def test_reference_fleet_numbers(self):
        """50 agents: 50 captures/tick with a stale daemon before; 11 now, any daemon."""
        ids = [f"s{i}" for i in range(50)]
        assert max(len(select_capture_sessions(ids, "s0", t)) for t in range(1, 41)) == 11


class TestFastPathRotationWithoutDaemon:
    """Drive the real fast-path worker body with the daemon absent.

    Before: an absent/stale daemon made the TUI capture every pane on every
    250 ms tick. Now the rotation applies and skipped sessions repeat their
    last known status and activity, so the screen reads the same as when
    they were captured.
    """

    N = 10

    def _app(self):
        from overcode.tui import SupervisorTUI

        app = SupervisorTUI.__new__(SupervisorTUI)
        sessions = []
        for i in range(self.N):
            s = MagicMock()
            s.id = f"s{i}"
            s.is_remote = False
            s.status = "running"
            s.tmux_window = f"w{i}"
            sessions.append(s)
        widgets = [MagicMock(session=s) for s in sessions]
        app.session_manager = MagicMock()
        app.session_manager.list_sessions.return_value = sessions
        app.tmux_session = "agents"
        app._get_focused_widget = lambda: widgets[0]
        app._previous_statuses = {}
        app._pane_content_cache = {}
        app._activity_cache = {}
        app._status_tick = 0
        app._prefs = MagicMock(status_change_logging=False)
        app._remote_sessions = []
        app._summaries = {}
        app.detector = MagicMock()
        # No pane listing (tmux cannot answer): the plain rotation this class pins
        from overcode.pane_capture_gate import PaneChangeTracker

        app._pane_change_tracker = PaneChangeTracker()
        app._tmux = MagicMock()
        app._tmux.list_panes.return_value = None

        def detect(session, num_lines=0):
            return ("running", f"act-{session.id}", f"pane-{session.id}")

        app.detector.detect_status.side_effect = detect
        applied = []
        app.call_from_thread = lambda fn, *a, **kw: applied.append((fn, a, kw))
        return app, widgets, applied

    def _tick(self, app, widgets, applied):
        from overcode.tui import SupervisorTUI

        with patch("overcode.tui.get_monitor_daemon_state", return_value=None):
            SupervisorTUI._fetch_statuses_async.__wrapped__(app, widgets)
        fn, args, _ = applied[-1]
        status_results = args[0]
        # What _apply_status_results does with the statuses on the main thread
        for sid, (status, _, _) in status_results.items():
            app._previous_statuses[sid] = status
        return status_results

    def test_first_tick_captures_every_session_once(self):
        app, widgets, applied = self._app()
        results = self._tick(app, widgets, applied)
        assert app.detector.detect_status.call_count == self.N
        for i in range(self.N):
            assert results[f"s{i}"] == ("running", f"act-s{i}", f"pane-s{i}")

    def test_later_ticks_rotate_and_replay_skipped_sessions(self):
        app, widgets, applied = self._app()
        self._tick(app, widgets, applied)
        every = NON_FOCUSED_CAPTURE_EVERY
        for _ in range(every):
            app.detector.detect_status.reset_mock()
            results = self._tick(app, widgets, applied)
            assert app.detector.detect_status.call_count <= 1 + (self.N + every - 1) // every
            # Every session still reports the same status/activity/pane text
            for i in range(self.N):
                assert results[f"s{i}"] == ("running", f"act-s{i}", f"pane-s{i}"), i
        assert "s0" in {c.args[0].id for c in app.detector.detect_status.call_args_list}

    def test_status_change_lands_when_the_slot_comes_round(self):
        app, widgets, applied = self._app()
        self._tick(app, widgets, applied)
        def idle(session, num_lines=0):
            return ("waiting_user", "idle", f"pane-{session.id}")

        app.detector.detect_status.side_effect = idle
        seen_change = {}
        for _ in range(NON_FOCUSED_CAPTURE_EVERY):
            results = self._tick(app, widgets, applied)
            for sid, (status, activity, _) in results.items():
                if status == "waiting_user":
                    seen_change[sid] = activity
        assert set(seen_change) == {f"s{i}" for i in range(self.N)}
        assert set(seen_change.values()) == {"idle"}


# ── windows_needing_resize ───────────────────────────────────────────


class TestWindowsNeedingResize:
    def test_steady_state_sends_nothing(self):
        sizes = {"a": (200, 40), "b": (200, 40)}
        assert windows_needing_resize(sizes, ["a", "b"], 200, 40) == []

    def test_only_mismatched_windows(self):
        sizes = {"a": (200, 40), "b": (180, 40), "c": (200, 39)}
        assert windows_needing_resize(sizes, ["a", "b", "c"], 200, 40) == ["b", "c"]

    def test_unknown_size_is_resized(self):
        """If list-windows failed (empty dict) fall back to resizing everything."""
        assert windows_needing_resize({}, ["a", "b"], 200, 40) == ["a", "b"]


# ── should_scan_git ──────────────────────────────────────────────────


class TestShouldScanGit:
    def test_first_sweep_scans(self):
        assert should_scan_git(0)

    def test_periodic(self):
        every = 3
        assert [should_scan_git(i, every) for i in range(7)] == [
            True, False, False, True, False, False, True
        ]

    def test_every_one_always(self):
        assert all(should_scan_git(i, 1) for i in range(5))


# ── window_lookup / launcher.list_sessions ───────────────────────────


class TestWindowLookup:
    WINDOWS = [
        {"index": 1, "name": "alpha-1234", "command": ""},
        {"index": 4, "name": "beta-abcd", "command": ""},
    ]

    def test_name_match(self):
        lookup = window_lookup(self.WINDOWS)
        assert window_in_lookup("alpha-1234", lookup)
        assert not window_in_lookup("alpha", lookup)

    def test_legacy_digit_index_match(self):
        lookup = window_lookup(self.WINDOWS)
        assert window_in_lookup("4", lookup)
        assert not window_in_lookup("2", lookup)

    def test_empty(self):
        assert not window_in_lookup("anything", window_lookup([]))


class CountingTmuxManager(TmuxManager):
    """TmuxManager that counts tmux-facing calls."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.list_calls = 0
        self.exists_calls = 0

    def list_windows(self, include_command: bool = True):
        self.list_calls += 1
        return super().list_windows(include_command=include_command)

    def window_exists(self, window_name: str) -> bool:
        self.exists_calls += 1
        return super().window_exists(window_name)


@pytest.fixture(autouse=True)
def _isolated_launcher_env(monkeypatch):
    """Same isolation as test_launcher.py: no real tmux/CLI checks, and no
    OVERCODE_* vars leaking from a host agent (which would make every launch
    a child launch of a parent the test SessionManager doesn't have)."""
    from unittest.mock import patch

    for key in ("OVERCODE_SESSION_NAME", "OVERCODE_TMUX_SESSION",
                "OVERCODE_PARENT_SESSION_ID", "OVERCODE_PARENT_NAME"):
        monkeypatch.delenv(key, raising=False)
    with patch("overcode.launcher.require_tmux"), \
         patch("overcode.launcher.require_agent_cli"):
        yield


class TestListSessionsUsesOneWindowList:
    def _launcher(self, tmp_path):
        mock_tmux = MockTmux()
        tm = CountingTmuxManager("agents", tmux=mock_tmux)
        sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        return AgentLauncher(tmux_session="agents", tmux_manager=tm, session_manager=sm), tm, sm

    def test_one_list_windows_no_per_session_exists(self, tmp_path):
        launcher, tm, sm = self._launcher(tmp_path)
        for i in range(5):
            launcher.launch(name=f"agent{i}")
        tm.list_calls = tm.exists_calls = 0

        sessions = launcher.list_sessions()

        assert len(sessions) == 5
        assert tm.list_calls == 1
        assert tm.exists_calls == 0
        assert all(s.status != "terminated" for s in sessions)

    def test_still_detects_terminated(self, tmp_path):
        launcher, tm, sm = self._launcher(tmp_path)
        launcher.launch(name="alive")
        launcher.launch(name="dead")
        dead = sm.get_session_by_name("dead")
        tm.kill_window(dead.tmux_window)

        by_name = {s.name: s for s in launcher.list_sessions()}
        assert by_name["dead"].status == "terminated"
        assert by_name["alive"].status != "terminated"


class TestTmuxManagerListWindowsCheap:
    def test_include_command_false_skips_pane_lookup(self):
        manager = TmuxManager("agents")
        manager._server = MagicMock()
        manager._server.has_session.return_value = True
        mock_session = MagicMock()
        manager._server.sessions.get.return_value = mock_session

        win = MagicMock()
        win.window_index = "3"
        win.window_name = "agent-xyz"
        type(win).panes = PropertyMock(side_effect=AssertionError("list-panes should not run"))
        mock_session.windows = [win]

        assert manager.list_windows(include_command=False) == [
            {"index": 3, "name": "agent-xyz", "command": ""}
        ]


# ── read_git_context_from_disk ───────────────────────────────────────


def _git(*args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"},
    ).stdout.strip()


def _git_says(cwd):
    top = _git("rev-parse", "--show-toplevel", cwd=cwd)
    branch = _git("branch", "--show-current", cwd=cwd)
    return Path(top).name, branch


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "myrepo"
    root.mkdir()
    _git("init", "-q", "-b", "main", cwd=root)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init", cwd=root)
    return root


class TestReadGitContextFromDisk:
    def test_matches_git_on_branch(self, repo):
        assert read_git_context_from_disk(str(repo)) == ("myrepo", "main")
        assert read_git_context_from_disk(str(repo)) == _git_says(repo)

    def test_subdirectory_walks_up(self, repo):
        sub = repo / "src" / "pkg"
        sub.mkdir(parents=True)
        assert read_git_context_from_disk(str(sub)) == ("myrepo", "main")

    def test_branch_with_slashes(self, repo):
        _git("checkout", "-q", "-b", "feat/perf/tmux", cwd=repo)
        assert read_git_context_from_disk(str(repo)) == _git_says(repo) == ("myrepo", "feat/perf/tmux")

    def test_detached_head_is_empty_string_like_git(self, repo):
        _git("checkout", "-q", "--detach", cwd=repo)
        assert read_git_context_from_disk(str(repo)) == _git_says(repo) == ("myrepo", "")

    def test_worktree_gitdir_file(self, repo, tmp_path):
        wt = tmp_path / "wt-feature"
        _git("worktree", "add", "-q", "-b", "feature", str(wt), cwd=repo)
        assert (wt / ".git").is_file()
        assert read_git_context_from_disk(str(wt)) == _git_says(wt) == ("wt-feature", "feature")

    def test_non_repo_returns_none_pair(self, tmp_path):
        plain = tmp_path / "plain"
        plain.mkdir()
        # tmp_path itself is not inside a repo in CI/dev checkouts; guard anyway
        if any((p / ".git").exists() for p in (plain, *plain.parents)):
            pytest.skip("tmp_path unexpectedly inside a git repo")
        assert read_git_context_from_disk(str(plain)) == (None, None)

    def test_unreadable_head_defers_to_git(self, repo):
        (repo / ".git" / "HEAD").write_text("garbage\n")
        assert read_git_context_from_disk(str(repo)) is None

    def test_session_manager_uses_disk_reader(self, repo, tmp_path, monkeypatch):
        """_detect_git_context must not spawn git when HEAD is readable."""

        state = tmp_path / "state"
        manager = SessionManager(state_dir=state)

        def boom(*a, **kw):
            raise AssertionError("git subprocess should not run")

        monkeypatch.setattr(subprocess, "run", boom)
        assert manager._detect_git_context(str(repo)) == ("myrepo", "main")


# ── entrypoint fast path ─────────────────────────────────────────────


class TestEntrypoint:
    def test_hook_handler_bypasses_cli(self, monkeypatch):
        import overcode.entrypoint as ep
        import overcode.hook_handler as hh

        called = []
        monkeypatch.setattr(hh, "handle_hook_event", lambda: called.append("hook"))
        monkeypatch.setattr(sys, "argv", ["overcode", "hook-handler"])

        # If the CLI were imported/run it would try to dispatch "hook-handler"
        # through typer; make that loud.
        import overcode.cli as cli
        monkeypatch.setattr(cli, "main", lambda: called.append("cli"))

        ep.main()
        assert called == ["hook"]

    def test_other_commands_reach_cli(self, monkeypatch):
        import overcode.entrypoint as ep
        import overcode.cli as cli

        called = []
        monkeypatch.setattr(cli, "main", lambda: called.append("cli"))
        monkeypatch.setattr(sys, "argv", ["overcode", "list"])
        ep.main()
        assert called == ["cli"]

    def test_hook_handler_with_extra_args_reaches_cli(self, monkeypatch):
        """Only the bare `overcode hook-handler` form is fast-pathed."""
        import overcode.entrypoint as ep
        import overcode.cli as cli

        called = []
        monkeypatch.setattr(cli, "main", lambda: called.append("cli"))
        monkeypatch.setattr(sys, "argv", ["overcode", "hook-handler", "--help"])
        ep.main()
        assert called == ["cli"]

    def test_console_script_points_at_entrypoint(self):
        text = (Path(__file__).parent.parent.parent / "pyproject.toml").read_text()
        assert 'overcode = "overcode.entrypoint:main"' in text


# ── signature gating of the rotation's picks (audit R11) ─────────────


def _pane(name, index, version):
    from overcode.tmux_utils import PaneInfo

    return PaneInfo(name, index, 1000 + index, 1_790_000_000, version, 0, 0, "claude", 0)


class TestGateWorthAListing:
    def test_pays_only_past_one_non_focused_capture_per_tick(self):
        from overcode.tui_logic import gate_worth_a_listing

        every = NON_FOCUSED_CAPTURE_EVERY
        for n in range(0, every + 1):
            assert not gate_worth_a_listing(n)  # rotation issues <= 1 per tick
        assert gate_worth_a_listing(every + 1)
        assert gate_worth_a_listing(49) and gate_worth_a_listing(200)
        assert gate_worth_a_listing(5, every=4) and not gate_worth_a_listing(4, every=4)


class TestGateCaptureIds:
    def _tracker(self):
        from overcode.pane_capture_gate import PaneChangeTracker

        return PaneChangeTracker()

    def test_no_listing_keeps_every_pick(self):
        from overcode.tui_logic import gate_capture_ids

        picks = {"s0", "s3", "s7"}
        out = gate_capture_ids(picks, "s0", {"s0": "w0", "s3": "w3", "s7": "w7"}, None, self._tracker(), 0.0)
        assert out == picks and out is not picks

    def test_unchanged_non_focused_picks_are_dropped_focused_stays(self):
        from overcode.tui_logic import gate_capture_ids

        windows = {f"s{i}": f"w{i}" for i in range(4)}
        panes = {f"w{i}": _pane(f"w{i}", i, 1) for i in range(4)}
        tracker = self._tracker()
        first = gate_capture_ids({"s0", "s1", "s2"}, "s0", windows, panes, tracker, 0.0)
        assert first == {"s0", "s1", "s2"}  # never captured
        second = gate_capture_ids({"s0", "s1", "s2"}, "s0", windows, panes, tracker, 1.0)
        assert second == {"s0"}
        panes["w2"] = _pane("w2", 2, 2)  # w2 moved
        third = gate_capture_ids({"s0", "s1", "s2"}, "s0", windows, panes, tracker, 2.0)
        assert third == {"s0", "s2"}
        fourth = gate_capture_ids({"s0", "s1", "s2"}, "s0", windows, panes, tracker, 3.0)
        assert fourth == {"s0", "s2"}  # the follow-up capture
        assert gate_capture_ids({"s0", "s1", "s2"}, "s0", windows, panes, tracker, 4.0) == {"s0"}

    def test_a_pick_not_in_the_listing_is_a_gone_window(self):
        from overcode.tui_logic import gate_capture_ids

        tracker = self._tracker()
        windows = {"s1": "w1", "s9": "w9"}
        panes = {"w1": _pane("w1", 1, 1)}
        assert gate_capture_ids({"s1", "s9"}, None, windows, panes, tracker, 0.0) == {"s1", "s9"}
        assert gate_capture_ids({"s1", "s9"}, None, windows, panes, tracker, 1.0) == set()
        panes["w9"] = _pane("w9", 9, 1)  # revived
        assert gate_capture_ids({"s1", "s9"}, None, windows, panes, tracker, 2.0) == {"s9"}

    def test_keepalive_recaptures_an_idle_pick(self):
        from overcode.tui_logic import gate_capture_ids

        tracker = self._tracker()
        windows, panes = {"s1": "w1"}, {"w1": _pane("w1", 1, 1)}
        assert gate_capture_ids({"s1"}, None, windows, panes, tracker, 0.0) == {"s1"}
        assert gate_capture_ids({"s1"}, None, windows, panes, tracker, 4.0) == set()
        assert gate_capture_ids({"s1"}, None, windows, panes, tracker, 5.5) == {"s1"}


class TestFastPathSignatureGating:
    """Drive the real fast-path worker body with a listing per tick.

    Focused: captured every tick, unconditionally. Non-focused: only when the
    listing shows the pane changed since its last capture (in its rotation
    slot); a listing that fails leaves the rotation as it was.
    """

    N = 10

    def _app(self, n=None, listing=True):
        from overcode.pane_capture_gate import PaneChangeTracker
        from overcode.tui import SupervisorTUI

        n = n or self.N
        app = SupervisorTUI.__new__(SupervisorTUI)
        sessions = []
        for i in range(n):
            s = MagicMock()
            s.id = f"s{i}"
            s.is_remote = False
            s.status = "running"
            s.tmux_window = f"w{i}"
            sessions.append(s)
        widgets = [MagicMock(session=s) for s in sessions]
        app.session_manager = MagicMock()
        app.session_manager.list_sessions.return_value = sessions
        app.tmux_session = "agents"
        app._get_focused_widget = lambda: widgets[0]
        app._previous_statuses = {}
        app._pane_content_cache = {}
        app._activity_cache = {}
        app._pane_change_tracker = PaneChangeTracker()
        app._status_tick = 0
        app._prefs = MagicMock(status_change_logging=False)
        app._remote_sessions = []
        app._summaries = {}
        app.detector = MagicMock()
        versions = {f"w{i}": 1 for i in range(n)}
        app._tmux = MagicMock()
        app._tmux.list_panes.side_effect = lambda session: (
            {w: _pane(w, i, v) for i, (w, v) in enumerate(versions.items())} if listing else None
        )

        def detect(session, num_lines=0):
            v = versions[session.tmux_window]
            return ("running", f"act-{session.id}-v{v}", f"pane-{session.id}-v{v}")

        app.detector.detect_status.side_effect = detect
        applied = []
        app.call_from_thread = lambda fn, *a, **kw: applied.append((fn, a, kw))
        return app, widgets, applied, versions

    def _tick(self, app, widgets, applied):
        from overcode.tui import SupervisorTUI

        app.detector.detect_status.reset_mock()
        with patch("overcode.tui.get_monitor_daemon_state", return_value=None):
            SupervisorTUI._fetch_statuses_async.__wrapped__(app, widgets)
        fn, args, _ = applied[-1]
        status_results = args[0]
        for sid, (status, _, _) in status_results.items():
            app._previous_statuses[sid] = status
        captured = {c.args[0].id for c in app.detector.detect_status.call_args_list}
        return status_results, captured

    def test_focused_captured_every_tick_non_focused_only_on_change(self):
        app, widgets, applied, versions = self._app()
        _, captured = self._tick(app, widgets, applied)
        assert captured == {f"s{i}" for i in range(self.N)}  # first sight: everyone
        for _ in range(3 * NON_FOCUSED_CAPTURE_EVERY):
            results, captured = self._tick(app, widgets, applied)
            assert captured == {"s0"}, captured
            for i in range(self.N):  # skipped sessions replay their last capture
                assert results[f"s{i}"] == ("running", f"act-s{i}-v1", f"pane-s{i}-v1")
        assert app._tmux.list_panes.call_count == 1 + 3 * NON_FOCUSED_CAPTURE_EVERY

        versions["w5"] = 2  # s5's pane moves
        seen = []
        for _ in range(NON_FOCUSED_CAPTURE_EVERY):
            results, captured = self._tick(app, widgets, applied)
            assert captured <= {"s0", "s5"}
            seen.append(captured)
            assert results["s5"] == ("running", "act-s5-v2", "pane-s5-v2") or "s5" not in captured
        assert {"s0", "s5"} in seen  # captured within its rotation period
        assert results["s5"] == ("running", "act-s5-v2", "pane-s5-v2")
        # One follow-up capture in its next slot, then quiet again
        follow_ups = 0
        for _ in range(2 * NON_FOCUSED_CAPTURE_EVERY):
            _, captured = self._tick(app, widgets, applied)
            follow_ups += "s5" in captured
        assert follow_ups == 1

    def test_listing_failure_falls_back_to_the_plain_rotation(self):
        app, widgets, applied, _ = self._app(listing=False)
        self._tick(app, widgets, applied)
        every = NON_FOCUSED_CAPTURE_EVERY
        per_tick = []
        for _ in range(every):
            _, captured = self._tick(app, widgets, applied)
            assert "s0" in captured
            per_tick.append(len(captured))
        assert max(per_tick) <= 1 + (self.N - 1 + every - 1) // every
        assert sum(per_tick) == 1 * every + (self.N - 1)  # focused each tick + every non-focused once

    def test_small_fleets_do_not_pay_for_a_listing(self):
        app, widgets, applied, _ = self._app(n=NON_FOCUSED_CAPTURE_EVERY + 1)
        for _ in range(8):
            self._tick(app, widgets, applied)
        app._tmux.list_panes.assert_not_called()

    def test_departed_sessions_are_forgotten(self):
        app, widgets, applied, _ = self._app()
        self._tick(app, widgets, applied)
        assert len(app._pane_change_tracker) == self.N
        self._tick(app, widgets[:4], applied)
        assert len(app._pane_change_tracker) == 4
