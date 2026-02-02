"""
Unit tests for supervisor_daemon_core.py pure functions.

These test pure business logic with no I/O or mocking required.
"""

import pytest

from overcode.supervisor_daemon_core import (
    build_daemon_claude_context,
    filter_non_green_sessions,
    calculate_daemon_claude_run_seconds,
    should_launch_daemon_claude,
    parse_intervention_log_line,
)


class TestBuildDaemonClaudeContext:
    """Tests for build_daemon_claude_context()."""

    def test_empty_sessions_list(self):
        """Should handle empty sessions list."""
        result = build_daemon_claude_context("agents", [])

        assert "TMUX SESSION: agents" in result
        assert "Sessions needing attention: 0" in result
        assert "Your mission" in result

    def test_single_session_with_instructions(self):
        """Should include session with standing instructions."""
        sessions = [{
            "name": "agent-1",
            "tmux_window": 3,
            "current_status": "waiting_user",
            "standing_instructions": "Auto-approve all tests",
            "repo_name": "my-project",
        }]

        result = build_daemon_claude_context("agents", sessions)

        assert "agent-1 (window 3)" in result
        assert "Autopilot: Auto-approve all tests" in result
        assert "Repo: my-project" in result
        assert "Sessions needing attention: 1" in result

    def test_session_without_instructions(self):
        """Should indicate no autopilot when instructions missing."""
        sessions = [{
            "name": "agent-2",
            "tmux_window": 5,
            "current_status": "waiting_user",
            "standing_instructions": None,
        }]

        result = build_daemon_claude_context("agents", sessions)

        assert "No autopilot instructions set" in result

    def test_multiple_sessions(self):
        """Should include all sessions."""
        sessions = [
            {"name": "agent-1", "tmux_window": 1, "current_status": "waiting_user"},
            {"name": "agent-2", "tmux_window": 2, "current_status": "error"},
            {"name": "agent-3", "tmux_window": 3, "current_status": "blocked"},
        ]

        result = build_daemon_claude_context("my-session", sessions)

        assert "agent-1 (window 1)" in result
        assert "agent-2 (window 2)" in result
        assert "agent-3 (window 3)" in result
        assert "Sessions needing attention: 3" in result

    def test_includes_footer_instructions(self):
        """Should include instructions for daemon claude."""
        result = build_daemon_claude_context("agents", [])

        assert "daemon claude skill" in result
        assert "sessions.json" in result


class TestFilterNonGreenSessions:
    """Tests for filter_non_green_sessions()."""

    def test_empty_list(self):
        """Should return empty list for empty input."""
        result = filter_non_green_sessions([])
        assert result == []

    def test_filters_out_running_sessions(self):
        """Should exclude running (green) sessions."""
        sessions = [
            {"name": "green-1", "current_status": "running"},
            {"name": "yellow-1", "current_status": "waiting_user"},
            {"name": "green-2", "current_status": "running"},
        ]

        result = filter_non_green_sessions(sessions)

        assert len(result) == 1
        assert result[0]["name"] == "yellow-1"

    def test_filters_out_excluded_names(self):
        """Should exclude sessions by name."""
        sessions = [
            {"name": "daemon_claude", "current_status": "waiting_user"},
            {"name": "agent-1", "current_status": "waiting_user"},
        ]

        result = filter_non_green_sessions(sessions, exclude_names=["daemon_claude"])

        assert len(result) == 1
        assert result[0]["name"] == "agent-1"

    def test_filters_out_asleep_sessions(self):
        """Should exclude asleep sessions."""
        sessions = [
            {"name": "agent-1", "current_status": "waiting_user", "is_asleep": True},
            {"name": "agent-2", "current_status": "waiting_user", "is_asleep": False},
        ]

        result = filter_non_green_sessions(sessions)

        assert len(result) == 1
        assert result[0]["name"] == "agent-2"

    def test_filters_out_do_nothing_sessions(self):
        """Should exclude sessions with DO_NOTHING standing orders."""
        sessions = [
            {"name": "agent-1", "current_status": "waiting_user",
             "standing_instructions": "DO_NOTHING - on break"},
            {"name": "agent-2", "current_status": "waiting_user",
             "standing_instructions": "Auto-approve tests"},
        ]

        result = filter_non_green_sessions(sessions)

        assert len(result) == 1
        assert result[0]["name"] == "agent-2"

    def test_do_nothing_case_insensitive(self):
        """Should match DO_NOTHING case-insensitively."""
        sessions = [
            {"name": "agent-1", "current_status": "waiting_user",
             "standing_instructions": "do_nothing please"},
        ]

        result = filter_non_green_sessions(sessions)
        assert len(result) == 0

    def test_keeps_sessions_with_other_instructions(self):
        """Should keep sessions with non-DO_NOTHING instructions."""
        sessions = [
            {"name": "agent-1", "current_status": "waiting_user",
             "standing_instructions": "auto-approve all"},
        ]

        result = filter_non_green_sessions(sessions)
        assert len(result) == 1

    def test_handles_missing_fields(self):
        """Should handle sessions with missing optional fields."""
        sessions = [
            {"name": "agent-1", "current_status": "waiting_user"},
            {"name": "agent-2"},  # Missing current_status
        ]

        result = filter_non_green_sessions(sessions)

        # agent-1 should be included, agent-2 too (missing status != running)
        assert len(result) == 2


class TestCalculateDaemonClaudeRunSeconds:
    """Tests for calculate_daemon_claude_run_seconds()."""

    def test_not_running_returns_previous(self):
        """Should return previous total when not currently running."""
        result = calculate_daemon_claude_run_seconds(
            started_at_iso=None,
            now_iso="2025-01-15T10:30:00",
            previous_total=120.0,
        )
        assert result == 120.0

    def test_running_adds_current_duration(self):
        """Should add current run duration to previous total."""
        result = calculate_daemon_claude_run_seconds(
            started_at_iso="2025-01-15T10:00:00",
            now_iso="2025-01-15T10:05:00",
            previous_total=100.0,
        )
        # 5 minutes = 300 seconds + 100 previous = 400
        assert result == 400.0

    def test_handles_invalid_started_at(self):
        """Should return previous total for invalid started_at."""
        result = calculate_daemon_claude_run_seconds(
            started_at_iso="not-a-date",
            now_iso="2025-01-15T10:05:00",
            previous_total=50.0,
        )
        assert result == 50.0

    def test_handles_invalid_now(self):
        """Should return previous total for invalid now."""
        result = calculate_daemon_claude_run_seconds(
            started_at_iso="2025-01-15T10:00:00",
            now_iso="invalid",
            previous_total=75.0,
        )
        assert result == 75.0

    def test_zero_previous_total(self):
        """Should work with zero previous total."""
        result = calculate_daemon_claude_run_seconds(
            started_at_iso="2025-01-15T10:00:00",
            now_iso="2025-01-15T10:01:00",
            previous_total=0.0,
        )
        assert result == 60.0


class TestShouldLaunchDaemonClaude:
    """Tests for should_launch_daemon_claude()."""

    def test_no_sessions_returns_false(self):
        """Should not launch when no sessions need attention."""
        should_launch, reason = should_launch_daemon_claude([], False)

        assert should_launch is False
        assert reason == "no_sessions"

    def test_already_running_returns_false(self):
        """Should not launch when daemon claude already running."""
        sessions = [{"name": "agent-1", "current_status": "waiting_user"}]

        should_launch, reason = should_launch_daemon_claude(sessions, True)

        assert should_launch is False
        assert reason == "already_running"

    def test_all_waiting_user_no_instructions(self):
        """Should not launch when all waiting for user with no instructions."""
        sessions = [
            {"name": "agent-1", "current_status": "waiting_user", "standing_instructions": None},
            {"name": "agent-2", "current_status": "waiting_user", "standing_instructions": ""},
        ]

        should_launch, reason = should_launch_daemon_claude(sessions, False)

        assert should_launch is False
        assert reason == "waiting_user_no_instructions"

    def test_launches_with_instructions(self):
        """Should launch when sessions have standing instructions."""
        sessions = [
            {"name": "agent-1", "current_status": "waiting_user",
             "standing_instructions": "Auto-approve tests"},
        ]

        should_launch, reason = should_launch_daemon_claude(sessions, False)

        assert should_launch is True
        assert reason == "with_instructions"

    def test_launches_for_non_user_blocked(self):
        """Should launch for non-waiting_user states without instructions."""
        sessions = [
            {"name": "agent-1", "current_status": "error", "standing_instructions": None},
        ]

        should_launch, reason = should_launch_daemon_claude(sessions, False)

        assert should_launch is True
        assert reason == "non_user_blocked"

    def test_mixed_statuses_with_instructions(self):
        """Should launch when any session has instructions."""
        sessions = [
            {"name": "agent-1", "current_status": "waiting_user", "standing_instructions": None},
            {"name": "agent-2", "current_status": "waiting_user",
             "standing_instructions": "Auto-approve"},
        ]

        should_launch, reason = should_launch_daemon_claude(sessions, False)

        assert should_launch is True
        assert reason == "with_instructions"


class TestParseInterventionLogLine:
    """Tests for parse_intervention_log_line()."""

    def test_no_match_returns_none(self):
        """Should return None for unrelated lines."""
        result = parse_intervention_log_line(
            line="Some random log message",
            session_names=["agent-1", "agent-2"],
            action_phrases=["approved", "sent"],
            no_action_phrases=["no intervention needed"],
        )
        assert result is None

    def test_detects_approved_action(self):
        """Should detect approved intervention."""
        result = parse_intervention_log_line(
            line="2025-01-15 10:30:00: agent-1 - Tool call approved",
            session_names=["agent-1", "agent-2"],
            action_phrases=["approved", "sent"],
            no_action_phrases=["no intervention needed"],
        )
        assert result == "agent-1"

    def test_detects_sent_action(self):
        """Should detect sent intervention."""
        result = parse_intervention_log_line(
            line="2025-01-15 10:30:00: agent-2 - Sent prompt to window",
            session_names=["agent-1", "agent-2"],
            action_phrases=["approved", "sent"],
            no_action_phrases=["no intervention needed"],
        )
        assert result == "agent-2"

    def test_ignores_no_intervention_needed(self):
        """Should return None for no-intervention lines."""
        result = parse_intervention_log_line(
            line="2025-01-15 10:30:00: agent-1 - No intervention needed",
            session_names=["agent-1"],
            action_phrases=["approved", "sent"],
            no_action_phrases=["no intervention needed"],
        )
        assert result is None

    def test_case_insensitive_action_phrases(self):
        """Should match action phrases case-insensitively."""
        result = parse_intervention_log_line(
            line="agent-1 - APPROVED the request",
            session_names=["agent-1"],
            action_phrases=["approved"],
            no_action_phrases=[],
        )
        assert result == "agent-1"

    def test_session_name_requires_separator(self):
        """Should require ' - ' separator after session name."""
        result = parse_intervention_log_line(
            line="agent-1-approved something",
            session_names=["agent-1"],
            action_phrases=["approved"],
            no_action_phrases=[],
        )
        assert result is None


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
