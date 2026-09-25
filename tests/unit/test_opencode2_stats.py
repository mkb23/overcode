"""Opencode2StatsReader against a synthetic session_v2/session_message DB.

Schema copied from the live v2 database (verified Sep 15 2026 — see the v2
session of `ses_f59f44840ffe7f1PaCEztE1b7y`: rows exist ONLY in session_v2 /
session_message; v1's session/message tables stay empty for v2 runs).

Asserts use the real ``AgentSessionStats`` field names: cost travels via the
reader's ``get_stored_cost`` (the daemon's cost path) and the opencode2 agent
persona via the ``agent`` field (the NULL-row fallback, active-session
scoped) — the reader must populate exactly the fields the v1 reader does so
the TUI columns work unchanged.
"""

import json
import sqlite3
from datetime import datetime
from unittest.mock import Mock, call, patch

import pytest

from overcode.backends.opencode2_stats import (
    Opencode2StatsReader,
    database_path,
    schema_findings,
)

SID = "ses_f59f44840ffe7f1PaCEztE1b7y"
DIR = "/tmp/proj"
# Before every time_created in the fixture (Sep 15 2026 00:00 UTC in ms is
# 1789430400000, the fixture rows start at 1789492181040).
LAUNCH = datetime(2026, 9, 15)
OTHER_DIR_SID = "ses_bbbbbbbbbbbbbbbbbbbbbbbbbb"
OTHER_AGENT_SID = "ses_ccccccccccccccccccccccccccc"
# An OLDER owned session (pre-/new) whose row and assistant message both
# carry identities the active conversation must never adopt.
OLD_SID = "ses_older_tracked_session_aaaaaa"
OLD_MODEL = "anthropic/claude-haiku-4-5"
OLD_AGENT = "plan"

SESSION_V2_DDL = """
CREATE TABLE session_v2 (
    id TEXT PRIMARY KEY, project_id TEXT, workspace_id TEXT,
    parent_id TEXT, fork_session_id TEXT, fork_boundary TEXT,
    slug TEXT, directory TEXT, path TEXT, title TEXT, version TEXT,
    share_url TEXT, summary_additions INTEGER, summary_deletions INTEGER,
    summary_files INTEGER, summary_diffs TEXT, metadata TEXT,
    cost REAL, tokens_input INTEGER, tokens_output INTEGER,
    tokens_reasoning INTEGER, tokens_cache_read INTEGER,
    tokens_cache_write INTEGER, revert TEXT, permission TEXT,
    agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER,
    time_compacting INTEGER, time_archived INTEGER, time_suspended INTEGER,
    resume_attempts INTEGER, time_idle INTEGER, time_viewed INTEGER,
    idle_outcome TEXT
);
CREATE TABLE session_message (
    id TEXT PRIMARY KEY, session_id TEXT, type TEXT, seq INTEGER,
    time_created INTEGER, time_updated INTEGER, data TEXT
);
"""


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "opencode.db"
    conn = sqlite3.connect(path)
    conn.executescript(SESSION_V2_DDL)
    # model/agent NULL on the session row is REAL v2 behaviour (verified).
    conn.execute(
        "INSERT INTO session_v2 (id, directory, parent_id, cost, tokens_input,"
        " tokens_output, tokens_reasoning, tokens_cache_read, tokens_cache_write,"
        " model, agent, time_created, time_updated)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (SID, DIR, None, 0.0157, 11261, 23, 0, 0, 0, None, None, 1789492181040, 1789492182604),
    )
    conn.execute(
        "INSERT INTO session_message (id, session_id, type, seq, time_created,"
        " time_updated, data) VALUES (?,?,?,?,?,?,?)",
        (
            "msg_u1",
            SID,
            "user",
            1,
            1789492181040,
            1789492181040,
            json.dumps({"time": {"created": 1789492181040}, "text": '"hi"'}),
        ),
    )
    conn.execute(
        "INSERT INTO session_message (id, session_id, type, seq, time_created,"
        " time_updated, data) VALUES (?,?,?,?,?,?,?)",
        (
            "msg_a1",
            SID,
            "assistant",
            2,
            1789492181926,
            1789492182604,
            json.dumps(
                {
                    "time": {"created": 1789492181926, "completed": 1789492182604},
                    "agent": "build",
                    "model": {"id": "acme-llm-1", "providerID": "acme", "variant": "high"},
                    "cost": 0.0134,
                    "finish": "tool-calls",
                    "tokens": {
                        "input": 9773,
                        "output": 14,
                        "reasoning": 0,
                        "cache": {"read": 0, "write": 0},
                    },
                }
            ),
        ),
    )
    conn.commit()
    conn.close()
    monkeypatch.delenv("OPENCODE_DB", raising=False)
    monkeypatch.setenv("OPENCODE_DATA_DIR", str(tmp_path))
    return path


class _FakeSession:
    start_directory = DIR
    agent_session_ids = [SID]


def test_get_stats_reads_v2_tables(db):
    reader = Opencode2StatsReader()
    stats = reader.get_stats(_FakeSession())
    assert stats is not None
    # Token totals come from the session_v2 row, reasoning folded into
    # output exactly like the v1 reader.
    assert stats.input_tokens == 11261
    assert stats.output_tokens == 23
    assert stats.provider is None
    # Cost is the reader's stored-cost answer (opencode2's own number).
    assert reader.get_stored_cost(_FakeSession()) == pytest.approx(0.0157)
    # model/agent come from the latest assistant message (session row is
    # NULL), model in the qualified provider/id form like the v1 reader.
    assert stats.model == "acme/acme-llm-1"
    assert stats.agent == "build"
    # Reasoning effort is opencode's model variant (#497)
    assert stats.effort == "high"
    assert stats.interaction_count == 1
    # v2's assistant data carries no tokens.total; the context column is
    # the processed prompt (input + cache read + cache write) of the
    # newest assistant turn.
    assert stats.current_context_tokens == 9773
    assert stats.work_times == pytest.approx([0.678])


def test_parse_variant_treats_default_as_unknown():
    from overcode.backends.opencode_stats import _parse_variant

    assert _parse_variant('{"id": "m", "providerID": "p", "variant": "max"}') == "max"
    assert _parse_variant({"id": "m", "variant": "default"}) is None
    assert _parse_variant({"id": "m"}) is None
    assert _parse_variant("not json") is None
    assert _parse_variant(None) is None


def test_database_path_honours_data_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENCODE_DB", raising=False)
    monkeypatch.setenv("OPENCODE_DATA_DIR", str(tmp_path))
    assert database_path() == tmp_path / "opencode.db"


def test_schema_findings_empty_on_match(db):
    assert schema_findings() == []


def test_schema_findings_flags_drift(db):
    conn = sqlite3.connect(db)
    # `agent` is one of the columns the reader reads by name; losing it is
    # drift the doctor must surface.
    conn.execute("ALTER TABLE session_v2 DROP COLUMN agent")
    conn.commit()
    conn.close()
    findings = schema_findings()
    assert any("schema" in f.lower() or "column" in f.lower() for f in findings)
    # The dashes-not-zeros contract: drift degrades get_stats to unknown,
    # never half-populated numbers.
    assert Opencode2StatsReader().get_stats(_FakeSession()) is None


def test_schema_findings_flags_v1_only_database(tmp_path, monkeypatch):
    # A database with only v1 tables cannot serve v2 sessions — one
    # human-readable warning per missing table.
    path = tmp_path / "v1only.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT)")
    conn.execute(
        "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, "
        "time_created INTEGER, time_updated INTEGER, data TEXT)"
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("OPENCODE_DB", str(path))
    findings = schema_findings()
    assert len(findings) == 2
    assert any("session_v2" in f for f in findings)
    assert any("session_message" in f for f in findings)


def test_missing_database_is_unknown_not_raised(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENCODE_DB", raising=False)
    monkeypatch.setenv("OPENCODE_DATA_DIR", str(tmp_path / "absent"))
    reader = Opencode2StatsReader()
    session = _FakeSession()
    assert reader.get_stats(session) is None
    assert reader.get_current_session_id(session, datetime(2026, 9, 15)) is None
    assert reader.get_stored_cost(session) is None
    assert reader.discover_session_ids(session, datetime(2026, 9, 15), []).ids == []
    assert reader.get_window_token_usage(session, datetime(2026, 9, 15)) == {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
    }


def test_discover_session_ids_adopts_this_directory_s_roots_only(db):
    conn = sqlite3.connect(db)
    # A root session in a different directory: same database, never ours.
    conn.execute(
        "INSERT INTO session_v2 (id, directory, parent_id, cost, time_created,"
        " time_updated) VALUES (?,?,?,?,?,?)",
        (OTHER_DIR_SID, "/tmp/other-proj", None, 0.0, 1789492190000, 1789492190000),
    )
    # A root session in this directory, started after the other two.
    conn.execute(
        "INSERT INTO session_v2 (id, directory, parent_id, cost, time_created,"
        " time_updated) VALUES (?,?,?,?,?,?)",
        (OTHER_AGENT_SID, DIR, None, 0.0, 1789492200000, 1789492200000),
    )
    conn.commit()
    conn.close()

    class _UnownedFakeSession(_FakeSession):
        agent_session_ids = []

    class _OtherAgent:
        id = "agent-2"
        agent_session_ids = [OTHER_AGENT_SID]

    reader = Opencode2StatsReader()
    # With another agent already claiming it, the newest root session is
    # excluded; the other-directory row is never a candidate.
    found = reader.discover_session_ids(_UnownedFakeSession(), LAUNCH, [_OtherAgent()])
    assert found.ids == [SID]
    assert found.latest == SID
    assert OTHER_DIR_SID not in found.ids
    assert OTHER_AGENT_SID not in found.ids
    # Unclaimed, the same row is adoptable and wins `latest` by time_updated.
    found = reader.discover_session_ids(_UnownedFakeSession(), LAUNCH, [])
    assert found.ids == [SID, OTHER_AGENT_SID]
    assert found.latest == OTHER_AGENT_SID


def test_directory_fallback_requires_a_unique_candidate(db):
    # Two opencode2 agents in one project, telemetry absent: the
    # fixture's root row plus a newer stranger's root session in the
    # SAME directory. Ownership is ambiguous, so the fallback must
    # answer unknown rather than sum a stranger's tokens or return the
    # stranger's session id.
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO session_v2 (id, directory, parent_id, cost, time_created,"
        " time_updated) VALUES (?,?,?,?,?,?)",
        (OTHER_AGENT_SID, DIR, None, 0.02, 1789492200000, 1789492200000),
    )
    conn.commit()
    conn.close()

    class _TelemetrylessSession(_FakeSession):
        agent_session_ids = []
        # Before both rows' time_created, so the directory fallback
        # sees both as candidates.
        start_time = "2026-09-15T00:00:00"

    reader = Opencode2StatsReader()
    assert reader.get_stats(_TelemetrylessSession()) is None
    assert reader.get_current_session_id(_TelemetrylessSession(), LAUNCH) is None

    # Single candidate: once the stranger's row is gone, adoption still
    # works — stats come from the one remaining row and its id is the
    # current session.
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM session_v2 WHERE id = ?", (OTHER_AGENT_SID,))
    conn.commit()
    conn.close()
    stats = reader.get_stats(_TelemetrylessSession())
    assert stats is not None
    assert stats.input_tokens == 11261
    assert reader.get_current_session_id(_TelemetrylessSession(), LAUNCH) == SID


def _add_stale_session(db, *, with_message):
    """An OLD owned session (row identities set) updated before the active one.

    After ``/new`` the plugin keeps reporting both ids, so both rows are
    tracked — the stale-identity regression shape. ``with_message`` also
    gives the old session an assistant message carrying its identities.
    """
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO session_v2 (id, directory, parent_id, cost, tokens_input,"
        " tokens_output, tokens_reasoning, tokens_cache_read, tokens_cache_write,"
        " model, agent, time_created, time_updated)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            OLD_SID, DIR, None, 0.02, 5000, 10, 0, 0, 0,
            OLD_MODEL, OLD_AGENT,
            1789492170000, 1789492175000,
        ),
    )
    if with_message:
        conn.execute(
            "INSERT INTO session_message (id, session_id, type, seq,"
            " time_created, time_updated, data) VALUES (?,?,?,?,?,?,?)",
            (
                "msg_old_a1",
                OLD_SID,
                "assistant",
                2,
                1789492171000,
                1789492175000,
                json.dumps(
                    {
                        "agent": OLD_AGENT,
                        "model": {"id": OLD_MODEL, "providerID": "anthropic"},
                        "tokens": {"input": 5000, "output": 10},
                    }
                ),
            ),
        )
    conn.commit()
    conn.close()


class _TwoSessionFake(_FakeSession):
    """Owns the old session and the active one, active = the fixture row."""
    agent_session_ids = [OLD_SID, SID]
    active_agent_session_id = SID


def test_stale_row_identities_do_not_suppress_the_active_session(db):
    # Regression: an OLD owned session row with model/agent set + the NEW
    # active row with NULL model/agent whose assistant messages carry a
    # DIFFERENT model/agent — get_stats must return the active session's
    # identities, not the old row's.
    _add_stale_session(db, with_message=True)
    stats = Opencode2StatsReader().get_stats(_TwoSessionFake())
    assert stats is not None
    assert stats.model == "acme/acme-llm-1"
    assert stats.agent == "build"


def test_stale_message_identities_do_not_leak_when_active_has_no_messages(db):
    # The message-scan half of the regression: a fresh active session with
    # NO assistant messages yet (right after /new) must degrade to unknown
    # rather than adopt the old session's message identities.
    _add_stale_session(db, with_message=True)
    conn = sqlite3.connect(db)
    # The active row's identities stay NULL (fixture) and its only
    # assistant message goes away — a conversation with no turns yet.
    conn.execute("DELETE FROM session_message WHERE id = 'msg_a1'")
    conn.commit()
    conn.close()
    stats = Opencode2StatsReader().get_stats(_TwoSessionFake())
    assert stats is not None
    assert stats.model is None
    assert stats.agent is None


def test_active_row_agent_survives_an_agentless_assistant_message(db):
    # The model line's precedence shape, for the agent: the active row's
    # session_v2.agent wins when populated, even while the newest
    # assistant message lacks an agent (the scan fallback finds nothing
    # and must not clobber or suppress the row's value).
    conn = sqlite3.connect(db)
    conn.execute("UPDATE session_v2 SET agent = 'plan' WHERE id = ?", (SID,))
    row = conn.execute(
        "SELECT data FROM session_message WHERE id = 'msg_a1'"
    ).fetchone()
    envelope = json.loads(row[0])
    del envelope["agent"]
    conn.execute(
        "UPDATE session_message SET data = ? WHERE id = 'msg_a1'",
        (json.dumps(envelope),),
    )
    conn.commit()
    conn.close()
    stats = Opencode2StatsReader().get_stats(_FakeSession())
    assert stats is not None
    assert stats.agent == "plan"


class TestDaemonAgentPersonaSync:
    """The daemon consumes the stats object's ``agent`` field, gated on
    the backend's AGENT_INJECTION capability.

    Backends that honor launch-time ``--agent`` (claude-code, opencode,
    grok) had the launcher's choice applied to the CLI, so a detected
    persona only fills an empty one. Backends without the bit
    (opencode2, codex) never apply a stored ``--agent`` to the CLI, so
    the stored value is fiction and the detected persona replaces it.
    """

    def _make_daemon(self, tmp_path, monkeypatch):
        from overcode.monitor_daemon import MonitorDaemon

        monkeypatch.setattr(
            "overcode.monitor_daemon.ensure_session_dir", lambda x: tmp_path
        )
        monkeypatch.setattr(
            "overcode.monitor_daemon.get_monitor_daemon_pid_path",
            lambda x: tmp_path / "pid",
        )
        monkeypatch.setattr(
            "overcode.monitor_daemon.get_monitor_daemon_state_path",
            lambda x: tmp_path / "state.json",
        )
        monkeypatch.setattr(
            "overcode.monitor_daemon.get_agent_history_path",
            lambda x: tmp_path / "history.csv",
        )
        with patch("overcode.monitor_daemon.SessionManager") as mock_sm_cls, \
                patch("overcode.monitor_daemon.StatusDetectorDispatcher"):
            daemon = MonitorDaemon(tmux_session="test")
            daemon.session_manager = mock_sm_cls.return_value
        return daemon

    def _make_session(self, agent_persona, backend="opencode2"):
        session = Mock()
        session.id = "sess-1"
        session.name = "agent-1"
        session.backend = backend
        session.start_directory = DIR
        session.start_time = "2026-09-15T00:00:00"
        session.tmux_session = "test"
        session.wrapper = None
        session.model = None
        session.effort = None
        session.provider = None
        session.agent_session_ids = [SID]
        session.active_agent_session_id = SID
        session.agent_persona = agent_persona
        session.stats = Mock()
        return session

    def _run_sync(self, daemon, session, db, monkeypatch, reader=None):
        if reader is None:
            reader = Opencode2StatsReader()
        monkeypatch.setattr(
            "overcode.monitor_daemon.stats_reader_for_session",
            lambda s: reader,
        )
        daemon.sync_agent_stats(session)

    def _mock_v1_reader(self, agent):
        """A reader whose stats report ``agent`` — the opencode (v1)
        shape, so the daemon's capability gate is the only thing under
        test (not v1's own storage)."""
        stats = Mock(
            interaction_count=5, input_tokens=100, output_tokens=10,
            cache_creation_tokens=0, cache_read_tokens=0,
            model="anthropic/claude-haiku-4-5", provider=None,
            last_command=None, agent=agent, current_context_tokens=100,
        )
        reader = Mock()
        reader.get_current_session_id.return_value = None
        reader.get_stats.return_value = stats
        reader.get_stored_cost.return_value = None
        return reader

    def _persona_calls(self, daemon):
        # The persona is staged for the tick's single sessions.json write
        # (R5), in the shape update_session used to be called with.
        assert daemon.session_manager.update_session.call_args_list == []
        return [
            call(sid, agent_persona=fields["agent_persona"])
            for sid, fields in daemon._pending.fields.items()
            if "agent_persona" in fields
        ]

    def test_detected_effort_is_persisted(self, db, tmp_path, monkeypatch):
        # #497: the reader's effort (opencode's model variant) is staged
        # onto the session record, like the model.
        daemon = self._make_daemon(tmp_path, monkeypatch)
        session = self._make_session(agent_persona=None)

        self._run_sync(daemon, session, db, monkeypatch)

        assert daemon._pending.fields["sess-1"]["effort"] == "high"

    def test_empty_persona_is_persisted_from_reader(self, db, tmp_path, monkeypatch):
        daemon = self._make_daemon(tmp_path, monkeypatch)
        session = self._make_session(agent_persona=None)

        self._run_sync(daemon, session, db, monkeypatch)

        assert self._persona_calls(daemon) == [
            call("sess-1", agent_persona="build")
        ]

    def test_opencode2_stored_persona_is_replaced_by_the_detected_one(
        self, db, tmp_path, monkeypatch
    ):
        # opencode2 never applies a stored --agent to the CLI (v2's TUI
        # rejects the flag), so the launcher-recorded persona is fiction:
        # the detected persona — what the agent is actually running — is
        # the truth and must replace it.
        daemon = self._make_daemon(tmp_path, monkeypatch)
        session = self._make_session(agent_persona="plan")

        self._run_sync(daemon, session, db, monkeypatch)

        assert self._persona_calls(daemon) == [
            call("sess-1", agent_persona="build")
        ]

    def test_opencode_v1_stored_persona_is_not_clobbered(
        self, db, tmp_path, monkeypatch
    ):
        # opencode (v1) honors launch-time --agent, so a stored persona
        # was applied to the CLI and must win over the detected one; an
        # empty persona is still filled.
        daemon = self._make_daemon(tmp_path, monkeypatch)
        session = self._make_session(agent_persona="plan", backend="opencode")

        self._run_sync(
            daemon, session, db, monkeypatch, reader=self._mock_v1_reader("build")
        )

        assert self._persona_calls(daemon) == []

    def test_opencode_v1_empty_persona_is_filled(
        self, db, tmp_path, monkeypatch
    ):
        daemon = self._make_daemon(tmp_path, monkeypatch)
        session = self._make_session(agent_persona=None, backend="opencode")

        self._run_sync(
            daemon, session, db, monkeypatch, reader=self._mock_v1_reader("build")
        )

        assert self._persona_calls(daemon) == [
            call("sess-1", agent_persona="build")
        ]
