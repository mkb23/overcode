"""A synthetic opencode (v1) SQLite store: the schema, and a writer for it.

One source for the tables ``backends/opencode_stats.py`` reads:

* the unit tests build fixture databases from ``SESSION_DDL`` /
  ``MESSAGE_DDL``;
* ``tests/mock_opencode.py`` writes conversations through ``MockStore``,
  so the container tier's daemon learns a mock agent's ``ses_…`` id, its
  tokens and its cost the way it does from real opencode.

``SESSION_DDL`` / ``MESSAGE_DDL`` are verbatim copies of the ``CREATE TABLE``
statements read out of a live opencode v1.18.19 store
(``sqlite3 ~/.local/share/opencode/opencode.db "select sql from sqlite_master"``),
so a fixture drifts only when opencode does. Stdlib only: the mock imports
this from a tmux pane, outside pytest.
"""

import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

SESSION_DDL = """
CREATE TABLE `session` (
  `id` text PRIMARY KEY,
  `project_id` text NOT NULL,
  `workspace_id` text,
  `parent_id` text,
  `slug` text NOT NULL,
  `directory` text NOT NULL,
  `path` text,
  `title` text NOT NULL,
  `version` text NOT NULL,
  `share_url` text,
  `summary_additions` integer,
  `summary_deletions` integer,
  `summary_files` integer,
  `summary_diffs` text,
  `metadata` text,
  `cost` real DEFAULT 0 NOT NULL,
  `tokens_input` integer DEFAULT 0 NOT NULL,
  `tokens_output` integer DEFAULT 0 NOT NULL,
  `tokens_reasoning` integer DEFAULT 0 NOT NULL,
  `tokens_cache_read` integer DEFAULT 0 NOT NULL,
  `tokens_cache_write` integer DEFAULT 0 NOT NULL,
  `revert` text,
  `permission` text,
  `agent` text,
  `model` text,
  `time_created` integer NOT NULL,
  `time_updated` integer NOT NULL,
  `time_compacting` integer,
  `time_archived` integer
)
"""

MESSAGE_DDL = """
CREATE TABLE `message` (
  `id` text PRIMARY KEY,
  `session_id` text NOT NULL,
  `time_created` integer NOT NULL,
  `time_updated` integer NOT NULL,
  `data` text NOT NULL
)
"""

# The index the live store keeps on message (the reader's scan walks it).
MESSAGE_INDEX_DDL = (
    "CREATE INDEX `message_session_time_created_id_idx` "
    "ON `message` (`session_id`, `time_created`, `id`)"
)

DEFAULT_MODEL = "openai/gpt-4o-mini"
OPENCODE_VERSION = "1.18.19"


def model_json(qualified: Optional[str]) -> str:
    """opencode's ``session.model`` JSON for ``provider/model``."""
    provider, _, model_id = (qualified or DEFAULT_MODEL).partition("/")
    if not model_id:
        provider, model_id = "openai", provider
    return json.dumps({"id": model_id, "providerID": provider})


def new_id(prefix: str) -> str:
    """An opencode-shaped id (``ses_…``, ``msg_…``): time-ordered, then random."""
    return f"{prefix}_{time.time_ns() // 1000:x}{secrets.token_hex(7)}"


def now_ms() -> int:
    return int(time.time() * 1000)


def session_row(sid: str, directory: str, **overrides: Any) -> Dict[str, Any]:
    """A ``session`` row with opencode's shape; ``overrides`` win."""
    created = now_ms()
    row: Dict[str, Any] = {
        "id": sid,
        "project_id": "prj_1",
        "workspace_id": None,
        "parent_id": None,
        "slug": "happy-otter",
        "directory": directory,
        "path": directory.lstrip("/"),
        "title": "Session",
        "version": OPENCODE_VERSION,
        "share_url": None,
        "summary_additions": None,
        "summary_deletions": None,
        "summary_files": None,
        "summary_diffs": None,
        "metadata": None,
        "cost": 0.0,
        "tokens_input": 0,
        "tokens_output": 0,
        "tokens_reasoning": 0,
        "tokens_cache_read": 0,
        "tokens_cache_write": 0,
        "revert": None,
        "permission": None,
        "agent": "build",
        "model": model_json(None),
        "time_created": created,
        "time_updated": created,
        "time_compacting": None,
        "time_archived": None,
    }
    row.update(overrides)
    return row


def insert_session_row(conn: sqlite3.Connection, row: Dict[str, Any]) -> None:
    columns = ", ".join(f"`{k}`" for k in row)
    conn.execute(
        f"INSERT INTO session ({columns}) VALUES ({', '.join('?' * len(row))})",
        tuple(row.values()),
    )


def insert_message(conn, msg_id, sid, data, time_created, time_updated=None):
    conn.execute(
        "INSERT INTO message (id, session_id, time_created, time_updated, data) "
        "VALUES (?, ?, ?, ?, ?)",
        (msg_id, sid, time_created,
         time_created if time_updated is None else time_updated,
         json.dumps(data)),
    )


def create_schema(conn: sqlite3.Connection) -> None:
    """Create the tables unless they are already there."""
    present = {name for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'index')")}
    if "session" not in present:
        conn.execute(SESSION_DDL)
    if "message" not in present:
        conn.execute(MESSAGE_DDL)
    if "message_session_time_created_id_idx" not in present:
        conn.execute(MESSAGE_INDEX_DDL)


def configured_path(env=None) -> Optional[Path]:
    """The store a mock may write: only one named by opencode's own env vars.

    ``OPENCODE_DB`` names the file, ``OPENCODE_DATA_DIR`` the directory
    holding ``opencode.db`` (what ``opencode_stats.database_path`` honours).
    Never the default ``~/.local/share/opencode``: a mock run on a host
    without them must not write into the user's real opencode store.
    """
    env = os.environ if env is None else env
    if env.get("OPENCODE_DB"):
        return Path(env["OPENCODE_DB"])
    if env.get("OPENCODE_DATA_DIR"):
        return Path(env["OPENCODE_DATA_DIR"]) / "opencode.db"
    return None


class SessionNotFound(Exception):
    """``--session <id>`` named a conversation the store does not have."""


class MockStore:
    """Writes conversations the way opencode does, one connection per call.

    Several mock agents share one store (one HOME per test), so each write
    is its own short transaction with a generous busy timeout.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._write() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            create_schema(conn)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """One committed transaction on a connection that is then closed."""
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def session(self, sid: str) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM session WHERE id = ?", (sid,)).fetchone()
            return dict(row) if row is not None else None
        finally:
            conn.close()

    def create_session(self, directory: str, model: Optional[str] = None,
                       title: str = "Session") -> str:
        """A new root conversation, as opencode creates on the first prompt."""
        sid = new_id("ses")
        with self._write() as conn:
            insert_session_row(conn, session_row(
                sid, directory, title=title, model=model_json(model)))
        return sid

    def fork_session(self, source_id: str, directory: str) -> str:
        """``--session <id> --fork``: a new root conversation titled
        ``<title> (fork #1)`` holding copies of the source's messages.

        A fork is not a child: opencode gives it no ``parent_id`` (that is
        the ``task`` tool's sub-agents), and its own totals start at zero.
        """
        source = self.session(source_id)
        if source is None:
            raise SessionNotFound(source_id)
        sid = new_id("ses")
        with self._write() as conn:
            insert_session_row(conn, session_row(
                sid, directory, title=f"{source['title']} (fork #1)",
                model=source["model"]))
            rows = conn.execute(
                "SELECT time_created, time_updated, data FROM message "
                "WHERE session_id = ? ORDER BY time_created", (source_id,)).fetchall()
            for created, updated, data in rows:
                insert_message(conn, new_id("msg"), sid, json.loads(data), created, updated)
        return sid

    def add_user_message(self, sid: str, text: str) -> str:
        msg_id = new_id("msg")
        created = now_ms()
        with self._write() as conn:
            insert_message(conn, msg_id, sid, {
                "id": msg_id, "sessionID": sid, "role": "user",
                "time": {"created": created},
                "summary": {"title": text[:60]},
            }, created)
            conn.execute("UPDATE session SET time_updated = ? WHERE id = ?",
                         (created, sid))
        return msg_id

    def add_assistant_turn(self, sid: str, cost: float, input_tokens: int,
                           output_tokens: int, reasoning_tokens: int = 0,
                           cache_read: int = 0, cache_write: int = 0,
                           duration_ms: int = 1500) -> str:
        """One completed assistant turn, rolled into the session's totals."""
        msg_id = new_id("msg")
        completed = now_ms()
        created = completed - duration_ms
        source = self.session(sid) or {}
        model = json.loads(source.get("model") or model_json(None))
        total = input_tokens + output_tokens + reasoning_tokens + cache_read + cache_write
        with self._write() as conn:
            insert_message(conn, msg_id, sid, {
                "id": msg_id, "sessionID": sid, "role": "assistant",
                "cost": cost,
                "tokens": {
                    "total": total,
                    "input": input_tokens,
                    "output": output_tokens,
                    "reasoning": reasoning_tokens,
                    "cache": {"read": cache_read, "write": cache_write},
                },
                "modelID": model.get("id"),
                "providerID": model.get("providerID"),
                "time": {"created": created, "completed": completed},
            }, created, completed)
            conn.execute(
                "UPDATE session SET cost = cost + ?, tokens_input = tokens_input + ?, "
                "tokens_output = tokens_output + ?, tokens_reasoning = tokens_reasoning + ?, "
                "tokens_cache_read = tokens_cache_read + ?, "
                "tokens_cache_write = tokens_cache_write + ?, time_updated = ? "
                "WHERE id = ?",
                (cost, input_tokens, output_tokens, reasoning_tokens, cache_read,
                 cache_write, completed, sid))
        return msg_id
