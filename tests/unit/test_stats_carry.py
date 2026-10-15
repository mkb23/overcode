"""A stats read below the spend on record carries it instead of freezing (StatsSync).

Codex and grok read only the newest conversation, so after /new (or a
restart --fresh, which replaces the conversations) their totals drop. The
read's context and interaction count are current; the spend on record must
stay, and the new conversation's spend add to it.
"""

import os

import pytest

from overcode.daemon_stats_sync import StatsSync
from overcode.history_reader import AgentSessionStats
from overcode.session_manager import PendingUpdates, SessionManager, _StatsLog


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith("OVERCODE_") and key not in ("OVERCODE_DIR", "OVERCODE_TMUX_SOCKET"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "sessions"))


@pytest.fixture
def sm():
    return SessionManager(skip_git_detection=True)


def _read(sm, session_id, tokens, cost, context, interactions=1):
    """One daemon stats sync of a read with ``tokens`` (all input) costing ``cost``."""
    stats = AgentSessionStats(
        interaction_count=interactions, input_tokens=tokens, output_tokens=0,
        cache_creation_tokens=0, cache_read_tokens=0, work_times=[],
        current_context_tokens=context, model="gpt-5")
    pending = PendingUpdates()
    StatsSync(session_manager=sm, log=_StatsLog())._stage_usage(
        sm.get_session(session_id), stats, pending, stored_cost=cost,
        interaction_count=interactions)
    sm.commit_pending(pending)
    return sm.get_session(session_id).stats


def _agent(sm, ids):
    s = sm.create_session(name="cx", tmux_session="agents", tmux_window="cx-1", command=[])
    sm.update_session(s.id, agent_session_ids=ids)
    return s


def test_a_new_conversation_updates_context_and_adds_its_spend(sm):
    s = _agent(sm, ["conv-a"])
    _read(sm, s.id, 2_000_000, 12.0, 180_000, interactions=40)
    sm.update_session(s.id, agent_session_ids=["conv-a", "conv-b"])  # codex /new
    after = _read(sm, s.id, 40_000, 0.5, 30_000, interactions=3)
    assert after.current_context_tokens == 30_000
    assert after.interaction_count == 3
    assert (after.total_tokens, after.estimated_cost_usd) == (2_040_000, 12.5)
    later = _read(sm, s.id, 100_000, 1.0, 50_000, interactions=5)
    assert (later.total_tokens, later.estimated_cost_usd) == (2_100_000, 13.0)
    assert later.current_context_tokens == 50_000


def test_a_restart_fresh_keeps_the_old_spend(sm):
    s = _agent(sm, ["conv-a"])
    _read(sm, s.id, 500_000, 3.0, 90_000)
    sm.update_session(s.id, agent_session_ids=["conv-fresh"])  # restart --fresh
    after = _read(sm, s.id, 10_000, 0.1, 10_000)
    assert (after.total_tokens, after.estimated_cost_usd) == (510_000, 3.1)
    assert after.current_context_tokens == 10_000


def test_pruned_transcripts_keep_the_spend_without_counting_twice(sm):
    s = _agent(sm, ["conv-a", "conv-b"])
    _read(sm, s.id, 150, 1.5, 100)
    after = _read(sm, s.id, 50, 0.5, 120)  # conv-a's transcript pruned
    assert (after.total_tokens, after.estimated_cost_usd) == (150, 1.5)
    assert after.current_context_tokens == 120
    later = _read(sm, s.id, 60, 0.6, 130)  # conv-b went on
    assert (later.total_tokens, later.estimated_cost_usd) == (160, 1.6)


def test_a_pre_upgrade_record_whose_transcript_is_gone_keeps_its_spend(sm):
    s = _agent(sm, ["conv-a"])
    sm.update_stats(s.id, estimated_cost_usd=7.0, total_tokens=700, input_tokens=700)
    after = _read(sm, s.id, 0, 0.0, 0)
    assert (after.total_tokens, after.estimated_cost_usd) == (700, 7.0)
