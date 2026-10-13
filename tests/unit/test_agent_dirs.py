"""Each agent's files in its own directory, agents/<session id>/ (item 35).

A name is reused all the time (every run called "shirka"); a file keyed by
it outlived its agent and was read as the next one's: a dead agent's Stop
and report settled a new child done at once. Keyed by session id, and with
the directory made at every launch, a new agent starts clean, and a
relaunch of the same agent (restart, revive) starts its hook state clean.
"""

import json
import os
from unittest.mock import patch

import pytest

from overcode.agent_files import PREVIOUS_REPORT, prepare_agent_dir, remove_agent_dir
from overcode.child_lifecycle import hook_stopped, read_report, settled_child_status
from overcode.launcher import AgentLauncher
from overcode.paths import agent_dir, agent_file, legacy_agent_file
from overcode.session_manager import SessionManager
from overcode.status_constants import STATUS_WAITING_OVERSIGHT
from overcode.tmux_manager import TmuxManager
from tests.tmux_doubles import MockTmux


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith("OVERCODE_") and key not in ("OVERCODE_DIR", "OVERCODE_TMUX_SOCKET"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "state"))
    with patch("overcode.launcher.require_tmux"), \
         patch("overcode.launcher.require_agent_cli"), \
         patch("overcode.launcher.time.sleep"):
        yield


@pytest.fixture
def launcher(tmp_path):
    sm = SessionManager(state_dir=tmp_path / "sessions", skip_git_detection=True)
    return AgentLauncher("agents", TmuxManager("agents", tmux=MockTmux()), sm)


def _stop_and_report(session):
    agent_file("agents", session.id, "hook_state").write_text(
        json.dumps({"event": "Stop", "timestamp": 1.0}))
    agent_file("agents", session.id, "report").write_text(
        json.dumps({"status": "success", "reason": "ok"}))


def _child(launcher, parent, name):
    with patch.dict(os.environ, {"OVERCODE_SESSION_NAME": parent.name}):
        return launcher.launch(name=name)


class TestLaunchMakesTheDirectory:
    def test_every_launch_makes_the_agents_directory(self, launcher):
        session = launcher.launch(name="solo")
        assert agent_dir("agents", session.id).is_dir()

    def test_a_failed_launch_leaves_no_directory(self, launcher):
        launcher.tmux.send_keys = lambda *a, **kw: False
        from overcode.exceptions import LaunchError
        with pytest.raises(LaunchError):
            launcher.launch(name="doomed")
        assert not any((agent_dir("agents", "x").parent).iterdir())


class TestReusedName:
    def test_a_reused_name_does_not_inherit_the_old_report_or_stop(self, launcher):
        parent = launcher.launch(name="parent")
        first = _child(launcher, parent, "kid")
        _stop_and_report(first)
        assert settled_child_status(launcher.sessions.get_session(first.id)) == "done"

        launcher.tmux.kill_window(first.tmux_window)
        launcher.sessions.delete_session(first.id)
        second = _child(launcher, parent, "kid")

        assert second.id != first.id
        assert not hook_stopped("agents", "kid", second.id)
        assert read_report("agents", "kid", second.id) is None
        assert settled_child_status(launcher.sessions.get_session(second.id)) is None

    def test_name_keyed_files_an_older_overcode_left_are_not_read(self, launcher):
        parent = launcher.launch(name="parent")
        legacy_state = legacy_agent_file("agents", "kid", "hook_state")
        legacy_state.parent.mkdir(parents=True, exist_ok=True)
        legacy_state.write_text(json.dumps({"event": "Stop", "timestamp": 1.0}))
        legacy_agent_file("agents", "kid", "report").write_text(json.dumps({"status": "success"}))

        kid = _child(launcher, parent, "kid")
        assert settled_child_status(launcher.sessions.get_session(kid.id)) is None

    def test_an_agent_launched_before_still_reads_its_name_keyed_files(self, tmp_path):
        """No directory: its hooks still write by name until it is relaunched."""
        legacy = legacy_agent_file("agents", "old", "report")
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(json.dumps({"status": "failure"}))
        assert read_report("agents", "old", "sid-from-before") == {"status": "failure"}


class TestRevive:
    def test_a_revived_waiting_oversight_child_does_not_settle_from_the_old_stop(self, launcher):
        parent = launcher.launch(name="parent")
        kid = _child(launcher, parent, "kid")
        agent_file("agents", kid.id, "hook_state").write_text(
            json.dumps({"event": "Stop", "timestamp": 1.0}))
        assert settled_child_status(launcher.sessions.get_session(kid.id)) == STATUS_WAITING_OVERSIGHT
        launcher.sessions.update_session_status(kid.id, STATUS_WAITING_OVERSIGHT)
        launcher.tmux.kill_window(kid.tmux_window)

        assert launcher.revive(launcher.sessions.get_session(kid.id)) is True

        revived = launcher.sessions.get_session(kid.id)
        assert revived.status == "running"
        assert not hook_stopped("agents", "kid", kid.id)
        assert settled_child_status(revived) is None

    def test_a_relaunch_moves_the_old_report_aside(self, launcher):
        parent = launcher.launch(name="parent")
        kid = _child(launcher, parent, "kid")
        _stop_and_report(kid)

        assert launcher.restart(launcher.sessions.get_session(kid.id), graceful_exit_wait=0)

        assert read_report("agents", "kid", kid.id) is None
        kept = json.loads((agent_dir("agents", kid.id) / PREVIOUS_REPORT).read_text())
        assert kept["status"] == "success"

    def test_a_rename_relaunch_keeps_the_state(self, launcher):
        session = launcher.launch(name="alpha")
        launcher._live_status = lambda s: "waiting_user"
        agent_file("agents", session.id, "hook_state").write_text(
            json.dumps({"event": "Stop", "timestamp": 1.0}))
        assert launcher.rename(session, "beta", graceful_exit_wait=0)
        assert hook_stopped("agents", "beta", session.id)


class TestAgentFiles:
    def test_prepare_and_remove(self, tmp_path):
        prepare_agent_dir("agents", "sid-1")
        assert agent_dir("agents", "sid-1").is_dir()
        agent_file("agents", "sid-1", "hook_state").write_text("{}")
        prepare_agent_dir("agents", "sid-1")  # a launch, not a reset: kept
        assert agent_file("agents", "sid-1", "hook_state").exists()
        prepare_agent_dir("agents", "sid-1", reset=True)
        assert not agent_file("agents", "sid-1", "hook_state").exists()
        remove_agent_dir("agents", "sid-1")
        assert not agent_dir("agents", "sid-1").exists()
        remove_agent_dir("agents", "sid-1")  # gone already: fine
