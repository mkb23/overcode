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
    sort_sessions_by_column,
    sort_mode_for_column,
    sort_column_for_mode,
    sort_descending,
    calculate_spin_stats,
    calculate_mean_spin_from_history,
    calculate_green_percentage,
    calculate_human_interaction_count,
    compute_tree_metadata,
    compute_stall_state,
    should_send_stall_notification,
    compute_active_session_names,
    compute_session_widget_diff,
    detect_display_changes,
    StallState,
    TreeNodeMeta,
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
    session.parent_session_id = None
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

    def test_drops_terminated_status_from_active_when_hidden(self):
        """A session that flipped to status=terminated inside active_sessions
        is hidden when show_terminated=False (#456)."""
        active = make_session("zombie", session_id="z")
        active.status = "terminated"
        live = make_session("alive", session_id="a")
        live.status = "running"

        result = filter_visible_sessions(
            [active, live], [], hide_asleep=False, show_terminated=False
        )

        assert {s.name for s in result} == {"alive"}

    def test_keeps_terminated_status_when_shown(self):
        """A session with status=terminated in active_sessions stays when
        show_terminated=True."""
        active = make_session("zombie", session_id="z")
        active.status = "terminated"

        result = filter_visible_sessions(
            [active], [], hide_asleep=False, show_terminated=True
        )

        assert len(result) == 1

    def test_tag_filter_keeps_only_matching(self):
        """tag_filter limits the result to sessions whose tags contain it (#357)."""
        a = make_session("alpha", session_id="a")
        a.tags = ["backend"]
        b = make_session("beta", session_id="b")
        b.tags = ["frontend"]
        c = make_session("gamma", session_id="c")
        c.tags = ["backend", "hot-path"]

        result = filter_visible_sessions(
            [a, b, c], [], hide_asleep=False, show_terminated=False,
            tag_filter="backend",
        )

        assert {s.name for s in result} == {"alpha", "gamma"}

    def test_tag_filter_case_insensitive(self):
        a = make_session("alpha", session_id="a")
        a.tags = ["Backend"]
        result = filter_visible_sessions(
            [a], [], hide_asleep=False, show_terminated=False,
            tag_filter="BACKEND",
        )
        assert len(result) == 1

    def test_tag_filter_none_disables(self):
        a = make_session("alpha", session_id="a")
        a.tags = []
        result = filter_visible_sessions(
            [a], [], hide_asleep=False, show_terminated=False,
            tag_filter=None,
        )
        assert len(result) == 1

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


class TestColumnSort:
    """Sorting by any summary column (#487)."""

    @staticmethod
    def _s(name, parent=None):
        from types import SimpleNamespace
        return SimpleNamespace(id=name, name=name, parent_session_id=parent)

    def test_descending_with_unknowns_last(self):
        a, b, c, d = (self._s(n) for n in "abcd")
        values = {"a": 5, "b": None, "c": 50}  # d missing
        out = sort_sessions_by_column([a, b, c, d], values, descending=True)
        assert [s.name for s in out] == ["c", "a", "b", "d"]

    def test_ascending_keeps_unknowns_last(self):
        a, b, c = (self._s(n) for n in "abc")
        out = sort_sessions_by_column([a, b, c], {"a": 5, "c": 1}, descending=False)
        assert [s.name for s in out] == ["c", "a", "b"]

    def test_ties_keep_name_order_either_way(self):
        x, y, z = self._s("x"), self._s("y"), self._s("z")
        values = {"x": 1, "y": 1, "z": 2}
        assert [s.name for s in sort_sessions_by_column([z, y, x], values, True)] == ["z", "x", "y"]
        assert [s.name for s in sort_sessions_by_column([z, y, x], values, False)] == ["x", "y", "z"]

    def test_children_stay_under_parent(self):
        p1, p2 = self._s("p1"), self._s("p2")
        k1, k2 = self._s("k1", parent="p1"), self._s("k2", parent="p1")
        values = {"p1": 1, "p2": 9, "k1": 3, "k2": 7}
        out = sort_sessions_by_column([p1, k1, k2, p2], values, descending=True)
        assert [s.name for s in out] == ["p2", "p1", "k2", "k1"]

    def test_mixed_types_do_not_crash(self):
        a, b = self._s("a"), self._s("b")
        out = sort_sessions_by_column([a, b], {"a": "x", "b": 3}, descending=False)
        assert {s.name for s in out} == {"a", "b"}

    def test_sort_sessions_dispatches_column_mode(self):
        a, b = self._s("a"), self._s("b")
        out = sort_sessions([a, b], "col:cpu_pct", values={"a": 1.0, "b": 90.0})
        assert [s.name for s in out] == ["b", "a"]  # CPU is largest-first
        out = sort_sessions([a, b], "col:cpu_pct", reverse=True, values={"a": 1.0, "b": 90.0})
        assert [s.name for s in out] == ["a", "b"]

    def test_presets_reverse(self):
        a, b = self._s("alpha"), self._s("bravo")
        assert [s.name for s in sort_sessions([a, b], "alphabetical", reverse=True)] == ["bravo", "alpha"]

    def test_mode_mapping(self):
        assert sort_mode_for_column("agent_name") == "alphabetical"
        assert sort_mode_for_column("status_symbol") == "by_status"
        assert sort_mode_for_column("agent_value") == "by_value"
        assert sort_mode_for_column("cpu_pct") == "col:cpu_pct"
        assert sort_column_for_mode("col:cpu_pct") == "cpu_pct"
        assert sort_column_for_mode("by_status") == "status_symbol"
        assert sort_column_for_mode("by_tree") is None

    def test_descending_follows_column_and_reverse(self):
        assert sort_descending("col:cpu_pct", False) is True
        assert sort_descending("col:cpu_pct", True) is False
        assert sort_descending("alphabetical", False) is False
        assert sort_descending("by_value", False) is True
        assert sort_descending("by_tree", True) is False

    def test_display_name_for_column_mode(self):
        assert get_sort_mode_display_name("col:cpu_pct") == "CPU %"


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
    """Tests for history-based mean spin calculation.

    Rows are written on change plus a keepalive (audit R10), so each row
    stands for its agent until the agent's next row; the mean is time-
    weighted over what the rows cover, scaled to the agent count. Fixtures
    are built from segments — (agent, from_minutes_ago, to_minutes_ago,
    status) — expanded into a keepalive row per minute, as the daemon
    writes them.
    """

    NOW = datetime(2026, 9, 23, 12, 0, 0)

    def _row(self, minutes_ago, agent, status):
        return (self.NOW - timedelta(minutes=minutes_ago), agent, status, "")

    def _rows(self, *segments):
        """Keepalive rows every minute over each (agent, from, to, status) segment."""
        rows = []
        for agent, from_min, to_min, status in segments:
            for m in range(from_min, to_min, -1):
                rows.append(self._row(m, agent, status))
        rows.sort(key=lambda r: r[0])
        return rows

    def test_empty_history_returns_zero(self):
        """Empty history should return 0.0 with 0 samples."""
        mean_spin, sample_count = calculate_mean_spin_from_history(
            history=[],
            agent_names=["agent1", "agent2"],
            baseline_minutes=30,
            now=self.NOW,
        )
        assert mean_spin == 0.0
        assert sample_count == 0

    def test_zero_baseline_returns_zero(self):
        """baseline_minutes=0 should return 0.0 (instantaneous mode)."""
        mean_spin, sample_count = calculate_mean_spin_from_history(
            history=[self._row(5, "agent1", "running")],
            agent_names=["agent1"],
            baseline_minutes=0,
            now=self.NOW,
        )
        assert mean_spin == 0.0
        assert sample_count == 0

    def test_empty_agent_names_returns_zero(self):
        """Empty agent_names list should return 0."""
        mean_spin, sample_count = calculate_mean_spin_from_history(
            history=[self._row(5, "agent1", "running")],
            agent_names=[],
            baseline_minutes=30,
            now=self.NOW,
        )
        assert mean_spin == 0.0
        assert sample_count == 0

    def test_all_running_returns_agent_count(self):
        """Agents running for the whole window: mean_spin equals num_agents."""
        history = self._rows(("agent1", 35, 0, "running"), ("agent2", 35, 0, "running"))
        mean_spin, sample_count = calculate_mean_spin_from_history(
            history, ["agent1", "agent2"], baseline_minutes=30, now=self.NOW
        )
        assert mean_spin == pytest.approx(2.0)
        assert sample_count == 60  # rows inside the window only: 30 per agent

    def test_rows_stand_until_the_agents_next_row(self):
        """One agent green for the first 10 of 30 minutes -> 1/3 of an agent."""
        history = self._rows(("agent1", 30, 20, "running"), ("agent1", 20, 0, "waiting_user"))
        mean_spin, sample_count = calculate_mean_spin_from_history(
            history, ["agent1"], baseline_minutes=30, now=self.NOW
        )
        assert mean_spin == pytest.approx(1 / 3)
        assert sample_count == 30

    def test_a_change_row_between_keepalives_counts_from_its_own_time(self):
        """Green from -30 to -12:30, then idle: 17.5 of 30 minutes."""
        history = self._rows(("agent1", 30, 12, "running"), ("agent1", 12, 0, "waiting_user"))
        history.append((self.NOW - timedelta(minutes=12, seconds=30), "agent1", "waiting_user", ""))
        history.sort(key=lambda r: r[0])
        mean_spin, _ = calculate_mean_spin_from_history(
            history, ["agent1"], baseline_minutes=30, now=self.NOW
        )
        assert mean_spin == pytest.approx(17.5 / 30)

    def test_half_running_returns_half_agents(self):
        """Two agents each green half the window -> mean_spin 1.0."""
        history = self._rows(
            ("agent1", 30, 15, "running"),
            ("agent1", 15, 0, "waiting_user"),
            ("agent2", 30, 15, "waiting_user"),
            ("agent2", 15, 0, "running"),
        )
        mean_spin, sample_count = calculate_mean_spin_from_history(
            history, ["agent1", "agent2"], baseline_minutes=30, now=self.NOW
        )
        assert mean_spin == pytest.approx(1.0)
        assert sample_count == 60

    def test_rows_before_the_cutoff_set_the_state_at_the_edge(self):
        """Pre-cutoff rows are not samples but cover the window from its start."""
        history = self._rows(("agent1", 35, 15, "running"), ("agent1", 15, 0, "waiting_user"))
        mean_spin, sample_count = calculate_mean_spin_from_history(
            history, ["agent1"], baseline_minutes=30, now=self.NOW
        )
        assert mean_spin == pytest.approx(0.5)  # green from the cutoff to -15 min
        assert sample_count == 30

    def test_filters_by_agent_names(self):
        """Should only include rows from specified agents."""
        history = self._rows(("agent1", 30, 0, "running"), ("agent2", 30, 0, "waiting_user"))
        mean_spin, sample_count = calculate_mean_spin_from_history(
            history, ["agent1"], baseline_minutes=30, now=self.NOW
        )
        assert mean_spin == pytest.approx(1.0)
        assert sample_count == 30

    def test_filters_by_time_window(self):
        """Rows far before the window are neither samples nor coverage."""
        history = [
            self._row(60, "agent1", "running"),       # outside the 30 m window and its validity
            self._row(10, "agent1", "waiting_user"),  # inside
            self._row(5, "agent1", "waiting_user"),   # inside
        ]
        mean_spin, sample_count = calculate_mean_spin_from_history(
            history, ["agent1"], baseline_minutes=30, now=self.NOW
        )
        assert mean_spin == 0.0
        assert sample_count == 2

    def test_rows_after_now_are_ignored(self):
        history = [
            self._row(10, "agent1", "waiting_user"),
            self._row(-5, "agent1", "running"),  # five minutes in the future
        ]
        mean_spin, sample_count = calculate_mean_spin_from_history(
            history, ["agent1"], baseline_minutes=30, now=self.NOW
        )
        assert mean_spin == 0.0
        assert sample_count == 1

    def test_an_agent_that_appears_mid_window_is_weighted_by_its_coverage(self):
        """The share is of the covered agent-time, as the row fraction was.

        agent1 idle for 30 min, agent2 running for its last 10: green 10 of
        40 covered minutes, scaled to 2 agents -> 0.5 (not 10/60 of 2).
        """
        history = self._rows(("agent1", 30, 0, "waiting_user"), ("agent2", 10, 0, "running"))
        mean_spin, _ = calculate_mean_spin_from_history(
            history, ["agent1", "agent2"], baseline_minutes=30, now=self.NOW
        )
        assert mean_spin == pytest.approx(0.5)

    def test_a_row_with_no_successor_stands_for_at_most_the_validity(self):
        """A daemon gap longer than SPIN_ROW_VALIDITY_SECONDS is 'no samples'."""
        from overcode.tui_logic import SPIN_ROW_VALIDITY_SECONDS
        assert SPIN_ROW_VALIDITY_SECONDS < 10 * 60
        history = [self._row(30, "agent1", "running")]  # then nothing for the rest of the window
        mean_spin, sample_count = calculate_mean_spin_from_history(
            history, ["agent1"], baseline_minutes=30, now=self.NOW
        )
        # Covered time is only the validity; it was all green.
        assert mean_spin == pytest.approx(1.0)
        assert sample_count == 1

        # An idle row after the gap: the gap between them counts for neither,
        # and each row covers its validity -> half green.
        history.append(self._row(10, "agent1", "waiting_user"))
        mean_spin, _ = calculate_mean_spin_from_history(
            history, ["agent1"], baseline_minutes=30, now=self.NOW
        )
        assert mean_spin == pytest.approx(0.5)

    def test_fixed_interval_rows_give_the_row_fraction(self):
        """Row-per-tick data (the old file) reproduces the old row fraction."""
        history = []
        for k in range(0, 900):  # a row every 2 s for 30 minutes
            status = "running" if k < 300 else "waiting_user"
            history.append((self.NOW - timedelta(minutes=30) + timedelta(seconds=2 * k), "agent1", status, ""))
        mean_spin, sample_count = calculate_mean_spin_from_history(
            history, ["agent1"], baseline_minutes=30, now=self.NOW
        )
        assert sample_count == 900
        assert mean_spin == pytest.approx(300 / 900, abs=2 / 1800)

    def test_agent_names_membership_is_a_set(self):
        """An agent listed twice still counts as two in the scale factor."""
        history = self._rows(("agent1", 30, 0, "running"))
        mean_spin, _ = calculate_mean_spin_from_history(
            history, ["agent1", "agent1"], baseline_minutes=30, now=self.NOW
        )
        assert mean_spin == pytest.approx(2.0)


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
    """Test that local and remote sessions sort intermixed by name."""

    def test_alphabetical_intermixes_local_and_remote(self):
        sessions = [
            make_remote_session("alpha", "remote-host"),
            make_local_session("zeta"),
        ]
        result = sort_sessions_alphabetical(sessions)
        assert result[0].name == "alpha"  # Alphabetical, not local-first
        assert result[1].name == "zeta"

    def test_remote_sorted_by_name_not_host(self):
        sessions = [
            make_remote_session("c", "host-b"),
            make_remote_session("a", "host-a"),
            make_remote_session("b", "host-b"),
            make_local_session("bb"),
        ]
        result = sort_sessions_alphabetical(sessions)
        assert [s.name for s in result] == ["a", "b", "bb", "c"]

    def test_status_sort_intermixes_local_and_remote(self):
        sessions = [
            make_remote_session("remote-waiting", "host", "waiting_user"),
            make_local_session("local-running", "running"),
        ]
        result = sort_sessions_by_status(sessions)
        assert result[0].name == "remote-waiting"  # waiting_user has higher priority

    def test_value_sort_intermixes_local_and_remote(self):
        remote = make_remote_session("remote", "host")
        remote.agent_value = 9999  # Very high value
        local = make_local_session("local")
        local.agent_value = 1  # Very low value

        result = sort_sessions_by_value([remote, local])
        assert result[0].name == "remote"  # Higher value first

    def test_tree_sort_intermixes_local_and_remote(self):
        sessions = [
            make_remote_session("alpha-remote", "host"),
            make_local_session("zeta-local"),
        ]
        for s in sessions:
            s.parent_session_id = None

        result = sort_sessions_by_tree(sessions)
        assert result[0].name == "alpha-remote"  # Alphabetical
        assert result[1].name == "zeta-local"

    def test_remote_tree_hierarchy(self):
        """Remote agents with parent-child relationships should nest in tree view."""
        parent = make_remote_session("remote-parent", "host")
        parent.id = "remote:host:remote-parent"
        parent.parent_session_id = None

        child_a = make_remote_session("remote-child-a", "host")
        child_a.id = "remote:host:remote-child-a"
        child_a.parent_session_id = "remote:host:remote-parent"

        child_b = make_remote_session("remote-child-b", "host")
        child_b.id = "remote:host:remote-child-b"
        child_b.parent_session_id = "remote:host:remote-parent"

        local = make_local_session("local-root")
        local.parent_session_id = None

        sessions = [child_b, local, child_a, parent]
        result = sort_sessions_by_tree(sessions)

        assert [s.name for s in result] == [
            "local-root",           # 'l' before 'r' alphabetically
            "remote-parent",        # Remote root
            "remote-child-a",       # Remote child (alpha order)
            "remote-child-b",       # Remote child (alpha order)
        ]


# =============================================================================
# Tree metadata computation
# =============================================================================


class TestComputeTreeMetadata:
    """Tests for compute_tree_metadata() pure function."""

    def _make_tree_session(self, name, session_id=None, parent_session_id=None,
                           is_remote=False, source_host=""):
        session = make_session(name, session_id=session_id or name)
        session.parent_session_id = parent_session_id
        session.is_remote = is_remote
        session.source_host = source_host
        return session

    def test_root_sessions_get_depth_zero(self):
        """Root sessions should have depth 0 and empty prefix."""
        sessions = [
            self._make_tree_session("alpha"),
            self._make_tree_session("bravo"),
        ]

        meta = compute_tree_metadata(sessions)

        assert meta["alpha"].depth == 0
        assert meta["alpha"].prefix == ""
        assert meta["bravo"].depth == 0
        assert meta["bravo"].prefix == ""

    def test_children_get_depth_one(self):
        """Children of a root should have depth 1 with proper prefix."""
        root = self._make_tree_session("root", session_id="root-id")
        child_a = self._make_tree_session("child-a", session_id="ca",
                                          parent_session_id="root-id")
        child_b = self._make_tree_session("child-b", session_id="cb",
                                          parent_session_id="root-id")

        meta = compute_tree_metadata([root, child_a, child_b])

        assert meta["root-id"].depth == 0
        assert meta["ca"].depth == 1
        assert meta["ca"].prefix == "├─"
        assert meta["cb"].depth == 1
        assert meta["cb"].prefix == "└─"
        assert meta["cb"].is_last is True
        assert meta["ca"].is_last is False

    def test_nested_children_get_depth_two(self):
        """Grandchildren should have depth 2 with indented prefix."""
        root = self._make_tree_session("root", session_id="r")
        child = self._make_tree_session("child", session_id="c",
                                        parent_session_id="r")
        grandchild = self._make_tree_session("grandchild", session_id="gc",
                                             parent_session_id="c")

        meta = compute_tree_metadata([root, child, grandchild])

        assert meta["r"].depth == 0
        assert meta["c"].depth == 1
        assert meta["gc"].depth == 2
        assert meta["gc"].prefix == "  └─"  # indented once + connector

    def test_child_count(self):
        """Should count direct children correctly."""
        root = self._make_tree_session("root", session_id="r")
        child_a = self._make_tree_session("child-a", session_id="ca",
                                          parent_session_id="r")
        child_b = self._make_tree_session("child-b", session_id="cb",
                                          parent_session_id="r")
        grandchild = self._make_tree_session("grandchild", session_id="gc",
                                             parent_session_id="ca")

        meta = compute_tree_metadata([root, child_a, child_b, grandchild])

        assert meta["r"].child_count == 2   # child_a and child_b
        assert meta["ca"].child_count == 1  # grandchild
        assert meta["cb"].child_count == 0
        assert meta["gc"].child_count == 0

    def test_remote_sessions_with_parent(self):
        """Remote sessions with parent_session_id should get correct depth."""
        parent = self._make_tree_session(
            "remote-parent", session_id="remote:host:remote-parent",
            is_remote=True, source_host="host")
        child = self._make_tree_session(
            "remote-child", session_id="remote:host:remote-child",
            parent_session_id="remote:host:remote-parent",
            is_remote=True, source_host="host")

        meta = compute_tree_metadata([parent, child])

        assert meta["remote:host:remote-parent"].depth == 0
        assert meta["remote:host:remote-child"].depth == 1
        assert meta["remote:host:remote-child"].prefix == "└─"

    def test_mixed_local_and_remote(self):
        """Mixed local and remote sessions should all get correct metadata."""
        local_root = self._make_tree_session("local-root", session_id="lr")
        local_child = self._make_tree_session(
            "local-child", session_id="lc", parent_session_id="lr")
        remote_root = self._make_tree_session(
            "remote-root", session_id="remote:host:rr",
            is_remote=True, source_host="host")
        remote_child = self._make_tree_session(
            "remote-child", session_id="remote:host:rc",
            parent_session_id="remote:host:rr",
            is_remote=True, source_host="host")

        sessions = [local_root, local_child, remote_root, remote_child]
        meta = compute_tree_metadata(sessions)

        assert meta["lr"].depth == 0
        assert meta["lc"].depth == 1
        assert meta["remote:host:rr"].depth == 0
        assert meta["remote:host:rc"].depth == 1

    def test_orphan_child_gets_depth_zero(self):
        """A child whose parent is not in the list should get depth 0."""
        orphan = self._make_tree_session(
            "orphan", session_id="o", parent_session_id="missing-parent")

        meta = compute_tree_metadata([orphan])

        assert meta["o"].depth == 0
        assert meta["o"].prefix == ""

    def test_empty_list(self):
        """Empty session list should return empty dict."""
        meta = compute_tree_metadata([])
        assert meta == {}

    def test_single_session(self):
        """Single root session should work."""
        s = self._make_tree_session("solo")
        meta = compute_tree_metadata([s])
        assert meta["solo"].depth == 0
        assert meta["solo"].prefix == ""
        assert meta["solo"].child_count == 0


# =============================================================================
# Session widget diffing
# =============================================================================


class TestComputeSessionWidgetDiff:
    """Tests for compute_session_widget_diff()."""

    def test_no_changes(self):
        """Should return empty sets when no changes needed."""
        to_add, to_remove = compute_session_widget_diff({"a", "b"}, ["a", "b"])
        assert to_add == set()
        assert to_remove == set()

    def test_additions_only(self):
        """Should detect new sessions to add."""
        to_add, to_remove = compute_session_widget_diff({"a"}, ["a", "b", "c"])
        assert to_add == {"b", "c"}
        assert to_remove == set()

    def test_removals_only(self):
        """Should detect sessions to remove."""
        to_add, to_remove = compute_session_widget_diff({"a", "b", "c"}, ["a"])
        assert to_add == set()
        assert to_remove == {"b", "c"}

    def test_mixed_add_remove(self):
        """Should handle simultaneous adds and removes."""
        to_add, to_remove = compute_session_widget_diff({"a", "b"}, ["b", "c"])
        assert to_add == {"c"}
        assert to_remove == {"a"}

    def test_empty_existing(self):
        """Should add all when no existing widgets."""
        to_add, to_remove = compute_session_widget_diff(set(), ["a", "b"])
        assert to_add == {"a", "b"}
        assert to_remove == set()

    def test_empty_display(self):
        """Should remove all when no display sessions."""
        to_add, to_remove = compute_session_widget_diff({"a", "b"}, [])
        assert to_add == set()
        assert to_remove == {"a", "b"}


class TestDetectDisplayChanges:
    """Tests for detect_display_changes()."""

    def test_no_budget_no_oversight(self):
        """Should return False, False, False when no budget/oversight/pr."""
        sessions = [Mock(cost_budget_usd=0, oversight_policy="wait", oversight_timeout_seconds=0, pr_number=None)]
        budget, oversight, pr = detect_display_changes(sessions, False, False)
        assert budget is False
        assert oversight is False
        assert pr is False

    def test_has_budget(self):
        """Should detect cost budget."""
        sessions = [Mock(cost_budget_usd=5.0, oversight_policy="wait", oversight_timeout_seconds=0, pr_number=None)]
        budget, oversight, pr = detect_display_changes(sessions, False, False)
        assert budget is True
        assert oversight is False

    def test_has_oversight(self):
        """Should detect oversight timeout."""
        sessions = [Mock(cost_budget_usd=0, oversight_policy="timeout", oversight_timeout_seconds=300, pr_number=None)]
        budget, oversight, pr = detect_display_changes(sessions, False, False)
        assert budget is False
        assert oversight is True

    def test_empty_sessions(self):
        """Should return False, False, False for empty sessions."""
        budget, oversight, pr = detect_display_changes([], False, False)
        assert budget is False
        assert oversight is False
        assert pr is False

    def test_has_pr(self):
        """Should detect PR number."""
        sessions = [Mock(cost_budget_usd=0, oversight_policy="wait", oversight_timeout_seconds=0, pr_number=42)]
        budget, oversight, pr = detect_display_changes(sessions, False, False)
        assert budget is False
        assert oversight is False
        assert pr is True

    def test_no_pr_when_none(self):
        """Should not detect PR when all are None."""
        sessions = [
            Mock(cost_budget_usd=0, oversight_policy="wait", oversight_timeout_seconds=0, pr_number=None),
            Mock(cost_budget_usd=0, oversight_policy="wait", oversight_timeout_seconds=0, pr_number=None),
        ]
        _, _, pr = detect_display_changes(sessions, False, False)
        assert pr is False


# =============================================================================
# Active session names
# =============================================================================


class TestComputeActiveSessionNames:
    """Tests for compute_active_session_names()."""

    def _make_daemon_state(self, session_id, name):
        """Create a mock daemon state with proper name attribute."""
        m = Mock(session_id=session_id)
        m.name = name  # Set after construction to avoid Mock's name param
        return m

    def test_all_active(self):
        """Should return all names when none are asleep."""
        sessions = [
            self._make_daemon_state("s1", "alpha"),
            self._make_daemon_state("s2", "bravo"),
        ]
        result = compute_active_session_names(sessions, set())
        assert result == ["alpha", "bravo"]

    def test_filters_asleep(self):
        """Should exclude asleep sessions."""
        sessions = [
            self._make_daemon_state("s1", "alpha"),
            self._make_daemon_state("s2", "bravo"),
            self._make_daemon_state("s3", "charlie"),
        ]
        result = compute_active_session_names(sessions, {"s2"})
        assert result == ["alpha", "charlie"]

    def test_empty_sessions(self):
        """Should return empty list for no sessions."""
        result = compute_active_session_names([], set())
        assert result == []

    def test_all_asleep(self):
        """Should return empty list when all are asleep."""
        sessions = [self._make_daemon_state("s1", "alpha")]
        result = compute_active_session_names(sessions, {"s1"})
        assert result == []


# =============================================================================
# Stall detection logic
# =============================================================================


class TestComputeStallState:
    """Tests for compute_stall_state()."""

    def test_new_stall_detected(self):
        """Should detect transition to waiting_user."""
        result = compute_stall_state(
            status="waiting_user",
            prev_status="running",
            session_id="s1",
            visited_stalled_agents=set(),
            is_asleep=False,
        )
        assert result.is_new_stall is True
        assert result.is_unvisited_stalled is True
        assert result.should_clear_tracking is False

    def test_continued_stall_not_new(self):
        """Should not flag as new when already stalled."""
        result = compute_stall_state(
            status="waiting_user",
            prev_status="waiting_user",
            session_id="s1",
            visited_stalled_agents=set(),
            is_asleep=False,
        )
        assert result.is_new_stall is False
        assert result.is_unvisited_stalled is True

    def test_stall_cleared_when_running(self):
        """Should clear tracking when no longer stalled."""
        result = compute_stall_state(
            status="running",
            prev_status="waiting_user",
            session_id="s1",
            visited_stalled_agents=set(),
            is_asleep=False,
        )
        assert result.should_clear_tracking is True
        assert result.is_new_stall is False
        assert result.is_unvisited_stalled is False

    def test_visited_stall_not_unvisited(self):
        """Should not flag as unvisited if already visited."""
        result = compute_stall_state(
            status="waiting_user",
            prev_status="running",
            session_id="s1",
            visited_stalled_agents={"s1"},
            is_asleep=False,
        )
        assert result.is_new_stall is True
        assert result.is_unvisited_stalled is False

    def test_asleep_not_unvisited(self):
        """Asleep sessions should not be flagged as unvisited stalled."""
        result = compute_stall_state(
            status="waiting_user",
            prev_status="running",
            session_id="s1",
            visited_stalled_agents=set(),
            is_asleep=True,
        )
        assert result.is_unvisited_stalled is False

    def test_first_observation_to_stalled(self):
        """First status being waiting_user (prev=None) should be new stall."""
        result = compute_stall_state(
            status="waiting_user",
            prev_status=None,
            session_id="s1",
            visited_stalled_agents=set(),
            is_asleep=False,
        )
        assert result.is_new_stall is True


class TestShouldSendStallNotification:
    """Tests for should_send_stall_notification()."""

    def test_eligible_notification(self):
        """Should return True when all conditions met."""
        assert should_send_stall_notification(
            status="waiting_user",
            is_notified=False,
            is_asleep=False,
            has_stall_start=True,
            stall_age_seconds=60,
            uptime_seconds=120,
        ) is True

    def test_not_waiting_user(self):
        """Should return False when not stalled."""
        assert should_send_stall_notification(
            status="running",
            is_notified=False,
            is_asleep=False,
            has_stall_start=True,
            stall_age_seconds=60,
            uptime_seconds=120,
        ) is False

    def test_already_notified(self):
        """Should return False when already notified."""
        assert should_send_stall_notification(
            status="waiting_user",
            is_notified=True,
            is_asleep=False,
            has_stall_start=True,
            stall_age_seconds=60,
            uptime_seconds=120,
        ) is False

    def test_asleep(self):
        """Should return False when asleep."""
        assert should_send_stall_notification(
            status="waiting_user",
            is_notified=False,
            is_asleep=True,
            has_stall_start=True,
            stall_age_seconds=60,
            uptime_seconds=120,
        ) is False

    def test_stall_too_young(self):
        """Should return False when stall is less than 30s old."""
        assert should_send_stall_notification(
            status="waiting_user",
            is_notified=False,
            is_asleep=False,
            has_stall_start=True,
            stall_age_seconds=20,
            uptime_seconds=120,
        ) is False

    def test_session_too_young(self):
        """Should return False when session uptime is less than 60s."""
        assert should_send_stall_notification(
            status="waiting_user",
            is_notified=False,
            is_asleep=False,
            has_stall_start=True,
            stall_age_seconds=60,
            uptime_seconds=30,
        ) is False

    def test_no_stall_start(self):
        """Should return False when no stall start time recorded."""
        assert should_send_stall_notification(
            status="waiting_user",
            is_notified=False,
            is_asleep=False,
            has_stall_start=False,
            stall_age_seconds=60,
            uptime_seconds=120,
        ) is False


class TestComputeWindowBurn:
    """Test compute_window_burn — aggregate burn rate across sessions (#174)."""

    def _make_session_with_jsonl(self, tmp_path, session_id, claude_sid, entries, model="claude-sonnet-4-6"):
        """Create a session + matching Claude JSONL file under tmp_path."""
        import json
        from overcode.history_reader import encode_project_path

        project_dir = tmp_path / "project"
        project_dir.mkdir(exist_ok=True)
        encoded = encode_project_path(str(project_dir))
        sess_files_dir = tmp_path / "claude_projects" / encoded
        sess_files_dir.mkdir(parents=True)
        sess_file = sess_files_dir / f"{claude_sid}.jsonl"
        sess_file.write_text("\n".join(json.dumps(e) for e in entries))

        sess = Mock()
        sess.id = session_id
        sess.start_directory = str(project_dir)
        sess.agent_session_ids = [claude_sid]
        sess.model = model
        sess.provider = "web"
        sess.is_asleep = False
        return sess

    def test_zero_window_returns_empty(self):
        from overcode.tui_logic import compute_window_burn
        result = compute_window_burn([], set(), hours=0)
        assert result.window_hours == 0
        assert result.total_tokens == 0
        assert result.tokens_per_hour == 0.0
        assert result.cost_per_hour == 0.0

    def test_empty_sessions_returns_empty(self):
        from overcode.tui_logic import compute_window_burn
        result = compute_window_burn([], set(), hours=1.0)
        assert result.total_tokens == 0

    def test_aggregates_tokens_in_window(self, tmp_path, monkeypatch):
        from datetime import timezone
        from overcode import history_reader
        from overcode.tui_logic import compute_window_burn

        # Anchor relative to real wall-clock so timezone math (UTC→local in
        # the reader vs local-naive `since` in compute_window_burn) lines up
        # regardless of the test runner's zone.
        now_utc = datetime.now(timezone.utc)
        inside_ts = (now_utc - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        outside_ts = (now_utc - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        old = {
            "type": "assistant",
            "timestamp": outside_ts,
            "message": {"model": "claude-sonnet-4-6",
                        "usage": {"input_tokens": 9999, "output_tokens": 9999}},
        }
        inside = {
            "type": "assistant",
            "timestamp": inside_ts,
            "message": {"model": "claude-sonnet-4-6",
                        "usage": {"input_tokens": 100, "output_tokens": 200,
                                  "cache_creation_input_tokens": 50,
                                  "cache_read_input_tokens": 300}},
        }
        sess = self._make_session_with_jsonl(
            tmp_path, "s1", "csid-1", [old, inside],
        )

        monkeypatch.setattr(
            history_reader,
            "CLAUDE_PROJECTS_PATH",
            tmp_path / "claude_projects",
        )

        result = compute_window_burn([sess], set(), hours=2.0)
        assert result.input_tokens == 100
        assert result.output_tokens == 200
        assert result.cache_creation_tokens == 50
        assert result.cache_read_tokens == 300
        assert result.cost_usd > 0
        assert result.tokens_per_hour == 150.0  # (100+200) / 2h

    def test_skips_asleep_sessions(self, tmp_path, monkeypatch):
        from datetime import timezone
        from overcode import history_reader
        from overcode.tui_logic import compute_window_burn

        inside_ts = (datetime.now(timezone.utc) - timedelta(minutes=30)).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        )
        entry = {
            "type": "assistant",
            "timestamp": inside_ts,
            "message": {"model": "claude-sonnet-4-6",
                        "usage": {"input_tokens": 100, "output_tokens": 200}},
        }
        sess = self._make_session_with_jsonl(tmp_path, "s1", "csid-1", [entry])
        monkeypatch.setattr(
            history_reader,
            "CLAUDE_PROJECTS_PATH",
            tmp_path / "claude_projects",
        )

        result = compute_window_burn([sess], {"s1"}, hours=2.0)
        assert result.total_tokens == 0


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
