"""hermes stats: the SQLite ``sessions``/``messages`` tables behind ``StatsReader``.

Hermes keeps everything overcode needs for the token/cost/context columns in
``$HERMES_HOME/state.db`` (WAL mode, one writer, many readers — the same
shape as opencode's store, and read the same failure-tolerant way).

The schema below was read off a live v0.21.3 database (2026-09-17) and
matches ``hermes_state_common.SCHEMA_SQL`` in the Hermes tree:

    sessions(id, source, user_id, model, model_config, parent_session_id,
             started_at, ended_at, end_reason, message_count,
             tool_call_count, input_tokens, output_tokens,
             cache_read_tokens, cache_write_tokens, reasoning_tokens,
             cwd, git_branch, git_repo_root, billing_provider, billing_mode,
             estimated_cost_usd, actual_cost_usd, cost_status, cost_source,
             title, api_call_count, ...)
    messages(id, session_id, role, content, tool_name, timestamp, ...)

Load-bearing facts, all verified live:

* ``sessions.*_tokens`` are **cumulative per session** (the row is updated
  after every API call), so the reader sums rows, never messages.
* ``messages`` carries **no per-message token usage** (its ``token_count``
  column was NULL on every row observed), so the burn-rate window can't be
  rebuilt from history the way opencode's is — the reader samples the
  session totals over time instead (see ``_WindowSampler``).
* The live context size is ``model_config._usage_anchor.prompt_tokens`` —
  the prompt size of the most recent API call, which is exactly the number
  Hermes's own status bar shows ("12.7K/400K").
* ``estimated_cost_usd`` is 0.0 with ``cost_status = "unknown"`` for
  providers Hermes has no price list for (``openai-api`` at verification),
  so ``get_stored_cost`` answers None unless the status says otherwise and
  the figure is non-zero — the caller then prices real token counts through
  ``pricing.py`` as it does for codex.
* ``cwd`` is written when a session is *finalized*, not at start — a
  running session can carry NULL — so directory matching is only the
  plugin-less fallback; the plugin's SessionStart is the primary id source.
* Session ids look like ``20260917_131721_8f80ea`` (``hermes_state_ids.
  new_session_id``: ``%Y%m%d_%H%M%S`` + ``_`` + hex).
"""

import json
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..stats_reader import (
    AgentSessionStats,
    DiscoveredSessionIds,
    empty_window_usage,
)

# Columns the reader reads by name. Anything missing here is schema drift —
# `schema_findings()` turns that into a doctor warning and `get_stats` returns
# None rather than half-populated numbers.
EXPECTED_SESSION_COLUMNS: Tuple[str, ...] = (
    "id",
    "source",
    "cwd",
    "model",
    "model_config",
    "parent_session_id",
    "started_at",
    "ended_at",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "estimated_cost_usd",
    "cost_status",
)

EXPECTED_MESSAGE_COLUMNS: Tuple[str, ...] = (
    "session_id",
    "role",
    "timestamp",
)

# Keeps the reader from adopting a Claude UUID left over on a rebadged session.
SESSION_ID_RE = re.compile(r"^\d{8}_\d{6}_[0-9a-f]{4,}$")

# Only CLI-sourced sessions are overcode's — never a gateway/cron/batch one
# that happens to share the directory.
CLI_SOURCES: Tuple[str, ...] = ("cli",)

# Messages scanned per session for interaction counts / work times.
_MESSAGE_SCAN_LIMIT = 500

_BUSY_TIMEOUT_MS = 300
_CONNECT_TIMEOUT_SECONDS = 0.5

# Window sampler bounds: one sample per get_window_token_usage call (a daemon
# tick), kept for at most this long.
_SAMPLE_MAX_AGE_SECONDS = 2 * 60 * 60
_SAMPLE_MAX_COUNT = 2000


def database_path() -> Path:
    from .hermes import state_db_path
    return state_db_path()


def connect(path: Optional[Path] = None) -> Optional[sqlite3.Connection]:
    """Read-only connection, or None when the store isn't there/usable."""
    db = path or database_path()
    if not db.exists():
        return None
    try:
        conn = sqlite3.connect(
            f"file:{db}?mode=ro", uri=True, timeout=_CONNECT_TIMEOUT_SECONDS
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        return conn
    except sqlite3.Error:
        return None


def _table_columns(conn: sqlite3.Connection, table: str) -> Tuple[str, ...]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.Error:
        return ()
    return tuple(row[1] for row in rows)


def missing_columns(conn: sqlite3.Connection) -> Dict[str, List[str]]:
    """Expected columns the live schema lacks, keyed by table."""
    out: Dict[str, List[str]] = {}
    for table, expected in (
        ("sessions", EXPECTED_SESSION_COLUMNS),
        ("messages", EXPECTED_MESSAGE_COLUMNS),
    ):
        present = set(_table_columns(conn, table))
        missing = [col for col in expected if col not in present]
        if missing:
            out[table] = missing
    return out


def schema_findings() -> List[str]:
    """Doctor warnings when state.db has drifted from the columns read here."""
    conn = connect()
    if conn is None:
        return []
    try:
        drift = missing_columns(conn)
    finally:
        conn.close()
    return [
        f"hermes state.db table `{table}` is missing column(s) "
        f"{', '.join(cols)} — the token/cost/context columns will show dashes "
        "until overcode's hermes reader is updated for this Hermes version"
        for table, cols in drift.items()
    ]


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _placeholders(count: int) -> str:
    return ",".join("?" for _ in range(count))


def fetch_session_rows(
    conn: sqlite3.Connection, session_ids: Sequence[str]
) -> List[sqlite3.Row]:
    """Rows for ``session_ids`` plus their compression-split descendants.

    Hermes splits a session into a child row (``parent_session_id`` set)
    when context compression rotates the id; the plugin may not see that as
    a reset, so one level of children is folded in. Oldest first.
    """
    ids = [sid for sid in session_ids if sid]
    if not ids:
        return []
    sql = (
        "SELECT * FROM sessions "
        f"WHERE id IN ({_placeholders(len(ids))}) "
        f"OR parent_session_id IN ({_placeholders(len(ids))}) "
        "ORDER BY started_at ASC"
    )
    return conn.execute(sql, (*ids, *ids)).fetchall()


def fetch_rows_for_directory(
    conn: sqlite3.Connection, directories: Sequence[str], since_ts: float
) -> List[sqlite3.Row]:
    """CLI sessions started in one of ``directories`` after ``since_ts``.

    Only root sessions (no parent) — a compression child is reached through
    its parent by ``fetch_session_rows``. Oldest first.
    """
    dirs = [d for d in directories if d]
    if not dirs:
        return []
    sql = (
        "SELECT * FROM sessions "
        f"WHERE source IN ({_placeholders(len(CLI_SOURCES))}) "
        f"AND cwd IN ({_placeholders(len(dirs))}) "
        "AND started_at >= ? AND parent_session_id IS NULL "
        "ORDER BY started_at ASC"
    )
    return conn.execute(sql, (*CLI_SOURCES, *dirs, since_ts)).fetchall()


def _scan_messages(
    conn: sqlite3.Connection, session_ids: Sequence[str]
) -> Dict[str, Any]:
    """One pass over recent messages for interaction counts and work times.

    A work cycle is a user message to the last assistant message before the
    next user message (or the end of the transcript).
    """
    out: Dict[str, Any] = {"interaction_count": 0, "work_times": []}
    ids = [sid for sid in session_ids if sid]
    if not ids:
        return out
    sql = (
        "SELECT session_id, role, timestamp FROM messages "
        f"WHERE session_id IN ({_placeholders(len(ids))}) "
        "ORDER BY timestamp DESC LIMIT ?"
    )
    try:
        rows = conn.execute(sql, (*ids, _MESSAGE_SCAN_LIMIT * max(1, len(ids)))).fetchall()
    except sqlite3.Error:
        return out

    # Walk oldest-first for the cycle arithmetic.
    cycle_start: Optional[float] = None
    last_assistant: Optional[float] = None
    for row in reversed(rows):
        role = row["role"]
        ts = _as_float(row["timestamp"])
        if role == "user":
            out["interaction_count"] += 1
            if cycle_start is not None and last_assistant is not None:
                elapsed = last_assistant - cycle_start
                if elapsed > 0:
                    out["work_times"].append(elapsed)
            cycle_start = ts
            last_assistant = None
        elif role == "assistant" and cycle_start is not None:
            last_assistant = ts
    if cycle_start is not None and last_assistant is not None:
        elapsed = last_assistant - cycle_start
        if elapsed > 0:
            out["work_times"].append(elapsed)
    return out


def _context_tokens(row: sqlite3.Row) -> int:
    """The most recent API call's prompt size, from ``model_config``."""
    try:
        config = json.loads(row["model_config"] or "{}")
    except (ValueError, TypeError):
        return 0
    if not isinstance(config, dict):
        return 0
    anchor = config.get("_usage_anchor")
    if not isinstance(anchor, dict):
        return 0
    return _as_int(anchor.get("prompt_tokens"))


def _effort(row: sqlite3.Row) -> Optional[str]:
    """Reasoning effort (#497), from ``model_config.reasoning_config``.

    Reasoning switched off reads as "none" — Hermes's own word for it.
    """
    try:
        config = json.loads(row["model_config"] or "{}")
    except (ValueError, TypeError):
        return None
    if not isinstance(config, dict):
        return None
    reasoning = config.get("reasoning_config")
    if not isinstance(reasoning, dict):
        return None
    if reasoning.get("enabled") is False:
        return "none"
    effort = reasoning.get("effort")
    return effort if isinstance(effort, str) and effort else None


def _hook_state_path(session: Any) -> Optional[Path]:
    """Where the plugin (via hook-handler) publishes this agent's hook state."""
    tmux_session = getattr(session, "tmux_session", None)
    name = getattr(session, "name", None)
    if not tmux_session or not name:
        return None
    state_dir = os.environ.get("OVERCODE_STATE_DIR")
    base = Path(state_dir) if state_dir else Path.home() / ".overcode" / "sessions"
    return base / tmux_session / f"hook_state_{name}.json"


def session_ids_from_hook_state(session: Any) -> List[str]:
    """Hermes session ids the plugin's SessionStart recorded, newest last."""
    path = _hook_state_path(session)
    if path is None:
        return []
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(state, dict):
        return []
    ids: List[str] = []
    raw_ids = state.get("agent_session_ids")
    if isinstance(raw_ids, list):
        ids.extend(sid for sid in raw_ids if isinstance(sid, str) and sid)
    active = state.get("agent_session_id")
    if isinstance(active, str) and active:
        ids = [sid for sid in ids if sid != active] + [active]
    return ids


class _WindowSampler:
    """Rebuilds "tokens used since <t>" from periodic total snapshots.

    Hermes stores cumulative per-session totals but no per-message usage,
    so the burn-rate window is the difference between the current totals
    and the oldest sample taken at or after ``since``. Samples are per
    overcode session id, bounded in age and count; a daemon restart loses
    them, which only resets the window (never misreports it).
    """

    def __init__(self) -> None:
        self._samples: Dict[str, List[Tuple[float, Dict[str, int]]]] = {}

    def record(self, key: str, totals: Dict[str, int], now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        samples = self._samples.setdefault(key, [])
        samples.append((now, dict(totals)))
        cutoff = now - _SAMPLE_MAX_AGE_SECONDS
        while samples and (samples[0][0] < cutoff or len(samples) > _SAMPLE_MAX_COUNT):
            samples.pop(0)

    def usage_since(self, key: str, since_ts: float, totals: Dict[str, int]) -> Dict[str, int]:
        samples = self._samples.get(key) or []
        baseline: Optional[Dict[str, int]] = None
        for ts, snapshot in samples:
            if ts >= since_ts:
                baseline = snapshot
                break
        if baseline is None:
            # Nothing sampled inside the window yet: the whole window is
            # "since we started looking", which is this very sample.
            return empty_window_usage()
        return {
            k: max(0, totals.get(k, 0) - baseline.get(k, 0))
            for k in empty_window_usage()
        }


class HermesStatsReader:
    """Reads Hermes's SQLite store for one session.

    Locates rows by the ids the plugin captured, falling back to the
    session's working directory plus its launch time when the plugin never
    ran. Any failure — no database, a lock, a renamed column — answers
    "unknown" so the columns render dashes instead of zeros.
    """

    backend_name = "hermes"

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path
        self._sampler = _WindowSampler()

    # -- helpers ---------------------------------------------------------

    def _connect(self) -> Optional[sqlite3.Connection]:
        return connect(self._db_path)

    @staticmethod
    def _owned_ids(session: Any) -> List[str]:
        ids = list(getattr(session, "agent_session_ids", None) or [])
        active = getattr(session, "active_agent_session_id", None)
        if active and active not in ids:
            ids.append(active)
        return [sid for sid in ids if isinstance(sid, str) and sid]

    @staticmethod
    def _directories(session: Any) -> List[str]:
        directory = getattr(session, "start_directory", None)
        if not directory:
            return []
        candidates = [str(directory)]
        try:
            candidates.append(str(Path(directory).resolve()))
        except OSError:
            pass
        return list(dict.fromkeys(candidates))

    def _rows_for(self, conn: sqlite3.Connection, session: Any) -> List[sqlite3.Row]:
        ids = self._owned_ids(session)
        if ids:
            rows = fetch_session_rows(conn, ids)
            if rows:
                return rows
        directories = self._directories(session)
        if not directories:
            return []
        since = _launch_ts(session)
        if since is None:
            return []
        roots = fetch_rows_for_directory(conn, directories, since)
        if not roots:
            return []
        return fetch_session_rows(conn, [row["id"] for row in roots])

    @staticmethod
    def _totals(rows: Sequence[sqlite3.Row]) -> Dict[str, int]:
        totals = empty_window_usage()
        for row in rows:
            totals["input_tokens"] += _as_int(row["input_tokens"])
            # Hermes bills reasoning as output and reports it separately;
            # overcode has no reasoning bucket, so it folds into output.
            totals["output_tokens"] += _as_int(row["output_tokens"]) + _as_int(
                row["reasoning_tokens"]
            )
            totals["cache_read_tokens"] += _as_int(row["cache_read_tokens"])
            totals["cache_creation_tokens"] += _as_int(row["cache_write_tokens"])
        return totals

    # -- StatsReader -----------------------------------------------------

    def get_stats(
        self, session: Any, *, history_file: Any = None
    ) -> Optional[AgentSessionStats]:
        conn = self._connect()
        if conn is None:
            return None
        try:
            if missing_columns(conn):
                return None
            rows = self._rows_for(conn, session)
            if not rows:
                return None

            totals = self._totals(rows)
            model = None
            for row in rows:
                if row["model"]:
                    model = str(row["model"])

            scan = _scan_messages(conn, [row["id"] for row in rows])

            from .hermes import configured_context_length
            return AgentSessionStats(
                interaction_count=scan["interaction_count"],
                input_tokens=totals["input_tokens"],
                output_tokens=totals["output_tokens"],
                cache_creation_tokens=totals["cache_creation_tokens"],
                cache_read_tokens=totals["cache_read_tokens"],
                work_times=scan["work_times"],
                # The newest row is the live conversation (a compression
                # child, or the only one).
                current_context_tokens=_context_tokens(rows[-1]),
                model=model,
                effort=_effort(rows[-1]),
                # Hermes's own denominator when the user pinned one (#469);
                # otherwise overcode's model tables, which agree with
                # Hermes's catalogue for the models checked at verification.
                reported_context_window=configured_context_length(),
                # Deliberately None: `provider` is overcode's API-transport
                # discriminator ("web"/"bedrock"), not Hermes's
                # billing_provider.
                provider=None,
            )
        except (sqlite3.Error, OSError, ValueError, KeyError, IndexError):
            return None
        finally:
            conn.close()

    def get_stored_cost(self, session: Any) -> Optional[float]:
        """Hermes's own cost figure for this agent, or None.

        Only trusted when Hermes itself says it priced the session
        (``cost_status`` other than ``unknown``) and the total is non-zero;
        otherwise the caller falls back to ``pricing.py`` on real tokens.
        """
        conn = self._connect()
        if conn is None:
            return None
        try:
            rows = self._rows_for(conn, session)
            total = 0.0
            for row in rows:
                status = row["cost_status"]
                if status is None or status == "unknown":
                    continue
                total += _as_float(row["estimated_cost_usd"])
        except (sqlite3.Error, OSError, ValueError, KeyError, IndexError):
            return None
        finally:
            conn.close()
        return total if total > 0 else None

    def get_current_session_id(
        self, session: Any, since: datetime
    ) -> Optional[str]:
        from_plugin = session_ids_from_hook_state(session)
        if from_plugin:
            return from_plugin[-1]

        directories = self._directories(session)
        if not directories:
            return None
        conn = self._connect()
        if conn is None:
            return None
        try:
            rows = fetch_rows_for_directory(conn, directories, since.timestamp())
        except (sqlite3.Error, OSError, ValueError):
            return None
        finally:
            conn.close()
        return rows[-1]["id"] if rows else None

    def discover_session_ids(
        self, session: Any, since: datetime, all_sessions: Sequence[Any]
    ) -> DiscoveredSessionIds:
        """Adopt Hermes conversation ids this agent owns but hasn't recorded.

        Plugin-reported ids first (exact), then any unowned root CLI
        session that started in this directory after launch.
        """
        from ..claude_pid import is_session_id_owned_by_others

        owned = set(self._owned_ids(session))
        session_id = getattr(session, "id", None)

        discovered: List[str] = []
        latest: Optional[str] = None

        def consider(sid: str) -> None:
            nonlocal latest
            if not sid or not SESSION_ID_RE.match(sid):
                return
            if is_session_id_owned_by_others(sid, session_id, all_sessions):
                return
            if sid not in owned and sid not in discovered:
                discovered.append(sid)
            latest = sid

        for sid in session_ids_from_hook_state(session):
            consider(sid)

        if latest is None:
            directories = self._directories(session)
            if directories:
                conn = self._connect()
                if conn is not None:
                    try:
                        for row in fetch_rows_for_directory(
                            conn, directories, since.timestamp()
                        ):
                            consider(row["id"])
                    except (sqlite3.Error, OSError, ValueError):
                        pass
                    finally:
                        conn.close()

        return DiscoveredSessionIds(ids=discovered, latest=latest)

    def get_window_token_usage(
        self, session: Any, since: datetime
    ) -> Dict[str, int]:
        conn = self._connect()
        if conn is None:
            return empty_window_usage()
        try:
            rows = self._rows_for(conn, session)
        except (sqlite3.Error, OSError, ValueError, KeyError, IndexError):
            return empty_window_usage()
        finally:
            conn.close()
        if not rows:
            return empty_window_usage()
        totals = self._totals(rows)
        key = str(getattr(session, "id", None) or getattr(session, "name", ""))
        usage = self._sampler.usage_since(key, since.timestamp(), totals)
        self._sampler.record(key, totals)
        return usage

    def get_container_stats(self, session: Any) -> Optional[AgentSessionStats]:
        # The host database is the only source; not visible from inside a
        # container.
        return None


def _launch_ts(session: Any) -> Optional[float]:
    """The agent's launch time in Hermes's epoch-seconds convention."""
    start_time = getattr(session, "start_time", None)
    if not start_time:
        return None
    try:
        return datetime.fromisoformat(start_time).timestamp()
    except (ValueError, TypeError):
        return None


__all__ = [
    "CLI_SOURCES",
    "EXPECTED_MESSAGE_COLUMNS",
    "EXPECTED_SESSION_COLUMNS",
    "HermesStatsReader",
    "SESSION_ID_RE",
    "connect",
    "database_path",
    "fetch_rows_for_directory",
    "fetch_session_rows",
    "missing_columns",
    "schema_findings",
    "session_ids_from_hook_state",
]
