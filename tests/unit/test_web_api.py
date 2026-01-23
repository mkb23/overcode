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
