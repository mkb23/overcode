"""Tests for overcode.doctor — hook-health inspection."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional

import pytest

from overcode.doctor import (
    FINDING_BUDGET_EXCEEDED,
    FINDING_CONTEXT_ZERO,
    FINDING_COST_ZERO,
    FINDING_DAEMON_DOWN,
    FINDING_HEARTBEAT_OVERDUE,
    FINDING_MODEL_DRIFT,
    FINDING_OVERSIGHT_OVERDUE,
    FINDING_SID_ORPHAN,
    FINDING_SIDS_EMPTY,
    FINDING_SLEEP_BUT_ACTIVE,
    FINDING_STALE_ACTIVITY,
    FINDING_TOKENS_ZERO,
    Finding,
    SEVERITY_ERROR,
    VERDICT_MISSING_SETTINGS,
    VERDICT_NO_CLAUDE,
    VERDICT_OK,
    VERDICT_REMOTE,
    VERDICT_WINDOW_GONE,
    _build_child_index,
    find_claude_process,
    find_agent_process,
    gather_data_findings,
    get_descendant_pids,
    inspect_agent,
)
from overcode.session_manager import Session, SessionStats


def _make_session(**overrides) -> Session:
    defaults = dict(
        id="sid-1",
        name="agent",
        tmux_session="agents",
        tmux_window="agent-1234",
        command=["claude"],
        start_directory="/tmp",
        start_time="2026-04-20T12:00:00",
        stats=SessionStats(),
    )
    defaults.update(overrides)
    return Session(**defaults)


@dataclass
class _FakeStats:
    """Stand-in for history_reader.ClaudeSessionStats in data-finding tests.

    We duck-type rather than constructing the real thing to keep these tests
    independent of history_reader's import chain.
    """
    interaction_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    current_context_tokens: int = 0
    model: Optional[str] = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def _codes(findings: List[Finding]) -> List[str]:
    return [f.code for f in findings]


class TestBuildChildIndex:
    def test_groups_by_ppid(self):
        rows = [(100, 1, "shell"), (101, 100, "claude --foo"), (102, 100, "other")]
        children, argv_by_pid = _build_child_index(rows)
        assert sorted(children[100]) == [101, 102]
        assert argv_by_pid[101] == "claude --foo"

    def test_handles_empty(self):
        children, argv = _build_child_index([])
        assert children == {}
        assert argv == {}


class TestGetDescendantPids:
    def test_bfs_traversal(self):
        # 1 -> 2 -> 4
        # 1 -> 3
        children = {1: [2, 3], 2: [4]}
        descendants = get_descendant_pids(1, children)
        assert set(descendants) == {2, 3, 4}

    def test_does_not_include_root(self):
        children = {1: [2]}
        assert 1 not in get_descendant_pids(1, children)

    def test_depth_bounded(self):
        # Chain 1 -> 2 -> 3 -> 4 -> 5; max_depth=2 stops after 1->2->3
        children = {1: [2], 2: [3], 3: [4], 4: [5]}
        result = get_descendant_pids(1, children, max_depth=2)
        assert 2 in result
        assert 3 in result
        assert 4 not in result

    def test_unknown_root(self):
        assert get_descendant_pids(9999, {1: [2]}) == []


class TestFindClaudeProcess:
    def test_finds_bare_claude(self):
        children = {100: [101]}
        argv = {101: "claude --session-id abc --settings {...}"}
        pid, found_argv = find_claude_process(100, children, argv)
        assert pid == 101
        assert "--settings" in found_argv

    def test_finds_claude_via_absolute_path(self):
        children = {100: [101]}
        argv = {101: "/usr/local/bin/claude --dangerously-skip-permissions"}
        pid, _ = find_claude_process(100, children, argv)
        assert pid == 101

    def test_ignores_non_claude(self):
        children = {100: [101, 102]}
        argv = {101: "zsh", 102: "cat"}
        assert find_claude_process(100, children, argv) == (None, "")

    def test_skips_claude_substring_in_other_process(self):
        """'claudefoo' or 'my-claude-thing' must not match."""
        children = {100: [101]}
        argv = {101: "claudefoo --bar"}
        assert find_claude_process(100, children, argv) == (None, "")

    def test_walks_through_wrapper(self):
        # Shell (100) spawns a wrapper (101) which spawns claude (102).
        children = {100: [101], 101: [102]}
        argv = {101: "bash wrapper.sh", 102: "claude --settings {}"}
        pid, _ = find_claude_process(100, children, argv)
        assert pid == 102


class TestFindAgentProcessConsoleScript:
    """A pip/uv/venv install runs an agent as `<python> <script>`, never as a
    binary of its own name: the agent's name is the script's basename."""

    HERMES = (("hermes",), ("hermes-agent/hermes",))

    def _find(self, argv):
        basenames, markers = self.HERMES
        return find_agent_process(100, {100: [101]}, {101: argv}, basenames, markers)

    @pytest.mark.parametrize("argv", [
        # the Nous installer's shim (the argv marker's case, unchanged)
        "/u/.hermes/hermes-agent/venv/bin/python /u/.hermes/hermes-agent/hermes --cli",
        # a pip or uv console script in any venv
        "/u/venv/bin/python3 /u/venv/bin/hermes --cli",
        "/u/.local/share/uv/tools/hermes-agent/bin/python /u/.local/bin/hermes --cli",
        "/opt/venvs/py/bin/python3.11 /opt/venvs/hermes/bin/hermes -p work --cli",
        # a real binary on PATH
        "/usr/local/bin/hermes --cli",
    ])
    def test_finds_hermes_however_it_was_installed(self, argv):
        assert self._find(argv) == (101, argv)

    @pytest.mark.parametrize("argv", [
        "/u/venv/bin/python /u/venv/bin/hermes-acp",          # a sibling script
        "/u/venv/bin/python /u/work/hermes/run.py",           # a directory named hermes
        "/u/venv/bin/pythonx /u/venv/bin/hermes",             # not an interpreter
        "/usr/bin/node /u/bin/hermes",                        # other runtimes are not assumed
        "/u/venv/bin/python",                                 # no script at all
    ])
    def test_does_not_match_other_processes(self, argv):
        assert self._find(argv) == (None, "")

    @pytest.mark.parametrize("argv", [
        # versioned and free-threaded interpreters
        "/u/.pyenv/versions/3.12.1/bin/python3.12 /u/venv/bin/hermes --cli",
        "/opt/py/bin/python3.13t /u/venv/bin/hermes --cli",
        # `#!/usr/bin/env python3`: env execs the interpreter by bare name
        "python3 /u/.local/bin/hermes --cli",
        # macOS python.org / Homebrew framework builds re-exec into Python.app
        "/Library/Frameworks/Python.framework/Versions/3.12/Resources/Python.app"
        "/Contents/MacOS/Python /u/venv/bin/hermes --cli",
        # interpreter options from a distro shebang, or -X with its value
        "/usr/bin/python3 -s /usr/bin/hermes --cli",
        "/usr/bin/python3 -sP /usr/bin/hermes",
        "/usr/bin/python3 -X utf8 -u /u/bin/hermes --cli",
    ])
    def test_finds_hermes_under_other_interpreter_spellings(self, argv):
        assert self._find(argv) == (101, argv)

    @pytest.mark.parametrize("argv", [
        # a module or a -c string: no script, whatever follows
        "/u/venv/bin/python -m hermes --cli",
        "/u/venv/bin/python -c import hermes /u/bin/hermes",
        # -X's value is not the script
        "/u/venv/bin/python -X hermes",
        # an argument that looks like the agent's path, after another script
        "/u/venv/bin/python /u/tools/run.py /u/venv/bin/hermes",
        # the interpreter is an argument of another program
        "/usr/bin/node /u/x.js /usr/bin/python3 /u/venv/bin/hermes",
        "/bin/sh -c python3 /u/venv/bin/hermes",
        # a non-Python first token, interpreter-like names that are not
        "/u/bin/ruby /u/bin/hermes",
        "/u/bin/python-config /u/bin/hermes",
        "/u/bin/ipython /u/bin/hermes",
        "/u/bin/python3.x /u/bin/hermes",
        "/u/venv/bin/python -s",
    ])
    def test_does_not_misidentify(self, argv):
        assert self._find(argv) == (None, "")

    def test_paths_with_spaces_resolve_only_when_they_exist(self, tmp_path):
        venv = tmp_path / "My Projects" / "venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "python3").write_text("")
        (venv / "hermes").write_text("")
        argv = f"{venv}/python3 {venv}/hermes --cli"
        assert " " in argv
        assert self._find(argv) == (101, argv)
        # the same shape where the joined paths are not real files
        ghost = str(venv).replace("My Projects", "Nope Here")
        assert self._find(f"{ghost}/python3 {ghost}/hermes --cli") == (None, "")
        # a real interpreter with spaces, running a script that is not hermes
        # but whose argument spans to a path ending in /hermes
        argv = f"{venv}/python3 /u/run.py {venv}/hermes"
        assert self._find(argv) == (None, "")

    def test_whitespace_only_argv_is_skipped(self):
        assert self._find("   ") == (None, "")

    def test_claude_lookup_is_unchanged_by_a_python_process(self):
        children = {100: [101]}
        argv = {101: "/usr/bin/python3 /u/bin/claude-helper"}
        assert find_claude_process(100, children, argv) == (None, "")


class TestInspectAgent:
    def test_healthy_when_settings_present(self):
        sess = _make_session()
        children = {10: [11]}
        argv = {11: "claude --session-id abc --settings {hooks:[]}"}
        health = inspect_agent(sess, pane_pid=10, children=children, argv_by_pid=argv)
        assert health.verdict == VERDICT_OK
        assert health.claude_pid == 11
        assert health.ok

    def test_broken_when_settings_missing(self):
        sess = _make_session()
        children = {10: [11]}
        argv = {11: "claude --dangerously-skip-permissions"}
        health = inspect_agent(sess, 10, children, argv)
        assert health.verdict == VERDICT_MISSING_SETTINGS
        assert "overcode restart" in health.details
        assert not health.ok

    def test_no_claude_when_nothing_in_subtree(self):
        sess = _make_session()
        children = {10: [11]}
        argv = {11: "zsh"}
        health = inspect_agent(sess, 10, children, argv)
        assert health.verdict == VERDICT_NO_CLAUDE

    def test_window_gone_when_pane_pid_none(self):
        sess = _make_session()
        health = inspect_agent(sess, None, {}, {})
        assert health.verdict == VERDICT_WINDOW_GONE

    def test_remote_agents_are_skipped(self):
        sess = _make_session(is_remote=True)
        health = inspect_agent(sess, None, {}, {})
        assert health.verdict == VERDICT_REMOTE

    def test_exposes_launcher_version(self):
        sess = _make_session(launcher_version="0.4.0 (abc123)")
        health = inspect_agent(sess, 10, {10: [11]}, {11: "claude --settings x"})
        assert health.launcher_version == "0.4.0 (abc123)"


class TestInspectAgentTelemetryDisabled:
    """Deliberately-off backend_telemetry reads as informational, not broken."""

    def test_codex_missing_hooks_becomes_telemetry_disabled_when_configured_off(
        self, tmp_path, monkeypatch
    ):
        from overcode import config
        from overcode.doctor import VERDICT_TELEMETRY_DISABLED

        config_file = tmp_path / "config.yaml"
        config_file.write_text("backend_telemetry:\n  codex: off\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)
        config._clear_config_cache()

        sess = _make_session(backend="codex", command=["codex"])
        children = {10: [11]}
        argv = {11: "codex -m gpt-5.6-sol"}  # no --dangerously-bypass-hook-trust
        health = inspect_agent(sess, 10, children, argv)
        assert health.verdict == VERDICT_TELEMETRY_DISABLED
        assert "telemetry disabled by config" in health.details
        assert not health.ok

    def test_codex_missing_hooks_stays_missing_settings_by_default(
        self, tmp_path, monkeypatch
    ):
        from overcode import config

        monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "nonexistent.yaml")
        config._clear_config_cache()

        sess = _make_session(backend="codex", command=["codex"])
        children = {10: [11]}
        argv = {11: "codex -m gpt-5.6-sol"}
        health = inspect_agent(sess, 10, children, argv)
        assert health.verdict == VERDICT_MISSING_SETTINGS

    def test_claude_code_never_downgrades_even_if_configured_off(
        self, tmp_path, monkeypatch
    ):
        from overcode import config

        config_file = tmp_path / "config.yaml"
        config_file.write_text("backend_telemetry:\n  claude-code: off\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)
        config._clear_config_cache()

        sess = _make_session()  # default backend = claude-code
        children = {10: [11]}
        argv = {11: "claude --dangerously-skip-permissions"}
        health = inspect_agent(sess, 10, children, argv)
        assert health.verdict == VERDICT_MISSING_SETTINGS

    def test_ok_verdict_is_unaffected(self, tmp_path, monkeypatch):
        from overcode import config

        config_file = tmp_path / "config.yaml"
        config_file.write_text("backend_telemetry:\n  codex: off\n")
        monkeypatch.setattr(config, "CONFIG_PATH", config_file)
        config._clear_config_cache()

        sess = _make_session(backend="codex", command=["codex"])
        children = {10: [11]}
        argv = {11: "codex -m gpt-5.6-sol --dangerously-bypass-hook-trust"}
        health = inspect_agent(sess, 10, children, argv)
        assert health.verdict == VERDICT_OK


class TestGatherDataFindings:
    def test_no_findings_on_fresh_healthy_session(self):
        sess = _make_session()
        assert gather_data_findings(sess) == []

    def test_daemon_down_is_an_error(self):
        sess = _make_session()
        findings = gather_data_findings(sess, daemon_running=False)
        assert FINDING_DAEMON_DOWN in _codes(findings)
        assert any(f.severity == SEVERITY_ERROR for f in findings)

    def test_tokens_zero_with_multiple_interactions(self):
        sess = _make_session()
        stats = _FakeStats(interaction_count=5, input_tokens=0, output_tokens=0)
        findings = gather_data_findings(sess, stats)
        assert FINDING_TOKENS_ZERO in _codes(findings)

    def test_tokens_zero_suppressed_on_first_interaction(self):
        """A single interaction with zero tokens is normal (spawn, no replies yet)."""
        sess = _make_session()
        stats = _FakeStats(interaction_count=1, input_tokens=0, output_tokens=0)
        assert FINDING_TOKENS_ZERO not in _codes(gather_data_findings(sess, stats))

    def test_context_zero_when_tokens_flowed(self):
        sess = _make_session()
        stats = _FakeStats(
            interaction_count=3, input_tokens=100, output_tokens=200,
            current_context_tokens=0,
        )
        assert FINDING_CONTEXT_ZERO in _codes(gather_data_findings(sess, stats))

    def test_context_zero_suppressed_when_no_tokens(self):
        """Zero context is expected before any tokens have flowed."""
        sess = _make_session()
        stats = _FakeStats(interaction_count=0, current_context_tokens=0)
        assert FINDING_CONTEXT_ZERO not in _codes(gather_data_findings(sess, stats))

    def test_cost_zero_when_tokens_over_threshold(self):
        sess = _make_session(stats=SessionStats(estimated_cost_usd=0.0))
        stats = _FakeStats(input_tokens=5000, output_tokens=0, current_context_tokens=5000)
        assert FINDING_COST_ZERO in _codes(gather_data_findings(sess, stats))

    def test_cost_zero_suppressed_below_threshold(self):
        sess = _make_session(stats=SessionStats(estimated_cost_usd=0.0))
        stats = _FakeStats(input_tokens=500, output_tokens=0, current_context_tokens=500)
        assert FINDING_COST_ZERO not in _codes(gather_data_findings(sess, stats))

    def test_sid_orphan_when_active_not_in_list(self):
        sess = _make_session(
            active_agent_session_id="abc12345-deadbeef",
            agent_session_ids=["ff000000-other"],
        )
        assert FINDING_SID_ORPHAN in _codes(gather_data_findings(sess))

    def test_sid_orphan_suppressed_when_active_is_in_list(self):
        sid = "abc12345-deadbeef"
        sess = _make_session(active_agent_session_id=sid, agent_session_ids=[sid])
        assert FINDING_SID_ORPHAN not in _codes(gather_data_findings(sess))

    def test_sid_orphan_suppressed_when_no_active_sid(self):
        sess = _make_session(active_agent_session_id=None, agent_session_ids=[])
        assert FINDING_SID_ORPHAN not in _codes(gather_data_findings(sess))

    def test_stale_activity_on_running_agent(self):
        now = datetime(2026, 4, 20, 12, 0, 0)
        # 6 hours ago — well past default 4h threshold
        last = (now - timedelta(hours=6)).isoformat()
        sess = _make_session(status="running", stats=SessionStats(last_activity=last))
        findings = gather_data_findings(sess, now=now)
        assert FINDING_STALE_ACTIVITY in _codes(findings)

    def test_stale_activity_suppressed_when_recent(self):
        now = datetime(2026, 4, 20, 12, 0, 0)
        last = (now - timedelta(minutes=30)).isoformat()
        sess = _make_session(status="running", stats=SessionStats(last_activity=last))
        assert FINDING_STALE_ACTIVITY not in _codes(gather_data_findings(sess, now=now))

    def test_stale_activity_suppressed_when_asleep(self):
        """Sleeping agents are expected to be silent."""
        now = datetime(2026, 4, 20, 12, 0, 0)
        last = (now - timedelta(hours=6)).isoformat()
        sess = _make_session(
            status="running", is_asleep=True,
            stats=SessionStats(last_activity=last),
        )
        assert FINDING_STALE_ACTIVITY not in _codes(gather_data_findings(sess, now=now))

    def test_stale_activity_suppressed_when_not_running(self):
        now = datetime(2026, 4, 20, 12, 0, 0)
        last = (now - timedelta(hours=6)).isoformat()
        sess = _make_session(status="done", stats=SessionStats(last_activity=last))
        assert FINDING_STALE_ACTIVITY not in _codes(gather_data_findings(sess, now=now))

    def test_oversight_overdue_when_deadline_past(self):
        now = datetime(2026, 4, 20, 12, 0, 0)
        deadline = (now - timedelta(minutes=30)).isoformat()
        sess = _make_session(status="running", oversight_deadline=deadline)
        assert FINDING_OVERSIGHT_OVERDUE in _codes(gather_data_findings(sess, now=now))

    def test_oversight_overdue_suppressed_before_deadline(self):
        now = datetime(2026, 4, 20, 12, 0, 0)
        deadline = (now + timedelta(minutes=30)).isoformat()
        sess = _make_session(status="running", oversight_deadline=deadline)
        assert FINDING_OVERSIGHT_OVERDUE not in _codes(gather_data_findings(sess, now=now))

    def test_malformed_timestamps_do_not_raise(self):
        """Garbage timestamps should be silently skipped, not crash the doctor."""
        sess = _make_session(
            status="running",
            oversight_deadline="not-an-iso",
            stats=SessionStats(last_activity="also-not-iso"),
        )
        # Should not raise
        findings = gather_data_findings(sess)
        # The malformed checks shouldn't have fired
        assert FINDING_STALE_ACTIVITY not in _codes(findings)
        assert FINDING_OVERSIGHT_OVERDUE not in _codes(findings)

    # ---- sleep_but_active ------------------------------------------------

    def test_sleep_but_active_running(self):
        sess = _make_session(
            is_asleep=True,
            stats=SessionStats(current_state="running"),
        )
        findings = gather_data_findings(sess)
        assert FINDING_SLEEP_BUT_ACTIVE in _codes(findings)
        # This one is an error, not a warning — a sleeping agent doing work
        # can burn budget undetected.
        assert any(
            f.code == FINDING_SLEEP_BUT_ACTIVE and f.severity == SEVERITY_ERROR
            for f in findings
        )

    @pytest.mark.parametrize("state", [
        "waiting_user", "waiting_approval", "waiting_heartbeat",
        "waiting_oversight", "running_heartbeat",
    ])
    def test_sleep_but_active_all_active_states(self, state):
        sess = _make_session(
            is_asleep=True,
            stats=SessionStats(current_state=state),
        )
        assert FINDING_SLEEP_BUT_ACTIVE in _codes(gather_data_findings(sess))

    def test_sleep_but_active_suppressed_when_awake(self):
        sess = _make_session(
            is_asleep=False,
            stats=SessionStats(current_state="running"),
        )
        assert FINDING_SLEEP_BUT_ACTIVE not in _codes(gather_data_findings(sess))

    def test_sleep_but_active_suppressed_when_state_is_asleep(self):
        sess = _make_session(
            is_asleep=True,
            stats=SessionStats(current_state="asleep"),
        )
        assert FINDING_SLEEP_BUT_ACTIVE not in _codes(gather_data_findings(sess))

    def test_sleep_but_active_suppressed_when_done(self):
        sess = _make_session(
            is_asleep=True,
            stats=SessionStats(current_state="done"),
        )
        assert FINDING_SLEEP_BUT_ACTIVE not in _codes(gather_data_findings(sess))

    # ---- heartbeat_overdue -----------------------------------------------

    def test_heartbeat_overdue_fires_when_stale(self):
        now = datetime(2026, 4, 20, 12, 0, 0)
        # cadence 300s, last heartbeat 800s ago → overdue by 500s (> cadence)
        last_hb = (now - timedelta(seconds=800)).isoformat()
        sess = _make_session(
            heartbeat_enabled=True,
            heartbeat_frequency_seconds=300,
            last_heartbeat_time=last_hb,
        )
        assert FINDING_HEARTBEAT_OVERDUE in _codes(gather_data_findings(sess, now=now))

    def test_heartbeat_overdue_suppressed_when_fresh(self):
        now = datetime(2026, 4, 20, 12, 0, 0)
        last_hb = (now - timedelta(seconds=100)).isoformat()
        sess = _make_session(
            heartbeat_enabled=True,
            heartbeat_frequency_seconds=300,
            last_heartbeat_time=last_hb,
        )
        assert FINDING_HEARTBEAT_OVERDUE not in _codes(gather_data_findings(sess, now=now))

    def test_heartbeat_overdue_suppressed_when_paused(self):
        """A paused heartbeat can't be overdue."""
        now = datetime(2026, 4, 20, 12, 0, 0)
        last_hb = (now - timedelta(hours=5)).isoformat()
        sess = _make_session(
            heartbeat_enabled=True,
            heartbeat_paused=True,
            heartbeat_frequency_seconds=300,
            last_heartbeat_time=last_hb,
        )
        assert FINDING_HEARTBEAT_OVERDUE not in _codes(gather_data_findings(sess, now=now))

    def test_heartbeat_overdue_suppressed_when_asleep(self):
        now = datetime(2026, 4, 20, 12, 0, 0)
        last_hb = (now - timedelta(hours=5)).isoformat()
        sess = _make_session(
            heartbeat_enabled=True,
            is_asleep=True,
            heartbeat_frequency_seconds=300,
            last_heartbeat_time=last_hb,
        )
        assert FINDING_HEARTBEAT_OVERDUE not in _codes(gather_data_findings(sess, now=now))

    def test_heartbeat_overdue_suppressed_when_disabled(self):
        now = datetime(2026, 4, 20, 12, 0, 0)
        last_hb = (now - timedelta(hours=5)).isoformat()
        sess = _make_session(
            heartbeat_enabled=False,
            heartbeat_frequency_seconds=300,
            last_heartbeat_time=last_hb,
        )
        assert FINDING_HEARTBEAT_OVERDUE not in _codes(gather_data_findings(sess, now=now))

    # ---- budget_exceeded -------------------------------------------------

    def test_budget_exceeded_when_cost_over_cap(self):
        sess = _make_session(
            cost_budget_usd=5.0,
            stats=SessionStats(estimated_cost_usd=7.50),
        )
        findings = gather_data_findings(sess)
        assert FINDING_BUDGET_EXCEEDED in _codes(findings)
        assert any(f.severity == SEVERITY_ERROR and f.code == FINDING_BUDGET_EXCEEDED
                   for f in findings)

    def test_budget_exceeded_suppressed_under_cap(self):
        sess = _make_session(
            cost_budget_usd=5.0,
            stats=SessionStats(estimated_cost_usd=2.50),
        )
        assert FINDING_BUDGET_EXCEEDED not in _codes(gather_data_findings(sess))

    def test_budget_exceeded_suppressed_when_unlimited(self):
        """A budget of 0.0 means unlimited — no overage possible."""
        sess = _make_session(
            cost_budget_usd=0.0,
            stats=SessionStats(estimated_cost_usd=100.0),
        )
        assert FINDING_BUDGET_EXCEEDED not in _codes(gather_data_findings(sess))

    def test_budget_exceeded_suppressed_when_done(self):
        """A finished agent over budget is already past the point of mitigation."""
        sess = _make_session(
            status="done",
            cost_budget_usd=5.0,
            stats=SessionStats(estimated_cost_usd=7.0),
        )
        assert FINDING_BUDGET_EXCEEDED not in _codes(gather_data_findings(sess))

    # ---- sids_empty_with_interactions -----------------------------------

    def test_sids_empty_with_interactions(self):
        sess = _make_session(agent_session_ids=[])
        stats = _FakeStats(interaction_count=3, input_tokens=100, output_tokens=50)
        assert FINDING_SIDS_EMPTY in _codes(gather_data_findings(sess, stats))

    def test_sids_empty_suppressed_when_sids_tracked(self):
        sess = _make_session(agent_session_ids=["abc-123"])
        stats = _FakeStats(interaction_count=3, input_tokens=100, output_tokens=50)
        assert FINDING_SIDS_EMPTY not in _codes(gather_data_findings(sess, stats))

    def test_sids_empty_suppressed_without_interactions(self):
        """No interactions = no expectation of sid tracking yet."""
        sess = _make_session(agent_session_ids=[])
        stats = _FakeStats(interaction_count=0)
        assert FINDING_SIDS_EMPTY not in _codes(gather_data_findings(sess, stats))

    # ---- model_drift ------------------------------------------------------

    def test_model_drift_when_models_differ(self):
        sess = _make_session(model="opus")
        stats = _FakeStats(model="claude-sonnet-4-6")
        assert FINDING_MODEL_DRIFT in _codes(gather_data_findings(sess, stats))

    def test_model_drift_suppressed_when_short_matches_long(self):
        """'sonnet' is a shortname that matches 'claude-sonnet-4-6'."""
        sess = _make_session(model="sonnet")
        stats = _FakeStats(model="claude-sonnet-4-6")
        assert FINDING_MODEL_DRIFT not in _codes(gather_data_findings(sess, stats))

    def test_model_drift_suppressed_when_session_model_unset(self):
        """No launched model means no drift to detect."""
        sess = _make_session(model=None)
        stats = _FakeStats(model="claude-sonnet-4-6")
        assert FINDING_MODEL_DRIFT not in _codes(gather_data_findings(sess, stats))

    def test_model_drift_suppressed_when_live_model_unknown(self):
        sess = _make_session(model="opus")
        stats = _FakeStats(model=None)
        assert FINDING_MODEL_DRIFT not in _codes(gather_data_findings(sess, stats))


class TestInspectAgentFindings:
    def test_findings_attached_to_healthy_agent(self):
        sess = _make_session()
        health = inspect_agent(
            sess, 10, {10: [11]}, {11: "claude --settings x"},
            daemon_running=False,
        )
        assert health.verdict == VERDICT_OK
        assert FINDING_DAEMON_DOWN in _codes(health.data_findings)
        # Presence of findings means the agent is not fully ok
        assert not health.ok

    def test_findings_attached_to_missing_settings_agent(self):
        sess = _make_session()
        health = inspect_agent(
            sess, 10, {10: [11]}, {11: "claude"},
            daemon_running=False,
        )
        assert health.verdict == VERDICT_MISSING_SETTINGS
        assert FINDING_DAEMON_DOWN in _codes(health.data_findings)

    def test_remote_agents_get_no_findings(self):
        """Remote sessions short-circuit before data checks — we can't inspect them."""
        sess = _make_session(is_remote=True)
        health = inspect_agent(
            sess, None, {}, {},
            daemon_running=False,  # Would normally trigger a finding
        )
        assert health.verdict == VERDICT_REMOTE
        assert health.data_findings == []

    def test_window_gone_still_includes_findings(self):
        """A dead window doesn't excuse missing monitor — findings still surface."""
        sess = _make_session()
        health = inspect_agent(
            sess, None, {}, {},
            daemon_running=False,
        )
        assert health.verdict == VERDICT_WINDOW_GONE
        assert FINDING_DAEMON_DOWN in _codes(health.data_findings)

    def test_ok_property_flips_on_finding(self):
        sess = _make_session()
        healthy = inspect_agent(sess, 10, {10: [11]}, {11: "claude --settings x"})
        assert healthy.ok is True

        flagged = inspect_agent(
            sess, 10, {10: [11]}, {11: "claude --settings x"},
            daemon_running=False,
        )
        assert flagged.ok is False
