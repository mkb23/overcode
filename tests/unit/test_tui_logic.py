"""
Unit tests for TUI logic functions.

These tests verify the pure business logic functions that handle
sorting, filtering, and calculations for the TUI.
"""

import pytest
from unittest.mock import Mock
from dataclasses import dataclass
from typing import Optional

from datetime import datetime, timedelta

from overcode.tui_logic import (
    sort_sessions_alphabetical,
    sort_sessions_by_status,
    sort_sessions_by_value,
    sort_sessions_by_tree,
    sort_sessions,
    filter_visible_sessions,
    get_sort_mode_display_name,
    cycle_sort_mode,
    calculate_spin_stats,
    calculate_mean_spin_from_history,
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
            make_session_with_stats("h", "asleep"),
            make_session_with_stats("a", "waiting_user"),
            make_session_with_stats("f", "running"),
            make_session_with_stats("c", "error"),
            make_session_with_stats("b", "waiting_approval"),
            make_session_with_stats("g", "terminated"),
            make_session_with_stats("d", "running_heartbeat"),
            make_session_with_stats("e", "waiting_heartbeat"),
        ]

        result = sort_sessions_by_status(sessions)

        expected_order = ["a", "b", "c", "d", "e", "f", "g", "h"]
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


class TestCalculateMeanSpinFromHistory:
    """Tests for history-based mean spin calculation."""

    def test_empty_history_returns_zero(self):
        """Empty history should return 0.0 with 0 samples."""
        mean_spin, sample_count = calculate_mean_spin_from_history(
            history=[],
            agent_names=["agent1", "agent2"],
            baseline_minutes=30,
        )
        assert mean_spin == 0.0
        assert sample_count == 0

    def test_zero_baseline_returns_zero(self):
        """baseline_minutes=0 should return 0.0 (instantaneous mode)."""
        now = datetime.now()
        history = [
            (now - timedelta(minutes=5), "agent1", "running", ""),
        ]
        mean_spin, sample_count = calculate_mean_spin_from_history(
            history=history,
            agent_names=["agent1"],
            baseline_minutes=0,
        )
        assert mean_spin == 0.0
        assert sample_count == 0

    def test_all_running_returns_agent_count(self):
        """If all samples are running, mean_spin should equal num_agents."""
        now = datetime.now()
        history = [
            (now - timedelta(minutes=10), "agent1", "running", ""),
            (now - timedelta(minutes=10), "agent2", "running", ""),
            (now - timedelta(minutes=5), "agent1", "running", ""),
            (now - timedelta(minutes=5), "agent2", "running", ""),
        ]
        mean_spin, sample_count = calculate_mean_spin_from_history(
            history=history,
            agent_names=["agent1", "agent2"],
            baseline_minutes=30,
            now=now,
        )
        assert mean_spin == 2.0  # 100% of 2 agents
        assert sample_count == 4

    def test_half_running_returns_half_agents(self):
        """If 50% of samples are running, mean_spin should be 0.5 * num_agents."""
        now = datetime.now()
        history = [
            (now - timedelta(minutes=10), "agent1", "running", ""),
            (now - timedelta(minutes=10), "agent2", "waiting_user", ""),
            (now - timedelta(minutes=5), "agent1", "waiting_user", ""),
            (now - timedelta(minutes=5), "agent2", "running", ""),
        ]
        mean_spin, sample_count = calculate_mean_spin_from_history(
            history=history,
            agent_names=["agent1", "agent2"],
            baseline_minutes=30,
            now=now,
        )
        assert mean_spin == 1.0  # 50% of 2 agents = 1.0
        assert sample_count == 4

    def test_filters_by_agent_names(self):
        """Should only include samples from specified agents."""
        now = datetime.now()
        history = [
            (now - timedelta(minutes=10), "agent1", "running", ""),
            (now - timedelta(minutes=10), "agent2", "running", ""),  # excluded
            (now - timedelta(minutes=5), "agent1", "running", ""),
        ]
        mean_spin, sample_count = calculate_mean_spin_from_history(
            history=history,
            agent_names=["agent1"],  # only agent1
            baseline_minutes=30,
            now=now,
        )
        assert mean_spin == 1.0  # 100% of 1 agent
        assert sample_count == 2

    def test_filters_by_time_window(self):
        """Should only include samples within the baseline window."""
        now = datetime.now()
        history = [
            (now - timedelta(minutes=60), "agent1", "running", ""),  # outside 30m window
            (now - timedelta(minutes=10), "agent1", "waiting_user", ""),  # inside
            (now - timedelta(minutes=5), "agent1", "waiting_user", ""),  # inside
        ]
        mean_spin, sample_count = calculate_mean_spin_from_history(
            history=history,
            agent_names=["agent1"],
            baseline_minutes=30,  # only last 30 minutes
            now=now,
        )
        assert mean_spin == 0.0  # 0% running
        assert sample_count == 2  # only 2 samples in window

    def test_empty_agent_names_returns_zero(self):
        """Empty agent_names list should return 0."""
        now = datetime.now()
        history = [
            (now - timedelta(minutes=5), "agent1", "running", ""),
        ]
        mean_spin, sample_count = calculate_mean_spin_from_history(
            history=history,
            agent_names=[],  # no agents
            baseline_minutes=30,
            now=now,
        )
        assert mean_spin == 0.0
        assert sample_count == 0


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


class TestSortSessionsByTree:
    """Tests for tree hierarchy sorting (#244)."""

    def _make_tree_session(self, name, session_id=None, parent_session_id=None):
        session = make_session(name, session_id=session_id or name)
        session.parent_session_id = parent_session_id
        return session

    def test_roots_sorted_alphabetically(self):
        """Root sessions (no parent) should sort alphabetically."""
        sessions = [
            self._make_tree_session("charlie"),
            self._make_tree_session("alpha"),
            self._make_tree_session("bravo"),
        ]

        result = sort_sessions_by_tree(sessions)

        assert [s.name for s in result] == ["alpha", "bravo", "charlie"]

    def test_children_follow_parent(self):
        """Children should appear immediately after their parent."""
        root = self._make_tree_session("root", session_id="root-id")
        child_a = self._make_tree_session("child-a", session_id="child-a-id", parent_session_id="root-id")
        child_b = self._make_tree_session("child-b", session_id="child-b-id", parent_session_id="root-id")
        other = self._make_tree_session("other", session_id="other-id")

        sessions = [other, child_b, root, child_a]
        result = sort_sessions_by_tree(sessions)

        assert [s.name for s in result] == ["other", "root", "child-a", "child-b"]

    def test_nested_hierarchy(self):
        """Deeply nested hierarchy should be correctly ordered."""
        root = self._make_tree_session("root", session_id="r")
        child = self._make_tree_session("child", session_id="c", parent_session_id="r")
        grandchild = self._make_tree_session("grandchild", session_id="gc", parent_session_id="c")

        sessions = [grandchild, root, child]
        result = sort_sessions_by_tree(sessions)

        assert [s.name for s in result] == ["root", "child", "grandchild"]

    def test_multiple_trees(self):
        """Multiple root trees should sort independently."""
        root_a = self._make_tree_session("alpha-root", session_id="ar")
        child_a = self._make_tree_session("alpha-child", session_id="ac", parent_session_id="ar")
        root_z = self._make_tree_session("zeta-root", session_id="zr")
        child_z = self._make_tree_session("zeta-child", session_id="zc", parent_session_id="zr")

        sessions = [child_z, root_z, child_a, root_a]
        result = sort_sessions_by_tree(sessions)

        assert [s.name for s in result] == [
            "alpha-root", "alpha-child",
            "zeta-root", "zeta-child",
        ]

    def test_empty_list(self):
        """Should handle empty list."""
        result = sort_sessions_by_tree([])
        assert result == []

    def test_does_not_mutate_input(self):
        """Should not mutate input list."""
        sessions = [
            self._make_tree_session("b"),
            self._make_tree_session("a"),
        ]
        original_order = [s.name for s in sessions]

        sort_sessions_by_tree(sessions)

        assert [s.name for s in sessions] == original_order


class TestFilterVisibleSessionsDone:
    """Tests for done agent filtering (#244)."""

    def test_done_hidden_by_default(self):
        """Done agents should be hidden by default."""
        done_session = make_session("done-agent")
        done_session.status = "done"
        active = [make_session("active"), done_session]

        result = filter_visible_sessions(
            active, [], hide_asleep=False, show_terminated=False, show_done=False
        )

        assert len(result) == 1
        assert result[0].name == "active"

    def test_done_shown_when_enabled(self):
        """Done agents should appear when show_done=True."""
        done_session = make_session("done-agent")
        done_session.status = "done"
        active = [make_session("active"), done_session]

        result = filter_visible_sessions(
            active, [], hide_asleep=False, show_terminated=False, show_done=True
        )

        assert len(result) == 2
        assert {s.name for s in result} == {"active", "done-agent"}


class TestStatusOrderConstants:
    """Tests for status order constants."""

    def test_attention_order_has_all_statuses(self):
        """Status order should have all expected statuses."""
        expected = {"waiting_user", "waiting_approval", "error",
                    "running_heartbeat", "heartbeat_start", "waiting_heartbeat",
                    "running", "terminated", "done", "asleep"}
        assert set(STATUS_ORDER_BY_ATTENTION.keys()) == expected

    def test_value_order_has_all_statuses(self):
        """Value order should have all expected statuses."""
        expected = {"waiting_user", "waiting_approval", "error",
                    "waiting_heartbeat", "running", "running_heartbeat",
                    "heartbeat_start", "terminated", "done", "asleep"}
        assert set(STATUS_ORDER_BY_VALUE.keys()) == expected

    def test_waiting_user_highest_priority(self):
        """waiting_user should have highest priority (0) in both orders."""
        assert STATUS_ORDER_BY_ATTENTION["waiting_user"] == 0
        assert STATUS_ORDER_BY_VALUE["waiting_user"] == 0


class TestFilterCollapsedParents:
    """Tests for collapsed parent filtering in tree view (#244)."""

    def test_collapsed_parent_hides_children(self):
        """Children of a collapsed parent should be hidden."""
        parent = make_session("parent", session_id="p1")
        parent.parent_session_id = None
        parent.status = "running"
        child = make_session("child", session_id="c1")
        child.parent_session_id = "p1"
        child.status = "running"

        result = filter_visible_sessions(
            [parent, child], [], hide_asleep=False, show_terminated=False,
            collapsed_parents={"p1"},
        )

        assert len(result) == 1
        assert result[0].name == "parent"

    def test_collapsed_parent_hides_grandchildren(self):
        """Grandchildren of a collapsed parent should also be hidden."""
        root = make_session("root", session_id="r1")
        root.parent_session_id = None
        root.status = "running"
        child = make_session("child", session_id="c1")
        child.parent_session_id = "r1"
        child.status = "running"
        grandchild = make_session("grandchild", session_id="gc1")
        grandchild.parent_session_id = "c1"
        grandchild.status = "running"

        result = filter_visible_sessions(
            [root, child, grandchild], [], hide_asleep=False, show_terminated=False,
            collapsed_parents={"r1"},
        )

        assert len(result) == 1
        assert result[0].name == "root"

    def test_no_collapse_when_not_in_tree_mode(self):
        """When collapsed_parents is None, no filtering happens."""
        parent = make_session("parent", session_id="p1")
        parent.parent_session_id = None
        parent.status = "running"
        child = make_session("child", session_id="c1")
        child.parent_session_id = "p1"
        child.status = "running"

        result = filter_visible_sessions(
            [parent, child], [], hide_asleep=False, show_terminated=False,
            collapsed_parents=None,
        )

        assert len(result) == 2

    def test_collapse_only_affects_descendants(self):
        """Collapsing one parent shouldn't affect unrelated agents."""
        parent1 = make_session("parent1", session_id="p1")
        parent1.parent_session_id = None
        parent1.status = "running"
        child1 = make_session("child1", session_id="c1")
        child1.parent_session_id = "p1"
        child1.status = "running"
        parent2 = make_session("parent2", session_id="p2")
        parent2.parent_session_id = None
        parent2.status = "running"

        result = filter_visible_sessions(
            [parent1, child1, parent2], [], hide_asleep=False, show_terminated=False,
            collapsed_parents={"p1"},
        )

        assert len(result) == 2
        assert {s.name for s in result} == {"parent1", "parent2"}


# =============================================================================
# Remote-aware sorting (#245)
# =============================================================================


def make_remote_session(name: str, host: str, current_state: str = "running"):
    """Create a mock remote session for testing."""
    session = make_session_with_stats(name, current_state)
    session.is_remote = True
    session.source_host = host
    return session


def make_local_session(name: str, current_state: str = "running"):
    """Create a mock local session for testing."""
    session = make_session_with_stats(name, current_state)
    session.is_remote = False
    session.source_host = ""
    return session


class TestRemoteAwareSorting:
    """Test that remote sessions sort after local ones."""

    def test_local_before_remote_alphabetical(self):
        sessions = [
            make_remote_session("alpha", "remote-host"),
            make_local_session("zeta"),
        ]
        result = sort_sessions_alphabetical(sessions)
        assert result[0].name == "zeta"  # Local first
        assert result[1].name == "alpha"  # Remote second

    def test_remote_grouped_by_host(self):
        sessions = [
            make_remote_session("c", "host-b"),
            make_remote_session("a", "host-a"),
            make_remote_session("b", "host-b"),
            make_local_session("local1"),
        ]
        result = sort_sessions_alphabetical(sessions)
        assert result[0].name == "local1"
        assert result[1].name == "a"  # host-a
        assert result[2].name == "b"  # host-b
        assert result[3].name == "c"  # host-b

    def test_local_before_remote_by_status(self):
        sessions = [
            make_remote_session("remote-waiting", "host", "waiting_user"),
            make_local_session("local-running", "running"),
        ]
        result = sort_sessions_by_status(sessions)
        assert result[0].name == "local-running"  # Local first despite lower priority

    def test_local_before_remote_by_value(self):
        remote = make_remote_session("remote", "host")
        remote.agent_value = 9999  # Very high value
        local = make_local_session("local")
        local.agent_value = 1  # Very low value

        result = sort_sessions_by_value([remote, local])
        assert result[0].name == "local"  # Local always first

    def test_local_before_remote_tree(self):
        sessions = [
            make_remote_session("remote", "host"),
            make_local_session("local"),
        ]
        # Remote has no parent, so it's a root
        for s in sessions:
            s.parent_session_id = None

        result = sort_sessions_by_tree(sessions)
        assert result[0].name == "local"
        assert result[1].name == "remote"


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
