"""The quota model and its calibration (cost model Phase B), on synthetic data."""

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from quota_synth import TRUE_THETA, price_fn, simulate  # noqa: E402

from overcode.quota_calibration import (  # noqa: E402
    Obs,
    five_hour_ratio,
    fit,
    invert,
    nnls_quadratic,
    observations,
    thin,
    truncated_normal_moments,
)
from overcode.quota_model import (  # noqa: E402
    FEATURES,
    QuotaModel,
    add_tokens,
    features,
    format_pp,
    plan_from_credentials,
    prior_model,
    weekly_budget_usd,
)


class TestModel:
    def test_features_price_each_type_at_list(self):
        f = features({"claude-opus-x": {"in": 1_000_000, "out": 1_000_000, "cr": 2_000_000}}, price_fn)
        assert f["in"] == pytest.approx(5.0)
        assert f["out"] == pytest.approx(25.0)
        assert f["cr"] == pytest.approx(1.0)
        assert f["x:fable"] == 0.0

    def test_fable_excess_feature_leaves_out_cache_reads(self):
        f = features({"claude-fable-x": {"out": 1_000_000, "cr": 1_000_000}}, price_fn)
        assert f["x:fable"] == pytest.approx(50.0)

    def test_long_context_part_priced_at_long_tier(self):
        def prices(model):
            return ({"in": 1.0, "out": 5.0, "cw5m": 1.25, "cw1h": 2.0, "cr": 0.1},
                    {"in": 2.0, "out": 7.5, "cw5m": 2.5, "cw1h": 4.0, "cr": 0.2})
        f = features({"m": {"in": 1_000_000, "lin": 400_000}}, prices)
        assert f["in"] == pytest.approx(0.6 * 1.0 + 0.4 * 2.0)

    def test_unknown_models_add_nothing(self):
        assert sum(features({"gpt-x": {"out": 10**9}}, price_fn).values()) == 0

    def test_prior_cache_reads_free_and_scale_from_plan(self):
        m = prior_model("max20x")
        assert m.theta["cr"] == 0.0
        assert m.theta["out"] == pytest.approx(100 / 1550)
        # $15.50 of list output on Max 20x is one point
        assert m.predict_tokens({"claude-opus-x": {"out": 620_000}}, price_fn) == pytest.approx(1.0)
        assert m.confidence == "prior"

    def test_weekly_budget_override_wins(self):
        assert weekly_budget_usd("pro", 500) == 500
        assert weekly_budget_usd(None) > 0

    @pytest.mark.parametrize("kind,tier,plan", [
        ("max", "default_claude_max_20x", "max20x"),
        ("max", "default_claude_max_5x", "max5x"),
        ("pro", None, "pro"),
        ("team", "team_premium", "team_premium"),
        (None, None, None),
    ])
    def test_plan_from_credentials(self, kind, tier, plan):
        assert plan_from_credentials(kind, tier) == plan

    def test_round_trip(self):
        m = prior_model("pro")
        back = QuotaModel.from_dict(m.to_dict())
        assert back == m
        assert QuotaModel.from_dict({"nonsense": 1}) is None

    def test_format_pp_two_decimals(self):
        assert format_pp(1.3749) == "1.37%"
        assert format_pp(123.45) == "123.5%"
        assert format_pp(None) == "-"

    def test_add_tokens(self):
        into = {}
        add_tokens(into, {"m": {"in": 3, "bogus": 9}})
        add_tokens(into, {"m": {"in": 2}})
        assert into == {"m": {"in": 5}}


class TestNumerics:
    def test_nnls_clamps_negative_solution(self):
        # Unconstrained optimum is (2, -1)
        a = [[1.0, 0.0], [0.0, 1.0]]
        assert nnls_quadratic(a, [2.0, -1.0]) == pytest.approx([2.0, 0.0])

    def test_invert(self):
        inv = invert([[4.0, 1.0], [1.0, 3.0]])
        assert inv[0][0] == pytest.approx(3 / 11)
        assert invert([[0.0, 0.0], [0.0, 0.0]]) is None

    def test_truncated_normal_inside_cell(self):
        mean, var = truncated_normal_moments(5.5, 10.0, 5.0, 6.0)
        assert mean == pytest.approx(5.5, abs=1e-3)
        assert var == pytest.approx(1 / 12, rel=0.05)

    def test_truncated_normal_mass_beyond_edge(self):
        mean, _ = truncated_normal_moments(50.0, 0.1, 5.0, 6.0)
        assert mean == 6.0


class TestObservations:
    def test_windows_ticks_and_full_window(self):
        lines = simulate(1.0, ticks=True, poll_minutes=30)
        obs, report = observations(lines, price_fn=price_fn)
        assert report.windows == 1
        assert report.ticks > 0
        assert all(o.k < 100 for o in obs)

    def test_credits_period_dropped(self):
        lines = simulate(1.0, poll_minutes=30, credits_from=3000)
        obs, report = observations(lines, price_fn=price_fn)
        assert report.dropped.get("usage credits")
        assert max(o.t for o in obs) < lines[0]["t"] + 3000 * 60

    def test_reset_starts_new_window(self):
        lines = simulate(2.0, poll_minutes=60)
        obs, report = observations(lines, price_fn=price_fn)
        assert report.windows == 2

    def test_plan_change_and_break_split_windows(self):
        lines = simulate(1.0, poll_minutes=60)
        half = len(lines) // 2
        changed = [dict(ln, plan="max5x") if i >= half else ln for i, ln in enumerate(lines)]
        assert observations(changed, price_fn=price_fn)[1].windows == 2
        brk = lines[half]["t"]
        assert observations(lines, price_fn=price_fn, breaks=[brk])[1].windows == 2

    def test_lines_without_tokens_are_dropped(self):
        obs, report = observations([{"t": 1.0, "w7": 3, "r7": 100.0}], price_fn=price_fn)
        assert obs == [] and report.dropped == {"no token totals": 1}

    def test_surface_breakdown_preferred(self):
        line = {"t": 1.0, "w7": 30, "r7": 9.0, "x": {}, "surf": {"claude_code": 12}}
        obs, _ = observations([line], price_fn=price_fn)
        assert obs[0].k == 12

    def test_thin_keeps_run_ends_and_ticks(self):
        obs = [Obs(t=i, window="w", k=k, kind="poll", f={})
               for i, k in enumerate([1, 1, 1, 1, 1, 2, 2, 2])]
        assert [o.t for o in thin(obs)] == [0, 4, 5, 7]
        obs[2].kind = "tick"
        assert [o.t for o in thin(obs)] == [0, 2, 4, 5, 7]


def _rel_err(model, key):
    return abs(model.theta[key] - TRUE_THETA[key]) / TRUE_THETA[key]


class TestFit:
    def test_recovers_known_weights_from_integer_polls(self):
        lines = simulate(3, poll_minutes=5, seed=3)
        obs, report = observations(lines, price_fn=price_fn)
        model = fit(thin(obs), prior_model("max20x"), now=1.0, report=report)
        assert model.fitted
        # The prior was $1550/week (θ ≈ 0.065); the truth is ~0.08–0.12
        for key in ("out", "cw5m", "cw1h"):
            assert _rel_err(model, key) < 0.15, (key, model.theta[key])
        assert model.theta["cr"] < 0.02  # cache reads ≈ free
        assert model.theta["x:fable"] == pytest.approx(TRUE_THETA["x:fable"], abs=0.012)
        assert model.n_windows == 3
        assert model.coverage > 0.8

    def test_ticks_tighten_the_fit(self):
        lines = simulate(2, poll_minutes=5, seed=5, ticks=True)
        obs, report = observations(lines, price_fn=price_fn)
        assert report.ticks > 50
        model = fit(thin(obs), prior_model("max20x"), report=report)
        assert model.n_ticks == report.ticks
        assert _rel_err(model, "cw5m") < 0.15
        assert model.rmse_pp < 0.4

    def test_thin_data_stays_near_prior(self):
        lines = simulate(0.1, poll_minutes=60, seed=7)
        obs, report = observations(lines, price_fn=price_fn)
        prior = prior_model("max20x")
        model = fit(thin(obs), prior, report=report)
        for key in ("in", "out", "cw5m", "cw1h"):
            assert abs(model.theta[key] - prior.theta[key]) < 0.7 * prior.theta[key]
        assert model.confidence in ("low", "medium")

    def test_no_data_returns_prior(self):
        prior = prior_model("pro")
        model = fit([], prior)
        assert not model.fitted and model.theta == prior.theta

    def test_unexplained_burst_split_off(self):
        start_min = 2000
        lines = simulate(1, poll_minutes=5, seed=9, bursts={start_min: 15.0})
        obs, report = observations(lines, price_fn=price_fn)
        model = fit(thin(obs), prior_model("max20x"), report=report)
        assert model.n_segments >= 2
        assert any("unexplained" in n for n in model.notes)
        assert _rel_err(model, "cw5m") < 0.25

    def test_sd_reported_for_every_feature(self):
        lines = simulate(1, poll_minutes=15, seed=11)
        obs, report = observations(lines, price_fn=price_fn)
        model = fit(thin(obs), prior_model("max20x"), report=report)
        assert set(model.sd) == set(FEATURES)
        assert all(v >= 0 and math.isfinite(v) for v in model.sd.values())


class TestFiveHourRatio:
    def test_ratio_from_rises_in_one_window(self):
        lines = [
            {"t": 0, "w5": 10, "w7": 1, "r5": 18000, "r7": 600000},
            {"t": 60, "w5": 18, "w7": 2, "r5": 18000, "r7": 600000},
            {"t": 120, "w5": 26, "w7": 3, "r5": 18000, "r7": 600000},
            # a 5-hour reset in between: not compared
            {"t": 20000, "w5": 2, "w7": 4, "r5": 36000, "r7": 600000},
        ]
        ratio, n = five_hour_ratio(lines, prior=8.0, prior_weight_pp=0.0001)
        assert n == 2
        assert ratio == pytest.approx(8.0, rel=1e-3)

    def test_prior_without_data(self):
        assert five_hour_ratio([], prior=6.0) == (6.0, 0)
