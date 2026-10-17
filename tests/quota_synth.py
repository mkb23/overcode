"""Synthetic usage histories for the quota-model tests (no real data).

``simulate`` runs a made-up fleet for some weeks against a known θ and
writes usage-history lines the way ``quota_engine`` does: a poll every
``poll_minutes`` reading floor(U), and (``ticks``) a statusline line at each
integer crossing.
"""

import math
import random

PRICES = {
    "claude-opus-x": ({"in": 5.0, "out": 25.0, "cw5m": 6.25, "cw1h": 10.0, "cr": 0.5}, None),
    "claude-sonnet-x": ({"in": 3.0, "out": 15.0, "cw5m": 3.75, "cw1h": 6.0, "cr": 0.3}, None),
    "claude-fable-x": ({"in": 10.0, "out": 50.0, "cw5m": 12.5, "cw1h": 20.0, "cr": 1.0}, None),
}
_ZERO = {k: 0.0 for k in ("in", "out", "cw5m", "cw1h", "cr")}


def price_fn(model):
    return PRICES.get(model, (_ZERO, None))


TRUE_THETA = {"in": 0.10, "out": 0.08, "cw5m": 0.12, "cw1h": 0.09, "cr": 0.0, "x:fable": 0.03}

WEEK = 7 * 86400


def true_u(theta, x):
    from overcode.quota_model import features
    f = features(x, price_fn)
    return sum(theta.get(k, 0.0) * v for k, v in f.items())


_MIXES = [
    (2000, 800, 4000, 1000, 60000),
    (500, 3000, 1000, 6000, 80000),
    (8000, 300, 20000, 500, 20000),
    (1000, 1500, 500, 15000, 150000),
    (300, 200, 200, 200, 5000),
]


def workload(rng, minute):
    """Tokens one minute of a varied fleet spends, by model."""
    hour = minute // 60
    regime = (hour * 7919) % 5
    out = {}
    for m_i, model in enumerate(PRICES):
        if model == "claude-fable-x" and (hour + m_i) % 3 == 2:
            continue
        if rng.random() >= (0.6 if regime != 4 else 0.1):
            continue
        mix = _MIXES[(regime + m_i) % 5]
        scale = rng.uniform(0.1, 0.6)
        out[model] = {k: int(v * scale * rng.uniform(0.5, 1.5))
                      for k, v in zip(("in", "out", "cw5m", "cw1h", "cr"), mix)}
    return out


def simulate(weeks=3, *, theta=None, poll_minutes=5, ticks=False, seed=1,
             start=1_800_000_000.0, plan="max20x", bursts=(), credits_from=None,
             idle_after=None):
    """Usage-history lines (oldest first).

    ``bursts``: {minute: pp} usage the fleet's tokens do not explain.
    ``credits_from``: minute from which the line's credits figure moves.
    """
    from overcode.quota_model import add_tokens
    theta = theta or TRUE_THETA
    rng = random.Random(seed)
    lines = []
    x = {}
    window = 0
    offset = rng.uniform(0, 5)
    extra = 0.0
    prev_k = None
    burst_at = dict(bursts)
    for minute in range(int(weeks * WEEK / 60)):
        t = start + minute * 60
        w = int((t - start) // WEEK)
        if w != window:
            window, x, offset, extra, prev_k = w, {}, rng.uniform(0, 5), 0.0, None
        if idle_after is None or minute < idle_after:
            add_tokens(x, workload(rng, minute))
        extra += burst_at.get(minute, 0.0)
        k = math.floor(true_u(theta, x) + offset + extra)
        credits = 1.0 if credits_from is not None and minute >= credits_from else 0.0
        base = {"t": t, "w7": k, "r7": start + (window + 1) * WEEK,
                "w5": k % 50, "r5": t - t % 18000 + 18000,
                "plan": plan, "credits": credits,
                "x": {m: dict(v) for m, v in x.items()}}
        if ticks and prev_k is not None and k == prev_k + 1:
            lines.append(dict(base, src="statusline", prev=prev_k, dt=60.0))
        if minute % poll_minutes == 0:
            lines.append(dict(base, src="api"))
        prev_k = k
    return lines
