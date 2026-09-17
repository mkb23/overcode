"""opencode2 stats: the SQLite ``session_v2``/``session_message`` tables.

opencode2 (the 2.0 preview CLI) shares v1's database file and env overrides
(``OPENCODE_DB`` / ``OPENCODE_DATA_DIR`` / ``XDG_DATA_HOME`` →
``opencode.db``) but writes sessions ONLY to ``session_v2`` and
``session_message`` — a live-verified v2 run
(``ses_f59f44840ffe7f1PaCEztE1b7y``) leaves v1's ``session`` / ``message``
tables empty, so the v1 reader can never see it.

Schema read off the live v0.0.0-dev-19272 database, Sep 15 2026:

    session_v2(id, project_id, workspace_id, parent_id, fork_session_id,
               fork_boundary, slug, directory, path, title, version,
               share_url, summary_*, metadata, cost, tokens_input,
               tokens_output, tokens_reasoning, tokens_cache_read,
               tokens_cache_write, revert, permission, agent, model,
               time_created, time_updated, time_compacting, time_archived,
               time_suspended, resume_attempts, time_idle, time_viewed,
               idle_outcome)
    session_message(id, session_id, type, seq, time_created, time_updated,
                    data)

``type`` is ``"user"``/``"assistant"`` (v1 kept the role inside the JSON
envelope) and assistant ``data`` carries ``agent``, ``model``,
``cost``, ``tokens`` and ``time`` per message.

Two v2 deltas vs the v1 reader, both live-verified:

- ``session_v2.model`` / ``session_v2.agent`` are NULL even on completed
  sessions; the fallback is the newest assistant message's ``data`` JSON
  (``model.id`` / ``agent``). The resolved persona rides the
  ``AgentSessionStats.agent`` field; both the row read and the message
  fallback are scoped to the ACTIVE session, so after ``/new`` an older
  tracked session's identities never leak into the live conversation.
- v2's assistant ``data`` has no ``tokens.total``; the live context size is
  derived as the processed prompt (input + cache read + cache write) of the
  newest assistant turn — the same semantics
  ``AgentSessionStats.current_context_tokens`` documents.

Everything else is the v1 reader's shape: the env/path/plugin-state and
coercion helpers are shared with ``backends/opencode_stats.py`` (the
telemetry plugin writes the same hook-state files), and every entry point
is failure-tolerant by construction — a missing database, a locked one, or
a renamed column returns None/empty rather than raising into a daemon tick.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..stats_reader import (
    AgentSessionStats,
    DiscoveredSessionIds,
    empty_window_usage,
)
from .opencode_stats import (
    _as_float,
    _as_int,
    _launch_ms,
    _parse_model,
    _placeholders,
    _table_columns,
    connect,
    database_path,
    default_data_dir,
    session_ids_from_hook_state,
)

# Columns the reader reads by name. Anything missing here is schema drift —
# `schema_findings()` turns that into a doctor warning and `get_stats`
# returns None rather than half-populated numbers.
EXPECTED_SESSION_V2_COLUMNS: Tuple[str, ...] = (
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
    "agent",
    "time_created",
    "time_updated",
)

EXPECTED_SESSION_MESSAGE_COLUMNS: Tuple[str, ...] = (
    "id",
    "session_id",
    "type",
    "seq",
    "time_created",
    "data",
)

# opencode2 session ids are `ses_` + random, same as v1. Used to keep the
# reader from adopting a Claude UUID left over on a rebadged session.
SESSION_ID_PREFIX = "ses_"

# Messages scanned per session for interaction counts / work times /
# context. A turn is one or two rows, so this covers a long conversation
# while keeping the JSON parsing bounded.
_MESSAGE_SCAN_LIMIT = 500


def missing_columns(conn: sqlite3.Connection) -> Dict[str, List[str]]:
    """Expected-but-absent columns, keyed by table. Empty when the schema fits."""
    result: Dict[str, List[str]] = {}
    for table, expected in (
        ("session_v2", EXPECTED_SESSION_V2_COLUMNS),
        ("session_message", EXPECTED_SESSION_MESSAGE_COLUMNS),
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
    """Doctor warnings about opencode2's SQLite schema, best effort.

    Empty when the database is absent (that is not a fault — the user may
    simply not have run opencode2 yet) or when the schema matches. A
    missing table or missing columns produce one human-readable warning
    each — including the v1-only-database case, where neither v2 table
    exists and no v2 session can ever be read.
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
    db = database_path()
    findings: List[str] = []
    for table, cols in sorted(drift.items()):
        if cols == ["<table missing>"]:
            findings.append(
                f"opencode2's SQLite database {db} has no {table} table — "
                "v2 sessions cannot be read from it. Token/cost columns "
                "will show dashes until overcode is updated."
            )
        else:
            findings.append(
                "opencode2's SQLite schema has drifted — "
                f"{table} is missing column(s) {', '.join(cols)} in {db}. "
                "Token/cost columns will show dashes until overcode is "
                "updated."
            )
    return findings


def fetch_session_rows(conn: sqlite3.Connection, session_ids: Sequence[str]) -> List[sqlite3.Row]:
    """Session rows for the given ids, newest-updated last."""
    ids = [sid for sid in session_ids if sid]
    if not ids:
        return []
    columns = ", ".join(EXPECTED_SESSION_V2_COLUMNS)
    sql = (
        f"SELECT {columns} FROM session_v2 "
        f"WHERE id IN ({_placeholders(len(ids))}) "
        "ORDER BY time_updated ASC"
    )
    conn.row_factory = sqlite3.Row
    return conn.execute(sql, ids).fetchall()


def fetch_rows_for_directory(
    conn: sqlite3.Connection, directories: Sequence[str], since_ms: int
) -> List[sqlite3.Row]:
    """Root session rows started in one of ``directories`` at or after ``since_ms``.

    Several spellings of the same directory are accepted because opencode2
    records the cwd it was handed while overcode holds the configured path
    (on macOS ``/tmp`` and ``/private/tmp`` are the same place, and a
    symlinked project root is common).

    Child sessions are excluded: their tokens roll up through the parent's
    own turns, and adopting one as the agent's conversation would make
    resume target the wrong id.
    """
    candidates = [d for d in dict.fromkeys(directories) if d]
    if not candidates:
        return []
    columns = ", ".join(EXPECTED_SESSION_V2_COLUMNS)
    sql = (
        f"SELECT {columns} FROM session_v2 "
        f"WHERE directory IN ({_placeholders(len(candidates))}) "
        "AND time_created >= ? AND parent_id IS NULL "
        "ORDER BY time_updated ASC"
    )
    conn.row_factory = sqlite3.Row
    return conn.execute(sql, (*candidates, since_ms)).fetchall()


def _row_identities(rows: Sequence[sqlite3.Row]) -> Tuple[Optional[str], Optional[str]]:
    """(model, agent) taken from the ACTIVE session row (newest-updated last).

    Only ``rows[-1]`` — the session the agent is in right now — is read:
    after ``/new`` an older tracked session's row-level identities must
    not suppress the active session's.
    """
    active = rows[-1]
    return _parse_model(active["model"]), (active["agent"] or None)


def _scan_messages(
    conn: sqlite3.Connection,
    session_ids: Sequence[str],
    active_id: Optional[str],
    since_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """One pass over recent messages for the counts the columns need.

    Returns interaction count (user messages), per-turn work times, the
    newest assistant message's context snapshot (the live context size),
    the newest assistant message's model/agent (v2's session-row columns
    are NULL on verified live rows), and, when ``since_ms`` is given, the
    token usage inside that window.

    Rows are consumed newest-first (``time_created DESC`` with ``seq`` as
    the tie-breaker — ``seq`` alone would interleave multiple owned
    sessions), mirroring the v1 scan's consumption order.
    """
    out: Dict[str, Any] = {
        "interaction_count": 0,
        "work_times": [],
        "current_context_tokens": 0,
        "window": empty_window_usage(),
        "model": None,
        "agent": None,
    }
    ids = [sid for sid in session_ids if sid]
    if not ids:
        return out

    sql = (
        "SELECT id, session_id, type, time_created, data FROM session_message "
        f"WHERE session_id IN ({_placeholders(len(ids))}) "
        "AND type IN ('user', 'assistant') "
        "ORDER BY time_created DESC, seq DESC LIMIT ?"
    )
    try:
        rows = conn.execute(sql, (*ids, _MESSAGE_SCAN_LIMIT * max(1, len(ids)))).fetchall()
    except sqlite3.Error:
        return out

    seen_context = False
    for _msg_id, session_id, mtype, time_created, data in rows:
        try:
            envelope = json.loads(data)
        except (ValueError, TypeError):
            continue
        if not isinstance(envelope, dict):
            continue
        if mtype == "user":
            out["interaction_count"] += 1
            continue
        if mtype != "assistant":
            continue

        tokens = envelope.get("tokens")
        tokens = tokens if isinstance(tokens, dict) else {}
        cache = tokens.get("cache")
        cache = cache if isinstance(cache, dict) else {}

        # v2's assistant data has no `tokens.total` (verified); the context
        # size is the processed prompt — input + cache read + cache write —
        # the semantics AgentSessionStats.current_context_tokens documents.
        # Rows arrive newest-first, and only the active conversation's
        # context is meaningful — an older /new session's is stale.
        if not seen_context and (active_id is None or session_id == active_id):
            context = (
                _as_int(tokens.get("input"))
                + _as_int(cache.get("read"))
                + _as_int(cache.get("write"))
            )
            if context > 0:
                out["current_context_tokens"] = context
                seen_context = True

        # Newest assistant message wins for the model/agent fallback —
        # but only inside the ACTIVE session: after `/new`, an older
        # tracked session's identities must not leak into the active
        # conversation.
        if active_id is not None and session_id == active_id:
            if out["model"] is None:
                parsed_model = _parse_model(envelope.get("model"))
                if parsed_model:
                    out["model"] = parsed_model
            if out["agent"] is None:
                agent = envelope.get("agent")
                if isinstance(agent, str) and agent:
                    out["agent"] = agent

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


class Opencode2StatsReader:
    """Reads opencode2's SQLite store for one session.

    Locates rows by the ids the bundled telemetry plugin captured (the v2
    TUI plugin writes the same hook-state files as v1's), falling back to
    the session's working directory plus its launch time when the plugin
    never ran. Any failure — no database, a lock, a renamed column —
    answers "unknown" so the columns render dashes instead of zeros.
    """

    backend_name = "opencode2"

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

    def _rows_for(self, conn: sqlite3.Connection, session: Any) -> List[sqlite3.Row]:
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
        # The directory fallback runs only when telemetry is absent, and
        # same-directory multi-agent is a supported fleet shape (two
        # opencode2 agents in one project): with no plugin ids to
        # discriminate on, every root session started here since launch
        # is a candidate, and adopting more than one would sum a
        # stranger's tokens. Ambiguity must degrade to unknown (dashes),
        # never wrong numbers — so only a unique candidate is adopted.
        # v1's ``opencode_stats.py`` shares this directory-fallback
        # design; the uniqueness guard is deliberate for v2.
        rows = fetch_rows_for_directory(conn, directories, since_ms)
        return rows if len(rows) == 1 else []

    # -- StatsReader -----------------------------------------------------

    def get_stats(self, session: Any, *, history_file: Any = None) -> Optional[AgentSessionStats]:
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
            cache_read = 0
            cache_creation = 0
            for row in rows:
                input_tokens += _as_int(row["tokens_input"])
                # opencode bills reasoning as output and reports it
                # separately; overcode has no reasoning bucket, so it folds
                # into output rather than silently disappearing.
                output_tokens += _as_int(row["tokens_output"]) + _as_int(row["tokens_reasoning"])
                cache_read += _as_int(row["tokens_cache_read"])
                cache_creation += _as_int(row["tokens_cache_write"])

            row_model, row_agent = _row_identities(rows)
            row_ids = [row["id"] for row in rows]
            scan = _scan_messages(conn, row_ids, row_ids[-1])

            return AgentSessionStats(
                interaction_count=scan["interaction_count"],
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_creation_tokens=cache_creation,
                cache_read_tokens=cache_read,
                work_times=scan["work_times"],
                current_context_tokens=scan["current_context_tokens"],
                # Session rows carry model/agent first; the newest
                # assistant message is the verified-NULL fallback. Both
                # sides are scoped to the active session.
                model=row_model or scan["model"],
                # Same precedence shape as model: the active row's
                # agent wins, the newest assistant message is the
                # NULL-row fallback.
                agent=row_agent or scan["agent"],
                # Deliberately None: `provider` is overcode's API-transport
                # discriminator ("web"/"bedrock"), not opencode's model
                # provider, and writing opencode's provider id into it
                # would corrupt the session record.
                provider=None,
            )
        except (sqlite3.Error, OSError, ValueError, KeyError, IndexError):
            return None
        finally:
            conn.close()

    def get_stored_cost(self, session: Any) -> Optional[float]:
        """opencode2's own cost total for this agent, or None.

        Preferred over recomputing from tokens because opencode2 records the
        provider's actual per-turn charge. Returns None when it is zero (the
        subscription-auth case the design doc flags) so the caller falls
        back to ``pricing.py``.
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

    def get_current_session_id(self, session: Any, since: datetime) -> Optional[str]:
        """The conversation this agent is in right now.

        The plugin's answer wins; the directory scan is the plugin-less
        fallback and mirrors ``ClaudeStatsReader``'s history.jsonl lookup.
        With more than one root session started in this directory since
        ``since`` (a second opencode2 agent in the same project, telemetry
        absent), ownership is ambiguous and the answer is None — never the
        newest stranger's session.
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
            rows = fetch_rows_for_directory(conn, directories, int(since.timestamp() * 1000))
        except (sqlite3.Error, OSError, ValueError):
            return None
        finally:
            conn.close()
        return rows[-1]["id"] if len(rows) == 1 else None

    def discover_session_ids(
        self, session: Any, since: datetime, all_sessions: Sequence[Any]
    ) -> DiscoveredSessionIds:
        """Adopt opencode2 conversation ids this agent owns but hasn't recorded.

        Plugin-reported ids first (exact), then any unowned root session
        that started in this directory after launch.
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

    def get_window_token_usage(self, session: Any, since: datetime) -> Dict[str, int]:
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
        # opencode2 has no devcontainer story; the host database is the
        # only source, and it is not visible from inside a container.
        return None


__all__ = [
    "EXPECTED_SESSION_MESSAGE_COLUMNS",
    "EXPECTED_SESSION_V2_COLUMNS",
    "Opencode2StatsReader",
    "connect",
    "database_path",
    "default_data_dir",
    "fetch_rows_for_directory",
    "fetch_session_rows",
    "missing_columns",
    "schema_findings",
    "session_ids_from_hook_state",
]
