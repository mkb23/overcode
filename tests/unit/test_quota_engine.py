"""The engine's quota component: token stream, usage polls, statusline samples, views.

Synthetic agents, a synthetic usage API (an injected fetch) and a temp
usage directory: no network, no real ~/.claude or ~/.overcode.
"""

import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from quota_synth import price_fn, simulate  # noqa: E402

from overcode import quota_engine as qe  # noqa: E402
from overcode.quota_model import prior_model  # noqa: E402

T0 = 1_800_000_000.0
R7 = T0 + 3 * 86400  # the weekly window resets in three days
R5 = T0 + 3600


def _config(**over):
    cfg = {"enabled": True, "calibration": False, "plan": None, "weekly_usd": None,
           "weeks": 4, "poll_minutes": 5.0, "breaks": [], "warnings": []}
    cfg.update(over)
    return lambda: cfg


def _agent(sid, out_tokens=0, *, model="claude-opus-x", provider="web", backend="claude-code",
           start=T0 + 10, code=None, stamp=None):
    conv = {"c1": {"in": 0, "out": out_tokens, "cw": 0, "cr": 0, "model": model}}
    return SimpleNamespace(
        id=sid, name=f"agent-{sid}", provider=provider, backend=backend, model=model,
        start_time=start, billing_code=code or f"code-{sid}",
        stats=SimpleNamespace(conversations=conv, last_stats_update=stamp or str(out_tokens)))


class FakeApi:
    def __init__(self, w7=10.0, w5=4.0):
        self.calls = 0
        self.w7, self.w5 = w7, w5
        self.result = None

    def __call__(self):
        self.calls += 1
        if self.result is not None:
            return self.result
        return {"line": {"w7": self.w7, "w5": self.w5, "r7": R7, "r5": R5}, "plan": "max20x"}


def _engine(tmp_path, api=None, **cfg):
    return qe.QuotaEngine("agents", store=qe.UsageStore(tmp_path / "usage"),
                          fetch=api or FakeApi(), account=lambda: "acct1",
                          config=_config(**cfg), price_fn=price_fn, threaded=False)


class TestReading:
    def test_tokens_by_model_splits_cache_writes_by_ttl(self):
        stats = SimpleNamespace(conversations={
            "a": {"in": 10, "out": 20, "cw": 100, "cw1h": 60, "cr": 5, "model": "m1"},
            "a/sub": {"out": 7, "model": "m2"},
            "b": {"out": 3, "model": "m1", "lin": 4},
        })
        got = qe.tokens_by_model(stats)
        assert got["m1"] == {"in": 10, "out": 23, "cw5m": 40, "cw1h": 60, "cr": 5, "lin": 4}
        assert got["m2"] == {"out": 7}

    def test_token_rise_never_negative(self):
        assert qe.token_rise({"m": {"out": 10, "in": 5}}, {"m": {"out": 12, "in": 3}}) == {"m": {"out": 2}}

    def test_parse_usage_reads_windows_credits_and_surfaces(self):
        line = qe.parse_usage({
            "five_hour": {"utilization": 12.0, "resets_at": "2027-01-15T10:00:00Z"},
            "seven_day": {"utilization": 37.0, "resets_at": "2027-01-18T00:00:00+00:00"},
            "seven_day_fable": {"utilization": 5.0},
            "extra_usage": {"is_enabled": True, "used_credits": 2.5},
            "seven_day_by_surface": {"claude_code": {"utilization": 30}, "chat": 7},
        })
        assert line["w7"] == 37.0 and line["w5"] == 12.0
        assert line["r7"] == pytest.approx(1800230400.0)
        assert line["w7f"] == 5.0
        assert line["credits"] == 2.5
        assert line["surf"] == {"claude_code": 30.0, "chat": 7.0}
        assert qe.parse_usage({"five_hour": {}}) is None

    def test_parse_statusline(self):
        got = qe.parse_statusline({"t": 5.0, "rl": {"seven_day": {"used_percentage": 3, "resets_at": 99},
                                                   "five_hour": {"used_percentage": 1}}})
        assert got == {"t": 5.0, "w7": 3.0, "r7": 99.0, "w5": 1.0, "r5": None}
        assert qe.parse_statusline({"t": 1.0}) is None

    def test_counts_toward_quota(self):
        assert qe.counts_toward_quota(_agent("a"))
        assert not qe.counts_toward_quota(_agent("b", provider="bedrock"))
        assert not qe.counts_toward_quota(_agent("c", backend="codex"))


class TestTokenStream:
    def test_rise_is_flushed_after_a_minute_and_published(self, tmp_path):
        eng = _engine(tmp_path)
        eng.sync([_agent("a", 0)], T0 + 20)
        eng.sync([_agent("a", 620_000)], T0 + 30)   # $15.50 of Opus output
        assert eng.pending
        assert not list(eng.store.iter_tokens())
        # Unflushed tokens already count in the view
        assert eng.agent_view("a")["pp7d"] == pytest.approx(1.0, abs=0.01)
        eng.sync([_agent("a", 620_000)], T0 + 100)
        rows = list(eng.store.iter_tokens())
        assert len(rows) == 1
        assert rows[0]["code"] == "code-a" and rows[0]["acct"] == "acct1"
        assert rows[0]["tok"] == {"claude-opus-x": {"out": 620_000}}
        assert eng.agent_view("a")["pp7d"] == pytest.approx(1.0, abs=0.01)
        assert not eng.pending

    def test_agent_from_before_counting_is_not_back_billed(self, tmp_path):
        eng = _engine(tmp_path)
        old = _agent("old", 5_000_000, start=T0 - 86400)
        eng.sync([old], T0)
        eng.sync([_agent("old", 5_620_000, start=T0 - 86400)], T0 + 100)
        eng.flush(T0 + 200, force=True)
        assert eng.agent_view("old")["pp7d"] == pytest.approx(1.0, abs=0.01)

    def test_restart_counts_downtime_tokens_once(self, tmp_path):
        eng = _engine(tmp_path)
        eng.sync([], T0)
        eng.sync([_agent("a", 100_000)], T0 + 20)
        eng.flush(T0 + 30, force=True)
        # The daemon stops; the agent spends more; a new daemon starts
        eng2 = _engine(tmp_path)
        eng2.sync([_agent("a", 720_000)], T0 + 500)
        eng2.flush(T0 + 600, force=True)
        total = sum(r["tok"]["claude-opus-x"]["out"] for r in eng2.store.iter_tokens())
        assert total == 720_000

    def test_off_subscription_agents_are_not_counted(self, tmp_path):
        eng = _engine(tmp_path)
        eng.sync([_agent("b", 1000, provider="bedrock")], T0 + 20)
        eng.flush(T0 + 100, force=True)
        assert not list(eng.store.iter_tokens())
        assert eng.agent_view("b") is None


class TestPublishing:
    def test_attribution_sums_to_observed(self, tmp_path):
        api = FakeApi(w7=10.0)
        eng = _engine(tmp_path, api)
        agents = [_agent("a", 0), _agent("b", 0, model="claude-sonnet-x")]
        eng.sync(agents, T0 + 20)
        agents = [_agent("a", 620_000), _agent("b", 1_033_333, model="claude-sonnet-x")]
        eng.sync(agents, T0 + 100)
        eng.sync(agents, T0 + 200)
        views = [eng.agent_view("a"), eng.agent_view("b")]
        usage = eng.fleet_usage
        assert sum(v["pp7d"] for v in views) == pytest.approx(usage["pp_attributed"], abs=0.02)
        assert usage["pp_attributed"] + usage["pp_unattributed"] == pytest.approx(10.0)
        assert views[0]["w7"] == 10.0 and views[0]["conf"] == "prior"
        assert usage["plan"] == "max20x"

    def test_a_subagent_s_points_from_its_own_entries(self, tmp_path):
        """A delegated agent's row (swarm virtual rows) states its share:
        its spend entries through the same model as its parent's view."""
        eng = _engine(tmp_path)
        assert eng.conversation_pp({"s/agent-x": {"out": 1000, "model": "claude-opus-x"}}) is None
        eng.sync([_agent("a", 0)], T0 + 20)
        eng.sync([_agent("a", 300_000)], T0 + 100)
        whole = eng.agent_view("a")["pp7d"]
        half = eng.conversation_pp({"s/agent-x": {"out": 150_000, "model": "claude-opus-x"}})
        assert half == pytest.approx(whole / 2, abs=0.02)
        assert eng.conversation_pp({}) is None

    def test_view_holds_still_when_nothing_moves(self, tmp_path):
        eng = _engine(tmp_path)
        eng.sync([_agent("a", 0)], T0 + 20)
        eng.sync([_agent("a", 1000)], T0 + 30)
        first = eng.agent_view("a")
        eng.sync([_agent("a", 1000)], T0 + 40)
        eng.sync([_agent("b", 0), _agent("a", 1000)], T0 + 41)
        assert eng.agent_view("a") is first

    def test_no_login_publishes_nothing(self, tmp_path):
        api = FakeApi()
        api.result = {"error": "no token", "no_token": True}
        eng = _engine(tmp_path, api)
        eng.sync([_agent("a", 1000)], T0 + 20)
        assert eng.fleet_usage is None and eng.agent_view("a") is None

    def test_disabled_does_nothing(self, tmp_path):
        api = FakeApi()
        eng = _engine(tmp_path, api, enabled=False)
        eng.sync([_agent("a", 1000)], T0 + 20)
        assert api.calls == 0 and eng.fleet_usage is None
        assert not (tmp_path / "usage" / "tokens").exists()


class TestPolling:
    def test_poll_writes_history_with_fleet_tokens(self, tmp_path):
        api = FakeApi()
        eng = _engine(tmp_path, api)
        eng.sync([_agent("a", 0)], T0 + 20)
        eng.sync([_agent("a", 5000)], T0 + 100)
        eng.next_poll_at = 0
        eng.sync([_agent("a", 5000)], T0 + 400)
        history = eng.store.read_history("acct1")
        assert [h["src"] for h in history] == ["api", "api"]
        assert history[-1]["x"] == {"claude-opus-x": {"out": 5000}}
        assert history[-1]["plan"] == "max20x"

    def test_poll_interval_and_429_backoff(self, tmp_path):
        api = FakeApi()
        eng = _engine(tmp_path, api)
        eng.sync([], T0)
        eng.sync([], T0 + 60)
        assert api.calls == 1
        api.result = {"error": "HTTP 429", "rate_limited": True, "retry_after": 1200.0}
        eng.sync([], T0 + 301)
        assert api.calls == 2
        assert eng.next_poll_at >= T0 + 301 + 1200
        assert eng.latest is not None  # the last good reading stays

    def test_fresh_line_from_another_daemon_is_adopted(self, tmp_path):
        store = qe.UsageStore(tmp_path / "usage")
        store.append_history("acct1", {"t": T0 - 30, "src": "api", "w7": 42.0, "r7": R7, "r5": R5})
        api = FakeApi()
        eng = _engine(tmp_path, api)
        eng.sync([], T0)
        assert api.calls == 0
        assert eng.fleet_usage["w7"] == 42.0

    def test_sample_source_plugs_in(self, tmp_path):
        class Source:
            def poll(self, now):
                return [{"t": now, "w7": 11.0, "r7": R7, "src": "headers"}]
        eng = _engine(tmp_path)
        eng.sources.append(Source())
        eng.sync([], T0 + 20)
        assert eng.store.read_history("acct1")[-1]["src"] == "headers"
        assert eng.fleet_usage["w7"] == 11.0


class TestStatusline:
    def _write_samples(self, tmp_path, values):
        d = tmp_path / "usage" / "statusline"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "agent-a.jsonl", "w") as f:
            for t, w7 in values:
                f.write(json.dumps({"t": t, "rl": {"seven_day": {"used_percentage": w7, "resets_at": R7}}}) + "\n")

    def test_ticks_recorded_with_bracket(self, tmp_path):
        self._write_samples(tmp_path, [(T0 + 10, 10), (T0 + 40, 10), (T0 + 70, 11), (T0 + 90, 11)])
        eng = _engine(tmp_path, calibration=True)
        eng.sync([], T0 + 100)
        sl = [h for h in eng.store.read_history("acct1") if h["src"] == "statusline"]
        assert [(h["w7"], h.get("prev"), h.get("dt")) for h in sl] == [(10, None, None), (11, 10, 30.0)]

    def test_off_reads_no_samples(self, tmp_path):
        self._write_samples(tmp_path, [(T0 + 10, 10), (T0 + 70, 11)])
        eng = _engine(tmp_path, calibration=False)
        eng.sync([], T0 + 100)
        assert all(h["src"] == "api" for h in eng.store.read_history("acct1"))


class TestFitAndReports:
    def test_fit_runs_on_history_and_is_saved(self, tmp_path):
        store = qe.UsageStore(tmp_path / "usage")
        now = T0 + 3 * qe.WEEK
        for line in simulate(3, poll_minutes=10, seed=2, start=T0):
            store.append_history("acct1", line)
        model = qe.fit_account(store, "acct1", prior_model("max20x"), now=now, price_fn=price_fn)
        assert model.fitted and model.n_windows >= 2
        store.save_model("acct1", model)
        assert store.load_model("acct1") == model

    def test_engine_fits_when_new_observations_arrive(self, tmp_path):
        store = qe.UsageStore(tmp_path / "usage")
        for line in simulate(1, poll_minutes=30, seed=4, start=T0 - qe.WEEK):
            store.append_history("acct1", line)
        eng = _engine(tmp_path)
        eng.sync([], T0 + 20)
        assert eng.model is not None and eng.model.fitted
        assert store.load_model("acct1") is not None

    def test_quota_by_code(self, tmp_path):
        store = qe.UsageStore(tmp_path / "usage")
        store.append_tokens([
            {"t": T0, "sid": "a", "code": "x", "tok": {"claude-opus-x": {"out": 620_000}}},
            {"t": T0 + 1, "sid": "b", "code": "x", "tok": {"claude-opus-x": {"out": 620_000}}},
            {"t": T0 + 2, "sid": "c", "code": "y", "tok": {"claude-opus-x": {"out": 310_000}}},
            {"t": T0 - 9e5, "sid": "d", "code": "z", "tok": {"claude-opus-x": {"out": 1}}},
        ])
        got = qe.quota_by_code(store, prior_model("max20x"), since=T0 - 10, price_fn=price_fn)
        assert got == {"x": pytest.approx(2.0, abs=1e-6), "y": pytest.approx(0.5, abs=1e-6)}

    def test_window_start(self):
        assert qe.window_start({"r7": R7}, T0) == R7 - qe.WEEK
        assert qe.window_start(None, T0) == T0 - qe.WEEK


class TestAccountPlan:
    """The plan from .claude.json's account record (2.1.302's field names)."""

    def _write(self, tmp_path, monkeypatch, account):
        import json
        (tmp_path / ".claude.json").write_text(json.dumps({"oauthAccount": account}))
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))

    def test_max_20x_from_the_organisation_tier(self, tmp_path, monkeypatch):
        from overcode import quota_engine
        self._write(tmp_path, monkeypatch, {"organizationType": "claude_max",
                                            "organizationRateLimitTier": "default_claude_max_20x",
                                            "userRateLimitTier": None})
        assert quota_engine.account_plan() == "max20x"

    def test_a_user_tier_wins(self, tmp_path, monkeypatch):
        from overcode import quota_engine
        self._write(tmp_path, monkeypatch, {"organizationType": "claude_max",
                                            "organizationRateLimitTier": "default_claude_max_20x",
                                            "userRateLimitTier": "default_claude_max_5x"})
        assert quota_engine.account_plan() == "max5x"

    def test_no_record_is_unknown(self, tmp_path, monkeypatch):
        from overcode import quota_engine
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        assert quota_engine.account_plan() is None
