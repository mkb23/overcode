"""Spend per conversation: what each of an agent's conversations has cost.

An agent's tokens, cost and energy are cumulative and feed the billing
ledger, but a stats reader can legitimately read *less* than before:
Claude Code prunes old transcripts, codex and grok read only the newest
conversation after ``/new``, ``restart --fresh`` replaces the conversations
the agent owns, and a container read can fail for one file. So the spend is
kept per conversation (the agent CLI's own conversation id) in
``SessionStats.conversations``::

    {conversation_id: {"in", "out", "cw", "cr",   # tokens, as priced
                       "cost", "energy", "model", "recorded"?, "charged"?}}

Each sync (``merge``) updates only the conversations the read covers, each
token count taking the larger of what is on record and what was read, so
a pruned or no-longer-read conversation keeps its last entry and a briefly
low or failed read changes nothing. A new conversation adds an entry. The
agent's spend is the sum of the entries (``totals``).

A conversation's cost *accrues*: the tokens a sync adds are priced at the
model that conversation was read at, so a later ``/model`` switch prices
only the tokens spent after it and never reprices the past (nor moves
spend from before a rebill onto the new billing code). A backend that
records its own charge (opencode, hermes, grok) gives a ``cost`` per
conversation; the entry is then marked ``recorded`` and its cost grows by
what the charge grows (``charged`` is the last charge taken). A charge that
first appears after the tokens were estimated replaces the estimate only
when larger: the cost is never lowered.

A record from before this model (an upgrade, or a pre-release build that
carried spend) has totals and no entries. Its first sync folds the totals
into a ``_legacy`` entry: what the totals hold beyond the conversations
that read covers. The owned conversations that read did not cover are
``absorbs``: their spend is inside the legacy entry, so if one is read
again later its first read is taken out of the legacy entry, not counted
twice. The fold keeps the record's cost: when the read, each conversation
priced at its own model, comes to more than the record's one-model
estimate, the read entries are scaled down to it. When the legacy cost was an estimate at one price row, that row
(``px``) and the billing segment it was folded in (``seg``) are kept, so
the ledger can price a pre-upgrade rebill's base the way the folded cost
was priced (``ledger.make_row``).

Readers that cannot split their totals by conversation report them under
``TOTAL_KEY``: a monotonic max of the totals.

Nothing here imports the rest of overcode: readers build usages with
``usage`` and the daemon passes the pricing in.
"""

from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

# Token kinds, as priced (the ledger's ``usage`` keys), and their stats fields
TOKEN_KEYS = ("in", "out", "cw", "cr")
STATS_FIELDS = {
    "in": "input_tokens",
    "out": "output_tokens",
    "cw": "cache_creation_tokens",
    "cr": "cache_read_tokens",
}
SPEND_KEYS = TOKEN_KEYS + ("cost", "energy")
# The pseudo conversation a pre-per-conversation record's totals fold into
LEGACY_KEY = "_legacy"
# The pseudo conversation of a reader that reports only totals
TOTAL_KEY = "_total"

# model -> per-million prices (input, output, cache write, cache read)
PriceFn = Callable[[Optional[str]], Sequence[float]]
# (model, token usage) -> joules
EnergyFn = Callable[[Optional[str], Mapping[str, int]], float]


def usage(input_tokens: Any = 0, output_tokens: Any = 0, cache_creation_tokens: Any = 0,
          cache_read_tokens: Any = 0, *, model: Optional[str] = None,
          cost: Optional[float] = None) -> Dict[str, Any]:
    """One conversation's read, as a reader reports it.

    ``cost`` is the backend's own recorded charge, when it keeps one and it
    is non-zero; otherwise the daemon prices the tokens.
    """
    out: Dict[str, Any] = {
        "in": _int(input_tokens), "out": _int(output_tokens),
        "cw": _int(cache_creation_tokens), "cr": _int(cache_read_tokens),
    }
    if model:
        out["model"] = model
    if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost > 0:
        out["cost"] = float(cost)
    return out


def price(px: Sequence[float], tokens: Mapping[str, Any]) -> float:
    """What ``tokens`` cost at per-million prices ``px``."""
    return sum(float(p) * _int(tokens.get(k, 0)) for p, k in zip(px, TOKEN_KEYS)) / 1_000_000


def totals(entries: Mapping[str, Any]) -> Dict[str, float]:
    """The agent's spend: every entry summed, as ``SessionStats`` fields."""
    sums = _sums(entries or {})
    out: Dict[str, float] = {STATS_FIELDS[k]: int(sums[k]) for k in TOKEN_KEYS}
    out["total_tokens"] = sum(int(sums[k]) for k in TOKEN_KEYS)
    out["estimated_cost_usd"] = round(sums["cost"], 4)
    out["estimated_energy_j"] = round(sums["energy"], 1)
    return out


def recorded_totals(stats: Any) -> Dict[str, float]:
    """A stats record's totals, by ``SPEND_KEYS`` (a dict or a ``SessionStats``)."""
    get = (stats.get if isinstance(stats, Mapping)
           else (lambda key, default=0: getattr(stats, key, default)))
    out = {k: float(_int(get(STATS_FIELDS[k], 0))) for k in TOKEN_KEYS}
    out["cost"] = _num(get("estimated_cost_usd", 0))
    out["energy"] = _num(get("estimated_energy_j", 0))
    return out


def merge(
    entries: Optional[Mapping[str, Any]],
    reads: Mapping[str, Mapping[str, Any]],
    *,
    prices: PriceFn,
    energy: EnergyFn,
    default_model: Optional[str] = None,
    recorded: Optional[Mapping[str, float]] = None,
    owned_ids: Iterable[str] = (),
    seg: int = 0,
) -> Dict[str, Dict[str, Any]]:
    """The entries after one read: ``entries`` (never changed) updated by ``reads``.

    ``reads`` maps conversation ids to ``usage`` dicts. ``recorded`` is the
    record's totals (``recorded_totals``) and ``owned_ids`` the
    conversations it owns: used only to fold a record that has no entries
    yet (see the module docstring); ``seg`` is its billing segment then.
    """
    out: Dict[str, Dict[str, Any]] = {
        key: dict(value) for key, value in
        (entries.items() if isinstance(entries, Mapping) else ())
        if isinstance(key, str) and isinstance(value, Mapping)
    }
    fold = not out and recorded is not None and any(v > 0 for v in recorded.values())
    legacy = out.get(LEGACY_KEY)
    for cid, read in reads.items():
        if not isinstance(cid, str) or not cid or cid == LEGACY_KEY:
            continue
        if not isinstance(read, Mapping):
            continue
        model = read.get("model") or default_model
        previous = out.get(cid)
        entry = dict(previous) if previous is not None else _empty()
        delta = {k: max(0, _int(read.get(k, 0)) - _int(entry.get(k, 0))) for k in TOKEN_KEYS}
        grew = any(delta.values())
        for k in TOKEN_KEYS:
            entry[k] = _int(entry.get(k, 0)) + delta[k]
        if grew:
            entry["energy"] = round(_num(entry.get("energy", 0)) + energy(model, delta), 3)
        charged = read.get("cost")
        if isinstance(charged, (int, float)) and charged > 0:
            charged = float(charged)
            if entry.get("recorded"):
                # The charge's growth since the last one taken accrues
                seen = _num(entry.get("charged", entry.get("cost", 0)))
                if charged > seen:
                    entry["cost"] = round(_num(entry.get("cost", 0)) + charged - seen, 8)
                    entry["charged"] = charged
            else:
                # The first charge covers the tokens already estimated:
                # it replaces the estimate only when larger, never lowers it
                entry["cost"] = round(max(_num(entry.get("cost", 0)), charged), 8)
                entry["charged"], entry["recorded"] = charged, True
        elif grew and not entry.get("recorded"):
            entry["cost"] = round(_num(entry.get("cost", 0)) + price(prices(model), delta), 8)
        if isinstance(model, str) and model:
            entry["model"] = model
        out[cid] = entry
        if previous is None and legacy is not None and cid in (legacy.get("absorbs") or ()):
            _settle(legacy, entry, cid)
    if fold:
        out[LEGACY_KEY] = _fold(recorded, out, reads, owned_ids, prices, default_model, seg)
    return out


def _fold(recorded: Mapping[str, float], out: Dict[str, Dict[str, Any]],
          reads: Mapping[str, Any], owned_ids: Iterable[str], prices: PriceFn,
          model: Optional[str], seg: int) -> Dict[str, Any]:
    """The legacy entry of a record's totals beyond what this read covers.

    The read prices each conversation at its own model, the record priced
    every token at one: when the read's cost (or energy) is above the
    record's, the read entries are scaled down to it, so the fold never
    reprices the past and the agent's total stays what the ledger holds.
    """
    covered = _sums(out)
    legacy: Dict[str, Any] = {}
    for k in TOKEN_KEYS:
        legacy[k] = max(0, int(recorded[k]) - int(covered[k]))
    for k, places in (("cost", 8), ("energy", 3)):
        over = covered[k] - recorded[k]
        if over > 0:
            _shrink(out, reads, k, over, places)
        legacy[k] = round(max(0.0, -over), places)
    legacy["absorbs"] = [cid for cid in dict.fromkeys(owned_ids)
                         if isinstance(cid, str) and cid and cid not in reads]
    try:
        px = [float(p) for p in prices(model)]
    except Exception:
        px = None
    whole = {k: recorded[k] for k in TOKEN_KEYS}
    if px and abs(price(px, whole) - recorded["cost"]) <= max(1e-3, abs(recorded["cost"]) * 1e-6):
        # The totals' cost was every token at one price row (the old
        # estimate): kept for a rebill made before the fold (ledger.make_row)
        legacy["px"] = px
        legacy["seg"] = int(seg or 0)
    return legacy


def _sums(entries: Mapping[str, Any]) -> Dict[str, float]:
    """Every entry summed by ``SPEND_KEYS``, unrounded."""
    sums = dict.fromkeys(SPEND_KEYS, 0.0)
    for entry in entries.values():
        if isinstance(entry, Mapping):
            for key in SPEND_KEYS:
                sums[key] += _num(entry.get(key, 0))
    return sums


def _shrink(out: Dict[str, Dict[str, Any]], reads: Mapping[str, Any], key: str,
            over: float, places: int) -> None:
    """Take ``over`` of ``key`` off the entries this read covers, in proportion."""
    read = [out[cid] for cid in reads if cid in out and cid != LEGACY_KEY]
    have = sum(_num(entry.get(key, 0)) for entry in read)
    if have <= 0:
        return
    keep = max(0.0, have - over) / have
    for entry in read:
        entry[key] = round(_num(entry.get(key, 0)) * keep, places)


def _settle(legacy: Dict[str, Any], entry: Mapping[str, Any], cid: str) -> None:
    """A conversation folded into the legacy entry is read again: its first
    read moves out of the legacy entry instead of being counted twice."""
    for k in SPEND_KEYS:
        have = _num(legacy.get(k, 0))
        take = min(have, _num(entry.get(k, 0)))
        legacy[k] = int(have - take) if k in TOKEN_KEYS else round(have - take, 8)
    legacy["absorbs"] = [i for i in legacy.get("absorbs") or () if i != cid]


def _empty() -> Dict[str, Any]:
    return {"in": 0, "out": 0, "cw": 0, "cr": 0, "cost": 0.0, "energy": 0.0}


def count(value: Any) -> int:
    """A non-negative int from anything a record may hold (0 when not a number)."""
    return _int(value)


def _int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _num(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def legacy_base_cost(stats: Any, seg: int, base_usage: Any) -> Optional[float]:
    """A pre-fold rebill's base, priced as the folded cost was; None when not one.

    Under the old estimate every token was priced at the current model, and
    the segment's base was the cost at the rebill: after a ``/model`` switch
    the two disagree. When the legacy entry was folded in the same segment
    and kept its price row, the base is that row times the base tokens.
    """
    entries = (stats.get("conversations") if isinstance(stats, Mapping)
               else getattr(stats, "conversations", None))
    legacy = entries.get(LEGACY_KEY) if isinstance(entries, Mapping) else None
    if not isinstance(legacy, Mapping) or not seg or not isinstance(base_usage, Mapping):
        return None
    px = legacy.get("px")
    if not isinstance(px, list) or len(px) != 4 or _int(legacy.get("seg")) != int(seg):
        return None
    return price(px, base_usage)


def has_entries(stats: Any) -> bool:
    """Whether a stats record keeps spend per conversation (accrued costs)."""
    entries = (stats.get("conversations") if isinstance(stats, Mapping)
               else getattr(stats, "conversations", None))
    return isinstance(entries, Mapping) and bool(entries)


__all__: List[str] = [
    "LEGACY_KEY", "TOTAL_KEY", "TOKEN_KEYS", "STATS_FIELDS", "has_entries",
    "legacy_base_cost", "merge", "price", "recorded_totals", "totals", "usage",
]
