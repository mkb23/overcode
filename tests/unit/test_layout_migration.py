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
        assert sm.ledger.totals()["shirka"]["cost"] == 4.0
        assert sm.get_session("live-1").billing_code == "worker"
        # Idempotent
        session_dir("agents").joinpath(".layout").unlink()
        again = migrate_layout("agents", sm)
        assert again["backfilled"] == 0 and again["coded"] == 0
        assert sm.ledger.totals()["shirka"]["cost"] == 4.0


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
