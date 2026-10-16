"""
Standalone pricing module for token cost estimation.

Provides model pricing tables and cost calculation for both Claude and
third-party models (OpenAI, etc.) used across the codebase — agent sessions
and the AI summariser.
"""

import re
from dataclasses import dataclass, replace
from typing import Optional

from . import model_metadata, price_table


# A 1-hour TTL cache write costs this many times the input price (Anthropic
# prompt caching: 5-minute writes are 1.25x, 1-hour writes 2x). Claude
# Code writes its prompt cache with the 1-hour TTL.
CACHE_WRITE_1H_INPUT_MULTIPLE = 2.0


@dataclass
class ModelPricing:
    """Per-million-token pricing for a model.

    ``cache_write`` is the 5-minute TTL cache write; ``cache_write_1h``
    the 1-hour one, ``CACHE_WRITE_1H_INPUT_MULTIPLE`` times the input
    price unless set (``cache_write_1h_price``).

    ``long`` is the long-context tier: what a whole request costs when its
    prompt (input + cache read + cache write tokens) passes ``long_above``
    tokens (Haiku 5.5 over 100K, Sonnet 4 over 200K). ``regional`` is
    Amazon Bedrock's charge on a geographic or in-region inference profile
    over the ``global.`` one. ``net`` marks a ``model_pricing:`` row that is
    already the price paid: no Bedrock premium or discount applies to it.
    ``source`` says where the row came from.
    """
    input: float
    output: float
    cache_write: float = 0.0
    cache_read: float = 0.0
    cache_write_1h: Optional[float] = None
    long: Optional["ModelPricing"] = None
    long_above: Optional[int] = None
    # None only on a parsed model_pricing row: the built-in row's applies
    regional: Optional[float] = 1.0
    net: bool = False
    source: str = ""

    @property
    def cache_write_1h_price(self) -> float:
        if self.cache_write_1h is not None:
            return float(self.cache_write_1h)
        return float(self.input) * CACHE_WRITE_1H_INPUT_MULTIPLE

    def per_million(self) -> tuple:
        """The price row ``conversation_spend.price`` takes.

        (input, output, cache write, cache read, 1-hour cache write), then
        the same five of the long-context tier when the model has one.
        """
        base = (self.input, self.output, self.cache_write, self.cache_read,
                self.cache_write_1h_price)
        if self.long is None:
            return base
        return base + self.long.per_million()[:5]

    def scaled(self, factor: float) -> "ModelPricing":
        """Every rate, both tiers, times ``factor``."""
        if factor == 1.0:
            return self
        return replace(
            self,
            input=self.input * factor,
            output=self.output * factor,
            cache_write=self.cache_write * factor,
            cache_read=self.cache_read * factor,
            cache_write_1h=(None if self.cache_write_1h is None
                            else self.cache_write_1h * factor),
            long=None if self.long is None else self.long.scaled(factor),
        )


# Built-in pricing for known model families: the FALLBACK behind the price
# table (``price_table``: one dated, sourced row per Claude model id). A
# model with its own row there is priced by it; these keys answer only for
# ids the table does not name (an alias such as "haiku", a new release),
# and ``overcode doctor`` warns when a Claude model is priced only here.
# Keys are checked as substrings against model names, LONGEST KEY FIRST, so
# "opus-4-1" matches "claude-opus-4-1-..." before the generic "opus" entry,
# and "gpt-4o-mini" matches before "gpt-4o". Keep this in mind when adding
# versioned keys: the more specific (longer) key must be a substring of the
# model id you want it to win for.
#
# Claude rows: Anthropic list prices (the AWS Price List's global column of
# 2026-10-12, equal to https://platform.claude.com/docs/en/about-claude/pricing).
# ``regional`` is Bedrock's premium on geographic / in-region profiles: 1.1
# on every model from the 4.5 generation on, none on older ones. Any AWS
# private-offer / committed-use discount is applied separately (see the
# bedrock handling in settings.get_model_pricing).
_REGIONAL = 1.1
MODEL_PRICING: dict[str, ModelPricing] = {
    # Claude models — order here is cosmetic; lookups sort by key length.
    # Fable — Fable 5's $10 / $50, cache read $1 (Fable 5.1's own row reads $0.25).
    "fable":        ModelPricing(input=10.0, output=50.0, cache_write=12.50, cache_read=1.00,
                                 regional=_REGIONAL),
    # Current Opus (4.5 / 4.6 / 4.7 / 4.8 / 5) — $5 / $25.
    "opus":         ModelPricing(input=5.0,  output=25.0, cache_write=6.25,  cache_read=0.50,
                                 regional=_REGIONAL),
    # Legacy Opus (4.1 and the deprecated original 4.0) — $15 / $75.
    "opus-4-1":     ModelPricing(input=15.0, output=75.0, cache_write=18.75, cache_read=1.50),
    "opus-4-2025":  ModelPricing(input=15.0, output=75.0, cache_write=18.75, cache_read=1.50),
    # Opus 5.5 (claude-opus-5-5) — $4 / $20, cache read $0.20 (#519).
    "opus-5-5":     ModelPricing(input=4.0,  output=20.0, cache_write=5.00,  cache_read=0.20,
                                 regional=_REGIONAL),
    # Sonnet 4.x — $3 / $15.
    "sonnet":       ModelPricing(input=3.0,  output=15.0, cache_write=3.75,  cache_read=0.30,
                                 regional=_REGIONAL),
    # Sonnet 5 (claude-sonnet-5) — $2 / $10 (#519; was priced as Sonnet 4.x).
    "sonnet-5":     ModelPricing(input=2.0,  output=10.0, cache_write=2.50,  cache_read=0.20,
                                 regional=_REGIONAL),
    # Haiku 4.5 — $1 / $5: the `haiku` alias on Bedrock and Vertex. (On the
    # Anthropic API the alias is Haiku 5.5, $0.10 / $0.50, which has its own
    # row: this key priced it 10x too high before the price table.)
    "haiku":        ModelPricing(input=1.0,  output=5.0,  cache_write=1.25,  cache_read=0.10,
                                 regional=_REGIONAL),
    # Legacy Haiku 3.5 (retired, but still served on Bedrock/Vertex) — $0.80 / $4.
    # Note: Claude 3.x ids put the version before the family ("claude-3-5-haiku").
    "3-5-haiku":    ModelPricing(input=0.80, output=4.0,  cache_write=1.0,   cache_read=0.08),
    # OpenAI models (commonly used for summariser)
    "gpt-4o-mini": ModelPricing(input=0.15, output=0.60),
    "gpt-4o":      ModelPricing(input=2.50, output=10.0),
    "gpt-4-turbo": ModelPricing(input=10.0, output=30.0),
    "gpt-3.5":     ModelPricing(input=0.50, output=1.50),
    # Codex CLI's account-default model (Appendix A of
    # docs/design/agent-backends-codex-grok.md — the bare id codex's `-m`
    # flag takes). Standard-tier, short-context (<128k) API pricing, sourced
    # from OpenAI's own pricing docs (developers.openai.com/api/docs/pricing,
    # verified Aug 28, 2026): $4.00 / $20.00 per million input/output tokens,
    # $0.40 cached-input. This is promotional pricing OpenAI has said runs
    # through ~Nov 21, 2026 (a 20%+ cut from the July list price); it may
    # revert afterward. codex is subscription/ChatGPT-billed with no local
    # per-turn cost recorded (see codex_stats.py), so this entry only feeds
    # the cost *estimate* `CodexStatsReader`'s caller falls back to — not a
    # verified-against-a-real-invoice figure the way the Claude table is.
    # Batch/Flex/Fast-mode and long-context (>=128k) tiers are cheaper/pricier
    # respectively and are not modelled here — overcode has no signal for
    # which tier a given codex turn ran under.
    "gpt-5.6-sol": ModelPricing(input=4.0, output=20.0, cache_read=0.40),
    # Grok Build's account-default models (Appendix B of the same design
    # doc). Standard-tier, short-context (<200k) API pricing, sourced from
    # xAI's own docs (docs.x.ai/docs/models, verified Aug 28, 2026). Unlike
    # codex, grok's `GrokStatsReader` reads a real local `costUsdTicks`
    # figure per turn (billing-accurate, not an estimate) — these entries are
    # the *fallback* pricing.py path uses only when that figure is
    # unavailable, and were cross-checked against Phase 4's real stored-cost
    # sample (a ~4.13M-input-token/3.34M-cached/137k-output batch billed at
    # 7,295,125,400 costUsdTicks = $7.295): the long-context tier below
    # ($4.00/$1.00 cached/$12.00) prices that same batch at ~$8.15, within
    # ~12% of the real billed figure — consistent with a session whose
    # per-call context had grown past the 200k long-context threshold.
    # Long-context tiers (>=200k tokens, billed on the *whole* request) and
    # the grok-4.6-vs-4.5 cached-input difference are not modelled here;
    # standard-tier short-context rates are the representative value.
    "grok-4.6":    ModelPricing(input=2.0, output=6.0, cache_read=0.50),
    "grok-4.5":    ModelPricing(input=2.0, output=6.0, cache_read=0.30),
}
for _key, _row in MODEL_PRICING.items():
    _row.source = f"family: {_key}"


def snapshot_pricing(model: Optional[str]) -> Optional[ModelPricing]:
    """List pricing from the bundled models.dev snapshot (#473), or None.

    The second tier behind ``MODEL_PRICING``: exact match on the bare model
    id (provider qualifier and capacity suffix stripped, case-insensitive)
    rather than the curated table's substring rules, and only when the
    snapshot carries a non-zero rate — a $0/$0 listing is a subscription or
    promo tier, not a price, so it answers None and the caller keeps its
    configured default.
    """
    meta = model_metadata.lookup(model)
    if meta is None or not meta.has_pricing:
        return None
    return ModelPricing(
        input=meta.price_input or 0.0,
        output=meta.price_output or 0.0,
        cache_write=meta.price_cache_write or 0.0,
        cache_read=meta.price_cache_read or 0.0,
    )


def table_pricing(model: Optional[str]) -> Optional[ModelPricing]:
    """The model's own row in the price table (``price_table``), or None."""
    row = price_table.row_for(model)
    if row is None:
        return None
    long = None
    if row.long is not None:
        p_in, p_out, p_cw, p_cr, p_cw1h = row.long
        long = ModelPricing(input=p_in, output=p_out, cache_write=p_cw, cache_read=p_cr,
                            cache_write_1h=p_cw1h)
    return ModelPricing(
        input=row.input, output=row.output, cache_write=row.cache_write,
        cache_read=row.cache_read, cache_write_1h=row.cache_write_1h,
        long=long, long_above=row.long_above if long is not None else None,
        regional=row.regional, source=f"price table: {row.model} (as of {row.as_of})")


def family_pricing(model: Optional[str]) -> Optional[ModelPricing]:
    """The longest ``MODEL_PRICING`` key in ``model``, or None."""
    model_lower = (model or "").lower()
    if not model_lower:
        return None
    # Try longer keys first to avoid "gpt-4o" matching before "gpt-4o-mini"
    for key in sorted(MODEL_PRICING, key=len, reverse=True):
        if key in model_lower:
            return MODEL_PRICING[key]
    return None


def builtin_pricing(model: Optional[str]) -> Optional[ModelPricing]:
    """Built-in list pricing: the price table's row, else a family row, else
    the models.dev snapshot (#473); None when none knows the model."""
    for tier in (table_pricing, family_pricing, snapshot_pricing):
        found = tier(model)
        if found is not None:
            return found
    return None


def lookup_pricing(model: str) -> ModelPricing:
    """Look up pricing for a model name.

    The price table's row for the exact model id first, then the curated
    ``MODEL_PRICING`` substring match (longest key wins), then the bundled
    models.dev snapshot by exact bare id (#473), then a zero-cost fallback.

    Args:
        model: Model name (e.g. "gpt-4o-mini", "claude-haiku-4-5-20250929",
            "zai/glm-4.6")

    Returns:
        ModelPricing for the model family, or a zero-cost fallback if unknown.
    """
    found = builtin_pricing(model)
    return found if found is not None else ModelPricing(input=0.0, output=0.0)


def is_priced(model: str) -> bool:
    """Whether ``model`` has a known price (lookup_pricing falls back to $0)."""
    return bool(model) and builtin_pricing(model) is not None


def priced_by_family(model: Optional[str]) -> bool:
    """Whether a Claude model has no row of its own and is priced by a family
    row (a guess: ``overcode doctor`` says so)."""
    return (price_table.price_key(model) is not None and table_pricing(model) is None
            and family_pricing(model) is not None)


# Bedrock routing (cost model 0.6.1, §3 and §5). Only a ``global.`` cross-region
# inference profile is billed at list price; a geographic one (``us.``,
# ``eu.``, ...), a single-region model id and an application inference
# profile (which wraps one or the other; overcode cannot see which) are
# billed at the model's ``regional`` multiple.
BEDROCK_GLOBAL = "global"
BEDROCK_PREFIXES = ("global", "us-gov", "us", "eu", "apac", "jp", "au")
_BEDROCK_ID_PREFIX_RE = re.compile(r"^(global|us-gov|us|eu|apac|jp|au)\.anthropic\.")


def bedrock_routing(*, model: Optional[str] = None, prefix: Optional[str] = None,
                    region: Optional[str] = None) -> str:
    """How a Bedrock agent's requests are routed, from what it was launched with.

    ``model`` is the model id or inference-profile ARN Claude Code was given
    (an alias such as ``opus`` says nothing), ``prefix`` the preferred
    profile prefix (``bedrock.routing`` in config, else Claude Code's
    ``ANTHROPIC_BEDROCK_REGION_PREFIX``), ``region`` the AWS region, from
    which Claude Code derives the prefix otherwise (``us-*`` -> ``us``,
    ``eu-*`` -> ``eu``, ``ap-*`` -> ``apac``, GovCloud -> ``us-gov``, other
    regions -> ``global``). Returns a prefix, ``in-region`` (a bare
    ``anthropic.`` id) or ``profile`` (an application inference profile).
    """
    text = (model or "").strip().lower()
    if text.startswith("arn:"):
        if ":application-inference-profile/" in text:
            return "profile"
        text = text.rsplit("/", 1)[-1]
    match = _BEDROCK_ID_PREFIX_RE.match(text)
    if match:
        return match.group(1)
    if text.startswith("anthropic."):
        return "in-region"
    chosen = (prefix or "").strip().lower().rstrip(".")
    if chosen in BEDROCK_PREFIXES:
        return chosen
    area = (region or "").strip().lower()
    if area.startswith("us-gov-"):
        return "us-gov"
    for start, derived in (("us-", "us"), ("eu-", "eu"), ("ap-", "apac")):
        if area.startswith(start):
            return derived
    return BEDROCK_GLOBAL


def cost_of_1h_writes(cache_creation_1h_tokens: int, pricing: ModelPricing) -> float:
    """What 1-hour cache writes cost beyond the 5-minute price they were counted at."""
    extra = pricing.cache_write_1h_price - float(pricing.cache_write)
    return max(0, int(cache_creation_1h_tokens or 0)) / 1_000_000 * extra


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
