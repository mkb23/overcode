"""The spend ledger: what every agent spent, by billing code, kept for ever.

Every agent has a billing code (``Session.billing_code``): its launch name
unless ``overcode launch --billing-code`` set one, and always its parent's
for a child or a fork. ``overcode rebill`` changes it forward only.

``~/.overcode/ledger/<YYYY-MM>.jsonl`` holds one row per spend event,
named by write time (a closed month is gzipped). A row carries the agent's
*cumulative* own spend so far, never a delta, so a repeated or late row is
harmless: readers keep the last row of each agent's segment. Rows::

    {"t", "seq", "sid", "name", "parent", "billing_code", "seg", "model",
     "cost", "tokens", "tok_in", "tok_out", "tok_cache", "tok_cw", "tok_cr",
     "px", "base_cost", "base_tokens", "base_usage", "kind"}

``kind`` is ``checkpoint`` (a running agent, at most every
``ledger.checkpoint_minutes``), ``final`` (written with its archive
record), ``close`` / ``open`` (a rebill: the old code's last row, the new
code's first) or ``backfill`` (an agent archived before the ledger).
``seg`` numbers an agent's billing segments: 0 at launch, one more per
rebill. A segment's spend is its last row's cumulative values less its
``base_*`` (the cumulative values when the segment began), so spend before
a rebill stays on the old code.

An estimated cost is every token so far at the agent's *current* model's
prices, so a ``/model`` switch reprices the past. A row whose cost is that
estimate carries the prices (``px``, per million input, output, cache
write, cache read tokens) and its token breakdown, and a rebill keeps the
segment's base breakdown (``base_usage``): a segment's spend, or a
period's, is then the tokens it added at the row's prices, never a
repricing of tokens spent before it. A recorded cost (a backend that
reports one) carries no ``px`` and is taken as it is.

``ledger/totals.json`` folds the rows incrementally: per month, how far
they were read; the last row of every open segment; per-code totals of
closed ones (a ``final``, ``close`` or ``backfill`` row closes a segment;
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

from . import paths
from .clock import stamp
from .month_files import (GZIP_SUFFIX, PLAIN_SUFFIX, fold_into_gzip, month_files,
                          read_gzip, writable_month)

try:
    import fcntl
    HAS_FCNTL = True
except ImportError:  # pragma: no cover - Windows
    HAS_FCNTL = False

logger = logging.getLogger(__name__)

TOTALS_VERSION = 1
# How long a closed segment's contribution is remembered, to replace (not
# add) a duplicate or revised final row for it
CLOSED_KEEP_DAYS = 62
# The numbers a row carries, cumulative, and what totals sum
AMOUNTS = ("cost", "tokens", "tok_in", "tok_out", "tok_cache")
# The kinds of row that end a segment
CLOSING_KINDS = ("final", "close", "backfill")


def _get(record, key, default=None):
    if isinstance(record, dict):
        return record.get(key, default)
    return getattr(record, key, default)


def billing_code_of(record) -> str:
    """An agent's billing code: its own, else its name (records from before codes)."""
    return _get(record, "billing_code") or _get(record, "name") or "?"


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
    """``{id: {billing_code, parent_session_id, name}}`` of records and sessions.

    What ``inherited_billing_code`` walks; the first group to name an id wins.
    """
    found: Dict[str, dict] = {}
    for group in groups:
        for record in group:
            sid = _get(record, "id")
            if isinstance(sid, str) and sid not in found:
                found[sid] = {key: _get(record, key)
                              for key in ("billing_code", "parent_session_id", "name")}
    return found


def inherited_billing_code(record, lookup: Dict[str, dict]) -> str:
    """A record's billing code: its own, else its parent's (up to the root), else its name.

    For records from before billing codes (an upgrade): children always
    bill to the code their tree's root was launched with.
    """
    seen = set()
    current = record
    while True:
        code = _get(current, "billing_code")
        if code:
            return code
        parent_id = _get(current, "parent_session_id")
        parent = lookup.get(parent_id) if parent_id and parent_id not in seen else None
        if parent is None:
            return _get(current, "name") or billing_code_of(record)
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
             billing_code: Optional[str] = None, seg: Optional[int] = None,
             base: Optional[Dict[str, float]] = None, t: Optional[str] = None) -> dict:
    """The ledger row for an agent's record (a sessions.json dict or a Session)."""
    now = now or datetime.now()
    spend = spend_of(record)
    usage = usage_of(record)
    if base is None:
        base = {"cost": _get(record, "billing_base_cost", 0.0) or 0.0,
                "tokens": _get(record, "billing_base_tokens", 0) or 0,
                "usage": _get(record, "billing_base_usage")}
    base_usage = base.get("usage")
    return {
        "t": t or stamp(now),
        "seq": time.time_ns(),
        "sid": _get(record, "id"),
        "name": _get(record, "name"),
        "parent": _get(record, "parent_session_id"),
        "billing_code": billing_code or billing_code_of(record),
        "seg": int(_get(record, "billing_seg", 0) or 0) if seg is None else seg,
        "model": _get(record, "model"),
        **spend,
        "tok_cw": usage["cw"],
        "tok_cr": usage["cr"],
        "px": estimate_prices(record, usage, spend["cost"]),
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
    row_closing = row.get("kind") in CLOSING_KINDS
    last_closing = last.get("kind") in CLOSING_KINDS
    if row_closing != last_closing:
        return row_closing
    return int(last.get("seq", 0)) <= int(row.get("seq", 0))


def _key(row: dict) -> str:
    return f"{row.get('sid')}/{int(row.get('seg', 0) or 0)}"


class Ledger:
    """The ledger directory of one sessions registry (see the module docstring)."""

    def __init__(self, registry_dir: Optional[Path] = None):
        self.dir = paths.ledger_dir(registry_dir)
        self.totals_path = paths.ledger_totals_path(registry_dir)

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
        closing = row.get("kind") in CLOSING_KINDS
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
            del closed[key]
        last = open_.get(key)
        if (last is not None and not closing
                and int(last.get("seq", 0)) > int(row.get("seq", 0))):
            row = last  # an older row arriving late does not move the segment back
        if row.get("kind") in CLOSING_KINDS:
            open_.pop(key, None)
            code = row.get("billing_code") or "?"
            amounts = contribution(row)
            entry = codes.setdefault(code, dict.fromkeys(AMOUNTS, 0) | {"agents": 0})
            for name in AMOUNTS:
                entry[name] = entry.get(name, 0) + amounts[name]
            entry["agents"] = entry.get("agents", 0) + 1
            closed[key] = {"code": code, "amounts": amounts, "at": stamp(now),
                           "seq": int(row.get("seq", 0))}
        else:
            open_[key] = row

    def totals(self, *, now: Optional[datetime] = None, save: bool = True) -> Dict[str, dict]:
        """Spend per billing code over the whole ledger, folded incrementally.

        ``{code: {"cost", "tokens", "tok_in", "tok_out", "tok_cache",
        "agents", "running"}}``: closed segments from the fold, plus each
        open segment's last row (``running`` counts those).
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
        return self._report(totals)

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
        for row in totals["open"].values():
            code = row.get("billing_code") or "?"
            entry = report.setdefault(code, dict.fromkeys(AMOUNTS, 0) | {"agents": 0, "running": 0})
            for name, value in contribution(row).items():
                entry[name] += value
            entry["agents"] += 1
            entry["running"] += 1
        return report

    def report(self, since: Optional[datetime] = None,
               until: Optional[datetime] = None) -> Dict[str, dict]:
        """Spend per billing code in [since, until), from the rows (by their time).

        A segment's spend in the period is its last cumulative value before
        ``until`` less its last one before ``since``. Reads every month: a
        CLI report, not a hot path.
        """
        start = stamp(since) if since else ""
        end = stamp(until) if until else "￿"
        before: Dict[str, dict] = {}
        within: Dict[str, dict] = {}
        for row in self.iter_rows():
            t = str(row.get("t", ""))
            if t >= end:
                continue
            target = before if t < start else within
            key = _key(row)
            if _supersedes(row, target.get(key)):
                target[key] = row
        report: Dict[str, dict] = {}
        for key, row in within.items():
            amounts = contribution(row)
            prior = before.get(key)
            if prior is not None and not _supersedes(row, prior):
                continue  # closed before the period: a stale checkpoint after it is no spend
            if prior is not None:
                earlier = contribution(prior)
                amounts = {k: max(0, amounts[k] - earlier[k]) for k in AMOUNTS}
                px, prior_usage = row.get("px"), _usage(prior)
                if px and prior_usage is not None:
                    # The tokens added in the period at the end row's prices:
                    # a model switch does not reprice earlier periods into it
                    amounts["cost"] = max(0.0, float(row.get("cost", 0) or 0)
                                          - price(px, prior_usage))
            code = row.get("billing_code") or "?"
            entry = report.setdefault(code, dict.fromkeys(AMOUNTS, 0) | {"agents": 0, "running": 0})
            for name in AMOUNTS:
                entry[name] += amounts[name]
            entry["agents"] += 1
            if row.get("kind") not in CLOSING_KINDS:
                entry["running"] += 1
        return report

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
            if last is not None and not record.get("billing_code"):
                rows.append(make_row(record, "backfill", t=t,
                                     billing_code=last.get("billing_code"),
                                     seg=int(last.get("seg", 0) or 0),
                                     base={"cost": last.get("base_cost", 0.0),
                                           "tokens": last.get("base_tokens", 0),
                                           "usage": last.get("base_usage")}))
            else:
                rows.append(make_row(record, "backfill", t=t,
                                     billing_code=inherited_billing_code(record, lookup)))
        rows.reverse()
        for i in range(0, len(rows), 1000):
            self.append(rows[i:i + 1000])
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._backfill_marker.write_text(datetime.now().isoformat())
        except OSError:
            pass
        return len(rows)
