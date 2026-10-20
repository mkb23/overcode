"""layout_migration: a session's storage moved to the 0.6 layout in batches."""

import json
from unittest.mock import MagicMock

import pytest

from overcode import agent_bundle
from overcode.daemon_housekeeping import Housekeeping
from overcode.layout_migration import (
    LAYOUT_VERSION,
    layout_version,
    legacy_files,
    legacy_name,
    migrate_layout,
)
from overcode.paths import agent_dir, agent_path, legacy_agent_file, session_dir
from overcode.session_manager import Session, SessionManager, SessionStats


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "sessions"))
    agent_bundle._checked_tails.clear()
    return tmp_path / "sessions"


@pytest.fixture
def sm():
    return SessionManager(skip_git_detection=True)


def _leftovers(name, tmux="agents"):
    """What a pre-0.6 agent left in the session directory."""
    for kind, text in (("hook_state", '{"event": "Stop", "timestamp": 1}'),
                       ("hook_events", '{"event": "Stop"}\n'),
                       ("report", '{"status": "success"}')):
        path = legacy_agent_file(tmux, name, kind)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    legacy_agent_file(tmux, name, "hook_state").with_name(
        f"hook_state_{name}.json.lock").write_text("")


class TestLegacyNames:
    @pytest.mark.parametrize("filename, expected", [
        ("hook_state_a.json", ("a", "hook_state")),
        ("hook_state_my-agent.json.lock", ("my-agent", "hook_state")),
        ("hook_events_x_y.jsonl", ("x_y", "hook_events")),
        ("episodes_a.jsonl.1", ("a", "episodes")),
        ("report_a.json", ("a", "report")),
        ("rename_notice_a.txt", ("a", "rename_notice")),
        ("heartbeat_a.last", ("a", "heartbeat")),
        ("monitor_daemon_state.json", None),
        ("agent_status_history.csv", None),
        ("hook_state_.json", None),
    ])
    def test_parse(self, filename, expected):
        assert legacy_name(filename) == expected


class TestMigrate:
    def test_dead_agents_files_are_packed_and_removed(self, sm):
        _leftovers("old-1")
        _leftovers("old-2")
        counts = migrate_layout("agents", sm)
        assert counts["packed"] == 2 and counts["done"] == 1
        assert legacy_files("agents") == {}
        assert layout_version("agents") == LAYOUT_VERSION
        (day,) = [d for _, d, _ in agent_bundle.bundle_day_files()]
        members = list(agent_bundle.iter_day("agents", day))
        assert {m["name"] for m in members} == {"old-1", "old-2"}
        assert all(m["legacy"] for m in members)
        assert members[0]["files"]["report_" + members[0]["name"] + ".json"] == {"status": "success"}
        # Done: the next call reads the marker and nothing else
        assert migrate_layout("agents", sm)["done"] == 1

    def test_a_live_agent_launched_before_keeps_its_files_until_relaunched(self, sm):
        live = sm.create_session(name="still-here", tmux_session="agents",
                                 tmux_window="still-here-1", command=[])
        _leftovers("still-here")
        _leftovers("gone")
        counts = migrate_layout("agents", sm)
        assert counts["packed"] == 1 and counts["skipped"] == 1 and counts["done"] == 0
        assert layout_version("agents") == "1"
        # Its plugin still writes by name, and detection still reads it there
        assert agent_path("agents", live.id, "still-here", "hook_state") == \
            legacy_agent_file("agents", "still-here", "hook_state")
        # Relaunched by this overcode: it has its own directory now
        agent_dir("agents", live.id).mkdir(parents=True)
        counts = migrate_layout("agents", sm)
        assert counts["packed"] == 1 and counts["done"] == 1
        assert legacy_files("agents") == {}

    def test_batches(self, sm):
        for i in range(5):
            _leftovers(f"old-{i}")
        counts = migrate_layout("agents", sm, batch=2)
        assert counts["packed"] == 2 and counts["left"] == 3 and counts["done"] == 0
        assert len(legacy_files("agents")) == 3
        migrate_layout("agents", sm, batch=2)
        assert migrate_layout("agents", sm, batch=2)["done"] == 1

    def test_archive_ledger_and_billing_codes(self, sm, state):
        old = Session(id="arch-1", name="shirka", tmux_session="agents", tmux_window="w",
                      command=["claude", "--secret"], start_directory=None,
                      start_time="2026-08-01T00:00:00",
                      stats=SessionStats(estimated_cost_usd=4.0))
        record = dict(old.to_dict(), status="archived", end_time="2026-08-02T00:00:00")
        sm.old_archive_file.write_text(json.dumps(record) + "\n")
        sm._save_state({"live-1": dict(Session(
            id="live-1", name="worker", tmux_session="agents", tmux_window="worker-1",
            command=[], start_directory=None, start_time="2026-10-01T00:00:00").to_dict(),
            billing_code=None)})
        counts = migrate_layout("agents", sm)
        assert counts["backfilled"] == 1 and counts["coded"] == 1
        assert not sm.old_archive_file.exists()
        assert sm.find_archived("shirka").id == "arch-1"
        assert sm.ledger.named(sm.ledger.totals())["shirka"]["cost"] == 4.0
        assert sm.get_session("live-1").billing_code == "worker"
        # Idempotent
        session_dir("agents").joinpath(".layout").unlink()
        again = migrate_layout("agents", sm)
        assert again["backfilled"] == 0 and again["coded"] == 0
        assert sm.ledger.named(sm.ledger.totals())["shirka"]["cost"] == 4.0


def _old_record(sid, name, cost, parent=None, end="2026-08-02T00:00:00"):
    """A record as an older overcode archived it: no billing code."""
    session = Session(id=sid, name=name, tmux_session="agents", tmux_window=name,
                      command=[], start_directory=None, start_time="2026-08-01T00:00:00",
                      parent_session_id=parent, stats=SessionStats(estimated_cost_usd=cost))
    return dict(session.to_dict(), status="archived", end_time=end, billing_code=None)


class TestChildrenBillToTheParent:
    def test_live_and_archived_children_take_the_root_code(self, sm):
        sm.old_archive_file.write_text(
            json.dumps(_old_record("docs", "proj-docs", 2.0, parent="proj")) + "\n")
        live = {}
        for sid, name, parent in (("proj", "proj", None), ("tests", "proj-tests", "proj"),
                                  ("grand", "proj-tests-sub", "tests")):
            live[sid] = dict(Session(
                id=sid, name=name, tmux_session="agents", tmux_window=name, command=[],
                start_directory=None, start_time="2026-10-01T00:00:00",
                parent_session_id=parent).to_dict(), billing_code=None)
        sm._save_state(live)
        migrate_layout("agents", sm)
        assert {s.id: s.billing_code for s in sm.list_sessions()} == {
            "proj": "proj", "tests": "proj", "grand": "proj"}
        assert set(sm.ledger.named(sm.ledger.totals())) == {"proj"}

    def test_an_archived_child_of_an_archived_parent(self, sm):
        sm.old_archive_file.write_text(
            json.dumps(_old_record("root", "lead", 1.0)) + "\n"
            + json.dumps(_old_record("kid", "lead-kid", 3.0, parent="root")) + "\n")
        migrate_layout("agents", sm)
        totals = sm.ledger.named(sm.ledger.totals())
        assert set(totals) == {"lead"} and totals["lead"]["cost"] == 4.0


class TestReupgrade:
    def test_agents_archived_by_an_older_overcode_after_the_upgrade_get_rows(self, sm):
        sm.old_archive_file.write_text(json.dumps(_old_record("a", "first", 1.0)) + "\n")
        assert migrate_layout("agents", sm)["done"] == 1
        # A checkpoint of a live agent, then a downgrade archives it and another
        sm.ledger.append([{"sid": "b", "billing_code": "second", "seg": 0, "cost": 2.0,
                           "tokens": 0, "base_cost": 0.0, "base_tokens": 0,
                           "kind": "checkpoint", "seq": 1, "t": "2026-10-01T00:00:00"}])
        sm.old_archive_file.write_text(
            json.dumps(_old_record("b", "second", 5.0, end="2026-10-03T00:00:00")) + "\n"
            + json.dumps(_old_record("c", "third", 7.0, end="2026-10-03T00:00:00")) + "\n")
        _leftovers("third")
        hk = Housekeeping("agents", tmux=MagicMock(), history_path=None, log=MagicMock(),
                          housekeeping=MagicMock(), rotation=MagicMock(),
                          model_metadata=MagicMock())
        hk.migrate_storage(sm)  # the re-upgraded daemon starts
        totals = sm.ledger.named(sm.ledger.totals())
        assert totals["first"]["cost"] == 1.0
        assert (totals["second"]["cost"], totals["second"]["running"]) == (5.0, 0)
        assert totals["third"]["cost"] == 7.0
        assert legacy_files("agents") == {}
        # Idempotent by agent
        sm.ledger.invalidate_backfill()
        assert sm.ledger.backfill_from_archive(sm) == 0


def _old_child(sm, parent_id, sid="kid1", name="kid"):
    """A child an older overcode launched after the migration: no billing fields."""
    with sm._state_transaction() as txn:
        entry = dict(txn.state[parent_id], id=sid, name=name,
                     parent_session_id=parent_id, tmux_window=f"{name}-1")
        for key in [k for k in entry if k.startswith("billing_")]:
            entry.pop(key)
        txn.state[sid] = entry
        txn.dirty = True


def _housekeeping():
    return Housekeeping("agents", tmux=MagicMock(), history_path=None, log=MagicMock(),
                        housekeeping=MagicMock(), rotation=MagicMock(),
                        model_metadata=MagicMock())


class TestChildLaunchedByAnOlderOvercode:
    """0.5.8 (a downgrade, or one still running) launches a child after the migration."""

    def _parent(self, sm):
        return sm.create_session(name="parent", tmux_session="agents",
                                 tmux_window="parent-1", command=["claude"],
                                 billing_code="ACME")

    def test_the_next_migration_call_gives_it_the_parent_code(self, sm):
        parent = self._parent(sm)
        assert migrate_layout("agents", sm)["done"] == 1
        _old_child(sm, parent.id)
        counts = migrate_layout("agents", sm)
        assert (counts["done"], counts["coded"]) == (1, 1)
        assert sm.get_session("kid1").billing_code == "ACME"
        assert migrate_layout("agents", sm)["coded"] == 0

    def test_the_daemon_gives_it_the_parent_code_after_it_migrated(self, sm):
        from overcode.ledger import billing_name_of as billing_code_of

        parent = self._parent(sm)
        hk = _housekeeping()
        hk.migrate_storage(sm)
        assert hk.layout_migrated
        _old_child(sm, parent.id)
        sessions = sm.list_sessions()
        views = hk.fill_billing_codes(sessions, sm)
        assert {s.id: billing_code_of(s) for s in views} == {parent.id: "ACME", "kid1": "ACME"}
        assert sm.get_session("kid1").billing_code == "ACME"
        # The snapshot is not touched; a stale one gets the code on disk
        assert [s.billing_code for s in sessions if s.id == "kid1"] == [None]
        again = {s.id: s.billing_code for s in hk.fill_billing_codes(sessions, sm)}
        assert again["kid1"] == "ACME"

    def test_nothing_missing_reads_nothing(self, sm):
        self._parent(sm)
        sessions = sm.list_sessions()
        sm.list_sessions = MagicMock(side_effect=AssertionError("read"))
        sm._state_transaction = MagicMock(side_effect=AssertionError("write"))
        assert _housekeeping().fill_billing_codes(sessions, sm) == sessions


class TestDaemonRunsIt:
    def test_each_pass_until_done_then_never(self, sm):
        _leftovers("old")
        hk = Housekeeping("agents", tmux=MagicMock(), history_path=None, log=MagicMock(),
                          housekeeping=MagicMock(), rotation=MagicMock(),
                          model_metadata=MagicMock())
        hk.migrate_storage(sm)
        assert hk.layout_migrated and layout_version("agents") == LAYOUT_VERSION
        _leftovers("late")  # an older overcode wrote one since: not this process's job
        hk.migrate_storage(sm)
        assert legacy_files("agents")
