"""Billing codes and the spend ledger.

Every agent is billed to a code (its launch name by default, its parent's
for a child, always); the ledger keeps cumulative spend rows per agent
segment, folded into per-code totals. Synthetic records only.
"""

import json
import os
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from overcode.cli import app
from overcode.daemon_housekeeping import Housekeeping
from overcode.launcher import AgentLauncher
from overcode.ledger import Ledger, billing_code_of, contribution, make_row
from overcode.session_manager import PendingUpdates, SessionManager
from overcode.tmux_manager import TmuxManager
from tests.tmux_doubles import MockTmux

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith("OVERCODE_") and key not in ("OVERCODE_DIR", "OVERCODE_TMUX_SOCKET"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "sessions"))
    with patch("overcode.launcher.require_tmux"), \
         patch("overcode.launcher.require_agent_cli"), \
         patch("overcode.launcher.time.sleep"):
        yield


@pytest.fixture
def sm():
    return SessionManager(skip_git_detection=True)


@pytest.fixture
def launcher(sm):
    return AgentLauncher("agents", TmuxManager("agents", tmux=MockTmux()), sm)


def _spend(sm, session, cost, tokens=0):
    sm.update_stats(session.id, estimated_cost_usd=cost, total_tokens=tokens)


def _rows(sm):
    return list(sm.ledger.iter_rows())


class TestBillingCodes:
    def test_defaults_to_the_launch_name_and_survives_a_rename(self, launcher, sm):
        agent = launcher.launch(name="shirka")
        assert sm.get_session(agent.id).billing_code == "shirka"
        launcher._live_status = lambda s: "waiting_user"
        launcher.rename(sm.get_session(agent.id), "shirka-2", graceful_exit_wait=0)
        assert sm.get_session(agent.id).billing_code == "shirka"

    def test_launch_flag(self, launcher, sm):
        agent = launcher.launch(name="a", billing_code="client-x")
        assert sm.get_session(agent.id).billing_code == "client-x"

    def test_children_always_take_the_parents_code(self, launcher, sm, capsys):
        parent = launcher.launch(name="boss", billing_code="client-x")
        with patch.dict(os.environ, {"OVERCODE_SESSION_NAME": "boss"}):
            child = launcher.launch(name="kid")
            grandchild_env = {"OVERCODE_SESSION_NAME": "kid"}
        with patch.dict(os.environ, grandchild_env):
            grandchild = launcher.launch(name="grandkid", billing_code="mine",
                                         inherit_parent_settings=False)
        assert sm.get_session(child.id).billing_code == "client-x"
        assert sm.get_session(grandchild.id).billing_code == "client-x"
        assert "--billing-code mine ignored" in capsys.readouterr().err
        assert parent.parent_session_id is None

    def test_a_record_from_before_codes_reads_as_its_name(self):
        assert billing_code_of({"name": "old"}) == "old"
        assert billing_code_of(SimpleNamespace(billing_code=None, name="old")) == "old"

    def test_an_old_sessions_json_still_loads(self, sm):
        sm._save_state({"x": {"id": "x", "name": "x", "tmux_session": "agents",
                              "tmux_window": "x-1", "command": [], "start_directory": None,
                              "start_time": "2026-01-01T00:00:00"}})
        session = sm.get_session("x")
        assert session.billing_code is None and session.billing_seg == 0
        assert billing_code_of(session) == "x"


class TestRows:
    def test_row_carries_cumulative_spend(self, sm):
        s = sm.create_session(name="a", tmux_session="agents", tmux_window="a-1", command=[])
        sm.update_stats(s.id, estimated_cost_usd=1.5, total_tokens=100, input_tokens=60,
                        output_tokens=30, cache_read_tokens=10)
        row = make_row(sm.get_session(s.id), "checkpoint")
        assert row["sid"] == s.id and row["billing_code"] == "a" and row["seg"] == 0
        assert (row["cost"], row["tokens"], row["tok_in"], row["tok_out"], row["tok_cache"]) == \
            (1.5, 100, 60, 30, 10)
        assert contribution(row)["cost"] == 1.5

    def test_archive_writes_the_final_row_in_the_same_commit(self, sm):
        s = sm.create_session(name="a", tmux_session="agents", tmux_window="a-1", command=[])
        _spend(sm, s, 2.0)
        with patch.object(sm._store, "_write", side_effect=OSError("crash")):
            with pytest.raises(OSError):
                sm.delete_session(s.id)
        # Write-ahead: the final row landed before the live file
        assert [r["kind"] for r in _rows(sm)] == ["final"]
        sm.delete_session(s.id)  # the retry writes a duplicate final row
        assert [r["kind"] for r in _rows(sm)] == ["final", "final"]
        assert sm.ledger.totals()["a"]["cost"] == 2.0  # counted once
        assert sm.ledger.totals()["a"]["agents"] == 1


class TestFinalRow:
    """The final row is the agent's last word on spend: nothing adds to it later."""

    def test_kill_reads_the_transcript_once_more(self, sm):
        s = sm.create_session(name="a", tmux_session="agents", tmux_window="a-1", command=[])
        _spend(sm, s, 1.0)  # the daemon's last sync

        def last_turn(self, session, pending):
            pending.update_stats(session.id, estimated_cost_usd=3.0)  # the turn since

        with patch("overcode.daemon_stats_sync.StatsSync.sync_agent_stats", last_turn):
            sm.delete_session(s.id)
        assert [(r["kind"], r["cost"]) for r in _rows(sm)] == [("final", 3.0)]
        assert sm.find_archived(s.id).stats.estimated_cost_usd == 3.0

    def test_a_failed_refresh_still_archives(self, sm):
        s = sm.create_session(name="a", tmux_session="agents", tmux_window="a-1", command=[])
        _spend(sm, s, 1.0)
        with patch("overcode.daemon_stats_sync.StatsSync.sync_agent_stats",
                   side_effect=RuntimeError("unreadable")):
            sm.delete_session(s.id)
        assert [(r["kind"], r["cost"]) for r in _rows(sm)] == [("final", 1.0)]

    def test_stats_staged_in_the_archiving_commit_are_kept(self, sm):
        s = sm.create_session(name="a", tmux_session="agents", tmux_window="a-1", command=[])
        pending = PendingUpdates()
        pending.update_stats(s.id, estimated_cost_usd=4.0)
        pending.archive_session(s.id)
        sm.commit_pending(pending)
        assert [(r["kind"], r["cost"]) for r in _rows(sm)] == [("final", 4.0)]


class TestRebill:
    def test_forward_only(self, sm):
        s = sm.create_session(name="a", tmux_session="agents", tmux_window="a-1", command=[])
        _spend(sm, s, 3.0, 300)
        assert sm.rebill(s.id, "client-y") == "a"
        assert sm.rebill(s.id, "client-y") == "client-y"  # no-op
        _spend(sm, s, 5.0, 500)
        sm.delete_session(s.id)
        assert [(r["kind"], r["billing_code"], r["seg"]) for r in _rows(sm)] == [
            ("close", "a", 0), ("open", "client-y", 1), ("final", "client-y", 1)]
        totals = sm.ledger.totals()
        assert totals["a"]["cost"] == 3.0 and totals["a"]["tokens"] == 300
        assert totals["client-y"]["cost"] == 2.0 and totals["client-y"]["tokens"] == 200

    @staticmethod
    def _estimate(model, inp, out):
        """The daemon's estimate: every token so far at ``model``'s prices."""
        from overcode.pricing import calculate_cost_estimate
        from overcode.settings import get_model_pricing, get_user_config

        mp = get_model_pricing(model, get_user_config())
        return round(calculate_cost_estimate(inp, out, 0, 0, price_input=mp.input,
                                             price_output=mp.output,
                                             price_cache_write=mp.cache_write,
                                             price_cache_read=mp.cache_read), 4)

    def _work(self, sm, s, model, inp, out):
        cost = self._estimate(model, inp, out)
        sm.update_session(s.id, model=model)
        sm.update_stats(s.id, input_tokens=inp, output_tokens=out, total_tokens=inp + out,
                        estimated_cost_usd=cost)
        return cost

    @pytest.mark.parametrize("before,after", [("claude-sonnet-4-5", "claude-opus-4-1"),
                                              ("claude-opus-4-1", "claude-sonnet-4-5")])
    def test_a_model_switch_after_a_rebill_does_not_move_the_past(self, sm, before, after):
        s = sm.create_session(name="w", tmux_session="agents", tmux_window="w-1", command=[],
                              billing_code="client-a")
        spent = self._work(sm, s, before, 5_000_000, 1_000_000)
        sm.rebill(s.id, "client-b")
        self._work(sm, s, after, 5_010_000, 1_001_000)  # /model, then a little work
        new_work = self._estimate(after, 10_000, 1_000)
        sm.delete_sessions([s.id])
        totals = sm.ledger.totals()
        assert totals["client-a"]["cost"] == pytest.approx(spent)
        assert totals["client-b"]["cost"] == pytest.approx(new_work, abs=1e-3)

    def test_a_model_switch_does_not_reprice_an_earlier_month(self, sm):
        s = sm.create_session(name="w", tmux_session="agents", tmux_window="w-1", command=[])
        self._work(sm, s, "claude-sonnet-4-5", 5_000_000, 1_000_000)
        sm.ledger.append([make_row(sm.get_session(s.id), "checkpoint", t="2026-09-30T12:00:00")])
        self._work(sm, s, "claude-opus-4-1", 5_010_000, 1_001_000)
        sm.ledger.append([make_row(sm.get_session(s.id), "checkpoint", t="2026-10-02T12:00:00")])
        octo = sm.ledger.report(since=datetime(2026, 10, 1), until=datetime(2026, 11, 1))
        assert octo["w"]["cost"] == pytest.approx(
            self._estimate("claude-opus-4-1", 10_000, 1_000), abs=1e-3)

    def test_a_recorded_cost_is_taken_as_it_is(self, sm):
        s = sm.create_session(name="w", tmux_session="agents", tmux_window="w-1", command=[])
        sm.update_stats(s.id, input_tokens=1000, output_tokens=1000, total_tokens=2000,
                        estimated_cost_usd=7.0)  # what the backend charged
        row = make_row(sm.get_session(s.id), "checkpoint")
        assert row["px"] is None and contribution(row)["cost"] == 7.0

    def test_cli(self, sm):
        s = sm.create_session(name="a", tmux_session="agents", tmux_window="a-1", command=[])
        result = runner.invoke(app, ["rebill", "a", "client-z"])
        assert result.exit_code == 0 and "client-z" in result.output
        assert sm.get_session(s.id).billing_code == "client-z"
        assert runner.invoke(app, ["rebill", "nobody", "x"]).exit_code == 1
        assert runner.invoke(app, ["rebill", "a", " "]).exit_code == 1


class TestTotals:
    def _ledger(self, sm, rows):
        sm.ledger.append(rows)
        return sm.ledger

    def _row(self, sid, code, cost, kind="checkpoint", seq=None, seg=0, base=0.0, t=None):
        return {"sid": sid, "billing_code": code, "cost": cost, "tokens": int(cost * 100),
                "tok_in": 0, "tok_out": 0, "tok_cache": 0, "seg": seg, "base_cost": base,
                "base_tokens": int(base * 100), "kind": kind, "seq": seq or 0,
                "t": t or datetime.now().isoformat()}

    def test_the_last_row_of_a_segment_counts(self, sm):
        ledger = self._ledger(sm, [self._row("s1", "x", 1.0, seq=1),
                                   self._row("s1", "x", 4.0, seq=2),
                                   self._row("s2", "x", 0.5, kind="final", seq=3)])
        totals = ledger.totals()
        assert totals["x"]["cost"] == 4.5
        assert totals["x"]["agents"] == 2 and totals["x"]["running"] == 1

    def test_a_downward_revision_and_a_late_older_row(self, sm):
        ledger = self._ledger(sm, [self._row("s1", "x", 4.0, seq=2)])
        ledger.totals()
        ledger.append([self._row("s1", "x", 3.0, seq=5),   # revised down
                       self._row("s1", "x", 9.0, seq=1)])  # an older row, late
        assert ledger.totals()["x"]["cost"] == 3.0

    def test_incremental_reads_only_new_rows(self, sm):
        ledger = self._ledger(sm, [self._row("s1", "x", 1.0, kind="final", seq=1)])
        assert ledger.totals()["x"]["cost"] == 1.0
        seen = []
        real = Ledger._rows_of
        with patch.object(Ledger, "_rows_of", staticmethod(
                lambda path, offset=0: seen.append(offset) or real(path, offset))):
            ledger.append([self._row("s2", "y", 2.0, kind="final", seq=2)])
            totals = ledger.totals()
            assert totals["y"]["cost"] == 2.0 and totals["x"]["cost"] == 1.0
            assert seen and seen[0] > 0  # from where the last fold stopped

    def test_a_late_checkpoint_after_the_final_is_ignored(self, sm):
        ledger = self._ledger(sm, [self._row("s1", "x", 5.0, kind="final", seq=10)])
        ledger.totals()
        ledger.append([self._row("s1", "x", 2.0, seq=4)])
        totals = ledger.totals()
        assert totals["x"]["cost"] == 5.0 and totals["x"]["running"] == 0

    def test_period_report(self, sm):
        sept, octo = datetime(2026, 9, 10), datetime(2026, 10, 5)
        ledger = self._ledger(sm, [
            self._row("s1", "x", 1.0, seq=1, t=sept.isoformat()),
            self._row("s1", "x", 4.0, seq=2, t=octo.isoformat()),
            self._row("s2", "y", 2.0, kind="final", seq=3, t=sept.isoformat()),
        ])
        oct_report = ledger.report(since=datetime(2026, 10, 1), until=datetime(2026, 11, 1))
        assert oct_report == {"x": {"cost": 3.0, "tokens": 300, "tok_in": 0, "tok_out": 0,
                                    "tok_cache": 0, "agents": 1, "running": 1}}
        sep_report = ledger.report(since=datetime(2026, 9, 1), until=datetime(2026, 10, 1))
        assert sep_report["x"]["cost"] == 1.0 and sep_report["y"]["cost"] == 2.0

    def test_months_are_gzipped_and_still_read(self, sm):
        ledger = sm.ledger
        ledger.append([self._row("s1", "x", 1.0, kind="final", seq=1)], now=datetime(2026, 8, 1))
        ledger.totals()
        ledger.append([self._row("s2", "x", 2.0, kind="final", seq=2)], now=datetime(2026, 9, 1))
        assert [p.name for _, p in ledger.months()] == ["2026-08.jsonl.gz", "2026-09.jsonl"]
        assert ledger.totals()["x"]["cost"] == 3.0
        assert Ledger(sm.state_dir).totals(save=False)["x"]["cost"] == 3.0
        os.unlink(ledger.totals_path)
        assert ledger.totals()["x"]["cost"] == 3.0  # a full refold agrees

    def test_cli(self, sm):
        sm.ledger.append([self._row("s1", "client-x", 12.5, kind="final", seq=1)])
        result = runner.invoke(app, ["billing"])
        assert result.exit_code == 0 and "client-x" in result.output and "12.50" in result.output
        data = json.loads(runner.invoke(app, ["billing", "--json", "--since", "30d"]).output)
        assert data["codes"]["client-x"]["cost"] == 12.5
        assert runner.invoke(app, ["billing", "--month", "1999-01"]).exit_code == 0
        assert runner.invoke(app, ["billing", "--month", "soon"]).exit_code == 1


class TestStaleCheckpoints:
    """A daemon tick's checkpoint, built from its opening snapshot, lands after
    a kill or a rebill closed the segment: it must not reopen it."""

    def _stale(self, sm, s, cost):
        stale = sm.get_session(s.id)  # the tick's snapshot
        stale.stats.estimated_cost_usd = cost  # its staged stats
        return stale

    def test_after_a_rebill(self, sm):
        s = sm.create_session(name="a", tmux_session="agents", tmux_window="a-1", command=[])
        _spend(sm, s, 10.0, 1000)
        stale = self._stale(sm, s, 15.0)
        sm.rebill(s.id, "b")
        sm.ledger.append([make_row(stale, "checkpoint")])  # seg 0, code a, newer seq
        _spend(sm, s, 15.0, 1500)
        sm.delete_session(s.id)
        totals = sm.ledger.totals()
        assert (totals["a"]["cost"], totals["a"]["running"]) == (10.0, 0)
        assert (totals["b"]["cost"], totals["b"]["running"]) == (5.0, 0)
        report = sm.ledger.report()
        assert report["a"]["cost"] == 10.0 and report["a"]["running"] == 0
        assert report["b"]["cost"] == 5.0

    def test_after_a_kill(self, sm):
        s = sm.create_session(name="w", tmux_session="agents", tmux_window="w-1", command=[])
        _spend(sm, s, 2.0)
        stale = self._stale(sm, s, 2.0)
        sm.delete_session(s.id)
        sm.ledger.append([make_row(stale, "checkpoint")])
        assert sm.ledger.totals()["w"]["running"] == 0
        os.unlink(sm.ledger.totals_path)
        assert sm.ledger.totals()["w"]["running"] == 0  # a full refold agrees
        assert sm.ledger.report()["w"]["running"] == 0

    def test_across_a_period_boundary(self, sm):
        row = {"sid": "s", "billing_code": "x", "seg": 0, "tokens": 0, "base_cost": 0.0,
               "base_tokens": 0}
        sm.ledger.append([dict(row, cost=2.0, kind="final", seq=1, t="2026-09-30T23:59:59"),
                          dict(row, cost=3.0, kind="checkpoint", seq=2,
                               t="2026-10-01T00:00:01")])
        assert sm.ledger.report(since=datetime(2026, 10, 1)) == {}


class TestCheckpoints:
    def _hk(self):
        return Housekeeping("agents", tmux=MagicMock(), history_path=None, log=MagicMock(),
                            housekeeping=MagicMock(), rotation=MagicMock(),
                            model_metadata=MagicMock())

    def test_at_most_every_interval_and_only_on_change(self, sm):
        s = sm.create_session(name="a", tmux_session="agents", tmux_window="a-1", command=[])
        hk, start = self._hk(), datetime(2026, 10, 1, 12)
        _spend(sm, s, 1.0)
        assert hk.checkpoint_spend([sm.get_session(s.id)], sm.ledger, start) == 0  # clock starts
        later = start + timedelta(minutes=61)
        assert hk.checkpoint_spend([sm.get_session(s.id)], sm.ledger, later) == 1
        assert hk.checkpoint_spend([sm.get_session(s.id)], sm.ledger,
                                   later + timedelta(minutes=120)) == 0  # unchanged
        _spend(sm, s, 2.0)
        assert hk.checkpoint_spend([sm.get_session(s.id)], sm.ledger,
                                   later + timedelta(minutes=125)) == 1
        assert [r["cost"] for r in _rows(sm)] == [1.0, 2.0]
        assert sm.ledger.totals()["a"]["running"] == 1
        hk.checkpoint_spend([], sm.ledger, later)
        assert hk.last_checkpoint == {}  # a departed agent is forgotten


class TestBackfill:
    def test_archived_agents_get_one_row_once(self, sm):
        archived = []
        for name, cost in (("a", 1.0), ("b", 2.0)):
            s = sm.create_session(name=name, tmux_session="agents", tmux_window=f"{name}-1",
                                  command=[])
            _spend(sm, s, cost)
            archived.append(s)
        # Archived before the ledger existed
        with patch.object(sm.ledger, "append"):
            sm.delete_sessions([s.id for s in archived])
        assert _rows(sm) == []
        assert sm.ledger.backfill_from_archive(sm) == 2
        assert sm.ledger.backfill_from_archive(sm) == 0
        totals = sm.ledger.totals()
        assert totals["a"]["cost"] == 1.0 and totals["b"]["cost"] == 2.0


def test_pending_updates_unchanged_without_archive(sm):
    pending = PendingUpdates()
    assert not pending
    assert sm.commit_pending(pending) is False
    assert _rows(sm) == []
