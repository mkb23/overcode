"""OpencodeStatsReader against a fixture database built from the real schema.

``SESSION_DDL`` / ``MESSAGE_DDL`` are verbatim copies of the ``CREATE TABLE``
statements read out of a live opencode v1.18.19 store
(``sqlite3 ~/.local/share/opencode/opencode.db "select sql from sqlite_master"``),
so the fixture drifts only when opencode does — at which point
``TestSchemaDrift`` documents what the reader does about it.
"""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from overcode.backends.opencode_stats import (
    EXPECTED_SESSION_COLUMNS,
    OpencodeStatsReader,
    database_path,
    default_data_dir,
    missing_columns,
    schema_findings,
    session_ids_from_hook_state,
)

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

# Schema drift the reader has to survive: opencode renames a token column.
DRIFTED_SESSION_DDL = SESSION_DDL.replace("`tokens_input`", "`input_tokens`")

LAUNCH = datetime(2026, 8, 20, 12, 0, 0)
LAUNCH_MS = int(LAUNCH.timestamp() * 1000)
PROJECT_DIR = "/tmp/oc-project"
SID = "ses_aaaaaaaaaaaaaaaaaaaaaaaaaa"
OTHER_SID = "ses_bbbbbbbbbbbbbbbbbbbbbbbbbb"


def insert_session(conn, sid, **overrides):
    row = {
        "id": sid,
        "project_id": "prj_1",
        "workspace_id": None,
        "parent_id": None,
        "slug": "happy-otter",
        "directory": PROJECT_DIR,
        "path": PROJECT_DIR.lstrip("/"),
        "title": "Session",
        "version": "1.18.19",
        "share_url": None,
        "summary_additions": None,
        "summary_deletions": None,
        "summary_files": None,
        "summary_diffs": None,
        "metadata": None,
        "cost": 0.0025,
        "tokens_input": 7000,
        "tokens_output": 100,
        "tokens_reasoning": 20,
        "tokens_cache_read": 512,
        "tokens_cache_write": 64,
        "revert": None,
        "permission": None,
        "agent": "build",
        "model": json.dumps({"id": "gpt-4o-mini", "providerID": "openai"}),
        "time_created": LAUNCH_MS + 1000,
        "time_updated": LAUNCH_MS + 5000,
        "time_compacting": None,
        "time_archived": None,
    }
    row.update(overrides)
    columns = ", ".join(f"`{k}`" for k in row)
    conn.execute(
        f"INSERT INTO session ({columns}) VALUES ({', '.join('?' * len(row))})",
        tuple(row.values()),
    )


def insert_message(conn, msg_id, sid, data, time_created):
    conn.execute(
        "INSERT INTO message (id, session_id, time_created, time_updated, data) "
        "VALUES (?, ?, ?, ?, ?)",
        (msg_id, sid, time_created, time_created, json.dumps(data)),
    )


def user_message(**over):
    return {"role": "user", "time": {"created": LAUNCH_MS}, **over}


def assistant_message(total=7120, inp=7000, out=100, reasoning=20, read=512, write=64,
                      created=LAUNCH_MS + 1000, completed=LAUNCH_MS + 4000):
    return {
        "role": "assistant",
        "cost": 0.0025,
        "tokens": {
            "total": total,
            "input": inp,
            "output": out,
            "reasoning": reasoning,
            "cache": {"read": read, "write": write},
        },
        "modelID": "gpt-4o-mini",
        "providerID": "openai",
        "time": {"created": created, "completed": completed},
    }


@pytest.fixture
def db(tmp_path):
    """A fixture database matching the observed opencode schema."""
    path = tmp_path / "opencode.db"
    conn = sqlite3.connect(path)
    conn.executescript(SESSION_DDL + ";" + MESSAGE_DDL)
    insert_session(conn, SID)
    insert_message(conn, "msg_1", SID, user_message(), LAUNCH_MS)
    insert_message(conn, "msg_2", SID, assistant_message(), LAUNCH_MS + 1000)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def drifted_db(tmp_path):
    path = tmp_path / "drifted.db"
    conn = sqlite3.connect(path)
    conn.executescript(DRIFTED_SESSION_DDL + ";" + MESSAGE_DDL)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def reader(db):
    return OpencodeStatsReader(db_path=db)


def make_session(**over):
    base = dict(
        id="agent-1",
        name="oc-agent",
        tmux_session="agents",
        agent_session_ids=[SID],
        active_agent_session_id=SID,
        start_directory=PROJECT_DIR,
        start_time=LAUNCH.isoformat(),
        wrapper=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


class TestDatabaseLocation:
    def test_default_path(self, monkeypatch):
        monkeypatch.delenv("OPENCODE_DB", raising=False)
        monkeypatch.delenv("OPENCODE_DATA_DIR", raising=False)
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        assert database_path() == Path.home() / ".local/share/opencode/opencode.db"

    def test_xdg_data_home_is_honoured(self, monkeypatch, tmp_path):
        monkeypatch.delenv("OPENCODE_DB", raising=False)
        monkeypatch.delenv("OPENCODE_DATA_DIR", raising=False)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        assert default_data_dir() == tmp_path / "opencode"

    def test_opencode_db_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCODE_DB", str(tmp_path / "custom.db"))
        monkeypatch.setenv("OPENCODE_DATA_DIR", str(tmp_path / "ignored"))
        assert database_path() == tmp_path / "custom.db"

    def test_opencode_data_dir(self, monkeypatch, tmp_path):
        monkeypatch.delenv("OPENCODE_DB", raising=False)
        monkeypatch.setenv("OPENCODE_DATA_DIR", str(tmp_path))
        assert database_path() == tmp_path / "opencode.db"


class TestGetStats:
    def test_reads_tokens_from_the_session_row(self, reader):
        stats = reader.get_stats(make_session())
        assert stats.input_tokens == 7000
        # reasoning has no bucket of its own, so it folds into output rather
        # than vanishing from the totals.
        assert stats.output_tokens == 120
        assert stats.cache_read_tokens == 512
        assert stats.cache_creation_tokens == 64

    def test_model_keeps_the_qualified_form(self, reader):
        # A restart re-emits this via `--model`, and opencode rejects a bare id.
        assert reader.get_stats(make_session()).model == "openai/gpt-4o-mini"

    def test_model_still_matches_the_pricing_table(self, reader):
        from overcode.pricing import lookup_pricing

        assert lookup_pricing(reader.get_stats(make_session()).model).input == 0.15

    def test_provider_is_left_alone(self, reader):
        # `provider` is overcode's API-transport discriminator, not opencode's
        # model provider — writing "openai" into it would corrupt the session.
        assert reader.get_stats(make_session()).provider is None

    def test_interaction_count_from_user_messages(self, reader):
        assert reader.get_stats(make_session()).interaction_count == 1

    def test_current_context_from_latest_assistant_turn(self, reader):
        assert reader.get_stats(make_session()).current_context_tokens == 7120

    def test_work_times_from_message_timing(self, reader):
        assert reader.get_stats(make_session()).work_times == [3.0]

    def test_multiple_owned_sessions_are_summed(self, db):
        conn = sqlite3.connect(db)
        insert_session(conn, OTHER_SID, tokens_input=1000, tokens_output=10,
                       time_updated=LAUNCH_MS + 9000)
        conn.commit()
        conn.close()
        reader = OpencodeStatsReader(db_path=db)
        stats = reader.get_stats(make_session(agent_session_ids=[SID, OTHER_SID]))
        assert stats.input_tokens == 8000

    def test_unknown_session_falls_back_to_directory_match(self, reader):
        stats = reader.get_stats(
            make_session(agent_session_ids=[], active_agent_session_id=None)
        )
        assert stats is not None and stats.input_tokens == 7000

    def test_directory_fallback_respects_the_launch_window(self, reader):
        stats = reader.get_stats(
            make_session(
                agent_session_ids=[],
                active_agent_session_id=None,
                start_time=(LAUNCH + timedelta(days=1)).isoformat(),
            )
        )
        assert stats is None

    def test_child_sessions_are_not_adopted_by_directory(self, db):
        conn = sqlite3.connect(db)
        insert_session(conn, OTHER_SID, parent_id=SID, tokens_input=99)
        conn.commit()
        conn.close()
        reader = OpencodeStatsReader(db_path=db)
        stats = reader.get_stats(
            make_session(agent_session_ids=[], active_agent_session_id=None)
        )
        assert stats.input_tokens == 7000

    def test_no_matching_row_is_unknown_not_zero(self, reader):
        stats = reader.get_stats(make_session(start_directory="/somewhere/else",
                                              agent_session_ids=[],
                                              active_agent_session_id=None))
        assert stats is None


class TestStoredCost:
    def test_prefers_opencode_s_own_number(self, reader):
        assert reader.get_stored_cost(make_session()) == pytest.approx(0.0025)

    def test_zero_cost_defers_to_the_pricing_table(self, db):
        conn = sqlite3.connect(db)
        conn.execute("UPDATE session SET cost = 0")
        conn.commit()
        conn.close()
        assert OpencodeStatsReader(db_path=db).get_stored_cost(make_session()) is None

    def test_missing_database_is_none(self, tmp_path):
        reader = OpencodeStatsReader(db_path=tmp_path / "absent.db")
        assert reader.get_stored_cost(make_session()) is None


class TestSessionIdDiscovery:
    def test_plugin_recorded_ids_win(self, reader, tmp_path, monkeypatch):
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "state"))
        state = tmp_path / "state" / "agents"
        state.mkdir(parents=True)
        (state / "hook_state_oc-agent.json").write_text(
            json.dumps({
                "event": "Stop",
                "timestamp": 0,
                "agent_session_ids": [OTHER_SID, SID],
                "agent_session_id": SID,
            })
        )
        assert session_ids_from_hook_state(make_session()) == [OTHER_SID, SID]
        assert reader.get_current_session_id(make_session(), LAUNCH) == SID

    def test_directory_fallback_when_no_hook_state(self, reader, tmp_path, monkeypatch):
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "empty"))
        assert reader.get_current_session_id(make_session(), LAUNCH) == SID

    def test_discovers_unowned_ids(self, reader, tmp_path, monkeypatch):
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "empty"))
        found = reader.discover_session_ids(
            make_session(agent_session_ids=[], active_agent_session_id=None), LAUNCH, []
        )
        assert found.ids == [SID]
        assert found.latest == SID

    def test_never_steals_another_agent_s_session(self, reader, tmp_path, monkeypatch):
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "empty"))
        other_agent = make_session(id="agent-2", name="other")
        found = reader.discover_session_ids(
            make_session(agent_session_ids=[], active_agent_session_id=None),
            LAUNCH,
            [other_agent],
        )
        assert found.ids == [] and found.latest is None

    def test_ignores_non_opencode_ids(self, reader, tmp_path, monkeypatch):
        # A rebadged session can carry a leftover Claude UUID; adopting it
        # would send `--session <uuid>` at opencode.
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "state"))
        state = tmp_path / "state" / "agents"
        state.mkdir(parents=True)
        (state / "hook_state_oc-agent.json").write_text(
            json.dumps({"event": "Stop", "timestamp": 0,
                        "agent_session_id": "3f8a-not-an-opencode-id"})
        )
        found = reader.discover_session_ids(
            make_session(agent_session_ids=[], active_agent_session_id=None), LAUNCH, []
        )
        assert "3f8a-not-an-opencode-id" not in found.ids
        assert found.latest != "3f8a-not-an-opencode-id"

    def test_corrupt_hook_state_is_ignored(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "state"))
        state = tmp_path / "state" / "agents"
        state.mkdir(parents=True)
        (state / "hook_state_oc-agent.json").write_text("{not json")
        assert session_ids_from_hook_state(make_session()) == []


class TestWindowUsage:
    def test_counts_messages_inside_the_window(self, reader):
        usage = reader.get_window_token_usage(make_session(), LAUNCH)
        assert usage["input_tokens"] == 7000
        assert usage["output_tokens"] == 120
        assert usage["cache_read_tokens"] == 512

    def test_excludes_messages_before_the_window(self, reader):
        usage = reader.get_window_token_usage(
            make_session(), LAUNCH + timedelta(hours=1)
        )
        assert usage == {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
        }

    def test_missing_database_is_zeroed_not_raised(self, tmp_path):
        reader = OpencodeStatsReader(db_path=tmp_path / "absent.db")
        assert reader.get_window_token_usage(make_session(), LAUNCH)["input_tokens"] == 0


class TestSchemaDrift:
    def test_missing_columns_are_reported(self, drifted_db):
        conn = sqlite3.connect(f"file:{drifted_db}?mode=ro", uri=True)
        try:
            assert missing_columns(conn) == {"session": ["tokens_input"]}
        finally:
            conn.close()

    def test_intact_schema_reports_nothing(self, db):
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            assert missing_columns(conn) == {}
        finally:
            conn.close()

    def test_drift_degrades_to_unknown(self, drifted_db):
        reader = OpencodeStatsReader(db_path=drifted_db)
        assert reader.get_stats(make_session()) is None

    def test_drift_surfaces_a_doctor_finding(self, drifted_db, monkeypatch):
        monkeypatch.setenv("OPENCODE_DB", str(drifted_db))
        findings = schema_findings()
        assert len(findings) == 1
        assert "tokens_input" in findings[0]

    def test_no_database_is_not_a_finding(self, tmp_path, monkeypatch):
        # A user who has not run opencode yet is not misconfigured.
        monkeypatch.setenv("OPENCODE_DB", str(tmp_path / "absent.db"))
        assert schema_findings() == []

    def test_expected_columns_match_the_fixture_ddl(self):
        for column in EXPECTED_SESSION_COLUMNS:
            assert f"`{column}`" in SESSION_DDL


class TestFailureIsolation:
    """Nothing the reader does may raise into a daemon tick."""

    def test_missing_database(self, tmp_path):
        reader = OpencodeStatsReader(db_path=tmp_path / "absent.db")
        session = make_session()
        assert reader.get_stats(session) is None
        assert reader.get_current_session_id(session, LAUNCH) is None
        assert reader.discover_session_ids(session, LAUNCH, []).ids == []
        assert reader.get_container_stats(session) is None

    def test_corrupt_database(self, tmp_path):
        path = tmp_path / "junk.db"
        path.write_bytes(b"this is not a database")
        reader = OpencodeStatsReader(db_path=path)
        assert reader.get_stats(make_session()) is None
        assert reader.get_stored_cost(make_session()) is None

    def test_session_without_a_directory(self, reader):
        session = make_session(
            start_directory=None, agent_session_ids=[], active_agent_session_id=None
        )
        assert reader.get_stats(session) is None
        assert reader.get_current_session_id(session, LAUNCH) is None

    def test_unparseable_start_time(self, reader):
        session = make_session(
            start_time="not-a-timestamp",
            agent_session_ids=[],
            active_agent_session_id=None,
        )
        assert reader.get_stats(session) is None

    def test_corrupt_message_json_is_skipped(self, db):
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO message (id, session_id, time_created, time_updated, data) "
            "VALUES ('msg_bad', ?, ?, ?, 'not json')",
            (SID, LAUNCH_MS + 2000, LAUNCH_MS + 2000),
        )
        conn.commit()
        conn.close()
        stats = OpencodeStatsReader(db_path=db).get_stats(make_session())
        assert stats is not None and stats.interaction_count == 1

    def test_container_stats_are_not_available(self, reader):
        assert reader.get_container_stats(make_session(wrapper="devcontainer")) is None


class TestBackendWiring:
    def test_backend_hands_out_the_sqlite_reader(self):
        from overcode.backends.opencode import get_opencode_backend

        assert isinstance(get_opencode_backend().make_stats_reader(), OpencodeStatsReader)

    def test_session_resolution_picks_it_up(self):
        from overcode.stats_reader import clear_reader_cache, stats_reader_for_session

        clear_reader_cache()
        try:
            reader = stats_reader_for_session(SimpleNamespace(backend="opencode"))
            assert isinstance(reader, OpencodeStatsReader)
        finally:
            clear_reader_cache()
