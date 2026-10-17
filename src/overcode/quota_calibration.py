"""Calibrating the quota model (``quota_model``) against observed weekly percentages.

Every ground-truth source reports the weekly window in **whole percents**,
so one observation says only that the true usage ``U`` lay in a cell:

- a **poll** (the usage API, or a statusline sample whose value did not
  just change) at time t reading k: ``U(t) ∈ [k, k+1)`` (shifted by a
  constant if the source rounds rather than floors; see below);
- a **tick** (a statusline sample whose value just went from k-1 to k,
  with the previous sample a short while before): ``U(t) = k``, give or
  take how far the bracket lets the crossing move.

The model is ``U(t) = θ · x(t) + a_s``: ``x(t)`` the fleet's cumulative
features (list dollars per token type, ``quota_model.features``) since the
weekly window began, ``a_s`` a free level per *segment* (a window, split
where something the model cannot see happened). The free level absorbs
usage before overcode started counting and the source's rounding
convention, so only increments inform θ.

Fit (``fit``): maximum a posteriori under interval censoring, by EM:

1. impute each poll's ``U`` as the mean of a normal ``N(θ·x + a, σ²)``
   truncated to its cell (ticks are taken as observed);
2. solve the weighted least squares for θ with each segment's level
   profiled out (centring within the segment), a Gaussian prior on θ
   (``quota_model.prior_theta``: shrinkage toward the price ratios, so
   thin data stays near them) and θ ≥ 0 (coordinate descent on the normal
   equations: a convex box-constrained quadratic);
3. re-estimate σ (the model's error in percentage points: unattributed
   use, timing) from the residuals; repeat to convergence.

Robustness: after a fit, an interval whose observed rise exceeds the
predicted rise by more than ``JUMP_PP`` + 3σ is usage the model cannot see
(chat, Cowork, Claude Code outside overcode): the segment is split there
and the fit repeated, so a burst moves a level instead of the weights.

Exclusions (``observations``): a line without overcode's token totals;
the window's percentage at or past 100 (capped) or once its usage credits
moved (a credits period bills money, not quota); a plan change and a
configured limit change (``breaks``) start a new window. Lines older than
the history the caller passes are its business (2–4 rolling weeks).

The 5-hour window gets one number, the ratio of 5-hour to weekly points
(``five_hour_ratio``): the ratio of their summed rises over intervals
inside one 5-hour window.

Pure: lists in, a ``QuotaModel`` out.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .quota_model import (
    FEATURES,
    FIVE_HOUR_RATIO_PRIOR,
    PriceFn,
    QuotaModel,
    features,
)

# A statusline value change counts as a tick when the previous sample
# (which read one less) is at most this many seconds older
TICK_MAX_BRACKET = 300.0
# A tick's timing error, in percentage points (one sd)
TICK_SD_PP = 0.1
# Unexplained rise that splits a segment: this plus 3σ
JUMP_PP = 2.0
MIN_SIGMA = 0.15
EM_ITERATIONS = 30
ROBUST_ROUNDS = 2


@dataclass
class Obs:
    """One observation: the weekly percentage ``k`` at ``t`` with features ``f``."""

    t: float
    window: str
    k: float
    kind: str  # "poll" | "tick"
    f: Dict[str, float]
    segment: str = ""


@dataclass
class ObsReport:
    """What ``observations`` kept and why it dropped the rest."""

    kept: int = 0
    ticks: int = 0
    windows: int = 0
    dropped: Dict[str, int] = field(default_factory=dict)

    def drop(self, why: str) -> None:
        self.dropped[why] = self.dropped.get(why, 0) + 1


def window_key(resets_at: float, plan: Optional[str], breaks_before: int) -> str:
    """The weekly window a sample belongs to: its reset time (to 10 minutes;
    the API's stamp jitters), the plan, and the limit changes before it."""
    return f"{int(round(resets_at / 600.0))}|{plan or '?'}|{breaks_before}"


def observed_value(line: Mapping) -> Optional[float]:
    """The weekly figure of a history line: the Claude Code surface's share
    when the line carries the surface breakdown, else the whole window's."""
    surf = line.get("surf")
    if isinstance(surf, Mapping) and isinstance(surf.get("claude_code"), (int, float)):
        return float(surf["claude_code"])
    w7 = line.get("w7")
    return float(w7) if isinstance(w7, (int, float)) else None


def observations(lines: Iterable[Mapping], *, price_fn: Optional[PriceFn] = None,
                 breaks: Sequence[float] = ()) -> Tuple[List[Obs], ObsReport]:
    """Calibration observations from usage-history lines (oldest first).

    A line (``quota_engine`` writes them)::

        {"t", "src": "api" | "statusline", "w7", "w5", "r7", "r5", "plan",
         "credits", "surf"?, "x": {model: {kind: tokens}}, "prev"?, "dt"?}

    ``x`` is the fleet's cumulative tokens since the window began.
    """
    report = ObsReport()
    out: List[Obs] = []
    breaks = sorted(b for b in breaks if isinstance(b, (int, float)))
    first_credits: Dict[str, object] = {}
    spent: set = set()  # windows whose credits moved: the rest is dropped
    rows = sorted((ln for ln in lines if isinstance(ln, Mapping)
                   and isinstance(ln.get("t"), (int, float))), key=lambda ln: ln["t"])
    for line in rows:
        k = observed_value(line)
        r7 = line.get("r7")
        if k is None or not isinstance(r7, (int, float)):
            report.drop("no weekly figure")
            continue
        x = line.get("x")
        if not isinstance(x, Mapping):
            report.drop("no token totals")
            continue
        t = float(line["t"])
        key = window_key(float(r7), line.get("plan"), sum(1 for b in breaks if b <= t))
        credits = line.get("credits")
        if key not in first_credits:
            first_credits[key] = credits
        elif credits != first_credits[key] and credits is not None:
            spent.add(key)
        if key in spent:
            report.drop("usage credits")
            continue
        if k >= 100:
            report.drop("window full")
            continue
        kind = "poll"
        prev, dt = line.get("prev"), line.get("dt")
        if (line.get("src") == "statusline" and isinstance(prev, (int, float))
                and k - prev == 1 and isinstance(dt, (int, float)) and 0 <= dt <= TICK_MAX_BRACKET):
            kind = "tick"
            report.ticks += 1
        out.append(Obs(t=t, window=key, k=k, kind=kind, f=features(x, price_fn)))
    report.kept = len(out)
    report.windows = len({o.window for o in out})
    return out, report


# -- the fit -------------------------------------------------------------

def _phi(z: float) -> float:
    return math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)


def _cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2)))


def truncated_normal_moments(mu: float, sigma: float, lo: float, hi: float) -> Tuple[float, float]:
    """Mean and variance of N(mu, sigma²) truncated to [lo, hi)."""
    a, b = (lo - mu) / sigma, (hi - mu) / sigma
    z = _cdf(b) - _cdf(a)
    if z < 1e-12:
        # All the mass is beyond one edge: the nearest edge, the cell's spread
        edge = lo if mu < lo else hi
        return edge, (hi - lo) ** 2 / 12.0
    pa, pb = _phi(a), _phi(b)
    mean = mu + sigma * (pa - pb) / z
    var = sigma * sigma * (1.0 + (a * pa - b * pb) / z - ((pa - pb) / z) ** 2)
    return min(max(mean, lo), hi), max(var, 0.0)


def nnls_quadratic(a: List[List[float]], b: List[float], start: Optional[List[float]] = None,
                   sweeps: int = 2000, tol: float = 1e-12) -> List[float]:
    """argmin ½θᵀAθ − bᵀθ over θ ≥ 0, A symmetric positive definite (coordinate descent)."""
    n = len(b)
    theta = [max(0.0, v) for v in (start or [0.0] * n)]
    for _ in range(sweeps):
        biggest = 0.0
        for j in range(n):
            if a[j][j] <= 0:
                continue
            rest = sum(a[j][i] * theta[i] for i in range(n) if i != j)
            new = max(0.0, (b[j] - rest) / a[j][j])
            biggest = max(biggest, abs(new - theta[j]) * math.sqrt(a[j][j]))
            theta[j] = new
        if biggest < tol:
            break
    return theta


def invert(a: List[List[float]]) -> Optional[List[List[float]]]:
    """Gauss-Jordan inverse with partial pivoting; None when singular."""
    n = len(a)
    m = [list(row) + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-300:
            return None
        m[col], m[pivot] = m[pivot], m[col]
        p = m[col][col]
        m[col] = [v / p for v in m[col]]
        for r in range(n):
            if r != col and m[r][col] != 0.0:
                factor = m[r][col]
                m[r] = [rv - factor * cv for rv, cv in zip(m[r], m[col])]
    return [row[n:] for row in m]


def _segments(obs: List[Obs]) -> Dict[str, List[int]]:
    groups: Dict[str, List[int]] = {}
    for i, o in enumerate(obs):
        groups.setdefault(o.segment or o.window, []).append(i)
    return groups


def _solve(obs: List[Obs], y: List[float], w: List[float], prior: QuotaModel,
           theta0: List[float]) -> Tuple[List[float], List[List[float]]]:
    """θ ≥ 0 maximising the posterior with each segment's level profiled out."""
    p = len(FEATURES)
    a = [[0.0] * p for _ in range(p)]
    b = [0.0] * p
    for idx in _segments(obs).values():
        wsum = sum(w[i] for i in idx)
        if len(idx) < 2 or wsum <= 0:
            continue
        ybar = sum(w[i] * y[i] for i in idx) / wsum
        fbar = [sum(w[i] * obs[i].f[f] for i in idx) / wsum for f in FEATURES]
        for i in idx:
            fc = [obs[i].f[f] - fbar[j] for j, f in enumerate(FEATURES)]
            yc = y[i] - ybar
            for j in range(p):
                if fc[j] == 0.0:
                    continue
                b[j] += w[i] * fc[j] * yc
                row = a[j]
                for k in range(p):
                    row[k] += w[i] * fc[j] * fc[k]
    for j, f in enumerate(FEATURES):
        lam = 1.0 / max(prior.sd.get(f, 1.0), 1e-12) ** 2
        a[j][j] += lam
        b[j] += lam * prior.prior.get(f, 0.0)
    return nnls_quadratic(a, b, theta0), a


def _levels(obs: List[Obs], y: List[float], w: List[float], theta: List[float]) -> List[float]:
    """Each observation's segment level a_s at θ (the weighted mean residual)."""
    level = [0.0] * len(obs)
    for idx in _segments(obs).values():
        wsum = sum(w[i] for i in idx) or 1.0
        a = sum(w[i] * (y[i] - _dot(theta, obs[i].f)) for i in idx) / wsum
        for i in idx:
            level[i] = a
    return level


def _dot(theta: List[float], f: Mapping[str, float]) -> float:
    return sum(t * f.get(name, 0.0) for t, name in zip(theta, FEATURES))


def _em(obs: List[Obs], prior: QuotaModel, theta: List[float], sigma: float
        ) -> Tuple[List[float], float, List[List[float]], List[float]]:
    y = [o.k + (0.5 if o.kind == "poll" else 0.0) for o in obs]
    a: List[List[float]] = []
    level = [0.0] * len(obs)
    for _ in range(EM_ITERATIONS):
        w = [1.0 / (sigma ** 2 + (TICK_SD_PP ** 2 if o.kind == "tick" else 0.0)) for o in obs]
        level = _levels(obs, y, w, theta)
        extra = []
        for i, o in enumerate(obs):
            if o.kind == "poll":
                mean, var = truncated_normal_moments(_dot(theta, o.f) + level[i], sigma, o.k, o.k + 1)
                y[i] = mean
                extra.append(var)
            else:
                extra.append(0.0)
        new_theta, a = _solve(obs, y, w, prior, theta)
        level = _levels(obs, y, w, new_theta)
        resid = [(y[i] - _dot(new_theta, o.f) - level[i]) ** 2 + extra[i] for i, o in enumerate(obs)]
        new_sigma = max(MIN_SIGMA, math.sqrt(sum(resid) / max(1, len(obs))))
        scale = max(max(prior.prior.values()), 1e-12)
        moved = max(abs(n - t) for n, t in zip(new_theta, theta)) / scale
        theta, sigma = new_theta, new_sigma
        if moved < 1e-7:
            break
    return theta, sigma, a, level


def _split_jumps(obs: List[Obs], theta: List[float]) -> int:
    """Start a new segment after every unexplained rise; returns how many splits.

    An interval's excess is its observed rise less the predicted one; the
    spread is the excesses' median absolute deviation (robust: the bursts
    being looked for would inflate a standard deviation), and an excess past
    ``JUMP_PP`` plus four spreads is a burst.
    """
    by_window: Dict[str, List[int]] = {}
    for i, o in enumerate(obs):
        by_window.setdefault(o.window, []).append(i)
    excess: Dict[int, float] = {}
    for idx in by_window.values():
        idx.sort(key=lambda i: obs[i].t)
        for prev, i in zip(idx, idx[1:]):
            predicted = _dot(theta, obs[i].f) - _dot(theta, obs[prev].f)
            excess[i] = (obs[i].k - obs[prev].k) - predicted
    if not excess:
        return 0
    values = sorted(excess.values())
    median = values[len(values) // 2]
    mad = sorted(abs(v - median) for v in values)[len(values) // 2]
    limit = JUMP_PP + 4 * max(1.4826 * mad, MIN_SIGMA)
    splits = 0
    for window, idx in by_window.items():
        seg = 0
        for i in idx:
            if excess.get(i, 0.0) - median > limit:
                seg += 1
                splits += 1
            obs[i].segment = f"{window}#{seg}"
    return splits


def thin(obs: List[Obs]) -> List[Obs]:
    """The first and last observation of every run of equal readings in a window.

    The prediction is cumulative and θ ≥ 0, so it never falls: when the
    first and last of a run sit in the run's cell, so do the ones between.
    Keeps a month of 5-minute polls to a few hundred observations. Ticks
    are always kept.
    """
    out: List[Obs] = []
    by_window: Dict[str, List[Obs]] = {}
    for o in obs:
        by_window.setdefault(o.window, []).append(o)
    for rows in by_window.values():
        rows.sort(key=lambda o: o.t)
        for i, o in enumerate(rows):
            first = i == 0 or rows[i - 1].k != o.k
            last = i == len(rows) - 1 or rows[i + 1].k != o.k
            if first or last or o.kind == "tick":
                out.append(o)
    out.sort(key=lambda o: o.t)
    return out


def fit(obs: List[Obs], prior: QuotaModel, *, now: Optional[float] = None,
        report: Optional[ObsReport] = None) -> QuotaModel:
    """The posterior model from ``obs`` (see the module docstring).

    Returns a copy of ``prior`` with θ, sd and the diagnostics filled in;
    ``fitted`` False (the prior unchanged) when nothing informs θ (no
    segment with two observations).
    """
    obs = [Obs(o.t, o.window, o.k, o.kind, dict(o.f)) for o in obs]
    for o in obs:
        for f in FEATURES:
            o.f.setdefault(f, 0.0)
        o.segment = o.window
    model = QuotaModel.from_dict(prior.to_dict()) or prior
    usable = sum(len(idx) for idx in _segments(obs).values() if len(idx) >= 2)
    if usable < 2:
        model.notes = ["not enough observations to fit"]
        return model
    theta = [prior.theta.get(f, 0.0) for f in FEATURES]
    sigma = 0.3
    splits = 0
    for round_ in range(ROBUST_ROUNDS + 1):
        theta, sigma, a, level = _em(obs, prior, theta, sigma)
        if round_ == ROBUST_ROUNDS:
            break
        new = _split_jumps(obs, theta)
        if not new:
            break
        splits = new
    cov = invert(a) if a else None
    model.theta = {f: theta[j] for j, f in enumerate(FEATURES)}
    if cov is not None:
        model.sd = {f: math.sqrt(max(cov[j][j], 0.0)) for j, f in enumerate(FEATURES)}
    model.fitted = True
    model.fitted_at = now
    model.n_obs = len(obs)
    model.n_ticks = sum(1 for o in obs if o.kind == "tick")
    model.n_windows = len({o.window for o in obs})
    model.n_segments = len(_segments(obs))
    model.sigma_pp = round(sigma, 4)
    # Fit quality on the polls: the predicted level against the cell middle
    sq, inside, polls = 0.0, 0, 0
    y_mid = [o.k + (0.5 if o.kind == "poll" else 0.0) for o in obs]
    w = [1.0] * len(obs)
    level = _levels(obs, y_mid, w, theta)
    for i, o in enumerate(obs):
        pred = _dot(theta, o.f) + level[i]
        sq += (pred - y_mid[i]) ** 2
        if o.kind == "poll":
            polls += 1
            inside += int(o.k <= pred < o.k + 1)
    model.rmse_pp = round(math.sqrt(sq / len(obs)), 4)
    model.coverage = round(inside / polls, 4) if polls else None
    notes = []
    if splits:
        notes.append(f"{splits} unexplained rise(s) split off (usage outside overcode)")
    if report is not None and report.dropped:
        notes.append("dropped: " + ", ".join(f"{n} {why}" for why, n in sorted(report.dropped.items())))
    model.notes = notes
    return model


def five_hour_ratio(lines: Iterable[Mapping], prior: float = FIVE_HOUR_RATIO_PRIOR,
                    prior_weight_pp: float = 5.0) -> Tuple[float, int]:
    """5-hour points per weekly point: Σ 5-hour rises / Σ weekly rises over
    consecutive samples inside one 5-hour window (and one weekly window),
    with ``prior_weight_pp`` weekly points of prior. Returns (ratio, intervals)."""
    rows = sorted((ln for ln in lines if isinstance(ln, Mapping)
                   and isinstance(ln.get("t"), (int, float))), key=lambda ln: ln["t"])
    up5 = up7 = 0.0
    n = 0
    prev = None
    for line in rows:
        if prev is not None and _same(prev, line, "r5") and _same(prev, line, "r7"):
            d5 = _diff(line, prev, "w5")
            d7 = _diff(line, prev, "w7")
            if d5 is not None and d7 is not None and d5 >= 0 and d7 >= 0 and (d5 or d7):
                up5 += d5
                up7 += d7
                n += 1
        prev = line
    ratio = (up5 + prior * prior_weight_pp) / (up7 + prior_weight_pp)
    return ratio, n


def _same(a: Mapping, b: Mapping, key: str) -> bool:
    x, y = a.get(key), b.get(key)
    return isinstance(x, (int, float)) and isinstance(y, (int, float)) and abs(x - y) < 600


def _diff(a: Mapping, b: Mapping, key: str) -> Optional[float]:
    x, y = a.get(key), b.get(key)
    if isinstance(x, (int, float)) and isinstance(y, (int, float)) and x < 100 and y < 100:
        return float(x) - float(y)
    return None
