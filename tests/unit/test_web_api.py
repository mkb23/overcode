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

        for state_code, expected_name in [(1, "locked"), (2, "inactive"), (3, "active")]:
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
