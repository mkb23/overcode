"""
Standalone pricing module for token cost estimation.

Provides model pricing tables and cost calculation for both Claude and
third-party models (OpenAI, etc.) used across the codebase — agent sessions
and the AI summariser.
"""

from dataclasses import dataclass


@dataclass
class ModelPricing:
    """Per-million-token pricing for a model."""
    input: float
    output: float
    cache_write: float = 0.0
    cache_read: float = 0.0


# Built-in pricing for known model families.
# Keys are checked as substrings against model names, LONGEST KEY FIRST, so
# "opus-4-1" matches "claude-opus-4-1-..." before the generic "opus" entry,
# and "gpt-4o-mini" matches before "gpt-4o". Keep this in mind when adding
# versioned keys: the more specific (longer) key must be a substring of the
# model id you want it to win for.
#
# Claude pricing source: https://platform.claude.com/docs/en/about-claude/pricing
# (verified June 2026). Bedrock on-demand list prices are identical to these;
# any AWS private-offer / committed-use discount is applied separately (see
# the bedrock discount handling in settings.get_model_pricing).
MODEL_PRICING: dict[str, ModelPricing] = {
    # Claude models — order here is cosmetic; lookups sort by key length.
    # Current Opus (4.5 / 4.6 / 4.7 / 4.8) — $5 / $25.
    "opus":         ModelPricing(input=5.0,  output=25.0, cache_write=6.25,  cache_read=0.50),
    # Legacy Opus (4.1 and the deprecated original 4.0) — $15 / $75.
    "opus-4-1":     ModelPricing(input=15.0, output=75.0, cache_write=18.75, cache_read=1.50),
    "opus-4-2025":  ModelPricing(input=15.0, output=75.0, cache_write=18.75, cache_read=1.50),
    # Sonnet 4.x — $3 / $15.
    "sonnet":       ModelPricing(input=3.0,  output=15.0, cache_write=3.75,  cache_read=0.30),
    # Current Haiku (4.5) — $1 / $5.
    "haiku":        ModelPricing(input=1.0,  output=5.0,  cache_write=1.25,  cache_read=0.10),
    # Legacy Haiku 3.5 (retired, but still served on Bedrock/Vertex) — $0.80 / $4.
    # Note: Claude 3.x ids put the version before the family ("claude-3-5-haiku").
    "3-5-haiku":    ModelPricing(input=0.80, output=4.0,  cache_write=1.0,   cache_read=0.08),
    # OpenAI models (commonly used for summariser)
    "gpt-4o-mini": ModelPricing(input=0.15, output=0.60),
    "gpt-4o":      ModelPricing(input=2.50, output=10.0),
    "gpt-4-turbo": ModelPricing(input=10.0, output=30.0),
    "gpt-3.5":     ModelPricing(input=0.50, output=1.50),
}


def lookup_pricing(model: str) -> ModelPricing:
    """Look up pricing for a model name by substring match.

    Args:
        model: Model name (e.g. "gpt-4o-mini", "claude-haiku-4-5-20250929")

    Returns:
        ModelPricing for the model family, or a zero-cost fallback if unknown.
    """
    model_lower = model.lower()
    # Try longer keys first to avoid "gpt-4o" matching before "gpt-4o-mini"
    for key in sorted(MODEL_PRICING, key=len, reverse=True):
        if key in model_lower:
            return MODEL_PRICING[key]
    return ModelPricing(input=0.0, output=0.0)


def calculate_cost_estimate(
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
    price_input: float = 3.0,
    price_output: float = 15.0,
    price_cache_write: float = 3.75,
    price_cache_read: float = 0.30,
) -> float:
    """Calculate estimated cost from token counts.

    Pure function - no side effects, fully testable.

    Args:
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens
        cache_creation_tokens: Number of cache creation tokens
        cache_read_tokens: Number of cache read tokens
        price_input: Price per million input tokens (default: Sonnet)
        price_output: Price per million output tokens (default: Sonnet)
        price_cache_write: Price per million cache write tokens (default: Sonnet)
        price_cache_read: Price per million cache read tokens (default: Sonnet)

    Returns:
        Estimated cost in USD
    """
    return (
        (input_tokens / 1_000_000) * price_input
        + (output_tokens / 1_000_000) * price_output
        + (cache_creation_tokens / 1_000_000) * price_cache_write
        + (cache_read_tokens / 1_000_000) * price_cache_read
    )


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Convenience: estimate cost for a model from input/output tokens.

    Args:
        model: Model name (e.g. "gpt-4o-mini")
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens

    Returns:
        Estimated cost in USD
    """
    pricing = lookup_pricing(model)
    return calculate_cost_estimate(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        price_input=pricing.input,
        price_output=pricing.output,
    )
