"""The Claude price table: one dated, sourced row per model (cost model 0.6.1, §4).

``data/claude_prices.json`` holds a row per Claude model id: list prices
per million tokens (input, output, 5-minute and 1-hour cache writes, cache
reads), the multiplier Amazon Bedrock charges on a geographic or in-region
inference profile over the ``global.`` one (``regional``), the long-context
tier a request moves to when its prompt passes a size (``long_context``),
and where each figure came from (``source``) and when (``as_of``).

The rows are generated from the AWS Price List (``AmazonBedrockFoundationModels``:
its ``global`` column is Anthropic's list price, its plain column the
regional one) by ``build_table``, with what the AWS list does not say (tier
thresholds, a model it lists elsewhere: ``OVERLAY``) added and sourced. The
release vendors the table (``scripts/refresh_price_table.py``); on a host
that cannot wait for a release, ``overcode models refresh --prices <file>``
builds a local override from an offer file copied in by hand
(``~/.overcode/cache/claude_prices.json``), used while it is dated after
the vendored table, its rows over the vendored ones. ``pricing.py`` asks
this table first, by exact model id (``price_key``), and falls back to its
hand-written family rows only for ids the table does not name: a family
row never overrides a model's own row.

Nothing here imports the rest of overcode except ``model_metadata``'s id
normaliser, so the reader can ask for tier thresholds cheaply.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

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


# The local override table ``overcode models refresh --prices`` writes, under
# the overcode data dir (never inside the installed package)
LOCAL_FILENAME = "claude_prices.json"


def local_override_path() -> Optional[Path]:
    """Where the local override table lives (``~/.overcode/cache/claude_prices.json``),
    or None when the data dir cannot be resolved in this process."""
    try:
        from .paths import overcode_dir
        return overcode_dir() / "cache" / LOCAL_FILENAME
    except Exception:
        return None


def _read_doc(path: Optional[Path]) -> Optional[Dict[str, Any]]:
    if path is None:
        return None
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _rows_of(doc: Optional[Mapping[str, Any]]) -> Dict[str, PriceRow]:
    rows: Dict[str, PriceRow] = {}
    for raw in (doc or {}).get("rows") or ():
        if isinstance(raw, Mapping):
            row = _row(raw)
            if row is not None:
                rows[row.model] = row
    return rows


def _header_of(doc: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    return {k: v for k, v in (doc or {}).items() if k != "rows"}


def _signature(path: Optional[Path]) -> Optional[Tuple[int, int]]:
    if path is None:
        return None
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


@lru_cache(maxsize=1)
def vendored_table() -> Tuple[Dict[str, PriceRow], Dict[str, Any]]:
    """The table shipped in the package: ``(rows, header)``; empty when unreadable."""
    doc = _read_doc(DATA_PATH)
    return _rows_of(doc), _header_of(doc)


@lru_cache(maxsize=1)
def _resolved(local: Optional[Path], signature: Optional[Tuple[int, int]],
              ) -> Tuple[Dict[str, PriceRow], Dict[str, Any]]:
    # A new key means the override changed: the per-id threshold cache with it
    long_context_threshold.cache_clear()
    vrows, vheader = vendored_table()
    header = {**vheader, "tier": "vendored", "origin": str(DATA_PATH)}
    doc = _read_doc(local) if signature is not None else None
    lrows, lheader = _rows_of(doc), _header_of(doc)
    if lrows and str(lheader.get("as_of") or "") > str(vheader.get("as_of") or ""):
        # The override's rows first; a model it does not price keeps its
        # vendored row rather than dropping to a family guess
        header = {**lheader, "tier": "local", "origin": str(local),
                  "vendored_as_of": vheader.get("as_of")}
        return {**vrows, **lrows}, header
    if lrows:
        header["ignored_local"] = {"origin": str(local), "as_of": lheader.get("as_of")}
    return vrows, header


def table() -> Tuple[Dict[str, PriceRow], Dict[str, Any]]:
    """``(rows by model id, header)`` of the table in use.

    The local override (``local_override_path``) when it is readable, has
    rows and is dated after the vendored table, its rows over the vendored
    ones; else the vendored table. The header says which (``tier``:
    ``local`` / ``vendored``; ``origin``: the file; ``ignored_local`` when
    an override exists but is not newer). One ``stat`` per call; the files
    are parsed again only when the override changes.
    """
    local = local_override_path()
    return _resolved(local, _signature(local))


def invalidate() -> None:
    """Forget the parsed tables."""
    vendored_table.cache_clear()
    _resolved.cache_clear()
    long_context_threshold.cache_clear()


def age_days(header: Mapping[str, Any], today: Optional[date] = None) -> Optional[int]:
    """Days since a table's as-of date, or None when it has none."""
    try:
        when = date.fromisoformat(str(header.get("as_of") or "")[:10])
    except ValueError:
        return None
    return ((today or date.today()) - when).days


DEFAULT_STALE_AFTER_DAYS = 90
STALE_HINT = ("upgrade overcode, or `overcode models refresh --prices <AWS price list file>` "
              "(works offline: docs/configuration.md, \"Prices on an air-gapped host\")")


def max_age_days_from(config: Any) -> float:
    """``pricing.table_max_age_days`` of a parsed config.yaml, else the default."""
    pricing = config.get("pricing") if isinstance(config, Mapping) else None
    value = pricing.get("table_max_age_days") if isinstance(pricing, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return float(DEFAULT_STALE_AFTER_DAYS)
    return float(value)


def staleness_findings(max_age_days: float = DEFAULT_STALE_AFTER_DAYS,
                       today: Optional[date] = None) -> List[str]:
    """``overcode doctor``'s warning when the table in use is older than ``max_age_days``."""
    _rows, header = table()
    age = age_days(header, today)
    if age is None or age <= max_age_days:
        return []
    return [f"Claude price table ({header.get('tier')}, as of {header.get('as_of')}) is "
            f"{age} days old, so price changes since then are missing: {STALE_HINT}"]


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
                    usd = float((dim.get("pricePerUnit") or {})["USD"])
                except (KeyError, TypeError, ValueError):
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


# ── building a table (release time, and ``models refresh --prices``) ─────

OFFER_CODE = "AmazonBedrockFoundationModels"
OFFER_URL = ("https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/"
             "AmazonBedrockFoundationModels/current/{region}/index.json")

AWS_SOURCE = ("AWS Price List, AmazonBedrockFoundationModels {region} ({published}): "
              "global column = list price; regional = plain column / global")

# What the AWS foundation-model list does not carry. Each entry is merged
# into the generated row and its note appended to the row's source.
OVERLAY: Dict[str, Dict[str, Any]] = {
    "claude-haiku-5-5": {
        "long_context": {"above": 100_000},
        "note": ("long-context tier applies to prompts over 100K tokens: Claude Code "
                 "changelog (claude-haiku-5-5: $0.50/$2.50 for prompts over 100K) and "
                 "model-config docs ('costs more on prompts longer than 100K tokens')"),
    },
    "claude-sonnet-5-5": {
        "note": ("cache read $0.10 from 2026-10-07 (was $0.20; cost-model-0.6.1 audit); "
                 "tokens read before this table shipped stay at the rate they were priced at"),
    },
    "claude-sonnet-4": {
        # Listed in the AmazonBedrockService offer (per 1K tokens), not the
        # foundation-model one: global long-context rates
        "long_context": {"above": 200_000, "input": 6.0, "output": 22.5,
                         "cache_write": 7.5, "cache_read": 0.6, "cache_write_1h": None},
        "note": ("long-context rates: AWS Price List AmazonBedrockService us-east-1 "
                 "(2026-10-14), Claude4Sonnet *-long-context-cross-region-global; "
                 "threshold 200K input tokens per Anthropic's long-context pricing "
                 "(not restated by AWS); 1-hour long-context cache write not listed, "
                 "taken as 2x input"),
    },
}


class PriceListError(ValueError):
    """A file that is not a usable AWS Price List offer for Bedrock foundation models."""


def load_offer(path: Path) -> Dict[str, Any]:
    """Parse an AWS Price List offer file (plain or ``.gz``), checking it is the
    ``AmazonBedrockFoundationModels`` one. Raises ``PriceListError``."""
    import gzip
    try:
        raw = path.read_bytes()
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        doc = json.loads(raw)
    except OSError as e:
        raise PriceListError(f"cannot read {path}: {e.strerror or e}") from e
    except (ValueError, EOFError) as e:
        raise PriceListError(f"{path} is not JSON: {e}") from e
    if not isinstance(doc, dict) or "products" not in doc:
        raise PriceListError(f"{path} is not an AWS Price List offer file (no 'products')")
    offer = doc.get("offerCode")
    if offer and offer != OFFER_CODE:
        raise PriceListError(f"{path} is the {offer} offer; overcode needs {OFFER_CODE} "
                             f"({OFFER_URL.format(region='us-east-1')})")
    return doc


def _offer_region(offer: Mapping[str, Any]) -> str:
    """The region an offer file prices (its Anthropic products' ``regionCode``)."""
    regions: Dict[str, int] = {}
    for product in (offer.get("products") or {}).values():
        attrs = (product or {}).get("attributes") or {}
        if attrs.get("provider") == "Anthropic" and attrs.get("regionCode"):
            regions[attrs["regionCode"]] = regions.get(attrs["regionCode"], 0) + 1
    return max(regions, key=regions.get) if regions else "us-east-1"


def build_table(offer: Mapping[str, Any], *, generated_by: str,
                source_file: Optional[str] = None) -> Dict[str, Any]:
    """A table document (``data/claude_prices.json`` shape) from an offer file:
    the parsed rows plus ``OVERLAY``, each row dated and sourced."""
    published = str(offer.get("publicationDate") or "")[:10]
    region = _offer_region(offer)
    rows = parse_aws_price_list(offer)
    out = []
    for model in sorted(rows):
        row = dict(rows[model])
        source = AWS_SOURCE.format(region=region, published=published)
        if row.get("cache_write_1h") is None:
            source += "; 1-hour cache write not listed, taken as 2x input"
        extra = OVERLAY.get(model)
        if extra:
            if "long_context" in extra:
                row["long_context"] = {**(row.get("long_context") or {}), **extra["long_context"]}
            source += "; " + extra["note"]
        if isinstance(row.get("long_context"), dict) and row["long_context"].get("above") is None:
            # A long-context column with no known threshold cannot be applied
            del row["long_context"]
            source += "; long-context rates listed but threshold unknown: not applied"
        row["source"] = source
        row["as_of"] = published
        out.append(row)
    doc: Dict[str, Any] = {"as_of": published, "generated_by": generated_by}
    if source_file:
        doc["source"] = (f"AWS Price List {OFFER_CODE} {region}, published {published} "
                         f"(from {source_file})")
    doc["rows"] = out
    return doc


def diff_tables(old: Mapping[str, Any], new: Mapping[str, Any]) -> List[str]:
    """One line per model a new table adds (``+``), drops (``-``) or reprices (``~``)."""
    old_rows = {r["model"]: r for r in old.get("rows") or () if isinstance(r, Mapping)}
    new_rows = {r["model"]: r for r in new.get("rows") or () if isinstance(r, Mapping)}
    lines = []
    for model in sorted(set(old_rows) | set(new_rows)):
        before, after = old_rows.get(model), new_rows.get(model)
        if before == after:
            continue
        if before is None:
            lines.append(f"+ {model}")
        elif after is None:
            lines.append(f"- {model}")
        else:
            changed = sorted(k for k in set(before) | set(after)
                             if k not in ("source", "as_of") and before.get(k) != after.get(k))
            if changed:
                lines.append(f"~ {model}: " + ", ".join(
                    f"{k} {before.get(k)} -> {after.get(k)}" for k in changed))
    return lines


def write_table(doc: Mapping[str, Any], path: Path) -> None:
    """Write a table document atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=1) + "\n")
    os.replace(tmp, path)
    invalidate()


def _raw_rows(doc: Optional[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {r["model"]: dict(r) for r in (doc or {}).get("rows") or ()
            if isinstance(r, Mapping) and "model" in r}


def refresh_local_override(offer_path: Path, *, write: bool = True) -> Dict[str, Any]:
    """Build the local override table from an offer file the user copied in.

    No network; the installed package is never written. Returns ``{"doc",
    "diff" (the rows in use before against after), "path", "vendored_as_of",
    "newer"}``; ``newer`` False means the vendored table is as new or
    newer, and nothing is written (``table`` would ignore the override).
    Raises ``PriceListError`` for an unusable file.
    """
    offer = load_offer(offer_path)
    doc = build_table(offer, generated_by="overcode models refresh --prices",
                      source_file=offer_path.name)
    if not doc["rows"]:
        raise PriceListError(f"{offer_path} has no Anthropic on-demand token prices "
                             f"(is it the {OFFER_CODE} offer?)")
    if not doc["as_of"]:
        raise PriceListError(f"{offer_path} has no publicationDate")
    path = local_override_path()
    if path is None:
        raise PriceListError("cannot resolve the overcode data directory")
    vendored = _read_doc(DATA_PATH)
    vendored_as_of = str((vendored or {}).get("as_of") or "")
    in_use = _raw_rows(vendored)
    if table()[1].get("tier") == "local":
        in_use.update(_raw_rows(_read_doc(path)))
    after = {**_raw_rows(vendored), **_raw_rows(doc)}
    newer = doc["as_of"] > vendored_as_of
    if write and newer:
        write_table(doc, path)
    return {"doc": doc, "path": path, "vendored_as_of": vendored_as_of, "newer": newer,
            "diff": diff_tables({"rows": list(in_use.values())}, {"rows": list(after.values())})}


__all__ = [
    "DATA_PATH", "DEFAULT_STALE_AFTER_DAYS", "OFFER_URL", "OVERLAY", "PRICE_FIELDS",
    "PriceListError", "PriceRow", "age_days", "aws_model_id", "build_table", "diff_tables",
    "invalidate", "load_offer", "local_override_path", "long_context_threshold",
    "max_age_days_from",
    "parse_aws_price_list", "price_key", "refresh_local_override", "row_for",
    "staleness_findings", "table", "vendored_table", "write_table",
]
