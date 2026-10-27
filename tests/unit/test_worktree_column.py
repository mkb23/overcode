"""Linked git worktrees in the repo cell (#544).

Detection runs in the git sync (``git_context``) on synthetic repos in
tmp_path; the cell, its plain text, width and hover are rendered from
the stored ``worktree`` dict.
"""

import os
import subprocess
from pathlib import Path

import pytest

from overcode.git_context import (
    detect_git_layout,
    read_git_context_from_disk,
    read_git_layout_from_disk,
)
from overcode.session_manager import Session, SessionManager
from overcode.summary_columns import (
    WORKTREE_MARK,
    build_cli_context,
    names_match_repos,
    render_repo_name_plain,
    repo_cell_parts,
    repo_cell_width,
)
from overcode.columns.hover import hover_repo_name
from overcode.columns.render_git import render_repo_name

pytestmark = pytest.mark.unit

_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          check=True, env=_ENV).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "myrepo"
    root.mkdir()
    _git("init", "-q", "-b", "main", cwd=root)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty",
         "-m", "init", cwd=root)
    return root


def _add_worktree(repo, path, branch):
    _git("worktree", "add", "-q", "-b", branch, str(path), cwd=repo)
    return path


def _git_says_worktree(cwd) -> bool:
    git_dir = _git("rev-parse", "--path-format=absolute", "--git-dir", cwd=cwd)
    common = _git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=cwd)
    return Path(git_dir).resolve() != Path(common).resolve()


# ── detection ───────────────────────────────────────────────────────


class TestDetection:
    def test_main_checkout_has_no_worktree(self, repo):
        assert read_git_layout_from_disk(str(repo)) == ("myrepo", "main", None)
        assert not _git_says_worktree(repo)

    def test_linked_worktree(self, repo, tmp_path):
        wt = _add_worktree(repo, tmp_path / "myrepo-wt" / "billing-ux", "billing-ux")
        repo_name, branch, worktree = read_git_layout_from_disk(str(wt))
        assert _git_says_worktree(wt)
        assert (repo_name, branch) == ("billing-ux", "billing-ux")
        assert worktree == {"name": "billing-ux", "path": str(wt.resolve()),
                            "main": str(repo.resolve()), "repo": "myrepo"}
        # The two-value reader is unchanged
        assert read_git_context_from_disk(str(wt)) == ("billing-ux", "billing-ux")

    def test_subdirectory_of_a_worktree(self, repo, tmp_path):
        wt = _add_worktree(repo, tmp_path / ".claude" / "worktrees" / "agent-x",
                           "worktree-agent-x")
        sub = wt / "src" / "pkg"
        sub.mkdir(parents=True)
        _, branch, worktree = read_git_layout_from_disk(str(sub))
        assert branch == "worktree-agent-x"
        assert worktree["name"] == "agent-x"
        assert worktree["path"] == str(wt.resolve())

    def test_submodule_style_gitdir_file_is_not_a_worktree(self, repo, tmp_path):
        """A .git file whose gitdir has no commondir (a submodule) is its own repo."""
        gitdir = tmp_path / "modules" / "sub"
        gitdir.mkdir(parents=True)
        (gitdir / "HEAD").write_text("ref: refs/heads/main\n")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / ".git").write_text(f"gitdir: {gitdir}\n")
        assert read_git_layout_from_disk(str(sub)) == ("sub", "main", None)

    def test_git_fallback_agrees_with_the_disk_reader(self, repo, tmp_path, monkeypatch):
        wt = _add_worktree(repo, tmp_path / "wt-feature", "feature")
        on_disk = detect_git_layout(str(wt))
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
        monkeypatch.setattr("overcode.git_context.read_git_layout_from_disk", lambda d: None)
        assert detect_git_layout(str(wt)) == on_disk
        assert detect_git_layout(str(repo)) == ("myrepo", "main", None)

    def test_no_subprocess_when_disk_is_readable(self, repo, tmp_path, monkeypatch):
        wt = _add_worktree(repo, tmp_path / "wt", "feature")

        def boom(*a, **kw):
            raise AssertionError("git subprocess should not run")

        monkeypatch.setattr(subprocess, "run", boom)
        assert detect_git_layout(str(wt))[2]["name"] == "wt"


class TestSessionSync:
    def test_create_and_refresh_store_the_worktree(self, repo, tmp_path):
        wt = _add_worktree(repo, tmp_path / "wt-a", "feature-a")
        manager = SessionManager(state_dir=tmp_path / "state")
        s = manager.create_session(name="a", tmux_session="agents", tmux_window=1,
                                   command=["claude"], start_directory=str(wt))
        assert manager.get_session(s.id).worktree["name"] == "wt-a"
        assert manager.refresh_git_context(s.id) is False  # nothing moved

        # The agent's directory turns out to be the main checkout
        manager.update_session(s.id, start_directory=str(repo))
        assert manager.refresh_git_context(s.id) is True
        reloaded = manager.get_session(s.id)
        assert (reloaded.repo_name, reloaded.worktree) == ("myrepo", None)

    def test_daemon_stages_a_worktree_change(self, repo, tmp_path):
        from overcode.monitor_daemon import MonitorDaemon
        wt = _add_worktree(repo, tmp_path / "wt-b", "feature-b")
        manager = SessionManager(state_dir=tmp_path / "state", skip_git_detection=True)
        s = manager.create_session(name="b", tmux_session="agents", tmux_window=1,
                                   command=["claude"], start_directory=str(wt))
        daemon = MonitorDaemon.for_test(tmp_path / "state", session_manager=manager)
        assert daemon._refresh_git_context(manager.get_session(s.id)) is True
        staged = daemon._pending.view(manager.get_session(s.id))
        assert staged.worktree["name"] == "wt-b"
        assert staged.worktree["repo"] == "myrepo"
        assert daemon._refresh_git_context(staged) is False


# ── the repo cell ───────────────────────────────────────────────────


def _session(name="agent", repo="myrepo", branch="main", worktree=None):
    return Session(id=f"id-{name}", name=name, tmux_session="t", tmux_window="w",
                   command=["claude"], start_directory="/tmp", start_time="2026-10-27T10:00:00",
                   repo_name=repo, branch=branch, worktree=worktree)


def _wt(name, repo="overcode"):
    return {"name": name, "path": f"/src/{repo}-wt/{name}", "main": f"/src/{repo}",
            "repo": repo}


class TestRepoCell:
    def test_main_checkout(self):
        assert repo_cell_parts(_session()) == ("myrepo", "")
        assert repo_cell_parts(_session(repo=None), "n/a") == ("n/a", "")

    def test_worktree_named_differently_from_its_branch(self):
        s = _session(repo="agent-x", branch="fix/thing", worktree=_wt("agent-x"))
        assert repo_cell_parts(s) == ("overcode", f"{WORKTREE_MARK}agent-x")
        assert repo_cell_width(s) == len("overcode⎇agent-x")

    @pytest.mark.parametrize("branch", ["billing-ux", "worktree-billing-ux", "mike/billing-ux"])
    def test_the_branch_already_says_the_name(self, branch):
        s = _session(repo="billing-ux", branch=branch, worktree=_wt("billing-ux"))
        assert repo_cell_parts(s) == ("overcode", WORKTREE_MARK)

    def test_name_inside_a_longer_branch_word_still_shows(self):
        s = _session(repo="ux", branch="linux", worktree=_wt("ux"))
        assert repo_cell_parts(s) == ("overcode", f"{WORKTREE_MARK}ux")

    def test_long_names_are_cut(self):
        s = _session(repo="x", branch="main", worktree=_wt("agent-a6e0f4b6ad7943db8"))
        assert repo_cell_parts(s)[1] == f"{WORKTREE_MARK}agent-a6e0f4b6a…"

    def test_detached_head_shows_the_name(self):
        s = _session(repo="wt", branch="", worktree=_wt("wt"))
        assert repo_cell_parts(s)[1] == f"{WORKTREE_MARK}wt"

    def test_names_match_repos_ignores_worktree_agents(self):
        plain = _session(name="myrepo", repo="myrepo")
        assert names_match_repos([plain])
        named_after_wt = _session(name="wt", repo="wt", worktree=_wt("wt"))
        assert not names_match_repos([plain, named_after_wt])

    def test_cli_context_render_plain_and_padding(self):
        s = _session(repo="agent-x", branch="fix/thing", worktree=_wt("agent-x"))
        ctx = build_cli_context(s, s.stats, None, None, "running", 0, 0, max_repo_width=20)
        assert ctx.repo_name == "overcode"
        assert ctx.worktree_label == "⎇agent-x"
        assert render_repo_name_plain(ctx) == "overcode⎇agent-x"
        segments = render_repo_name(ctx)
        assert "".join(t for t, _ in segments) == " " + "overcode⎇agent-x".ljust(20)
        # Same shape as a main checkout's cell at the same width
        plain = _session()
        pctx = build_cli_context(plain, plain.stats, None, None, "running", 0, 0,
                                 max_repo_width=20)
        assert len("".join(t for t, _ in render_repo_name(pctx))) == 21

    def test_hover_names_both_paths(self):
        s = _session(repo="agent-x", branch="fix/thing", worktree=_wt("agent-x"))
        ctx = build_cli_context(s, s.stats, None, None, "running", 0, 0)
        text = hover_repo_name(ctx).plain
        assert "Worktree agent-x" in text
        assert "/src/overcode-wt/agent-x" in text
        assert "main checkout: /src/overcode" in text
        plain = _session()
        assert hover_repo_name(build_cli_context(plain, plain.stats, None, None,
                                                 "running", 0, 0)) is None

    def test_old_sessions_json_without_the_field_loads(self):
        data = {k: v for k, v in _session().to_dict().items() if k != "worktree"} \
            if hasattr(Session, "to_dict") else None
        if data is None:
            pytest.skip("Session has no to_dict")
        assert Session.from_dict(data).worktree is None
