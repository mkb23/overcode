"""
Unit tests for TUI render functions.

These tests verify the pure render functions that generate Rich Text objects.
"""

import pytest

from overcode.tui_render import render_ai_summarizer_section


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

    def test_renders_cost_when_enabled(self):
        """Should show cost alongside call count when enabled."""
        result = render_ai_summarizer_section(
            summarizer_available=True,
            summarizer_enabled=True,
            summarizer_calls=10,
            summarizer_cost_usd=1.23,
        )

        plain = result.plain
        assert "$1.23" in plain

    def test_renders_cost_when_disabled(self):
        """Should show accumulated cost even when disabled (dimmed)."""
        result = render_ai_summarizer_section(
            summarizer_available=True,
            summarizer_enabled=False,
            summarizer_calls=0,
            summarizer_cost_usd=0.50,
        )

        plain = result.plain
        assert "$0.50" in plain

    def test_no_cost_when_zero(self):
        """Should not show cost when zero."""
        result = render_ai_summarizer_section(
            summarizer_available=True,
            summarizer_enabled=True,
            summarizer_calls=5,
            summarizer_cost_usd=0.0,
        )

        plain = result.plain
        assert "$" not in plain

    def test_renders_halted_when_cost_cap_hit(self):
        """Should show HALTED with cost when cap is hit."""
        result = render_ai_summarizer_section(
            summarizer_available=True,
            summarizer_enabled=False,
            summarizer_calls=100,
            summarizer_cost_usd=100.0,
            summarizer_cost_cap=100.0,
            summarizer_cost_cap_hit=True,
        )

        plain = result.plain
        assert "HALTED" in plain
        assert "$" in plain

    def test_cost_color_green_under_50_pct(self):
        """Cost should be green when under 50% of cap."""
        result = render_ai_summarizer_section(
            summarizer_available=True,
            summarizer_enabled=True,
            summarizer_calls=10,
            summarizer_cost_usd=10.0,
            summarizer_cost_cap=100.0,
        )
        # Check the style of the cost span
        spans = result._spans
        cost_spans = [s for s in spans if "$" in result.plain[s.start:s.end]]
        assert any("green" in s.style for s in cost_spans)

    def test_cost_color_yellow_at_60_pct(self):
        """Cost should be yellow at 50-80% of cap."""
        result = render_ai_summarizer_section(
            summarizer_available=True,
            summarizer_enabled=True,
            summarizer_calls=10,
            summarizer_cost_usd=60.0,
            summarizer_cost_cap=100.0,
        )
        spans = result._spans
        cost_spans = [s for s in spans if "$" in result.plain[s.start:s.end]]
        assert any("yellow" in s.style for s in cost_spans)

    def test_cost_color_orange_at_90_pct(self):
        """Cost should be orange at 80-100% of cap."""
        result = render_ai_summarizer_section(
            summarizer_available=True,
            summarizer_enabled=True,
            summarizer_calls=10,
            summarizer_cost_usd=90.0,
            summarizer_cost_cap=100.0,
        )
        spans = result._spans
        cost_spans = [s for s in spans if "$" in result.plain[s.start:s.end]]
        assert any("orange" in s.style for s in cost_spans)

    def test_cost_color_red_at_cap(self):
        """Cost should be red bold at/above cap."""
        result = render_ai_summarizer_section(
            summarizer_available=True,
            summarizer_enabled=False,
            summarizer_calls=100,
            summarizer_cost_usd=100.0,
            summarizer_cost_cap=100.0,
            summarizer_cost_cap_hit=True,
        )
        spans = result._spans
        cost_spans = [s for s in spans if "$" in result.plain[s.start:s.end]]
        assert any("red" in s.style for s in cost_spans)


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
