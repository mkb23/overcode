"""opencode stats: the SQLite ``session``/``message`` tables behind ``StatsReader``.

opencode keeps everything overcode needs for the token/cost/context columns in
``~/.local/share/opencode/opencode.db`` — no JSONL scraping. Phase 5 of
``docs/design/agent-agnostic-backends-opencode.md``.

The schema below was read off a live v1.18.19 database (macOS/arm64) rather
than taken from the design doc's research, and matches it:

    session(id, project_id, workspace_id, parent_id, slug, directory, path,
            title, version, share_url, summary_*, metadata, cost,
            tokens_input, tokens_output, tokens_reasoning, tokens_cache_read,
            tokens_cache_write, revert, permission, agent, model,
            time_created, time_updated, time_compacting, time_archived)
    message(id, session_id, time_created, time_updated, data)

``model`` is JSON (``{"id": "gpt-4o-mini", "providerID": "openai"}``) and
``message.data`` is the whole message envelope, including per-turn
``tokens`` / ``cost`` for assistant messages.

Every entry point is failure-tolerant by construction: a missing database, a
locked one, or a renamed column returns None/empty rather than raising into a
daemon tick.
"""

import json
import os
import sqlite3
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
    "directory",
    "parent_id",
    "cost",
    "tokens_input",
    "tokens_output",
    "tokens_reasoning",
    "tokens_cache_read",
    "tokens_cache_write",
    "model",
    "time_created",
    "time_updated",
)

EXPECTED_MESSAGE_COLUMNS: Tuple[str, ...] = (
    "id",
    "session_id",
    "time_created",
    "data",
)

# opencode session ids are `ses_` + random. Used to keep the reader from
# adopting a Claude UUID left over on a rebadged session.
SESSION_ID_PREFIX = "ses_"

# Messages scanned per session for interaction counts / work times / context.
# A turn is one or two rows, so this covers a long conversation while keeping
# the JSON parsing bounded.
_MESSAGE_SCAN_LIMIT = 500

# SQLite is in WAL mode with a live writer; a short wait beats a hard failure,
# and a long one would stall the daemon tick.
_BUSY_TIMEOUT_MS = 300
_CONNECT_TIMEOUT_SECONDS = 0.5


def default_data_dir() -> Path:
    """opencode's data directory, honouring XDG."""
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "opencode"


def database_path() -> Path:
    """Where opencode's SQLite database lives.

    ``OPENCODE_DB`` names the file outright; ``OPENCODE_DATA_DIR`` names the
    directory holding it. Both are opencode's own env vars.
    """
    explicit = os.environ.get("OPENCODE_DB")
    if explicit:
        return Path(explicit)
    data_dir = os.environ.get("OPENCODE_DATA_DIR")
    if data_dir:
        return Path(data_dir) / "opencode.db"
    return default_data_dir() / "opencode.db"


def connect(path: Optional[Path] = None) -> Optional[sqlite3.Connection]:
    """Open the database read-only, or return None.

    Read-only URI mode means overcode can never write to (or create) opencode's
    store, and a busy timeout keeps a mid-write WAL from turning into an
    exception on the daemon's thread.
    """
    db_path = path or database_path()
    try:
        if not db_path.exists():
            return None
        conn = sqlite3.connect(
            f"file:{db_path}?mode=ro",
            uri=True,
            timeout=_CONNECT_TIMEOUT_SECONDS,
        )
        conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        return conn
    except (sqlite3.Error, OSError, ValueError):
        return None


def _table_columns(conn: sqlite3.Connection, table: str) -> Tuple[str, ...]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.Error:
        return ()
    return tuple(row[1] for row in rows)


def missing_columns(conn: sqlite3.Connection) -> Dict[str, List[str]]:
    """Expected-but-absent columns, keyed by table. Empty when the schema fits."""
    result: Dict[str, List[str]] = {}
    for table, expected in (
        ("session", EXPECTED_SESSION_COLUMNS),
        ("message", EXPECTED_MESSAGE_COLUMNS),
    ):
        present = set(_table_columns(conn, table))
        if not present:
            result[table] = ["<table missing>"]
            continue
        absent = [name for name in expected if name not in present]
        if absent:
            result[table] = absent
    return result


def schema_findings() -> List[str]:
    """Doctor warnings about opencode's SQLite schema, best effort.

    Empty when the database is absent (that is not a fault — the user may
    simply not have run opencode yet) or when the schema matches.
    """
    conn = connect()
    if conn is None:
        return []
    try:
        drift = missing_columns(conn)
    finally:
        conn.close()
    if not drift:
        return []
    parts = [f"{table} ({', '.join(cols)})" for table, cols in sorted(drift.items())]
    return [
        "opencode's SQLite schema has drifted — missing "
        + "; ".join(parts)
        + f" in {database_path()}. Token/cost columns will show dashes until "
        "overcode is updated."
    ]


def _parse_model(raw: Any) -> Optional[str]:
    """Render opencode's ``model`` JSON as the ``provider/model`` id it launched with.

    Keeping the qualified form matters: it is what ``--model`` needs on a
    restart, and ``pricing.lookup_pricing`` still substring-matches the bare
    model name inside it.
    """
    if not raw:
        return None
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return raw or None
    else:
        parsed = raw
    if not isinstance(parsed, dict):
        return None
    model_id = parsed.get("id") or parsed.get("modelID")
    provider = parsed.get("providerID") or parsed.get("provider")
    if model_id and provider:
        return f"{provider}/{model_id}"
    return model_id or None


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
    return ",".join("?" * count)


def fetch_session_rows(
    conn: sqlite3.Connection, session_ids: Sequence[str]
) -> List[sqlite3.Row]:
    """Session rows for the given ids, newest-updated last."""
    ids = [sid for sid in session_ids if sid]
    if not ids:
        return []
    columns = ", ".join(EXPECTED_SESSION_COLUMNS)
    sql = (
        f"SELECT {columns} FROM session WHERE id IN ({_placeholders(len(ids))}) "
        "ORDER BY time_updated ASC"
    )
    conn.row_factory = sqlite3.Row
    return conn.execute(sql, ids).fetchall()


def fetch_rows_for_directory(
    conn: sqlite3.Connection, directories: Sequence[str], since_ms: int
) -> List[sqlite3.Row]:
    """Root session rows started in one of ``directories`` at or after ``since_ms``.

    Several spellings of the same directory are accepted because opencode
    records the cwd it was handed while overcode holds the configured path —
    on macOS ``/tmp`` and ``/private/tmp`` are the same place, and a symlinked
    project root is common.

    Child sessions (the `task` tool's sub-agents) are excluded: their tokens
    already roll up through the parent's own turns, and adopting one as the
    agent's conversation would make resume target the wrong id.
    """
    candidates = [d for d in dict.fromkeys(directories) if d]
    if not candidates:
        return []
    columns = ", ".join(EXPECTED_SESSION_COLUMNS)
    sql = (
        f"SELECT {columns} FROM session "
        f"WHERE directory IN ({_placeholders(len(candidates))}) "
        "AND time_created >= ? AND parent_id IS NULL "
        "ORDER BY time_updated ASC"
    )
    conn.row_factory = sqlite3.Row
    return conn.execute(sql, (*candidates, since_ms)).fetchall()


def _scan_messages(
    conn: sqlite3.Connection,
    session_ids: Sequence[str],
    active_id: Optional[str],
    since_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """One pass over recent messages for the counts the columns need.

    Returns interaction count (user messages), per-turn work times, the newest
    assistant message's total-token snapshot (the live context size), and, when
    ``since_ms`` is given, the token usage inside that window.
    """
    out: Dict[str, Any] = {
        "interaction_count": 0,
        "work_times": [],
        "current_context_tokens": 0,
        "window": empty_window_usage(),
    }
    ids = [sid for sid in session_ids if sid]
    if not ids:
        return out

    sql = (
        "SELECT id, session_id, time_created, data FROM message "
        f"WHERE session_id IN ({_placeholders(len(ids))}) "
        "ORDER BY time_created DESC LIMIT ?"
    )
    try:
        rows = conn.execute(sql, (*ids, _MESSAGE_SCAN_LIMIT * max(1, len(ids)))).fetchall()
    except sqlite3.Error:
        return out

    seen_context = False
    for _msg_id, session_id, time_created, data in rows:
        try:
            envelope = json.loads(data)
        except (ValueError, TypeError):
            continue
        if not isinstance(envelope, dict):
            continue
        role = envelope.get("role")
        if role == "user":
            out["interaction_count"] += 1
            continue
        if role != "assistant":
            continue

        tokens = envelope.get("tokens")
        tokens = tokens if isinstance(tokens, dict) else {}
        cache = tokens.get("cache")
        cache = cache if isinstance(cache, dict) else {}

        # Rows arrive newest-first, so the first assistant turn carrying a
        # total is the live context size. Only the active conversation's
        # context is meaningful — an older /new session's is stale.
        if (
            not seen_context
            and tokens.get("total")
            and (active_id is None or session_id == active_id)
        ):
            out["current_context_tokens"] = _as_int(tokens.get("total"))
            seen_context = True

        times = envelope.get("time")
        times = times if isinstance(times, dict) else {}
        created = times.get("created")
        completed = times.get("completed")
        if isinstance(created, (int, float)) and isinstance(completed, (int, float)):
            elapsed = (completed - created) / 1000.0
            if elapsed > 0:
                out["work_times"].append(elapsed)

        if since_ms is not None and _as_int(time_created) >= since_ms:
            window = out["window"]
            window["input_tokens"] += _as_int(tokens.get("input"))
            window["output_tokens"] += _as_int(tokens.get("output")) + _as_int(
                tokens.get("reasoning")
            )
            window["cache_creation_tokens"] += _as_int(cache.get("write"))
            window["cache_read_tokens"] += _as_int(cache.get("read"))

    out["work_times"].reverse()
    return out


def _hook_state_path(session: Any) -> Optional[Path]:
    """Where the bundled plugin publishes this agent's hook state."""
    tmux_session = getattr(session, "tmux_session", None)
    name = getattr(session, "name", None)
    if not tmux_session or not name:
        return None
    state_dir = os.environ.get("OVERCODE_STATE_DIR")
    base = Path(state_dir) if state_dir else Path.home() / ".overcode" / "sessions"
    return base / tmux_session / f"hook_state_{name}.json"


def session_ids_from_hook_state(session: Any) -> List[str]:
    """opencode session ids the plugin recorded, newest last.

    This is the exact analogue of Claude's prescribed ``--session-id``: the
    agent process itself tells overcode which conversation it owns, so the
    directory+time fallback below only has to cover a plugin-less launch.
    """
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
        # Active id goes last so callers can treat the tail as "current".
        ids = [sid for sid in ids if sid != active] + [active]
    return ids


class OpencodeStatsReader:
    """Reads opencode's SQLite store for one session.

    Locates rows by the ids the bundled plugin captured, falling back to the
    session's working directory plus its launch time when the plugin never
    ran. Any failure — no database, a lock, a renamed column — answers
    "unknown" so the columns render dashes instead of zeros.
    """

    backend_name = "opencode"

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path

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
        """Every spelling of the agent's working directory worth matching on."""
        directory = getattr(session, "start_directory", None)
        if not directory:
            return []
        candidates = [str(directory)]
        try:
            candidates.append(str(Path(directory).resolve()))
        except OSError:
            pass
        return list(dict.fromkeys(candidates))

    def _rows_for(
        self, conn: sqlite3.Connection, session: Any
    ) -> List[sqlite3.Row]:
        ids = self._owned_ids(session)
        if ids:
            rows = fetch_session_rows(conn, ids)
            if rows:
                return rows
        directories = self._directories(session)
        if not directories:
            return []
        since_ms = _launch_ms(session)
        if since_ms is None:
            return []
        return fetch_rows_for_directory(conn, directories, since_ms)

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

            input_tokens = 0
            output_tokens = 0
            cache_creation = 0
            cache_read = 0
            model = None
            for row in rows:
                input_tokens += _as_int(row["tokens_input"])
                # opencode bills reasoning as output and reports it separately;
                # overcode has no reasoning bucket, so it folds into output
                # rather than silently disappearing from the totals.
                output_tokens += _as_int(row["tokens_output"]) + _as_int(
                    row["tokens_reasoning"]
                )
                cache_read += _as_int(row["tokens_cache_read"])
                cache_creation += _as_int(row["tokens_cache_write"])
                parsed_model = _parse_model(row["model"])
                if parsed_model:
                    model = parsed_model

            row_ids = [row["id"] for row in rows]
            active_id = row_ids[-1] if row_ids else None
            scan = _scan_messages(conn, row_ids, active_id)

            return AgentSessionStats(
                interaction_count=scan["interaction_count"],
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_creation_tokens=cache_creation,
                cache_read_tokens=cache_read,
                work_times=scan["work_times"],
                current_context_tokens=scan["current_context_tokens"],
                model=model,
                # Deliberately None: `provider` is overcode's API-transport
                # discriminator ("web"/"bedrock"), not opencode's model
                # provider, and writing "openai" into it would corrupt the
                # session record.
                provider=None,
            )
        except (sqlite3.Error, OSError, ValueError, KeyError, IndexError):
            return None
        finally:
            conn.close()

    def get_stored_cost(self, session: Any) -> Optional[float]:
        """opencode's own cost total for this agent, or None.

        Preferred over recomputing from tokens because opencode records the
        provider's actual per-turn charge. Returns None when it is zero (the
        subscription-auth case the design doc flags) so the caller falls back
        to ``pricing.py``.
        """
        conn = self._connect()
        if conn is None:
            return None
        try:
            rows = self._rows_for(conn, session)
            total = sum(_as_float(row["cost"]) for row in rows)
        except (sqlite3.Error, OSError, ValueError, KeyError, IndexError):
            return None
        finally:
            conn.close()
        return total if total > 0 else None

    def get_current_session_id(
        self, session: Any, since: datetime
    ) -> Optional[str]:
        """The conversation this agent is in right now.

        The plugin's answer wins; the directory scan is the plugin-less
        fallback and mirrors ``ClaudeStatsReader``'s history.jsonl lookup.
        """
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
            rows = fetch_rows_for_directory(
                conn, directories, int(since.timestamp() * 1000)
            )
        except (sqlite3.Error, OSError, ValueError):
            return None
        finally:
            conn.close()
        return rows[-1]["id"] if rows else None

    def discover_session_ids(
        self, session: Any, since: datetime, all_sessions: Sequence[Any]
    ) -> DiscoveredSessionIds:
        """Adopt opencode conversation ids this agent owns but hasn't recorded.

        Plugin-reported ids first (exact), then any unowned root session that
        started in this directory after launch.
        """
        from ..claude_pid import is_session_id_owned_by_others

        owned = set(self._owned_ids(session))
        session_id = getattr(session, "id", None)

        discovered: List[str] = []
        latest: Optional[str] = None

        def consider(sid: str) -> None:
            nonlocal latest
            if not sid or not sid.startswith(SESSION_ID_PREFIX):
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
                        rows = fetch_rows_for_directory(
                            conn, directories, int(since.timestamp() * 1000)
                        )
                        for row in rows:
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
            if not rows:
                return empty_window_usage()
            row_ids = [row["id"] for row in rows]
            scan = _scan_messages(
                conn, row_ids, row_ids[-1], since_ms=int(since.timestamp() * 1000)
            )
            return scan["window"]
        except (sqlite3.Error, OSError, ValueError, KeyError, IndexError):
            return empty_window_usage()
        finally:
            conn.close()

    def get_container_stats(self, session: Any) -> Optional[AgentSessionStats]:
        # opencode has no devcontainer story yet; the host database is the
        # only source, and it is not visible from inside a container.
        return None


def _launch_ms(session: Any) -> Optional[int]:
    """The agent's launch time in opencode's millisecond epoch."""
    start_time = getattr(session, "start_time", None)
    if not start_time:
        return None
    try:
        return int(datetime.fromisoformat(start_time).timestamp() * 1000)
    except (ValueError, TypeError):
        return None


__all__ = [
    "EXPECTED_MESSAGE_COLUMNS",
    "EXPECTED_SESSION_COLUMNS",
    "OpencodeStatsReader",
    "connect",
    "database_path",
    "default_data_dir",
    "fetch_rows_for_directory",
    "fetch_session_rows",
    "missing_columns",
    "schema_findings",
    "session_ids_from_hook_state",
]
