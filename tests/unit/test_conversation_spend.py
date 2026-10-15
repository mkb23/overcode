"""Spend is kept per conversation, so a read that covers less never loses or doubles it.

Regression tests for the stats-spend findings: a pruned transcript, a
briefly low read, codex/grok /new, restart --fresh, a failed container
read, a /model switch after a rebill, archiving an agent whose transcript
is gone, and records from before per-conversation spend (totals only, or a
pre-release build's carried spend).
"""

import json
import os

import pytest

from overcode import conversation_spend as spend
from overcode.daemon_stats_sync import StatsSync
from overcode.history_reader import AgentSessionStats
from overcode.ledger import contribution, make_row
from overcode.session_manager import PendingUpdates, SessionManager, SessionStats, _StatsLog

OPUS = "claude-opus-4-1"  # $15 / $75 per million in / out
HAIKU = "claude-haiku-4-5"  # $1 / $5


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith("OVERCODE_") and key not in ("OVERCODE_DIR", "OVERCODE_TMUX_SOCKET"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "sessions"))


@pytest.fixture
def sm():
    return SessionManager(skip_git_detection=True)


def conv(inp, out=0, *, model=None, cost=None):
    return spend.usage(inp, out, 0, 0, model=model, cost=cost)


def _stats(reads, *, model=OPUS, context=1000, interactions=1):
    if reads is None:
        per, totals = None, {}
    else:
        per = reads
        totals = {k: sum(r.get(k, 0) for r in reads.values()) for k in spend.TOKEN_KEYS}
    return AgentSessionStats(
        interaction_count=interactions, input_tokens=totals.get("in", 0),
        output_tokens=totals.get("out", 0), cache_creation_tokens=totals.get("cw", 0),
        cache_read_tokens=totals.get("cr", 0), work_times=[],
        current_context_tokens=context, model=model, per_conversation=per)


def _sync(sm, session_id, reads, *, stored_cost=None, **kw):
    """One daemon stats sync of a read split by conversation."""
    stats = _stats(reads, **kw)
    pending = PendingUpdates()
    StatsSync(session_manager=sm, log=_StatsLog())._stage_usage(
        sm.get_session(session_id), stats, pending, stored_cost=stored_cost,
        interaction_count=stats.interaction_count)
    sm.commit_pending(pending)
    return sm.get_session(session_id).stats


def _agent(sm, ids, model=OPUS, **kw):
    s = sm.create_session(name=kw.pop("name", "cx"), tmux_session="agents",
                          tmux_window="cx-1", command=[], **kw)
    sm.update_session(s.id, agent_session_ids=ids, model=model)
    return s


def _opus(inp, out=0):
    return (inp * 15 + out * 75) / 1_000_000


def _haiku(inp, out=0):
    return (inp * 1 + out * 5) / 1_000_000


def _spent(stats):
    return stats.total_tokens, stats.estimated_cost_usd


# ── a read that covers less ──────────────────────────────────────────────


def test_a_pruned_transcript_keeps_its_spend(sm):
    s = _agent(sm, ["a", "b"])
    _sync(sm, s.id, {"a": conv(100_000), "b": conv(50_000)})
    after = _sync(sm, s.id, {"b": conv(50_000)})  # a's transcript pruned
    assert _spent(after) == (150_000, pytest.approx(_opus(150_000)))
    later = _sync(sm, s.id, {"b": conv(60_000)}, context=120)  # b went on
    assert _spent(later) == (160_000, pytest.approx(_opus(160_000)))
    assert later.current_context_tokens == 120


def test_a_briefly_low_read_is_not_counted_twice(sm):
    """Round 4: 150, then 50, then 150 again was 250 tokens and $2.50."""
    s = _agent(sm, ["a"])
    _sync(sm, s.id, {"a": conv(150, cost=1.5)})
    low = _sync(sm, s.id, {"a": conv(50, cost=0.5)})
    assert _spent(low) == (150, 1.5)
    back = _sync(sm, s.id, {"a": conv(150, cost=1.5)})
    assert _spent(back) == (150, 1.5)
    on = _sync(sm, s.id, {"a": conv(160, cost=1.6)})
    assert _spent(on) == (160, 1.6)


def test_a_container_read_that_misses_a_conversation_changes_nothing(sm):
    s = _agent(sm, ["a", "b"], wrapper="devcontainer.sh")
    _sync(sm, s.id, {"a": conv(100_000), "b": conv(50_000)})
    failed = _sync(sm, s.id, {"b": conv(50_000)})  # docker exec cat of a timed out
    assert failed.total_tokens == 150_000
    back = _sync(sm, s.id, {"a": conv(100_000), "b": conv(50_000)})
    assert _spent(back) == (150_000, pytest.approx(_opus(150_000)))


def test_a_reader_that_cannot_split_keeps_its_totals_monotonic(sm):
    s = _agent(sm, ["a"])
    stats = _stats(None)
    stats.input_tokens = 1000
    pending = PendingUpdates()
    sync = StatsSync(session_manager=sm, log=_StatsLog())
    sync._stage_usage(sm.get_session(s.id), stats, pending, stored_cost=2.0)
    sm.commit_pending(pending)
    stats.input_tokens = 400  # briefly low
    pending = PendingUpdates()
    sync._stage_usage(sm.get_session(s.id), stats, pending, stored_cost=0.8)
    sm.commit_pending(pending)
    kept = sm.get_session(s.id).stats
    assert _spent(kept) == (1000, 2.0)
    assert list(kept.conversations) == [spend.TOTAL_KEY]


# ── new conversations ────────────────────────────────────────────────────


def test_codex_new_keeps_the_old_conversation_and_adds_the_new(sm):
    s = _agent(sm, ["conv-a"], model="gpt-5")
    _sync(sm, s.id, {"conv-a": conv(2_000_000, cost=12.0)}, model="gpt-5",
          context=180_000, interactions=40)
    sm.update_session(s.id, agent_session_ids=["conv-a", "conv-b"])  # /new
    after = _sync(sm, s.id, {"conv-b": conv(40_000, cost=0.5)}, model="gpt-5",
                  context=30_000, interactions=3)
    assert after.current_context_tokens == 30_000
    assert after.interaction_count == 3
    assert _spent(after) == (2_040_000, 12.5)
    later = _sync(sm, s.id, {"conv-b": conv(100_000, cost=1.0)}, model="gpt-5",
                  context=50_000, interactions=5)
    assert _spent(later) == (2_100_000, 13.0)


def test_grok_new_with_estimated_costs(sm):
    s = _agent(sm, ["g-1"], model=HAIKU)
    _sync(sm, s.id, {"g-1": conv(1_000_000, model=HAIKU)}, model=HAIKU)
    sm.update_session(s.id, agent_session_ids=["g-1", "g-2"])
    after = _sync(sm, s.id, {"g-2": conv(10_000, model=HAIKU)}, model=HAIKU)
    assert _spent(after) == (1_010_000, pytest.approx(_haiku(1_010_000)))


def test_restart_fresh_keeps_the_old_spend(sm):
    s = _agent(sm, ["conv-a"])
    _sync(sm, s.id, {"conv-a": conv(500_000)}, context=90_000)
    sm.update_session(s.id, agent_session_ids=["conv-fresh"])  # restart --fresh
    after = _sync(sm, s.id, {"conv-fresh": conv(10_000)}, context=10_000)
    assert _spent(after) == (510_000, pytest.approx(_opus(510_000)))
    assert after.current_context_tokens == 10_000
    assert set(after.conversations) == {"conv-a", "conv-fresh"}


# ── pricing: a /model switch never reprices the past ─────────────────────


@pytest.mark.parametrize("before,after,price_before,price_after", [
    (OPUS, HAIKU, _opus, _haiku), (HAIKU, OPUS, _haiku, _opus)])
def test_a_model_switch_after_a_rebill_moves_no_spend(sm, before, after,
                                                      price_before, price_after):
    s = _agent(sm, ["a"], model=before, billing_code="client-a")
    _sync(sm, s.id, {"a": conv(1_000_000, 100_000, model=before)}, model=before)
    spent = price_before(1_000_000, 100_000)
    sm.rebill(s.id, "client-b")
    sm.update_session(s.id, model=after)  # /model, no new tokens yet
    same = _sync(sm, s.id, {"a": conv(1_000_000, 100_000, model=after)}, model=after)
    assert same.estimated_cost_usd == pytest.approx(spent, abs=1e-4)
    row = make_row(sm.get_session(s.id), "checkpoint")
    assert row["px"] is None and contribution(row)["cost"] == pytest.approx(0, abs=1e-4)
    _sync(sm, s.id, {"a": conv(1_010_000, 101_000, model=after)}, model=after)
    sm.delete_sessions([s.id])
    totals = sm.ledger.totals()
    assert totals["client-a"]["cost"] == pytest.approx(spent, abs=1e-4)
    assert totals["client-b"]["cost"] == pytest.approx(price_after(10_000, 1_000), abs=1e-4)


def test_each_conversation_keeps_the_price_it_was_read_at(sm):
    s = _agent(sm, ["a"])
    _sync(sm, s.id, {"a": conv(1_000_000)})
    sm.update_session(s.id, agent_session_ids=["a", "b"], model=HAIKU)
    after = _sync(sm, s.id, {"b": conv(1_000_000, model=HAIKU)}, model=HAIKU)
    assert after.estimated_cost_usd == pytest.approx(_opus(1_000_000) + _haiku(1_000_000))


# ── archive and kill ─────────────────────────────────────────────────────


class _Reader:
    """A stats reader double answering one read."""

    backend_name = "claude-code"

    def __init__(self, stats):
        self.stats = stats

    def get_stats(self, session, **kw):
        return self.stats

    def get_current_session_id(self, *a, **kw):
        return None

    def discover_session_ids(self, *a, **kw):
        from overcode.stats_reader import DiscoveredSessionIds
        return DiscoveredSessionIds()


def test_archiving_an_agent_whose_transcript_is_gone_records_its_spend(sm, monkeypatch):
    s = _agent(sm, ["a"], start_directory="/work")
    _sync(sm, s.id, {"a": conv(1_000_000)})
    gone = _Reader(_stats({}))
    monkeypatch.setattr("overcode.daemon_stats_sync.stats_reader_for_session", lambda s: gone)
    sm.delete_session(s.id)  # kill / cleanup: one last sync, then the final row
    archived = sm.find_archived(s.id)
    final = [r for r in sm.ledger.iter_rows() if r["sid"] == s.id and r["kind"] == "final"]
    assert archived.stats.estimated_cost_usd == pytest.approx(_opus(1_000_000))
    assert final and final[-1]["cost"] == pytest.approx(_opus(1_000_000))


def test_kill_records_the_last_turn_through_the_same_merge(sm, monkeypatch):
    s = _agent(sm, ["a", "b"], start_directory="/work")
    _sync(sm, s.id, {"a": conv(100_000), "b": conv(10_000)})
    last = _Reader(_stats({"b": conv(20_000)}))  # a pruned meanwhile, b's last turn
    monkeypatch.setattr("overcode.daemon_stats_sync.stats_reader_for_session", lambda s: last)
    sm.delete_session(s.id)
    final = [r for r in sm.ledger.iter_rows() if r["sid"] == s.id and r["kind"] == "final"]
    assert final[-1]["tokens"] == 120_000
    assert final[-1]["cost"] == pytest.approx(_opus(120_000))


# ── records from before per-conversation spend ──────────────────────────


def test_an_old_record_with_only_totals_folds_into_a_legacy_entry(sm):
    s = _agent(sm, ["a"])
    sm.update_stats(s.id, estimated_cost_usd=7.0, total_tokens=700, input_tokens=700)
    kept = _sync(sm, s.id, {})  # its transcript is gone
    assert _spent(kept) == (700, 7.0)
    assert kept.conversations[spend.LEGACY_KEY]["absorbs"] == ["a"]
    sm.update_session(s.id, agent_session_ids=["a", "b"])
    added = _sync(sm, s.id, {"b": conv(100_000)})
    assert _spent(added) == (100_700, pytest.approx(7.0 + _opus(100_000)))


def test_an_old_record_whose_read_covers_part_of_it(sm):
    s = _agent(sm, ["a", "b"])  # a pruned before the upgrade
    sm.update_stats(s.id, estimated_cost_usd=_opus(700_000), total_tokens=700_000,
                    input_tokens=700_000)
    folded = _sync(sm, s.id, {"b": conv(500_000)})
    assert _spent(folded) == (700_000, pytest.approx(_opus(700_000)))
    grew = _sync(sm, s.id, {"b": conv(600_000)})
    assert _spent(grew) == (800_000, pytest.approx(_opus(800_000)))


def test_a_folded_conversation_read_again_is_not_counted_twice(sm):
    """The upgrade's first read missed a (a failed container read)."""
    s = _agent(sm, ["a", "b"])
    sm.update_stats(s.id, estimated_cost_usd=_opus(700), total_tokens=700, input_tokens=700)
    _sync(sm, s.id, {"b": conv(200)})
    back = _sync(sm, s.id, {"a": conv(500), "b": conv(200)})
    assert back.total_tokens == 700
    on = _sync(sm, s.id, {"a": conv(600), "b": conv(200)})
    assert _spent(on) == (800, pytest.approx(_opus(800)))


def test_a_pre_release_record_with_carried_spend_loads_and_folds(sm):
    s = _agent(sm, ["conv-b"])
    sm.update_stats(s.id, estimated_cost_usd=12.5, total_tokens=2_040_000,
                    input_tokens=2_040_000,
                    carried_spend={"total_tokens": 2_000_000, "estimated_cost_usd": 12.0},
                    stats_conversation="conv-b")
    loaded = sm.get_session(s.id).stats
    assert _spent(loaded) == (2_040_000, 12.5)
    folded = _sync(sm, s.id, {"conv-b": conv(40_000, cost=0.5)})
    assert _spent(folded) == (2_040_000, 12.5)
    later = _sync(sm, s.id, {"conv-b": conv(100_000, cost=1.0)})
    assert _spent(later) == (2_100_000, 13.0)


def test_the_upgrade_fold_never_reprices_the_past(sm):
    """A 0.5.x agent ran on opus, then /model haiku and /clear: the old
    estimate priced all 2M tokens at haiku ($2). Read per conversation at
    their own models they come to $16; the fold keeps $2."""
    s = _agent(sm, ["old", "act"], model=HAIKU, billing_code="client-a")
    sm.update_stats(s.id, estimated_cost_usd=2.0, total_tokens=2_000_000,
                    input_tokens=2_000_000)
    sm.rebill(s.id, "client-b")  # before the upgrade: base $2
    folded = _sync(sm, s.id, {"old": conv(1_000_000, model=OPUS),
                              "act": conv(1_000_000, model=HAIKU)}, model=HAIKU)
    assert _spent(folded) == (2_000_000, pytest.approx(2.0))
    row = make_row(sm.get_session(s.id), "checkpoint")
    assert contribution(row)["cost"] == pytest.approx(0, abs=1e-4)
    grew = _sync(sm, s.id, {"old": conv(1_000_000, model=OPUS),
                            "act": conv(2_000_000, model=HAIKU)}, model=HAIKU)
    assert _spent(grew) == (3_000_000, pytest.approx(2.0 + _haiku(1_000_000)))


def test_malformed_conversations_in_a_state_file_load_as_none(sm):
    assert SessionStats.from_dict({"conversations": ["x"], "total_tokens": 5}).conversations == {}
    s = _agent(sm, ["a"])
    sm.update_stats(s.id, conversations={"a": "junk", "b": {"in": "x"}})
    after = _sync(sm, s.id, {"a": conv(100)})
    assert after.total_tokens == 100


def test_a_rebill_before_the_upgrade_keeps_its_base_after_a_model_switch(sm):
    """A pre-upgrade segment's base was priced at the model of the rebill;
    the folded cost is every token at the model of the upgrade."""
    s = _agent(sm, ["a"], model=HAIKU)
    # Pre-upgrade: 1M input at opus, rebilled (base 1M tokens, $15), then
    # /model haiku and 100k more: the old estimate reprices it all at haiku
    sm.update_session(s.id, billing_code="client-b", billing_seg=1, billing_base_cost=15.0,
                      billing_base_tokens=1_000_000,
                      billing_base_usage={"in": 1_000_000, "out": 0, "cw": 0, "cr": 0})
    sm.update_stats(s.id, estimated_cost_usd=_haiku(1_100_000), total_tokens=1_100_000,
                    input_tokens=1_100_000)
    _sync(sm, s.id, {"a": conv(1_100_000, model=HAIKU)}, model=HAIKU)
    row = make_row(sm.get_session(s.id), "checkpoint")
    assert contribution(row)["cost"] == pytest.approx(_haiku(100_000), abs=1e-6)
    _sync(sm, s.id, {"a": conv(1_200_000, model=HAIKU)}, model=HAIKU)
    row = make_row(sm.get_session(s.id), "checkpoint")
    assert contribution(row)["cost"] == pytest.approx(_haiku(200_000), abs=1e-6)


def test_the_conversations_persist_and_bound_by_conversation(sm):
    s = _agent(sm, ["a"])
    for n in range(1, 6):
        _sync(sm, s.id, {"a": conv(n * 100)})
    raw = json.loads(sm.state_file.read_text())[s.id]["stats"]
    assert set(raw["conversations"]) == {"a"}
    assert "carried_spend" not in raw


# ── merge on its own ─────────────────────────────────────────────────────


def _merge(entries, reads, **kw):
    return spend.merge(entries, reads, prices=lambda m: (1e6, 1e6, 1e6, 1e6),
                       energy=lambda m, t: float(sum(t.values())), **kw)


def test_merge_never_lowers_a_field_and_prices_only_what_was_added():
    first = _merge({}, {"a": spend.usage(10, 5, 1, 1)})
    assert first["a"]["cost"] == 17 and first["a"]["energy"] == 17
    lower = _merge(first, {"a": spend.usage(4, 9, 0, 0)})
    assert (lower["a"]["in"], lower["a"]["out"]) == (10, 9)
    assert lower["a"]["cost"] == 21  # the 4 output tokens added
    assert first["a"]["out"] == 5  # the input is not changed


def test_merge_takes_a_recorded_cost_as_it_is_and_never_lowers_it():
    rec = _merge({}, {"a": spend.usage(10, cost=3.0)})
    assert rec["a"]["cost"] == 3.0 and rec["a"]["recorded"]
    low = _merge(rec, {"a": spend.usage(12, cost=2.0)})
    assert low["a"]["cost"] == 3.0
    zero = _merge(low, {"a": spend.usage(14)})  # no figure this time
    assert zero["a"]["cost"] == 3.0
    up = _merge(zero, {"a": spend.usage(16, cost=3.5)})
    assert up["a"]["cost"] == 3.5


def test_a_charge_that_appears_after_an_estimate_never_lowers_the_cost():
    """An unpriced conversation (subscription, hermes cost_status unknown)
    accrued $15 of estimate; a $1 charge then appears. The cost stays $15
    and grows by what the charge grows."""
    est = _merge({}, {"a": spend.usage(15)})
    assert est["a"]["cost"] == 15
    rec = _merge(est, {"a": spend.usage(16, cost=1.0)})
    assert rec["a"]["cost"] == 15 and rec["a"]["recorded"]
    on = _merge(rec, {"a": spend.usage(17, cost=1.5)})
    assert on["a"]["cost"] == 15.5
    larger = _merge(est, {"a": spend.usage(16, cost=20.0)})
    assert larger["a"]["cost"] == 20.0


def test_a_late_charge_after_a_rebill_keeps_the_ledger_and_total_together(sm):
    s = _agent(sm, ["a"], billing_code="client-a")
    _sync(sm, s.id, {"a": conv(1_000_000)})
    sm.rebill(s.id, "client-b")
    after = _sync(sm, s.id, {"a": conv(1_100_000, cost=1.0)})
    assert after.estimated_cost_usd == pytest.approx(_opus(1_000_000))
    later = _sync(sm, s.id, {"a": conv(1_200_000, cost=2.0)})
    assert later.estimated_cost_usd == pytest.approx(_opus(1_000_000) + 1.0)
    row = make_row(sm.get_session(s.id), "checkpoint")
    assert contribution(row)["cost"] == pytest.approx(1.0, abs=1e-4)


def test_merge_leaves_a_reserved_key_alone():
    out = _merge({}, {spend.LEGACY_KEY: spend.usage(10)})
    assert out == {}
