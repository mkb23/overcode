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

- ``session_v2.model`` / ``session_v2.agent`` were NULL on the Sep 15
  capture but populated (a ``{"id", "providerID"}`` JSON string / ``build``)
  on Sep 17 runs of the same build; the row wins when present and the
  fallback is the newest assistant message's ``data`` JSON (``model.id`` /
  ``agent``). The resolved persona rides the
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
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..stats_reader import (
    AgentSessionStats,
    empty_window_usage,
)
from ._stats_common import as_int, placeholders, table_columns
from .opencode_stats import (
    WindowIndex,
    _cached_records,
    _OpencodeFamilyReader,
    _optional_ms,
    _parse_model,
    _parse_variant,
    connect,
    database_path,
    default_data_dir,
    remember_window_index,
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
        present = set(table_columns(conn, table))
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
        f"WHERE id IN ({placeholders(len(ids))}) "
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
        f"WHERE directory IN ({placeholders(len(candidates))}) "
        "AND time_created >= ? AND parent_id IS NULL "
        "ORDER BY time_updated ASC"
    )
    conn.row_factory = sqlite3.Row
    return conn.execute(sql, (*candidates, since_ms)).fetchall()


def _row_identities(
    rows: Sequence[sqlite3.Row],
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """(model, agent, effort) taken from the ACTIVE session row (newest-updated last).

    Only ``rows[-1]`` — the session the agent is in right now — is read:
    after ``/new`` an older tracked session's row-level identities must
    not suppress the active session's.
    """
    active = rows[-1]
    return (
        _parse_model(active["model"]),
        (active["agent"] or None),
        _parse_variant(active["model"]),
    )


def _scan_sql(ids: Sequence[str]) -> Tuple[str, List[Any]]:
    """The newest ``_MESSAGE_SCAN_LIMIT`` user/assistant rows of each
    conversation — narrow columns only, unordered across conversations.

    One bounded probe per conversation rather than one ``session_id IN
    (...) ORDER BY ... LIMIT`` over all of them — the shared sort's cost
    grew roughly quadratically with the number of owned ids (#476; the v1
    reader's ``_scan_sql`` has the numbers). Inside one conversation
    ``seq`` is assigned in insertion order, so ``ORDER BY seq DESC`` on the
    unique ``(session_id, seq)`` index yields exactly the newest rows
    without a sort; the caller merges by ``(time_created, seq)`` in Python.

    ``data`` is deliberately not selected: on a real store an assistant
    row carries its tool output inline (1 KB-1 MB); bodies are fetched
    once per version through ``opencode_stats._cached_records``.
    """
    probe = (
        "SELECT * FROM (SELECT id, session_id, type, seq, time_created, "
        "time_updated FROM session_message WHERE session_id = ? "
        "AND type IN ('user', 'assistant') ORDER BY seq DESC LIMIT ?)"
    )
    sql = " UNION ALL ".join([probe] * len(ids))
    params: List[Any] = []
    for sid in ids:
        params.extend((sid, _MESSAGE_SCAN_LIMIT))
    return sql, params


def _parse_session_message_record(
    data: Optional[str], mtype: Any
) -> Tuple[Optional[Dict[str, Any]], bool]:
    """Extract what the scan needs from a v2 ``session_message.data`` body.

    ``mtype`` is the row's ``type`` column (the envelope has no role).
    """
    try:
        envelope = json.loads(data) if data is not None else None
    except (ValueError, TypeError):
        return None, False
    if not isinstance(envelope, dict):
        return None, False
    tokens = envelope.get("tokens")
    tokens = tokens if isinstance(tokens, dict) else {}
    cache = tokens.get("cache")
    cache = cache if isinstance(cache, dict) else {}
    times = envelope.get("time")
    times = times if isinstance(times, dict) else {}
    agent = envelope.get("agent")
    record = {
        "input": as_int(tokens.get("input")),
        "output": as_int(tokens.get("output")),
        "reasoning": as_int(tokens.get("reasoning")),
        "cache_read": as_int(cache.get("read")),
        "cache_write": as_int(cache.get("write")),
        "created": _optional_ms(times.get("created")),
        "completed": _optional_ms(times.get("completed")),
        "model": _parse_model(envelope.get("model")),
        "effort": _parse_variant(envelope.get("model")),
        "agent": agent if isinstance(agent, str) and agent else None,
    }
    in_flight = mtype == "assistant" and record["completed"] is None
    return record, not in_flight


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
    are NULL on verified live rows), a ``WindowIndex`` over the assistant
    turns (None when the scan failed), and, when ``since_ms`` is given, the token usage inside that
    window.

    Rows are consumed newest-first (``time_created DESC`` with ``seq`` as
    the tie-breaker — ``seq`` alone would interleave multiple owned
    sessions), mirroring the v1 scan's consumption order.
    """
    out: Dict[str, Any] = {
        "interaction_count": 0,
        "work_times": [],
        "current_context_tokens": 0,
        "window": empty_window_usage(),
        # None until a scan succeeds: a failed one must not be published.
        "window_index": None,
        "model": None,
        "effort": None,
        "agent": None,
    }
    ids = [sid for sid in session_ids if sid]
    if not ids:
        return out

    sql, params = _scan_sql(ids)
    try:
        rows = conn.execute(sql, params).fetchall()
        rows.sort(key=lambda row: (row[4], row[3]), reverse=True)
        records = _cached_records(
            conn, "session_message",
            [(row[0], row[1], row[5], row[2]) for row in rows],
            _parse_session_message_record,
        )
    except sqlite3.Error:
        return out

    seen_context = False
    turns: List[Tuple[int, int, int, int, int]] = []
    for msg_id, session_id, mtype, _seq, time_created, _updated in rows:
        record = records.get(msg_id)
        if record is None:
            continue
        if mtype == "user":
            out["interaction_count"] += 1
            continue
        if mtype != "assistant":
            continue

        # v2's assistant data has no `tokens.total` (verified); the context
        # size is the processed prompt — input + cache read + cache write —
        # the semantics AgentSessionStats.current_context_tokens documents.
        # Rows arrive newest-first, and only the active conversation's
        # context is meaningful — an older /new session's is stale.
        if not seen_context and (active_id is None or session_id == active_id):
            context = record["input"] + record["cache_read"] + record["cache_write"]
            if context > 0:
                out["current_context_tokens"] = context
                seen_context = True

        # Newest assistant message wins for the model/agent fallback —
        # but only inside the ACTIVE session: after `/new`, an older
        # tracked session's identities must not leak into the active
        # conversation.
        if active_id is not None and session_id == active_id:
            if out["model"] is None and record["model"]:
                out["model"] = record["model"]
            if out["effort"] is None and record["effort"]:
                out["effort"] = record["effort"]
            if out["agent"] is None and record["agent"]:
                out["agent"] = record["agent"]

        created = record["created"]
        completed = record["completed"]
        if created is not None and completed is not None:
            elapsed = (completed - created) / 1000.0
            if elapsed > 0:
                out["work_times"].append(elapsed)

        turns.append((
            as_int(time_created),
            record["input"],
            record["output"] + record["reasoning"],
            record["cache_write"],
            record["cache_read"],
        ))

    out["work_times"].reverse()
    out["window_index"] = WindowIndex(turns)
    if since_ms is not None:
        out["window"] = out["window_index"].usage(since_ms)
    return out


class Opencode2StatsReader(_OpencodeFamilyReader):
    """Reads opencode2's ``session_v2``/``session_message`` tables for one session.

    Everything but the tables and ``get_stats`` is
    ``opencode_stats._OpencodeFamilyReader`` (the v2 TUI plugin writes the
    same hook-state files as v1's).
    """

    backend_name = "opencode2"
    _message_table = "session_message"
    # Same-directory multi-agent is a supported fleet shape (two opencode2
    # agents in one project): with no plugin ids to discriminate on, every
    # root session started there since launch is a candidate, and adopting
    # more than one would sum a stranger's tokens. Ambiguity degrades to
    # unknown (dashes), never wrong numbers.
    _unique_directory_match = True

    def _fetch_session_rows(self, conn, ids):
        return fetch_session_rows(conn, ids)

    def _fetch_rows_for_directory(self, conn, dirs, since_ms):
        return fetch_rows_for_directory(conn, dirs, since_ms)

    def _scan(self, conn, row_ids, active_id):
        return _scan_messages(conn, row_ids, active_id)

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
                input_tokens += as_int(row["tokens_input"])
                # opencode bills reasoning as output and reports it
                # separately; overcode has no reasoning bucket, so it folds
                # into output rather than silently disappearing.
                output_tokens += as_int(row["tokens_output"]) + as_int(row["tokens_reasoning"])
                cache_read += as_int(row["tokens_cache_read"])
                cache_creation += as_int(row["tokens_cache_write"])

            row_model, row_agent, row_effort = _row_identities(rows)
            row_ids = [row["id"] for row in rows]
            scan = self._scan(conn, row_ids, row_ids[-1])
            # Hand the burn rate this scan (#517) — see opencode_stats.WindowIndex.
            if scan["window_index"] is not None:
                remember_window_index(self._window_key(row_ids), scan["window_index"])

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
                # Reasoning effort is opencode's model variant (#497);
                # same precedence as model.
                effort=row_effort or scan["effort"],
                # Deliberately None: `provider` is overcode's API-transport
                # discriminator ("web"/"bedrock"), not opencode's model
                # provider, and writing opencode's provider id into it
                # would corrupt the session record.
                provider=None,
                per_conversation=self._per_conversation(rows),
            )
        except (sqlite3.Error, OSError, ValueError, KeyError, IndexError):
            return None
        finally:
            conn.close()


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
]
