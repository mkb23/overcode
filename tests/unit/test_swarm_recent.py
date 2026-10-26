"""
"recent" finished subagents (the default): ✅ counts only what finished within
observability.recent_finished_hours (6 by default, config.yaml only); ❌ counts
every failure; the hover says how many there were in all.
"""

import pytest

from overcode import swarm, swarm_view

pytestmark = pytest.mark.unit

H = 3600.0


def _state(done, folded=None):
    return {"event": "Stop", "subagents": {},
            "swarm": {"done": done, **({"folded": folded} if folded else {})}}


def _done(ended_at, result="ok"):
    return {"kind": "subagent", "result": result, "ended_at": ended_at}


class TestRollup:
    def test_ok_recent_counts_successes_in_the_window(self):
        now = 100 * H
        state = _state({"a": _done(now - 7 * H), "b": _done(now - 5 * H),
                        "c": _done(now - 1), "d": _done(now - 1, "failed")},
                       folded={"agents_ok": 40})
        r = swarm.rollup(state, now, recent_seconds=6 * H)
        assert r["ok"] == 43 and r["failed"] == 1
        assert r["ok_recent"] == 2  # b and c; a is older, folded ones carry no time
        assert r["recent_hours"] == 6

    def test_the_count_holds_still_until_one_ages_out(self):
        state = _state({"a": _done(0.0)})
        assert swarm.rollup(state, 6 * H - 1, recent_seconds=6 * H)["ok_recent"] == 1
        assert swarm.rollup(state, 6 * H, recent_seconds=6 * H)["ok_recent"] == 1
        assert swarm.rollup(state, 6 * H + 1, recent_seconds=6 * H)["ok_recent"] == 0

    def test_without_a_window_nothing_is_added(self):
        r = swarm.rollup(_state({"a": _done(0.0)}), 1.0)
        assert "ok_recent" not in r and "recent_hours" not in r


class TestShown:
    ROLLUP = {"total": 12, "ok": 11, "failed": 1, "ok_recent": 3, "recent_hours": 6.0}

    def test_recent_shows_the_recent_successes_and_every_failure(self):
        shown = swarm_view.shown_rollup(self.ROLLUP, swarm_view.FINISHED_RECENT)
        assert (shown["ok"], shown["ok_all"], shown["failed"], shown["total"]) == (3, 11, 1, 12)

    def test_show_is_every_one(self):
        assert swarm_view.shown_rollup(self.ROLLUP, swarm_view.FINISHED_SHOW)["ok"] == 11

    def test_an_older_daemon_without_the_window_shows_them_all(self):
        rollup = {"total": 2, "ok": 2, "failed": 0}
        assert swarm_view.shown_rollup(rollup, swarm_view.FINISHED_RECENT) == rollup

    def test_recent_keeps_finished_rows(self):
        assert swarm_view.finished_shown({"result": "ok"}, swarm_view.FINISHED_RECENT)

    def test_it_is_the_default(self):
        from overcode.settings import TUIPreferences
        assert TUIPreferences().swarm_finished == swarm_view.FINISHED_RECENT
        assert swarm_view.FINISHED_RECENT in swarm_view.FINISHED_MODES


class TestHover:
    def test_says_the_window_and_the_whole(self):
        from overcode.summary_columns import COLUMNS_BY_ID
        from tests.unit.test_summary_columns import _make_ctx
        shown = swarm_view.shown_rollup(
            {"total": 12, "running": 0, "waiting": 0, "idle": 0, "ok": 12, "failed": 0,
             "workflows": 0, "workflows_running": 0, "ok_recent": 2, "recent_hours": 6.0},
            swarm_view.FINISHED_RECENT)
        text = COLUMNS_BY_ID["subagents"].hover(_make_ctx(swarm_rollup=shown)).plain
        assert "2 finished in the last 6h (12 in all)" in text


class TestConfig:
    def test_default_and_value(self, monkeypatch):
        from overcode import config
        monkeypatch.setattr(config, "_cached_config", lambda: {})
        assert config.get_observability_config()["recent_finished_hours"] == 6.0
        monkeypatch.setattr(config, "_cached_config",
                            lambda: {"observability": {"recent_finished_hours": 24}})
        assert config.get_observability_config()["recent_finished_hours"] == 24.0
        monkeypatch.setattr(config, "_cached_config",
                            lambda: {"observability": {"recent_finished_hours": "soon"}})
        assert config.get_observability_config()["recent_finished_hours"] == 6.0
