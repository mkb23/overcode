"""#532 — opencode/opencode2 agents get their PR from stored tool output.

Claude agents take the PR from transcript pr-link records (#489). opencode
has no such record, but it stores every tool call's output: the PR is the
URL the newest ``gh pr create`` printed (or the "already exists" error that
names it). v1 keeps tool calls in ``part`` rows; v2 inline in the assistant
envelope's ``content``. Both readers publish it as ``stats.pr_number`` and
the daemon's ``sync_pr`` takes it; the pane scrape stays the fallback.

Every fixture here is synthetic; the DDL is the live schema's shape.
"""

import json
import sqlite3
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from overcode.backends import opencode2_stats, opencode_stats
from overcode.backends.opencode2_stats import Opencode2StatsReader
from overcode.backends.opencode_stats import OpencodeStatsReader, pr_from_tool_call

pytestmark = pytest.mark.unit

LAUNCH = datetime(2026, 9, 1, 9, 0, 0)
LAUNCH_MS = int(LAUNCH.timestamp() * 1000)
DIRECTORY = "/work/pr-project"
SID = "ses_pr_aaaaaaaaaaaaaaaaaaaaaaaa"
OLD_SID = "ses_pr_bbbbbbbbbbbbbbbbbbbbbbbb"
URL = "https://github.com/acme/widgets/pull/{}"

DDL = """
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
CREATE TABLE message (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
    time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX message_session_time_created_id_idx
    ON message (session_id, time_created, id);
CREATE TABLE part (
    id TEXT PRIMARY KEY, message_id TEXT NOT NULL, session_id TEXT NOT NULL,
    time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX part_message_id_id_idx ON part (message_id, id);
CREATE INDEX part_session_idx ON part (session_id);
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
CREATE TABLE session_message (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, type TEXT NOT NULL,
    seq INTEGER NOT NULL, time_created INTEGER NOT NULL,
    time_updated INTEGER NOT NULL, data TEXT NOT NULL
);
CREATE UNIQUE INDEX session_message_session_seq_idx
    ON session_message (session_id, seq);
"""


@pytest.fixture(autouse=True)
def _fresh_caches():
    opencode_stats.clear_row_cache()
    opencode_stats.clear_window_indexes()
    yield
    opencode_stats.clear_row_cache()
    opencode_stats.clear_window_indexes()


# -- tool-call shapes -------------------------------------------------------


def v1_bash(command, output="", status="completed", error=None):
    """A v1 ``part.data`` tool call."""
    state = {"status": status, "input": {"command": command},
             "time": {"start": 1, "end": 2}}
    if status == "completed":
        state.update(output=output, title=command,
                     metadata={"output": output, "exit": 0, "truncated": False})
    else:
        state["error"] = error or ""
    return {"type": "tool", "tool": "bash", "callID": "call_1", "state": state}


def v2_bash(command, output="", status="completed", error=None):
    """A v2 assistant ``content`` tool item."""
    state = {"status": status, "input": {"command": command}}
    if status == "completed":
        state.update(content=[{"type": "text", "text": output}],
                     metadata={"output": output, "exit": 0, "truncated": False})
    else:
        state["error"] = {"type": "tool.execution", "message": error or ""}
    return {"type": "tool", "id": "call_1", "name": "bash", "state": state,
            "time": {"created": 1, "completed": 2}}


class TestPrFromToolCall:
    @pytest.mark.parametrize("shape", [v1_bash, v2_bash])
    def test_gh_pr_create_output_names_the_pr(self, shape):
        call = shape("git push -u origin fix && gh pr create --fill",
                     "Creating pull request for fix into main\n\n" + URL.format(57) + "\n")
        assert pr_from_tool_call(call) == 57

    @pytest.mark.parametrize("shape", [v1_bash, v2_bash])
    def test_already_exists_error_names_the_pr(self, shape):
        call = shape("gh pr create --title x --body y", status="error",
                     error='a pull request for branch "fix" into branch "main" '
                           "already exists:\n" + URL.format(12))
        assert pr_from_tool_call(call) == 12

    @pytest.mark.parametrize("shape", [v1_bash, v2_bash])
    def test_other_commands_do_not_link(self, shape):
        # Reading someone else's PR says nothing about this branch.
        assert pr_from_tool_call(shape("gh pr view 99 --web", URL.format(99))) is None
        assert pr_from_tool_call(shape("curl " + URL.format(98), URL.format(98))) is None

    def test_no_url_and_junk_are_none(self):
        assert pr_from_tool_call(v1_bash("gh pr create", "error: no commits")) is None
        assert pr_from_tool_call({"type": "text", "text": URL.format(3)}) is None
        assert pr_from_tool_call(None) is None
        assert pr_from_tool_call({"type": "tool", "state": "weird"}) is None


# -- v1: tool calls in `part` rows ----------------------------------------


def _store(tmp_path):
    path = tmp_path / "opencode.db"
    conn = sqlite3.connect(path)
    conn.executescript(DDL)
    conn.commit()
    return path, conn


def _v1_session(conn, sid, t):
    conn.execute(
        "INSERT INTO session (id, project_id, slug, directory, version, parent_id,"
        " cost, tokens_input, tokens_output, tokens_reasoning, tokens_cache_read,"
        " tokens_cache_write, model, time_created, time_updated)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sid, "prj", "slug", DIRECTORY, "1", None, 0.0, 10, 1, 0, 0, 0,
         json.dumps({"id": "gpt-5", "providerID": "openai"}), t, t + 100_000),
    )


def _v1_turn(conn, sid, n, t, parts=(), completed=True, role="assistant"):
    """One message ``msg_<n>`` at ``t`` with its tool ``parts``."""
    times = {"created": t}
    if completed:
        times["completed"] = t + 500
    data = {"role": role, "time": times,
            "tokens": {"total": 11, "input": 10, "output": 1, "reasoning": 0,
                       "cache": {"read": 0, "write": 0}}}
    msg_id = f"msg_{n:04d}"
    conn.execute("INSERT INTO message VALUES (?,?,?,?,?)",
                 (msg_id, sid, t, t + (500 if completed else 0), json.dumps(data)))
    for i, part in enumerate(parts):
        conn.execute("INSERT INTO part VALUES (?,?,?,?,?,?)",
                     (f"prt_{n:04d}_{i:02d}", msg_id, sid, t, t, json.dumps(part)))
    return msg_id


def _session(*ids):
    return SimpleNamespace(
        id="agent-1", name="oc-agent", tmux_session="agents",
        agent_session_ids=list(ids), active_agent_session_id=ids[-1],
        start_directory=DIRECTORY, start_time=LAUNCH.isoformat(), wrapper=None,
    )


class TestOpencodeV1:
    def test_pr_comes_from_the_gh_pr_create_part(self, tmp_path):
        path, conn = _store(tmp_path)
        t = LAUNCH_MS + 1000
        _v1_session(conn, SID, t)
        _v1_turn(conn, SID, 1, t, role="user")
        _v1_turn(conn, SID, 2, t + 1000, parts=[
            {"type": "step-start"},
            v1_bash("gh pr create --fill", URL.format(41)),
            {"type": "text", "text": "Opened " + URL.format(41)},
        ])
        conn.commit()

        stats = OpencodeStatsReader(db_path=path).get_stats(_session(SID))
        assert stats.pr_number == 41

    def test_newest_create_wins_across_conversations(self, tmp_path):
        path, conn = _store(tmp_path)
        t = LAUNCH_MS + 1000
        _v1_session(conn, OLD_SID, t)
        _v1_session(conn, SID, t + 50_000)
        _v1_turn(conn, OLD_SID, 1, t, parts=[v1_bash("gh pr create", URL.format(5))])
        _v1_turn(conn, SID, 2, t + 60_000, parts=[
            v1_bash("gh pr create", URL.format(6)),
            v1_bash("gh pr create --draft", URL.format(7)),
        ])
        _v1_turn(conn, SID, 3, t + 70_000, parts=[v1_bash("gh pr view 99", URL.format(99))])
        conn.commit()

        stats = OpencodeStatsReader(db_path=path).get_stats(_session(OLD_SID, SID))
        assert stats.pr_number == 7

    def test_no_pr_and_no_part_table_are_none(self, tmp_path):
        path, conn = _store(tmp_path)
        t = LAUNCH_MS + 1000
        _v1_session(conn, SID, t)
        _v1_turn(conn, SID, 1, t, parts=[v1_bash("ls", "README.md")])
        conn.commit()
        reader = OpencodeStatsReader(db_path=path)
        assert reader.get_stats(_session(SID)).pr_number is None

        conn.execute("DROP TABLE part")
        conn.commit()
        opencode_stats.clear_row_cache()
        stats = reader.get_stats(_session(SID))
        assert stats is not None and stats.pr_number is None
        assert stats.interaction_count == 0

    def test_parts_are_read_once_per_message_version(self, tmp_path, monkeypatch):
        path, conn = _store(tmp_path)
        t = LAUNCH_MS + 1000
        _v1_session(conn, SID, t)
        _v1_turn(conn, SID, 1, t, parts=[v1_bash("gh pr create", URL.format(8))])
        in_flight = _v1_turn(conn, SID, 2, t + 1000, completed=False)
        conn.commit()

        looked_up = []
        real = opencode_stats._attach_part_prs

        def spy(c, records):
            looked_up.append(sorted(records))
            real(c, records)

        monkeypatch.setattr(opencode_stats, "_attach_part_prs", spy)
        # _scan_messages binds the enricher at call time
        reader = OpencodeStatsReader(db_path=path)
        assert reader.get_stats(_session(SID)).pr_number == 8
        assert reader.get_stats(_session(SID)).pr_number == 8
        # The completed message was read once; only the in-flight one again.
        assert looked_up == [["msg_0001", in_flight], [in_flight]]

    def test_in_flight_turn_shows_its_pr_before_it_completes(self, tmp_path):
        path, conn = _store(tmp_path)
        t = LAUNCH_MS + 1000
        _v1_session(conn, SID, t)
        _v1_turn(conn, SID, 1, t, completed=False,
                 parts=[v1_bash("gh pr create", URL.format(9))])
        conn.commit()
        stats = OpencodeStatsReader(db_path=path).get_stats(_session(SID))
        assert stats.pr_number == 9


# -- v2: tool calls inline in the assistant envelope -----------------------


def _v2_session(conn, sid, t):
    conn.execute(
        "INSERT INTO session_v2 (id, project_id, slug, directory, version, parent_id,"
        " cost, tokens_input, tokens_output, tokens_reasoning, tokens_cache_read,"
        " tokens_cache_write, time_created, time_updated)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sid, "prj", "slug", DIRECTORY, "1", None, 0.0, 10, 1, 0, 0, 0, t, t + 100_000),
    )


def _v2_turn(conn, sid, seq, t, content=(), mtype="assistant"):
    data = {"time": {"created": t, "completed": t + 500},
            "tokens": {"input": 10, "output": 1, "reasoning": 0,
                       "cache": {"read": 0, "write": 0}},
            "content": list(content)}
    conn.execute("INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
                 (f"msg_{sid[-4:]}_{seq}", sid, mtype, seq, t, t + 500, json.dumps(data)))


@pytest.fixture
def v2_env(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENCODE_DB", raising=False)
    monkeypatch.setenv("OPENCODE_DATA_DIR", str(tmp_path))
    return _store(tmp_path)


class TestOpencode2:
    def test_pr_comes_from_the_inline_tool_call(self, v2_env):
        _path, conn = v2_env
        t = LAUNCH_MS + 1000
        _v2_session(conn, SID, t)
        _v2_turn(conn, SID, 1, t, mtype="user")
        _v2_turn(conn, SID, 2, t + 1000, content=[
            {"type": "reasoning", "text": "push and open a PR"},
            v2_bash("gh pr create --fill", "\n" + URL.format(73) + "\n"),
            {"type": "text", "text": "Done: " + URL.format(73)},
        ])
        conn.commit()

        stats = Opencode2StatsReader().get_stats(_session(SID))
        assert stats.pr_number == 73

    def test_newest_create_wins_and_reads_are_ignored(self, v2_env):
        _path, conn = v2_env
        t = LAUNCH_MS + 1000
        _v2_session(conn, SID, t)
        _v2_turn(conn, SID, 1, t, content=[v2_bash("gh pr create", URL.format(1))])
        _v2_turn(conn, SID, 2, t + 1000, content=[
            v2_bash("gh pr create", status="error",
                    error="already exists:\n" + URL.format(2)),
        ])
        _v2_turn(conn, SID, 3, t + 2000, content=[
            v2_bash("gh pr view 99", URL.format(99)),
            {"type": "text", "text": "see " + URL.format(98)},
        ])
        conn.commit()

        stats = Opencode2StatsReader().get_stats(_session(SID))
        assert stats.pr_number == 2

    def test_user_text_with_a_url_does_not_link(self, v2_env):
        _path, conn = v2_env
        t = LAUNCH_MS + 1000
        _v2_session(conn, SID, t)
        _v2_turn(conn, SID, 1, t, mtype="user",
                 content=[v2_bash("gh pr create", URL.format(4))])
        _v2_turn(conn, SID, 2, t + 1000, content=[{"type": "text", "text": "ok"}])
        conn.commit()

        assert Opencode2StatsReader().get_stats(_session(SID)).pr_number is None


# -- the daemon takes it like Claude's pr-link ----------------------------


class TestDaemonTakesIt:
    def _daemon(self, tmp_path, monkeypatch):
        from overcode.monitor_daemon import MonitorDaemon

        for name, value in (
            ("ensure_session_dir", lambda x: tmp_path),
            ("get_monitor_daemon_pid_path", lambda x: tmp_path / "pid"),
            ("get_monitor_daemon_state_path", lambda x: tmp_path / "state.json"),
            ("get_agent_history_path", lambda x: tmp_path / "history.csv"),
        ):
            monkeypatch.setattr(f"overcode.monitor_daemon.{name}", value)
        with patch("overcode.monitor_daemon.SessionManager") as sm, \
                patch("overcode.monitor_daemon.StatusDetectorDispatcher"):
            daemon = MonitorDaemon(tmux_session="test")
            daemon.session_manager = sm.return_value
        return daemon

    @pytest.mark.parametrize("backend", ["opencode", "opencode2"])
    def test_sync_stages_the_pr_and_stops_the_scrape(self, backend, v2_env, tmp_path,
                                                      monkeypatch):
        path, conn = v2_env
        t = LAUNCH_MS + 1000
        if backend == "opencode":
            _v1_session(conn, SID, t)
            _v1_turn(conn, SID, 1, t, parts=[v1_bash("gh pr create", URL.format(64))])
            reader = OpencodeStatsReader(db_path=path)
        else:
            _v2_session(conn, SID, t)
            _v2_turn(conn, SID, 1, t, content=[v2_bash("gh pr create", URL.format(64))])
            reader = Opencode2StatsReader()
        conn.commit()

        daemon = self._daemon(tmp_path, monkeypatch)
        session = Mock(**vars(_session(SID)))
        session.backend = backend
        session.branch = "fix-thing"
        session.pr_number = None
        session.model = session.effort = session.provider = session.agent_persona = None
        session.stats = Mock()
        monkeypatch.setattr("overcode.daemon_stats_sync.stats_reader_for_session",
                            lambda s: reader)
        daemon.sync_agent_stats(session)

        staged = daemon._pending.fields["agent-1"]
        assert staged["pr_number"] == 64
        assert staged["pr_branch"] == "fix-thing"
        # The pane scrape is gated off once the stored output named the PR.
        assert daemon.stats_sync.transcript_prs["agent-1"] == 64


def test_both_readers_share_the_matcher():
    assert opencode2_stats.pr_from_tool_call is pr_from_tool_call
