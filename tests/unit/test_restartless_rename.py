"""Rename without a restart (auto-naming §1): the agent is its session id.

A renamed agent keeps running with the name it was launched under in
``OVERCODE_SESSION_NAME``. Everything that runs inside it — the hook handler,
the budget check, the enhanced-context line, ``overcode report``, ``overcode
bash``'s agent link, a child launch — finds it by ``OVERCODE_SESSION_ID``.
Each test gives the old name to another agent afterwards, so a lookup by the
env name would land on the wrong record.
"""

import io
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from overcode import hook_handler
from overcode.session_manager import SessionManager


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    """``alpha`` renamed to ``beta``; a new agent then took ``alpha``.

    The process environment is the renamed agent's: launched as ``alpha``.
    """
    monkeypatch.setenv("OVERCODE_DIR", str(tmp_path / "home"))
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "state"))
    sm = SessionManager(skip_git_detection=True)
    renamed = sm.create_session(name="alpha", tmux_session="agents",
                                tmux_window="alpha-xxxx", command=["claude"])
    assert sm.rename_session(renamed.id, "beta", tmux_window=f"beta-{renamed.id[:4]}")
    namesake = sm.create_session(name="alpha", tmux_session="agents",
                                 tmux_window="alpha-yyyy", command=["claude"])
    monkeypatch.setenv("OVERCODE_SESSION_ID", renamed.id)
    monkeypatch.setenv("OVERCODE_SESSION_NAME", "alpha")
    monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
    return SimpleNamespace(sm=sm, renamed=renamed, namesake=namesake, root=tmp_path / "state")


def _hook(monkeypatch, payload):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    hook_handler.handle_hook_event()


def _record(sid):
    return SessionManager(skip_git_detection=True).get_session(sid)


def _daemon_state(fleet, entries):
    path = fleet.root / "agents" / "monitor_daemon_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sessions": entries}))


class TestHookAfterRename:

    def test_a_hook_updates_the_renamed_agents_record(self, fleet, monkeypatch):
        _hook(monkeypatch, {"hook_event_name": "SessionStart", "source": "startup",
                            "session_id": "conv-1"})
        assert _record(fleet.renamed.id).active_agent_session_id == "conv-1"
        assert _record(fleet.namesake.id).active_agent_session_id is None

    def test_the_hook_state_lands_in_the_agents_directory(self, fleet, monkeypatch):
        _hook(monkeypatch, {"hook_event_name": "Stop", "session_id": "conv-1"})
        own = fleet.root / "agents" / "agents" / fleet.renamed.id / "hook_state.json"
        assert json.loads(own.read_text())["event"] == "Stop"

    def test_an_agent_with_no_id_is_found_by_name_following_renames(self, fleet, monkeypatch):
        """Pre-0.6: the env name is all it has; an old name resolves to its holder."""
        legacy = fleet.sm.create_session(name="gamma", tmux_session="agents",
                                         tmux_window="gamma-zzzz", command=["claude"])
        assert fleet.sm.rename_session(legacy.id, "delta")
        monkeypatch.delenv("OVERCODE_SESSION_ID")
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "gamma")
        _hook(monkeypatch, {"hook_event_name": "SessionStart", "source": "startup",
                            "session_id": "conv-9"})
        assert _record(legacy.id).active_agent_session_id == "conv-9"

    def test_an_id_with_no_record_is_no_agent(self, fleet, monkeypatch):
        """Not the agent now holding the launch name."""
        monkeypatch.setenv("OVERCODE_SESSION_ID", "not-registered")
        _hook(monkeypatch, {"hook_event_name": "SessionStart", "source": "startup",
                            "session_id": "conv-1"})
        assert _record(fleet.namesake.id).active_agent_session_id is None


class TestBudgetAfterRename:

    def test_the_budget_of_the_renamed_agent_blocks_its_prompt(self, fleet, monkeypatch, capsys):
        _daemon_state(fleet, [
            {"session_id": fleet.namesake.id, "name": "alpha", "budget_exceeded": False},
            {"session_id": fleet.renamed.id, "name": "beta", "budget_exceeded": True,
             "cost_budget_usd": 1.0, "estimated_cost_usd": 2.0},
        ])
        with pytest.raises(SystemExit) as exc:
            _hook(monkeypatch, {"hook_event_name": "UserPromptSubmit", "session_id": "c"})
        assert exc.value.code == 2
        assert "Budget exceeded" in capsys.readouterr().err

    def test_another_agents_budget_under_the_old_name_does_not(self, fleet, monkeypatch):
        _daemon_state(fleet, [
            {"session_id": fleet.namesake.id, "name": "alpha", "budget_exceeded": True,
             "cost_budget_usd": 1.0, "estimated_cost_usd": 2.0},
            {"session_id": fleet.renamed.id, "name": "beta", "budget_exceeded": False},
        ])
        with patch("overcode.time_context.generate_enhanced_context", return_value=""):
            _hook(monkeypatch, {"hook_event_name": "UserPromptSubmit", "session_id": "c"})


class TestEnhancedContextAfterRename:

    def test_shows_the_current_name(self, fleet):
        from overcode.time_context import generate_enhanced_context

        _daemon_state(fleet, [
            {"session_id": fleet.namesake.id, "name": "alpha", "enhanced_context_enabled": False},
            {"session_id": fleet.renamed.id, "name": "alpha", "enhanced_context_enabled": True},
        ])
        line = generate_enhanced_context("agents", "alpha", config={})
        assert line.startswith("Agent: beta |")

    def test_the_hook_prints_the_new_name_and_the_notice(self, fleet, monkeypatch, capsys):
        from overcode.paths import agent_file

        _daemon_state(fleet, [
            {"session_id": fleet.renamed.id, "name": "beta", "enhanced_context_enabled": True},
        ])
        notice = agent_file("agents", fleet.renamed.id, "rename_notice")
        notice.parent.mkdir(parents=True, exist_ok=True)
        notice.write_text("[overcode] This agent was renamed from 'alpha' to 'beta'.\n")
        _hook(monkeypatch, {"hook_event_name": "UserPromptSubmit", "session_id": "c"})
        out = capsys.readouterr().out
        assert "Agent: beta |" in out
        assert "renamed from 'alpha' to 'beta'" in out
        assert not notice.exists()


class TestPaneFallback:

    @staticmethod
    def _tmux_says(monkeypatch, stdout):
        monkeypatch.setenv("TMUX_PANE", "%7")
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(hook_handler.subprocess, "run", run)
        return calls

    def test_reads_the_window_option(self, monkeypatch):
        calls = self._tmux_says(monkeypatch, "oc-view-agents\tsid-1\tbeta-abcd\n")
        assert hook_handler._detect_from_tmux_pane() == ("beta", "agents", "sid-1")
        assert len(calls) == 1 and "#{@overcode_id}" in calls[0][-1]

    def test_a_window_without_the_option_falls_back_to_its_name(self, monkeypatch):
        self._tmux_says(monkeypatch, "agents\t\tmy-agent-abcd\n")
        assert hook_handler._detect_from_tmux_pane() == ("my-agent", "agents", None)

    def test_nothing_without_a_pane(self, monkeypatch):
        monkeypatch.delenv("TMUX_PANE", raising=False)
        assert hook_handler._detect_from_tmux_pane() == (None, None, None)


class TestCliFromInsideTheAgent:

    def test_report_marks_the_renamed_agent(self, fleet):
        from typer.testing import CliRunner

        from overcode.cli import app

        result = CliRunner().invoke(app, ["report", "--status", "success", "--reason", "ok"])
        assert result.exit_code == 0, result.output
        assert _record(fleet.renamed.id).report_status == "success"
        assert _record(fleet.namesake.id).report_status is None

    def test_a_job_links_to_the_renamed_agent(self, fleet):
        from typer.testing import CliRunner

        from overcode.cli import app

        with patch("overcode.job_launcher.JobLauncher") as launcher_cls:
            launcher_cls.return_value.launch.return_value = SimpleNamespace(
                name="j", command="true", tmux_session="jobs", tmux_window="j")
            result = CliRunner().invoke(app, ["bash", "true", "--no-follow"])
        assert result.exit_code == 0, result.output
        kwargs = launcher_cls.return_value.launch.call_args.kwargs
        assert (kwargs["agent_session_id"], kwargs["agent_name"]) == (fleet.renamed.id, "beta")

    def test_a_child_is_parented_to_the_renamed_agent(self, fleet, monkeypatch):
        from overcode.launcher import AgentLauncher
        from overcode.tmux_manager import TmuxManager
        from tests.tmux_doubles import MockTmux

        monkeypatch.setattr("overcode.launcher.require_tmux", lambda: "tmux")
        monkeypatch.setattr("overcode.launcher.require_agent_cli", lambda backend: "claude")
        launcher = AgentLauncher("agents", TmuxManager("agents", tmux=MockTmux()), fleet.sm)
        child = launcher.launch(name="kid")
        assert child.parent_session_id == fleet.renamed.id


class TestInAgent:

    @pytest.mark.parametrize("env, expected", [
        ({}, False),
        ({"OVERCODE_SESSION_ID": "sid"}, True),
        ({"OVERCODE_SESSION_NAME": "alpha"}, True),
    ])
    def test_either_variable_marks_an_agent(self, env, expected):
        from overcode.agent_identity import in_agent

        assert in_agent(env) is expected

    def test_activity_log_counts_an_agents_call(self, monkeypatch):
        from overcode import activity_log

        monkeypatch.setenv("OVERCODE_SESSION_ID", "sid")
        monkeypatch.setattr(activity_log, "recording_enabled", lambda: True)
        with patch.object(activity_log, "count_agent_cli") as counted, \
                patch.object(activity_log, "ActivityRecorder") as recorder:
            activity_log.record_cli_invocation(["list"], commands={"list"})
        counted.assert_called_once_with("list")
        recorder.assert_not_called()


class TestDaemonTagsWindows:

    def test_live_agent_windows_get_their_id_once(self, tmp_path):
        from overcode.monitor_daemon import MonitorDaemon
        from overcode.tmux_utils import AGENT_ID_OPTION
        from tests.tmux_doubles import MockTmux

        tmux = MockTmux()
        tmux.new_session("agents")
        tmux.new_window("agents", "live-abcd")
        daemon = MonitorDaemon.for_test(tmp_path, tmux=tmux)
        live = SimpleNamespace(id="sid-live", tmux_window="live-abcd")
        gone = SimpleNamespace(id="sid-gone", tmux_window="gone-abcd")

        daemon._tag_agent_windows([live, gone])

        assert tmux.window_options == {("agents", "live-abcd"): {AGENT_ID_OPTION: "sid-live"}}
