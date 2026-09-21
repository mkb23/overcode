"""#476 — the opencode stats readers' message scan must stay cheap as an
agent accumulates conversations.

Every ``/new`` adds an id to ``agent_session_ids``. The scan used to fetch
the newest rows of all owned conversations with one
``WHERE session_id IN (...) ORDER BY time_created DESC LIMIT 500*N``.
SQLite answers that through a temp B-tree sorter fed by every candidate
row, so the cost grew ~quadratically with the id count: ~2 ms at one id,
~115 ms at eight on a real store — and the TUI calls it per agent every
second for the burn rate. Per-conversation probes walk the index backwards
and stop at their own limit, so cost is linear in the id count.

The second cost is the row body: on a real store an assistant row carries
its tool output inline (median ~1 KB, tail past 1 MB), and the old scan
fetched and JSON-parsed 500 of them per conversation per call. The scan
now selects only the narrow columns and parses a body once per
``(store, row, time_updated)`` — see ``opencode_stats._cached_records``.

These tests build a store with the live schema AND its indexes (the
reader fixtures elsewhere have none, which hides the difference) and pin:

* the query shape (deterministic),
* the scaling ratio between one and eight owned ids (a timing ratio, but
  a wide one: on this fixture the old shape measures ~22x for opencode
  and ~80-150x for opencode2, the new one ~9-10x for both), and
* the row cache: bodies are fetched once, in-flight rows are not cached,
  a rewritten row is re-read, and stores never share entries.
"""

import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from overcode.backends import opencode2_stats, opencode_stats

pytestmark = pytest.mark.unit

N_SESSIONS = 8
MSGS_PER_SESSION = 2500  # well past the per-session scan limit
PAYLOAD = "x" * 2000  # message.data on a real store is ~0.4-2 KB
LAUNCH = datetime(2026, 9, 1, 9, 0, 0)
LAUNCH_MS = int(LAUNCH.timestamp() * 1000)
DIRECTORY = "/work/project"

LIVE_DDL = """
CREATE TABLE session (
    id TEXT PRIMARY KEY, project_id TEXT, workspace_id TEXT, parent_id TEXT,
    slug TEXT, directory TEXT, path TEXT, title TEXT, version TEXT,
    share_url TEXT, summary_additions INTEGER, summary_deletions INTEGER,
    summary_files INTEGER, summary_diffs TEXT, metadata TEXT, cost REAL,
    tokens_input INTEGER, tokens_output INTEGER, tokens_reasoning INTEGER,
    tokens_cache_read INTEGER, tokens_cache_write INTEGER, revert TEXT,
    permission TEXT, agent TEXT, model TEXT, time_created INTEGER,
    time_updated INTEGER, time_compacting INTEGER, time_archived INTEGER
);
CREATE INDEX session_parent_idx ON session (parent_id);
CREATE TABLE message (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
    time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX message_session_time_created_id_idx
    ON message (session_id, time_created, id);

CREATE TABLE session_v2 (
    id TEXT PRIMARY KEY, project_id TEXT, workspace_id TEXT, parent_id TEXT,
    fork_session_id TEXT, fork_boundary TEXT, slug TEXT, directory TEXT,
    path TEXT, title TEXT, version TEXT, share_url TEXT,
    summary_additions INTEGER, summary_deletions INTEGER,
    summary_files INTEGER, summary_diffs TEXT, metadata TEXT, cost REAL,
    tokens_input INTEGER, tokens_output INTEGER, tokens_reasoning INTEGER,
    tokens_cache_read INTEGER, tokens_cache_write INTEGER, revert TEXT,
    permission TEXT, agent TEXT, model TEXT, time_created INTEGER,
    time_updated INTEGER, time_compacting INTEGER, time_archived INTEGER,
    time_suspended INTEGER, resume_attempts INTEGER, time_idle INTEGER,
    time_viewed INTEGER, idle_outcome TEXT
);
CREATE INDEX session_v2_parent_idx ON session_v2 (parent_id);
CREATE TABLE session_message (
    id TEXT PRIMARY KEY, session_id TEXT, type TEXT, seq INTEGER,
    time_created INTEGER, time_updated INTEGER, data TEXT
);
CREATE UNIQUE INDEX session_message_session_seq_idx
    ON session_message (session_id, seq);
CREATE INDEX session_message_session_type_seq_idx
    ON session_message (session_id, type, seq);
CREATE INDEX session_message_session_time_created_id_idx
    ON session_message (session_id, time_created, id);
"""


# Even-numbered conversations are 3 user : 1 assistant, odd ones 1 : 1,
# so a scan that spends its budget on the wrong conversations produces a
# different interaction count rather than the right one by coincidence.
USER_EVERY = {0: 4, 1: 2}


def sid(n: int) -> str:
    return f"ses_{n:026d}"


def users_in_newest(session: int, rows: int) -> int:
    every = USER_EVERY[session % 2]
    return sum(
        1 for m in range(MSGS_PER_SESSION - rows, MSGS_PER_SESSION) if m % every != 0
    )


@pytest.fixture(scope="module")
def store(tmp_path_factory) -> Path:
    """Eight owned conversations, each entirely newer than the one before.

    Non-interleaved timestamps matter: they are what makes a shared
    ``LIMIT 500*N`` spend its whole budget on the newest conversations.
    """
    path = tmp_path_factory.mktemp("opencode") / "opencode.db"
    conn = sqlite3.connect(path)
    conn.executescript(LIVE_DDL)
    model = json.dumps({"id": "gpt-5", "providerID": "openai"})
    for s in range(N_SESSIONS):
        first = LAUNCH_MS + s * MSGS_PER_SESSION * 1000
        last = first + (MSGS_PER_SESSION - 1) * 1000
        for table in ("session", "session_v2"):
            conn.execute(
                f"INSERT INTO {table} (id, project_id, slug, directory, version,"
                " parent_id, cost, tokens_input, tokens_output, tokens_reasoning,"
                " tokens_cache_read, tokens_cache_write, model, time_created,"
                " time_updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid(s), "prj", "slug", DIRECTORY, "1", None, 1.0,
                 100, 200, 0, 0, 0, model, first, last),
            )
        v1_rows, v2_rows = [], []
        for m in range(MSGS_PER_SESSION):
            t = first + m * 1000
            role = "user" if m % USER_EVERY[s % 2] != 0 else "assistant"
            envelope = {"role": role, "pad": PAYLOAD}
            if role == "assistant":
                envelope["tokens"] = {
                    "input": 10, "output": 20, "reasoning": 0,
                    "total": 5000, "cache": {"read": 1, "write": 2},
                }
                envelope["time"] = {"created": t, "completed": t + 500}
            data = json.dumps(envelope)
            v1_rows.append((f"msg_{s}_{m:06d}", sid(s), t, t, data))
            v2_rows.append((f"msg_{s}_{m:06d}", sid(s), role, m + 1, t, t, data))
        conn.executemany("INSERT INTO message VALUES (?,?,?,?,?)", v1_rows)
        conn.executemany(
            "INSERT INTO session_message VALUES (?,?,?,?,?,?,?)", v2_rows
        )
    conn.commit()
    conn.close()
    return path


def session_with(n_ids: int):
    ids = [sid(i) for i in range(n_ids)]
    return SimpleNamespace(
        agent_session_ids=ids,
        active_agent_session_id=ids[-1],
        start_directory=DIRECTORY,
        start_time=LAUNCH.isoformat(),
        tmux_session=None,
        name=None,
    )


def best_of(fn, runs: int = 3) -> float:
    fn()  # warm the page cache
    return min(_timed(fn) for _ in range(runs))


def _timed(fn) -> float:
    start = time.perf_counter()
    fn()
    return time.perf_counter() - start


# A linear scan lands at 9-10x for eight ids (8x of scan plus the JSON
# parse of eight times the rows); the shared-sort shape measured 22x
# (opencode) and 80-150x (opencode2) on this fixture. 15x splits them.
MAX_SCALING = 15


@pytest.mark.parametrize(
    "module, reader_cls",
    [
        (opencode_stats, opencode_stats.OpencodeStatsReader),
        (opencode2_stats, opencode2_stats.Opencode2StatsReader),
    ],
    ids=["opencode", "opencode2"],
)
class TestScanCost:
    def _reader(self, module, reader_cls, store, monkeypatch):
        if module is opencode_stats:
            return reader_cls(db_path=store)
        monkeypatch.delenv("OPENCODE_DB", raising=False)
        monkeypatch.setenv("OPENCODE_DATA_DIR", str(store.parent))
        return reader_cls()

    def test_stats_cost_is_linear_in_owned_ids(
        self, module, reader_cls, store, monkeypatch
    ):
        reader = self._reader(module, reader_cls, store, monkeypatch)
        assert reader.get_stats(session_with(1)) is not None
        one = best_of(lambda: reader.get_stats(session_with(1)))
        eight = best_of(lambda: reader.get_stats(session_with(N_SESSIONS)))
        ratio = eight / one
        print(
            f"\n{module.__name__}: get_stats 1 id {one*1000:.1f} ms, "
            f"{N_SESSIONS} ids {eight*1000:.1f} ms, ratio {ratio:.1f}x"
        )
        assert ratio < MAX_SCALING, (
            f"scan cost grew {ratio:.1f}x from 1 to {N_SESSIONS} owned ids "
            f"({one*1000:.1f} ms -> {eight*1000:.1f} ms); expected ~linear"
        )

    def test_window_cost_is_linear_in_owned_ids(
        self, module, reader_cls, store, monkeypatch
    ):
        reader = self._reader(module, reader_cls, store, monkeypatch)
        one = best_of(lambda: reader.get_window_token_usage(session_with(1), LAUNCH))
        eight = best_of(
            lambda: reader.get_window_token_usage(session_with(N_SESSIONS), LAUNCH)
        )
        ratio = eight / one
        print(
            f"\n{module.__name__}: window 1 id {one*1000:.1f} ms, "
            f"{N_SESSIONS} ids {eight*1000:.1f} ms, ratio {ratio:.1f}x"
        )
        assert ratio < MAX_SCALING, (
            f"window cost grew {ratio:.1f}x from 1 to {N_SESSIONS} owned ids "
            f"({one*1000:.1f} ms -> {eight*1000:.1f} ms); expected ~linear"
        )

    def test_every_conversation_gets_its_own_scan_budget(
        self, module, reader_cls, store, monkeypatch
    ):
        """``_MESSAGE_SCAN_LIMIT`` is documented per session.

        With one shared ``LIMIT 500*N`` the newest conversations swallowed
        the whole budget (here: two of the eight), so interaction counts
        silently dropped the older ones.
        """
        reader = self._reader(module, reader_cls, store, monkeypatch)
        stats = reader.get_stats(session_with(N_SESSIONS))
        expected = sum(
            users_in_newest(s, module._MESSAGE_SCAN_LIMIT) for s in range(N_SESSIONS)
        )
        assert stats.interaction_count == expected
        # The active (newest) conversation still supplies the live context.
        assert stats.current_context_tokens > 0

    def test_scan_sql_probes_each_session_under_its_own_limit(
        self, module, reader_cls
    ):
        ids = [sid(i) for i in range(3)]
        sql, params = module._scan_sql(ids)
        assert "session_id IN" not in sql
        assert sql.count("session_id = ?") == len(ids)
        assert sql.count("LIMIT ?") == len(ids)
        assert params.count(module._MESSAGE_SCAN_LIMIT) == len(ids)
        for one_id in ids:
            assert one_id in params


# ── row cache ────────────────────────────────────────────────────────


def _small_store(path: Path, *, v2: bool, completed: bool = True, tokens_in: int = 100) -> None:
    """One conversation: a user turn and one assistant turn."""
    conn = sqlite3.connect(path)
    conn.executescript(LIVE_DDL)
    t = LAUNCH_MS + 1000
    times = {"created": t + 500}
    if completed:
        times["completed"] = t + 2500
    assistant = {
        "tokens": {"input": tokens_in, "output": 5, "reasoning": 0,
                   "total": tokens_in + 5, "cache": {"read": 0, "write": 0}},
        "time": times,
        "model": {"id": "gpt-5", "providerID": "openai"},
        "agent": "build",
    }
    if v2:
        conn.execute(
            "INSERT INTO session_v2 (id, project_id, slug, directory, version,"
            " parent_id, cost, time_created, time_updated)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (sid(0), "prj", "slug", DIRECTORY, "1", None, 0.0, t, t + 2500),
        )
        conn.execute(
            "INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
            ("msg_u", sid(0), "user", 1, t, t, json.dumps({"text": "hi", "time": {"created": t}})),
        )
        conn.execute(
            "INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
            ("msg_a", sid(0), "assistant", 2, t + 500, t + 2500, json.dumps(assistant)),
        )
    else:
        conn.execute(
            "INSERT INTO session (id, project_id, slug, directory, version,"
            " parent_id, cost, time_created, time_updated)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (sid(0), "prj", "slug", DIRECTORY, "1", None, 0.0, t, t + 2500),
        )
        conn.execute(
            "INSERT INTO message VALUES (?,?,?,?,?)",
            ("msg_u", sid(0), t, t, json.dumps({"role": "user", "time": {"created": t}})),
        )
        conn.execute(
            "INSERT INTO message VALUES (?,?,?,?,?)",
            ("msg_a", sid(0), t + 500, t + 2500, json.dumps({"role": "assistant", **assistant})),
        )
    conn.commit()
    conn.close()


def _rewrite(path: Path, table: str, msg_id: str, data: dict, time_updated: int) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        f"UPDATE {table} SET data = ?, time_updated = ? WHERE id = ?",
        (json.dumps(data), time_updated, msg_id),
    )
    conn.commit()
    conn.close()


@pytest.mark.parametrize("v2", [False, True], ids=["opencode", "opencode2"])
class TestRowCache:
    @pytest.fixture(autouse=True)
    def _fresh_cache(self):
        opencode_stats.clear_row_cache()
        yield
        opencode_stats.clear_row_cache()

    @pytest.fixture
    def fetched(self, monkeypatch):
        """Ids whose bodies each scan actually fetched, in call order."""
        calls: list = []
        real = opencode_stats._fetch_data

        def counting(conn, table, ids):
            calls.append(list(ids))
            return real(conn, table, ids)

        monkeypatch.setattr(opencode_stats, "_fetch_data", counting)
        return calls

    def _reader(self, v2, path, monkeypatch):
        if not v2:
            return opencode_stats.OpencodeStatsReader(db_path=path)
        monkeypatch.delenv("OPENCODE_DB", raising=False)
        monkeypatch.setenv("OPENCODE_DATA_DIR", str(path.parent))
        return opencode2_stats.Opencode2StatsReader()

    def test_bodies_are_fetched_once_per_version(self, v2, tmp_path, monkeypatch, fetched):
        path = tmp_path / "opencode.db"
        _small_store(path, v2=v2)
        reader = self._reader(v2, path, monkeypatch)
        first = reader.get_stats(session_with(1))
        assert sorted(fetched[-1]) == ["msg_a", "msg_u"]
        fetched.clear()
        second = reader.get_stats(session_with(1))
        assert fetched == []  # every row served from the cache, no body query at all
        assert second.interaction_count == first.interaction_count == 1
        assert second.work_times == first.work_times == [2.0]

    def test_in_flight_assistant_row_is_re_read_until_it_completes(
        self, v2, tmp_path, monkeypatch, fetched
    ):
        path = tmp_path / "opencode.db"
        _small_store(path, v2=v2, completed=False)
        reader = self._reader(v2, path, monkeypatch)
        assert reader.get_stats(session_with(1)).work_times == []
        fetched.clear()
        reader.get_stats(session_with(1))
        assert fetched == [["msg_a"]]  # the user row is cached, the live turn is not

        table = "session_message" if v2 else "message"
        done = {
            "role": "assistant",
            "tokens": {"input": 100, "output": 5, "reasoning": 0, "total": 105,
                       "cache": {"read": 0, "write": 0}},
            "time": {"created": LAUNCH_MS + 1500, "completed": LAUNCH_MS + 3500},
            "model": {"id": "gpt-5", "providerID": "openai"},
            "agent": "build",
        }
        _rewrite(path, table, "msg_a", done, LAUNCH_MS + 3500)
        assert reader.get_stats(session_with(1)).work_times == [2.0]
        fetched.clear()
        reader.get_stats(session_with(1))
        assert fetched == []  # completed: now cached like any other row

    def test_rewritten_row_is_re_parsed(self, v2, tmp_path, monkeypatch, fetched):
        path = tmp_path / "opencode.db"
        _small_store(path, v2=v2, tokens_in=100)
        reader = self._reader(v2, path, monkeypatch)
        before = reader.get_window_token_usage(session_with(1), LAUNCH)
        assert before["input_tokens"] == 100

        table = "session_message" if v2 else "message"
        bumped = {
            "role": "assistant",
            "tokens": {"input": 900, "output": 5, "reasoning": 0, "total": 905,
                       "cache": {"read": 0, "write": 0}},
            "time": {"created": LAUNCH_MS + 1500, "completed": LAUNCH_MS + 9000},
            "model": {"id": "gpt-5", "providerID": "openai"},
            "agent": "build",
        }
        _rewrite(path, table, "msg_a", bumped, LAUNCH_MS + 9000)
        fetched.clear()
        after = reader.get_window_token_usage(session_with(1), LAUNCH)
        assert after["input_tokens"] == 900
        assert fetched == [["msg_a"]]  # only the rewritten row was re-read

    def test_stores_do_not_share_entries(self, v2, tmp_path, monkeypatch):
        """Same row ids in two files (tests, a copied store) stay distinct."""
        a = tmp_path / "a" / "opencode.db"
        b = tmp_path / "b" / "opencode.db"
        a.parent.mkdir()
        b.parent.mkdir()
        _small_store(a, v2=v2, tokens_in=100)
        _small_store(b, v2=v2, tokens_in=700)
        usage_a = self._reader(v2, a, monkeypatch).get_window_token_usage(session_with(1), LAUNCH)
        usage_b = self._reader(v2, b, monkeypatch).get_window_token_usage(session_with(1), LAUNCH)
        assert (usage_a["input_tokens"], usage_b["input_tokens"]) == (100, 700)

    def test_cache_stays_bounded(self, v2, tmp_path, monkeypatch):
        path = tmp_path / "opencode.db"
        _small_store(path, v2=v2)
        monkeypatch.setattr(opencode_stats, "_ROW_CACHE_MAX", 4)
        for n in range(6):
            opencode_stats._row_cache[("x", "t", f"filler{n}")] = (0, {})
        self._reader(v2, path, monkeypatch).get_stats(session_with(1))
        assert len(opencode_stats._row_cache) <= 6  # oldest half evicted, new rows in
        assert any(key[2] == "msg_a" for key in opencode_stats._row_cache)
