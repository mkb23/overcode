"""The spend ledger: what every agent spent, by billing code, kept for ever.

Every agent has a billing code (``Session.billing_code``): its launch name
unless ``overcode launch --billing-code`` set one, and always its parent's
for a child or a fork. ``overcode rebill`` changes it forward only.

``~/.overcode/ledger/<YYYY-MM>.jsonl`` holds one row per spend event,
named by write time (a closed month is gzipped). A row carries the agent's
*cumulative* own spend so far, never a delta, so a repeated or late row is
harmless: readers keep the last row of each agent's segment. Rows::

    {"t", "seq", "sid", "name", "parent", "billing_code", "seg", "model",
     "cost", "tokens", "tok_in", "tok_out", "tok_cache",
     "base_cost", "base_tokens", "kind"}

``kind`` is ``checkpoint`` (a running agent, at most every
``ledger.checkpoint_minutes``), ``final`` (written with its archive
record), ``close`` / ``open`` (a rebill: the old code's last row, the new
code's first) or ``backfill`` (an agent archived before the ledger).
``seg`` numbers an agent's billing segments: 0 at launch, one more per
rebill. A segment's spend is its last row's cumulative values less its
``base_*`` (the cumulative values when the segment began), so spend before
a rebill stays on the old code.

``ledger/totals.json`` folds the rows incrementally: per month, how far
they were read; the last row of every open segment; per-code totals of
closed ones (a ``final`` or ``close`` row closes a segment). A closed
segment's contribution is remembered for a while (CLOSED_KEEP_DAYS), so a
duplicate final row (a crash between the archive and the live file) is
not counted twice. A report for a period (``--since``, ``--month``) reads
the rows instead: a segment's spend in the period is its last cumulative
value by the end less its last one before the start.
"""

from __future__ import annotations

import gzip
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

try:
    import fcntl
    HAS_FCNTL = True
except ImportError:  # pragma: no cover - Windows
    HAS_FCNTL = False

logger = logging.getLogger(__name__)

PLAIN_SUFFIX = ".jsonl"
GZIP_SUFFIX = ".jsonl.gz"
TOTALS_VERSION = 1
# How long a closed segment's contribution is remembered, to replace (not
# add) a duplicate or revised final row for it
CLOSED_KEEP_DAYS = 62
# The numbers a row carries, cumulative, and what totals sum
AMOUNTS = ("cost", "tokens", "tok_in", "tok_out", "tok_cache")


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


def make_row(record, kind: str, *, now: Optional[datetime] = None,
             billing_code: Optional[str] = None, seg: Optional[int] = None,
             base: Optional[Dict[str, float]] = None, t: Optional[str] = None) -> dict:
    """The ledger row for an agent's record (a sessions.json dict or a Session)."""
    now = now or datetime.now()
    spend = spend_of(record)
    if base is None:
        base = {"cost": _get(record, "billing_base_cost", 0.0) or 0.0,
                "tokens": _get(record, "billing_base_tokens", 0) or 0}
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
        "base_cost": float(base.get("cost", 0.0) or 0.0),
        "base_tokens": int(base.get("tokens", 0) or 0),
        "kind": kind,
    }


def contribution(row: dict) -> Dict[str, float]:
    """What a segment's (last) row adds to its code: cumulative less the segment's base."""
    out = {key: row.get(key, 0) or 0 for key in AMOUNTS}
    out["cost"] = max(0.0, float(out["cost"]) - float(row.get("base_cost", 0) or 0))
    out["tokens"] = max(0, int(out["tokens"]) - int(row.get("base_tokens", 0) or 0))
    if row.get("seg"):
        # The breakdown is not split at a rebill: only cost and tokens are
        out["tok_in"] = out["tok_out"] = out["tok_cache"] = 0
    return out


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
        """Append rows to this month's file under the ledger lock; one fsync."""
        rows = [r for r in rows if r.get("sid")]
        if not rows:
            return
        month = (now or datetime.now()).strftime("%Y-%m")
        with self._lock():
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
        """Gzip every plain month before ``current`` (caller holds the lock)."""
        for month, path in self.months():
            plain = self.dir / f"{month}{PLAIN_SUFFIX}"
            if month >= current or not plain.exists():
                continue
            target = self.dir / f"{month}{GZIP_SUFFIX}"
            try:
                if not target.exists():
                    tmp = target.with_name(target.name + ".tmp")
                    with open(plain, "rb") as src, gzip.open(tmp, "wb") as dst:
                        while chunk := src.read(1 << 20):
                            dst.write(chunk)
                    os.replace(tmp, target)
                plain.unlink()
            except OSError as e:
                logger.warning("could not gzip ledger month %s: %s", month, e)

    @staticmethod
    def _rows_of(path: Path, offset: int = 0) -> Tuple[List[dict], int, int]:
        """(rows, end offset of the last whole line, inode) of a month file."""
        try:
            st = path.stat()
            if path.name.endswith(GZIP_SUFFIX):
                with gzip.open(path, "rb") as f:
                    data, offset = f.read(), 0
            else:
                with open(path, "rb") as f:
                    f.seek(offset)
                    data = f.read()
        except (OSError, EOFError, gzip.BadGzipFile):
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
        if key in closed:
            if int(row.get("seq", 0)) <= int(closed[key].get("seq", 0)):
                return  # older than what closed it: already counted
            # A duplicate or revised row of a closed segment: replace its share
            code, previous = closed[key]["code"], closed[key]["amounts"]
            entry = codes.setdefault(code, dict.fromkeys(AMOUNTS, 0) | {"agents": 0})
            for name in AMOUNTS:
                entry[name] = entry.get(name, 0) - previous.get(name, 0)
            entry["agents"] = entry.get("agents", 0) - 1
            del closed[key]
        last = open_.get(key)
        if last is not None and int(last.get("seq", 0)) > int(row.get("seq", 0)):
            row = last  # an older row arriving late does not move the segment back
        if row.get("kind") in ("final", "close", "backfill"):
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
            last = target.get(key)
            if last is None or int(last.get("seq", 0)) <= int(row.get("seq", 0)):
                target[key] = row
        report: Dict[str, dict] = {}
        for key, row in within.items():
            amounts = contribution(row)
            prior = before.get(key)
            if prior is not None:
                earlier = contribution(prior)
                amounts = {k: max(0, amounts[k] - earlier[k]) for k in AMOUNTS}
            code = row.get("billing_code") or "?"
            entry = report.setdefault(code, dict.fromkeys(AMOUNTS, 0) | {"agents": 0, "running": 0})
            for name in AMOUNTS:
                entry[name] += amounts[name]
            entry["agents"] += 1
            if row.get("kind") not in ("final", "close", "backfill"):
                entry["running"] += 1
        return report

    # ── backfill ────────────────────────────────────────────────────────

    def backfill_from_archive(self, session_manager) -> int:
        """Give every agent archived before the ledger a ``backfill`` row; once.

        Its billing code is its own, else its name. A marker file makes it
        run once; agents already in the ledger are skipped.
        """
        marker = self.dir / ".backfilled"
        if marker.exists():
            return 0
        known = {row.get("sid") for row in self.iter_rows()}
        rows = []
        for record in session_manager.iter_archived_records(newest_first=True):
            if record.get("id") in known:
                continue
            rows.append(make_row(record, "backfill",
                                 t=record.get("end_time") or record.get("start_time")))
        rows.reverse()
        for i in range(0, len(rows), 1000):
            self.append(rows[i:i + 1000])
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            marker.write_text(datetime.now().isoformat())
        except OSError:
            pass
        return len(rows)
