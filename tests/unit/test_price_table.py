"""The Claude price table: dated, sourced rows that a family row never overrides.

Cost model 0.6.1, Phase A. The rows come from the AWS Price List's
``AmazonBedrockFoundationModels`` offer (``scripts/refresh_price_table.py``);
the generator is tested here on a synthetic offer, and the shipped rows
against the prices the audit found wrong.
"""

from __future__ import annotations

import json

import pytest

from overcode import price_table
from overcode.pricing import (
    MODEL_PRICING,
    ModelPricing,
    family_pricing,
    lookup_pricing,
    priced_by_family,
    table_pricing,
)
from overcode.price_table import aws_model_id, parse_aws_price_list, price_key


class TestPriceKey:
    @pytest.mark.parametrize("model,key", [
        ("claude-haiku-5-5", "claude-haiku-5-5"),
        ("claude-haiku-4-5-20251001", "claude-haiku-4-5"),
        ("us.anthropic.claude-sonnet-4-5-20250929-v1:0", "claude-sonnet-4-5"),
        ("global.anthropic.claude-opus-5-5", "claude-opus-5-5"),
        ("anthropic.claude-opus-4-1-20250805-v1:0", "claude-opus-4-1"),
        ("anthropic/claude-opus-4.1", "claude-opus-4-1"),
        ("claude-opus-4-5@20251101", "claude-opus-4-5"),
        ("claude-opus-5[1m]", "claude-opus-5"),
        ("arn:aws:bedrock:us-east-1:123:inference-profile/eu.anthropic.claude-sonnet-5",
         "claude-sonnet-5"),
        ("CLAUDE-SONNET-5-5", "claude-sonnet-5-5"),
    ])
    def test_spellings_reach_one_key(self, model, key):
        assert price_key(model) == key

    @pytest.mark.parametrize("model", [None, "", "gpt-4o", "sonnet", "glm-4.6"])
    def test_non_claude_ids_have_no_key(self, model):
        assert price_key(model) is None


class TestShippedRows:
    """The rows the 0.6.1 audit found wrong (cost-model-0.6.1.md §2, item 4)."""

    def test_haiku_5_5_is_a_tenth_of_haiku_4_5(self):
        # The family key "haiku" priced it at Haiku 4.5's $1 / $5
        p = lookup_pricing("claude-haiku-5-5")
        assert (p.input, p.output, p.cache_write, p.cache_read, p.cache_write_1h_price) \
            == pytest.approx((0.10, 0.50, 0.125, 0.01, 0.20))

    def test_haiku_5_5_long_context_tier(self):
        p = lookup_pricing("claude-haiku-5-5")
        assert p.long_above == 100_000
        assert p.long.per_million() == pytest.approx((0.50, 2.50, 0.625, 0.05, 1.0))
        assert len(p.per_million()) == 10

    def test_sonnet_5_5_cache_read(self):
        # The "sonnet-5" family row read $0.20
        p = lookup_pricing("claude-sonnet-5-5")
        assert (p.input, p.output, p.cache_read) == pytest.approx((2.0, 10.0, 0.10))

    def test_fable_5_1_cache_read(self):
        # The "fable" family row read Fable 5's $1.00
        assert lookup_pricing("claude-fable-5-1").cache_read == pytest.approx(0.25)
        assert lookup_pricing("claude-fable-5").cache_read == pytest.approx(1.00)

    def test_sonnet_4_long_context_tier(self):
        p = lookup_pricing("claude-sonnet-4-20250514")
        assert p.long_above == 200_000
        assert p.long.per_million() == pytest.approx((6.0, 22.5, 7.5, 0.6, 12.0))
        # Sonnet 4.5 has no long-context row: a single tier
        assert lookup_pricing("claude-sonnet-4-5-20250929").long is None

    @pytest.mark.parametrize("model,regional", [
        ("claude-opus-5-5", 1.1), ("claude-haiku-4-5", 1.1), ("claude-sonnet-4-5", 1.1),
        ("claude-sonnet-4", 1.0), ("claude-opus-4-1", 1.0), ("claude-3-5-haiku", 1.0),
    ])
    def test_bedrock_regional_premium(self, model, regional):
        assert lookup_pricing(model).regional == pytest.approx(regional)

    def test_every_row_is_dated_and_sourced(self):
        rows, header = price_table.table()
        assert len(rows) >= 20
        assert header["generated_by"] == "scripts/refresh_price_table.py"
        for row in rows.values():
            assert row.as_of and row.source.startswith("AWS Price List"), row.model
            assert row.input > 0 and row.output > 0 and row.cache_read > 0, row.model

    def test_table_rows_agree_with_the_family_rows_they_shadow(self):
        # Where a family row is right for a model, the table says the same:
        # the table corrects only what the audit found
        for model, key in (("claude-opus-4-6", "opus"), ("claude-sonnet-4-6", "sonnet"),
                           ("claude-haiku-4-5", "haiku"), ("claude-opus-4-1", "opus-4-1"),
                           ("claude-sonnet-5", "sonnet-5"), ("claude-opus-5-5", "opus-5-5")):
            row, family = table_pricing(model), MODEL_PRICING[key]
            assert (row.input, row.output, row.cache_write, row.cache_read) == pytest.approx(
                (family.input, family.output, family.cache_write, family.cache_read)), model


class TestLookupOrder:
    def test_table_row_wins_over_family_row(self):
        assert family_pricing("claude-haiku-5-5") is MODEL_PRICING["haiku"]
        assert lookup_pricing("claude-haiku-5-5").input == pytest.approx(0.10)

    def test_unknown_claude_release_falls_back_to_its_family(self):
        assert lookup_pricing("claude-haiku-6") is MODEL_PRICING["haiku"]
        assert priced_by_family("claude-haiku-6")
        assert not priced_by_family("claude-haiku-5-5")
        assert not priced_by_family("gpt-4o")  # not a Claude id: no row expected

    def test_bedrock_id_reaches_its_row(self):
        assert lookup_pricing("us.anthropic.claude-haiku-5-5").input == pytest.approx(0.10)

    def test_settings_lookup_uses_the_table(self):
        from overcode.settings import UserConfig, get_model_pricing
        assert get_model_pricing("claude-haiku-5-5", UserConfig()).input == pytest.approx(0.10)


def _offer(products: dict) -> dict:
    """A synthetic AmazonBedrockFoundationModels offer: {usage type: (model, usd)}."""
    doc = {"publicationDate": "2026-10-12T08:40:08Z", "products": {},
           "terms": {"OnDemand": {}}}
    for i, (usage, (model, usd)) in enumerate(products.items()):
        sku = f"SKU{i}"
        doc["products"][sku] = {"attributes": {
            "provider": "Anthropic", "feature": "On-demand Inference",
            "model": model, "usagetype": usage}}
        doc["terms"]["OnDemand"][sku] = {"t": {"priceDimensions": {"d": {
            "unit": "1M tokens", "pricePerUnit": {"USD": str(usd)}}}}}
    return doc


class TestParseAwsPriceList:
    def test_newer_scheme_global_is_list_and_plain_is_regional(self):
        h = "Claude Haiku 5.5"
        rows = parse_aws_price_list(_offer({
            "USE1_input_tokens_global_standard-Units": (h, 0.10),
            "USE1_input_tokens_standard-Units": (h, 0.11),
            "USE1_output_tokens_global_standard-Units": (h, 0.50),
            "USE1_cache_write_tokens_global_standard-Units": (h, 0.125),
            "USE1_cache_write_tokens_1h_global_standard-Units": (h, 0.20),
            "USE1_cache_read_tokens_global_standard-Units": (h, 0.01),
            "USE1_input_tokens_long_ctx_global_standard-Units": (h, 0.50),
            "USE1_output_tokens_long_ctx_global_standard-Units": (h, 2.50),
        }))
        row = rows["claude-haiku-5-5"]
        assert (row["input"], row["output"], row["cache_write"], row["cache_read"],
                row["cache_write_1h"], row["regional"]) == (0.10, 0.50, 0.125, 0.01, 0.20, 1.1)
        assert row["long_context"]["input"] == 0.50 and row["long_context"]["output"] == 2.50
        assert row["long_context"]["above"] is None  # the AWS list never says

    def test_older_scheme_without_global_column_is_list(self):
        o = "Claude Opus 4"
        rows = parse_aws_price_list(_offer({
            "USE1_InputTokenCount-Units": (o, 15.0),
            "USE1_OutputTokenCount-Units": (o, 75.0),
            "USE1_CacheWriteInputTokenCount-Units": (o, 18.75),
            "USE1_CacheReadInputTokenCount-Units": (o, 1.5),
            "USE1_InputTokenCount_LatencyOptimized-Units": (o, 99.0),
        }))
        row = rows["claude-opus-4"]
        assert (row["input"], row["regional"], row["cache_write_1h"]) == (15.0, 1.0, None)

    def test_rows_it_cannot_place_are_left_out(self):
        rows = parse_aws_price_list(_offer({
            # newer scheme, no global column: which one is list?
            "USE1_input_tokens_standard-Units": ("Claude Mythos 5", 11.0),
            "USE1_output_tokens_standard-Units": ("Claude Mythos 5", 55.0),
            "USE1_cache_write_tokens_standard-Units": ("Claude Mythos 5", 13.75),
            "USE1_cache_read_tokens_standard-Units": ("Claude Mythos 5", 1.1),
            # no cache prices
            "USE1_InputTokenCount-Units": ("Claude 3 Haiku", 0.25),
            "USE1_OutputTokenCount-Units": ("Claude 3 Haiku", 1.25),
        }))
        assert rows == {}

    def test_model_names(self):
        assert aws_model_id("Claude Haiku 5.5") == "claude-haiku-5-5"
        assert aws_model_id("Claude 3.5 Sonnet v2") == "claude-3-5-sonnet"
        assert aws_model_id("Llama 3") is None


class TestGenerator:
    def test_overlay_adds_the_haiku_threshold_and_sources(self):
        import importlib.util
        from pathlib import Path

        script = Path(__file__).resolve().parents[2] / "scripts" / "refresh_price_table.py"
        spec = importlib.util.spec_from_file_location("refresh_price_table", script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        h = "Claude Haiku 5.5"
        doc = mod.build(_offer({
            "USE1_input_tokens_global_standard-Units": (h, 0.10),
            "USE1_output_tokens_global_standard-Units": (h, 0.50),
            "USE1_cache_write_tokens_global_standard-Units": (h, 0.125),
            "USE1_cache_read_tokens_global_standard-Units": (h, 0.01),
            "USE1_input_tokens_long_ctx_global_standard-Units": (h, 0.50),
        }))
        row = doc["rows"][0]
        assert row["long_context"]["above"] == 100_000
        assert row["as_of"] == "2026-10-12" and "100K" in row["source"]
        assert "1-hour cache write not listed" in row["source"]
        json.dumps(doc)


class TestPerMillion:
    def test_single_tier_row_has_five_prices(self):
        assert ModelPricing(input=1.0, output=2.0).per_million() == (1.0, 2.0, 0.0, 0.0, 2.0)

    def test_scaled_scales_both_tiers(self):
        p = lookup_pricing("claude-haiku-5-5").scaled(2.0)
        assert p.input == pytest.approx(0.20) and p.long.input == pytest.approx(1.0)
