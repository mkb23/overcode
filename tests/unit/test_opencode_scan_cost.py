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
        opencode_stats.clear_window_indexes()
        yield
        opencode_stats.clear_row_cache()
        opencode_stats.clear_window_indexes()

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
        # This pins the ROW layer; a window index younger than its max age
        # (#517) would answer without reaching the rows at all.
        opencode_stats.clear_window_indexes()
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


# ── #517: the burn rate reuses the stats scan ──────────────────────────
#
# The status bar asks every agent for its window usage once a second; each
# answer used to be a full scan. get_stats (every 5 s, never cached) now
# publishes a WindowIndex that window calls reuse for up to
# WINDOW_INDEX_MAX_AGE_SECONDS, inside the burn rate's 5-30 s freshness
# contract. These pin: the stats columns stay exactly as fresh as before,
# the window is exact for any `since`, and reuse is scoped and bounded.


def _assistant(tokens_in: int, completed_ms: int) -> dict:
    return {
        "role": "assistant",
        "tokens": {"input": tokens_in, "output": 5, "reasoning": 0,
                   "total": tokens_in + 5, "cache": {"read": 0, "write": 0}},
        "time": {"created": LAUNCH_MS + 1500, "completed": completed_ms},
        "model": {"id": "gpt-5", "providerID": "openai"},
        "agent": "build",
    }


@pytest.mark.parametrize("v2", [False, True], ids=["opencode", "opencode2"])
class TestWindowIndexReuse:
    @pytest.fixture
    def scans(self, v2, monkeypatch):
        """How many full message scans the reader ran."""
        module = opencode2_stats if v2 else opencode_stats
        real = module._scan_messages
        count = {"n": 0}

        def counting(*args, **kwargs):
            count["n"] += 1
            return real(*args, **kwargs)

        monkeypatch.setattr(module, "_scan_messages", counting)
        return count

    @pytest.fixture
    def clock(self, monkeypatch):
        """A hand-driven monotonic clock for the index's max age."""
        now = {"t": 1000.0}
        monkeypatch.setattr(opencode_stats, "window_index_clock", lambda: now["t"])
        return now

    def _small(self, v2, tmp_path, monkeypatch, name="opencode.db"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _small_store(path, v2=v2, tokens_in=100)
        if v2:
            return path, opencode2_stats.Opencode2StatsReader(db_path=path)
        return path, opencode_stats.OpencodeStatsReader(db_path=path)

    @staticmethod
    def _table(v2):
        return "session_message" if v2 else "message"

    def test_window_after_get_stats_does_not_scan(self, v2, tmp_path, monkeypatch, scans):
        _path, reader = self._small(v2, tmp_path, monkeypatch)
        reader.get_stats(session_with(1))
        assert scans["n"] == 1
        usage = reader.get_window_token_usage(session_with(1), LAUNCH)
        assert scans["n"] == 1  # served by the index get_stats published
        assert usage["input_tokens"] == 100

    def test_sliding_since_answers_from_one_scan(self, v2, tmp_path, monkeypatch, scans):
        _path, reader = self._small(v2, tmp_path, monkeypatch)
        # The assistant turn is created at LAUNCH_MS + 1500.
        inside = reader.get_window_token_usage(session_with(1), LAUNCH)
        at_turn = reader.get_window_token_usage(
            session_with(1), datetime.fromtimestamp((LAUNCH_MS + 1500) / 1000)
        )
        past_turn = reader.get_window_token_usage(
            session_with(1), datetime.fromtimestamp((LAUNCH_MS + 1501) / 1000)
        )
        assert scans["n"] == 1
        assert inside["input_tokens"] == 100
        assert at_turn["input_tokens"] == 100  # the window start is inclusive
        assert past_turn == opencode_stats.empty_window_usage()

    def test_get_stats_is_never_served_from_the_index(
        self, v2, tmp_path, monkeypatch, scans
    ):
        """The token/context columns keep their 5 s freshness: a rewrite shows
        on the very next get_stats, even inside the window index's max age."""
        path, reader = self._small(v2, tmp_path, monkeypatch)
        reader.get_window_token_usage(session_with(1), LAUNCH)
        before = reader.get_stats(session_with(1))
        _rewrite(path, self._table(v2), "msg_a", _assistant(900, LAUNCH_MS + 9000),
                 LAUNCH_MS + 9000)
        after = reader.get_stats(session_with(1))
        assert scans["n"] == 3
        assert after.current_context_tokens != before.current_context_tokens
        assert after.current_context_tokens >= 900

    def test_get_stats_refreshes_the_window(self, v2, tmp_path, monkeypatch, scans, clock):
        path, reader = self._small(v2, tmp_path, monkeypatch)
        assert reader.get_window_token_usage(session_with(1), LAUNCH)["input_tokens"] == 100
        _rewrite(path, self._table(v2), "msg_a", _assistant(900, LAUNCH_MS + 9000),
                 LAUNCH_MS + 9000)
        clock["t"] += 3
        # Inside the max age the window may lag a rewrite...
        assert reader.get_window_token_usage(session_with(1), LAUNCH)["input_tokens"] == 100
        # ...until the next stats tick scans, which the window then reuses.
        reader.get_stats(session_with(1))
        scanned = scans["n"]
        assert reader.get_window_token_usage(session_with(1), LAUNCH)["input_tokens"] == 900
        assert scans["n"] == scanned

    def test_window_rescans_at_max_age(self, v2, tmp_path, monkeypatch, scans, clock):
        path, reader = self._small(v2, tmp_path, monkeypatch)
        reader.get_window_token_usage(session_with(1), LAUNCH)
        _rewrite(path, self._table(v2), "msg_a", _assistant(900, LAUNCH_MS + 9000),
                 LAUNCH_MS + 9000)
        clock["t"] += opencode_stats.WINDOW_INDEX_MAX_AGE_SECONDS - 0.01
        assert reader.get_window_token_usage(session_with(1), LAUNCH)["input_tokens"] == 100
        assert scans["n"] == 1
        clock["t"] += 0.01
        assert reader.get_window_token_usage(session_with(1), LAUNCH)["input_tokens"] == 900
        assert scans["n"] == 2

    def test_max_age_is_inside_the_burn_freshness_contract(self, v2):
        # Burn/μ window averages: 5-30 s (the scaling audit's contract).
        assert 5 <= opencode_stats.WINDOW_INDEX_MAX_AGE_SECONDS <= 30

    def test_status_bar_cadence_scans_once_per_stats_tick(
        self, v2, tmp_path, monkeypatch, scans, clock
    ):
        """The TUI's shape: get_stats every 5 s, the window every second."""
        _path, reader = self._small(v2, tmp_path, monkeypatch)
        for _tick in range(4):
            reader.get_stats(session_with(1))
            for _second in range(5):
                reader.get_window_token_usage(session_with(1), LAUNCH)
                clock["t"] += 1
        assert scans["n"] == 4  # was 4 + 20

    def test_a_new_conversation_misses(self, v2, store, monkeypatch, scans):
        reader = (opencode2_stats.Opencode2StatsReader if v2
                  else opencode_stats.OpencodeStatsReader)(db_path=store)
        reader.get_stats(session_with(1))
        reader.get_window_token_usage(session_with(2), LAUNCH)  # after /new
        assert scans["n"] == 2

    def test_stores_do_not_share_indexes(self, v2, tmp_path, monkeypatch, scans):
        _a, reader_a = self._small(v2, tmp_path / "a", monkeypatch)
        b, _reader_b = self._small(v2, tmp_path / "b", monkeypatch)
        _rewrite(b, self._table(v2), "msg_a", _assistant(900, LAUNCH_MS + 9000),
                 LAUNCH_MS + 9000)
        reader_b = type(reader_a)(db_path=b)
        reader_a.get_stats(session_with(1))
        assert reader_b.get_window_token_usage(session_with(1), LAUNCH)["input_tokens"] == 900
        assert scans["n"] == 2

    def test_window_matches_a_full_scan_for_any_since(self, v2, store):
        """The index answers exactly what a per-call scan summed, for window
        starts before, inside, on and just past assistant turns."""
        module = opencode2_stats if v2 else opencode_stats
        reader = (opencode2_stats.Opencode2StatsReader if v2
                  else opencode_stats.OpencodeStatsReader)(db_path=store)
        session = session_with(3)
        ids = session.agent_session_ids
        conn = module.connect(store)
        try:
            span = 3 * MSGS_PER_SESSION * 1000
            starts = [LAUNCH_MS - 1, LAUNCH_MS + span + 1]
            for offset in (0.1, 0.4, 0.5, 0.75, 0.97):
                t = LAUNCH_MS + int(span * offset) // 1000 * 1000
                starts += [t - 1, t, t + 1]
            for since_ms in starts:
                since = datetime.fromtimestamp(since_ms / 1000)
                got = reader.get_window_token_usage(session, since)
                # Same ms the reader converts `since` to.
                expected = module._scan_messages(
                    conn, ids, ids[-1], since_ms=int(since.timestamp() * 1000)
                )["window"]
                assert got == expected, since_ms
        finally:
            conn.close()

    def test_v1_and_v2_indexes_do_not_collide(self, v2, store, scans):
        """Both tables hold the same ids in the fixture store (and v1/v2 share
        a file in real life): one reader's index must not answer the other."""
        other = (opencode_stats.OpencodeStatsReader if v2
                 else opencode2_stats.Opencode2StatsReader)(db_path=store)
        reader = (opencode2_stats.Opencode2StatsReader if v2
                  else opencode_stats.OpencodeStatsReader)(db_path=store)
        other.get_stats(session_with(1))
        reader.get_window_token_usage(session_with(1), LAUNCH)
        assert scans["n"] == 1  # only this reader's own scan is counted

    def test_callers_cannot_corrupt_the_index(self, v2, tmp_path, monkeypatch):
        _path, reader = self._small(v2, tmp_path, monkeypatch)
        first = reader.get_window_token_usage(session_with(1), LAUNCH)
        first["input_tokens"] = 999_999
        assert reader.get_window_token_usage(session_with(1), LAUNCH)["input_tokens"] == 100

    def test_a_transient_scan_error_is_not_published(self, v2, tmp_path, monkeypatch):
        """A locked store answers 'nothing' for that call only — never an
        empty index that would zero the burn rate for the max age."""
        _path, reader = self._small(v2, tmp_path, monkeypatch)
        real = opencode_stats._fetch_data
        monkeypatch.setattr(
            opencode_stats, "_fetch_data",
            lambda *a: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")),
        )
        assert reader.get_window_token_usage(session_with(1), LAUNCH) == (
            opencode_stats.empty_window_usage()
        )
        reader.get_stats(session_with(1))
        assert opencode_stats._window_indexes == {}
        monkeypatch.setattr(opencode_stats, "_fetch_data", real)
        assert reader.get_window_token_usage(session_with(1), LAUNCH)["input_tokens"] == 100

    def test_failed_scan_publishes_nothing(self, v2, tmp_path, monkeypatch):
        reader = (opencode2_stats.Opencode2StatsReader if v2
                  else opencode_stats.OpencodeStatsReader)(db_path=tmp_path / "absent.db")
        assert reader.get_window_token_usage(session_with(1), LAUNCH) == (
            opencode_stats.empty_window_usage()
        )
        assert reader.get_stats(session_with(1)) is None
        assert opencode_stats._window_indexes == {}


class TestWindowIndex:
    def test_empty(self):
        assert opencode_stats.WindowIndex(()).usage(0) == opencode_stats.empty_window_usage()

    def test_sums_turns_at_or_after_since_whatever_the_input_order(self):
        index = opencode_stats.WindowIndex([
            (300, 3, 30, 300, 3000),
            (100, 1, 10, 100, 1000),
            (200, 2, 20, 200, 2000),
            (200, 4, 40, 400, 4000),  # a tie on time_created
        ])
        assert index.usage(0) == {"input_tokens": 10, "output_tokens": 100,
                                  "cache_creation_tokens": 1000,
                                  "cache_read_tokens": 10000}
        assert index.usage(200)["input_tokens"] == 9  # both tied turns, inclusive
        assert index.usage(201)["input_tokens"] == 3
        assert index.usage(301) == opencode_stats.empty_window_usage()

    def test_keys_match_the_burn_rate_shape(self):
        assert set(opencode_stats.WindowIndex(()).usage(0)) == set(
            opencode_stats.empty_window_usage()
        )


class TestWindowIndexStore:
    @pytest.fixture
    def clock(self, monkeypatch):
        now = {"t": 1000.0}
        monkeypatch.setattr(opencode_stats, "window_index_clock", lambda: now["t"])
        return now

    def test_bounded_stale_entries_go_first(self, clock):
        cap = opencode_stats._WINDOW_INDEX_MAX_ENTRIES
        index = opencode_stats.WindowIndex(())
        for i in range(cap):
            opencode_stats.remember_window_index(("stale", i), index)
        clock["t"] += opencode_stats.WINDOW_INDEX_MAX_AGE_SECONDS
        opencode_stats.remember_window_index(("live", 0), index)
        assert len(opencode_stats._window_indexes) == 1
        assert opencode_stats.recent_window_index(("live", 0)) is index

    def test_bounded_when_every_entry_is_live(self, clock):
        cap = opencode_stats._WINDOW_INDEX_MAX_ENTRIES
        index = opencode_stats.WindowIndex(())
        for i in range(cap + 1):
            clock["t"] += 0.001
            opencode_stats.remember_window_index(("k", i), index)
        assert len(opencode_stats._window_indexes) <= cap
        assert opencode_stats.recent_window_index(("k", cap)) is index  # newest kept
        assert opencode_stats.recent_window_index(("k", 0)) is None  # oldest dropped

    def test_concurrent_publishers(self):
        import threading

        index = opencode_stats.WindowIndex(())
        errors = []

        def publish(worker):
            try:
                for i in range(2000):
                    opencode_stats.remember_window_index((worker, i), index)
                    opencode_stats.recent_window_index((worker, i // 2))
            except Exception as exc:  # pragma: no cover - the failure mode
                errors.append(exc)

        threads = [threading.Thread(target=publish, args=(w,)) for w in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert len(opencode_stats._window_indexes) <= opencode_stats._WINDOW_INDEX_MAX_ENTRIES
