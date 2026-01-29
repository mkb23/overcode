"""
Unit tests for the summarizer client and component.

Tests the two-prompt summarizer system that generates:
- Short summaries: current activity (~40 chars)
- Context summaries: wider goal (~60 chars)
"""

import os
import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.summarizer_client import (
    SummarizerClient,
    SUMMARIZE_PROMPT_SHORT,
    SUMMARIZE_PROMPT_CONTEXT,
)
from overcode.summarizer_component import (
    SummarizerComponent,
    SummarizerConfig,
    AgentSummary,
)


class TestSummarizerPrompts:
    """Test that the two prompts exist and have correct focus."""

    def test_short_prompt_exists(self):
        """Short prompt template exists."""
        assert SUMMARIZE_PROMPT_SHORT is not None
        assert len(SUMMARIZE_PROMPT_SHORT) > 0

    def test_context_prompt_exists(self):
        """Context prompt template exists."""
        assert SUMMARIZE_PROMPT_CONTEXT is not None
        assert len(SUMMARIZE_PROMPT_CONTEXT) > 0

    def test_short_prompt_focuses_on_current_activity(self):
        """Short prompt emphasizes current/immediate action."""
        prompt_lower = SUMMARIZE_PROMPT_SHORT.lower()
        assert "current" in prompt_lower or "right now" in prompt_lower
        assert "40 char" in prompt_lower or "40char" in prompt_lower.replace(" ", "")

    def test_context_prompt_focuses_on_wider_goal(self):
        """Context prompt emphasizes broader task/goal."""
        prompt_lower = SUMMARIZE_PROMPT_CONTEXT.lower()
        assert "context" in prompt_lower or "overall" in prompt_lower or "goal" in prompt_lower
        assert "60 char" in prompt_lower or "60char" in prompt_lower.replace(" ", "")

    def test_prompts_have_placeholders(self):
        """Both prompts have required placeholders."""
        for prompt in [SUMMARIZE_PROMPT_SHORT, SUMMARIZE_PROMPT_CONTEXT]:
            assert "{lines}" in prompt
            assert "{pane_content}" in prompt
            assert "{previous_summary}" in prompt


class TestSummarizerClientMode:
    """Test the mode parameter in SummarizerClient.summarize()."""

    def test_summarize_accepts_mode_parameter(self):
        """summarize() method accepts mode parameter."""
        client = SummarizerClient.__new__(SummarizerClient)
        client.api_url = "http://test"
        client.model = "test"
        client.api_key = None
        client._available = False

        # Should not raise - just return None because no API key
        result = client.summarize(
            pane_content="test",
            previous_summary="",
            current_status="running",
            mode="short"
        )
        assert result is None

        result = client.summarize(
            pane_content="test",
            previous_summary="",
            current_status="running",
            mode="context"
        )
        assert result is None

    @patch('overcode.summarizer_client.urllib.request.urlopen')
    def test_short_mode_uses_short_prompt(self, mock_urlopen):
        """Mode 'short' uses SUMMARIZE_PROMPT_SHORT."""
        # Mock successful API response
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = b'{"choices": [{"message": {"content": "test summary"}}]}'
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)
        mock_urlopen.return_value = mock_response

        client = SummarizerClient.__new__(SummarizerClient)
        client.api_url = "http://test"
        client.model = "test"
        client.api_key = "test-key"
        client._available = True

        client.summarize(
            pane_content="test content",
            previous_summary="prev",
            current_status="running",
            mode="short"
        )

        # Check that the request was made with short prompt content
        call_args = mock_urlopen.call_args
        request = call_args[0][0]
        # The prompt should contain "RIGHT NOW" (from short prompt - immediate action)
        assert b"RIGHT NOW" in request.data or b"right now" in request.data.lower()

    @patch('overcode.summarizer_client.urllib.request.urlopen')
    def test_context_mode_uses_context_prompt(self, mock_urlopen):
        """Mode 'context' uses SUMMARIZE_PROMPT_CONTEXT."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = b'{"choices": [{"message": {"content": "test summary"}}]}'
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)
        mock_urlopen.return_value = mock_response

        client = SummarizerClient.__new__(SummarizerClient)
        client.api_url = "http://test"
        client.model = "test"
        client.api_key = "test-key"
        client._available = True

        client.summarize(
            pane_content="test content",
            previous_summary="prev",
            current_status="running",
            mode="context"
        )

        call_args = mock_urlopen.call_args
        request = call_args[0][0]
        # The prompt should contain "TASK" or "FEATURE" (from context prompt - goal focused)
        data_lower = request.data.lower()
        assert b"task" in data_lower or b"feature" in data_lower


class TestAgentSummaryDataclass:
    """Test the AgentSummary dataclass."""

    def test_has_short_summary_fields(self):
        """AgentSummary has text and updated_at for short summary."""
        summary = AgentSummary()
        assert hasattr(summary, 'text')
        assert hasattr(summary, 'updated_at')
        assert summary.text == ""
        assert summary.updated_at is None

    def test_has_context_summary_fields(self):
        """AgentSummary has context and context_updated_at for context summary."""
        summary = AgentSummary()
        assert hasattr(summary, 'context')
        assert hasattr(summary, 'context_updated_at')
        assert summary.context == ""
        assert summary.context_updated_at is None

    def test_can_set_both_summaries(self):
        """Can set both short and context summaries."""
        summary = AgentSummary(
            text="reading config",
            updated_at="2024-01-01T00:00:00",
            context="implementing auth system",
            context_updated_at="2024-01-01T00:00:00",
        )
        assert summary.text == "reading config"
        assert summary.context == "implementing auth system"


class TestSummarizerConfig:
    """Test the SummarizerConfig dataclass."""

    def test_has_context_interval(self):
        """SummarizerConfig has context_interval field."""
        config = SummarizerConfig()
        assert hasattr(config, 'context_interval')

    def test_default_intervals(self):
        """Default intervals are sensible."""
        config = SummarizerConfig()
        # Short interval should be frequent (5s default)
        assert config.interval == 5.0
        # Context interval should be less frequent (15s default)
        assert config.context_interval == 15.0
        # Context should be slower than short
        assert config.context_interval > config.interval

    def test_custom_intervals(self):
        """Can set custom intervals."""
        config = SummarizerConfig(interval=10.0, context_interval=60.0)
        assert config.interval == 10.0
        assert config.context_interval == 60.0


class TestSummarizerComponentDualSummaries:
    """Test that SummarizerComponent generates both summary types."""

    def test_component_tracks_both_update_times(self):
        """Component has separate tracking for short and context updates."""
        component = SummarizerComponent(
            tmux_session="test",
            config=SummarizerConfig(enabled=False),
        )
        assert hasattr(component, '_last_update')
        assert hasattr(component, '_last_context_update')
        assert isinstance(component._last_update, dict)
        assert isinstance(component._last_context_update, dict)

    def test_summaries_dict_contains_agent_summary_objects(self):
        """Summaries dict stores AgentSummary objects with both fields."""
        component = SummarizerComponent(
            tmux_session="test",
            config=SummarizerConfig(enabled=False),
        )
        # Manually add a summary
        component.summaries["test-id"] = AgentSummary(
            text="short",
            context="long context"
        )

        summary = component.summaries["test-id"]
        assert summary.text == "short"
        assert summary.context == "long context"


class TestMonitorDaemonStateFields:
    """Test that monitor daemon state has both summary fields."""

    def test_session_daemon_state_has_context_fields(self):
        """SessionDaemonState has activity_summary_context fields."""
        from overcode.monitor_daemon_state import SessionDaemonState

        state = SessionDaemonState()
        assert hasattr(state, 'activity_summary')
        assert hasattr(state, 'activity_summary_updated')
        assert hasattr(state, 'activity_summary_context')
        assert hasattr(state, 'activity_summary_context_updated')

    def test_session_daemon_state_serialization(self):
        """SessionDaemonState serializes context fields correctly."""
        from overcode.monitor_daemon_state import SessionDaemonState

        state = SessionDaemonState(
            session_id="test",
            activity_summary="reading files",
            activity_summary_context="implementing auth",
        )

        data = state.to_dict()
        assert data["activity_summary"] == "reading files"
        assert data["activity_summary_context"] == "implementing auth"

    def test_session_daemon_state_deserialization(self):
        """SessionDaemonState deserializes context fields correctly."""
        from overcode.monitor_daemon_state import SessionDaemonState

        data = {
            "session_id": "test",
            "activity_summary": "writing tests",
            "activity_summary_context": "fixing bug in login",
        }

        state = SessionDaemonState.from_dict(data)
        assert state.activity_summary == "writing tests"
        assert state.activity_summary_context == "fixing bug in login"


class TestSummarizerComponentMethods:
    """Tests for SummarizerComponent instance methods."""

    def test_available_property(self):
        """Should check if API key is available."""
        component = SummarizerComponent(
            tmux_session="test",
            config=SummarizerConfig(enabled=False),
        )

        with patch.object(SummarizerClient, 'is_available', return_value=True):
            assert component.available is True

        with patch.object(SummarizerClient, 'is_available', return_value=False):
            assert component.available is False

    def test_enabled_property_false_when_disabled(self):
        """Should return False when config.enabled is False."""
        component = SummarizerComponent(
            tmux_session="test",
            config=SummarizerConfig(enabled=False),
        )
        assert component.enabled is False

    def test_enabled_property_false_when_no_client(self):
        """Should return False when client is None."""
        with patch.object(SummarizerClient, 'is_available', return_value=False):
            component = SummarizerComponent(
                tmux_session="test",
                config=SummarizerConfig(enabled=True),
            )
            assert component.enabled is False

    def test_update_returns_summaries_when_disabled(self):
        """Should return existing summaries when disabled."""
        component = SummarizerComponent(
            tmux_session="test",
            config=SummarizerConfig(enabled=False),
        )
        component.summaries = {"test-id": AgentSummary(text="existing")}

        result = component.update([])

        assert result == {"test-id": AgentSummary(text="existing")}

    def test_get_summary_returns_existing(self):
        """Should return existing summary for session."""
        component = SummarizerComponent(
            tmux_session="test",
            config=SummarizerConfig(enabled=False),
        )
        expected = AgentSummary(text="test summary")
        component.summaries["test-id"] = expected

        result = component.get_summary("test-id")

        assert result == expected

    def test_get_summary_returns_none_for_unknown(self):
        """Should return None for unknown session."""
        component = SummarizerComponent(
            tmux_session="test",
            config=SummarizerConfig(enabled=False),
        )

        result = component.get_summary("unknown-id")

        assert result is None

    def test_stop_closes_client(self):
        """Should close client on stop."""
        component = SummarizerComponent(
            tmux_session="test",
            config=SummarizerConfig(enabled=False),
        )
        mock_client = Mock()
        component._client = mock_client

        component.stop()

        mock_client.close.assert_called_once()
        assert component._client is None

    def test_stop_handles_no_client(self):
        """Should handle stop when no client exists."""
        component = SummarizerComponent(
            tmux_session="test",
            config=SummarizerConfig(enabled=False),
        )
        component._client = None

        # Should not raise
        component.stop()

    def test_capture_pane_returns_content(self):
        """Should return pane content."""
        mock_tmux = Mock()
        mock_tmux.capture_pane.return_value = "line1\nline2\nline3\n\n"

        component = SummarizerComponent(
            tmux_session="test",
            tmux=mock_tmux,
            config=SummarizerConfig(enabled=False, lines=100),
        )

        result = component._capture_pane(1)

        assert result == "line1\nline2\nline3"
        mock_tmux.capture_pane.assert_called_once()

    def test_capture_pane_returns_none_on_error(self):
        """Should return None when capture fails."""
        mock_tmux = Mock()
        mock_tmux.capture_pane.side_effect = Exception("tmux error")

        component = SummarizerComponent(
            tmux_session="test",
            tmux=mock_tmux,
            config=SummarizerConfig(enabled=False),
        )

        result = component._capture_pane(1)

        assert result is None

    def test_capture_pane_returns_none_for_empty_content(self):
        """Should return None when pane content is empty."""
        mock_tmux = Mock()
        mock_tmux.capture_pane.return_value = None

        component = SummarizerComponent(
            tmux_session="test",
            tmux=mock_tmux,
            config=SummarizerConfig(enabled=False),
        )

        result = component._capture_pane(1)

        assert result is None


class TestSummarizerClientMethods:
    """Tests for SummarizerClient methods."""

    def test_is_available_returns_true_with_api_key(self):
        """Should return True when API key is available."""
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'}):
            # Re-check availability
            assert SummarizerClient.is_available() is True

    def test_is_available_checks_env_and_config(self):
        """Should check environment and config for API key."""
        # Just verify the method exists and returns a boolean
        result = SummarizerClient.is_available()
        assert isinstance(result, bool)

    def test_close_is_noop(self):
        """Close should be a no-op."""
        client = SummarizerClient.__new__(SummarizerClient)
        client.api_key = None

        # Should not raise
        client.close()


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
