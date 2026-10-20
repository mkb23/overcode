"""A fresh agent in a git worktree of its own (the TUI's ``g`` key).

The worktree half runs real git on a throwaway repository under tmp_path;
the launch half uses the MockTmux launcher the fork tests use.
"""

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from overcode.agent_worktree import (
    WorktreeError,
    create_agent_worktree,
    remove_agent_worktree,
)
from overcode.exceptions import LaunchError
from overcode.launcher import AgentLauncher
from overcode.session_manager import SessionManager
from overcode.tmux_manager import TmuxManager
from overcode.tui import SupervisorTUI
from tests.tmux_doubles import MockTmux


def git(cwd, *args) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path) -> Path:
    """``tmp_path/proj``: one commit on main, with a ``pkg`` subdirectory."""
    root = tmp_path / "proj"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "a.txt").write_text("a\n")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "first")
    return root


def branches(repo) -> set:
    return set(git(repo, "branch", "--format=%(refname:short)").split())


class TestCreateAgentWorktree:
    def test_a_new_branch_from_the_source_commit_beside_the_main_checkout(self, repo):
        wt = create_agent_worktree(str(repo), "proj-wt")

        assert Path(wt.path) == repo.parent / "proj-wt" / "proj-wt"
        assert wt.branch == "proj-wt"
        assert wt.start_directory == wt.path
        assert git(wt.path, "rev-parse", "HEAD") == git(repo, "rev-parse", "HEAD") == wt.base
        assert git(wt.path, "branch", "--show-current") == "proj-wt"
        # The source checkout is left on its own branch
        assert git(repo, "branch", "--show-current") == "main"

    def test_it_starts_from_the_source_s_commit_not_main(self, repo):
        git(repo, "switch", "-q", "-c", "feature")
        (repo / "f.txt").write_text("f\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "feature work")

        wt = create_agent_worktree(str(repo), "sib")
        assert (Path(wt.path) / "f.txt").exists()

    def test_uncommitted_changes_stay_behind(self, repo):
        (repo / "dirty.txt").write_text("x\n")
        wt = create_agent_worktree(str(repo), "sib")
        assert not (Path(wt.path) / "dirty.txt").exists()

    def test_a_subdirectory_start_keeps_its_place(self, repo):
        wt = create_agent_worktree(str(repo / "pkg"), "sib")
        assert Path(wt.start_directory) == Path(wt.path) / "pkg"

    def test_from_inside_a_worktree_it_goes_beside_the_main_checkout(self, repo):
        first = create_agent_worktree(str(repo), "one")
        second = create_agent_worktree(first.path, "two")
        assert Path(second.path) == repo.parent / "proj-wt" / "two"
        assert Path(second.repository).resolve() == repo.resolve()

    def test_a_taken_branch_or_directory_gets_a_suffix(self, repo):
        git(repo, "branch", "sib")
        (repo.parent / "proj-wt" / "sib-2").mkdir(parents=True)

        wt = create_agent_worktree(str(repo), "sib")
        assert wt.branch == "sib-3"
        assert Path(wt.path).name == "sib-3"

    def test_not_a_repository(self, tmp_path):
        plain = tmp_path / "plain"
        plain.mkdir()
        with pytest.raises(WorktreeError, match="not in a git repository"):
            create_agent_worktree(str(plain), "sib")

    def test_a_missing_directory(self, tmp_path):
        with pytest.raises(WorktreeError, match="does not exist"):
            create_agent_worktree(str(tmp_path / "gone"), "sib")

    def test_a_repository_with_no_commits(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        git(empty, "init", "-q")
        with pytest.raises(WorktreeError, match="no commits"):
            create_agent_worktree(str(empty), "sib")

    def test_remove_undoes_it(self, repo):
        wt = create_agent_worktree(str(repo), "sib")
        remove_agent_worktree(wt)
        assert not Path(wt.path).exists()
        assert "sib" not in branches(repo)
        assert len(git(repo, "worktree", "list").splitlines()) == 1


@pytest.fixture
def fleet(tmp_path):
    mock_tmux = MockTmux()
    sessions = SessionManager(state_dir=tmp_path / "state", skip_git_detection=True)
    launcher = AgentLauncher("agents", TmuxManager("agents", tmux=mock_tmux), sessions)
    return SimpleNamespace(launcher=launcher, sessions=sessions, tmux=mock_tmux)


class TestLaunchInWorktree:
    def test_a_fresh_peer_in_the_worktree(self, fleet, repo):
        source = fleet.launcher.launch(name="src", start_directory=str(repo / "pkg"))
        fleet.sessions.set_active_agent_session_id(source.id, "claude-session-abc")
        source = fleet.sessions.get_session(source.id)

        session, wt = fleet.launcher.launch_in_worktree("src-wt", source)

        assert session.name == "src-wt"
        assert session.start_directory == str(Path(wt.path) / "pkg")
        assert session.parent_session_id is None
        # A clear context: nothing resumed, nothing forked
        cmd = [k[2] for k in fleet.tmux.sent_keys if "claude" in k[2]][-1]
        assert "--resume" not in cmd and "--fork-session" not in cmd
        assert "claude-session-abc" not in cmd

    def test_it_runs_the_way_the_source_does(self, fleet, repo):
        source = fleet.launcher.launch(
            name="src", start_directory=str(repo), dangerously_skip_permissions=True,
            model="sonnet", allowed_tools="Read,Bash",
        )
        session, _ = fleet.launcher.launch_in_worktree("sib", source)
        assert session.permissiveness_mode == "bypass"
        assert session.model == "sonnet"
        assert session.allowed_tools == "Read,Bash"
        assert session.backend == source.backend

    def test_a_live_agent_of_that_name_makes_no_worktree(self, fleet, repo):
        source = fleet.launcher.launch(name="src", start_directory=str(repo))
        with pytest.raises(LaunchError, match="already exists"):
            fleet.launcher.launch_in_worktree("src", source)
        assert branches(repo) == {"main"}

    def test_an_invalid_name_makes_no_worktree(self, fleet, repo):
        source = fleet.launcher.launch(name="src", start_directory=str(repo))
        with pytest.raises(LaunchError):
            fleet.launcher.launch_in_worktree("has space", source)
        assert branches(repo) == {"main"}

    def test_not_a_repository_is_a_launch_error(self, fleet, tmp_path):
        source = fleet.launcher.launch(name="src", start_directory=str(tmp_path))
        with pytest.raises(LaunchError, match="not in a git repository"):
            fleet.launcher.launch_in_worktree("sib", source)

    def test_a_failed_launch_removes_the_worktree(self, fleet, repo):
        source = fleet.launcher.launch(name="src", start_directory=str(repo))
        with patch.object(AgentLauncher, "launch", side_effect=LaunchError("boom")):
            with pytest.raises(LaunchError, match="boom"):
                fleet.launcher.launch_in_worktree("sib", source)
        assert branches(repo) == {"main"}
        assert not (repo.parent / "proj-wt" / "sib").exists()


def _app():
    def run_worker(fn, **_):
        fn()
    return SimpleNamespace(
        tmux_session="agents",
        session_manager=MagicMock(),
        notify=MagicMock(),
        refresh_sessions=MagicMock(),
        call_from_thread=lambda fn, *a, **kw: fn(*a, **kw),
        run_worker=run_worker,
    )


def _notes(app):
    return [(c.args[0], c.kwargs.get("severity")) for c in app.notify.call_args_list]


class TestTuiHandler:
    def test_it_says_where_the_agent_went(self):
        app = _app()
        message = SimpleNamespace(source_session=SimpleNamespace(name="src"), name="sib")
        wt = SimpleNamespace(branch="sib", path="/r/proj-wt/sib")
        with patch("overcode.tui_actions.command_bar.AgentLauncher") as cls:
            cls.return_value.launch_in_worktree.return_value = (MagicMock(), wt)
            SupervisorTUI.on_command_bar_worktree_requested(app, message)

        assert _notes(app) == [
            ("Making a worktree for 'sib'…", "information"),
            ("Created 'sib' on branch sib in /r/proj-wt/sib", "information"),
        ]
        app.refresh_sessions.assert_called_once()

    def test_a_refusal_is_an_error_with_its_reason(self):
        app = _app()
        message = SimpleNamespace(source_session=SimpleNamespace(name="src"), name="sib")
        with patch("overcode.tui_actions.command_bar.AgentLauncher") as cls:
            cls.return_value.launch_in_worktree.side_effect = LaunchError(
                "'/tmp' is not in a git repository")
            SupervisorTUI.on_command_bar_worktree_requested(app, message)

        assert _notes(app)[-1] == ("Cannot launch: '/tmp' is not in a git repository", "error")
        app.refresh_sessions.assert_not_called()


class TestTuiAction:
    def test_a_remote_agent_is_refused(self):
        app = SimpleNamespace(
            notify=MagicMock(),
            _is_remote=lambda s: True,
            query_one=MagicMock(),
        )
        session = SimpleNamespace(name="far", source_host="box", start_directory="/x")
        with patch("overcode.tui_actions.session._get_focused_session",
                   return_value=SimpleNamespace(session=session)):
            SupervisorTUI.action_new_agent_in_worktree(app)
        assert _notes(app) == [("'far' runs on box: make its worktree there", "warning")]
        app.query_one.assert_not_called()
