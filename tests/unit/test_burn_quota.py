"""The burn rate in Q% mode: weekly-quota points per hour, from the Q% column's model."""

from types import SimpleNamespace

import pytest
from rich.cells import cell_len

from overcode import tui_engine
from overcode.burn import WindowBurnStats, compute_window_burn
from overcode.columns.render_usage import format_pp_rate
from overcode.summary_columns import COLUMNS_BY_ID

pytestmark = pytest.mark.unit

COL = COLUMNS_BY_ID["burn_rate"]


def _ctx(burn, show_cost="quota"):
    return SimpleNamespace(window_burn=burn, show_cost=show_cost, bg="")


def _text(segments):
    return "".join(t for t, _ in segments)


def _burn(pp=0.84, hours=2.0, cost=3.0, tokens=1000):
    return WindowBurnStats(window_hours=hours, input_tokens=tokens, cost_usd=cost, quota_pp=pp)


class TestCell:
    def test_points_per_hour(self):
        assert _text(COL.render(_ctx(_burn()))) == " 🔥0.42%/h"
        assert COL.render_plain(_ctx(_burn())) == "0.42%/h"
        assert COL.sort_key(_ctx(_burn())) == pytest.approx(0.42)

    def test_the_width_holds_from_a_trickle_to_a_flood(self):
        widths = {cell_len(_text(COL.render(_ctx(_burn(pp=pp, hours=1.0)))))
                  for pp in (0.001, 0.42, 9.99, 12.3, 99.9, 123.0, 2500.0)}
        cost = cell_len(_text(COL.render(_ctx(_burn(), show_cost="cost"))))
        assert widths == {cost}

    def test_placeholder_without_a_quota_estimate(self):
        for burn in (_burn(pp=None), _burn(pp=0.0)):
            assert _text(COL.render(_ctx(burn))).strip() == "-"
            assert COL.render_plain(_ctx(burn)) is None

    def test_colour_still_tracks_dollars_per_hour(self):
        from overcode.columns.render_usage import burn_color_for_rate
        (_, style), = COL.render(_ctx(_burn(cost=500.0)))
        assert burn_color_for_rate(250.0) in style

    def test_other_modes_unchanged(self):
        assert "/h" in _text(COL.render(_ctx(_burn(), show_cost="tokens")))
        assert "%" not in _text(COL.render(_ctx(_burn(), show_cost="tokens")))

    def test_format(self):
        assert [format_pp_rate(v) for v in (0.004, 1.234, 12.34, 123.4)] == \
            ["0.00%", "1.23%", "12.3%", "123%"]


class TestEngineView:
    def test_published_points_reach_the_row_and_the_fleet(self):
        views = {"a": {"burn": {"1.0": {"cost_usd": 1.0, "quota_pp": 0.3}}},
                 "b": {"burn": {"1.0": {"cost_usd": 2.0, "quota_pp": 0.2}}},
                 "c": {"burn": {"1.0": {"cost_usd": 0.5}}}}
        assert tui_engine.window_burn(views["a"], 1.0).quota_pp == 0.3
        assert tui_engine.window_burn(views["c"], 1.0).quota_pp is None
        assert tui_engine.fleet_burn(views, 1.0).quota_pp == pytest.approx(0.5)
        assert tui_engine.fleet_burn({"c": views["c"]}, 1.0).pp_per_hour is None


def test_compute_window_burn_prices_each_session_in_points(monkeypatch):
    usage = {"input_tokens": 1000, "output_tokens": 100, "cache_creation_tokens": 0,
             "cache_read_tokens": 0}
    reader = SimpleNamespace(get_window_token_usage=lambda session, since: usage)
    monkeypatch.setattr("overcode.stats_reader.stats_reader_for_session", lambda s: reader)
    sessions = [SimpleNamespace(id="a", model="claude-opus-4-6"),
                SimpleNamespace(id="b", model="claude-opus-4-6")]
    seen = []

    def pp_of(session, u):
        seen.append((session.id, u))
        return 0.25 if session.id == "a" else None

    stats = compute_window_burn(sessions, set(), 1.0, quota_pp_of=pp_of)
    assert [sid for sid, _ in seen] == ["a", "b"] and seen[0][1] is usage
    assert stats.per_session["a"].quota_pp == 0.25 and stats.per_session["b"].quota_pp is None
    assert stats.quota_pp == 0.25


def test_quota_engine_window_pp_uses_the_q_model(tmp_path):
    from overcode import quota_model as qm
    from overcode.quota_engine import QuotaEngine, UsageStore

    engine = QuotaEngine("agents", store=UsageStore(tmp_path / "usage"), account=lambda: "acct",
                         fetch=lambda: {}, config=lambda: {"enabled": True, "plan": "pro"},
                         threaded=False)
    session = SimpleNamespace(id="a", model="claude-opus-4-6", transcript_model=None)
    usage = {"input_tokens": 100_000, "output_tokens": 20_000, "cache_creation_tokens": 5000,
             "cache_creation_1h_tokens": 1000, "cache_read_tokens": 50_000}
    assert engine.window_pp(session, usage) is None  # no quota view for it
    engine.views = {"a": {"pp7d": 1.0}}
    model = engine.current_model(None)
    want = model.predict_tokens({"claude-opus-4-6": {"in": 100_000, "out": 20_000, "cw5m": 4000,
                                                     "cw1h": 1000, "cr": 50_000}})
    assert engine.window_pp(session, usage) == pytest.approx(round(want, 4))
    assert engine.window_pp(session, usage) > 0
    assert isinstance(model, qm.QuotaModel)
