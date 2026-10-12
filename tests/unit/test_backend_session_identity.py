"""Non-Claude agents keep their own conversation, never a dead namesake's or a neighbour's.

The wrong-session bug fixed for Claude in 13dc539 (an agent showing another
conversation's model, effort and tokens), for codex, hermes, opencode and
opencode2. Two ways in:

* ``kill`` + ``launch`` under the same name leaves the old agent's
  ``hook_state_<name>.json``. Its ids are not the new record's, so they must
  not become the new agent's conversation.
* Without hook ids, the readers fall back to "a conversation in this
  directory since launch". The newest one there can be another overcode
  agent's; the fallback must skip it, and must not run at all while the
  agent's own hooks are reporting.

Each backend is driven through its real reader against a synthetic store;
the hook state is written the way ``hook_handler`` / the opencode plugin
write it.
"""

import json
import sqlite3
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from overcode.backends import _stats_common
from overcode.backends.codex_stats import CodexStatsReader
from overcode.backends.hermes_stats import HermesStatsReader
from overcode.backends.opencode2_stats import Opencode2StatsReader
from overcode.backends.opencode_stats import OpencodeStatsReader
from tests.unit.test_codex_stats import (
    rollout_path,
    session_meta_line,
    token_count_event,
    turn_context_event,
    user_turn_item,
    write_rollout,
)
from tests.unit.test_hermes_stats import add_session as hermes_add_session
from tests.unit.test_hermes_stats import make_db as hermes_make_db
from tests.unit.test_opencode2_stats import SESSION_V2_DDL
from tests.unit.test_opencode_stats import MESSAGE_DDL, SESSION_DDL
from tests.unit.test_opencode_stats import insert_session as opencode_insert_session

LAUNCH = datetime(2026, 9, 20, 12, 0, 0)
DIR = "/tmp/identity-project"
NAME = "alpha"

OWN_MODEL, DEAD_MODEL, OTHER_MODEL = "own-model", "dead-model", "other-model"


def agent(**over):
    """The relaunched agent: a fresh record, nothing adopted yet."""
    base = dict(
        id="agent-new", name=NAME, tmux_session="agents",
        agent_session_ids=[], active_agent_session_id=None,
        start_directory=DIR, start_time=LAUNCH.isoformat(), wrapper=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def other_agent(sid):
    """Another overcode agent working in the same directory."""
    return SimpleNamespace(id="agent-other", name="beta", tmux_session="agents",
                           agent_session_ids=[sid], active_agent_session_id=sid)


@pytest.fixture
def hook_state(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(state_dir))

    def write(active, ids=(), at=None):
        path = state_dir / "agents" / f"hook_state_{NAME}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "event": "Stop",
            "timestamp": (at or LAUNCH + timedelta(seconds=5)).timestamp(),
            "agent_session_ids": [*ids, active],
            "agent_session_id": active,
        }))

    return write


def stale(write, dead):
    """The dead agent's last hook, written before the new agent launched."""
    write(dead, at=LAUNCH - timedelta(minutes=30))


# -- per-backend stores ------------------------------------------------------
#
# Each world has three conversations in DIR: the dead agent's (started
# before the relaunch), this agent's own, and another overcode agent's
# (started after this agent's, so it is the directory's newest).


class CodexWorld:
    DEAD, OWN, OTHER = (
        "01a0439d-0000-7000-8000-00000000dead",
        "01a0439d-0000-7000-8000-000000000001",
        "01a0439d-0000-7000-8000-000000000002",
    )

    def __init__(self, tmp_path):
        root = tmp_path / "codex-sessions"
        for sid, ts, model in (
            (self.DEAD, "2026-09-20T11-30-00", DEAD_MODEL),
            (self.OWN, "2026-09-20T12-00-05", OWN_MODEL),
            (self.OTHER, "2026-09-20T12-00-09", OTHER_MODEL),
        ):
            write_rollout(rollout_path(root, sid, ts=ts, dt=LAUNCH), [
                session_meta_line(session_id=sid, cwd=DIR), turn_context_event(model),
                user_turn_item(), token_count_event(),
            ])
        self.reader = CodexStatsReader(sessions_dir=root)

    def drop_other(self):
        for path in self.reader._root().rglob(f"*{self.OTHER}.jsonl"):
            path.unlink()


class HermesWorld:
    DEAD, OWN, OTHER = "20260920_113000_dead00", "20260920_120005_aaaaaa", "20260920_120009_bbbbbb"

    def __init__(self, tmp_path):
        path = tmp_path / "state.db"
        self.conn = hermes_make_db(path)
        launch = LAUNCH.timestamp()
        for sid, started, model in (
            (self.DEAD, launch - 1800, DEAD_MODEL),
            (self.OWN, launch + 5, OWN_MODEL),
            (self.OTHER, launch + 9, OTHER_MODEL),
        ):
            hermes_add_session(self.conn, sid, started=started, cwd=DIR, model=model)
        self.conn.commit()
        self.reader = HermesStatsReader(path)

    def drop_other(self):
        self.conn.execute("DELETE FROM sessions WHERE id = ?", (self.OTHER,))
        self.conn.commit()


def _model_json(model):
    return json.dumps({"id": model, "providerID": "acme"})


class OpencodeWorld:
    DEAD, OWN, OTHER = "ses_dead0000000000000000000000", "ses_own00000000000000000000000", "ses_other000000000000000000000"

    def __init__(self, tmp_path):
        path = tmp_path / "opencode.db"
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SESSION_DDL + ";" + MESSAGE_DDL)
        launch_ms = int(LAUNCH.timestamp() * 1000)
        for sid, created, model in (
            (self.DEAD, launch_ms - 1_800_000, DEAD_MODEL),
            (self.OWN, launch_ms + 5_000, OWN_MODEL),
            (self.OTHER, launch_ms + 9_000, OTHER_MODEL),
        ):
            opencode_insert_session(self.conn, sid, directory=DIR, model=_model_json(model),
                                    time_created=created, time_updated=created + 100)
        self.conn.commit()
        self.reader = OpencodeStatsReader(db_path=path)

    def drop_other(self):
        self.conn.execute("DELETE FROM session WHERE id = ?", (self.OTHER,))
        self.conn.commit()


class Opencode2World:
    DEAD, OWN, OTHER = OpencodeWorld.DEAD, OpencodeWorld.OWN, OpencodeWorld.OTHER

    def __init__(self, tmp_path):
        path = tmp_path / "opencode.db"
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SESSION_V2_DDL)
        launch_ms = int(LAUNCH.timestamp() * 1000)
        for sid, created, model in (
            (self.DEAD, launch_ms - 1_800_000, DEAD_MODEL),
            (self.OWN, launch_ms + 5_000, OWN_MODEL),
            (self.OTHER, launch_ms + 9_000, OTHER_MODEL),
        ):
            self.conn.execute(
                "INSERT INTO session_v2 (id, directory, parent_id, cost, tokens_input,"
                " tokens_output, tokens_reasoning, tokens_cache_read, tokens_cache_write,"
                " model, agent, time_created, time_updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, DIR, None, 0.01, 1000, 10, 0, 0, 0, _model_json(model), "build",
                 created, created + 100),
            )
        self.conn.commit()
        self.reader = Opencode2StatsReader(db_path=path)

    def drop_other(self):
        self.conn.execute("DELETE FROM session_v2 WHERE id = ?", (self.OTHER,))
        self.conn.commit()


WORLDS = {
    "codex": CodexWorld,
    "hermes": HermesWorld,
    "opencode": OpencodeWorld,
    "opencode2": Opencode2World,
}


@pytest.fixture(params=sorted(WORLDS))
def world(request, tmp_path):
    return WORLDS[request.param](tmp_path)


def _model(world, sid):
    stats = world.reader.get_stats(agent(agent_session_ids=[sid], active_agent_session_id=sid))
    return stats.model if stats else None


class TestRelaunchUnderADeadAgentsName:

    def test_the_dead_agents_hook_state_does_not_pin_the_new_agent(self, world, hook_state):
        stale(hook_state, world.DEAD)
        world.drop_other()
        me = agent()

        current = world.reader.get_current_session_id(me, LAUNCH, all_sessions=[me])
        found = world.reader.discover_session_ids(me, LAUNCH, [me])

        assert current == world.OWN
        assert world.DEAD not in found.ids and found.latest == world.OWN

    def test_the_dead_agents_id_carried_into_a_fresh_hook_state_is_not_adopted(
        self, world, hook_state,
    ):
        """The new process's first hook rewrote the file, but it still names the dead id."""
        hook_state(world.DEAD)
        me = agent()

        assert world.reader.get_current_session_id(me, LAUNCH, all_sessions=[me]) is None
        found = world.reader.discover_session_ids(me, LAUNCH, [me])
        assert found.ids == [] and found.latest is None
        # Its hooks are reporting, so the directory is not consulted either.
        assert world.reader.get_stats(me) is None

    def test_the_agents_own_hook_id_wins_over_the_dead_ones_in_the_list(self, world, hook_state):
        hook_state(world.OWN, ids=[world.DEAD])
        me = agent(agent_session_ids=[world.OWN], active_agent_session_id=world.OWN)

        assert world.reader.get_current_session_id(me, LAUNCH, all_sessions=[me]) == world.OWN
        assert world.DEAD not in world.reader.discover_session_ids(me, LAUNCH, [me]).ids
        assert world.reader.get_stats(me).model.endswith(OWN_MODEL)


class TestTheDirectoryFallback:

    def test_skips_another_agents_conversation(self, world, hook_state):
        me, other = agent(), other_agent(world.OTHER)

        current = world.reader.get_current_session_id(me, LAUNCH, all_sessions=[me, other])
        found = world.reader.discover_session_ids(me, LAUNCH, [me, other])

        assert current == world.OWN
        assert world.OTHER not in found.ids and found.latest == world.OWN
        assert _model(world, world.OWN).endswith(OWN_MODEL)


class TestOpencodePluginIds:
    """The opencode plugins write hook state without touching sessions.json."""

    @pytest.fixture(params=["opencode", "opencode2"])
    def oc_world(self, request, tmp_path):
        return WORLDS[request.param](tmp_path)

    def test_a_conversation_started_since_launch_is_adopted(self, oc_world, hook_state):
        hook_state(oc_world.OWN, ids=[oc_world.DEAD])
        me = agent()

        assert oc_world.reader.get_current_session_id(me, LAUNCH, all_sessions=[me]) == oc_world.OWN
        found = oc_world.reader.discover_session_ids(me, LAUNCH, [me])
        assert found.ids == [oc_world.OWN] and found.latest == oc_world.OWN


class TestAgentHookIds:
    """The shared lookup every reader (and Claude's) goes through."""

    def test_no_hook_state(self, hook_state):
        assert _stats_common.agent_hook_ids(agent()) == _stats_common.HookIds()

    def test_only_ids_the_record_holds(self, hook_state):
        hook_state("mine", ids=["dead"])
        hooks = _stats_common.agent_hook_ids(agent(agent_session_ids=["mine"]))
        assert hooks.own == ("mine",) and hooks.live

    def test_vouched_ids_count(self, hook_state):
        hook_state("new", ids=["dead"])
        hooks = _stats_common.agent_hook_ids(agent(), vouch=lambda ids: [s for s in ids if s == "new"])
        assert hooks.own == ("new",)

    def test_a_file_from_before_launch_is_not_live(self, hook_state):
        stale(hook_state, "dead")
        assert _stats_common.agent_hook_ids(agent()) == _stats_common.HookIds(own=(), live=False)
        assert not _stats_common.hooks_live(agent())

    def test_unknown_launch_time_counts_as_live(self, hook_state):
        stale(hook_state, "dead")
        assert _stats_common.hooks_live(agent(start_time=None))

    def test_others_owned_ids_excludes_the_agent_itself(self):
        me = agent(agent_session_ids=["a"])
        assert _stats_common.others_owned_ids(me, [me, other_agent("b")]) == frozenset({"b"})
