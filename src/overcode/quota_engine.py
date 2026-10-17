"""Subscription quota in the engine: ground truth, the token stream, the fit, the view.

Cost model Phase B (``docs/design/cost-model-0.6.1.md`` §6, "Phase B as
built"). The monitor daemon owns everything here; the TUI renders what it
publishes (AGENTS.md: the TUI is a view of the engine).

Data, under ``paths.usage_dir()`` (``~/.overcode/usage``)::

    <account>/<YYYY-MM>.jsonl   usage history: one line per observation
    <account>/model.json        the fitted quota model
    <account>/poll.lock         one poll at a time across daemons
    tokens/<YYYY-MM>.jsonl      the token stream: what each agent spent, when
    statusline/<agent>.jsonl    statusline samples (calibration only)
    marks-<tmux session>.json   each agent's tokens as last counted

**The token stream.** Each tick, a subscription agent's cumulative tokens
by model (``SessionStats.conversations``: deduplicated per message id by
the transcript readers, forks' copied history taken out) are compared with
its mark; the rise is held and written at most once a minute per agent as
``{"t", "sid", "name", "code", "acct", "tok": {model: {kind: n}}}`` (kinds:
``quota_model.TOKEN_KEYS``). Rows outlive their agents: billing codes read
them for ``overcode billing``. Marks persist, so a daemon restart books
the tokens spent while it was down at the restart, never twice.

**Ground truth.** The daemon polls ``GET /api/oauth/usage`` once per
account (``quota.poll_minutes``, at least 5; a 429 backs off, honouring
``Retry-After``); a fresh enough line another daemon wrote is taken
instead. A poll's line::

    {"t", "src": "api", "w7", "w5", "r7", "r5", "w7f"?, "credits"?, "surf"?,
     "plan", "x": {model: {kind: n}}}

``x`` is the fleet's tokens since the weekly window began (``r7`` − 7
days), so the calibration needs no other file. With ``quota.calibration``
on, agents overcode launches run ``overcode statusline``
(``statusline_tap``) and the daemon reads their samples: a line with
``"src": "statusline"`` is written each time the weekly figure changes,
with ``prev`` (the figure before) and ``dt`` (seconds since the last
sample that read it): an integer tick with its time bracketed.

Another ground-truth source plugs in as a ``SampleSource``: anything that
yields ``{"t", "w7", "r7", ...}`` samples; the engine stamps ``x``.

**The fit** runs at most hourly when new observations arrived, in a
thread, over ``quota.weeks`` of history (``quota_calibration``), and is
saved to ``model.json``.

**The view.** Per agent: ``{"pp7d", "pp5h", "w7", "w5", "conf"}``: its
predicted share of the weekly quota this week (its tokens since the weekly
reset) and of the current 5-hour window, the account's observed figures and
the model's confidence. It changes only when the agent's tokens, the model
or the observed figures do. The fleet's ``usage``: the latest observation,
the plan, and the attributed / unattributed split of the weekly figure.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol

from . import quota_model as qm

try:
    import fcntl
    HAS_FCNTL = True
except ImportError:  # pragma: no cover - Windows
    HAS_FCNTL = False

logger = logging.getLogger(__name__)

WEEK = 7 * 86400.0
FIVE_HOURS = 5 * 3600.0
FLUSH_SECONDS = 60.0
FIT_INTERVAL_SECONDS = 3600.0
MAX_BACKOFF_SECONDS = 3600.0
NO_TOKEN_RETRY_SECONDS = 1800.0
MARK_KEEP_SECONDS = 30 * 86400.0
STATUSLINE_MAX_FILE_AGE = 7 * 86400.0


# -- reading an agent ------------------------------------------------------

def counts_toward_quota(session: Any) -> bool:
    """A Claude Code agent on the subscription transport (not Bedrock/Vertex)."""
    from .backends import BackendCapability, session_supports

    provider = getattr(session, "provider", None) or "web"
    if provider != "web":
        return False
    try:
        return session_supports(session, BackendCapability.SUBSCRIPTION_USAGE)
    except Exception:
        return False


def tokens_by_model(stats: Any, default_model: Optional[str] = None) -> Dict[str, Dict[str, int]]:
    """An agent's cumulative tokens by model and kind, from its conversation entries."""
    entries = getattr(stats, "conversations", None)
    if not isinstance(entries, Mapping):
        return {}
    out: Dict[str, Dict[str, int]] = {}
    for entry in entries.values():
        if not isinstance(entry, Mapping):
            continue
        model = entry.get("model") or default_model or "?"
        cw, cw1h = _int(entry.get("cw")), _int(entry.get("cw1h"))
        lcw, lcw1h = _int(entry.get("lcw")), _int(entry.get("lcw1h"))
        counts = {
            "in": _int(entry.get("in")), "out": _int(entry.get("out")),
            "cw5m": max(0, cw - cw1h), "cw1h": cw1h, "cr": _int(entry.get("cr")),
            "lin": _int(entry.get("lin")), "lout": _int(entry.get("lout")),
            "lcw5m": max(0, lcw - lcw1h), "lcw1h": lcw1h, "lcr": _int(entry.get("lcr")),
        }
        slot = out.setdefault(model, {})
        for k, v in counts.items():
            if v:
                slot[k] = slot.get(k, 0) + v
    return {m: v for m, v in out.items() if v}


def token_rise(before: Mapping, after: Mapping) -> Dict[str, Dict[str, int]]:
    """What ``after`` holds beyond ``before`` (per model and kind; never negative)."""
    out: Dict[str, Dict[str, int]] = {}
    for model, toks in after.items():
        prev = before.get(model) or {}
        for k, v in toks.items():
            d = _int(v) - _int(prev.get(k))
            if d > 0:
                out.setdefault(model, {})[k] = d
    return out


# -- reading ground truth --------------------------------------------------

def _epoch(value: Any) -> Optional[float]:
    """Unix seconds from seconds, milliseconds or ISO 8601 text; None otherwise."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) / 1000.0 if value > 1e11 else float(value)
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            try:
                return _epoch(float(value))
            except ValueError:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    return None


def _pct(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _surfaces(data: Mapping) -> Optional[Dict[str, float]]:
    """A weekly breakdown by surface ({"claude_code": %, "chat": %, ...}), if any."""
    for value in data.values():
        if isinstance(value, Mapping) and "claude_code" in value:
            out = {}
            for name, v in value.items():
                pct = _pct(v.get("utilization") if isinstance(v, Mapping) else v)
                if pct is not None:
                    out[str(name)] = pct
            return out or None
    return None


def parse_usage(data: Mapping) -> Optional[dict]:
    """A history line's figures from a ``/api/oauth/usage`` response, None if unusable."""
    if not isinstance(data, Mapping):
        return None
    week = data.get("seven_day") if isinstance(data.get("seven_day"), Mapping) else {}
    five = data.get("five_hour") if isinstance(data.get("five_hour"), Mapping) else {}
    w7 = _pct(week.get("utilization"))
    if w7 is None:
        return None
    line = {"w7": w7, "r7": _epoch(week.get("resets_at")),
            "w5": _pct(five.get("utilization")), "r5": _epoch(five.get("resets_at"))}
    for key in ("seven_day_fable", "seven_day_opus"):
        value = data.get(key)
        if isinstance(value, Mapping) and _pct(value.get("utilization")) is not None:
            line["w7f"] = _pct(value.get("utilization"))
            break
    extra = data.get("extra_usage")
    if isinstance(extra, Mapping):
        credits = extra.get("used_credits")
        if isinstance(credits, (int, float)) and not isinstance(credits, bool):
            line["credits"] = float(credits)
    surf = _surfaces(data)
    if surf:
        line["surf"] = surf
    return line


def parse_statusline(sample: Mapping) -> Optional[dict]:
    """The weekly / 5-hour figures of one statusline sample, None without them."""
    rl = sample.get("rl") if isinstance(sample, Mapping) else None
    if not isinstance(rl, Mapping):
        return None
    week = rl.get("seven_day") if isinstance(rl.get("seven_day"), Mapping) else {}
    five = rl.get("five_hour") if isinstance(rl.get("five_hour"), Mapping) else {}
    w7 = _pct(week.get("used_percentage", week.get("utilization")))
    t = sample.get("t")
    if w7 is None or not isinstance(t, (int, float)):
        return None
    return {"t": float(t), "w7": w7, "r7": _epoch(week.get("resets_at")),
            "w5": _pct(five.get("used_percentage", five.get("utilization"))),
            "r5": _epoch(five.get("resets_at"))}


class SampleSource(Protocol):
    """A finer ground-truth source (e.g. a future header feed): new samples
    since the last call, each ``{"t", "w7", "r7", "w5"?, "r5"?, "src"}``."""

    def poll(self, now: float) -> Iterable[dict]: ...


# -- files -------------------------------------------------------------------

def _month(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%Y-%m")


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)[:80] or "default"


def _oauth_account() -> dict:
    """Claude Code's ``oauthAccount`` record from ``.claude.json`` (no secrets), or {}."""
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    path = Path(base) / ".claude.json" if base else Path.home() / ".claude.json"
    try:
        account = json.loads(path.read_text()).get("oauthAccount")
    except (OSError, ValueError, AttributeError):
        return {}
    return account if isinstance(account, dict) else {}


def default_account() -> str:
    """This machine's Claude login: its account uuid (first 12 characters)
    from Claude Code's ``.claude.json``, else "default"."""
    uuid = _oauth_account().get("accountUuid")
    if isinstance(uuid, str) and uuid:
        return _safe(uuid[:12])
    return "default"


def account_plan() -> Optional[str]:
    """The plan from ``.claude.json``'s account record, when the credentials
    don't say: seen on 2.1.302 as ``organizationType: "claude_max"`` and
    ``organizationRateLimitTier: "default_claude_max_20x"``."""
    account = _oauth_account()
    tier = account.get("userRateLimitTier") or account.get("organizationRateLimitTier")
    kind = account.get("organizationType")
    kind = kind.removeprefix("claude_") if isinstance(kind, str) else None
    return qm.plan_from_credentials(kind, tier if isinstance(tier, str) else None)


class UsageStore:
    """The files under the usage directory (see the module docstring)."""

    def __init__(self, root: Optional[Path] = None):
        from . import paths
        self.root = Path(root) if root is not None else paths.usage_dir()

    # history
    def account_dir(self, account: str) -> Path:
        return self.root / _safe(account)

    def append_history(self, account: str, line: dict) -> None:
        path = self.account_dir(account) / f"{_month(line['t'])}.jsonl"
        _append_lines(path, [line])

    def read_history(self, account: str, since: float = 0.0) -> List[dict]:
        rows: List[dict] = []
        for path in _month_files(self.account_dir(account), since):
            rows.extend(r for r in _read_jsonl(path) if _t(r) >= since)
        rows.sort(key=_t)
        return rows

    def last_history(self, account: str) -> Optional[dict]:
        files = _month_files(self.account_dir(account), 0.0)
        for path in reversed(files):
            line = _last_line(path)
            if line is not None:
                return line
        return None

    def model_path(self, account: str) -> Path:
        return self.account_dir(account) / "model.json"

    def load_model(self, account: str) -> Optional[qm.QuotaModel]:
        try:
            return qm.QuotaModel.from_dict(json.loads(self.model_path(account).read_text()))
        except (OSError, ValueError):
            return None

    def save_model(self, account: str, model: qm.QuotaModel) -> None:
        _write_json(self.model_path(account), model.to_dict())

    def poll_lock(self, account: str) -> Path:
        return self.account_dir(account) / "poll.lock"

    # token stream
    @property
    def tokens_dir(self) -> Path:
        return self.root / "tokens"

    def append_tokens(self, rows: List[dict]) -> None:
        if rows:
            _append_lines(self.tokens_dir / f"{_month(rows[0]['t'])}.jsonl", rows)

    def iter_tokens(self, since: float = 0.0, until: Optional[float] = None) -> Iterable[dict]:
        for path in _month_files(self.tokens_dir, since):
            for row in _read_jsonl(path):
                t = _t(row)
                if t >= since and (until is None or t < until):
                    yield row

    def tail_tokens(self, offsets: Dict[str, int], since: float) -> List[dict]:
        """Rows appended since ``offsets`` (updated in place), of months from ``since`` on."""
        rows: List[dict] = []
        for path in _month_files(self.tokens_dir, since):
            got, offsets[path.name] = _read_from(path, offsets.get(path.name, 0))
            rows.extend(got)
        return rows

    # statusline samples
    @property
    def statusline_dir(self) -> Path:
        return self.root / "statusline"

    def tail_statusline(self, offsets: Dict[str, int], now: float) -> List[dict]:
        rows: List[dict] = []
        try:
            entries = list(os.scandir(self.statusline_dir))
        except OSError:
            return rows
        for entry in entries:
            if not entry.name.endswith(".jsonl"):
                continue
            try:
                if time.time() - entry.stat().st_mtime > STATUSLINE_MAX_FILE_AGE:
                    continue
            except OSError:
                continue
            got, offsets[entry.name] = _read_from(Path(entry.path), offsets.get(entry.name, 0))
            rows.extend(got)
        rows.sort(key=_t)
        return rows

    # marks
    def marks_path(self, tmux_session: str) -> Path:
        return self.root / f"marks-{_safe(tmux_session)}.json"

    def load_marks(self, tmux_session: str) -> dict:
        try:
            data = json.loads(self.marks_path(tmux_session).read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_marks(self, tmux_session: str, data: dict) -> None:
        _write_json(self.marks_path(tmux_session), data)


def _t(row: Mapping) -> float:
    t = row.get("t") if isinstance(row, Mapping) else None
    return float(t) if isinstance(t, (int, float)) else 0.0


def _month_files(directory: Path, since: float) -> List[Path]:
    try:
        names = sorted(n for n in os.listdir(directory) if re.fullmatch(r"\d{4}-\d{2}\.jsonl", n))
    except OSError:
        return []
    first = _month(since) if since > 0 else ""
    return [directory / n for n in names if n[:7] >= first]


def _read_jsonl(path: Path) -> List[dict]:
    rows, _ = _read_from(path, 0)
    return rows


def _read_from(path: Path, offset: int):
    """Whole lines from ``offset``: (rows, the offset after the last whole line)."""
    try:
        with open(path, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            if offset > size:
                offset = 0  # replaced or truncated
            f.seek(offset)
            data = f.read()
    except OSError:
        return [], offset
    end = data.rfind(b"\n")
    if end < 0:
        return [], offset
    rows = []
    for raw in data[: end + 1].splitlines():
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows, offset + end + 1


def _last_line(path: Path) -> Optional[dict]:
    try:
        with open(path, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            f.seek(max(0, size - 65536))
            data = f.read()
    except OSError:
        return None
    for raw in reversed(data.splitlines()):
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict):
            return row
    return None


def _append_lines(path: Path, rows: List[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = "".join(json.dumps(r, separators=(",", ":"), sort_keys=True) + "\n" for r in rows)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        if HAS_FCNTL:
            fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, data.encode())
    finally:
        os.close(fd)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":"), sort_keys=True))
    os.replace(tmp, path)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# -- fetching --------------------------------------------------------------

def default_fetch() -> dict:
    """One usage poll: ``{"line"?, "plan"?, "error"?, "retry_after"?, "no_token"?}``."""
    from .usage_monitor import UsageMonitor

    if os.environ.get("OVERCODE_NO_USAGE_FETCH") == "1":  # the test suites set it
        return {"error": "no token", "no_token": True}
    creds = UsageMonitor.get_oauth_credentials()
    if not creds:
        return {"error": "no token", "no_token": True}
    plan = (qm.plan_from_credentials(creds.get("subscriptionType"), creds.get("rateLimitTier"))
            or account_plan())
    data, error, retry = UsageMonitor.fetch_usage_json(creds["accessToken"])
    if error:
        return {"error": error, "retry_after": retry, "plan": plan,
                "rate_limited": error == "HTTP 429"}
    line = parse_usage(data or {})
    if line is None:
        return {"error": "no weekly figure in the response", "plan": plan}
    return {"line": line, "plan": plan}


# -- the engine component ------------------------------------------------------

class QuotaEngine:
    """One daemon's share of the quota work (see the module docstring)."""

    def __init__(self, tmux_session: str, *, store: Optional[UsageStore] = None,
                 fetch: Optional[Callable[[], dict]] = None,
                 account: Optional[Callable[[], str]] = None,
                 config: Optional[Callable[[], dict]] = None,
                 sources: Iterable[SampleSource] = (),
                 price_fn: Optional[qm.PriceFn] = None,
                 threaded: bool = True, log=None):
        self.tmux_session = tmux_session
        self.store = store or UsageStore()
        self._fetch = fetch or default_fetch
        self._account_fn = account or default_account
        self._config_fn = config
        self.sources = list(sources)
        self.price_fn = price_fn
        self.threaded = threaded
        self.log = log
        self.account: Optional[str] = None
        self._account_checked = 0.0
        # tokens
        marks = self.store.load_marks(tmux_session)
        self.marks_created_at: Optional[float] = marks.get("created_at")
        self.marks: Dict[str, dict] = marks.get("marks") or {}
        self.mark_seen: Dict[str, float] = marks.get("seen") or {}
        self.sl_offsets: Dict[str, int] = marks.get("statusline") or {}
        sl = marks.get("sl") or [None, None]
        self.sl_last = (sl[0], sl[1]) if isinstance(sl, list) and len(sl) == 2 else (None, None)
        self._stamps: Dict[str, Any] = {}
        self.pending: Dict[str, dict] = {}
        self._marks_dirty = False
        # windows and totals
        self.start7: Optional[float] = None
        self.start5: Optional[float] = None
        self._tail_offsets: Dict[str, int] = {}
        self.tot7: Dict[str, dict] = {}
        self.tot5: Dict[str, dict] = {}
        self.fleet7: Dict[str, dict] = {}
        self.fleet5: Dict[str, dict] = {}
        self._tokens_version = 0
        # ground truth
        self.latest: Optional[dict] = None
        self.plan: Optional[str] = None
        self.no_token = False
        self.last_error: Optional[str] = None
        self.next_poll_at = 0.0
        self._backoff = 0.0
        self._poll_thread: Optional[threading.Thread] = None
        self._poll_result: Optional[dict] = None
        # model
        self.model: Optional[qm.QuotaModel] = None
        self._model_version = 0
        self.last_fit_at = 0.0
        self._obs_since_fit = 0
        self._fit_thread: Optional[threading.Thread] = None
        self._fit_result: Optional[qm.QuotaModel] = None
        # published
        self.views: Dict[str, dict] = {}
        self.fleet_usage: Optional[dict] = None
        self._view_keys: Dict[str, tuple] = {}

    # -- config / account -------------------------------------------------
    def config(self) -> dict:
        if self._config_fn is not None:
            return self._config_fn()
        from .config import get_quota_config
        return get_quota_config()

    def _refresh_account(self, now: float) -> None:
        if self.account is not None and now - self._account_checked < 3600:
            return
        self._account_checked = now
        try:
            account = self._account_fn()
        except Exception:
            account = "default"
        if account != self.account:
            self.account = account
            self.model = self.store.load_model(account)
            self.last_fit_at = (self.model.fitted_at or 0.0) if self.model else 0.0
            if self.model is None or not self.model.fitted:
                self._obs_since_fit = max(self._obs_since_fit, 1)  # try one on what is kept
            self._model_version += 1
            last = self.store.last_history(account)
            if last is not None and last.get("w7") is not None:
                self._adopt(last)

    # -- one tick -----------------------------------------------------------
    def sync(self, sessions: Iterable[Any], now: Optional[float] = None) -> None:
        """One tick's work; never raises (a quota problem must not stop the tick)."""
        now = time.time() if now is None else now
        try:
            cfg = self.config()
            if not cfg.get("enabled", True):
                self.views, self.fleet_usage = {}, None
                return
            sessions = list(sessions)
            self._refresh_account(now)
            self.observe_tokens(sessions, now)
            self.flush(now)
            self._tail_tokens()
            self._collect_poll(now)
            self._maybe_poll(now, cfg)
            for source in self.sources:
                for sample in source.poll(now) or ():
                    self._record_sample(dict(sample), now)
            if cfg.get("calibration"):
                self._ingest_statusline(now)
            self._collect_fit()
            self._maybe_fit(now, cfg)
            self._publish(sessions, cfg)
            if self._marks_dirty:
                self._save_marks()
        except Exception as e:  # pragma: no cover - defensive
            if self.log is not None:
                self.log.warn(f"quota: {e}")
            else:
                logger.warning("quota: %s", e)

    # -- tokens --------------------------------------------------------------
    def observe_tokens(self, sessions: List[Any], now: float) -> None:
        if self.marks_created_at is None:
            self.marks_created_at = now
            self._marks_dirty = True
        for session in sessions:
            if not counts_toward_quota(session):
                continue
            sid = session.id
            self.mark_seen[sid] = now
            stats = session.stats
            stamp = (getattr(stats, "last_stats_update", None), id(getattr(stats, "conversations", None)))
            if self._stamps.get(sid) == stamp and sid in self.marks:
                continue
            self._stamps[sid] = stamp
            current = tokens_by_model(stats, getattr(session, "model", None))
            before = self.marks.get(sid)
            if before is None:
                # An agent from before counting began: its past is not this week's news
                started = _epoch_local(getattr(session, "start_time", None))
                before = current if (started is None or started < self.marks_created_at) else {}
            rise = token_rise(before, current)
            self.marks[sid] = current
            self._marks_dirty = True
            if not rise:
                continue
            slot = self.pending.setdefault(sid, {"since": now, "tok": {}})
            qm.add_tokens(slot["tok"], rise)
            slot["name"] = getattr(session, "name", "")
            from .ledger import billing_code_of
            slot["code"] = billing_code_of(session)
            self._tokens_version += 1

    def flush(self, now: float, force: bool = False) -> None:
        rows = []
        for sid, slot in list(self.pending.items()):
            if force or now - slot["since"] >= FLUSH_SECONDS:
                rows.append({"t": now, "sid": sid, "name": slot.get("name", ""),
                             "code": slot.get("code", ""), "acct": self.account,
                             "tok": slot["tok"]})
                del self.pending[sid]
        if rows:
            self.store.append_tokens(rows)
            self._save_marks()

    def _save_marks(self) -> None:
        cutoff = max(self.mark_seen.values(), default=0.0) - MARK_KEEP_SECONDS
        for sid in [s for s, t in self.mark_seen.items() if t < cutoff]:
            self.mark_seen.pop(sid, None)
            self.marks.pop(sid, None)
        try:
            self.store.save_marks(self.tmux_session, {
                "created_at": self.marks_created_at, "marks": self.marks, "seen": self.mark_seen,
                "statusline": self.sl_offsets, "sl": list(self.sl_last),
            })
            self._marks_dirty = False
        except OSError as e:
            logger.warning("quota marks not saved: %s", e)

    def _apply_row(self, row: Mapping) -> None:
        if self.account and row.get("acct") not in (None, self.account):
            return
        t = _t(row)
        tok = row.get("tok")
        if not isinstance(tok, Mapping):
            return
        sid = row.get("sid") or "?"
        if self.start7 is not None and t >= self.start7:
            qm.add_tokens(self.tot7.setdefault(sid, {}), tok)
            qm.add_tokens(self.fleet7, tok)
        if self.start5 is not None and t >= self.start5:
            qm.add_tokens(self.tot5.setdefault(sid, {}), tok)
            qm.add_tokens(self.fleet5, tok)
        self._tokens_version += 1

    def _tail_tokens(self) -> None:
        if self.start7 is None:
            return
        for row in self.store.tail_tokens(self._tail_offsets, min(self.start7, self.start5 or self.start7)):
            self._apply_row(row)

    def _set_windows(self, r7: Optional[float], r5: Optional[float]) -> None:
        start7 = r7 - WEEK if isinstance(r7, (int, float)) else None
        start5 = r5 - FIVE_HOURS if isinstance(r5, (int, float)) else None
        if start7 is None:
            return
        # The API's stamp jitters by seconds: only a real move rebuilds
        moved7 = self.start7 is None or abs(start7 - self.start7) > 600
        moved5 = start5 is not None and (self.start5 is None or abs(start5 - self.start5) > 600)
        if not moved7 and not moved5:
            return
        if moved7:
            self.start7 = start7
        if moved5:
            self.start5 = start5
        self.tot7, self.tot5, self.fleet7, self.fleet5 = {}, {}, {}, {}
        self._tail_offsets = {}
        self._tokens_version += 1
        self._tail_tokens()

    def _with_pending(self, base: Mapping, sid: Optional[str] = None) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        qm.add_tokens(out, base)
        for key, slot in self.pending.items():
            if sid is None or key == sid:
                qm.add_tokens(out, slot["tok"])
        return out

    # -- ground truth ---------------------------------------------------------
    def _adopt(self, line: Mapping) -> None:
        """Take ``line`` as the latest observation (if it is newer)."""
        if self.latest is not None and _t(line) < _t(self.latest):
            return
        self.latest = dict(line)
        self.latest.pop("x", None)
        if line.get("plan"):
            self.plan = line["plan"]
        self._set_windows(line.get("r7"), line.get("r5"))

    def _maybe_poll(self, now: float, cfg: Mapping) -> None:
        if now < self.next_poll_at or (self._poll_thread is not None and self._poll_thread.is_alive()):
            return
        interval = float(cfg.get("poll_minutes", 5)) * 60.0
        self.next_poll_at = now + interval
        account = self.account or "default"
        last = self.store.last_history(account)
        if last is not None and last.get("src") == "api" and now - _t(last) < interval:
            self._adopt(last)  # another daemon polled
            return
        if self.threaded:
            self._poll_thread = threading.Thread(target=self._poll_worker,
                                                 args=(account, interval, now),
                                                 name="quota-poll", daemon=True)
            self._poll_thread.start()
        else:
            self._poll_worker(account, interval, now)
            self._collect_poll(now)

    def _poll_worker(self, account: str, interval: float, now: float) -> None:
        lock_fd = None
        try:
            lock_path = self.store.poll_lock(account)
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o644)
            if HAS_FCNTL:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
            last = self.store.last_history(account)
            if last is not None and last.get("src") == "api" and now - _t(last) < interval:
                self._poll_result = {"adopt": last}
                return
            self._poll_result = self._fetch()
        except Exception as e:
            self._poll_result = {"error": str(e)}
        finally:
            if lock_fd is not None:
                os.close(lock_fd)

    def _collect_poll(self, now: float) -> None:
        result, self._poll_result = self._poll_result, None
        if not result:
            return
        if "adopt" in result:
            self._adopt(result["adopt"])
            return
        if result.get("plan"):
            self.plan = result["plan"]
        if result.get("no_token"):
            self.no_token = True
            self.latest = None
            self.next_poll_at = now + NO_TOKEN_RETRY_SECONDS
            return
        self.no_token = False
        if result.get("error"):
            self.last_error = result["error"]
            if result.get("rate_limited"):
                self._backoff = min(MAX_BACKOFF_SECONDS, max(300.0, self._backoff * 2 or 600.0))
                wait = max(self._backoff, float(result.get("retry_after") or 0.0))
                self.next_poll_at = now + wait
            return
        self._backoff = 0.0
        self.last_error = None
        line = dict(result["line"], t=now, src="api")
        self._record_sample(line, now)

    def _record_sample(self, line: dict, now: float) -> None:
        """Stamp ``line`` with the plan and the fleet's tokens, keep it, adopt it."""
        line.setdefault("t", now)
        line.setdefault("src", "other")
        line["plan"] = self._plan()
        self._set_windows(line.get("r7"), line.get("r5"))
        if self.start7 is not None:
            line["x"] = self._with_pending(self.fleet7)
        try:
            self.store.append_history(self.account or "default", line)
        except OSError as e:
            logger.warning("quota history not written: %s", e)
        self._obs_since_fit += 1
        self._adopt(line)

    def _ingest_statusline(self, now: float) -> None:
        for sample in self.store.tail_statusline(self.sl_offsets, now):
            parsed = parse_statusline(sample)
            if parsed is None:
                continue
            last_value, last_t = self.sl_last
            value, t = parsed["w7"], parsed["t"]
            if value == last_value:
                self.sl_last = (value, t)
                self._marks_dirty = True
                continue
            line = dict(parsed, src="statusline")
            if last_value is not None:
                line["prev"] = last_value
                if isinstance(last_t, (int, float)):
                    line["dt"] = round(t - last_t, 1)
            self.sl_last = (value, t)
            self._marks_dirty = True
            # Kept even when a poll is newer (readers sort by time); only
            # adopted as the latest reading if it is the newest
            self._record_sample(line, now)

    def _plan(self) -> Optional[str]:
        cfg_plan = None
        try:
            cfg_plan = self.config().get("plan")
        except Exception:
            pass
        return cfg_plan or self.plan

    # -- the model ----------------------------------------------------------------
    def prior(self, cfg: Optional[Mapping] = None) -> qm.QuotaModel:
        """The prior for the plan and budget in force (one object while they hold)."""
        cfg = cfg if cfg is not None else self.config()
        key = (cfg.get("plan") or self.plan, cfg.get("weekly_usd"))
        cached = getattr(self, "_prior_cache", None)
        if cached is None or cached[0] != key:
            cached = (key, qm.prior_model(*key))
            self._prior_cache = cached
        return cached[1]

    def current_model(self, cfg: Optional[Mapping] = None) -> qm.QuotaModel:
        prior = self.prior(cfg)
        model = self.model
        if model is not None and model.prior == prior.prior:
            return model
        return prior

    def _maybe_fit(self, now: float, cfg: Mapping) -> None:
        if self._fit_thread is not None and self._fit_thread.is_alive():
            return
        if self._obs_since_fit <= 0 or now - self.last_fit_at < FIT_INTERVAL_SECONDS:
            return
        self.last_fit_at = now
        self._obs_since_fit = 0
        args = (self.account or "default", now, dict(cfg))
        if self.threaded:
            self._fit_thread = threading.Thread(target=self._fit_worker, args=args,
                                                name="quota-fit", daemon=True)
            self._fit_thread.start()
        else:
            self._fit_worker(*args)
            self._collect_fit()

    def _fit_worker(self, account: str, now: float, cfg: dict) -> None:
        try:
            self._fit_result = fit_account(self.store, account, self.prior(cfg), now=now,
                                           weeks=cfg.get("weeks", 4), breaks=cfg.get("breaks", ()),
                                           price_fn=self.price_fn)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("quota fit failed: %s", e)

    def _collect_fit(self) -> None:
        model, self._fit_result = self._fit_result, None
        if model is None:
            return
        self.model = model
        self._model_version += 1
        try:
            self.store.save_model(self.account or "default", model)
        except OSError as e:
            logger.warning("quota model not saved: %s", e)

    # -- publishing ---------------------------------------------------------------
    def _publish(self, sessions: List[Any], cfg: Mapping) -> None:
        latest = self.latest
        if latest is None or self.no_token:
            self.views, self.fleet_usage = {}, None
            return
        model = self.current_model(cfg)
        plan = self._plan()
        views: Dict[str, dict] = {}
        for session in sessions:
            if not counts_toward_quota(session):
                continue
            sid = session.id
            key = (self._tokens_version, self._model_version, id(model),
                   latest.get("w7"), latest.get("w5"), plan)
            if self._view_keys.get(sid) == key and sid in self.views:
                views[sid] = self.views[sid]
                continue
            self._view_keys[sid] = key
            pp7 = model.predict_tokens(self._with_pending(self.tot7.get(sid, {}), sid), self.price_fn)
            pp5 = model.predict_tokens(self._with_pending(self.tot5.get(sid, {}), sid), self.price_fn)
            view = {"pp7d": round(pp7, 2), "pp5h": round(pp5 * model.five_hour_ratio, 2),
                    "w7": latest.get("w7"), "w5": latest.get("w5"), "conf": model.confidence}
            old = self.views.get(sid)
            views[sid] = old if old == view else view
        self.views = views
        attributed = model.predict_tokens(self._with_pending(self.fleet7), self.price_fn)
        w7 = latest.get("w7")
        usage = {
            "w7": w7, "w5": latest.get("w5"), "r7": latest.get("r7"), "r5": latest.get("r5"),
            "w7f": latest.get("w7f"), "fetched_at": _t(latest), "src": latest.get("src"),
            "plan": plan, "conf": model.confidence,
            "pp_attributed": round(attributed, 2),
            "pp_unattributed": (round(w7 - attributed, 2) if isinstance(w7, (int, float)) else None),
            "error": self.last_error,
        }
        if usage != self.fleet_usage:
            self.fleet_usage = usage

    def agent_view(self, session_id: str) -> Optional[dict]:
        return self.views.get(session_id)


def _epoch_local(value: Any) -> Optional[float]:
    if not value:
        return None
    try:
        from .clock import to_epoch
        return to_epoch(value)
    except Exception:
        return None


# -- the fit, as a function (the daemon's thread and `overcode usage model --refit`) --

def fit_account(store: UsageStore, account: str, prior: qm.QuotaModel, *, now: float,
                weeks: int = 4, breaks=(), price_fn: Optional[qm.PriceFn] = None) -> qm.QuotaModel:
    """Fit the model on ``account``'s last ``weeks`` of history."""
    from .quota_calibration import fit, five_hour_ratio, observations, thin

    lines = store.read_history(account, since=now - weeks * WEEK)
    obs, report = observations(lines, price_fn=price_fn, breaks=breaks)
    model = fit(thin(obs), prior, now=now, report=report)
    model.fitted_at = now
    ratio, n = five_hour_ratio(lines)
    model.five_hour_ratio = round(ratio, 4)
    model.five_hour_n = n
    return model


# -- reports (`overcode billing`, `overcode usage model`, doctor) ------------------

def quota_by_code(store: UsageStore, model: qm.QuotaModel, *, since: float,
                  until: Optional[float] = None, account: Optional[str] = None,
                  price_fn: Optional[qm.PriceFn] = None) -> Dict[str, float]:
    """Predicted weekly-quota points per billing code from the token stream rows in [since, until)."""
    tokens: Dict[str, Dict[str, dict]] = {}
    for row in store.iter_tokens(since, until):
        if account and row.get("acct") not in (None, account):
            continue
        tok = row.get("tok")
        if isinstance(tok, Mapping):
            qm.add_tokens(tokens.setdefault(row.get("code") or "?", {}), tok)
    return {code: model.predict_tokens(tok, price_fn) for code, tok in tokens.items()}


def window_start(latest: Optional[Mapping], now: float) -> float:
    """The weekly window's start from an observation's reset, else a week ago."""
    r7 = latest.get("r7") if isinstance(latest, Mapping) else None
    if isinstance(r7, (int, float)) and r7 > now - WEEK:
        return float(r7) - WEEK
    return now - WEEK


def model_in_use(store: UsageStore, account: str, cfg: Mapping,
                 plan: Optional[str] = None) -> qm.QuotaModel:
    """The saved fit when it was fitted against today's prior, else the prior."""
    prior = qm.prior_model(cfg.get("plan") or plan, cfg.get("weekly_usd"))
    saved = store.load_model(account)
    return saved if saved is not None and saved.prior == prior.prior else prior


def quota_status(store: UsageStore, account: str, cfg: Mapping, *, now: float,
                 price_fn: Optional[qm.PriceFn] = None) -> dict:
    """Everything ``overcode usage model``, ``overcode billing`` and doctor say about the quota.

    Read from the files the engine writes (no network): the latest reading,
    the model in use, this week's predicted points per billing code and the
    unattributed rest, and the sample counts behind the fit.
    """
    latest = store.last_history(account)
    plan = cfg.get("plan") or (latest or {}).get("plan")
    model = model_in_use(store, account, cfg, plan)
    start = window_start(latest, now)
    codes = quota_by_code(store, model, since=start, account=account, price_fn=price_fn)
    attributed = sum(codes.values())
    counts = {"api": 0, "statusline": 0, "other": 0}
    for line in store.read_history(account, since=now - cfg.get("weeks", 4) * WEEK):
        src = line.get("src")
        counts[src if src in counts else "other"] += 1
    observed = None
    if latest is not None:
        from .quota_calibration import observed_value
        observed = observed_value(latest)
    return {
        "account": account,
        "plan": plan,
        "prior_weekly_usd": qm.weekly_budget_usd(plan, cfg.get("weekly_usd")),
        "model": model,
        "latest": {k: v for k, v in latest.items() if k != "x"} if latest else None,
        "week_start": start,
        "codes": codes,
        "attributed": attributed,
        "observed": observed,
        "unattributed": (observed - attributed) if isinstance(observed, (int, float)) else None,
        "samples": counts,
    }
