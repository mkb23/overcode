"""A reader fix restates spend: entries are replaced, the ledger is corrected.

Claude reader version 2 reads less than version 1 did (each message once),
so the per-conversation max would keep the old figure for ever. An entry
read by an older reader version is replaced by a newer read
(``conversation_spend``), the agent's cumulative cost goes down once, and
the ledger gets ``correction`` rows that scale every billing segment, so
billing totals drop by the over-count and a rebilled agent's segments
stay proportional. 1-hour cache writes are priced at twice the input.
"""

import os
from datetime import datetime, timedelta

import pytest

from overcode import conversation_spend as spend
from overcode.daemon_stats_sync import StatsSync
from overcode.history_reader import AgentSessionStats
from overcode.ledger import CORRECTION, Ledger, contribution, correction_rows, make_row
from overcode.pricing import ModelPricing, cost_of_1h_writes
from overcode.session_manager import PendingUpdates, SessionManager, _StatsLog
from overcode.settings import UserConfig, get_model_pricing

OPUS = "claude-opus-4-1"  # $15 / $75 per million in / out
HAIKU = "claude-haiku-4-5"  # $1 / $5
V2 = 2


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith("OVERCODE_") and key not in ("OVERCODE_DIR", "OVERCODE_TMUX_SOCKET"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "sessions"))


@pytest.fixture
def sm():
    return SessionManager(skip_git_detection=True)


def _opus(inp, out=0):
    return (inp * 15 + out * 75) / 1_000_000


def _haiku(inp, out=0):
    return (inp * 1 + out * 5) / 1_000_000


def v1(inp, out=0, model=None):
    """A read by reader version 1 (no ``rv``)."""
    return spend.usage(inp, out, 0, 0, model=model)


def v2(inp, out=0, model=None, cw=0, cw1h=0):
    return spend.usage(inp, out, cw, 0, model=model, cache_creation_1h_tokens=cw1h, rv=V2)


def _stats(reads, model=OPUS):
    totals = {k: sum(r.get(k, 0) for r in reads.values()) for k in spend.TOKEN_KEYS}
    return AgentSessionStats(
        interaction_count=1, input_tokens=totals["in"], output_tokens=totals["out"],
        cache_creation_tokens=totals["cw"], cache_read_tokens=totals["cr"], work_times=[],
        current_context_tokens=100, model=model, per_conversation=reads)


def _sync(sm, session_id, reads, *, model=OPUS, earlier_reads=None, now=None):
    pending = PendingUpdates()
    sync = StatsSync(session_manager=sm, log=_StatsLog())
    if now is not None:
        sync.clock = type("C", (), {"wall": staticmethod(lambda: now)})()
    sync._stage_usage(sm.get_session(session_id), _stats(reads, model), pending,
                      earlier_reads=earlier_reads)
    sm.commit_pending(pending)
    return sm.get_session(session_id), pending


def _agent(sm, ids, model=OPUS, name="cx"):
    s = sm.create_session(name=name, tmux_session="agents", tmux_window=f"{name}-1", command=[])
    sm.update_session(s.id, agent_session_ids=ids, model=model)
    return s


def _corrections(sm):
    return [r for r in sm.ledger.iter_rows() if r.get("kind") == CORRECTION]


# ── conversation_spend ──────────────────────────────────────────────────


class TestUsageAndPrice:
    def test_usage_carries_the_1h_part_and_the_reader_version(self):
        assert spend.usage(1, 2, 300, 4, cache_creation_1h_tokens=200, rv=2) == {
            "in": 1, "out": 2, "cw": 300, "cr": 4, "cw1h": 200, "rv": 2}
        assert spend.usage(1, 2, 300, 4, cache_creation_1h_tokens=999)["cw1h"] == 300
        assert "cw1h" not in spend.usage(1) and "rv" not in spend.usage(1)

    def test_1h_writes_take_the_fifth_price(self):
        tokens = {"in": 0, "out": 0, "cw": 1_000_000, "cr": 0, "cw1h": 400_000}
        assert spend.price((4, 20, 5, 0.4, 8), tokens) == pytest.approx(600_000 * 5 / 1e6
                                                                         + 400_000 * 8 / 1e6)
        # A four-price row (an old ledger row) prices every write at the 5-minute rate
        assert spend.price((4, 20, 5, 0.4), tokens) == pytest.approx(5.0)


class TestPricing:
    def test_1h_writes_default_to_twice_the_input_price(self):
        mp = ModelPricing(input=4.0, output=20.0, cache_write=5.0, cache_read=0.4)
        assert mp.cache_write_1h_price == 8.0
        assert mp.per_million() == (4.0, 20.0, 5.0, 0.4, 8.0)
        assert cost_of_1h_writes(1_000_000, mp) == pytest.approx(3.0)

    def test_configured_1h_price_wins_and_the_bedrock_discount_applies(self):
        cfg = UserConfig.from_dict({
            "pricing": {"cache_write_1h": 7.0},
            "model_pricing": {"mymodel": {"input": 2.0, "output": 9.0, "cache_write": 2.5,
                                          "cache_read": 0.2, "cache_write_1h": 3.5}},
            "bedrock": {"discount": 0.5},
        })
        assert get_model_pricing("mymodel-x", cfg).cache_write_1h_price == 3.5
        assert get_model_pricing("mymodel-x", cfg, provider="bedrock").cache_write_1h_price == 1.75
        assert get_model_pricing("unknown-model-zz", cfg).cache_write_1h_price == 7.0
        assert get_model_pricing(OPUS, cfg).cache_write_1h_price == 30.0  # 2 x $15
        assert get_model_pricing(OPUS, cfg, provider="bedrock").cache_write_1h_price == 15.0


def _merge(entries, reads, model=OPUS, **kw):
    def prices(m):
        return get_model_pricing(m or model, UserConfig()).per_million()

    def energy(m, t):
        return float(t["in"] + t["out"] + t["cw"] + t["cr"])

    return spend.merge(entries, reads, prices=prices, energy=energy, default_model=model, **kw)


class TestReplacement:
    def test_an_older_version_entry_is_replaced_not_maxed(self):
        old = _merge(None, {"a": v1(300, 30)})
        assert old["a"]["cost"] == pytest.approx(_opus(300, 30))
        new = _merge(old, {"a": v2(200, 20)})
        assert (new["a"]["in"], new["a"]["out"], new["a"]["rv"]) == (200, 20, V2)
        assert new["a"]["cost"] == pytest.approx(_opus(200, 20))
        assert new["a"]["energy"] == pytest.approx(220.0)
        assert spend.replaced(old, new) == ["a"]

    def test_same_version_reads_are_maxed_as_before(self):
        new = _merge(_merge(None, {"a": v2(200)}), {"a": v2(150)})
        assert new["a"]["in"] == 200
        grown = _merge(new, {"a": v2(260)})
        assert grown["a"]["in"] == 260
        assert grown["a"]["cost"] == pytest.approx(_opus(260))
        assert spend.replaced(new, grown) == []

    def test_a_conversation_the_new_reader_does_not_read_keeps_its_figures(self):
        old = _merge(None, {"a": v1(300), "b": v1(100)})
        new = _merge(old, {"b": v2(60)})
        assert new["a"] == old["a"]
        assert spend.totals(new)["input_tokens"] == 360

    def test_the_past_model_mix_is_kept_by_scaling(self):
        """Accrued at haiku, then opus: the replacement scales, never reprices."""
        entries = _merge(None, {"a": v1(1000, model=HAIKU)}, model=HAIKU)
        entries = _merge(entries, {"a": v1(2000, model=OPUS)})
        accrued = entries["a"]["cost"]
        assert accrued == pytest.approx(_haiku(1000) + _opus(1000))
        new = _merge(entries, {"a": v2(1000, model=OPUS)})
        assert new["a"]["cost"] == pytest.approx(accrued / 2)

    def test_a_charged_entry_keeps_its_charge(self):
        entries = _merge(None, {"a": spend.usage(300, model=OPUS, cost=1.25)})
        new = _merge(entries, {"a": spend.usage(200, model=OPUS, cost=1.25, rv=V2)})
        assert new["a"]["in"] == 200 and new["a"]["cost"] == 1.25

    def test_1h_writes_accrue_at_their_price(self):
        entries = _merge(None, {"a": v2(0, cw=1_000_000, cw1h=400_000)})
        assert entries["a"]["cw1h"] == 400_000
        assert entries["a"]["cost"] == pytest.approx(0.6 * 18.75 + 0.4 * 30.0)
        grown = _merge(entries, {"a": v2(0, cw=2_000_000, cw1h=1_400_000)})
        assert grown["a"]["cost"] == pytest.approx(0.6 * 18.75 + 0.4 * 30.0
                                                   + 1.0 * 30.0)

    def test_a_record_with_totals_only_restates_exactly_its_over_count(self):
        """0.5.x totals: 300 read of a (still there) + 100 of b (pruned)."""
        recorded = {"in": 400.0, "out": 0.0, "cw": 0.0, "cr": 0.0,
                    "cost": _opus(400), "energy": 400.0}

        def prices(m):
            return get_model_pricing(m or OPUS, UserConfig()).per_million()

        new = spend.merge_restating(
            None, {"a": v2(200)}, earlier_reads=lambda: {"a": v1(300)},
            prices=prices, energy=lambda m, t: float(sum(t.values())),
            default_model=OPUS, recorded=recorded, owned_ids=["b", "a"])
        totals = spend.totals(new)
        assert totals["input_tokens"] == 300  # 200 for a, b's 100 kept
        assert totals["estimated_cost_usd"] == pytest.approx(_opus(300), abs=1e-4)
        assert new[spend.LEGACY_KEY]["in"] == 100


# ── the ledger ──────────────────────────────────────────────────────────


class TestLiveCorrection:
    def test_the_cost_goes_down_once_with_a_correction_row(self, sm):
        s = _agent(sm, ["a"])
        _sync(sm, s.id, {"a": v1(300_000)})
        assert sm.ledger.totals() == {}
        rows = [make_row(sm.get_session(s.id), "checkpoint")]
        sm.ledger.append(rows)
        assert sm.ledger.totals()["cx"]["cost"] == pytest.approx(_opus(300_000))

        session, pending = _sync(sm, s.id, {"a": v2(200_000)})
        assert session.stats.estimated_cost_usd == pytest.approx(_opus(200_000))
        [row] = _corrections(sm)
        assert row["closing"] is False and row["seg"] == 0
        assert row["scale"]["cost"] == pytest.approx(2 / 3)
        assert row["was"]["cost"] == pytest.approx(_opus(300_000))
        assert sm.ledger.totals()["cx"]["cost"] == pytest.approx(_opus(200_000))

        # Idempotent: the next sync reads at the same version, nothing to restate
        _sync(sm, s.id, {"a": v2(200_000)})
        assert len(_corrections(sm)) == 1
        # And spend grows normally after it
        session, _ = _sync(sm, s.id, {"a": v2(210_000)})
        sm.ledger.append([make_row(session, "checkpoint")])
        assert sm.ledger.totals()["cx"]["cost"] == pytest.approx(_opus(210_000))

    def test_a_rebilled_agent_has_every_segment_scaled(self, sm):
        s = _agent(sm, ["a"])
        _sync(sm, s.id, {"a": v1(300_000)})
        assert sm.rebill(s.id, "client-b") == "cx"
        session, _ = _sync(sm, s.id, {"a": v1(450_000)})
        sm.ledger.append([make_row(session, "checkpoint")])
        before = sm.ledger.totals()
        assert before["cx"]["cost"] == pytest.approx(_opus(300_000))
        assert before["client-b"]["cost"] == pytest.approx(_opus(150_000))

        session, _ = _sync(sm, s.id, {"a": v2(300_000)})  # really 2/3 of it
        rows = _corrections(sm)
        assert sorted((r["seg"], r["closing"]) for r in rows) == [(0, True), (1, False)]
        after = sm.ledger.totals()
        assert after["cx"]["cost"] == pytest.approx(_opus(200_000))
        assert after["client-b"]["cost"] == pytest.approx(_opus(100_000))
        assert after["cx"]["agents"] == 1 and after["client-b"]["running"] == 1
        # The record's segment base is scaled with it: new spend lands on the new code
        assert session.billing_base_cost == pytest.approx(_opus(200_000))
        assert session.billing_base_tokens == 200_000
        session, _ = _sync(sm, s.id, {"a": v2(330_000)})
        sm.ledger.append([make_row(session, "checkpoint")])
        assert sm.ledger.totals()["client-b"]["cost"] == pytest.approx(_opus(130_000))
        assert sm.ledger.totals()["cx"]["cost"] == pytest.approx(_opus(200_000))

    def test_a_period_report_reads_the_past_corrected(self, sm):
        s = _agent(sm, ["a"])
        t0 = datetime(2026, 9, 1, 12, 0)
        session, _ = _sync(sm, s.id, {"a": v1(300_000)})
        sm.ledger.append([make_row(session, "checkpoint", now=t0)])
        session, _ = _sync(sm, s.id, {"a": v1(600_000)})
        sm.ledger.append([make_row(session, "checkpoint", now=t0 + timedelta(days=10))])
        _sync(sm, s.id, {"a": v2(400_000)}, now=t0 + timedelta(days=20))
        september = sm.ledger.report(since=t0 - timedelta(days=1), until=t0 + timedelta(days=5))
        assert september["cx"]["cost"] == pytest.approx(_opus(200_000))
        later = sm.ledger.report(since=t0 + timedelta(days=5), until=t0 + timedelta(days=15))
        assert later["cx"]["cost"] == pytest.approx(_opus(200_000))
        everything = sm.ledger.report()
        assert everything["cx"]["cost"] == pytest.approx(_opus(400_000))

    def test_a_record_with_totals_only_is_folded_first(self, sm):
        s = _agent(sm, ["a"])
        sm.update_stats(s.id, input_tokens=300_000, total_tokens=300_000,
                        estimated_cost_usd=_opus(300_000))
        session, pending = _sync(sm, s.id, {"a": v2(200_000)},
                                 earlier_reads=lambda: {"a": v1(300_000)})
        assert session.stats.input_tokens == 200_000
        assert session.stats.estimated_cost_usd == pytest.approx(_opus(200_000))
        assert len(pending.ledger_rows) == 1


class TestLedgerRows:
    def _record(self, cost, tokens, **kw):
        return {"id": "s1", "name": "n", "billing_code": "code", "model": OPUS,
                "stats": {"estimated_cost_usd": cost, "total_tokens": tokens,
                          "input_tokens": tokens, "conversations": {"a": {}}}, **kw}

    def test_a_forgotten_closed_segment_moves_by_the_difference(self, tmp_path):
        book = Ledger(tmp_path / "sessions")
        long_ago = datetime(2026, 1, 1)
        book.append([make_row(self._record(3.0, 300), "final", now=long_ago)], now=long_ago)
        assert book.totals(now=long_ago)["code"]["cost"] == 3.0
        later = long_ago + timedelta(days=90)
        book.totals(now=later)  # the closed share is forgotten (CLOSED_KEEP_DAYS)
        rows, _ = correction_rows(self._record(3.0, 300), self._record(2.0, 200),
                                  closed=True, now=later)
        book.append(rows, now=later)
        totals = book.totals(now=later)
        assert totals["code"]["cost"] == pytest.approx(2.0)
        assert totals["code"]["agents"] == 1
        # A repeat of the same correction changes nothing
        rows, _ = correction_rows(self._record(3.0, 300), self._record(2.0, 200),
                                  closed=True, now=later)
        book.append(rows, now=later)
        assert book.totals(now=later)["code"]["cost"] == pytest.approx(2.0)

    def test_a_refold_from_the_rows_agrees(self, tmp_path):
        book = Ledger(tmp_path / "sessions")
        now = datetime(2026, 10, 1)
        book.append([make_row(self._record(3.0, 300), "final", now=now)], now=now)
        rows, _ = correction_rows(self._record(3.0, 300), self._record(2.0, 200),
                                  closed=True, now=now)
        book.append(rows, now=now)
        assert book.totals(now=now)["code"]["cost"] == pytest.approx(2.0)
        book.totals_path.unlink()
        assert book.totals(now=now)["code"]["cost"] == pytest.approx(2.0)
        assert contribution(rows[0])["cost"] == pytest.approx(2.0)
