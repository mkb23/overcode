"""StatsReader seam: Claude delegation, null degradation, daemon wiring.

Phase 2 of the agent-agnostic backends work — a backend without readable
transcripts must report "unknown" (None) rather than zeros, and must not
make the daemon write garbage stats.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.backends import (
    BackendCapability,
    register_backend,
    session_supports,
    unregister_backend,
)
from overcode.backends.claude_code import ClaudeCodeBackend
from overcode.history_reader import ClaudeSessionStats
from overcode.stats_reader import (
    AgentSessionStats,
    ClaudeStatsReader,
    DiscoveredSessionIds,
    NullStatsReader,
    clear_reader_cache,
    empty_window_usage,
    stats_reader_for_session,
)


STATSLESS_BACKEND = "__statsless_test__"


class StatslessBackend(ClaudeCodeBackend):
    """Test double: everything Claude does except on-disk telemetry."""

    name = STATSLESS_BACKEND
    display_name = "Statsless Test"
    binary = "statsless"
    process_basenames = ("statsless",)
    capabilities = BackendCapability.RESUME | BackendCapability.HOOK_EVENTS

    def make_stats_reader(self):
        return NullStatsReader(self.name)


@pytest.fixture
def statsless_backend():
    register_backend(StatslessBackend())
    try:
        yield STATSLESS_BACKEND
    finally:
        unregister_backend(STATSLESS_BACKEND)
        clear_reader_cache()


def _make_session(backend="claude-code", **overrides):
    session = Mock()
    session.id = "sess-1"
    session.name = "agent-1"
    session.backend = backend
    session.start_directory = "/tmp/project"
    session.start_time = datetime.now().isoformat()
    session.tmux_session = "test"
    session.wrapper = None
    session.model = None
    session.provider = "web"
    session.agent_session_ids = []
    session.active_agent_session_id = None
    for key, value in overrides.items():
        setattr(session, key, value)
    return session


def _make_stats(**overrides):
    defaults = dict(
        interaction_count=3,
        input_tokens=100,
        output_tokens=50,
        cache_creation_tokens=10,
        cache_read_tokens=5,
        work_times=[1.0],
        current_context_tokens=1000,
    )
    defaults.update(overrides)
    return ClaudeSessionStats(**defaults)


class TestAlias:
    def test_agent_session_stats_is_claude_session_stats(self):
        # Phase 6 flipped which name is canonical; both still resolve.
        assert AgentSessionStats is ClaudeSessionStats
        assert AgentSessionStats.__name__ == "AgentSessionStats"


class TestClaudeStatsReader:
    """Delegates to history_reader without reshaping anything."""

    def test_get_stats_delegates(self):
        reader = ClaudeStatsReader()
        session = _make_session()
        stats = _make_stats()
        with patch("overcode.history_reader.get_session_stats", return_value=stats) as m:
            assert reader.get_stats(session) is stats
        m.assert_called_once_with(session)

    def test_get_stats_passes_history_file_through(self):
        reader = ClaudeStatsReader()
        session = _make_session()
        hf = object()
        with patch("overcode.history_reader.get_session_stats", return_value=None) as m:
            reader.get_stats(session, history_file=hf)
        assert m.call_args[1]["history_file"] is hf

    def test_get_current_session_id_delegates(self):
        reader = ClaudeStatsReader()
        session = _make_session()
        since = datetime(2026, 1, 1)
        with patch(
            "overcode.history_reader.get_current_session_id_for_directory",
            return_value="sid-abc",
        ) as m:
            assert reader.get_current_session_id(session, since) == "sid-abc"
        m.assert_called_once_with("/tmp/project", since)

    def test_get_current_session_id_without_directory(self):
        reader = ClaudeStatsReader()
        session = _make_session(start_directory=None)
        assert reader.get_current_session_id(session, datetime.now()) is None

    def test_get_window_token_usage_delegates(self):
        reader = ClaudeStatsReader()
        session = _make_session()
        since = datetime.now() - timedelta(hours=1)
        totals = dict(empty_window_usage(), input_tokens=42)
        with patch(
            "overcode.history_reader.get_session_window_token_usage",
            return_value=totals,
        ) as m:
            assert reader.get_window_token_usage(session, since) == totals
        m.assert_called_once_with(session, since)

    def test_discover_session_ids_skips_owned_and_reports_latest(self, monkeypatch):
        reader = ClaudeStatsReader()
        session = _make_session(agent_session_ids=["mine"])
        other = Mock()
        other.id = "sess-2"
        other.agent_session_ids = ["theirs"]

        def _entry(sid, ts):
            e = Mock()
            e.session_id = sid
            e.project = "/tmp/project"
            e.timestamp_ms = ts
            return e

        start = datetime(2026, 1, 1, 12, 0, 0)
        start_ms = int(start.timestamp() * 1000)
        entries = [
            _entry("too-old", start_ms - 1000),
            _entry("mine", start_ms + 1000),
            _entry("theirs", start_ms + 2000),
            _entry("orphan-a", start_ms + 3000),
            _entry("orphan-b", start_ms + 4000),
        ]
        fake_history = Mock()
        fake_history.read_all.return_value = entries
        monkeypatch.setattr(
            "overcode.history_reader.HistoryFile", lambda *a, **k: fake_history
        )

        found = reader.discover_session_ids(session, start, [session, other])

        assert found.ids == ["orphan-a", "orphan-b"]
        assert found.latest == "orphan-b"

    def test_discover_session_ids_without_directory(self):
        reader = ClaudeStatsReader()
        session = _make_session(start_directory=None)
        found = reader.discover_session_ids(session, datetime.now(), [])
        assert found == DiscoveredSessionIds()

    def test_container_stats_none_without_wrapper(self):
        reader = ClaudeStatsReader()
        assert reader.get_container_stats(_make_session()) is None

    def test_container_stats_none_without_session_id(self):
        reader = ClaudeStatsReader()
        session = _make_session(wrapper="devcontainer.sh")
        assert reader.get_container_stats(session) is None

    def test_container_stats_reads_docker_transcripts(self, monkeypatch):
        reader = ClaudeStatsReader()
        session = _make_session(
            wrapper="devcontainer.sh",
            agent_session_ids=["sid-1"],
            active_agent_session_id="sid-1",
        )

        def fake_run(cmd, **kwargs):
            result = Mock()
            result.returncode = 0
            result.stdout = "/home/node" if "sh" in cmd else "{}"
            return result

        monkeypatch.setattr("overcode.stats_reader.subprocess.run", fake_run)
        monkeypatch.setattr(
            "overcode.history_reader.read_session_stats_from_content",
            lambda content: (
                {
                    "input_tokens": 700,
                    "output_tokens": 300,
                    "cache_creation_tokens": 20,
                    "cache_read_tokens": 10,
                    "current_context_tokens": 5000,
                    "model": "claude-opus-4-6",
                    "provider": "web",
                },
                [2.0],
            ),
        )

        stats = reader.get_container_stats(session)

        assert stats is not None
        assert stats.input_tokens == 700
        assert stats.output_tokens == 300
        assert stats.current_context_tokens == 5000
        assert stats.model == "claude-opus-4-6"
        assert stats.provider == "web"


class TestNullStatsReader:
    """Every answer is 'unknown' — never a zero pretending to be data."""

    def test_all_operations_degrade(self):
        reader = NullStatsReader("nostats")
        session = _make_session(backend="nostats")
        now = datetime.now()

        assert reader.get_stats(session) is None
        assert reader.get_stats(session, history_file=object()) is None
        assert reader.get_current_session_id(session, now) is None
        assert reader.discover_session_ids(session, now, []) == DiscoveredSessionIds()
        assert reader.get_window_token_usage(session, now) == empty_window_usage()
        assert reader.get_container_stats(session) is None

    def test_window_usage_shape_matches_claude(self):
        assert set(empty_window_usage()) == {
            "input_tokens",
            "output_tokens",
            "cache_creation_tokens",
            "cache_read_tokens",
        }


class TestReaderResolution:
    def test_claude_session_gets_claude_reader(self):
        assert isinstance(stats_reader_for_session(_make_session()), ClaudeStatsReader)

    def test_missing_backend_field_defaults_to_claude(self):
        session = Mock(spec=[])  # no .backend attribute at all
        assert isinstance(stats_reader_for_session(session), ClaudeStatsReader)

    def test_backend_without_transcript_stats_gets_null_reader(self, statsless_backend):
        session = _make_session(backend=statsless_backend)
        assert isinstance(stats_reader_for_session(session), NullStatsReader)

    def test_unknown_backend_gets_null_reader(self):
        session = _make_session(backend="__never_registered__")
        assert isinstance(stats_reader_for_session(session), NullStatsReader)

    def test_readers_are_cached_per_backend(self):
        session = _make_session()
        assert stats_reader_for_session(session) is stats_reader_for_session(session)

    def test_registering_a_backend_invalidates_the_cache(self):
        session = _make_session(backend=STATSLESS_BACKEND)
        assert isinstance(stats_reader_for_session(session), NullStatsReader)
        register_backend(StatslessBackend())
        try:
            assert isinstance(stats_reader_for_session(session), NullStatsReader)
        finally:
            unregister_backend(STATSLESS_BACKEND)
            clear_reader_cache()

    def test_capability_helper_matches_reader_choice(self, statsless_backend):
        claude = _make_session()
        statsless = _make_session(backend=statsless_backend)
        assert session_supports(claude, BackendCapability.TRANSCRIPT_STATS)
        assert not session_supports(statsless, BackendCapability.TRANSCRIPT_STATS)


class TestDaemonWithStatslessBackend:
    """A statsless backend must not crash or fabricate stats in a tick."""

    def _make_daemon(self, tmp_path, monkeypatch):
        from overcode.monitor_daemon import MonitorDaemon

        monkeypatch.setattr('overcode.monitor_daemon.ensure_session_dir', lambda x: tmp_path)
        monkeypatch.setattr(
            'overcode.monitor_daemon.get_monitor_daemon_pid_path',
            lambda x: tmp_path / "pid",
        )
        monkeypatch.setattr(
            'overcode.monitor_daemon.get_monitor_daemon_state_path',
            lambda x: tmp_path / "state.json",
        )
        monkeypatch.setattr(
            'overcode.monitor_daemon.get_agent_history_path',
            lambda x: tmp_path / "history.csv",
        )
        with patch('overcode.monitor_daemon.SessionManager') as mock_sm_cls, \
                patch('overcode.monitor_daemon.StatusDetectorDispatcher'):
            daemon = MonitorDaemon(tmux_session="test")
            daemon.session_manager = mock_sm_cls.return_value
        return daemon

    def test_sync_agent_stats_writes_nothing(self, tmp_path, monkeypatch, statsless_backend):
        daemon = self._make_daemon(tmp_path, monkeypatch)
        session = _make_session(backend=statsless_backend)
        daemon.session_manager.list_sessions.return_value = [session]

        with patch("overcode.history_reader.get_session_stats") as claude_stats:
            daemon.sync_agent_stats(session)

        claude_stats.assert_not_called()
        daemon.session_manager.update_stats.assert_not_called()
        daemon.session_manager.add_agent_session_id.assert_not_called()
        daemon.session_manager.set_active_agent_session_id.assert_not_called()

    def test_sync_session_id_writes_nothing(self, tmp_path, monkeypatch, statsless_backend):
        daemon = self._make_daemon(tmp_path, monkeypatch)
        session = _make_session(backend=statsless_backend)
        daemon.session_manager.list_sessions.return_value = [session]

        daemon.sync_session_id(session)

        daemon.session_manager.add_agent_session_id.assert_not_called()
        daemon.session_manager.set_active_agent_session_id.assert_not_called()

    def test_container_path_is_not_probed(self, tmp_path, monkeypatch, statsless_backend):
        daemon = self._make_daemon(tmp_path, monkeypatch)
        session = _make_session(backend=statsless_backend, wrapper="devcontainer.sh")
        daemon.session_manager.list_sessions.return_value = [session]

        def boom(*args, **kwargs):
            raise AssertionError("docker must not be invoked for a statsless backend")

        monkeypatch.setattr("overcode.stats_reader.subprocess.run", boom)

        daemon.sync_agent_stats(session)

        daemon.session_manager.update_stats.assert_not_called()

    def test_sandbox_probe_skipped(self, tmp_path, monkeypatch, statsless_backend):
        daemon = self._make_daemon(tmp_path, monkeypatch)
        session = _make_session(backend=statsless_backend, is_remote=False)
        session.tmux_window = "agent-ab12"

        monkeypatch.setattr(
            "overcode.doctor._snapshot_process_table", lambda: {1: "row"}
        )
        monkeypatch.setattr(
            "overcode.doctor._build_child_index", lambda rows: ({}, {})
        )
        tmux = Mock()
        monkeypatch.setattr("overcode.implementations.RealTmux", lambda: tmux)
        probed = []
        monkeypatch.setattr(
            "overcode.sandbox_detect.detect_sandbox_states",
            lambda pids: probed.append(list(pids)) or {},
        )

        daemon._sync_sandbox_state([session], datetime.now())

        assert probed == [[]]
        tmux.get_pane_pid.assert_not_called()
        daemon.session_manager.update_session.assert_not_called()

    def test_claude_session_still_syncs(self, tmp_path, monkeypatch):
        daemon = self._make_daemon(tmp_path, monkeypatch)
        session = _make_session()
        daemon.session_manager.list_sessions.return_value = [session]

        monkeypatch.setattr(
            "overcode.history_reader.get_session_stats", lambda s: _make_stats()
        )
        monkeypatch.setattr(
            "overcode.history_reader.get_current_session_id_for_directory",
            lambda d, s: None,
        )

        daemon.sync_agent_stats(session)

        daemon.session_manager.update_stats.assert_called_once()
        assert daemon.session_manager.update_stats.call_args[1]["input_tokens"] == 100


class TestUsageWidgetGate:
    """The subscription-usage widget is fleet-level, gated on the fleet."""

    def _fleet(self, *backends):
        state = Mock()
        state.sessions = [Mock(backend=b) for b in backends]
        return state

    def _check(self, state):
        from overcode.tui import SupervisorTUI

        return SupervisorTUI._fleet_has_subscription_usage(None, state)

    def test_claude_fleet_fetches(self):
        assert self._check(self._fleet("claude-code")) is True

    def test_mixed_fleet_still_fetches(self, statsless_backend):
        assert self._check(self._fleet(statsless_backend, "claude-code")) is True

    def test_fleet_without_subscription_backends_skips(self, statsless_backend):
        assert self._check(self._fleet(statsless_backend)) is False

    def test_empty_fleet_fetches(self):
        assert self._check(self._fleet()) is True
        assert self._check(None) is True


class TestDoctorSuppression:
    """Zero-token findings are meaningless without transcripts."""

    def _session(self, backend):
        from overcode.session_manager import Session

        return Session(
            id="sess-1",
            name="agent-1",
            tmux_session="test",
            tmux_window="agent-ab12",
            command="claude",
            start_time=datetime.now().isoformat(),
            start_directory="/tmp/project",
            backend=backend,
        )

    def test_zero_findings_fire_for_claude(self):
        from overcode.doctor import (
            FINDING_TOKENS_ZERO,
            gather_data_findings,
        )

        session = self._session("claude-code")
        stats = _make_stats(interaction_count=4, input_tokens=0, output_tokens=0)
        codes = {
            f.code for f in gather_data_findings(session, stats, daemon_running=True)
        }
        assert FINDING_TOKENS_ZERO in codes

    def test_zero_findings_suppressed_without_transcript_stats(self, statsless_backend):
        from overcode.doctor import (
            FINDING_CONTEXT_ZERO,
            FINDING_COST_ZERO,
            FINDING_TOKENS_ZERO,
            gather_data_findings,
        )

        session = self._session(statsless_backend)
        stats = _make_stats(interaction_count=4, input_tokens=0, output_tokens=0)
        codes = {
            f.code for f in gather_data_findings(session, stats, daemon_running=True)
        }
        assert FINDING_TOKENS_ZERO not in codes
        assert FINDING_CONTEXT_ZERO not in codes
        assert FINDING_COST_ZERO not in codes
