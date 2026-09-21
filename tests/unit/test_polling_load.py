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
from unittest.mock import MagicMock, PropertyMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.tui_logic import (  # noqa: E402
    NON_FOCUSED_CAPTURE_EVERY,
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
        known = set(self.IDS)
        for tick in range(20):
            chosen = select_capture_sessions(self.IDS, "s7", tick, known)
            assert "s7" in chosen

    def test_sessions_unknown_to_daemon_captured_every_tick(self):
        known = set(self.IDS) - {"s3"}
        for tick in range(20):
            chosen = select_capture_sessions(self.IDS, "s0", tick, known)
            assert "s3" in chosen

    def test_no_daemon_state_captures_everything(self):
        """Stale/absent daemon → the TUI is the only status source → no skipping."""
        for tick in range(8):
            assert select_capture_sessions(self.IDS, "s0", tick, set()) == set(self.IDS)

    def test_non_focused_rotate_once_per_window(self):
        """Over `every` consecutive ticks each non-focused session is captured exactly once."""
        known = set(self.IDS)
        every = NON_FOCUSED_CAPTURE_EVERY
        counts = {sid: 0 for sid in self.IDS}
        for tick in range(100, 100 + every):
            for sid in select_capture_sessions(self.IDS, "s0", tick, known, every=every):
                counts[sid] += 1
        assert counts["s0"] == every  # focused
        for sid in self.IDS[1:]:
            assert counts[sid] == 1, sid

    def test_per_tick_command_count_is_bounded(self):
        known = set(self.IDS)
        every = NON_FOCUSED_CAPTURE_EVERY
        for tick in range(every):
            chosen = select_capture_sessions(self.IDS, "s0", tick, known, every=every)
            # focused + at most ceil(N/every) rotating
            assert len(chosen) <= 1 + (len(self.IDS) + every - 1) // every

    def test_every_one_means_no_skipping(self):
        known = set(self.IDS)
        assert select_capture_sessions(self.IDS, None, 5, known, every=1) == set(self.IDS)

    def test_zero_every_is_clamped(self):
        known = set(self.IDS)
        assert select_capture_sessions(self.IDS, None, 5, known, every=0) == set(self.IDS)


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
