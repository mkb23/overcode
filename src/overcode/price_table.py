"""The Claude price table: one dated, sourced row per model (cost model 0.6.1, §4).

``data/claude_prices.json`` holds a row per Claude model id: list prices
per million tokens (input, output, 5-minute and 1-hour cache writes, cache
reads), the multiplier Amazon Bedrock charges on a geographic or in-region
inference profile over the ``global.`` one (``regional``), the long-context
tier a request moves to when its prompt passes a size (``long_context``),
and where each figure came from (``source``) and when (``as_of``).

The rows are generated from the AWS Price List (``AmazonBedrockFoundationModels``:
its ``global`` column is Anthropic's list price, its plain column the
regional one) by ``scripts/refresh_price_table.py``, with what the AWS list
does not say (tier thresholds, a model it lists elsewhere) added there and
sourced. ``pricing.py`` asks this table first, by exact model id
(``price_key``), and falls back to its hand-written family rows only for
ids the table does not name: a family row never overrides a model's own row.

Nothing here imports the rest of overcode except ``model_metadata``'s id
normaliser, so the reader can ask for tier thresholds cheaply.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from .model_metadata import bare_model_id

DATA_PATH = Path(__file__).resolve().parent / "data" / "claude_prices.json"

# The five prices of a row, in the order a price row (``per_million``) uses
PRICE_FIELDS = ("input", "output", "cache_write", "cache_read", "cache_write_1h")

# Bedrock inference-profile prefixes (Claude Code's ``ANTHROPIC_BEDROCK_REGION_PREFIX``
# values, plus GovCloud) and the ``anthropic.`` vendor part of a Bedrock id
_BEDROCK_PREFIX_RE = re.compile(r"^(?:(?:global|us-gov|us|eu|apac|jp|au)\.)?anthropic\.")
_BEDROCK_VERSION_RE = re.compile(r"-v\d+(?::\d+)?$")
_VERTEX_VERSION_RE = re.compile(r"@\d{8}$")
_DATE_SUFFIX_RE = re.compile(r"-20\d{6}$")


@dataclass(frozen=True)
class PriceRow:
    """One model's row. Prices are USD per million tokens.

    ``cache_write_1h`` is None when the source does not list it (it is then
    twice the input price, Anthropic's rule). ``long_above`` is the prompt
    size (input + cache read + cache write tokens of one request) past
    which the whole request is billed at ``long`` (the five prices, same
    order as ``PRICE_FIELDS``).
    """

    model: str
    name: str
    input: float
    output: float
    cache_write: float
    cache_read: float
    cache_write_1h: Optional[float]
    regional: float
    long_above: Optional[int]
    long: Optional[Tuple[float, float, float, float, float]]
    source: str
    as_of: str


def price_key(model: Optional[str]) -> Optional[str]:
    """The table key for a model id as any backend or provider spells it, or None.

    ``us.anthropic.claude-sonnet-4-5-20250929-v1:0``, ``anthropic/claude-opus-4.1``,
    ``claude-opus-4-5@20251101`` and ``claude-opus-5[1m]`` become
    ``claude-sonnet-4-5``, ``claude-opus-4-1``, ``claude-opus-4-5`` and
    ``claude-opus-5``. Only Claude ids get a key.
    """
    if not isinstance(model, str) or not model.strip():
        return None
    key = bare_model_id(model.strip()).lower()
    key = _BEDROCK_PREFIX_RE.sub("", key)
    key = _BEDROCK_VERSION_RE.sub("", key)
    key = _VERTEX_VERSION_RE.sub("", key)
    key = _DATE_SUFFIX_RE.sub("", key)
    if not key.startswith("claude-"):
        return None
    return key.replace(".", "-")


def _row(raw: Mapping[str, Any]) -> Optional[PriceRow]:
    try:
        long = raw.get("long_context")
        long_prices = None
        long_above = None
        if isinstance(long, Mapping):
            long_above = int(long["above"])
            long_prices = tuple(
                float(long[f]) if long.get(f) is not None else 2.0 * float(long["input"])
                for f in PRICE_FIELDS)
        one_hour = raw.get("cache_write_1h")
        return PriceRow(
            model=str(raw["model"]),
            name=str(raw.get("name") or raw["model"]),
            input=float(raw["input"]),
            output=float(raw["output"]),
            cache_write=float(raw["cache_write"]),
            cache_read=float(raw["cache_read"]),
            cache_write_1h=None if one_hour is None else float(one_hour),
            regional=float(raw.get("regional", 1.0)),
            long_above=long_above,
            long=long_prices,  # type: ignore[arg-type]
            source=str(raw.get("source") or ""),
            as_of=str(raw.get("as_of") or ""),
        )
    except (KeyError, TypeError, ValueError):
        return None


@lru_cache(maxsize=1)
def table() -> Tuple[Dict[str, PriceRow], Dict[str, Any]]:
    """``(rows by model id, the file's header)``; empty when the file is unreadable."""
    try:
        doc = json.loads(DATA_PATH.read_text())
    except (OSError, ValueError):
        return {}, {}
    rows: Dict[str, PriceRow] = {}
    for raw in doc.get("rows") or ():
        if isinstance(raw, Mapping):
            row = _row(raw)
            if row is not None:
                rows[row.model] = row
    header = {k: v for k, v in doc.items() if k != "rows"}
    return rows, header


def row_for(model: Optional[str]) -> Optional[PriceRow]:
    """The model's own row, by exact id (``price_key``), or None."""
    key = price_key(model)
    if key is None:
        return None
    return table()[0].get(key)


@lru_cache(maxsize=256)
def long_context_threshold(model: Optional[str]) -> Optional[int]:
    """The prompt size past which a request of ``model`` is billed at its long-context
    rates, or None when the model has no such tier. Cached per id (the reader asks
    once per assistant message)."""
    row = row_for(model)
    return row.long_above if row is not None else None


# ── generating rows from the AWS Price List ─────────────────────────────

# Usage types of the AWS Price List's Bedrock foundation-model offer. The
# newer scheme ("USE1_cache_write_tokens_1h_long_ctx_global_standard-Units")
# and the older one ("USE1_CacheWrite1hInputTokenCount_Global-Units").
_NEW_USAGE_RE = re.compile(
    r"_(input|output|cache_read|cache_write)_tokens(_1h)?(_long_ctx)?(_global)?_standard-Units$")
_OLD_USAGE_RE = re.compile(
    r"_(InputTokenCount|OutputTokenCount|CacheReadInputTokenCount|CacheWriteInputTokenCount"
    r"|CacheWrite1hInputTokenCount)(_Global)?-Units$")
_OLD_KINDS = {
    "InputTokenCount": "input",
    "OutputTokenCount": "output",
    "CacheReadInputTokenCount": "cache_read",
    "CacheWriteInputTokenCount": "cache_write",
    "CacheWrite1hInputTokenCount": "cache_write_1h",
}


def aws_model_id(name: str) -> Optional[str]:
    """``Claude Haiku 5.5`` -> ``claude-haiku-5-5``; ``Claude 3.5 Sonnet v2`` ->
    ``claude-3-5-sonnet``. None for a name that is not a Claude model."""
    words = name.strip().lower().split()
    if not words or words[0] != "claude":
        return None
    words = [w for w in words if not re.fullmatch(r"v\d+", w)]
    return "-".join(words).replace(".", "-")


def parse_aws_price_list(doc: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Rows (``data/claude_prices.json`` shape, without ``source``) from an AWS
    Price List offer file of ``AmazonBedrockFoundationModels``.

    Only Anthropic on-demand token prices are read. A model listed with a
    global column takes it as its list price, and ``regional`` is the plain
    column over it; one listed only plainly (the older models, priced the
    same everywhere) takes that, ``regional`` 1. A model of the newer
    scheme without a global column (it cannot say which is list) and one
    without cache prices are left out.
    """
    products = doc.get("products") or {}
    terms = (doc.get("terms") or {}).get("OnDemand") or {}
    found: Dict[str, Dict[Tuple[str, str, str], float]] = {}
    names: Dict[str, str] = {}
    newer: set = set()
    for sku, product in products.items():
        attrs = product.get("attributes") or {}
        if attrs.get("provider") != "Anthropic" or attrs.get("feature") != "On-demand Inference":
            continue
        model = aws_model_id(str(attrs.get("model") or ""))
        usage = str(attrs.get("usagetype") or "")
        if model is None:
            continue
        match = _NEW_USAGE_RE.search(usage)
        if match:
            kind = match.group(1) + ("_1h" if match.group(2) else "")
            tier = "long" if match.group(3) else "base"
            routing = "global" if match.group(4) else "regional"
            newer.add(model)
        else:
            match = _OLD_USAGE_RE.search(usage)
            if not match:
                continue
            kind, tier = _OLD_KINDS[match.group(1)], "base"
            routing = "global" if match.group(2) else "regional"
        for term in (terms.get(sku) or {}).values():
            for dim in (term.get("priceDimensions") or {}).values():
                if dim.get("unit") != "1M tokens":
                    continue
                try:
                    usd = float((dim.get("pricePerUnit") or {}).get("USD"))
                except (TypeError, ValueError):
                    continue
                found.setdefault(model, {})[(kind, tier, routing)] = usd
                names[model] = str(attrs.get("model"))
    rows: Dict[str, Dict[str, Any]] = {}
    for model, prices in found.items():
        has_global = any(r == "global" for _, _, r in prices)
        if model in newer and not has_global:
            continue
        column = "global" if has_global else "regional"

        def get(kind, tier="base", routing=column):
            return prices.get((kind, tier, routing))

        base = {f: get(f) for f in PRICE_FIELDS}
        if any(base[f] is None for f in ("input", "output", "cache_write", "cache_read")):
            continue
        row: Dict[str, Any] = {"model": model, "name": names[model], **base}
        regional_input = get("input", routing="regional")
        row["regional"] = (round(regional_input / base["input"], 4)
                           if has_global and regional_input and base["input"] else 1.0)
        long = {f: get(f, "long") for f in PRICE_FIELDS}
        if long["input"] is not None:
            row["long_context"] = {"above": None, **long}
        rows[model] = row
    return rows


__all__ = [
    "DATA_PATH", "PRICE_FIELDS", "PriceRow", "aws_model_id", "long_context_threshold",
    "parse_aws_price_list", "price_key", "row_for", "table",
]
