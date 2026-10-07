"""
Tests for web_api.py - the sister API's data functions.
"""

from datetime import datetime, timedelta
from unittest.mock import patch


# Test session name for all tests
TEST_SESSION = "test-session"


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

    def test_exposes_wrapper_and_sandbox_flags(self):
        """wrapper and sandbox_enabled must be in the API payload for sisters (#451)."""
        from overcode.web_api import _build_agent_info
        from overcode.monitor_daemon_state import SessionDaemonState
        from datetime import datetime

        session = SessionDaemonState(
            session_id="t", name="a", current_status="running",
            wrapper="/path/to/devcontainer.sh",
            sandbox_enabled=True,
        )
        result = _build_agent_info(session, datetime.now())
        assert result["wrapper"] == "/path/to/devcontainer.sh"
        assert result["sandbox_enabled"] is True

    def test_sandbox_unknown_serializes_as_none(self):
        from overcode.web_api import _build_agent_info
        from overcode.monitor_daemon_state import SessionDaemonState
        from datetime import datetime

        session = SessionDaemonState(session_id="t", name="a", current_status="running")
        result = _build_agent_info(session, datetime.now())
        assert result["wrapper"] is None
        assert result["sandbox_enabled"] is None

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

        assert set(result.keys()) == {"status", "timestamp", "version"}


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
            mock_history.assert_called_once_with(
                hours=6.0, history_file="/fake/session/path", carry=True
            )
