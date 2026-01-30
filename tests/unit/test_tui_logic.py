"""
Unit tests for TUI logic functions.

These tests verify the pure business logic functions that handle
sorting, filtering, and calculations for the TUI.
"""

import pytest
from unittest.mock import Mock
from dataclasses import dataclass
from typing import Optional

from overcode.tui_logic import (
    sort_sessions_alphabetical,
    sort_sessions_by_status,
    sort_sessions_by_value,
    sort_sessions,
    filter_visible_sessions,
    get_sort_mode_display_name,
    cycle_sort_mode,
    calculate_spin_stats,
    calculate_green_percentage,
    calculate_human_interaction_count,
    SpinStats,
    STATUS_ORDER_BY_ATTENTION,
    STATUS_ORDER_BY_VALUE,
)


def make_session(name: str, session_id: str = None, is_asleep: bool = False):
    """Create a mock session for testing."""
    session = Mock()
    session.name = name
    session.id = session_id or name
    session.is_asleep = is_asleep
    return session


def make_session_with_stats(
    name: str,
    current_state: str = "running",
    agent_value: float = 1.0,
    is_asleep: bool = False,
):
    """Create a mock session with stats for sorting tests."""
    session = make_session(name, is_asleep=is_asleep)
    session.stats = Mock()
    session.stats.current_state = current_state
    session.agent_value = agent_value
    return session


def make_daemon_session(
    session_id: str,
    current_status: str = "running",
    green_time: float = 100.0,
    non_green_time: float = 0.0,
    input_tokens: int = 1000,
    output_tokens: int = 500,
):
    """Create a mock daemon session state for spin stats tests."""
    session = Mock()
    session.session_id = session_id
    session.current_status = current_status
    session.green_time_seconds = green_time
    session.non_green_time_seconds = non_green_time
    session.input_tokens = input_tokens
    session.output_tokens = output_tokens
    return session


class TestSortSessionsAlphabetical:
    """Tests for alphabetical sorting."""

    def test_sorts_by_name(self):
        """Should sort sessions alphabetically by name."""
        sessions = [
            make_session("charlie"),
            make_session("alpha"),
            make_session("bravo"),
        ]

        result = sort_sessions_alphabetical(sessions)

        assert [s.name for s in result] == ["alpha", "bravo", "charlie"]

    def test_case_insensitive(self):
        """Should sort case-insensitively."""
        sessions = [
            make_session("Charlie"),
            make_session("alpha"),
            make_session("BRAVO"),
        ]

        result = sort_sessions_alphabetical(sessions)

        assert [s.name for s in result] == ["alpha", "BRAVO", "Charlie"]

    def test_does_not_mutate_input(self):
        """Should return new list, not mutate input."""
        sessions = [make_session("b"), make_session("a")]
        original_order = [s.name for s in sessions]

        result = sort_sessions_alphabetical(sessions)

        assert [s.name for s in sessions] == original_order  # Original unchanged
        assert result is not sessions  # New list

    def test_empty_list(self):
        """Should handle empty list."""
        result = sort_sessions_alphabetical([])
        assert result == []


class TestSortSessionsByStatus:
    """Tests for status-based sorting."""

    def test_waiting_user_first(self):
        """Waiting user sessions should sort first."""
        sessions = [
            make_session_with_stats("a", "running"),
            make_session_with_stats("b", "waiting_user"),
        ]

        result = sort_sessions_by_status(sessions)

        assert result[0].name == "b"  # waiting_user first

    def test_full_priority_order(self):
        """Should respect full priority order."""
        sessions = [
            make_session_with_stats("g", "asleep"),
            make_session_with_stats("a", "waiting_user"),
            make_session_with_stats("e", "running"),
            make_session_with_stats("c", "no_instructions"),
            make_session_with_stats("b", "waiting_supervisor"),
            make_session_with_stats("f", "terminated"),
            make_session_with_stats("d", "error"),
        ]

        result = sort_sessions_by_status(sessions)

        expected_order = ["a", "b", "c", "d", "e", "f", "g"]
        assert [s.name for s in result] == expected_order

    def test_alphabetical_within_same_status(self):
        """Sessions with same status should sort alphabetically."""
        sessions = [
            make_session_with_stats("charlie", "running"),
            make_session_with_stats("alpha", "running"),
            make_session_with_stats("bravo", "running"),
        ]

        result = sort_sessions_by_status(sessions)

        assert [s.name for s in result] == ["alpha", "bravo", "charlie"]

    def test_handles_none_state(self):
        """Should handle None state as running."""
        sessions = [
            make_session_with_stats("a", None),
            make_session_with_stats("b", "waiting_user"),
        ]

        result = sort_sessions_by_status(sessions)

        assert result[0].name == "b"  # waiting_user before None (treated as running)


class TestSortSessionsByValue:
    """Tests for value-based sorting."""

    def test_non_green_before_green(self):
        """Non-green sessions should sort before green ones."""
        sessions = [
            make_session_with_stats("green", "running", agent_value=100),
            make_session_with_stats("waiting", "waiting_user", agent_value=10),
        ]

        result = sort_sessions_by_value(sessions)

        assert result[0].name == "waiting"

    def test_higher_value_first_within_group(self):
        """Within same status group, higher value should sort first."""
        sessions = [
            make_session_with_stats("low", "running", agent_value=10),
            make_session_with_stats("high", "running", agent_value=100),
            make_session_with_stats("medium", "running", agent_value=50),
        ]

        result = sort_sessions_by_value(sessions)

        assert [s.name for s in result] == ["high", "medium", "low"]

    def test_alphabetical_tie_breaker(self):
        """Same status and value should sort alphabetically."""
        sessions = [
            make_session_with_stats("charlie", "running", agent_value=50),
            make_session_with_stats("alpha", "running", agent_value=50),
        ]

        result = sort_sessions_by_value(sessions)

        assert [s.name for s in result] == ["alpha", "charlie"]


class TestSortSessions:
    """Tests for the unified sort_sessions function."""

    def test_alphabetical_mode(self):
        """Should use alphabetical sorting for 'alphabetical' mode."""
        sessions = [make_session("b"), make_session("a")]

        result = sort_sessions(sessions, "alphabetical")

        assert [s.name for s in result] == ["a", "b"]

    def test_by_status_mode(self):
        """Should use status sorting for 'by_status' mode."""
        sessions = [
            make_session_with_stats("a", "running"),
            make_session_with_stats("b", "waiting_user"),
        ]

        result = sort_sessions(sessions, "by_status")

        assert result[0].name == "b"

    def test_by_value_mode(self):
        """Should use value sorting for 'by_value' mode."""
        sessions = [
            make_session_with_stats("low", "running", agent_value=10),
            make_session_with_stats("high", "running", agent_value=100),
        ]

        result = sort_sessions(sessions, "by_value")

        assert result[0].name == "high"

    def test_unknown_mode_defaults_to_alphabetical(self):
        """Unknown mode should default to alphabetical."""
        sessions = [make_session("b"), make_session("a")]

        result = sort_sessions(sessions, "unknown_mode")

        assert [s.name for s in result] == ["a", "b"]


class TestFilterVisibleSessions:
    """Tests for session visibility filtering."""

    def test_returns_all_active_by_default(self):
        """Should return all active sessions by default."""
        active = [make_session("a"), make_session("b")]
        terminated = [make_session("c")]

        result = filter_visible_sessions(
            active, terminated, hide_asleep=False, show_terminated=False
        )

        assert len(result) == 2
        assert {s.name for s in result} == {"a", "b"}

    def test_filters_out_asleep_when_hide_asleep(self):
        """Should filter sleeping sessions when hide_asleep is True."""
        active = [
            make_session("awake", is_asleep=False),
            make_session("sleeping", is_asleep=True),
        ]

        result = filter_visible_sessions(
            active, [], hide_asleep=True, show_terminated=False
        )

        assert len(result) == 1
        assert result[0].name == "awake"

    def test_includes_terminated_when_show_terminated(self):
        """Should include terminated sessions when show_terminated is True."""
        active = [make_session("active", session_id="1")]
        terminated = [make_session("killed", session_id="2")]

        result = filter_visible_sessions(
            active, terminated, hide_asleep=False, show_terminated=True
        )

        assert len(result) == 2
        assert {s.name for s in result} == {"active", "killed"}

    def test_does_not_duplicate_sessions(self):
        """Should not duplicate if same session in both lists."""
        session = make_session("same", session_id="1")
        active = [session]
        terminated = [session]

        result = filter_visible_sessions(
            active, terminated, hide_asleep=False, show_terminated=True
        )

        assert len(result) == 1

    def test_does_not_mutate_inputs(self):
        """Should not mutate input lists."""
        active = [make_session("a")]
        terminated = [make_session("b")]
        active_len = len(active)
        terminated_len = len(terminated)

        filter_visible_sessions(active, terminated, False, True)

        assert len(active) == active_len
        assert len(terminated) == terminated_len


class TestGetSortModeDisplayName:
    """Tests for sort mode display names."""

    def test_alphabetical_name(self):
        assert get_sort_mode_display_name("alphabetical") == "Alphabetical"

    def test_by_status_name(self):
        assert get_sort_mode_display_name("by_status") == "By Status"

    def test_by_value_name(self):
        assert get_sort_mode_display_name("by_value") == "By Value (priority)"

    def test_unknown_returns_original(self):
        assert get_sort_mode_display_name("custom") == "custom"


class TestCycleSortMode:
    """Tests for sort mode cycling."""

    def test_cycles_to_next(self):
        """Should cycle to next mode."""
        modes = ["a", "b", "c"]
        assert cycle_sort_mode("a", modes) == "b"
        assert cycle_sort_mode("b", modes) == "c"

    def test_wraps_around(self):
        """Should wrap around to first mode."""
        modes = ["a", "b", "c"]
        assert cycle_sort_mode("c", modes) == "a"

    def test_unknown_mode_starts_at_first(self):
        """Unknown current mode should start at first."""
        modes = ["a", "b", "c"]
        assert cycle_sort_mode("unknown", modes) == "a"

    def test_empty_modes_returns_current(self):
        """Empty modes list should return current mode."""
        assert cycle_sort_mode("current", []) == "current"


class TestCalculateSpinStats:
    """Tests for spin rate calculations."""

    def test_empty_sessions(self):
        """Should handle empty session list."""
        result = calculate_spin_stats([], set())

        assert result.green_count == 0
        assert result.total_count == 0
        assert result.sleeping_count == 0
        assert result.mean_spin == 0.0
        assert result.total_tokens == 0

    def test_counts_green_sessions(self):
        """Should count running sessions as green."""
        sessions = [
            make_daemon_session("1", "running"),
            make_daemon_session("2", "waiting_user"),
            make_daemon_session("3", "running"),
        ]

        result = calculate_spin_stats(sessions, set())

        assert result.green_count == 2
        assert result.total_count == 3

    def test_excludes_asleep_from_active(self):
        """Should exclude sleeping sessions from active stats."""
        sessions = [
            make_daemon_session("1", "running"),
            make_daemon_session("2", "running"),
        ]

        result = calculate_spin_stats(sessions, asleep_session_ids={"2"})

        assert result.total_count == 1
        assert result.sleeping_count == 1

    def test_includes_all_tokens(self):
        """Should include all tokens, even from sleeping sessions."""
        sessions = [
            make_daemon_session("1", input_tokens=1000, output_tokens=500),
            make_daemon_session("2", input_tokens=2000, output_tokens=1000),
        ]

        result = calculate_spin_stats(sessions, asleep_session_ids={"2"})

        assert result.total_tokens == 4500  # All tokens included

    def test_calculates_mean_spin(self):
        """Should calculate mean spin rate."""
        sessions = [
            make_daemon_session("1", green_time=100, non_green_time=0),  # 100% green
            make_daemon_session("2", green_time=50, non_green_time=50),  # 50% green
        ]

        result = calculate_spin_stats(sessions, set())

        # mean_spin is sum of ratios, not average
        assert result.mean_spin == 1.5  # 1.0 + 0.5


class TestCalculateGreenPercentage:
    """Tests for green percentage calculation."""

    def test_all_green(self):
        """100% green time should return 100."""
        assert calculate_green_percentage(100, 0) == 100.0

    def test_no_green(self):
        """0% green time should return 0."""
        assert calculate_green_percentage(0, 100) == 0.0

    def test_half_green(self):
        """50% green time should return 50."""
        assert calculate_green_percentage(50, 50) == 50.0

    def test_zero_total_time(self):
        """Zero total time should return 0."""
        assert calculate_green_percentage(0, 0) == 0.0


class TestCalculateHumanInteractionCount:
    """Tests for human interaction calculation."""

    def test_subtracts_robot_interactions(self):
        """Should subtract robot from total."""
        assert calculate_human_interaction_count(10, 3) == 7

    def test_none_total_returns_zero(self):
        """None total should return 0."""
        assert calculate_human_interaction_count(None, 5) == 0

    def test_clamps_to_zero(self):
        """Should not return negative values."""
        assert calculate_human_interaction_count(3, 10) == 0


class TestStatusOrderConstants:
    """Tests for status order constants."""

    def test_attention_order_has_all_statuses(self):
        """Status order should have all expected statuses."""
        expected = {"waiting_user", "waiting_supervisor", "no_instructions",
                    "error", "running", "terminated", "asleep"}
        assert set(STATUS_ORDER_BY_ATTENTION.keys()) == expected

    def test_value_order_has_all_statuses(self):
        """Value order should have all expected statuses."""
        expected = {"waiting_user", "waiting_supervisor", "no_instructions",
                    "error", "running", "terminated", "asleep"}
        assert set(STATUS_ORDER_BY_VALUE.keys()) == expected

    def test_waiting_user_highest_priority(self):
        """waiting_user should have highest priority (0) in both orders."""
        assert STATUS_ORDER_BY_ATTENTION["waiting_user"] == 0
        assert STATUS_ORDER_BY_VALUE["waiting_user"] == 0


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
