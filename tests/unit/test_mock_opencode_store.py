"""The opencode mock's store and telemetry, read back through overcode (#534).

The container tier trusts ``tests/mock_opencode.py`` to leave what real
opencode leaves: a SQLite conversation the stats reader prices, and hook
state (from the installed telemetry plugin) naming that conversation. These
tests pin both against the real reader, so a mock that drifts from what
overcode reads fails here, on the host, rather than as a confusing
container timeout.
"""

import json
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import mock_opencode  # noqa: E402
from overcode.backends.opencode import ensure_plugin_installed  # noqa: E402
from overcode.backends.opencode_stats import OpencodeStatsReader  # noqa: E402
# The module the mock imports (same sys.path entry), so exception classes match
from opencode_store import MockStore, configured_path  # noqa: E402

node = pytest.mark.skipif(shutil.which("node") is None,
                          reason="node is required to run the plugin")


@pytest.fixture
def store(tmp_path):
    return MockStore(tmp_path / "data" / "opencode.db")


def agent(directory, **over):
    base = dict(
        id="agent-1", name="oc-agent", tmux_session="agents",
        agent_session_ids=[], active_agent_session_id=None,
        start_directory=str(directory),
        start_time=(datetime.now() - timedelta(seconds=5)).isoformat(),
        wrapper=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def run_steps(runner, steps):
    for step in steps:
        runner.execute_step(step)


class TestConfiguredPath:
    def test_never_defaults_to_the_real_store(self):
        assert configured_path({"HOME": "/home/someone"}) is None

    def test_honours_opencodes_env_vars(self, tmp_path):
        assert configured_path({"OPENCODE_DB": "/x/y.db"}) == Path("/x/y.db")
        assert configured_path({"OPENCODE_DATA_DIR": "/d"}) == Path("/d/opencode.db")


class TestStoreReadsBack:
    def test_turn_cost_and_tokens_reach_the_reader(self, store, tmp_path):
        sid = store.create_session(str(tmp_path), "openai/gpt-4o-mini")
        store.add_user_message(sid, "hi")
        store.add_assistant_turn(sid, cost=0.75, **mock_opencode.TURN_TOKENS)

        reader = OpencodeStatsReader(db_path=store.path)
        session = agent(tmp_path, agent_session_ids=[sid], active_agent_session_id=sid)
        stats = reader.get_stats(session)
        assert stats is not None
        assert stats.input_tokens == 7000
        assert stats.output_tokens == 120  # reasoning folds into output
        assert stats.interaction_count == 1
        assert stats.model == "openai/gpt-4o-mini"
        assert reader.get_stored_cost(session) == pytest.approx(0.75)

    def test_fresh_conversation_found_by_directory(self, store, tmp_path):
        sid = store.create_session(str(tmp_path))
        reader = OpencodeStatsReader(db_path=store.path)
        session = agent(tmp_path)
        found = reader.discover_session_ids(
            session, datetime.fromisoformat(session.start_time), [session])
        assert found.latest == sid

    def test_fork_is_a_new_root_with_the_sources_messages(self, store, tmp_path):
        source = store.create_session(str(tmp_path), title="Primes")
        store.add_user_message(source, "hi")
        store.add_assistant_turn(source, cost=0.75, **mock_opencode.TURN_TOKENS)

        fork = store.fork_session(source, str(tmp_path))
        row = store.session(fork)
        assert fork != source and fork.startswith("ses_")
        assert row["parent_id"] is None
        assert row["title"] == "Primes (fork #1)"
        assert row["cost"] == 0

        reader = OpencodeStatsReader(db_path=store.path)
        stats = reader.get_stats(
            agent(tmp_path, agent_session_ids=[fork], active_agent_session_id=fork))
        assert stats.interaction_count == 1  # the copied history

    def test_fork_of_an_unknown_id_is_refused(self, store, tmp_path):
        with pytest.raises(mock_opencode.SessionNotFound):
            store.fork_session("ses_nope", str(tmp_path))


class TestStepsWithoutAStore:
    def test_oc_steps_are_no_ops(self):
        runner = mock_opencode.OpencodeRunner({"steps": []})
        run_steps(runner, [{"type": "oc_prompt", "text": "x"}, {"type": "oc_reply"}])
        assert runner.session_id is None


@node
class TestTelemetryThroughTheInstalledPlugin:
    """The mock drives the plugin overcode installed, and overcode's reader
    adopts the conversation it names, the way it does for real opencode."""

    def test_hook_state_names_the_mocks_conversation(self, store, tmp_path, monkeypatch):
        project = tmp_path / "project"
        project.mkdir()
        assert ensure_plugin_installed(str(project)) is not None
        monkeypatch.chdir(project)
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "oc-agent")
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        monkeypatch.setenv("OVERCODE_SESSION_ID", "agent-1")
        # This test's own state dir: hook state must not outlive it
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "state"))
        log = tmp_path / "mock.log"
        telemetry = mock_opencode.Telemetry(project, log)
        assert telemetry.plugin is not None, log.read_text() if log.exists() else ""

        runner = mock_opencode.OpencodeRunner({"steps": []}, store, telemetry)
        session = agent(project)
        run_steps(runner, [{"type": "oc_prompt", "text": "list primes"},
                           {"type": "oc_reply"}])
        sid = runner.session_id
        assert sid and sid.startswith("ses_")

        reader = OpencodeStatsReader(db_path=store.path)
        found = reader.discover_session_ids(
            session, datetime.fromisoformat(session.start_time), [session])
        assert found.latest == sid
        from overcode.backends._stats_common import read_hook_state
        state = read_hook_state(session)
        assert state["event"] == "Stop"
        assert state["agent_session_id"] == sid
        assert not log.exists() or "failed" not in log.read_text()

    def test_permission_steps_publish_waiting_approval(self, store, tmp_path, monkeypatch):
        project = tmp_path / "project"
        project.mkdir()
        ensure_plugin_installed(str(project))
        monkeypatch.chdir(project)
        monkeypatch.setenv("OVERCODE_SESSION_NAME", "oc-agent")
        monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
        monkeypatch.setenv("OVERCODE_SESSION_ID", "agent-1")
        # This test's own state dir: hook state must not outlive it
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "state"))
        telemetry = mock_opencode.Telemetry(project, tmp_path / "mock.log")
        runner = mock_opencode.OpencodeRunner({"steps": []}, store, telemetry)
        run_steps(runner, [{"type": "oc_prompt", "text": "echo"},
                           {"type": "oc_permission", "command": "echo hello"}])
        from overcode.backends._stats_common import read_hook_state
        state = read_hook_state(agent(project))
        assert state["event"] == "PermissionRequest"
        assert json.dumps(state.get("tool_input", {})).count("echo hello")
