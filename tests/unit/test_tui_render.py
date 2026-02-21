"""
Unit tests for TUI render functions.

These tests verify the pure render functions that generate Rich Text objects.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock

from overcode.tui_render import (
    render_daemon_monitor_section,
    render_supervisor_section,
    render_ai_summarizer_section,
    render_spin_stats,
    render_presence_indicator,
    render_session_summary_line,
)


class TestRenderDaemonMonitorSection:
    """Tests for render_daemon_monitor_section function."""

    def test_renders_stopped_when_no_state(self):
        """Should show stopped when state is None."""
        result = render_daemon_monitor_section(
            monitor_state=None,
            is_stale=False,
        )

        plain = result.plain
        assert "Monitor:" in plain
        assert "stopped" in plain

    def test_renders_stopped_when_stale(self):
        """Should show stopped when state is stale."""
        state = Mock(status="running", loop_count=10, current_interval=5.0, daemon_version=2)

        result = render_daemon_monitor_section(
            monitor_state=state,
            is_stale=True,
        )

        plain = result.plain
        assert "stopped" in plain

    def test_renders_running_state(self):
        """Should show running status with loop count."""
        state = Mock(status="running", loop_count=42, current_interval=10.0, daemon_version=2)

        result = render_daemon_monitor_section(
            monitor_state=state,
            is_stale=False,
        )

        plain = result.plain
        assert "Monitor:" in plain
        assert "#42" in plain
        assert "@10" in plain  # May be "10s" or "10.0s"

    def test_renders_version_mismatch_warning(self):
        """Should show warning when daemon version mismatches."""
        state = Mock(status="running", loop_count=1, current_interval=5.0, daemon_version=1)

        result = render_daemon_monitor_section(
            monitor_state=state,
            is_stale=False,
        )

        plain = result.plain
        assert "⚠" in plain
        assert "v1→2" in plain


class TestRenderSupervisorSection:
    """Tests for render_supervisor_section function."""

    def test_renders_stopped_when_not_running(self):
        """Should show stopped when supervisor not running."""
        result = render_supervisor_section(
            supervisor_running=False,
            monitor_state=None,
            is_monitor_running=False,
        )

        plain = result.plain
        assert "Supervisor:" in plain
        assert "stopped" in plain

    def test_renders_ready_when_running(self):
        """Should show ready when supervisor running but idle."""
        result = render_supervisor_section(
            supervisor_running=True,
            monitor_state=Mock(supervisor_claude_running=False, total_supervisions=0),
            is_monitor_running=True,
        )

        plain = result.plain
        assert "ready" in plain

    def test_renders_claude_running(self):
        """Should show RUNNING when supervisor claude is active."""
        state = Mock(
            supervisor_claude_running=True,
            supervisor_claude_started_at=datetime.now().isoformat(),
            total_supervisions=5,
        )

        result = render_supervisor_section(
            supervisor_running=True,
            monitor_state=state,
            is_monitor_running=True,
        )

        plain = result.plain
        assert "RUNNING" in plain
        assert "🤖" in plain

    def test_renders_supervision_stats(self):
        """Should show supervision stats when available."""
        state = Mock(
            supervisor_claude_running=False,
            total_supervisions=15,
            supervisor_tokens=5000,
            supervisor_claude_total_run_seconds=120,
        )

        result = render_supervisor_section(
            supervisor_running=True,
            monitor_state=state,
            is_monitor_running=True,
        )

        plain = result.plain
        assert "sup:15" in plain
        assert "5.0K" in plain  # formatted tokens


class TestRenderAiSummarizerSection:
    """Tests for render_ai_summarizer_section function."""

    def test_renders_not_available(self):
        """Should show n/a when not available."""
        result = render_ai_summarizer_section(
            summarizer_available=False,
            summarizer_enabled=False,
            summarizer_calls=0,
        )

        plain = result.plain
        assert "AI:" in plain
        assert "n/a" in plain

    def test_renders_off_when_available_but_disabled(self):
        """Should show off when available but disabled."""
        result = render_ai_summarizer_section(
            summarizer_available=True,
            summarizer_enabled=False,
            summarizer_calls=0,
        )

        plain = result.plain
        assert "off" in plain

    def test_renders_on_when_enabled(self):
        """Should show on when enabled."""
        result = render_ai_summarizer_section(
            summarizer_available=True,
            summarizer_enabled=True,
            summarizer_calls=0,
        )

        plain = result.plain
        assert "on" in plain

    def test_renders_call_count(self):
        """Should show call count when calls made."""
        result = render_ai_summarizer_section(
            summarizer_available=True,
            summarizer_enabled=True,
            summarizer_calls=42,
        )

        plain = result.plain
        assert "42" in plain


class TestRenderSpinStats:
    """Tests for render_spin_stats function."""

    def test_renders_empty_sessions(self):
        """Should handle empty session list."""
        result = render_spin_stats(
            sessions=[],
            asleep_session_ids=set(),
        )

        plain = result.plain
        assert "Spin:" in plain
        assert "0/0" in plain

    def test_renders_all_green_sessions(self):
        """Should show green count correctly."""
        sessions = [
            Mock(session_id="1", current_status="running", green_time_seconds=100, non_green_time_seconds=0, input_tokens=1000, output_tokens=500),
            Mock(session_id="2", current_status="running", green_time_seconds=100, non_green_time_seconds=0, input_tokens=2000, output_tokens=1000),
        ]

        result = render_spin_stats(
            sessions=sessions,
            asleep_session_ids=set(),
        )

        plain = result.plain
        assert "2/2" in plain

    def test_excludes_asleep_sessions(self):
        """Should exclude asleep sessions from stats."""
        sessions = [
            Mock(session_id="1", current_status="running", green_time_seconds=100, non_green_time_seconds=0, input_tokens=1000, output_tokens=500),
            Mock(session_id="2", current_status="running", green_time_seconds=100, non_green_time_seconds=0, input_tokens=2000, output_tokens=1000),
        ]

        result = render_spin_stats(
            sessions=sessions,
            asleep_session_ids={"2"},
        )

        plain = result.plain
        assert "1/1" in plain
        assert "💤1" in plain  # Shows sleeping count

    def test_renders_total_tokens(self):
        """Should show total tokens including sleeping agents."""
        sessions = [
            Mock(session_id="1", current_status="running", green_time_seconds=100, non_green_time_seconds=0, input_tokens=1000, output_tokens=500),
        ]

        result = render_spin_stats(
            sessions=sessions,
            asleep_session_ids=set(),
        )

        plain = result.plain
        assert "Σ" in plain
        assert "1.5K" in plain


class TestRenderPresenceIndicator:
    """Tests for render_presence_indicator function."""

    def test_renders_locked_state(self):
        """Should show locked icon for state 1."""
        result = render_presence_indicator(
            presence_state=1,
            idle_seconds=0,
        )

        plain = result.plain
        assert "🔒" in plain

    def test_renders_idle_state(self):
        """Should show meditating icon for state 2."""
        result = render_presence_indicator(
            presence_state=2,
            idle_seconds=120,
        )

        plain = result.plain
        assert "🧘" in plain
        assert "120s" in plain

    def test_renders_active_state(self):
        """Should show walking icon for state 3."""
        result = render_presence_indicator(
            presence_state=3,
            idle_seconds=5,
        )

        plain = result.plain
        assert "🚶" in plain
        assert "5s" in plain


class TestRenderSessionSummaryLine:
    """Tests for render_session_summary_line function."""

    def test_renders_basic_info(self):
        """Should render basic session info."""
        result = render_session_summary_line(
            name="test-agent",
            detected_status="running",
            expanded=False,
            summary_detail="low",
            start_time=datetime.now().isoformat(),
            repo_name="my-repo",
            branch="main",
            green_time=3600,
            non_green_time=600,
            permissiveness_mode="normal",
            state_since=None,
            local_status_changed_at=None,
            steers_count=5,
            total_tokens=10000,
            current_context_tokens=50000,
            interaction_count=15,
            median_work_time=120,
            git_diff_stats=None,
            is_unvisited_stalled=False,
            has_focus=False,
            is_list_mode=False,
        )

        plain = result.plain
        assert "test-agent" in plain
        assert "▶" in plain  # collapsed indicator

    def test_renders_expanded_indicator(self):
        """Should show expanded indicator when expanded."""
        result = render_session_summary_line(
            name="test-agent",
            detected_status="running",
            expanded=True,
            summary_detail="low",
            start_time=datetime.now().isoformat(),
            repo_name=None,
            branch=None,
            green_time=0,
            non_green_time=0,
            permissiveness_mode="normal",
            state_since=None,
            local_status_changed_at=None,
            steers_count=0,
            total_tokens=None,
            current_context_tokens=None,
            interaction_count=None,
            median_work_time=0,
            git_diff_stats=None,
            is_unvisited_stalled=False,
            has_focus=False,
            is_list_mode=False,
        )

        plain = result.plain
        assert "▼" in plain  # expanded indicator

    def test_renders_stalled_indicator(self):
        """Should show bell indicator for unvisited stalled agent."""
        result = render_session_summary_line(
            name="stalled-agent",
            detected_status="waiting_user",
            expanded=False,
            summary_detail="low",
            start_time=datetime.now().isoformat(),
            repo_name=None,
            branch=None,
            green_time=0,
            non_green_time=0,
            permissiveness_mode="normal",
            state_since=None,
            local_status_changed_at=None,
            steers_count=0,
            total_tokens=None,
            current_context_tokens=None,
            interaction_count=None,
            median_work_time=0,
            git_diff_stats=None,
            is_unvisited_stalled=True,
            has_focus=False,
            is_list_mode=False,
        )

        plain = result.plain
        assert "🔔" in plain

    def test_renders_full_detail(self):
        """Should show all info in full detail mode."""
        result = render_session_summary_line(
            name="full-agent",
            detected_status="running",
            expanded=False,
            summary_detail="full",
            start_time=(datetime.now() - timedelta(hours=2)).isoformat(),
            repo_name="my-repo",
            branch="feature",
            green_time=3600,
            non_green_time=1800,
            permissiveness_mode="bypass",
            state_since=datetime.now().isoformat(),
            local_status_changed_at=None,
            steers_count=10,
            total_tokens=50000,
            current_context_tokens=100000,
            interaction_count=25,
            median_work_time=300,
            git_diff_stats=(5, 100, 50),
            is_unvisited_stalled=False,
            has_focus=False,
            is_list_mode=False,
        )

        plain = result.plain
        assert "my-repo:feature" in plain
        assert "🔥" in plain  # bypass mode
        assert "Δ" in plain  # git diff

    def test_renders_list_mode_focus(self):
        """Should show focus indicator in list mode."""
        result = render_session_summary_line(
            name="focused-agent",
            detected_status="running",
            expanded=False,
            summary_detail="low",
            start_time=datetime.now().isoformat(),
            repo_name=None,
            branch=None,
            green_time=0,
            non_green_time=0,
            permissiveness_mode="normal",
            state_since=None,
            local_status_changed_at=None,
            steers_count=0,
            total_tokens=None,
            current_context_tokens=None,
            interaction_count=None,
            median_work_time=0,
            git_diff_stats=None,
            is_unvisited_stalled=False,
            has_focus=True,
            is_list_mode=True,
        )

        plain = result.plain
        assert "→" in plain  # focus indicator

    def test_renders_different_permission_modes(self):
        """Should show correct emoji for each permission mode."""
        for mode, expected_emoji in [("normal", "👮"), ("permissive", "🏃"), ("bypass", "🔥")]:
            result = render_session_summary_line(
                name="agent",
                detected_status="running",
                expanded=False,
                summary_detail="low",
                start_time=datetime.now().isoformat(),
                repo_name=None,
                branch=None,
                green_time=0,
                non_green_time=0,
                permissiveness_mode=mode,
                state_since=None,
                local_status_changed_at=None,
                steers_count=0,
                total_tokens=None,
                current_context_tokens=None,
                interaction_count=None,
                median_work_time=0,
                git_diff_stats=None,
                is_unvisited_stalled=False,
                has_focus=False,
                is_list_mode=False,
            )

            plain = result.plain
            assert expected_emoji in plain, f"Expected {expected_emoji} for mode {mode}"


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
