"""Long-context requests are priced at their tier, per request (cost model 0.6.1 §4).

A Haiku 5.5 request whose prompt (input + cache read + cache write) is over
100K tokens is billed whole at $0.50 / $2.50 instead of $0.10 / $0.50;
Sonnet 4's tier starts past 200K. The tier is a property of each request,
never of a session's running total: the transcript readers keep the part
of every count spent by such requests, and the price row's second tier
prices that part.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from overcode import conversation_spend as spend
from overcode import history_reader
from overcode.history_reader import _parse_session_lines, get_session_stats, read_tree_stats
from overcode.pricing import lookup_pricing
from overcode.transcript_index import LONG_KEYS, TranscriptRegistry, is_long_request

HAIKU = "claude-haiku-5-5"
T0 = datetime(2026, 10, 15, 10, 0, 0)
_ids = iter(range(1, 10**9))


def _iso(minute: int) -> str:
    dt = T0.replace(minute=minute).astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _line(minute, inp, out, cr, cw=0, model=HAIKU, mid=None, cw1h=0):
    usage = {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cr,
             "cache_creation_input_tokens": cw}
    if cw1h:
        usage["cache_creation"] = {"ephemeral_1h_input_tokens": cw1h,
                                   "ephemeral_5m_input_tokens": cw - cw1h}
    return json.dumps({"type": "assistant", "timestamp": _iso(minute), "message": {
        "id": mid or f"msg_{next(_ids):06d}", "model": model, "usage": usage}})


@pytest.fixture(autouse=True)
def _fresh_caches():
    history_reader.clear_transcript_caches()
    yield
    history_reader.clear_transcript_caches()


class TestIsLongRequest:
    def test_threshold_is_on_the_whole_prompt(self):
        assert not is_long_request(HAIKU, 0, 0, 100_000)  # "over 100K"
        assert is_long_request(HAIKU, 10, 0, 99_991)  # 100,001: input counts
        assert is_long_request(HAIKU, 0, 60_000, 40_001)  # cache writes count
        assert not is_long_request("claude-sonnet-4-20250514", 0, 0, 150_000)
        assert is_long_request("claude-sonnet-4-20250514", 0, 0, 200_001)

    def test_models_without_a_tier(self):
        assert not is_long_request("claude-opus-5-5", 0, 0, 900_000)
        assert not is_long_request(None, 0, 0, 900_000)
        assert not is_long_request("gpt-5.6-sol", 0, 0, 900_000)


class TestReaders:
    def _transcript(self, tmp_path):
        path = tmp_path / "t.jsonl"
        lines = [
            _line(1, 10, 100, 50_000),               # short
            _line(2, 20, 200, 150_000, cw=5_000, cw1h=5_000, mid="msg_long"),  # long
            _line(2, 20, 300, 150_000, cw=5_000, cw1h=5_000, mid="msg_long"),  # its 2nd block
            _line(3, 30, 400, 80_000),               # short again (after compaction)
        ]
        path.write_text("\n".join(lines) + "\n")
        return path

    def test_index_and_full_parse_keep_the_long_part(self, tmp_path):
        path = self._transcript(tmp_path)
        indexed, _ = TranscriptRegistry().stats(path)
        with open(path) as f:
            full, _ = _parse_session_lines(f)
        for totals in (indexed, full):
            assert totals["input_tokens"] == 60
            assert totals["output_tokens"] == 800  # the long message counted once, at 300
            assert totals["long_input_tokens"] == 20
            assert totals["long_output_tokens"] == 300
            assert totals["long_cache_read_tokens"] == 150_000
            assert totals["long_cache_creation_tokens"] == 5_000
            assert totals["long_cache_creation_1h_tokens"] == 5_000
        assert {k: indexed[k] for k in LONG_KEYS} == {k: full[k] for k in LONG_KEYS}

    def test_a_copied_long_message_counts_once(self, tmp_path):
        parent = tmp_path / "p.jsonl"
        fork = tmp_path / "agent-f.jsonl"
        copied = _line(2, 20, 300, 150_000, mid="msg_shared")
        parent.write_text(_line(1, 10, 100, 50_000) + "\n" + copied + "\n")
        fork.write_text(copied + "\n" + _line(3, 5, 50, 120_000) + "\n")
        reads = read_tree_stats([parent, fork], transcripts=TranscriptRegistry())
        assert reads[0][0]["long_output_tokens"] == 300
        assert reads[1][0]["long_output_tokens"] == 50  # the copy is the parent's

    def test_session_read_reports_the_breakdown(self, tmp_path):
        session, history, projects = _claude_session(tmp_path, [
            _line(1, 10, 100, 50_000), _line(2, 20, 300, 150_000)])
        stats = get_session_stats(session, history_path=history, projects_path=projects)
        read = stats.per_conversation["sid-1"]
        assert (read["lin"], read["lout"], read["lcr"]) == (20, 300, 150_000)
        assert "lcw" not in read  # zero parts are left out


def _claude_session(tmp_path: Path, lines):
    from overcode.history_reader import encode_project_path
    from overcode.session_manager import Session

    start = T0.replace(minute=0)
    history = tmp_path / "history.jsonl"
    history.write_text(json.dumps({"display": "go", "timestamp": int(start.timestamp() * 1000)
                                   + 1000, "project": "/p", "sessionId": "sid-1"}) + "\n")
    project = tmp_path / "projects" / encode_project_path("/p")
    project.mkdir(parents=True)
    (project / "sid-1.jsonl").write_text("\n".join(lines) + "\n")
    session = Session(id="s1", name="a", tmux_session="agents", tmux_window="a-1",
                      command=[], start_directory="/p", start_time=start.isoformat())
    session.agent_session_ids = ["sid-1"]
    return session, history, project.parent


HAIKU_PX = lookup_pricing(HAIKU).per_million()


def _no_energy(model, tokens):
    return 0.0


class TestPricing:
    def test_long_part_is_priced_at_the_long_tier(self):
        tokens = {"in": 30, "out": 400, "cw": 0, "cr": 200_000,
                  "lin": 20, "lout": 300, "lcr": 150_000}
        want = (10 * 0.10 + 100 * 0.50 + 50_000 * 0.01
                + 20 * 0.50 + 300 * 2.50 + 150_000 * 0.05) / 1e6
        assert spend.price(HAIKU_PX, tokens) == pytest.approx(want)
        # Before the tier, every token was at the base rates
        assert spend.price(HAIKU_PX[:5], tokens) == pytest.approx(
            (30 * 0.10 + 400 * 0.50 + 200_000 * 0.01) / 1e6)

    def test_one_hour_writes_split_in_each_tier(self):
        tokens = {"in": 0, "out": 0, "cw": 10_000, "cw1h": 8_000, "cr": 0,
                  "lcw": 6_000, "lcw1h": 6_000}
        want = (2_000 * 0.20 + 2_000 * 0.125 + 6_000 * 1.0) / 1e6
        assert spend.price(HAIKU_PX, tokens) == pytest.approx(want)

    def test_new_conversation_prices_each_request_at_its_tier(self):
        read = spend.usage(30, 400, 0, 200_000, model=HAIKU,
                           long={"in": 20, "out": 300, "cr": 150_000})
        entries = spend.merge({}, {"c": read}, prices=lambda m: HAIKU_PX, energy=_no_energy)
        assert entries["c"]["cost"] == pytest.approx(spend.price(HAIKU_PX, read), abs=1e-8)
        assert entries["c"]["lcr"] == 150_000

    def test_growth_prices_only_what_the_tier_gained(self):
        first = spend.usage(30, 400, 0, 200_000, model=HAIKU,
                            long={"in": 20, "out": 300, "cr": 150_000})
        entries = spend.merge({}, {"c": first}, prices=lambda m: HAIKU_PX, energy=_no_energy)
        before = entries["c"]["cost"]
        second = spend.usage(40, 500, 0, 320_000, model=HAIKU,
                             long={"in": 30, "out": 400, "cr": 270_000})
        entries = spend.merge(entries, {"c": second}, prices=lambda m: HAIKU_PX,
                              energy=_no_energy)
        added = (10 * 0.50 + 100 * 2.50 + 120_000 * 0.05) / 1e6
        assert entries["c"]["cost"] - before == pytest.approx(added, abs=1e-8)

    def test_an_entry_from_before_the_breakdown_is_not_repriced(self):
        # Recorded by the previous release: no breakdown, cost at base rates
        old = {"c": {"in": 30, "out": 400, "cw": 0, "cr": 200_000, "cost": 0.0022,
                     "energy": 0.0, "model": HAIKU, "rv": 2}}
        read = spend.usage(31, 410, 0, 200_000, model=HAIKU, rv=2,
                           long={"in": 20, "out": 300, "cr": 150_000})
        entries = spend.merge(old, {"c": read}, prices=lambda m: HAIKU_PX, energy=_no_energy)
        # The breakdown is taken, unpriced; only the new tokens are priced
        assert entries["c"]["lcr"] == 150_000
        assert entries["c"]["cost"] == pytest.approx(0.0022 + (1 * 0.10 + 10 * 0.50) / 1e6)

    def test_breakdown_never_exceeds_the_count(self):
        read = spend.usage(10, 10, 0, 0, long={"in": 99, "out": 5})
        assert read["lin"] == 10 and read["lout"] == 5

    def test_a_low_read_keeps_the_breakdown(self):
        first = spend.usage(30, 400, 0, 200_000, model=HAIKU, long={"cr": 150_000})
        entries = spend.merge({}, {"c": first}, prices=lambda m: HAIKU_PX, energy=_no_energy)
        low = spend.usage(30, 400, 0, 200_000, model=HAIKU, long={})
        entries = spend.merge(entries, {"c": low}, prices=lambda m: HAIKU_PX, energy=_no_energy)
        assert entries["c"]["lcr"] == 150_000

    def test_end_to_end_through_the_daemon_pricing(self, tmp_path):
        from types import SimpleNamespace
        from unittest.mock import patch

        from overcode.daemon_stats_sync import spend_pricing
        from overcode.settings import UserConfig

        session, history, projects = _claude_session(tmp_path, [
            _line(1, 10, 100, 50_000), _line(2, 20, 300, 150_000)])
        stats = get_session_stats(session, history_path=history, projects_path=projects)
        with patch("overcode.settings.get_user_config", return_value=UserConfig()):
            prices, energy, model = spend_pricing(SimpleNamespace(provider="web", model=HAIKU),
                                                  stats)
            entries = spend.merge({}, stats.per_conversation, prices=prices, energy=energy,
                                  default_model=model)
        want = (10 * 0.10 + 100 * 0.50 + 50_000 * 0.01
                + 20 * 0.50 + 300 * 2.50 + 150_000 * 0.05) / 1e6
        assert spend.totals(entries)["estimated_cost_usd"] == pytest.approx(want, abs=1e-4)
        assert entries["sid-1"]["cost"] == pytest.approx(want, abs=1e-8)
