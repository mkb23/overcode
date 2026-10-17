"""The subscription quota model: tokens to percentage points of the weekly quota.

A Claude subscription (Pro, Max, Team seats) exposes only "% of the weekly
window used", in whole percents (statusline ``rate_limits``, the
``anthropic-ratelimit-unified-*`` headers and ``/api/oauth/usage`` all
round to 1%). The figure overcode shows per agent and per billing code is
a *prediction* from the agent's tokens::

    weekly pp ≈ Σ_feature θ_f · x_f

where the features ``x_f`` are the tokens of each type priced at the
Anthropic list price of the model that spent them (US dollars,
"API-equivalent"):

- ``in``, ``out``, ``cw5m``, ``cw1h``, ``cr``: input, output, 5-minute and
  1-hour cache writes, cache reads; a request past its model's long-context
  threshold is priced at the long tier (the price table's);
- ``x:fable``: the Fable tokens' list dollars again (cache reads left out),
  so a Fable premium beyond the price ratio can be learnt (reports say the
  meter is heavier on Fable than its price).

``θ_f`` is percentage points per list dollar. The **prior**: every
non-cache-read type at ``100 / weekly budget`` (a per-plan API-equivalent
dollar budget, ``PLAN_WEEKLY_USD``, or config's ``quota.weekly_usd``),
cache reads at 0 (measured as close to free), no Fable premium. The
calibration (``quota_calibration``) fits θ to observed weekly percentages
and keeps the result here, with its uncertainty.

Pure: no files, no network. ``list_prices`` reads overcode's price table;
every function that prices takes a ``price_fn`` so tests can pass their own.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, Mapping, Optional, Tuple

# Token kinds a stream row carries, and their long-context parts
BASE_KEYS = ("in", "out", "cw5m", "cw1h", "cr")
LONG_KEYS = {"in": "lin", "out": "lout", "cw5m": "lcw5m", "cw1h": "lcw1h", "cr": "lcr"}
TOKEN_KEYS = BASE_KEYS + tuple(LONG_KEYS.values())

# Model families whose premium over the price ratio is a parameter
EXCESS_FAMILIES = ("fable",)
FEATURES = BASE_KEYS + tuple(f"x:{fam}" for fam in EXCESS_FAMILIES)

# API-equivalent dollars a week's quota holds, per plan (priors, not facts):
# Max 20x from public measurements (median ~$1.4k, range $0.7–2.7k over 8
# weeks; another ~$1.75k), Pro ~$170, Max 5x by Anthropic's stated hours
# (roughly 55% of Max 20x). Team / Enterprise seats are guesses.
PLAN_WEEKLY_USD: Dict[str, float] = {
    "pro": 170.0,
    "max5x": 850.0,
    "max20x": 1550.0,
    "max": 1200.0,
    "team": 220.0,
    "team_premium": 1100.0,
    "enterprise": 220.0,
}
UNKNOWN_PLAN_USD = 1000.0
# How sure the prior is, as a fraction of its value (one standard deviation)
PRIOR_REL_SD = 0.5
# Cache reads: prior mean 0, sd this fraction of the base weight
CACHE_READ_PRIOR_SD = 0.3
# 5-hour points per weekly point (prior; the engine fits it from data)
FIVE_HOUR_RATIO_PRIOR = 8.0

PriceRow = Tuple[Dict[str, float], Optional[Dict[str, float]]]
PriceFn = Callable[[Optional[str]], PriceRow]


def plan_from_credentials(subscription_type: Optional[str],
                          rate_limit_tier: Optional[str]) -> Optional[str]:
    """A plan key from Claude Code's OAuth credentials, None when unknown.

    ``claudeAiOauth.subscriptionType`` is "pro" / "max" / "team" /
    "enterprise"; ``rateLimitTier`` tells the Max tiers apart
    ("default_claude_max_20x", "default_claude_max_5x").
    """
    tier = (rate_limit_tier or "").lower()
    kind = (subscription_type or "").lower()
    if "20x" in tier:
        return "max20x"
    if "5x" in tier:
        return "max5x"
    if kind == "max":
        return "max"  # a Max tier we cannot tell apart
    if kind in ("pro", "team", "enterprise"):
        if kind == "team" and "premium" in tier:
            return "team_premium"
        return kind
    return None


def weekly_budget_usd(plan: Optional[str], override: Optional[float] = None) -> float:
    """The prior's API-equivalent dollars per week for ``plan``."""
    if isinstance(override, (int, float)) and override > 0:
        return float(override)
    return PLAN_WEEKLY_USD.get(plan or "", UNKNOWN_PLAN_USD)


def list_prices(model: Optional[str]) -> PriceRow:
    """``(base, long)`` list prices per million tokens, keyed like ``BASE_KEYS``.

    Anthropic list prices from overcode's price table (``pricing``), never
    a user's ``model_pricing`` override or a Bedrock rate: the quota meters
    API-equivalent usage. ``long`` is the long-context tier, None without one.
    Unknown models price at 0 (they add nothing).
    """
    from .pricing import lookup_pricing

    p = lookup_pricing(model or "")
    base = {"in": p.input, "out": p.output, "cw5m": p.cache_write,
            "cw1h": p.cache_write_1h_price, "cr": p.cache_read}
    long = None
    if p.long is not None:
        q = p.long
        long = {"in": q.input, "out": q.output, "cw5m": q.cache_write,
                "cw1h": q.cache_write_1h_price, "cr": q.cache_read}
    return base, long


def family_of(model: Optional[str]) -> Optional[str]:
    low = (model or "").lower()
    for fam in EXCESS_FAMILIES:
        if fam in low:
            return fam
    return None


def features(tokens_by_model: Mapping[str, Mapping[str, float]],
             price_fn: Optional[PriceFn] = None) -> Dict[str, float]:
    """The model's features (list dollars per type) of tokens grouped by model."""
    price_fn = price_fn or list_prices
    out = {f: 0.0 for f in FEATURES}
    for model, toks in (tokens_by_model or {}).items():
        if not isinstance(toks, Mapping):
            continue
        base, long = price_fn(model)
        fam = family_of(model)
        for k in BASE_KEYS:
            n = _num(toks.get(k))
            if n <= 0:
                continue
            n_long = min(_num(toks.get(LONG_KEYS[k])), n) if long else 0.0
            dollars = ((n - n_long) * base.get(k, 0.0)
                       + n_long * (long or {}).get(k, 0.0)) / 1_000_000
            out[k] += dollars
            if fam is not None and k != "cr":
                out[f"x:{fam}"] += dollars
    return out


def add_tokens(into: Dict[str, Dict[str, float]],
               more: Mapping[str, Mapping[str, float]], sign: float = 1.0) -> None:
    """``into[model][kind] += sign * more[model][kind]`` (in place)."""
    for model, toks in (more or {}).items():
        if not isinstance(toks, Mapping):
            continue
        slot = into.setdefault(model, {})
        for k, v in toks.items():
            if k in TOKEN_KEYS:
                slot[k] = slot.get(k, 0) + sign * _num(v)


@dataclass
class QuotaModel:
    """θ per feature (percentage points of the weekly quota per list dollar).

    ``prior`` is the θ the fit was shrunk toward; ``sd`` each θ's standard
    deviation (the prior's until fitted). ``fitted`` says whether data moved
    it; the fit's diagnostics ride along for ``overcode usage model``.
    """

    theta: Dict[str, float]
    prior: Dict[str, float]
    sd: Dict[str, float]
    plan: Optional[str] = None
    weekly_usd: float = UNKNOWN_PLAN_USD
    fitted: bool = False
    fitted_at: Optional[float] = None
    n_obs: int = 0
    n_ticks: int = 0
    n_windows: int = 0
    n_segments: int = 0
    rmse_pp: Optional[float] = None
    coverage: Optional[float] = None  # share of polls whose interval held the prediction
    sigma_pp: Optional[float] = None
    five_hour_ratio: float = FIVE_HOUR_RATIO_PRIOR
    five_hour_n: int = 0
    notes: list = field(default_factory=list)

    def predict(self, feats: Mapping[str, float]) -> float:
        return sum(self.theta.get(f, 0.0) * _num(feats.get(f)) for f in FEATURES)

    def predict_tokens(self, tokens_by_model, price_fn: Optional[PriceFn] = None) -> float:
        return self.predict(features(tokens_by_model, price_fn))

    @property
    def confidence(self) -> str:
        """"prior" (not fitted), else "low" / "medium" / "high" by the
        output weight's relative uncertainty and how much data there was."""
        if not self.fitted:
            return "prior"
        rel = self.relative_sd("out")
        if self.n_obs < 50 or self.n_windows < 1 or rel is None or rel > 0.3:
            return "low"
        if rel > 0.1 or self.n_windows < 2:
            return "medium"
        return "high"

    def relative_sd(self, feature: str) -> Optional[float]:
        theta = self.theta.get(feature, 0.0)
        sd = self.sd.get(feature)
        if sd is None or theta <= 0:
            return None
        return sd / theta

    def implied_weekly_usd(self) -> Optional[float]:
        """The weekly budget the output weight implies (100 / θ_out), list dollars."""
        theta = self.theta.get("out", 0.0)
        return 100.0 / theta if theta > 0 else None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping) -> Optional["QuotaModel"]:
        if not isinstance(data, Mapping):
            return None
        try:
            known = {f: data[f] for f in cls.__dataclass_fields__ if f in data}
            model = cls(**known)
        except TypeError:
            return None
        if not isinstance(model.theta, dict) or not isinstance(model.prior, dict):
            return None
        return model


def prior_theta(weekly_usd: float) -> Tuple[Dict[str, float], Dict[str, float]]:
    """(θ, sd) of the prior for a weekly budget of ``weekly_usd`` list dollars."""
    base = 100.0 / max(weekly_usd, 1e-9)
    theta = {f: 0.0 for f in FEATURES}
    sd = {f: base * PRIOR_REL_SD for f in FEATURES}
    for k in ("in", "out", "cw5m", "cw1h"):
        theta[k] = base
    sd["cr"] = base * CACHE_READ_PRIOR_SD
    return theta, sd


def prior_model(plan: Optional[str] = None, weekly_usd: Optional[float] = None) -> QuotaModel:
    """The unfitted model for ``plan`` (or an explicit weekly budget)."""
    usd = weekly_budget_usd(plan, weekly_usd)
    theta, sd = prior_theta(usd)
    return QuotaModel(theta=dict(theta), prior=dict(theta), sd=sd, plan=plan, weekly_usd=usd)


def format_pp(pp: Optional[float]) -> str:
    """``1.37pp``: two decimals under 100, one above."""
    if pp is None or not math.isfinite(pp):
        return "-"
    return f"{pp:.2f}pp" if abs(pp) < 100 else f"{pp:.1f}pp"


def _num(value) -> float:
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) else 0.0
