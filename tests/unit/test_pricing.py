"""
Unit tests for the pricing module.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import pytest

from overcode.pricing import (
    ModelPricing,
    MODEL_PRICING,
    calculate_cost_estimate,
    estimate_cost,
    lookup_pricing,
)


class TestModelPricing:
    """Test the ModelPricing dataclass."""

    def test_has_cache_defaults(self):
        """Cache pricing defaults to zero."""
        p = ModelPricing(input=1.0, output=2.0)
        assert p.cache_write == 0.0
        assert p.cache_read == 0.0

    def test_explicit_cache_pricing(self):
        """Can set explicit cache pricing."""
        p = ModelPricing(input=1.0, output=2.0, cache_write=3.0, cache_read=4.0)
        assert p.cache_write == 3.0
        assert p.cache_read == 4.0


class TestModelPricingTable:
    """Test built-in MODEL_PRICING table."""

    def test_has_claude_models(self):
        assert "opus" in MODEL_PRICING
        assert "sonnet" in MODEL_PRICING
        assert "haiku" in MODEL_PRICING

    def test_has_openai_models(self):
        assert "gpt-4o-mini" in MODEL_PRICING
        assert "gpt-4o" in MODEL_PRICING


class TestLookupPricing:
    """Test lookup_pricing substring matching."""

    def test_matches_claude_opus(self):
        p = lookup_pricing("claude-opus-4-6")
        assert p.input == 15.0

    def test_matches_gpt_4o_mini(self):
        p = lookup_pricing("gpt-4o-mini")
        assert p.input == 0.15

    def test_matches_gpt_4o_not_mini(self):
        """gpt-4o should match gpt-4o pricing, not gpt-4o-mini."""
        p = lookup_pricing("gpt-4o-2024-08-06")
        assert p.input == 2.50

    def test_unknown_model_returns_zero(self):
        p = lookup_pricing("unknown-model-xyz")
        assert p.input == 0.0
        assert p.output == 0.0

    def test_case_insensitive(self):
        p = lookup_pricing("GPT-4O-MINI")
        assert p.input == 0.15


class TestEstimateCost:
    """Test the estimate_cost convenience function."""

    def test_gpt_4o_mini_cost(self):
        """1M input + 1M output tokens at gpt-4o-mini rates."""
        cost = estimate_cost("gpt-4o-mini", 1_000_000, 1_000_000)
        assert cost == pytest.approx(0.75)  # $0.15 + $0.60

    def test_opus_cost(self):
        """1M input + 1M output tokens at opus rates."""
        cost = estimate_cost("claude-opus-4-6", 1_000_000, 1_000_000)
        assert cost == pytest.approx(90.0)  # $15 + $75

    def test_zero_tokens(self):
        cost = estimate_cost("gpt-4o-mini", 0, 0)
        assert cost == 0.0

    def test_unknown_model_zero_cost(self):
        cost = estimate_cost("unknown-model", 1_000_000, 1_000_000)
        assert cost == 0.0


class TestCalculateCostEstimate:
    """Test backward-compatible calculate_cost_estimate function."""

    def test_zero_tokens(self):
        assert calculate_cost_estimate(0, 0) == 0.0

    def test_with_custom_pricing(self):
        cost = calculate_cost_estimate(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            price_input=3.0,
            price_output=15.0,
        )
        assert cost == pytest.approx(18.0)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
