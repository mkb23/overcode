"""The spend ledger: what every agent spent, by billing code, kept for ever.

Every agent has a billing code (``Session.billing_code_id``, a code of the
registry in ``codes``; ``Session.billing_code`` is its name, a display
copy): the code named after its launch name unless ``overcode launch
--billing-code`` named one, and always its parent's for a child or a fork.
``overcode rebill`` changes it forward only.

``~/.overcode/ledger/<YYYY-MM>.jsonl`` holds one row per spend event,
named by write time (a closed month is gzipped). A row carries the agent's
*cumulative* own spend so far, never a delta, so a repeated or late row is
harmless: readers keep the last row of each agent's segment. Rows::

    {"t", "seq", "sid", "name", "parent", "code", "billing_code", "seg",
     "model", "cost", "tokens", "tok_in", "tok_out", "tok_cache", "tok_cw",
     "tok_cr", "px", "base_cost", "base_tokens", "base_usage", "kind"}

``code`` is the code's id; ``billing_code`` its name when the row was
written (for anyone grepping the files). Rows from before ids carry only
the ``billing_code`` string: a reader groups a row by its *code key*
(``row_code``: the id, else that string) and reports under the code the
registry maps it to (``codes.CodeRegistry.canonical``: legacy strings and
merges followed), shown by its current name. No row is ever rewritten.

A Claude background session the engine adopted (a 🌙 row) is billed the
same way, as an "agent" of its own (``sid`` ``claude-bg:<sessionId>``,
``parent`` the agent it nests under, that agent's code) whose rows carry
``claude_bg: true`` (``claude_bg_ledger``); reports count those segments
in ``background`` as well as ``agents``.

``kind`` is ``checkpoint`` (a running agent, at most every
``ledger.checkpoint_minutes``), ``final`` (written with its archive
record), ``close`` / ``open`` (a rebill: the old code's last row, the new
code's first), ``backfill`` (an agent archived before the ledger) or
``correction`` (below; it also carries ``closing``, ``scale`` and ``was``).
``seg`` numbers an agent's billing segments: 0 at launch, one more per
rebill. A segment's spend is its last row's cumulative values less its
``base_*`` (the cumulative values when the segment began), so spend before
a rebill stays on the old code.

An agent's cost is kept per conversation and accrues: tokens are priced
when they are read, at the model they were spent on
(``conversation_spend``), so a ``/model`` switch never reprices the past
and a segment's spend is its last cost less its base cost. Such a row
carries no prices (``px`` None). Rows and records from before that
(every token so far at the agent's *current* model's prices) carry the
prices (``px``, per million input, output, cache write, cache read tokens)
when the cost is that estimate, and a rebill keeps the segment's base
breakdown (``base_usage``): their segment's spend, or a period's, is the
tokens it added at the row's prices, never a repricing of tokens spent
before it. A recorded cost (a backend that reports one) carries no
``px`` and is taken as it is.

A **correction** row (``kind: correction``) is written when a reader fix
changes an agent's spend after the fact (``conversation_spend``: an entry
read by an older reader version is replaced, which can make the agent's
cumulative cost go *down*, once). The over-count was spread over the
agent's whole life, so the fix is too: every segment's cumulative values
*and* its base are scaled by ``scale`` (new/old per amount), which keeps
each segment's share proportional and their sum equal to the corrected
total. One correction row per segment: the open one (``closing`` false; a
live agent's later checkpoints carry the scaled base, which the record
keeps as its ``billing_base_*``) and each closed one (``closing`` true;
it replaces that segment's closed share, as a revised final row does).
``was`` is the contribution the row corrects, so a closed segment whose
share the totals have already forgotten (``CLOSED_KEEP_DAYS``) moves its
code's total by the difference instead of adding it again. A period
report scales every row written before a correction of its segment by
the correction's ``scale``, so past periods read corrected too. A
segment's total can only drop through a correction row: other rows keep
the "last cumulative row wins" rule, and contributions stay
non-negative.

``ledger/totals.json`` folds the rows incrementally: per month, how far
they were read; the last row of every open segment; per-code-key totals of
closed ones (mapped to codes when reported, so a rename or merge needs no
refold) (a ``final``, ``close`` or ``backfill`` row closes a segment;
a later checkpoint, from a snapshot taken before it closed, never reopens
it). A closed
segment's contribution is remembered for a while (CLOSED_KEEP_DAYS), so a
duplicate final row (a crash between the archive and the live file) is
not counted twice. A report for a period (``--since``, ``--month``) reads
the rows instead: a segment's spend in the period is its last cumulative
value by the end less its last one before the start.
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

from . import conversation_spend, paths
from .clock import stamp
from .month_files import (GZIP_SUFFIX, PLAIN_SUFFIX, fold_into_gzip, month_files,
                          read_gzip, writable_month)

try:
    import fcntl
    HAS_FCNTL = True
except ImportError:  # pragma: no cover - Windows
    HAS_FCNTL = False

logger = logging.getLogger(__name__)

TOTALS_VERSION = 2  # 2: keyed by code key (an id, else a legacy string)
# How long a closed segment's contribution is remembered, to replace (not
# add) a duplicate or revised final row for it
CLOSED_KEEP_DAYS = 62
# The numbers a row carries, cumulative, and what totals sum
AMOUNTS = ("cost", "tokens", "tok_in", "tok_out", "tok_cache")
# The kinds of row that end a segment
CLOSING_KINDS = ("final", "close", "backfill")
# A reader fix restating a segment's spend (see the module docstring)
CORRECTION = "correction"


def is_closing(row: dict) -> bool:
    """Whether a row ends its segment: a closing kind, or a correction of a closed one."""
    kind = row.get("kind")
    return kind in CLOSING_KINDS or (kind == CORRECTION and bool(row.get("closing")))


def _get(record, key, default=None):
    if isinstance(record, dict):
        return record.get(key, default)
    return getattr(record, key, default)


def billing_code_of(record) -> str:
    """An agent's billing code key: its code's id; for a record from before ids
    its code string, else its name (a legacy key, ``codes``)."""
    return (_get(record, "billing_code_id") or _get(record, "billing_code")
            or _get(record, "name") or "?")


def billing_name_of(record) -> str:
    """The name an agent's billing code is shown by (the record's display copy)."""
    return _get(record, "billing_code") or _get(record, "name") or "?"


def row_code(row: dict) -> str:
    """The code key a ledger row is grouped by: its id, else its legacy string."""
    return row.get("code") or row.get("billing_code") or "?"


def spend_of(record) -> Dict[str, float]:
    """An agent's cumulative own spend, from its record (a dict or a Session)."""
    stats = _get(record, "stats") or {}
    get = (lambda k: stats.get(k, 0)) if isinstance(stats, dict) else (lambda k: getattr(stats, k, 0))
    def num(key):
        try:
            return float(get(key) or 0)
        except (TypeError, ValueError):
            return 0.0
    return {
        "cost": num("estimated_cost_usd"),
        "tokens": int(num("total_tokens")),
        "tok_in": int(num("input_tokens")),
        "tok_out": int(num("output_tokens")),
        "tok_cache": int(num("cache_creation_tokens") + num("cache_read_tokens")),
    }


def billing_lookup(*groups) -> Dict[str, dict]:
    """``{id: {billing_code_id, billing_code, parent_session_id, name}}`` of records and sessions.

    What ``inherited_billing`` walks; the first group to name an id wins.
    """
    found: Dict[str, dict] = {}
    for group in groups:
        for record in group:
            sid = _get(record, "id")
            if isinstance(sid, str) and sid not in found:
                found[sid] = {key: _get(record, key) for key in
                              ("billing_code_id", "billing_code", "parent_session_id", "name")}
    return found


def inherited_billing(record, lookup: Dict[str, dict]) -> Tuple[Optional[str], str]:
    """``(id or None, name)`` of a record's billing code: its own, else its
    parent's (up to the root), else (None, its name).

    For records from before billing codes or ids (an upgrade): children
    always bill to the code their tree's root was launched with. None for
    the id: a legacy string, for the registry to map (``codes.adopt``).
    """
    seen = set()
    current = record
    while True:
        code_id, code = _get(current, "billing_code_id"), _get(current, "billing_code")
        if code_id or code:
            return code_id, code or billing_name_of(current)
        parent_id = _get(current, "parent_session_id")
        parent = lookup.get(parent_id) if parent_id and parent_id not in seen else None
        if parent is None:
            return None, _get(current, "name") or billing_name_of(record)
        seen.add(parent_id)
        current = parent


def usage_of(record) -> Dict[str, int]:
    """An agent's cumulative token breakdown, as priced: in, out, cache write, cache read."""
    stats = _get(record, "stats") or {}
    get = (lambda k: stats.get(k, 0)) if isinstance(stats, dict) else (lambda k: getattr(stats, k, 0))
    out = {}
    for name, key in (("in", "input_tokens"), ("out", "output_tokens"),
                      ("cw", "cache_creation_tokens"), ("cr", "cache_read_tokens")):
        try:
            out[name] = int(get(key) or 0)
        except (TypeError, ValueError):
            out[name] = 0
    return out


def price(px, usage: Dict[str, int]) -> float:
    """What ``usage`` costs at ``px`` (per-million prices: in, out, cache write, cache read)."""
    return sum(float(p) * int(usage.get(k, 0) or 0)
               for p, k in zip(px, ("in", "out", "cw", "cr"))) / 1_000_000


def estimate_prices(record, usage: Dict[str, int], cost: float) -> Optional[List[float]]:
    """The prices ``cost`` was estimated at, or None when it is a recorded cost.

    The daemon prices every token so far at the agent's model and
    provider (``daemon_stats_sync._stage_usage``); a cost that is not that
    estimate came from the backend.
    """
    try:
        from .settings import get_model_pricing, get_user_config

        mp = get_model_pricing(_get(record, "model"), get_user_config(),
                               provider=_get(record, "provider"))
        px = [float(mp.input), float(mp.output), float(mp.cache_write), float(mp.cache_read)]
    except Exception:
        return None
    if abs(price(px, usage) - cost) > max(1e-3, abs(cost) * 1e-6):
        return None
    return px


def _usage(row: dict) -> Optional[Dict[str, int]]:
    """A row's token breakdown, as priced; None for a row written without one."""
    if "tok_cw" not in row or "tok_cr" not in row:
        return None
    return {"in": row.get("tok_in", 0) or 0, "out": row.get("tok_out", 0) or 0,
            "cw": row.get("tok_cw", 0) or 0, "cr": row.get("tok_cr", 0) or 0}


def make_row(record, kind: str, *, now: Optional[datetime] = None,
             code: Optional[str] = None, billing_code: Optional[str] = None,
             seg: Optional[int] = None, base: Optional[Dict[str, float]] = None,
             t: Optional[str] = None) -> dict:
    """The ledger row for an agent's record (a sessions.json dict or a Session).

    Billed to ``code`` (an id) named ``billing_code``; by default the
    record's own. ``billing_code`` alone is a legacy string (no ``code``).
    """
    from .codes import is_code_id

    now = now or datetime.now()
    if code is None and billing_code is None:
        code, billing_code = billing_code_of(record), billing_name_of(record)
    if not is_code_id(code):
        billing_code, code = billing_code or code, None
    spend = spend_of(record)
    usage = usage_of(record)
    stats = _get(record, "stats") or {}
    if base is None:
        base = {"cost": _get(record, "billing_base_cost", 0.0) or 0.0,
                "tokens": _get(record, "billing_base_tokens", 0) or 0,
                "usage": _get(record, "billing_base_usage")}
        repriced = conversation_spend.legacy_base_cost(
            stats, int(_get(record, "billing_seg", 0) or 0), base["usage"])
        if repriced is not None:
            base["cost"] = repriced
    base_usage = base.get("usage")
    # Spend kept per conversation accrues (never repriced), so the
    # segment's spend is the cost less its base, as it is: no prices
    px = (None if conversation_spend.has_entries(stats)
          else estimate_prices(record, usage, spend["cost"]))
    return {
        "t": t or stamp(now),
        "seq": time.time_ns(),
        "sid": _get(record, "id"),
        "name": _get(record, "name"),
        "parent": _get(record, "parent_session_id"),
        **({"code": code} if code else {}),
        "billing_code": billing_code or billing_name_of(record),
        "seg": int(_get(record, "billing_seg", 0) or 0) if seg is None else seg,
        "model": _get(record, "model"),
        **spend,
        "tok_cw": usage["cw"],
        "tok_cr": usage["cr"],
        "px": px,
        "base_cost": float(base.get("cost", 0.0) or 0.0),
        "base_tokens": int(base.get("tokens", 0) or 0),
        "base_usage": dict(base_usage) if isinstance(base_usage, dict) else None,
        "kind": kind,
    }


def contribution(row: dict) -> Dict[str, float]:
    """What a segment's (last) row adds to its code: cumulative less the segment's base.

    With an estimated cost (``px``) and the base's breakdown, the base is
    priced as the row's cost was, so a model switch after a rebill does
    not move the repriced past onto the new code.
    """
    out = {key: row.get(key, 0) or 0 for key in AMOUNTS}
    base_cost = float(row.get("base_cost", 0) or 0)
    px, base_usage = row.get("px"), row.get("base_usage")
    if px and isinstance(base_usage, dict):
        base_cost = price(px, base_usage)
    out["cost"] = max(0.0, float(out["cost"]) - base_cost)
    out["tokens"] = max(0, int(out["tokens"]) - int(row.get("base_tokens", 0) or 0))
    if row.get("seg"):
        # The breakdown is not split at a rebill: only cost and tokens are
        out["tok_in"] = out["tok_out"] = out["tok_cache"] = 0
    return out


def _supersedes(row: dict, last: Optional[dict]) -> bool:
    """Whether ``row`` replaces ``last`` as its segment's last row.

    The newer one, except that a closing row is never replaced by a
    checkpoint (one built from a snapshot taken before the segment
    closed), and wins over any checkpoint.
    """
    if last is None:
        return True
    row_closing = is_closing(row)
    last_closing = is_closing(last)
    if row_closing != last_closing:
        return row_closing
    return int(last.get("seq", 0)) <= int(row.get("seq", 0))


def _key(row: dict) -> str:
    return f"{row.get('sid')}/{int(row.get('seg', 0) or 0)}"


def _factor(row: dict, corrections: Dict[str, List[Tuple[int, dict]]]) -> Dict[str, float]:
    """What the corrections written after ``row`` (of its segment) scale it by, per amount."""
    factor = dict.fromkeys(AMOUNTS, 1.0)
    seq = int(row.get("seq", 0))
    for at, scale in corrections.get(_key(row), ()):
        if at > seq:
            for name in AMOUNTS:
                try:
                    factor[name] *= float(scale.get(name, 1.0))
                except (TypeError, ValueError):
                    pass
    return factor


def _scaled(amounts: Dict[str, float], factor: Dict[str, float]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for name in AMOUNTS:
        value = amounts.get(name, 0) * factor.get(name, 1.0)
        out[name] = float(value) if name == "cost" else int(round(value))
    return out


def _ratio(new: float, old: float) -> float:
    return float(new) / float(old) if old else 1.0


def correction_rows(before, after, *, closed: bool, earlier: Optional[Dict[int, dict]] = None,
                    now: Optional[datetime] = None) -> Tuple[List[dict], Dict[str, object]]:
    """The ledger rows restating an agent's spend after a reader fix, and its new base.

    ``before`` and ``after`` are the agent's record (a dict or a Session)
    with the old and the corrected spend; ``closed`` whether its current
    segment is closed (an archived agent); ``earlier`` the last row of each
    earlier segment (by ``seg``, from a rebill: ``Ledger.segment_rows``).
    Every segment is scaled by new/old per amount (see the module
    docstring). Returns the rows and the record's new
    ``billing_base_cost`` / ``billing_base_tokens`` / ``billing_base_usage``.
    """
    now = now or datetime.now()
    old, new = spend_of(before), spend_of(after)
    scale = {name: _ratio(new[name], old[name]) for name in AMOUNTS}
    old_usage, new_usage = usage_of(before), usage_of(after)
    usage_scale = {k: _ratio(new_usage[k], old_usage[k]) for k in old_usage}
    stats = _get(before, "stats") or {}
    seg = int(_get(before, "billing_seg", 0) or 0)
    base_cost = float(_get(before, "billing_base_cost", 0.0) or 0.0)
    base_tokens = int(_get(before, "billing_base_tokens", 0) or 0)
    base_usage = _get(before, "billing_base_usage")
    repriced = conversation_spend.legacy_base_cost(stats, seg, base_usage)
    if repriced is not None:
        base_cost = repriced

    def scaled_usage(value):
        if not isinstance(value, dict):
            return None
        return {k: int(round(int(value.get(k, 0) or 0) * usage_scale.get(k, 1.0)))
                for k in ("in", "out", "cw", "cr")}

    base = {"cost": base_cost * scale["cost"],
            "tokens": int(round(base_tokens * scale["tokens"])),
            "usage": scaled_usage(base_usage)}
    was_row = make_row(before, "final" if closed else "checkpoint", now=now)
    row = make_row(after, CORRECTION, now=now, base=base, seg=seg)
    row.update(closing=bool(closed), scale=scale, was=contribution(was_row))
    rows = [row]
    for prior_seg, prior in sorted((earlier or {}).items()):
        if int(prior_seg) >= seg or not isinstance(prior, dict):
            continue
        fixed = dict(prior)
        for name in AMOUNTS:
            value = float(prior.get(name, 0) or 0) * scale[name]
            fixed[name] = value if name == "cost" else int(round(value))
        for field, k in (("tok_cw", "cw"), ("tok_cr", "cr")):
            if field in prior:
                fixed[field] = int(round(int(prior.get(field) or 0) * usage_scale[k]))
        prior_base_cost = float(prior.get("base_cost", 0) or 0)
        if prior.get("px") and isinstance(prior.get("base_usage"), dict):
            prior_base_cost = price(prior["px"], prior["base_usage"])
        fixed.update(
            t=stamp(now), seq=time.time_ns(), kind=CORRECTION, closing=True, px=None,
            base_cost=prior_base_cost * scale["cost"],
            base_tokens=int(round(int(prior.get("base_tokens", 0) or 0) * scale["tokens"])),
            base_usage=scaled_usage(prior.get("base_usage")),
            scale=scale, was=contribution(prior))
        rows.append(fixed)
    new_base = {"billing_base_cost": base["cost"], "billing_base_tokens": base["tokens"],
                "billing_base_usage": base["usage"]}
    return rows, new_base


class Ledger:
    """The ledger directory of one sessions registry (see the module docstring)."""

    def __init__(self, registry_dir: Optional[Path] = None):
        from .codes import CodeRegistry

        self.dir = paths.ledger_dir(registry_dir)
        self.totals_path = paths.ledger_totals_path(registry_dir)
        self.codes = CodeRegistry(self.dir)

    def by_code(self, report: Dict[str, dict]) -> Dict[str, dict]:
        """A report by code key, summed by the code each key reports under (``codes``).

        Keys: code ids, and any legacy string the registry does not map.
        """
        out: Dict[str, dict] = {}
        for key, entry in report.items():
            code = self.codes.canonical(key)
            if code not in out:
                out[code] = dict(entry)
                continue
            for name, value in entry.items():
                if isinstance(value, (int, float)):
                    out[code][name] = out[code].get(name, 0) + value
        return out

    def named(self, report: Dict[str, dict]) -> Dict[str, dict]:
        """A report by code (``totals``, ``report``) keyed by each code's current name."""
        return {self.codes.name_of(code): entry for code, entry in report.items()}

    # ── files ───────────────────────────────────────────────────────────

    @contextmanager
    def _lock(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        if not HAS_FCNTL:
            yield
            return
        fd = os.open(self.dir / ".lock", os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def months(self) -> List[Tuple[str, Path]]:
        """Each month's ledger file, oldest first: the gzipped one when both exist."""
        found: Dict[str, Path] = {}
        try:
            entries = list(os.scandir(self.dir))
        except OSError:
            return []
        for entry in entries:
            if entry.name.endswith(GZIP_SUFFIX):
                found[entry.name[: -len(GZIP_SUFFIX)]] = Path(entry.path)
            elif entry.name.endswith(PLAIN_SUFFIX):
                found.setdefault(entry.name[: -len(PLAIN_SUFFIX)], Path(entry.path))
        return sorted(found.items())

    def append(self, rows: Iterable[dict], *, now: Optional[datetime] = None) -> None:
        """Append rows to this month's file under the ledger lock; one fsync.

        The month is chosen under the lock and is never a closed one: a
        late write (a tick's ``now`` from before midnight, a clock behind
        another writer's) goes to the newest open month instead
        (``month_files.writable_month``). A row's own ``t`` dates it.
        """
        rows = [r for r in rows if r.get("sid")]
        if not rows:
            return
        with self._lock():
            month = writable_month(self.dir, (now or datetime.now()).strftime("%Y-%m"))
            path = self.dir / f"{month}{PLAIN_SUFFIX}"
            with open(path, "a+b") as f:
                f.seek(0, os.SEEK_END)
                if f.tell() > 0:
                    f.seek(-1, os.SEEK_END)
                    if f.read(1) != b"\n":
                        f.write(b"\n")
                for row in rows:
                    f.write((json.dumps(row, default=str) + "\n").encode("utf-8"))
                f.flush()
                os.fsync(f.fileno())
            self._gzip_closed(month)

    def _gzip_closed(self, current: str) -> None:
        """Gzip every plain month before ``current`` (caller holds the lock).

        Atomic and durable (``month_files.fold_into_gzip``): a plain file
        next to an existing gzip is folded into it, never dropped.
        """
        plain_months, _ = month_files(self.dir)
        for month in sorted(plain_months):
            if month >= current:
                continue
            plain = self.dir / f"{month}{PLAIN_SUFFIX}"
            try:
                fold_into_gzip(plain, self.dir / f"{month}{GZIP_SUFFIX}")
            except OSError as e:
                logger.warning("could not gzip ledger month %s: %s", month, e)

    @staticmethod
    def _rows_of(path: Path, offset: int = 0) -> Tuple[List[dict], int, int]:
        """(rows, end offset of the last whole line, inode) of a month file."""
        try:
            st = path.stat()
            if path.name.endswith(GZIP_SUFFIX):
                data, offset = read_gzip(path), 0
            else:
                with open(path, "rb") as f:
                    f.seek(offset)
                    data = f.read()
        except OSError:
            return [], offset, 0
        cut = data.rfind(b"\n") + 1
        rows = []
        for line in data[:cut].split(b"\n"):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("sid"):
                rows.append(row)
        return rows, (0 if path.name.endswith(GZIP_SUFFIX) else offset + cut), st.st_ino

    def iter_rows(self) -> Iterator[dict]:
        """Every row, oldest month first, in file order."""
        for _, path in self.months():
            yield from self._rows_of(path)[0]

    def segment_rows(self, session_ids: Iterable[str]) -> Dict[str, Dict[int, dict]]:
        """The last row of each segment of ``session_ids``: ``{sid: {seg: row}}``.

        Reads every month: what correcting a rebilled agent needs, once.
        """
        wanted = set(session_ids)
        found: Dict[str, Dict[int, dict]] = {}
        if not wanted:
            return found
        for row in self.iter_rows():
            sid = row.get("sid")
            if sid not in wanted:
                continue
            segs = found.setdefault(sid, {})
            seg = int(row.get("seg", 0) or 0)
            if _supersedes(row, segs.get(seg)):
                segs[seg] = row
        return found

    # ── totals ──────────────────────────────────────────────────────────

    def _load_totals(self) -> dict:
        empty = {"v": TOTALS_VERSION, "months": {}, "open": {}, "closed": {}, "codes": {}}
        try:
            data = json.loads(self.totals_path.read_text())
        except (OSError, ValueError):
            return empty
        if not isinstance(data, dict) or data.get("v") != TOTALS_VERSION:
            return empty
        for key in ("months", "open", "closed", "codes"):
            if not isinstance(data.get(key), dict):
                return empty
        return data

    @staticmethod
    def _fold(totals: dict, row: dict, now: datetime) -> None:
        """Fold one row into the running totals (see the module docstring)."""
        key = _key(row)
        codes, open_, closed = totals["codes"], totals["open"], totals["closed"]
        closing = is_closing(row)
        if (closing and row.get("kind") == CORRECTION and key not in closed
                and key not in open_ and isinstance(row.get("was"), dict)):
            # A correction of a segment whose closed share has been
            # forgotten: its code moves by the difference
            code = row_code(row)
            amounts, was = contribution(row), row["was"]
            entry = codes.setdefault(code, dict.fromkeys(AMOUNTS, 0) | {"agents": 0})
            for name in AMOUNTS:
                entry[name] = entry.get(name, 0) + amounts[name] - (was.get(name, 0) or 0)
            closed[key] = {"code": code, "amounts": amounts, "at": stamp(now),
                           "seq": int(row.get("seq", 0))}
            return
        if key in closed:
            if int(row.get("seq", 0)) <= int(closed[key].get("seq", 0)):
                return  # older than what closed it: already counted
            if not closing:
                # A checkpoint built from a snapshot taken before the segment
                # closed (a daemon tick racing a kill or a rebill): a closed
                # segment never reopens
                return
            # A duplicate or revised closing row: replace its share
            code, previous = closed[key]["code"], closed[key]["amounts"]
            entry = codes.setdefault(code, dict.fromkeys(AMOUNTS, 0) | {"agents": 0})
            for name in AMOUNTS:
                entry[name] = entry.get(name, 0) - previous.get(name, 0)
            entry["agents"] = entry.get("agents", 0) - 1
            if closed[key].get("bg"):
                entry["background"] = entry.get("background", 0) - 1
            del closed[key]
        last = open_.get(key)
        if (last is not None and not closing
                and int(last.get("seq", 0)) > int(row.get("seq", 0))):
            row = last  # an older row arriving late does not move the segment back
        if is_closing(row):
            open_.pop(key, None)
            code = row_code(row)
            amounts = contribution(row)
            entry = codes.setdefault(code, dict.fromkeys(AMOUNTS, 0) | {"agents": 0})
            for name in AMOUNTS:
                entry[name] = entry.get(name, 0) + amounts[name]
            entry["agents"] = entry.get("agents", 0) + 1
            closed[key] = {"code": code, "amounts": amounts, "at": stamp(now),
                           "seq": int(row.get("seq", 0))}
            if row.get("claude_bg"):
                entry["background"] = entry.get("background", 0) + 1
                closed[key]["bg"] = True
        else:
            open_[key] = row

    def totals(self, *, now: Optional[datetime] = None, save: bool = True) -> Dict[str, dict]:
        """Spend per billing code over the whole ledger, folded incrementally.

        ``{code id: {"cost", "tokens", "tok_in", "tok_out", "tok_cache",
        "agents", "running", "background"}}``: closed segments from the
        fold, plus each open segment's last row (``running`` counts those;
        ``background`` the background sessions' segments among ``agents``).
        """
        now = now or datetime.now()
        totals = self._load_totals()
        months = totals["months"]
        changed = False
        for month, path in self.months():
            cursor = months.get(month) or {"off": 0, "ino": None, "n": 0, "closed": False}
            if cursor.get("closed"):
                continue
            if path.name.endswith(GZIP_SUFFIX):
                rows, _, ino = self._rows_of(path)
                rows = rows[int(cursor.get("n", 0)):]
                cursor = {"off": 0, "ino": ino, "n": int(cursor.get("n", 0)) + len(rows),
                          "closed": True}
            else:
                rows, end, ino = self._rows_of(path, int(cursor.get("off", 0)))
                if cursor.get("ino") not in (None, ino):
                    return self._refold(now, save=save)  # replaced underneath us
                cursor = {"off": end, "ino": ino, "n": int(cursor.get("n", 0)) + len(rows),
                          "closed": False}
            for row in rows:
                self._fold(totals, row, now)
            if rows or months.get(month) != cursor:
                changed = True
            months[month] = cursor
        cutoff = stamp(now - timedelta(days=CLOSED_KEEP_DAYS))
        stale = [k for k, v in totals["closed"].items() if str(v.get("at", "")) < cutoff]
        for key in stale:
            del totals["closed"][key]
        changed = changed or bool(stale)
        if changed and save:
            self._save_totals(totals)
        return self.by_code(self._report(totals))

    def _refold(self, now: datetime, *, save: bool) -> Dict[str, dict]:
        try:
            self.totals_path.unlink()
        except OSError:
            pass
        return self.totals(now=now, save=save)

    def _save_totals(self, totals: dict) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.totals_path.with_name(f".{self.totals_path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(totals))
            os.replace(tmp, self.totals_path)
        except OSError as e:
            logger.warning("could not save ledger totals: %s", e)

    @staticmethod
    def _report(totals: dict) -> Dict[str, dict]:
        report: Dict[str, dict] = {}
        for code, entry in totals["codes"].items():
            report[code] = {name: entry.get(name, 0) for name in AMOUNTS}
            report[code]["agents"] = entry.get("agents", 0)
            report[code]["running"] = 0
            report[code]["background"] = entry.get("background", 0)
        for row in totals["open"].values():
            code = row_code(row)
            entry = report.setdefault(code, dict.fromkeys(AMOUNTS, 0)
                                      | {"agents": 0, "running": 0, "background": 0})
            for name, value in contribution(row).items():
                entry[name] += value
            entry["agents"] += 1
            entry["running"] += 1
            if row.get("claude_bg"):
                entry["background"] += 1
        return report

    def report(self, since: Optional[datetime] = None,
               until: Optional[datetime] = None) -> Dict[str, dict]:
        """Spend per billing code (id) in [since, until), from the rows (by their time).

        A segment's spend in the period is its last cumulative value before
        ``until`` less its last one before ``since``. Reads every month: a
        CLI report, not a hot path.
        """
        start = stamp(since) if since else ""
        end = stamp(until) if until else "￿"
        before: Dict[str, dict] = {}
        within: Dict[str, dict] = {}
        rows = list(self.iter_rows())
        # Corrections restate every earlier row of their segment
        corrections: Dict[str, List[Tuple[int, dict]]] = {}
        for row in rows:
            if row.get("kind") == CORRECTION and isinstance(row.get("scale"), dict):
                corrections.setdefault(_key(row), []).append(
                    (int(row.get("seq", 0)), row["scale"]))
        for row in rows:
            t = str(row.get("t", ""))
            if t >= end:
                continue
            target = before if t < start else within
            key = _key(row)
            if _supersedes(row, target.get(key)):
                target[key] = row
        report: Dict[str, dict] = {}
        for key, row in within.items():
            row_factor = _factor(row, corrections)
            amounts = _scaled(contribution(row), row_factor)
            prior = before.get(key)
            if prior is not None and not _supersedes(row, prior):
                continue  # closed before the period: a stale checkpoint after it is no spend
            if prior is not None:
                earlier = _scaled(contribution(prior), _factor(prior, corrections))
                amounts = {k: max(0, amounts[k] - earlier[k]) for k in AMOUNTS}
                px, prior_usage = row.get("px"), _usage(prior)
                if px and prior_usage is not None:
                    # The tokens added in the period at the end row's prices:
                    # a model switch does not reprice earlier periods into it
                    amounts["cost"] = max(0.0, (float(row.get("cost", 0) or 0)
                                                - price(px, prior_usage))
                                          * row_factor["cost"])
            code = row_code(row)
            entry = report.setdefault(code, dict.fromkeys(AMOUNTS, 0)
                                      | {"agents": 0, "running": 0, "background": 0})
            for name in AMOUNTS:
                entry[name] += amounts[name]
            entry["agents"] += 1
            if not is_closing(row):
                entry["running"] += 1
            if row.get("claude_bg"):
                entry["background"] += 1
        return self.by_code(report)

    # ── backfill ────────────────────────────────────────────────────────

    @property
    def _backfill_marker(self) -> Path:
        return self.dir / ".backfilled"

    def backfilled(self) -> bool:
        """Whether the archive has been backfilled since it last took in old records."""
        return self._backfill_marker.exists()

    def invalidate_backfill(self) -> None:
        """Make the next ``backfill_from_archive`` run (old archive records arrived)."""
        try:
            self._backfill_marker.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning("could not reset the ledger backfill marker: %s", e)

    def backfill_from_archive(self, session_manager) -> int:
        """Give every archived agent whose spend the ledger has not closed a ``backfill`` row.

        Idempotent by agent: one with a ``final`` or ``backfill`` row is
        skipped, so it can run again whenever records arrive from an older
        overcode (``invalidate_backfill``; a downgrade and re-upgrade). An
        agent with only a running segment (a checkpoint, then archived by
        an older overcode) has that segment closed under its code. Its
        billing code is its own, else its parent's (walking up to the
        root: children always bill to the parent), else its name. A marker
        file makes the next call cost one stat.
        """
        if self.backfilled():
            return 0
        closed, running = set(), {}
        for row in self.iter_rows():
            sid = row.get("sid")
            if row.get("kind") in ("final", "backfill"):
                closed.add(sid)
            elif sid not in running or int(running[sid].get("seq", 0)) <= int(row.get("seq", 0)):
                running[sid] = row
        records = list(session_manager.iter_archived_records(newest_first=True))
        lookup = billing_lookup(records, session_manager.list_sessions())
        rows = []
        for record in records:
            sid = record.get("id")
            if sid in closed:
                continue
            t = record.get("end_time") or record.get("start_time")
            last = running.get(sid)
            if last is not None and not (record.get("billing_code_id")
                                         or record.get("billing_code")):
                rows.append(make_row(record, "backfill", t=t, code=last.get("code"),
                                     billing_code=last.get("billing_code"),
                                     seg=int(last.get("seg", 0) or 0),
                                     base={"cost": last.get("base_cost", 0.0),
                                           "tokens": last.get("base_tokens", 0),
                                           "usage": last.get("base_usage")}))
            else:
                code_id, name = inherited_billing(record, lookup)
                rows.append(make_row(record, "backfill", t=t, code=code_id, billing_code=name))
        rows.reverse()
        for i in range(0, len(rows), 1000):
            self.append(rows[i:i + 1000])
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._backfill_marker.write_text(datetime.now().isoformat())
        except OSError:
            pass
        return len(rows)
