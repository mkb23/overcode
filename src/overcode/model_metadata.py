"""Bundled cross-vendor model metadata: context windows and list prices (#473).

Second-tier source behind the hand-curated tables in ``history_reader.py``
(``MODEL_CONTEXT_WINDOWS``) and ``pricing.py`` (``MODEL_PRICING``). Those
tables hold entries someone has personally verified against a primary
source or a live CLI figure, and they always win. This module holds a
transcoded snapshot of `models.dev <https://models.dev>`_'s open catalog
(``https://models.dev/api.json``) so the long tail — GLM, Kimi, DeepSeek,
Qwen, Mistral, Gemini, and every provider-qualified spelling opencode can
route to — resolves to a *cited* figure rather than a dash or a zero.

Precedence for both lookups::

    curated table  >  models.dev catalog  >  unknown (None / dash)

where "models.dev catalog" is the *freshest vintage available locally*:

- ``~/.overcode/cache/model_metadata.json`` — written by
  ``overcode models refresh`` (or by the monitor daemon when
  ``model_metadata.auto_refresh`` is on; off by default — no unattended
  network from a monitoring daemon);
- ``~/.cache/opencode/models.json`` — opencode's own cached copy of the same
  catalog, refreshed by opencode itself whenever it runs, read here because
  it is already on disk;
- the bundled snapshot ``data/model_metadata.json`` shipped in the wheel.

The newest of the first two by mtime wins; the bundled snapshot is the
offline fallback. All three are the same data (models.dev is also the
catalog opencode ships with, so for opencode sessions the context window
here is the very denominator opencode's own "N% used" figure uses — see
``opencode_stats.opencode_context_limit`` and docs/backends.md, "CTX%: what
the context column divides by"). Set ``OVERCODE_MODEL_METADATA_BUNDLED_ONLY=1``
to ignore the local tiers (the unit tests do, for reproducibility).

Refresh the *bundled* snapshot before a release with
``python scripts/refresh_model_metadata.py``; the transcoder lives here
(``transcode_models_dev``) so it is unit-tested and both the script and the
CLI stay thin fetch-and-write wrappers.
"""

import json
import os
import re
import time
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

MODELS_DEV_URL = "https://models.dev/api.json"
SNAPSHOT_PATH = Path(__file__).parent / "data" / "model_metadata.json"
BUNDLED_ONLY_ENV = "OVERCODE_MODEL_METADATA_BUNDLED_ONLY"
LOCAL_CACHE_ENV = "OVERCODE_MODEL_METADATA_CACHE"
DEFAULT_STALE_AFTER_DAYS = 90.0

# Where two providers list the same bare model id, the earlier provider in
# this list wins. First-party vendors first (their list prices are the
# reference everyone else discounts from), then OpenCode Zen (opencode's own
# gateway — the spelling opencode users most often type), then everything
# else alphabetically. Resellers/routers listing a model the first parties
# don't only ever *add* ids; they never override.
PROVIDER_PRIORITY: List[str] = [
    "anthropic",
    "openai",
    "xai",
    "zai",
    "moonshotai",
    "google",
    "deepseek",
    "mistral",
    "alibaba",
    "minimax",
    "meta",
    "cohere",
    "ai21",
    "perplexity",
    "opencode",
]

# Providers whose listings make it into the snapshot at all. models.dev
# catalogs ~220 providers, most of them small resellers that re-list the
# same models under mangled ids ("anthropic--claude-4-opus",
# "alicloud-glm-5.1") — noise that would bloat the wheel and pollute the
# candidate universe an alias-resolver matches against. First-party vendors
# plus the gateways/aggregators coding agents are actually pointed at.
INCLUDED_PROVIDERS: List[str] = PROVIDER_PRIORITY + [
    "openrouter",
    "github-copilot",
    "amazon-bedrock",
    "azure",
    "google-vertex",
    "google-vertex-anthropic",
    "groq",
    "togetherai",
    "fireworks-ai",
    "deepinfra",
    "cerebras",
    "nvidia",
    "huggingface",
    "ollama-cloud",
    "vercel",
    "cloudflare-workers-ai",
    "zhipuai",
    "zai-coding-plan",
    "moonshotai-cn",
    "kimi-for-coding",
    "alibaba-cn",
    "minimax-cn",
    "llama",
]

# Trailing capacity-variant suffix, e.g. the "[1m]" in "claude-opus-5[1m]".
_CAPACITY_SUFFIX_RE = re.compile(r"\[[^\]]*\]$")


def bare_model_id(model: str) -> str:
    """Normalise a model id to the bare form the lookup tables key on.

    Strips an opencode-style ``provider/model`` qualifier (keeping only the
    last path segment, so ``openrouter/qwen/qwen3-coder`` → ``qwen3-coder``)
    and a trailing bracketed capacity suffix (``claude-opus-5[1m]`` →
    ``claude-opus-5``). Case is preserved; callers that need
    case-insensitive matching lower() the result.
    """
    bare = model.rsplit("/", 1)[-1] if "/" in model else model
    return _CAPACITY_SUFFIX_RE.sub("", bare)


@dataclass(frozen=True)
class ModelMetadata:
    """One transcoded models.dev entry. Prices are USD per million tokens."""

    id: str
    provider: str
    name: str
    context_window: Optional[int]
    max_output: Optional[int]
    price_input: Optional[float]
    price_output: Optional[float]
    price_cache_read: Optional[float]
    price_cache_write: Optional[float]
    open_weights: bool = False

    @property
    def has_pricing(self) -> bool:
        """True when the snapshot carries a non-zero list price.

        A catalog entry priced 0/0 is either genuinely free (a vendor's
        flash-tier promo) or a subscription/coding-plan listing whose real
        cost lives elsewhere — either way, "no figure" is more honest than
        "$0.00", so callers fall through to their configured default.
        """
        return bool((self.price_input or 0) > 0 or (self.price_output or 0) > 0)


# ── Transcoding (models.dev → our snapshot) ──────────────────────────────


def _provider_order(providers: Iterable[str]) -> List[str]:
    names = set(providers)
    ordered = [p for p in PROVIDER_PRIORITY if p in names]
    ordered += sorted(names - set(ordered))
    return ordered


def _as_int(value: Any) -> Optional[int]:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _as_price(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def transcode_models_dev(raw: Dict[str, Any], fetched_at: str) -> Dict[str, Any]:
    """Reduce a models.dev ``api.json`` document to overcode's snapshot shape.

    Keeps text-output models from ``INCLUDED_PROVIDERS`` that declare a
    context window, keyed by lower-cased bare id, first-listed provider
    (per ``PROVIDER_PRIORITY``) winning. Costs are copied through when the entry carries any non-zero
    input/output price; zero-priced listings keep their limits but drop the
    cost block (see ``ModelMetadata.has_pricing``).
    """
    models: Dict[str, Dict[str, Any]] = {}
    included = set(INCLUDED_PROVIDERS)
    for provider in _provider_order(k for k in raw.keys() if k in included):
        entry = raw.get(provider) or {}
        for model_id, spec in (entry.get("models") or {}).items():
            if not isinstance(spec, dict):
                continue
            modalities = spec.get("modalities") or {}
            if "text" not in (modalities.get("output") or []):
                continue
            limit = spec.get("limit") or {}
            context = _as_int(limit.get("context"))
            if context is None:
                continue
            key = bare_model_id(model_id).lower()
            if key in models:
                continue
            record: Dict[str, Any] = {
                "provider": provider,
                "name": spec.get("name") or model_id,
                "context": context,
                "max_output": _as_int(limit.get("output")),
                "open_weights": bool(spec.get("open_weights", False)),
            }
            cost = spec.get("cost") or {}
            price_in = _as_price(cost.get("input"))
            price_out = _as_price(cost.get("output"))
            if (price_in or 0) > 0 or (price_out or 0) > 0:
                record["input"] = price_in or 0.0
                record["output"] = price_out or 0.0
                record["cache_read"] = _as_price(cost.get("cache_read")) or 0.0
                record["cache_write"] = _as_price(cost.get("cache_write")) or 0.0
            models[key] = record
    return {
        "source": MODELS_DEV_URL,
        "fetched_at": fetched_at,
        "provider_priority": list(PROVIDER_PRIORITY),
        "models": models,
    }


def dump_snapshot(snapshot: Dict[str, Any]) -> str:
    """Serialise one-model-per-line so refreshes diff cleanly in git."""
    lines = [
        "{",
        f'  "source": {json.dumps(snapshot.get("source", MODELS_DEV_URL))},',
        f'  "fetched_at": {json.dumps(snapshot.get("fetched_at", ""))},',
        f'  "provider_priority": {json.dumps(snapshot.get("provider_priority", PROVIDER_PRIORITY))},',
        '  "models": {',
    ]
    items = sorted(snapshot.get("models", {}).items())
    for i, (key, record) in enumerate(items):
        comma = "," if i < len(items) - 1 else ""
        lines.append(f"    {json.dumps(key)}: {json.dumps(record)}{comma}")
    lines += ["  }", "}", ""]
    return "\n".join(lines)


# ── Catalog sources ──────────────────────────────────────────────────────


def local_cache_path() -> Path:
    """Where ``overcode models refresh`` / the daemon's auto-refresh write.

    ``OVERCODE_MODEL_METADATA_CACHE`` names the file outright; otherwise it
    lives under the overcode data dir (``OVERCODE_DIR`` honoured).
    """
    explicit = os.environ.get(LOCAL_CACHE_ENV)
    if explicit:
        return Path(explicit)
    from .settings import get_overcode_dir  # lazy: settings imports pricing imports this
    return get_overcode_dir() / "cache" / "model_metadata.json"


def opencode_models_cache_path() -> Path:
    """opencode's cached models.dev catalog, honouring XDG."""
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "opencode" / "models.json"


def _bundled_only() -> bool:
    return os.environ.get(BUNDLED_ONLY_ENV, "").strip().lower() in ("1", "true", "yes", "on")


@lru_cache(maxsize=1)
def _load_bundled() -> Dict[str, Any]:
    """The bundled snapshot, parsed once. A missing or corrupt file is an
    empty catalog, never an exception — every caller must degrade to
    "unknown", not crash a daemon tick."""
    catalog = _parse_snapshot_file(SNAPSHOT_PATH)
    if catalog is None:
        return {"models": {}, "origin": str(SNAPSHOT_PATH), "tier": "bundled", "fetched_at": "unknown"}
    catalog["origin"] = str(SNAPSHOT_PATH)
    catalog["tier"] = "bundled"
    return catalog


def _parse_snapshot_file(path: Path) -> Optional[Dict[str, Any]]:
    """Parse a file in our transcoded snapshot shape; None if unusable."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("models"), dict):
        return None
    return data


def _parse_models_dev_file(path: Path) -> Optional[Dict[str, Any]]:
    """Parse a raw models.dev ``api.json`` (opencode's cache) into snapshot shape."""
    try:
        raw = json.loads(path.read_text())
        mtime = path.stat().st_mtime
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or not raw:
        return None
    # A raw catalog is keyed by provider; a stray snapshot-shaped file here
    # would have a "models" dict instead — don't try to transcode that.
    if "models" in raw and isinstance(raw["models"], dict) and "provider_priority" in raw:
        return None
    try:
        snapshot = transcode_models_dev(
            raw, fetched_at=datetime.fromtimestamp(mtime).date().isoformat()
        )
    except Exception:
        return None
    return snapshot if snapshot["models"] else None


# (path, mtime) -> parsed catalog, so a stable file is parsed once per process.
_local_parsed: Dict[Tuple[str, float], Dict[str, Any]] = {}
# The hot path (TUI render, daemon tick) calls lookup() constantly; re-stat
# the candidate files only every few seconds.
_active: Tuple[float, Optional[Dict[str, Any]]] = (0.0, None)
_ACTIVE_TTL_SECONDS = 5.0


def _local_candidates() -> List[Tuple[float, Path, Callable[[Path], Optional[Dict[str, Any]]], str]]:
    return [
        (0.0, local_cache_path(), _parse_snapshot_file, "local"),
        (0.0, opencode_models_cache_path(), _parse_models_dev_file, "opencode"),
    ]


def _select_local_catalog() -> Optional[Dict[str, Any]]:
    """Newest valid local catalog by mtime, or None."""
    found: List[Tuple[float, Path, Callable[[Path], Optional[Dict[str, Any]]], str]] = []
    for _, path, parser, tier in _local_candidates():
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        found.append((mtime, path, parser, tier))
    for mtime, path, parser, tier in sorted(found, key=lambda t: t[0], reverse=True):
        key = (str(path), mtime)
        catalog = _local_parsed.get(key)
        if catalog is None:
            parsed = parser(path)
            if parsed is None:
                continue
            parsed["origin"] = str(path)
            parsed["tier"] = tier
            _local_parsed.clear()  # never hold more than one local vintage
            _local_parsed[key] = parsed
            catalog = parsed
        return catalog
    return None


def active_catalog() -> Dict[str, Any]:
    """The catalog every lookup resolves against right now.

    Freshest valid local tier (our refreshed cache or opencode's cache, by
    mtime), else the bundled snapshot. Carries ``origin`` (path) and ``tier``
    (``local`` / ``opencode`` / ``bundled``) for doctor and ``overcode models
    info``.
    """
    global _active
    if _bundled_only():
        return _load_bundled()
    now = time.monotonic()
    cached_at, cached = _active
    if cached is not None and now - cached_at < _ACTIVE_TTL_SECONDS:
        return cached
    catalog = _select_local_catalog() or _load_bundled()
    _active = (now, catalog)
    return catalog


def invalidate_cache() -> None:
    """Forget every parsed catalog (after a refresh, or in tests)."""
    global _active
    _active = (0.0, None)
    _local_parsed.clear()
    _load_bundled.cache_clear()


# ── Refresh ──────────────────────────────────────────────────────────────


def fetch_models_dev(url: str = MODELS_DEV_URL, timeout: float = 60.0) -> Dict[str, Any]:
    """Download and parse models.dev's catalog. Raises on any failure."""
    req = urllib.request.Request(url, headers={"User-Agent": "overcode model-metadata refresh"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — fixed https URL
        data = json.load(resp)
    if not isinstance(data, dict) or not data:
        raise ValueError("models.dev returned an empty or non-object catalog")
    return data


def refresh_local_cache(
    path: Optional[Path] = None,
    fetch: Callable[[], Dict[str, Any]] = fetch_models_dev,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    """Fetch models.dev and rewrite the local cache atomically.

    Returns the same provenance dict as ``snapshot_info()`` for the new
    file. Raises on network/parse failure — callers decide whether that is
    a CLI error or a daemon log line — and never leaves a partial file.
    """
    target = path or local_cache_path()
    snapshot = transcode_models_dev(fetch(), fetched_at=(today or date.today()).isoformat())
    if not snapshot["models"]:
        raise ValueError("models.dev catalog transcoded to zero models — not overwriting the cache")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(dump_snapshot(snapshot))
    os.replace(tmp, target)
    invalidate_cache()
    return {
        "source": snapshot["source"],
        "fetched_at": snapshot["fetched_at"],
        "model_count": len(snapshot["models"]),
        "path": str(target),
        "tier": "local",
    }


def local_cache_age_days(path: Optional[Path] = None) -> Optional[float]:
    """Age of the local refreshed cache in days, or None if absent."""
    target = path or local_cache_path()
    try:
        mtime = target.stat().st_mtime
    except OSError:
        return None
    return max(0.0, (time.time() - mtime) / 86400.0)


# ── Lookup ───────────────────────────────────────────────────────────────


def snapshot_info() -> Dict[str, Any]:
    """Provenance of the catalog lookups currently resolve against."""
    catalog = active_catalog()
    return {
        "source": catalog.get("source", MODELS_DEV_URL),
        "fetched_at": catalog.get("fetched_at", "unknown"),
        "model_count": len(catalog.get("models", {})),
        "path": catalog.get("origin", str(SNAPSHOT_PATH)),
        "tier": catalog.get("tier", "bundled"),
    }


def _vintage(catalog: Dict[str, Any]) -> Optional[date]:
    try:
        return date.fromisoformat(str(catalog.get("fetched_at", "")))
    except ValueError:
        return None


def staleness_findings(
    max_age_days: float = DEFAULT_STALE_AFTER_DAYS, today: Optional[date] = None
) -> List[str]:
    """Doctor nudge when the active catalog is older than ``max_age_days``."""
    catalog = active_catalog()
    vintage = _vintage(catalog)
    if vintage is None:
        return []
    age = ((today or date.today()) - vintage).days
    if age <= max_age_days:
        return []
    tier = catalog.get("tier", "bundled")
    return [
        f"model metadata catalog ({tier}, {catalog.get('origin')}) is {age} days old "
        f"(fetched {vintage.isoformat()}) — newer models will show a dash for CTX%; "
        f"run [bold]overcode models refresh[/bold] or set model_metadata.auto_refresh: true"
    ]


def lookup(model: Optional[str]) -> Optional[ModelMetadata]:
    """Catalog entry for a model id, or None.

    Accepts every spelling the backends report — bare (``glm-4.6``),
    opencode-qualified (``zai/glm-4.6``), capacity-suffixed
    (``claude-opus-5[1m]``) — and matches case-insensitively.
    """
    if not model:
        return None
    key = bare_model_id(model).lower()
    record = active_catalog().get("models", {}).get(key)
    if not isinstance(record, dict):
        return None
    return ModelMetadata(
        id=key,
        provider=str(record.get("provider", "")),
        name=str(record.get("name", key)),
        context_window=_as_int(record.get("context")),
        max_output=_as_int(record.get("max_output")),
        price_input=_as_price(record.get("input")) if "input" in record else None,
        price_output=_as_price(record.get("output")) if "output" in record else None,
        price_cache_read=_as_price(record.get("cache_read")) if "cache_read" in record else None,
        price_cache_write=_as_price(record.get("cache_write")) if "cache_write" in record else None,
        open_weights=bool(record.get("open_weights", False)),
    )


def context_window(model: Optional[str]) -> Optional[int]:
    """Catalog context window for a model id, or None."""
    meta = lookup(model)
    return meta.context_window if meta else None


def known_model_ids() -> List[str]:
    """Every bare id the active catalog knows, sorted — the candidate universe a
    future alias-resolver maps unfamiliar ids onto (docs/design/model-alias-resolution.md)."""
    return sorted(active_catalog().get("models", {}).keys())


__all__ = [
    "BUNDLED_ONLY_ENV",
    "DEFAULT_STALE_AFTER_DAYS",
    "INCLUDED_PROVIDERS",
    "LOCAL_CACHE_ENV",
    "MODELS_DEV_URL",
    "PROVIDER_PRIORITY",
    "SNAPSHOT_PATH",
    "ModelMetadata",
    "active_catalog",
    "bare_model_id",
    "context_window",
    "dump_snapshot",
    "fetch_models_dev",
    "invalidate_cache",
    "known_model_ids",
    "local_cache_age_days",
    "local_cache_path",
    "lookup",
    "opencode_models_cache_path",
    "refresh_local_cache",
    "snapshot_info",
    "staleness_findings",
    "transcode_models_dev",
]
