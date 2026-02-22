"""
Tests for web_api.py - analytics API functions.
"""

from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
import pytest

from overcode.web_api import _calculate_presence_efficiency


# Test session name for all tests
TEST_SESSION = "test-session"


class TestCalculatePresenceEfficiency:
    """Tests for presence efficiency calculation."""

    def test_returns_zeros_when_no_presence_history(self):
        """Should return zeros when no presence data available."""
        end = datetime.now()
        start = end - timedelta(hours=1)

        with patch('overcode.web_api.read_agent_status_history') as mock_agent, \
             patch('overcode.presence_logger.read_presence_history') as mock_presence:
            mock_agent.return_value = [
                (start + timedelta(minutes=10), 'agent1', 'running', 'working'),
            ]
            mock_presence.return_value = []

            result = _calculate_presence_efficiency(TEST_SESSION, start, end)

            assert result['has_data'] is False
            assert result['present_efficiency'] == 0.0
            assert result['afk_efficiency'] == 0.0

    def test_returns_zeros_when_no_data(self):
        """Should return zeros when both histories are empty."""
        end = datetime.now()
        start = end - timedelta(hours=1)

        with patch('overcode.web_api.read_agent_status_history') as mock_agent, \
             patch('overcode.presence_logger.read_presence_history') as mock_presence:
            mock_agent.return_value = []
            mock_presence.return_value = []

            result = _calculate_presence_efficiency(TEST_SESSION, start, end)

            assert result['has_data'] is False
            assert result['present_efficiency'] == 0.0
            assert result['afk_efficiency'] == 0.0
            assert result['present_samples'] == 0
            assert result['afk_samples'] == 0

    def test_calculates_present_efficiency_when_user_active(self):
        """Should calculate efficiency correctly when user is present."""
        end = datetime.now()
        start = end - timedelta(minutes=10)

        with patch('overcode.web_api.read_agent_status_history') as mock_agent, \
             patch('overcode.presence_logger.read_presence_history') as mock_presence:
            # Agent running the whole time
            mock_agent.return_value = [
                (start + timedelta(minutes=1), 'agent1', 'running', 'working'),
            ]
            # User active (state=3) the whole time
            mock_presence.return_value = [
                (start, 3),
            ]

            result = _calculate_presence_efficiency(TEST_SESSION, start, end, sample_interval_seconds=60)

            assert result['has_data'] is True
            assert result['present_efficiency'] == 100.0
            assert result['afk_efficiency'] == 0.0
            assert result['present_samples'] > 0
            assert result['afk_samples'] == 0

    def test_calculates_afk_efficiency_when_user_away(self):
        """Should calculate efficiency correctly when user is AFK."""
        end = datetime.now()
        start = end - timedelta(minutes=10)

        with patch('overcode.web_api.read_agent_status_history') as mock_agent, \
             patch('overcode.presence_logger.read_presence_history') as mock_presence:
            # Agent running the whole time
            mock_agent.return_value = [
                (start + timedelta(minutes=1), 'agent1', 'running', 'working'),
            ]
            # User locked (state=1) the whole time
            mock_presence.return_value = [
                (start, 1),
            ]

            result = _calculate_presence_efficiency(TEST_SESSION, start, end, sample_interval_seconds=60)

            assert result['has_data'] is True
            assert result['present_efficiency'] == 0.0
            assert result['afk_efficiency'] == 100.0
            assert result['present_samples'] == 0
            assert result['afk_samples'] > 0

    def test_calculates_mixed_efficiency(self):
        """Should handle mixed present/AFK periods correctly."""
        end = datetime.now()
        start = end - timedelta(minutes=10)
        midpoint = start + timedelta(minutes=5)

        with patch('overcode.web_api.read_agent_status_history') as mock_agent, \
             patch('overcode.presence_logger.read_presence_history') as mock_presence:
            # Agent running the whole time
            mock_agent.return_value = [
                (start + timedelta(seconds=30), 'agent1', 'running', 'working'),
            ]
            # User active first half, then AFK second half
            mock_presence.return_value = [
                (start, 3),  # Active
                (midpoint, 1),  # Then locked
            ]

            result = _calculate_presence_efficiency(TEST_SESSION, start, end, sample_interval_seconds=60)

            assert result['has_data'] is True
            # Both should be 100% since agent was running throughout
            assert result['present_efficiency'] == 100.0
            assert result['afk_efficiency'] == 100.0
            assert result['present_samples'] > 0
            assert result['afk_samples'] > 0

    def test_handles_multiple_agents(self):
        """Should calculate percentage based on all agents."""
        end = datetime.now()
        start = end - timedelta(minutes=5)

        with patch('overcode.web_api.read_agent_status_history') as mock_agent, \
             patch('overcode.presence_logger.read_presence_history') as mock_presence:
            # Two agents: one running, one not
            mock_agent.return_value = [
                (start + timedelta(seconds=30), 'agent1', 'running', 'working'),
                (start + timedelta(seconds=30), 'agent2', 'waiting_user', 'blocked'),
            ]
            # User active
            mock_presence.return_value = [
                (start, 3),
            ]

            result = _calculate_presence_efficiency(TEST_SESSION, start, end, sample_interval_seconds=60)

            assert result['has_data'] is True
            # 1 of 2 agents running = 50%
            assert result['present_efficiency'] == 50.0
            assert result['present_samples'] > 0

    def test_inactive_state_counts_as_afk(self):
        """User state 2 (inactive) should count as AFK."""
        end = datetime.now()
        start = end - timedelta(minutes=5)

        with patch('overcode.web_api.read_agent_status_history') as mock_agent, \
             patch('overcode.presence_logger.read_presence_history') as mock_presence:
            mock_agent.return_value = [
                (start + timedelta(seconds=30), 'agent1', 'running', 'working'),
            ]
            # User inactive (state=2)
            mock_presence.return_value = [
                (start, 2),
            ]

            result = _calculate_presence_efficiency(TEST_SESSION, start, end, sample_interval_seconds=60)

            assert result['has_data'] is True
            assert result['present_efficiency'] == 0.0
            assert result['afk_efficiency'] == 100.0
            assert result['afk_samples'] > 0

    def test_uses_default_time_range_when_not_specified(self):
        """Should default to last 24 hours when no range given."""
        with patch('overcode.web_api.read_agent_status_history') as mock_agent, \
             patch('overcode.presence_logger.read_presence_history') as mock_presence:
            mock_agent.return_value = []
            mock_presence.return_value = []

            result = _calculate_presence_efficiency(TEST_SESSION)

            # Should not raise, just return empty data
            assert result['has_data'] is False
            # Verify it called with ~24 hours
            call_args = mock_agent.call_args
            assert call_args is not None
            hours = call_args[1].get('hours', 0)
            assert 23.9 < hours < 24.1


class TestGetAnalyticsStatsIncludesPresenceEfficiency:
    """Test that get_analytics_stats includes presence_efficiency field."""

    def test_stats_includes_presence_efficiency_field(self):
        """get_analytics_stats should include presence_efficiency in response."""
        from overcode.web_api import get_analytics_stats

        with patch('overcode.web_api.get_analytics_sessions') as mock_sessions, \
             patch('overcode.web_api._calculate_presence_efficiency') as mock_presence:
            mock_sessions.return_value = {
                'sessions': [],
                'summary': {
                    'total_cost_usd': 0,
                    'total_tokens': 0,
                    'total_green_time_seconds': 0,
                    'total_non_green_time_seconds': 0,
                    'avg_green_percent': 0,
                }
            }
            mock_presence.return_value = {
                'present_efficiency': 75.0,
                'afk_efficiency': 80.0,
                'present_samples': 100,
                'afk_samples': 50,
                'has_data': True,
            }

            result = get_analytics_stats(TEST_SESSION)

            assert 'presence_efficiency' in result
            assert result['presence_efficiency']['present_efficiency'] == 75.0
            assert result['presence_efficiency']['afk_efficiency'] == 80.0
            assert result['presence_efficiency']['has_data'] is True


class TestGetWebColor:
    """Tests for get_web_color function."""

    def test_returns_known_colors(self):
        """Should return correct hex for known colors."""
        from overcode.web_api import get_web_color

        assert get_web_color("green") == "#22c55e"
        assert get_web_color("yellow") == "#eab308"
        assert get_web_color("red") == "#ef4444"
        assert get_web_color("cyan") == "#06b6d4"

    def test_returns_default_for_unknown(self):
        """Should return dim gray for unknown colors."""
        from overcode.web_api import get_web_color

        assert get_web_color("unknown_color") == "#6b7280"
        assert get_web_color("") == "#6b7280"


class TestBuildDaemonInfo:
    """Tests for _build_daemon_info function."""

    def test_returns_stopped_when_no_state(self):
        """Should return stopped status when state is None."""
        from overcode.web_api import _build_daemon_info

        result = _build_daemon_info(None)

        assert result["running"] is False
        assert result["status"] == "stopped"
        assert result["loop_count"] == 0
        assert result["supervisor_claude_running"] is False

    def test_returns_running_info_when_state_exists(self):
        """Should return daemon info from state."""
        from overcode.web_api import _build_daemon_info
        from overcode.monitor_daemon_state import MonitorDaemonState
        from datetime import datetime

        state = MonitorDaemonState(
            status="running",
            loop_count=42,
            current_interval=5.0,
        )
        # Make it not stale by setting recent last_loop_time
        state.last_loop_time = datetime.now().isoformat()
        # Add missing summarizer attributes (these should exist but don't - potential bug)
        state.summarizer_enabled = False
        state.summarizer_available = False
        state.summarizer_calls = 0
        state.summarizer_cost_usd = 0.0

        result = _build_daemon_info(state)

        assert result["running"] is True
        assert result["status"] == "running"
        assert result["loop_count"] == 42
        assert result["interval"] == 5.0


class TestBuildPresenceInfo:
    """Tests for _build_presence_info function."""

    def test_returns_unavailable_when_no_state(self):
        """Should return unavailable when state is None."""
        from overcode.web_api import _build_presence_info

        result = _build_presence_info(None)

        assert result["available"] is False

    def test_returns_unavailable_when_presence_not_available(self):
        """Should return unavailable when presence not available."""
        from overcode.web_api import _build_presence_info
        from overcode.monitor_daemon_state import MonitorDaemonState

        state = MonitorDaemonState(presence_available=False)

        result = _build_presence_info(state)

        assert result["available"] is False

    def test_returns_presence_info_when_available(self):
        """Should return presence info when available."""
        from overcode.web_api import _build_presence_info
        from overcode.monitor_daemon_state import MonitorDaemonState

        state = MonitorDaemonState(
            presence_available=True,
            presence_state=3,
            presence_idle_seconds=120.5,
        )

        result = _build_presence_info(state)

        assert result["available"] is True
        assert result["state"] == 3
        assert result["state_name"] == "active"
        assert result["idle_seconds"] == 120.5

    def test_returns_correct_state_names(self):
        """Should return correct state names for each state."""
        from overcode.web_api import _build_presence_info
        from overcode.monitor_daemon_state import MonitorDaemonState

        for state_code, expected_name in [(0, "asleep"), (1, "locked"), (2, "idle"), (3, "active"), (4, "tui_active")]:
            state = MonitorDaemonState(
                presence_available=True,
                presence_state=state_code,
            )
            result = _build_presence_info(state)
            assert result["state_name"] == expected_name


class TestBuildSummary:
    """Tests for _build_summary function."""

    def test_returns_zeros_when_no_state(self):
        """Should return zeros when state is None."""
        from overcode.web_api import _build_summary

        result = _build_summary(None)

        assert result["total_agents"] == 0
        assert result["green_agents"] == 0
        assert result["total_green_time"] == 0
        assert result["total_non_green_time"] == 0

    def test_returns_summary_from_state(self):
        """Should return summary statistics from state."""
        from overcode.web_api import _build_summary
        from overcode.monitor_daemon_state import MonitorDaemonState, SessionDaemonState

        state = MonitorDaemonState(
            sessions=[
                SessionDaemonState(session_id="1", name="agent1", current_status="running"),
                SessionDaemonState(session_id="2", name="agent2", current_status="waiting_user"),
            ],
            green_sessions=1,
            total_green_time=3600.0,
            total_non_green_time=1800.0,
        )

        result = _build_summary(state)

        assert result["total_agents"] == 2
        assert result["green_agents"] == 1
        assert result["total_green_time"] == 3600.0
        assert result["total_non_green_time"] == 1800.0


class TestBuildAgentInfo:
    """Tests for _build_agent_info function."""

    def test_builds_basic_agent_info(self):
        """Should build agent info from SessionDaemonState."""
        from overcode.web_api import _build_agent_info
        from overcode.monitor_daemon_state import SessionDaemonState
        from datetime import datetime

        session = SessionDaemonState(
            session_id="test-id",
            name="test-agent",
            current_status="running",
            current_activity="processing files",
            repo_name="test-repo",
            branch="main",
            green_time_seconds=3600.0,
            non_green_time_seconds=600.0,
            interaction_count=10,
            steers_count=3,
            input_tokens=5000,
            output_tokens=2000,
            estimated_cost_usd=0.50,
        )
        now = datetime.now()

        result = _build_agent_info(session, now)

        assert result["name"] == "test-agent"
        assert result["status"] == "running"
        assert result["activity"] == "processing files"
        assert result["repo"] == "test-repo"
        assert result["branch"] == "main"
        assert result["human_interactions"] == 7  # 10 - 3
        assert result["robot_steers"] == 3
        assert result["tokens_raw"] == 7000
        assert result["cost_usd"] == 0.50

    def test_calculates_percent_active(self):
        """Should calculate percent active correctly."""
        from overcode.web_api import _build_agent_info
        from overcode.monitor_daemon_state import SessionDaemonState
        from datetime import datetime

        session = SessionDaemonState(
            session_id="test",
            name="agent",
            current_status="waiting_user",
            green_time_seconds=750.0,
            non_green_time_seconds=250.0,
        )
        now = datetime.now()

        result = _build_agent_info(session, now)

        # 750 / (750+250) = 75%
        assert result["percent_active"] == 75

    def test_handles_zero_total_time(self):
        """Should handle zero total time gracefully."""
        from overcode.web_api import _build_agent_info
        from overcode.monitor_daemon_state import SessionDaemonState
        from datetime import datetime

        session = SessionDaemonState(
            session_id="test",
            name="agent",
            current_status="running",
            green_time_seconds=0,
            non_green_time_seconds=0,
        )
        now = datetime.now()

        result = _build_agent_info(session, now)

        assert result["percent_active"] == 0

    def test_permissiveness_mode_emoji(self):
        """Should return correct emoji for permissiveness mode."""
        from overcode.web_api import _build_agent_info
        from overcode.monitor_daemon_state import SessionDaemonState
        from datetime import datetime

        now = datetime.now()

        # Normal mode
        session = SessionDaemonState(
            session_id="test",
            name="agent",
            current_status="running",
            permissiveness_mode="normal",
        )
        result = _build_agent_info(session, now)
        assert result["perm_emoji"] == "👮"

        # Bypass mode
        session.permissiveness_mode = "bypass"
        result = _build_agent_info(session, now)
        assert result["perm_emoji"] == "🔥"

        # Permissive mode
        session.permissiveness_mode = "permissive"
        result = _build_agent_info(session, now)
        assert result["perm_emoji"] == "🏃"


class TestGetStatusData:
    """Tests for get_status_data function."""

    def test_returns_basic_structure(self):
        """Should return dict with expected structure."""
        from overcode.web_api import get_status_data

        with patch('overcode.web_api.get_monitor_daemon_state') as mock_state:
            mock_state.return_value = None

            result = get_status_data("test-session")

            assert "timestamp" in result
            assert "daemon" in result
            assert "presence" in result
            assert "summary" in result
            assert "agents" in result
            assert isinstance(result["agents"], list)

    def test_includes_agents_when_state_exists(self):
        """Should include agent data when state exists."""
        from overcode.web_api import get_status_data
        from overcode.monitor_daemon_state import MonitorDaemonState, SessionDaemonState
        from datetime import datetime

        with patch('overcode.web_api.get_monitor_daemon_state') as mock_get_state:
            state = MonitorDaemonState(
                sessions=[
                    SessionDaemonState(session_id="1", name="agent1", current_status="running"),
                    SessionDaemonState(session_id="2", name="agent2", current_status="waiting_user"),
                ]
            )
            state.last_loop_time = datetime.now().isoformat()
            # Add missing summarizer attributes (these should exist but don't - potential bug)
            state.summarizer_enabled = False
            state.summarizer_available = False
            state.summarizer_calls = 0
            state.summarizer_cost_usd = 0.0
            mock_get_state.return_value = state

            result = get_status_data("test-session")

            assert len(result["agents"]) == 2
            assert result["agents"][0]["name"] == "agent1"
            assert result["agents"][1]["name"] == "agent2"


class TestGetTimelineData:
    """Tests for get_timeline_data function."""

    def test_returns_basic_structure(self):
        """Should return dict with expected structure."""
        from overcode.web_api import get_timeline_data

        with patch('overcode.web_api.read_agent_status_history') as mock_history:
            mock_history.return_value = []

            result = get_timeline_data("test-session", hours=3.0, slots=60)

            assert result["hours"] == 3.0
            assert result["slot_count"] == 60
            assert "agents" in result
            assert "status_chars" in result
            assert "status_colors" in result

    def test_groups_history_by_agent(self):
        """Should group history by agent name."""
        from overcode.web_api import get_timeline_data
        from datetime import datetime, timedelta

        now = datetime.now()

        with patch('overcode.web_api.read_agent_status_history') as mock_history:
            mock_history.return_value = [
                (now - timedelta(minutes=30), "agent1", "running", ""),
                (now - timedelta(minutes=20), "agent2", "waiting_user", ""),
                (now - timedelta(minutes=10), "agent1", "waiting_user", ""),
            ]

            result = get_timeline_data("test-session", hours=1.0, slots=10)

            # Should have both agents
            assert "agent1" in result["agents"]
            assert "agent2" in result["agents"]

    def test_timeline_slot_content_has_expected_fields(self):
        """Each slot should have index, status, char, and color."""
        from overcode.web_api import get_timeline_data
        from datetime import datetime, timedelta

        now = datetime.now()

        with patch('overcode.web_api.read_agent_status_history') as mock_history, \
             patch('overcode.web_api.get_agent_history_path') as mock_path:
            mock_path.return_value = "/fake/path"
            mock_history.return_value = [
                (now - timedelta(minutes=30), "agent1", "running", "working"),
            ]

            result = get_timeline_data("test-session", hours=1.0, slots=10)

            agent_data = result["agents"]["agent1"]
            assert "slots" in agent_data
            assert "percent_green" in agent_data
            # There should be at least one slot populated
            assert len(agent_data["slots"]) > 0
            slot = agent_data["slots"][0]
            assert "index" in slot
            assert "status" in slot
            assert "char" in slot
            assert "color" in slot

    def test_timeline_slot_running_status_is_green(self):
        """Slots with 'running' status should be counted as green."""
        from overcode.web_api import get_timeline_data
        from datetime import datetime, timedelta

        now = datetime.now()

        with patch('overcode.web_api.read_agent_status_history') as mock_history, \
             patch('overcode.web_api.get_agent_history_path') as mock_path:
            mock_path.return_value = "/fake/path"
            # Agent running for the entire hour
            mock_history.return_value = [
                (now - timedelta(minutes=59), "agent1", "running", "working"),
            ]

            result = get_timeline_data("test-session", hours=1.0, slots=10)

            agent_data = result["agents"]["agent1"]
            # All populated slots should be "running"
            for slot in agent_data["slots"]:
                assert slot["status"] == "running"
            assert agent_data["percent_green"] == 100

    def test_timeline_slot_waiting_status_not_green(self):
        """Slots with 'waiting_user' status should not be counted as green."""
        from overcode.web_api import get_timeline_data
        from datetime import datetime, timedelta

        now = datetime.now()

        with patch('overcode.web_api.read_agent_status_history') as mock_history, \
             patch('overcode.web_api.get_agent_history_path') as mock_path:
            mock_path.return_value = "/fake/path"
            # Agent waiting the entire hour
            mock_history.return_value = [
                (now - timedelta(minutes=59), "agent1", "waiting_user", "blocked"),
            ]

            result = get_timeline_data("test-session", hours=1.0, slots=10)

            agent_data = result["agents"]["agent1"]
            # All populated slots should be "waiting_user"
            for slot in agent_data["slots"]:
                assert slot["status"] == "waiting_user"
            assert agent_data["percent_green"] == 0


class TestCalculatePercentiles:
    """Tests for _calculate_percentiles function."""

    def test_empty_list_returns_all_zeros(self):
        """Should return all zeros for empty input."""
        from overcode.web_api import _calculate_percentiles

        result = _calculate_percentiles([])

        assert result["mean"] == 0.0
        assert result["median"] == 0.0
        assert result["p5"] == 0.0
        assert result["p95"] == 0.0
        assert result["min"] == 0.0
        assert result["max"] == 0.0

    def test_single_value(self):
        """Should return the single value for all percentiles."""
        from overcode.web_api import _calculate_percentiles

        result = _calculate_percentiles([42.0])

        assert result["mean"] == 42.0
        assert result["median"] == 42.0
        assert result["p5"] == 42.0
        assert result["p95"] == 42.0
        assert result["min"] == 42.0
        assert result["max"] == 42.0

    def test_symmetric_distribution(self):
        """Should calculate correct stats for a symmetric distribution."""
        from overcode.web_api import _calculate_percentiles

        values = [10.0, 20.0, 30.0, 40.0, 50.0]
        result = _calculate_percentiles(values)

        assert result["mean"] == 30.0
        assert result["median"] == 30.0
        assert result["min"] == 10.0
        assert result["max"] == 50.0

    def test_unsorted_input(self):
        """Should handle unsorted input correctly."""
        from overcode.web_api import _calculate_percentiles

        values = [50.0, 10.0, 30.0, 20.0, 40.0]
        result = _calculate_percentiles(values)

        assert result["min"] == 10.0
        assert result["max"] == 50.0
        assert result["mean"] == 30.0

    def test_large_list_p5_p95(self):
        """Should calculate p5 and p95 on a larger list."""
        from overcode.web_api import _calculate_percentiles

        # 100 values from 1 to 100
        values = [float(i) for i in range(1, 101)]
        result = _calculate_percentiles(values)

        assert result["min"] == 1.0
        assert result["max"] == 100.0
        assert result["mean"] == 50.5
        assert result["median"] == 50.0  # index 49 -> value 50
        # p5: index int(0.05 * 99) = 4 -> value 5
        assert result["p5"] == 5.0
        # p95: index int(0.95 * 99) = 94 -> value 95
        assert result["p95"] == 95.0

    def test_values_are_rounded(self):
        """Should round results to 1 decimal place."""
        from overcode.web_api import _calculate_percentiles

        values = [1.123, 2.456, 3.789]
        result = _calculate_percentiles(values)

        # Check all values are rounded to 1 decimal
        assert result["mean"] == round(sum(values) / len(values), 1)
        assert result["min"] == 1.1
        assert result["max"] == 3.8


class TestGetHealthData:
    """Tests for get_health_data function."""

    def test_returns_ok_status(self):
        """Should return status 'ok'."""
        from overcode.web_api import get_health_data

        result = get_health_data()

        assert result["status"] == "ok"

    def test_returns_timestamp(self):
        """Should include an ISO format timestamp."""
        from overcode.web_api import get_health_data

        result = get_health_data()

        assert "timestamp" in result
        # Verify it's a valid ISO timestamp
        datetime.fromisoformat(result["timestamp"])

    def test_returns_only_expected_keys(self):
        """Should return only status and timestamp."""
        from overcode.web_api import get_health_data

        result = get_health_data()

        assert set(result.keys()) == {"status", "timestamp"}


class TestGetAnalyticsDaily:
    """Tests for get_analytics_daily function."""

    def test_empty_sessions_returns_empty_days(self):
        """Should return empty days list when no sessions."""
        from overcode.web_api import get_analytics_daily

        with patch('overcode.web_api.get_analytics_sessions') as mock_sessions:
            mock_sessions.return_value = {
                'sessions': [],
                'summary': {
                    'session_count': 0,
                    'total_tokens': 0,
                    'total_cost_usd': 0,
                    'total_green_time_seconds': 0,
                    'total_non_green_time_seconds': 0,
                    'avg_green_percent': 0,
                },
            }

            result = get_analytics_daily()

            assert result["days"] == []
            assert result["labels"] == []

    def test_groups_sessions_by_date(self):
        """Should group sessions by date and aggregate stats."""
        from overcode.web_api import get_analytics_daily

        with patch('overcode.web_api.get_analytics_sessions') as mock_sessions:
            mock_sessions.return_value = {
                'sessions': [
                    {
                        'start_time': '2024-01-15T10:00:00',
                        'total_tokens': 1000,
                        'estimated_cost_usd': 0.50,
                        'green_time_seconds': 3600.0,
                        'non_green_time_seconds': 600.0,
                        'interaction_count': 5,
                        'steers_count': 1,
                    },
                    {
                        'start_time': '2024-01-15T14:00:00',
                        'total_tokens': 2000,
                        'estimated_cost_usd': 1.00,
                        'green_time_seconds': 1800.0,
                        'non_green_time_seconds': 200.0,
                        'interaction_count': 3,
                        'steers_count': 0,
                    },
                    {
                        'start_time': '2024-01-16T09:00:00',
                        'total_tokens': 500,
                        'estimated_cost_usd': 0.25,
                        'green_time_seconds': 900.0,
                        'non_green_time_seconds': 100.0,
                        'interaction_count': 2,
                        'steers_count': 1,
                    },
                ],
                'summary': {},
            }

            result = get_analytics_daily()

            assert len(result["days"]) == 2
            assert result["labels"] == ["2024-01-15", "2024-01-16"]

            # First day: two sessions aggregated
            day1 = result["days"][0]
            assert day1["date"] == "2024-01-15"
            assert day1["sessions"] == 2
            assert day1["tokens"] == 3000
            assert day1["cost_usd"] == 1.50
            assert day1["green_time_seconds"] == 5400.0
            assert day1["non_green_time_seconds"] == 800.0
            assert day1["interactions"] == 8
            assert day1["steers"] == 1

            # Second day: one session
            day2 = result["days"][1]
            assert day2["date"] == "2024-01-16"
            assert day2["sessions"] == 1
            assert day2["tokens"] == 500

    def test_calculates_green_percent_per_day(self):
        """Should calculate green_percent for each day."""
        from overcode.web_api import get_analytics_daily

        with patch('overcode.web_api.get_analytics_sessions') as mock_sessions:
            mock_sessions.return_value = {
                'sessions': [
                    {
                        'start_time': '2024-01-15T10:00:00',
                        'total_tokens': 100,
                        'estimated_cost_usd': 0.10,
                        'green_time_seconds': 750.0,
                        'non_green_time_seconds': 250.0,
                        'interaction_count': 1,
                        'steers_count': 0,
                    },
                ],
                'summary': {},
            }

            result = get_analytics_daily()

            day = result["days"][0]
            # 750 / (750+250) * 100 = 75.0%
            assert day["green_percent"] == 75.0

    def test_handles_invalid_start_time(self):
        """Should skip sessions with invalid start_time."""
        from overcode.web_api import get_analytics_daily

        with patch('overcode.web_api.get_analytics_sessions') as mock_sessions:
            mock_sessions.return_value = {
                'sessions': [
                    {
                        'start_time': 'not-a-date',
                        'total_tokens': 100,
                        'estimated_cost_usd': 0.10,
                        'green_time_seconds': 100.0,
                        'non_green_time_seconds': 0.0,
                        'interaction_count': 1,
                        'steers_count': 0,
                    },
                    {
                        'start_time': '2024-01-15T10:00:00',
                        'total_tokens': 200,
                        'estimated_cost_usd': 0.20,
                        'green_time_seconds': 200.0,
                        'non_green_time_seconds': 0.0,
                        'interaction_count': 1,
                        'steers_count': 0,
                    },
                ],
                'summary': {},
            }

            result = get_analytics_daily()

            # Only the valid session should be included
            assert len(result["days"]) == 1
            assert result["days"][0]["tokens"] == 200

    def test_passes_time_range_to_get_analytics_sessions(self):
        """Should pass start/end to get_analytics_sessions."""
        from overcode.web_api import get_analytics_daily

        start = datetime(2024, 1, 1)
        end = datetime(2024, 1, 31)

        with patch('overcode.web_api.get_analytics_sessions') as mock_sessions:
            mock_sessions.return_value = {'sessions': [], 'summary': {}}

            get_analytics_daily(start=start, end=end)

            mock_sessions.assert_called_once_with(start, end)


class TestGetAnalyticsSessions:
    """Tests for get_analytics_sessions function."""

    def test_returns_empty_when_no_sessions(self):
        """Should return empty sessions list and zero summary."""
        from overcode.web_api import get_analytics_sessions

        with patch('overcode.session_manager.SessionManager') as MockSM:
            mgr = MockSM.return_value
            mgr.list_sessions.return_value = []
            mgr.list_archived_sessions.return_value = []

            result = get_analytics_sessions()

            assert result["sessions"] == []
            assert result["summary"]["session_count"] == 0
            assert result["summary"]["total_tokens"] == 0
            assert result["summary"]["total_cost_usd"] == 0

    def test_includes_active_and_archived_sessions(self):
        """Should include both active and archived sessions."""
        from overcode.web_api import get_analytics_sessions
        from dataclasses import dataclass, field

        @dataclass
        class FakeStats:
            interaction_count: int = 5
            steers_count: int = 1
            total_tokens: int = 1000
            input_tokens: int = 700
            output_tokens: int = 300
            cache_creation_tokens: int = 0
            cache_read_tokens: int = 0
            estimated_cost_usd: float = 0.50
            green_time_seconds: float = 3600.0
            non_green_time_seconds: float = 600.0

        @dataclass
        class FakeSession:
            id: str = "sess-1"
            name: str = "agent1"
            start_time: str = "2024-01-15T10:00:00"
            repo_name: str = "test-repo"
            branch: str = "main"
            stats: FakeStats = field(default_factory=FakeStats)

        active = FakeSession(id="sess-1", name="active-agent")
        archived = FakeSession(id="sess-2", name="archived-agent")

        with patch('overcode.session_manager.SessionManager') as MockSM, \
             patch('overcode.history_reader.get_session_stats') as mock_stats:
            mgr = MockSM.return_value
            mgr.list_sessions.return_value = [active]
            mgr.list_archived_sessions.return_value = [archived]
            mock_stats.return_value = None

            result = get_analytics_sessions()

            assert result["summary"]["session_count"] == 2
            names = [s["name"] for s in result["sessions"]]
            assert "active-agent" in names
            assert "archived-agent" in names

    def test_filters_by_start_time(self):
        """Should filter sessions by start time range."""
        from overcode.web_api import get_analytics_sessions
        from dataclasses import dataclass, field

        @dataclass
        class FakeStats:
            interaction_count: int = 0
            steers_count: int = 0
            total_tokens: int = 100
            input_tokens: int = 70
            output_tokens: int = 30
            cache_creation_tokens: int = 0
            cache_read_tokens: int = 0
            estimated_cost_usd: float = 0.10
            green_time_seconds: float = 100.0
            non_green_time_seconds: float = 0.0

        @dataclass
        class FakeSession:
            id: str = "sess-1"
            name: str = "agent1"
            start_time: str = "2024-01-15T10:00:00"
            repo_name: str = "test-repo"
            branch: str = "main"
            stats: FakeStats = field(default_factory=FakeStats)

        early = FakeSession(id="s1", name="early", start_time="2024-01-10T10:00:00")
        middle = FakeSession(id="s2", name="middle", start_time="2024-01-15T10:00:00")
        late = FakeSession(id="s3", name="late", start_time="2024-01-20T10:00:00")

        with patch('overcode.session_manager.SessionManager') as MockSM, \
             patch('overcode.history_reader.get_session_stats') as mock_stats:
            mgr = MockSM.return_value
            mgr.list_sessions.return_value = [early, middle, late]
            mgr.list_archived_sessions.return_value = []
            mock_stats.return_value = None

            # Filter: only sessions between Jan 12 and Jan 18
            start = datetime(2024, 1, 12)
            end = datetime(2024, 1, 18)
            result = get_analytics_sessions(start=start, end=end)

            assert result["summary"]["session_count"] == 1
            assert result["sessions"][0]["name"] == "middle"

    def test_calculates_summary_stats(self):
        """Should calculate correct summary statistics."""
        from overcode.web_api import get_analytics_sessions
        from dataclasses import dataclass, field

        @dataclass
        class FakeStats:
            interaction_count: int = 0
            steers_count: int = 0
            total_tokens: int = 500
            input_tokens: int = 300
            output_tokens: int = 200
            cache_creation_tokens: int = 0
            cache_read_tokens: int = 0
            estimated_cost_usd: float = 0.25
            green_time_seconds: float = 900.0
            non_green_time_seconds: float = 100.0

        @dataclass
        class FakeSession:
            id: str = "sess-1"
            name: str = "agent1"
            start_time: str = "2024-01-15T10:00:00"
            repo_name: str = "test-repo"
            branch: str = "main"
            stats: FakeStats = field(default_factory=FakeStats)

        s1 = FakeSession(id="s1", name="a1", stats=FakeStats(total_tokens=1000, estimated_cost_usd=1.00, green_time_seconds=3600.0, non_green_time_seconds=400.0))
        s2 = FakeSession(id="s2", name="a2", stats=FakeStats(total_tokens=2000, estimated_cost_usd=2.00, green_time_seconds=1800.0, non_green_time_seconds=200.0))

        with patch('overcode.session_manager.SessionManager') as MockSM, \
             patch('overcode.history_reader.get_session_stats') as mock_stats:
            mgr = MockSM.return_value
            mgr.list_sessions.return_value = [s1, s2]
            mgr.list_archived_sessions.return_value = []
            mock_stats.return_value = None

            result = get_analytics_sessions()

            summary = result["summary"]
            assert summary["total_tokens"] == 3000
            assert summary["total_cost_usd"] == 3.00
            assert summary["total_green_time_seconds"] == 5400.0
            assert summary["total_non_green_time_seconds"] == 600.0
            # 5400 / 6000 * 100 = 90.0
            assert summary["avg_green_percent"] == 90.0

    def test_sessions_sorted_newest_first(self):
        """Should sort sessions by start_time descending."""
        from overcode.web_api import get_analytics_sessions
        from dataclasses import dataclass, field

        @dataclass
        class FakeStats:
            interaction_count: int = 0
            steers_count: int = 0
            total_tokens: int = 100
            input_tokens: int = 70
            output_tokens: int = 30
            cache_creation_tokens: int = 0
            cache_read_tokens: int = 0
            estimated_cost_usd: float = 0.10
            green_time_seconds: float = 100.0
            non_green_time_seconds: float = 0.0

        @dataclass
        class FakeSession:
            id: str = "sess-1"
            name: str = "agent1"
            start_time: str = "2024-01-15T10:00:00"
            repo_name: str = "test-repo"
            branch: str = "main"
            stats: FakeStats = field(default_factory=FakeStats)

        s1 = FakeSession(id="s1", name="oldest", start_time="2024-01-10T10:00:00")
        s2 = FakeSession(id="s2", name="newest", start_time="2024-01-20T10:00:00")
        s3 = FakeSession(id="s3", name="middle", start_time="2024-01-15T10:00:00")

        with patch('overcode.session_manager.SessionManager') as MockSM, \
             patch('overcode.history_reader.get_session_stats') as mock_stats:
            mgr = MockSM.return_value
            mgr.list_sessions.return_value = [s1, s2, s3]
            mgr.list_archived_sessions.return_value = []
            mock_stats.return_value = None

            result = get_analytics_sessions()

            names = [s["name"] for s in result["sessions"]]
            assert names == ["newest", "middle", "oldest"]


class TestSessionToAnalyticsRecord:
    """Tests for _session_to_analytics_record function."""

    def test_converts_session_to_record(self):
        """Should convert a Session object to an analytics dict."""
        from overcode.web_api import _session_to_analytics_record

        mock_session = MagicMock()
        mock_session.id = "test-id"
        mock_session.name = "test-agent"
        mock_session.start_time = "2024-01-15T10:00:00"
        mock_session.repo_name = "my-repo"
        mock_session.branch = "main"

        mock_stats = MagicMock()
        mock_stats.interaction_count = 10
        mock_stats.steers_count = 2
        mock_stats.total_tokens = 5000
        mock_stats.input_tokens = 3000
        mock_stats.output_tokens = 2000
        mock_stats.cache_creation_tokens = 100
        mock_stats.cache_read_tokens = 500
        mock_stats.estimated_cost_usd = 1.2345
        mock_stats.green_time_seconds = 3600.0
        mock_stats.non_green_time_seconds = 400.0
        mock_session.stats = mock_stats

        result = _session_to_analytics_record(mock_session, is_archived=False)

        assert result["id"] == "test-id"
        assert result["name"] == "test-agent"
        assert result["start_time"] == "2024-01-15T10:00:00"
        assert result["repo_name"] == "my-repo"
        assert result["branch"] == "main"
        assert result["is_archived"] is False
        assert result["interaction_count"] == 10
        assert result["steers_count"] == 2
        assert result["total_tokens"] == 5000
        assert result["estimated_cost_usd"] == 1.2345
        assert result["green_time_seconds"] == 3600.0
        assert result["non_green_time_seconds"] == 400.0
        # 3600 / 4000 * 100 = 90.0
        assert result["green_percent"] == 90.0

    def test_archived_flag(self):
        """Should set is_archived correctly."""
        from overcode.web_api import _session_to_analytics_record

        mock_session = MagicMock()
        mock_session.id = "arch-1"
        mock_session.name = "archived"
        mock_session.start_time = "2024-01-15T10:00:00"
        mock_session.repo_name = None
        mock_session.branch = None

        mock_stats = MagicMock()
        mock_stats.green_time_seconds = 0.0
        mock_stats.non_green_time_seconds = 0.0
        mock_stats.estimated_cost_usd = 0.0
        mock_stats.interaction_count = 0
        mock_stats.steers_count = 0
        mock_stats.total_tokens = 0
        mock_stats.input_tokens = 0
        mock_stats.output_tokens = 0
        mock_stats.cache_creation_tokens = 0
        mock_stats.cache_read_tokens = 0
        mock_session.stats = mock_stats

        result = _session_to_analytics_record(mock_session, is_archived=True)

        assert result["is_archived"] is True

    def test_zero_total_time_gives_zero_green_percent(self):
        """Should return 0% green when total time is zero."""
        from overcode.web_api import _session_to_analytics_record

        mock_session = MagicMock()
        mock_session.id = "z"
        mock_session.name = "zero"
        mock_session.start_time = "2024-01-15T10:00:00"
        mock_session.repo_name = None
        mock_session.branch = None

        mock_stats = MagicMock()
        mock_stats.green_time_seconds = 0.0
        mock_stats.non_green_time_seconds = 0.0
        mock_stats.estimated_cost_usd = 0.0
        mock_stats.interaction_count = 0
        mock_stats.steers_count = 0
        mock_stats.total_tokens = 0
        mock_stats.input_tokens = 0
        mock_stats.output_tokens = 0
        mock_stats.cache_creation_tokens = 0
        mock_stats.cache_read_tokens = 0
        mock_session.stats = mock_stats

        result = _session_to_analytics_record(mock_session, is_archived=False)

        assert result["green_percent"] == 0

    def test_record_has_empty_work_times_by_default(self):
        """Should have empty work_times and zero median_work_time."""
        from overcode.web_api import _session_to_analytics_record

        mock_session = MagicMock()
        mock_session.id = "t"
        mock_session.name = "test"
        mock_session.start_time = "2024-01-15T10:00:00"
        mock_session.repo_name = None
        mock_session.branch = None

        mock_stats = MagicMock()
        mock_stats.green_time_seconds = 100.0
        mock_stats.non_green_time_seconds = 0.0
        mock_stats.estimated_cost_usd = 0.0
        mock_stats.interaction_count = 0
        mock_stats.steers_count = 0
        mock_stats.total_tokens = 0
        mock_stats.input_tokens = 0
        mock_stats.output_tokens = 0
        mock_stats.cache_creation_tokens = 0
        mock_stats.cache_read_tokens = 0
        mock_session.stats = mock_stats

        result = _session_to_analytics_record(mock_session, is_archived=False)

        assert result["work_times"] == []
        assert result["median_work_time"] == 0.0


class TestGetRawTimelineData:
    """Tests for get_raw_timeline_data function."""

    def test_returns_basic_structure(self):
        """Should return dict with hours and agents."""
        from overcode.web_api import get_raw_timeline_data

        with patch('overcode.web_api.read_agent_status_history') as mock_history:
            mock_history.return_value = []

            result = get_raw_timeline_data("test-session", hours=3.0)

            assert result["hours"] == 3.0
            assert result["agents"] == {}

    def test_groups_entries_by_agent(self):
        """Should group raw entries by agent name."""
        from overcode.web_api import get_raw_timeline_data

        now = datetime.now()

        with patch('overcode.web_api.read_agent_status_history') as mock_history:
            mock_history.return_value = [
                (now - timedelta(minutes=30), "agent1", "running", "coding"),
                (now - timedelta(minutes=20), "agent2", "waiting_user", "blocked"),
                (now - timedelta(minutes=10), "agent1", "waiting_user", "stuck"),
            ]

            result = get_raw_timeline_data("test-session", hours=1.0)

            assert "agent1" in result["agents"]
            assert "agent2" in result["agents"]
            assert len(result["agents"]["agent1"]) == 2
            assert len(result["agents"]["agent2"]) == 1

    def test_entries_have_timestamp_and_status(self):
        """Each entry should have 't' (ISO timestamp) and 's' (status)."""
        from overcode.web_api import get_raw_timeline_data

        now = datetime.now()

        with patch('overcode.web_api.read_agent_status_history') as mock_history:
            mock_history.return_value = [
                (now, "agent1", "running", "working"),
            ]

            result = get_raw_timeline_data("test-session")

            entry = result["agents"]["agent1"][0]
            assert "t" in entry
            assert "s" in entry
            assert entry["s"] == "running"
            # Verify 't' is a valid ISO timestamp
            datetime.fromisoformat(entry["t"])

    def test_uses_session_specific_history_path(self):
        """Should pass the correct session history path to reader."""
        from overcode.web_api import get_raw_timeline_data

        with patch('overcode.web_api.read_agent_status_history') as mock_history, \
             patch('overcode.web_api.get_agent_history_path') as mock_path:
            mock_path.return_value = "/fake/session/path"
            mock_history.return_value = []

            get_raw_timeline_data("my-session", hours=6.0)

            mock_path.assert_called_once_with("my-session")
            mock_history.assert_called_once_with(hours=6.0, history_file="/fake/session/path")
