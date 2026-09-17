"""HermesStatsReader against a fixture state.db built from the real schema.

The tables are created with the exact column subset the reader reads
(``EXPECTED_SESSION_COLUMNS``/``EXPECTED_MESSAGE_COLUMNS``) plus a few
neighbours, and rows are shaped like the ones read off a live v0.21.3
store on 2026-09-17 (see the module docstring of ``hermes_stats.py``).
"""

import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.backends.hermes_stats import (
    EXPECTED_MESSAGE_COLUMNS,
    EXPECTED_SESSION_COLUMNS,
    HermesStatsReader,
    SESSION_ID_RE,
    _WindowSampler,
    connect,
    missing_columns,
    schema_findings,
    session_ids_from_hook_state,
)
from overcode.stats_reader import empty_window_usage


SCHEMA = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, user_id TEXT, model TEXT,
    model_config TEXT, parent_session_id TEXT, started_at REAL NOT NULL,
    ended_at REAL, end_reason TEXT, message_count INTEGER DEFAULT 0,
    tool_call_count INTEGER DEFAULT 0, input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0, cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0, reasoning_tokens INTEGER DEFAULT 0,
    cwd TEXT, estimated_cost_usd REAL, actual_cost_usd REAL, cost_status TEXT,
    cost_source TEXT, title TEXT, api_call_count INTEGER DEFAULT 0
);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, role TEXT NOT NULL,
    content TEXT, tool_name TEXT, timestamp REAL NOT NULL, token_count INTEGER,
    active INTEGER DEFAULT 1
);
"""

LAUNCH = datetime(2026, 9, 17, 13, 17, 0)
T0 = LAUNCH.timestamp() + 21  # session started 21s after launch
CWD = "/tmp/probe-hermes"


def anchor(prompt_tokens: int) -> str:
    return json.dumps({
        "max_iterations": 500,
        "_usage_anchor": {"prompt_tokens": prompt_tokens, "completion_tokens": 42},
    })


def make_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def add_session(conn, sid, *, started=T0, cwd=CWD, source="cli", parent=None,
                inp=12786, out=559, cache=36224, reasoning=384, cost=0.0,
                cost_status="unknown", model="gpt-5-mini", prompt_tokens=12256):
    conn.execute(
        "INSERT INTO sessions (id, source, model, model_config, parent_session_id, started_at, "
        "input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, reasoning_tokens, "
        "cwd, estimated_cost_usd, cost_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sid, source, model, anchor(prompt_tokens), parent, started, inp, out, cache, 0,
         reasoning, cwd, cost, cost_status),
    )


def add_turns(conn, sid, base=T0):
    # user -> assistant(tool_calls) -> tool -> assistant(stop) ; user -> assistant
    rows = [
        ("user", base + 0.0), ("assistant", base + 6.0), ("tool", base + 8.0),
        ("assistant", base + 9.0), ("user", base + 11.0), ("assistant", base + 15.0),
    ]
    for role, ts in rows:
        conn.execute("INSERT INTO messages (session_id, role, timestamp) VALUES (?,?,?)", (sid, role, ts))


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "state.db"
    conn = make_db(path)
    yield conn, path
    conn.close()


def session(**overrides):
    defaults = dict(
        id="oc-uuid-1", name="hm", tmux_session="agents",
        start_directory=CWD, start_time=LAUNCH.isoformat(),
        agent_session_ids=[], active_agent_session_id=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestSchema:
    def test_fixture_matches_expected_columns(self, db):
        conn, _ = db
        assert missing_columns(conn) == {}

    def test_drift_is_reported(self, tmp_path, monkeypatch):
        path = tmp_path / "state.db"
        conn = sqlite3.connect(path)
        conn.executescript(SCHEMA.replace("cost_status TEXT,", ""))
        conn.commit()
        conn.close()
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        findings = schema_findings()
        assert len(findings) == 1 and "cost_status" in findings[0]

    def test_no_database_no_findings(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "nowhere"))
        assert schema_findings() == []
        assert connect(tmp_path / "missing.db") is None

    def test_expected_column_lists_are_frozen(self):
        assert "cost_status" in EXPECTED_SESSION_COLUMNS
        assert "timestamp" in EXPECTED_MESSAGE_COLUMNS

    def test_session_id_shape(self):
        assert SESSION_ID_RE.match("20260917_131721_8f80ea")
        assert not SESSION_ID_RE.match("ses_abc")
        assert not SESSION_ID_RE.match("3b1c9f0e-7d2a-4c1b-9e8f-1a2b3c4d5e6f")


class TestGetStats:
    def test_reads_owned_session(self, db):
        conn, path = db
        add_session(conn, "20260917_131721_8f80ea")
        add_turns(conn, "20260917_131721_8f80ea")
        conn.commit()
        stats = HermesStatsReader(path).get_stats(session(agent_session_ids=["20260917_131721_8f80ea"]))
        assert stats is not None
        assert stats.input_tokens == 12786
        assert stats.output_tokens == 559 + 384      # reasoning folds into output
        assert stats.cache_read_tokens == 36224
        assert stats.cache_creation_tokens == 0
        assert stats.model == "gpt-5-mini"
        assert stats.current_context_tokens == 12256
        assert stats.interaction_count == 2
        assert stats.work_times == [9.0, 4.0]
        assert stats.provider is None

    def test_falls_back_to_directory_and_launch_time(self, db):
        conn, path = db
        add_session(conn, "20260917_131721_8f80ea")
        add_session(conn, "20260917_120000_aaaaaa", started=LAUNCH.timestamp() - 3600)  # before launch
        add_session(conn, "20260917_131800_bbbbbb", cwd="/elsewhere")
        add_session(conn, "20260917_131900_cccccc", source="telegram")
        conn.commit()
        stats = HermesStatsReader(path).get_stats(session())
        assert stats is not None and stats.input_tokens == 12786

    def test_compression_child_is_folded_in(self, db):
        conn, path = db
        add_session(conn, "20260917_131721_8f80ea")
        add_session(conn, "20260917_133000_child1", parent="20260917_131721_8f80ea",
                    started=T0 + 900, inp=1000, out=10, cache=0, reasoning=0, prompt_tokens=1500)
        conn.commit()
        stats = HermesStatsReader(path).get_stats(session(agent_session_ids=["20260917_131721_8f80ea"]))
        assert stats.input_tokens == 12786 + 1000
        # newest row is the live conversation
        assert stats.current_context_tokens == 1500

    def test_unknown_session_is_none(self, db):
        conn, path = db
        stats = HermesStatsReader(path).get_stats(session(agent_session_ids=["20260917_000000_zzzzzz"]))
        assert stats is None

    def test_missing_db_is_none(self, tmp_path):
        assert HermesStatsReader(tmp_path / "nope.db").get_stats(session()) is None

    def test_context_window_from_hermes_config(self, db, tmp_path, monkeypatch):
        conn, path = db
        add_session(conn, "20260917_131721_8f80ea")
        conn.commit()
        home = tmp_path / "hermes-home"
        home.mkdir()
        (home / "config.yaml").write_text("model:\n  context_length: 128000\n")
        monkeypatch.setenv("HERMES_HOME", str(home))
        stats = HermesStatsReader(path).get_stats(session(agent_session_ids=["20260917_131721_8f80ea"]))
        assert stats.reported_context_window == 128000
        assert stats.max_context_tokens == 128000


class TestStoredCost:
    def test_unknown_status_defers_to_pricing(self, db):
        conn, path = db
        add_session(conn, "20260917_131721_8f80ea", cost=0.0, cost_status="unknown")
        conn.commit()
        assert HermesStatsReader(path).get_stored_cost(session(agent_session_ids=["20260917_131721_8f80ea"])) is None

    def test_priced_session_is_trusted(self, db):
        conn, path = db
        add_session(conn, "20260917_131721_8f80ea", cost=0.0123, cost_status="estimated")
        conn.commit()
        assert HermesStatsReader(path).get_stored_cost(session(agent_session_ids=["20260917_131721_8f80ea"])) == pytest.approx(0.0123)


class TestSessionIds:
    def write_hook_state(self, tmp_path, monkeypatch, state):
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "state"))
        d = tmp_path / "state" / "agents"
        d.mkdir(parents=True)
        (d / "hook_state_hm.json").write_text(json.dumps(state))

    def test_hook_state_ids_active_last(self, tmp_path, monkeypatch):
        self.write_hook_state(tmp_path, monkeypatch, {
            "agent_session_ids": ["20260917_130000_aaaaaa", "20260917_131721_8f80ea"],
            "agent_session_id": "20260917_130000_aaaaaa",
        })
        assert session_ids_from_hook_state(session()) == ["20260917_131721_8f80ea", "20260917_130000_aaaaaa"]

    def test_current_session_id_prefers_plugin(self, db, tmp_path, monkeypatch):
        conn, path = db
        add_session(conn, "20260917_131721_8f80ea")
        conn.commit()
        self.write_hook_state(tmp_path, monkeypatch, {"agent_session_id": "20260917_140000_ffffff"})
        assert HermesStatsReader(path).get_current_session_id(session(), LAUNCH) == "20260917_140000_ffffff"

    def test_current_session_id_falls_back_to_directory(self, db, tmp_path, monkeypatch):
        conn, path = db
        add_session(conn, "20260917_131721_8f80ea")
        conn.commit()
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "empty"))
        assert HermesStatsReader(path).get_current_session_id(session(), LAUNCH) == "20260917_131721_8f80ea"

    def test_discover_adopts_unowned_ids_and_skips_others(self, db, tmp_path, monkeypatch):
        conn, path = db
        add_session(conn, "20260917_131721_8f80ea")
        add_session(conn, "20260917_131800_bbbbbb", started=T0 + 60)
        conn.commit()
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "empty"))
        other = SimpleNamespace(id="oc-uuid-2", agent_session_ids=["20260917_131800_bbbbbb"],
                                active_agent_session_id="20260917_131800_bbbbbb")
        found = HermesStatsReader(path).discover_session_ids(session(), LAUNCH, [session(), other])
        assert found.ids == ["20260917_131721_8f80ea"]
        assert found.latest == "20260917_131721_8f80ea"

    def test_discover_ignores_non_hermes_ids_from_hook_state(self, db, tmp_path, monkeypatch):
        conn, path = db
        self.write_hook_state(tmp_path, monkeypatch, {"agent_session_id": "3b1c9f0e-7d2a-4c1b-9e8f-1a2b3c4d5e6f"})
        found = HermesStatsReader(path).discover_session_ids(session(), LAUNCH, [session()])
        assert found.ids == [] and found.latest is None


class TestWindowUsage:
    def test_first_call_is_empty_then_deltas_accumulate(self, db):
        conn, path = db
        add_session(conn, "20260917_131721_8f80ea", inp=100, out=10, cache=0, reasoning=0)
        conn.commit()
        reader = HermesStatsReader(path)
        sess = session(agent_session_ids=["20260917_131721_8f80ea"])
        since = datetime.now() - timedelta(minutes=5)
        assert reader.get_window_token_usage(sess, since) == empty_window_usage()
        conn.execute("UPDATE sessions SET input_tokens = 350, output_tokens = 25")
        conn.commit()
        usage = reader.get_window_token_usage(sess, since)
        assert usage["input_tokens"] == 250 and usage["output_tokens"] == 15

    def test_sampler_window_boundary(self):
        s = _WindowSampler()
        s.record("k", {"input_tokens": 100, "output_tokens": 0, "cache_creation_tokens": 0, "cache_read_tokens": 0}, now=1000.0)
        s.record("k", {"input_tokens": 300, "output_tokens": 0, "cache_creation_tokens": 0, "cache_read_tokens": 0}, now=1060.0)
        now_totals = {"input_tokens": 500, "output_tokens": 0, "cache_creation_tokens": 0, "cache_read_tokens": 0}
        assert s.usage_since("k", 1030.0, now_totals)["input_tokens"] == 200
        assert s.usage_since("k", 900.0, now_totals)["input_tokens"] == 400
        assert s.usage_since("k", 2000.0, now_totals) == empty_window_usage()

    def test_no_container_stats(self, db):
        _, path = db
        assert HermesStatsReader(path).get_container_stats(session()) is None
