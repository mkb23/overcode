"""Claude background sessions' spend in the billing ledger (0.6.1 follow-ups).

Owner decision: a 🌙 row's spend is billed to the agent it nests under
(children inherit, sticky codes), counted once, never also added to the
agent's own spend; its row's spend moves at the stats cadence; archive,
restart and resume never count it twice; ``overcode billing`` shows it.

Synthetic jobs directory and transcripts under ``tmp_path``.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from overcode import claude_bg
from overcode.claude_bg import FleetAgent
from overcode.claude_bg_ledger import STATE_NAME, BgLedger
from tests.unit.test_claude_bg import NOW, SID, _Clock, _Engine, _iso, job, write_job

pytestmark = pytest.mark.unit

RID = f"claude-bg:{SID}"


def _message(i: int, model="claude-haiku-5-5", output=20):
    return {"type": "assistant", "timestamp": f"2026-10-15T10:{37 + i // 60:02d}:{i % 60:02d}Z",
            "message": {"id": f"m{i}", "model": model, "content": [{"type": "text", "text": "x"}],
                        "usage": {"input_tokens": 100, "output_tokens": output,
                                  "cache_creation_input_tokens": 0,
                                  "cache_read_input_tokens": 1000}}}


def spend_more(transcript: Path, n: int = 1):
    """Append ``n`` new assistant messages: the session spent more."""
    transcript.parent.mkdir(parents=True, exist_ok=True)
    existing = transcript.read_text().count("\n") if transcript.exists() else 0
    with open(transcript, "a") as f:
        for i in range(existing, existing + n):
            f.write(json.dumps(_message(i)) + "\n")


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    """A test daemon, one agent billed to ``proj-x`` in tmp/repo, a jobs dir, a transcript."""
    from overcode.monitor_daemon import MonitorDaemon
    from overcode.session_manager import SessionManager

    state_dir = tmp_path / "state"
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(state_dir))
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    sm = SessionManager(state_dir=state_dir, skip_git_detection=True)
    agent = sm.create_session(name="alpha", tmux_session="agents", tmux_window="alpha-1",
                              command=["claude"], start_directory=str(tmp_path / "repo"),
                              billing_code="proj-x")
    clock = _Clock()
    transcript = tmp_path / "proj" / f"{SID}.jsonl"
    spend_more(transcript)

    def daemon_for():
        daemon = MonitorDaemon.for_test(state_dir, session_manager=sm, clock=clock,
                                        claude_jobs_dir=jobs)
        daemon._engine = _Engine(tmp_path / "e.sock")
        daemon.claude_bg.keep_seconds = lambda: 30.0
        daemon.claude_bg.is_enabled = lambda: True
        return daemon

    return _Fleet(daemon_for, sm, agent, jobs, clock, tmp_path, transcript)


class _Fleet:
    def __init__(self, daemon_for, sm, agent, jobs, clock, tmp, transcript):
        self.daemon_for = daemon_for
        self.daemon = daemon_for()
        self.sm, self.agent, self.jobs, self.clock = sm, agent, jobs, clock
        self.tmp, self.transcript = tmp, transcript

    def job(self, state="working", tempo="active", **extra):
        extra.setdefault("linkScanPath", str(self.transcript))
        return write_job(self.jobs, job(state, tempo, cwd=str(self.tmp / "repo"), **extra))

    def tick(self, seconds=0.0):
        """A full engine tick (the first of a daemon: later ones read the
        test agent's missing window as terminated)."""
        self.clock.t += seconds
        self.daemon._tick(self.clock.wall())

    def step(self, seconds=1.0):
        """Wake steps between ticks, one a second, as the engine runs them."""
        from overcode.engine_wake import WakeLoop

        loop = WakeLoop(self.daemon)
        for _ in range(int(seconds)):
            self.clock.t += 1.0
            loop.publish_claude_bg_if_due()

    def rows(self):
        return [r for r in self.sm.ledger.iter_rows() if r["sid"] == RID]

    def totals(self):
        return self.sm.ledger.totals(save=False)

    def view(self):
        return self.daemon._engine.agents.get(RID)


class TestBilled:
    def test_its_spend_is_billed_to_its_agent_s_code(self, fleet):
        fleet.job()
        fleet.tick()
        [row] = fleet.rows()
        view = fleet.view()
        assert row["billing_code"] == "proj-x"
        assert row["parent"] == fleet.agent.id
        assert row["claude_bg"] is True and row["kind"] == "checkpoint"
        assert row["cost"] == pytest.approx(view["estimated_cost_usd"]) and row["cost"] > 0
        totals = fleet.totals()["proj-x"]
        assert totals["cost"] == pytest.approx(row["cost"])
        assert totals["background"] == 1 and totals["running"] == 1

    def test_never_added_to_its_agent_s_own_spend(self, fleet):
        fleet.job()
        fleet.tick()
        own = [r for r in fleet.sm.ledger.iter_rows() if r["sid"] == fleet.agent.id]
        assert all(r["cost"] == 0 for r in own)
        assert (fleet.sm.get_session(fleet.agent.id).stats.estimated_cost_usd or 0) == 0
        # counted once: the code's total is the session's spend, nothing more
        assert fleet.totals()["proj-x"]["cost"] == pytest.approx(fleet.view()["estimated_cost_usd"])

    def test_a_child_s_session_bills_the_child_s_inherited_code(self, fleet):
        child = fleet.sm.create_session(
            name="kid", tmux_session="agents", tmux_window="kid-1", command=["claude"],
            start_directory=str(fleet.tmp / "repo" / "kid"), billing_code="proj-x")
        fleet.sm.update_session(child.id, parent_session_id=fleet.agent.id)
        write_job(fleet.jobs, job(cwd=str(fleet.tmp / "repo" / "kid"),
                                  linkScanPath=str(fleet.transcript)))
        fleet.tick()
        [row] = fleet.rows()
        assert row["parent"] == child.id and row["billing_code"] == "proj-x"

    def test_its_agent_s_own_conversation_is_not_billed_again(self, fleet):
        fleet.sm.add_agent_session_id(fleet.agent.id, SID)
        fleet.job()
        fleet.tick()
        assert fleet.rows() == []

    def test_billing_shows_it(self, fleet):
        from overcode.cli import app

        fleet.job()
        fleet.tick()
        out = CliRunner().invoke(app, ["billing"]).output
        assert "proj-x" in out and "background" in out
        data = json.loads(CliRunner().invoke(app, ["billing", "--json"]).output)
        assert data["codes"]["proj-x"]["background"] == 1


class TestLive:
    def test_the_row_s_spend_moves_at_the_stats_cadence(self, fleet):
        from overcode.engine_wake import WakeLoop
        from overcode.monitor_daemon import STATS_SYNC_ATTENDED_SECONDS

        fleet.job()
        fleet.tick()
        loop = WakeLoop(fleet.daemon)
        before = fleet.view()["estimated_cost_usd"]
        spend_more(fleet.transcript, 3)
        fleet.daemon._engine.deltas.clear()
        for _ in range(int(STATS_SYNC_ATTENDED_SECONDS) - 1):
            fleet.clock.t += 1.0
            loop.publish_claude_bg_if_due()
        assert fleet.view()["estimated_cost_usd"] == before  # holds still between reads
        fleet.clock.t += 1.0
        loop.publish_claude_bg_if_due()
        assert fleet.view()["estimated_cost_usd"] > before

    def test_checkpoints_follow_the_ledger_cadence(self, fleet):
        from overcode.engine_wake import WakeLoop

        fleet.daemon.claude_bg_ledger._every = lambda: 60.0
        fleet.job()
        fleet.tick()
        loop = WakeLoop(fleet.daemon)
        spend_more(fleet.transcript)
        for _ in range(30):
            fleet.clock.t += 1.0
            loop.publish_claude_bg_if_due()
        assert len(fleet.rows()) == 1  # moved, but not a minute yet
        fleet.clock.t += 31.0
        assert fleet.daemon._sync_claude_bg_ledger(force=True) == 1  # the housekeeping pass
        assert fleet.rows()[-1]["cost"] == pytest.approx(fleet.view()["estimated_cost_usd"])


class TestOnce:
    def test_finished_then_a_restart_counts_it_once(self, fleet):
        fleet.job()
        fleet.tick()
        spend_more(fleet.transcript)
        fleet.job("done", "idle", lastTerminalAt=_iso(fleet.clock.t))
        fleet.step(2)
        kinds = [r["kind"] for r in fleet.rows()]
        assert kinds == ["checkpoint", "final"]
        total = fleet.totals()["proj-x"]["cost"]
        fleet.daemon = fleet.daemon_for()  # the engine restarts
        fleet.tick(1.0)
        fleet.step(40)  # the finished row's time runs out
        fleet.daemon._sync_claude_bg_ledger(force=True)
        assert [r["kind"] for r in fleet.rows()] == kinds
        assert fleet.totals()["proj-x"]["cost"] == pytest.approx(total)
        assert fleet.totals()["proj-x"]["running"] == 0

    def test_a_resumed_session_opens_a_new_segment_from_where_it_closed(self, fleet):
        fleet.job("done", "idle", lastTerminalAt=_iso(fleet.clock.t))
        fleet.tick()
        closed_at = fleet.totals()["proj-x"]["cost"]
        assert [r["kind"] for r in fleet.rows()] == ["final"]
        spend_more(fleet.transcript, 2)
        fleet.job("working", "active", updatedAt=_iso(fleet.clock.t + 1))
        fleet.step(2)
        rows = fleet.rows()
        assert [(r["kind"], r["seg"]) for r in rows] == [("final", 0), ("open", 1)]
        assert rows[-1]["base_cost"] == pytest.approx(closed_at)
        # the code holds the whole session once, not the closed part twice
        assert fleet.totals()["proj-x"]["cost"] == pytest.approx(fleet.view()["estimated_cost_usd"])

    def test_a_row_gone_unfinished_closes_at_what_it_really_spent(self, fleet):
        fleet.job()
        fleet.tick()
        spend_more(fleet.transcript, 4)  # spent after the last read
        fleet.sm.delete_session(fleet.agent.id, archive=True)  # its agent is archived
        fleet.tick(1.0)
        rows = fleet.rows()
        assert [r["kind"] for r in rows] == ["checkpoint", "final"]
        assert rows[-1]["cost"] > rows[0]["cost"]
        assert claude_bg.read_spend({"linkScanPath": str(fleet.transcript)})["cost_usd"] == \
            pytest.approx(rows[-1]["cost"])
        totals = fleet.totals()["proj-x"]
        assert totals["running"] == 0 and totals["cost"] == pytest.approx(rows[-1]["cost"])

    def test_a_rebill_moves_new_spend_only(self, fleet):
        fleet.daemon.claude_bg_ledger._every = lambda: 0.0  # every move checkpointed
        fleet.job()
        fleet.tick()
        first = fleet.rows()[0]["cost"]
        from dataclasses import replace

        # `overcode rebill`: the next tick's agents carry the new code
        fleet.daemon.claude_bg.set_agents(
            [replace(a, billing_code="proj-y") for a in fleet.daemon.claude_bg.agents])
        spend_more(fleet.transcript, 2)
        fleet.step(10)
        totals = fleet.totals()
        assert [(r["kind"], r["billing_code"]) for r in fleet.rows()][:3] == [
            ("checkpoint", "proj-x"), ("close", "proj-x"), ("open", "proj-y")]
        assert totals["proj-x"]["cost"] == pytest.approx(fleet.rows()[1]["cost"])
        assert totals["proj-x"]["cost"] + totals["proj-y"]["cost"] == pytest.approx(
            fleet.view()["estimated_cost_usd"])
        assert first <= totals["proj-x"]["cost"]


class TestBgLedgerUnit:
    """The writer alone: cheap when nothing is due, one owner closes."""

    class _Row:
        def __init__(self, cost, ended=None, parent="a1", sid=SID):
            from types import SimpleNamespace

            self.state = SimpleNamespace(
                session_id=f"claude-bg:{sid}", name="probe", model="claude-haiku-5-5",
                estimated_cost_usd=cost, input_tokens=10, output_tokens=int(cost * 100),
                cache_creation_tokens=0, cache_read_tokens=0,
                claude_bg={"parent_id": parent, "session_id": sid})
            self.light = SimpleNamespace(ended_at=ended)

    AGENTS = (FleetAgent(id="a1", name="alpha", billing_code="code-a"),)

    def test_a_quiet_session_does_not_touch_the_state_file(self, tmp_path, monkeypatch):
        from overcode.ledger import Ledger

        ledger = Ledger(tmp_path / "reg")
        writer = BgLedger(lambda: ledger.dir, owner="agents", checkpoint_seconds=lambda: 60.0)
        assert writer.sync([self._Row(1.0)], self.AGENTS, ledger, NOW) == 1
        monkeypatch.setattr(BgLedger, "_locked", lambda *a: pytest.fail("locked"))
        for i in range(10):
            assert writer.sync([self._Row(1.0)], self.AGENTS, ledger, NOW + i) == 0

    def test_another_engine_s_open_segment_is_not_closed(self, tmp_path):
        from overcode.ledger import Ledger

        ledger = Ledger(tmp_path / "reg")
        mine = BgLedger(lambda: ledger.dir, owner="agents")
        theirs = BgLedger(lambda: ledger.dir, owner="other")
        mine.sync([self._Row(1.0)], self.AGENTS, ledger, NOW)
        assert theirs.sync([], self.AGENTS, ledger, NOW + 1) == 0
        assert mine.sync([], self.AGENTS, ledger, NOW + 2) == 1  # gone: closed by its owner
        state = json.loads((ledger.dir / STATE_NAME).read_text())["sessions"]
        assert state[RID]["closed"] is True
