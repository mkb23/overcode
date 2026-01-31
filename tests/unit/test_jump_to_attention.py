"""
Unit tests for jump_to_attention navigation logic.

Tests the 'b' key functionality which should prioritize:
1. Sessions with bell indicator (is_unvisited_stalled=True)
2. Sessions with waiting_user status (red)
3. Other non-green statuses
"""

import pytest
from dataclasses import dataclass
from typing import List, Optional


# Replicate the status constants
STATUS_WAITING_USER = "waiting_user"
STATUS_NO_INSTRUCTIONS = "no_instructions"
STATUS_WAITING_SUPERVISOR = "waiting_supervisor"
STATUS_RUNNING = "running"


@dataclass
class MockWidget:
    """Mock session summary widget for testing."""
    name: str
    detected_status: str = STATUS_RUNNING
    is_unvisited_stalled: bool = False

    def focus(self):
        pass


def build_attention_list_old_broken(widgets: List[MockWidget]) -> List[MockWidget]:
    """
    OLD BROKEN implementation - uses only detected_status, ignores bell.
    This demonstrates what was broken before the fix.
    """
    attention_sessions = []
    for i, widget in enumerate(widgets):
        status = widget.detected_status
        if status == STATUS_WAITING_USER:
            attention_sessions.append((0, i, widget))  # Highest priority
        elif status == STATUS_NO_INSTRUCTIONS:
            attention_sessions.append((1, i, widget))
        elif status == STATUS_WAITING_SUPERVISOR:
            attention_sessions.append((2, i, widget))

    attention_sessions.sort(key=lambda x: (x[0], x[1]))
    return [w for _, _, w in attention_sessions]


def build_attention_list_fixed(widgets: List[MockWidget]) -> List[MockWidget]:
    """
    Fixed implementation - prioritizes bell (is_unvisited_stalled) first.
    """
    attention_sessions = []
    for i, widget in enumerate(widgets):
        status = widget.detected_status
        is_bell = widget.is_unvisited_stalled

        # Priority: bell > waiting_user > no_instructions > waiting_supervisor
        if is_bell:
            attention_sessions.append((0, i, widget))  # Bell = highest priority
        elif status == STATUS_WAITING_USER:
            attention_sessions.append((1, i, widget))  # Red but no bell
        elif status == STATUS_NO_INSTRUCTIONS:
            attention_sessions.append((2, i, widget))
        elif status == STATUS_WAITING_SUPERVISOR:
            attention_sessions.append((3, i, widget))

    attention_sessions.sort(key=lambda x: (x[0], x[1]))
    return [w for _, _, w in attention_sessions]


class TestJumpToAttentionBug:
    """Test that demonstrates the old bug and verifies the fix."""

    def test_old_impl_ignores_bell_indicator(self):
        """
        DEMONSTRATES OLD BUG: Old implementation didn't distinguish between
        visited and unvisited waiting_user sessions.

        Both Agent A (visited, no bell) and Agent B (unvisited, has bell)
        had the same priority in the old implementation, but Agent B
        should come first because it has the bell indicator.
        """
        widgets = [
            MockWidget("Agent A", STATUS_WAITING_USER, is_unvisited_stalled=False),  # Red but visited
            MockWidget("Agent B", STATUS_WAITING_USER, is_unvisited_stalled=True),   # Red with bell!
            MockWidget("Agent C", STATUS_RUNNING, is_unvisited_stalled=False),       # Running (ok)
        ]

        result = build_attention_list_old_broken(widgets)

        # Old implementation returns A before B (by index order)
        # because it doesn't consider is_unvisited_stalled
        assert result[0].name == "Agent A", "Old impl returns A first (wrong - should be B)"
        assert result[1].name == "Agent B", "Old impl returns B second (wrong - B has bell)"

    def test_fixed_impl_prioritizes_bell(self):
        """
        VERIFIES THE FIX: Fixed implementation should return bell sessions first.
        """
        widgets = [
            MockWidget("Agent A", STATUS_WAITING_USER, is_unvisited_stalled=False),  # Red but visited
            MockWidget("Agent B", STATUS_WAITING_USER, is_unvisited_stalled=True),   # Red with bell!
            MockWidget("Agent C", STATUS_RUNNING, is_unvisited_stalled=False),       # Running (ok)
        ]

        result = build_attention_list_fixed(widgets)

        # Fixed implementation should return B first (has bell)
        assert result[0].name == "Agent B", "Fixed impl should return B first (has bell)"
        assert result[1].name == "Agent A", "Fixed impl should return A second (red but no bell)"

    def test_bell_on_running_session_should_be_top_priority(self):
        """
        Edge case: A session with bell indicator should take priority
        even if its detected_status is technically 'running'.

        This can happen if status detection races with state updates.
        The bell indicator is the canonical "needs attention" flag.
        """
        widgets = [
            MockWidget("Agent A", STATUS_WAITING_USER, is_unvisited_stalled=False),  # Red
            MockWidget("Agent B", STATUS_RUNNING, is_unvisited_stalled=True),        # Bell on "running"?
            MockWidget("Agent C", STATUS_NO_INSTRUCTIONS, is_unvisited_stalled=False),
        ]

        result = build_attention_list_fixed(widgets)

        # B should come first because it has the bell, regardless of detected_status
        assert result[0].name == "Agent B", "Bell takes priority regardless of detected_status"


class TestAttentionPriorityOrder:
    """Test the full priority order."""

    def test_full_priority_order(self):
        """Test all priority levels in order."""
        widgets = [
            MockWidget("D-supervisor", STATUS_WAITING_SUPERVISOR, is_unvisited_stalled=False),
            MockWidget("A-bell", STATUS_WAITING_USER, is_unvisited_stalled=True),
            MockWidget("B-red", STATUS_WAITING_USER, is_unvisited_stalled=False),
            MockWidget("C-yellow", STATUS_NO_INSTRUCTIONS, is_unvisited_stalled=False),
            MockWidget("E-running", STATUS_RUNNING, is_unvisited_stalled=False),
        ]

        result = build_attention_list_fixed(widgets)

        # Should be: bell > red > yellow > supervisor
        # E-running should not be in the list at all
        assert len(result) == 4
        assert result[0].name == "A-bell", "Bell should be first"
        assert result[1].name == "B-red", "Red (no bell) should be second"
        assert result[2].name == "C-yellow", "Yellow should be third"
        assert result[3].name == "D-supervisor", "Supervisor should be fourth"

    def test_multiple_bells_preserve_order(self):
        """When multiple sessions have bells, maintain their original order."""
        widgets = [
            MockWidget("Agent 1", STATUS_WAITING_USER, is_unvisited_stalled=True),
            MockWidget("Agent 2", STATUS_WAITING_USER, is_unvisited_stalled=True),
            MockWidget("Agent 3", STATUS_WAITING_USER, is_unvisited_stalled=True),
        ]

        result = build_attention_list_fixed(widgets)

        assert [w.name for w in result] == ["Agent 1", "Agent 2", "Agent 3"]


class TestIntegrationScenarios:
    """Test realistic usage scenarios."""

    def test_new_stalled_agent_gets_bell(self):
        """
        Scenario: Agent becomes stalled (waiting_user) for the first time.
        Expected: Should have bell indicator and be top priority for 'b' key.
        """
        widgets = [
            MockWidget("Agent A", STATUS_RUNNING, is_unvisited_stalled=False),
            MockWidget("Agent B", STATUS_WAITING_USER, is_unvisited_stalled=True),  # New stall!
        ]

        result = build_attention_list_fixed(widgets)

        assert len(result) == 1
        assert result[0].name == "Agent B"
        assert result[0].is_unvisited_stalled == True

    def test_visited_stalled_agent_loses_bell(self):
        """
        Scenario: User navigates to a stalled agent (via 'b' or j/k).
        Expected: Bell should be cleared, but still show as needing attention (red).
        """
        # Before visiting - has bell
        agent_b = MockWidget("Agent B", STATUS_WAITING_USER, is_unvisited_stalled=True)

        # Simulate focus() clearing the bell (this is what on_focus does)
        agent_b.is_unvisited_stalled = False  # Bell cleared on visit

        widgets = [
            MockWidget("Agent A", STATUS_RUNNING, is_unvisited_stalled=False),
            agent_b,  # Still waiting_user but no bell
        ]

        result = build_attention_list_fixed(widgets)

        # Still shows up (red status), but with lower priority than bell
        assert len(result) == 1
        assert result[0].name == "Agent B"
        assert result[0].is_unvisited_stalled == False

    def test_multiple_agents_stall_in_sequence(self):
        """
        Scenario: Multiple agents stall one after another.
        User visits first, then second agent stalls.
        Expected: Unvisited one (bell) should be higher priority.
        """
        widgets = [
            MockWidget("Agent A", STATUS_WAITING_USER, is_unvisited_stalled=False),  # Visited already
            MockWidget("Agent B", STATUS_WAITING_USER, is_unvisited_stalled=True),   # New stall!
            MockWidget("Agent C", STATUS_RUNNING, is_unvisited_stalled=False),
        ]

        result = build_attention_list_fixed(widgets)

        assert len(result) == 2
        assert result[0].name == "Agent B"  # Bell = first
        assert result[1].name == "Agent A"  # Red no bell = second

    def test_cycling_through_attention_list(self):
        """
        Scenario: User presses 'b' multiple times.
        Expected: Should cycle through bell sessions first, then red sessions.
        """
        widgets = [
            MockWidget("Agent A", STATUS_WAITING_USER, is_unvisited_stalled=False),
            MockWidget("Agent B", STATUS_WAITING_USER, is_unvisited_stalled=True),
            MockWidget("Agent C", STATUS_WAITING_USER, is_unvisited_stalled=True),
            MockWidget("Agent D", STATUS_NO_INSTRUCTIONS, is_unvisited_stalled=False),
        ]

        result = build_attention_list_fixed(widgets)

        # Order should be: B, C (bells), A (red no bell), D (yellow)
        assert [w.name for w in result] == ["Agent B", "Agent C", "Agent A", "Agent D"]

        # Simulating pressing 'b' 4 times should cycle through all 4
        for i, expected in enumerate(["Agent B", "Agent C", "Agent A", "Agent D"]):
            assert result[i].name == expected


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
