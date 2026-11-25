"""The EVAL column: what an agent's evals and sims cost, beside $ (experiment billing).

Published by the daemon (``external_cost_usd``), drawn by the TUI and
``overcode list``. Synthetic sessions only.
"""

from types import SimpleNamespace

from overcode.columns.context import FleetFlags
from overcode.columns.hover import hover_eval_cost
from overcode.columns.render_usage import (
    EVAL_W, render_cost, render_cost_plain, render_eval_cost, render_eval_cost_plain,
)
from overcode.monitor_daemon_state import SessionDaemonState
from overcode.session_manager import SessionStats


def _ctx(own=1.0, evals=0.0, budget=0.0, trip=None):
    stats = SessionStats(estimated_cost_usd=own, external_cost_usd=evals)
    session = SimpleNamespace(stats=stats, cost_budget_usd=budget)
    return SimpleNamespace(session=session, stats=stats, claude_stats=object(), bg="",
                           trip=trip, fleet=FleetFlags(any_has_eval_cost=evals > 0),
                           e=lambda s: s, subagent_cost_usd=None)


def _text(cells):
    return "".join(t for t, _ in cells)


class TestCell:
    def test_value_and_placeholder_have_one_width(self):
        assert _text(render_eval_cost(_ctx(evals=6.25))) == " +$6.25"
        for value in (0.0, 0.04, 6.25, 99.0, 1234.5, 2.5e6):
            assert len(_text(render_eval_cost(_ctx(evals=value)))) == EVAL_W, value
        assert _text(render_eval_cost(_ctx())).strip() == "-"
        assert "+$1.2K" in _text(render_eval_cost(_ctx(evals=1234.5)))

    def test_colour_follows_the_charged_total_against_the_budget(self):
        assert "red" in render_eval_cost(_ctx(own=3.0, evals=2.0, budget=5.0))[0][1]
        assert "yellow" in render_eval_cost(_ctx(own=2.0, evals=2.0, budget=5.0))[0][1]
        assert "orange1" in render_eval_cost(_ctx(own=1.0, evals=1.0, budget=5.0))[0][1]
        # $ turns red with evals too: it is what the gate counts
        assert "red" in render_cost(_ctx(own=3.0, evals=2.0, budget=5.0))[0][1]
        assert "red" not in render_cost(_ctx(own=3.0, evals=0.0, budget=5.0))[0][1]

    def test_plain(self):
        assert render_eval_cost_plain(_ctx()) is None
        assert render_eval_cost_plain(_ctx(evals=2.0)) == "+$2.00"
        plain = render_cost_plain(_ctx(own=4.0, evals=1.25, budget=10.0))
        assert plain.count("$") == 3 and "+$" in plain and "/$" in plain

    def test_hover_splits_and_says_what_is_left(self):
        assert hover_eval_cost(_ctx()) is None
        text = str(hover_eval_cost(_ctx(own=3.0, evals=1.5, budget=10.0)))
        assert "3.00" in text and "1.50" in text and "4.50" in text and "5.50 left" in text
        text = str(hover_eval_cost(_ctx(own=3.0, evals=3.0, budget=5.0)))
        assert "refuse" in text


class TestDaemon:
    def test_subtree_counts_the_members_evals(self):
        from overcode.monitor_daemon import MonitorDaemon
        parent = SessionDaemonState(session_id="p", name="p", estimated_cost_usd=4.0,
                                    external_cost_usd=1.0)
        kid = SessionDaemonState(session_id="k", name="k", parent_name="p",
                                 estimated_cost_usd=2.0, external_cost_usd=0.5)
        MonitorDaemon._compute_subtree_costs(None, [parent, kid])
        assert parent.subtree_cost_usd == 7.5

    def test_published_to_the_tui_as_a_stat(self):
        from overcode.tui_engine import session_stats
        stats = session_stats(SessionStats(), {"external_cost_usd": 2.5})
        assert stats.external_cost_usd == 2.5


class TestPreferences:
    def test_eval_follows_cost_in_saved_overrides(self, tmp_path, monkeypatch):
        import json
        from overcode import settings

        path = tmp_path / "prefs.json"
        path.write_text(json.dumps({"column_config": {
            "med": {"cost": True, "token_count": False}, "low": {"cost": False},
            "high": {"cost": True, "eval_cost": False}}}))
        monkeypatch.setattr(settings, "get_tui_preferences_path", lambda session: path)
        prefs = settings.TUIPreferences.load("agents")
        assert prefs.column_config["med"]["eval_cost"] is True
        assert prefs.column_config["low"]["eval_cost"] is False
        assert prefs.column_config["high"]["eval_cost"] is False  # an explicit choice stays
