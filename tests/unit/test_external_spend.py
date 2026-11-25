"""Experiment billing: spend an agent's evals and sims make, outside its conversation.

``docs/design/experiment-billing.md``. Recorded as ledger runs under the
agent's billing code, charged to its budget, reported split from agent
spend. Synthetic records only.
"""

import json
import os
import subprocess
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from overcode import budget_gate
from overcode.cli import app
from overcode.external_spend import RecordError, Spend, record
from overcode.launcher import HOST_SESSION_VARS, AgentLauncher
from overcode.ledger import Ledger, make_row
from overcode.session_manager import SessionManager
from overcode.spend_client import OverBudget, SpendMeter, usage_tokens
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


def _usd(amount):
    return Spend(usd=amount, model="m-eval")


def _ext_rows(sm):
    return [r for r in sm.ledger.iter_rows() if r.get("external")]


class TestAttribution:
    def test_a_process_in_an_agent_bills_the_agent_and_its_code(self, launcher, sm):
        lead = launcher.launch(name="lead", billing_code="exp1")
        out = record(sm, _usd(1.5), env={"OVERCODE_SESSION_ID": lead.id}, label="sweep")
        assert out["agent"] == lead.id and out["code_name"] == "exp1"
        [row] = _ext_rows(sm)
        assert row["parent"] == lead.id and row["kind"] == "final"
        assert row["code"] == sm.get_session(lead.id).billing_code_id
        assert row["name"] == "sweep" and row["priced"] == "reported"
        assert sm.get_session(lead.id).stats.external_cost_usd == pytest.approx(1.5)
        # Its own conversation's spend is untouched
        assert sm.get_session(lead.id).stats.estimated_cost_usd == 0.0

    def test_bill_to_wins_over_the_job_owner_and_the_session(self, launcher, sm):
        a = launcher.launch(name="a", billing_code="ca")
        b = launcher.launch(name="b", billing_code="cb")
        out = record(sm, _usd(1.0), env={"OVERCODE_BILL_TO": "b", "OVERCODE_JOB_OWNER_ID": a.id,
                                         "OVERCODE_SESSION_ID": a.id})
        assert out["agent"] == b.id and out["code_name"] == "cb"
        out = record(sm, _usd(1.0), env={"OVERCODE_JOB_OWNER_ID": a.id})
        assert out["agent"] == a.id

    def test_nobody_to_bill_is_refused(self, sm):
        with pytest.raises(RecordError, match="nothing to bill"):
            record(sm, _usd(1.0), env={})
        assert _ext_rows(sm) == []

    def test_a_code_with_no_agent(self, sm):
        out = record(sm, _usd(2.0), code="exp-adhoc", env={})
        assert out["agent"] is None and out["code_name"] == "exp-adhoc"
        totals = sm.ledger.named(sm.ledger.totals())
        assert totals["exp-adhoc"]["cost"] == pytest.approx(2.0)
        assert totals["exp-adhoc"]["agents"] == 0 and totals["exp-adhoc"]["runs"] == 1

    def test_tokens_need_a_model(self, launcher, sm):
        lead = launcher.launch(name="lead")
        with pytest.raises(RecordError, match="model"):
            record(sm, Spend(tok_in=10), env={"OVERCODE_SESSION_ID": lead.id})
        with pytest.raises(RecordError):
            record(sm, Spend(model="x", tok_in=-1), env={"OVERCODE_SESSION_ID": lead.id})

    def test_tokens_are_priced_at_the_models_price(self, launcher, sm):
        from overcode.ledger import price
        from overcode.settings import get_model_pricing, get_user_config

        lead = launcher.launch(name="lead")
        model = "claude-sonnet-4-5"
        out = record(sm, Spend(model=model, tok_in=1_000_000, tok_out=100_000, tok_cr=50_000),
                     env={"OVERCODE_SESSION_ID": lead.id})
        mp = get_model_pricing(model, get_user_config(), provider="anthropic")
        expected = price([mp.input, mp.output, mp.cache_write, mp.cache_read],
                         {"in": 1_000_000, "out": 100_000, "cw": 0, "cr": 50_000})
        assert out["cost"] == pytest.approx(expected) and expected > 0
        assert out["priced"] == "estimate" and out["provider"] == "anthropic"
        [row] = _ext_rows(sm)
        assert row["tok_in"] == 1_000_000 and row["tok_cr"] == 50_000 and row["tokens"] == 1_150_000

    def test_an_unknown_model_is_priced_at_fallback_rates_and_says_so(self, launcher, sm):
        lead = launcher.launch(name="lead")
        out = record(sm, Spend(model="no-such-model-xyz", tok_in=1000),
                     env={"OVERCODE_SESSION_ID": lead.id})
        assert out["priced"] == "fallback"

    def test_an_archived_agent_still_bills_its_code(self, launcher, sm):
        lead = launcher.launch(name="lead", billing_code="exp1")
        sm.delete_session(lead.id)
        out = record(sm, _usd(3.0), env={"OVERCODE_SESSION_ID": lead.id})
        assert out["code_name"] == "exp1" and out["over_budget"] is False
        assert sm.ledger.named(sm.ledger.totals())["exp1"]["ext_cost"] == pytest.approx(3.0)


class TestRunsAndRollup:
    def test_a_run_adds_up_and_is_counted_once(self, launcher, sm):
        lead = launcher.launch(name="lead", billing_code="exp1")
        sm.update_stats(lead.id, estimated_cost_usd=4.0, total_tokens=100)
        env = {"OVERCODE_SESSION_ID": lead.id}
        record(sm, _usd(1.0), run="r1", env=env)
        out = record(sm, _usd(0.5), run="r1", env=env)
        record(sm, _usd(2.0), run="r2", env=env)
        assert out["run_cost"] == pytest.approx(1.5)
        rows = _ext_rows(sm)
        assert [r["cost"] for r in rows] == pytest.approx([1.0, 1.5, 2.0])  # cumulative
        sm.ledger.append([make_row(sm.get_session(lead.id), "checkpoint")])
        entry = sm.ledger.named(sm.ledger.totals())["exp1"]
        assert entry["cost"] == pytest.approx(4.0 + 3.5)
        assert entry["ext_cost"] == pytest.approx(3.5)
        assert entry["runs"] == 2 and entry["agents"] == 1
        assert sm.get_session(lead.id).stats.external_cost_usd == pytest.approx(3.5)

    def test_the_incremental_fold_matches_a_fresh_one(self, launcher, sm):
        lead = launcher.launch(name="lead", billing_code="exp1")
        env = {"OVERCODE_SESSION_ID": lead.id}
        record(sm, _usd(1.0), run="r1", env=env)
        first = sm.ledger.totals()  # folded and saved
        record(sm, _usd(1.0), run="r1", env=env)
        record(sm, _usd(1.0), run="r2", env=env)
        incremental = sm.ledger.totals()
        sm.ledger.totals_path.unlink()
        fresh = Ledger(sm.state_dir).totals()
        assert first != incremental
        assert incremental == fresh
        code = sm.get_session(lead.id).billing_code_id
        assert fresh[code]["ext_cost"] == pytest.approx(3.0) and fresh[code]["runs"] == 2

    def test_a_period_report_counts_what_the_run_spent_in_it(self, launcher, sm):
        lead = launcher.launch(name="lead", billing_code="exp1")
        env = {"OVERCODE_SESSION_ID": lead.id}
        old = datetime.now() - timedelta(days=10)
        record(sm, _usd(1.0), run="r1", env=env, now=old)
        record(sm, _usd(2.0), run="r1", env=env)
        code = sm.get_session(lead.id).billing_code_id
        week = sm.ledger.report(since=datetime.now() - timedelta(days=7))
        assert week[code]["cost"] == pytest.approx(2.0)
        assert week[code]["ext_cost"] == pytest.approx(2.0) and week[code]["runs"] == 1
        assert sm.ledger.report()[code]["cost"] == pytest.approx(3.0)

    def test_a_rebill_moves_later_spend_forward(self, launcher, sm):
        lead = launcher.launch(name="lead", billing_code="exp1")
        env = {"OVERCODE_SESSION_ID": lead.id}
        record(sm, _usd(1.0), run="r1", env=env)
        sm.rebill(lead.id, "exp2")
        record(sm, _usd(2.0), run="r1", env=env)
        totals = sm.ledger.named(sm.ledger.totals())
        assert totals["exp1"]["ext_cost"] == pytest.approx(1.0)
        assert totals["exp2"]["ext_cost"] == pytest.approx(2.0)
        assert [r["seg"] for r in _ext_rows(sm)] == [0, 1]

    def test_a_child_bills_the_inherited_code_and_the_parents_subtree(self, launcher, sm):
        lead = launcher.launch(name="lead", billing_code="exp1", budget_usd=10.0)
        with patch.dict(os.environ, {"OVERCODE_SESSION_NAME": "lead"}):
            kid = launcher.launch(name="kid", budget_usd=4.0)
        out = record(sm, _usd(1.25), env={"OVERCODE_SESSION_ID": kid.id})
        assert out["code_name"] == "exp1"
        with sm._locked_state() as state:
            assert SessionManager._subtree_spend(lead.id, state) == pytest.approx(1.25)

    def test_old_totals_entries_without_the_new_fields_still_report(self, launcher, sm):
        lead = launcher.launch(name="lead", billing_code="exp1")
        sm.update_stats(lead.id, estimated_cost_usd=2.0)
        sm.ledger.append([make_row(sm.get_session(lead.id), "final")])
        sm.ledger.totals()
        data = json.loads(sm.ledger.totals_path.read_text())
        for entry in data["codes"].values():  # as an older overcode saved it
            for key in ("runs", "ext_cost", "ext_tokens"):
                entry.pop(key, None)
        sm.ledger.totals_path.write_text(json.dumps(data))
        record(sm, _usd(1.0), code="exp1", env={})
        entry = sm.ledger.named(sm.ledger.totals())["exp1"]
        assert entry["cost"] == pytest.approx(3.0)
        assert entry["ext_cost"] == pytest.approx(1.0) and entry["runs"] == 1


class TestBudget:
    def test_eval_spend_counts_against_the_budget(self, launcher, sm):
        lead = launcher.launch(name="lead", budget_usd=5.0)
        sm.update_stats(lead.id, estimated_cost_usd=3.0)
        env = {"OVERCODE_SESSION_ID": lead.id}
        out = record(sm, _usd(1.0), env=env)
        assert out["over_budget"] is False and out["charged"] == pytest.approx(4.0)
        assert not budget_gate.is_budget_exceeded(sm.get_session(lead.id))
        out = record(sm, _usd(1.0), env=env)
        assert out["over_budget"] is True
        session = sm.get_session(lead.id)
        assert budget_gate.is_budget_exceeded(session)
        assert "$5.00 of $5.00" in budget_gate.refusal(session, sm)

    def test_charged_cost_reads_records_and_stats(self):
        assert budget_gate.charged_cost({"estimated_cost_usd": 1, "external_cost_usd": 2}) == 3
        assert budget_gate.charged_cost({}) == 0
        assert budget_gate.charged_cost(None) == 0

    def test_reclaim_leaves_what_the_child_spent_on_evals(self, launcher, sm):
        launcher.launch(name="lead", budget_usd=10.0)
        with patch.dict(os.environ, {"OVERCODE_SESSION_NAME": "lead"}):
            kid = launcher.launch(name="kid", budget_usd=4.0)
        record(sm, _usd(1.0), env={"OVERCODE_SESSION_ID": kid.id})
        assert sm.reclaim_budget(kid.id) == pytest.approx(3.0)
        assert sm.get_session(kid.id).cost_budget_usd == pytest.approx(1.0)

    def test_old_records_without_external_fields_load(self, launcher, sm):
        lead = launcher.launch(name="lead", budget_usd=5.0)
        with sm._locked_state() as state:
            state[lead.id]["stats"].pop("external_cost_usd", None)
            state[lead.id]["stats"].pop("external_tokens", None)
        session = sm.get_session(lead.id)
        assert session.stats.external_cost_usd == 0.0
        assert not budget_gate.is_budget_exceeded(session)


class TestEnvironment:
    def test_launch_lines_clear_bill_to(self):
        assert "OVERCODE_BILL_TO" in HOST_SESSION_VARS

    def test_a_job_exports_bill_to_for_its_agent(self, tmp_path):
        from unittest.mock import MagicMock

        from overcode.job_launcher import JobLauncher
        from overcode.job_manager import JobManager

        tmux = MagicMock()
        tmux.create_window.return_value = "w"
        jl = JobLauncher(tmux_manager=tmux, job_manager=JobManager(state_dir=tmp_path))
        jl.launch("python eval.py", directory=str(tmp_path), agent_session_id="abc123",
                  parent_launches=False)
        sent = tmux.send_keys.call_args[0][1]
        assert "export OVERCODE_BILL_TO=abc123;" in sent
        assert "unset OVERCODE_JOB_OWNER_ID;" in sent
        jl.launch("python eval.py", directory=str(tmp_path))
        assert "unset OVERCODE_BILL_TO;" in tmux.send_keys.call_args[0][1]


class TestCli:
    def test_record_and_report(self, launcher, sm, monkeypatch):
        lead = launcher.launch(name="lead", billing_code="exp1", budget_usd=20.0)
        sm.update_stats(lead.id, estimated_cost_usd=4.0)
        sm.ledger.append([make_row(sm.get_session(lead.id), "checkpoint")])
        monkeypatch.setenv("OVERCODE_SESSION_ID", lead.id)
        result = runner.invoke(app, ["spend", "record", "--usd", "1.5", "--label", "sweep-a",
                                     "--run", "r1", "--json"])
        assert result.exit_code == 0, result.output
        answer = json.loads(result.output)
        assert answer["run"] == "r1" and answer["cost"] == pytest.approx(1.5)
        assert answer["charged"] == pytest.approx(5.5)

        result = runner.invoke(app, ["billing"])
        assert result.exit_code == 0, result.output
        assert "evals" in result.output and "$     1.50" in result.output

        result = runner.invoke(app, ["billing", "show", "exp1", "--json"])
        assert result.exit_code == 0, result.output
        shown = json.loads(result.output)
        assert shown["agents_cost"] == pytest.approx(4.0)
        assert shown["evals_cost"] == pytest.approx(1.5)
        assert shown["runs"][0]["label"] == "sweep-a" and shown["runs"][0]["agent_name"] == "lead"

        result = runner.invoke(app, ["billing", "show", "exp1"])
        assert "agents $4.00 + evals $1.50" in result.output

        result = runner.invoke(app, ["budget", "show", "lead"])
        assert "spent=$5.5000" in result.output and "evals: $1.5000" in result.output

    def test_stdin_batch_is_checked_before_anything_is_recorded(self, launcher, sm, monkeypatch):
        lead = launcher.launch(name="lead")
        monkeypatch.setenv("OVERCODE_SESSION_ID", lead.id)
        lines = json.dumps({"usd": 1.0}) + "\n" + json.dumps({"in": 5}) + "\n"
        result = runner.invoke(app, ["spend", "record", "--stdin", "--json"], input=lines)
        assert result.exit_code == 1
        assert "model" in json.loads(result.output)["error"]
        assert _ext_rows(sm) == []

    def test_unknown_code_for_show(self, sm):
        result = runner.invoke(app, ["billing", "show", "nope"])
        assert result.exit_code == 1


def _cli_subprocess(args, input=None, **_):
    """``subprocess.run`` of the recorder, in process (CliRunner)."""
    assert args[0] == "overcode"
    result = runner.invoke(app, args[1:], input=input)
    return subprocess.CompletedProcess(args, result.exit_code, stdout=result.output, stderr="")


class TestSpendClient:
    def test_usage_shapes(self):
        anthropic = {"input_tokens": 100, "output_tokens": 20,
                     "cache_creation_input_tokens": 5, "cache_read_input_tokens": 50}
        assert usage_tokens(anthropic) == {"in": 100, "out": 20, "cache_write": 5, "cache_read": 50}
        openai = {"prompt_tokens": 100, "completion_tokens": 20,
                  "prompt_tokens_details": {"cached_tokens": 30}}
        assert usage_tokens(openai) == {"in": 70, "out": 20, "cache_write": 0, "cache_read": 30}

        class Obj:
            input_tokens, output_tokens = 7, 3
        assert usage_tokens(Obj())["in"] == 7

    def test_meter_batches_per_model_and_records_one_run(self, launcher, sm, monkeypatch):
        lead = launcher.launch(name="lead", billing_code="exp1")
        monkeypatch.setenv("OVERCODE_SESSION_ID", lead.id)
        with patch("overcode.spend_client.subprocess.run", side_effect=_cli_subprocess) as run:
            meter = SpendMeter(label="sweep", flush_seconds=3600)
            for _ in range(3):
                meter.add_usage({"input_tokens": 1000, "output_tokens": 100}, model="claude-sonnet-4-5")
            meter.add(usd=0.25)
            answer = meter.close()
        assert run.call_count == 1
        rows = _ext_rows(sm)
        assert {r["sid"] for r in rows} == {f"ext:{meter.run}"}
        assert rows[-1]["tok_in"] == 3000 and rows[-1]["name"] == "sweep"
        assert answer["run_cost"] == pytest.approx(rows[-1]["cost"])
        assert rows[-1]["cost"] > 0.25

    def test_a_failed_flush_keeps_the_usage(self, monkeypatch, capsys):
        meter = SpendMeter(flush_seconds=3600, command=["overcode-missing-xyz"])
        meter.add(usd=1.0)
        assert meter.flush() is None
        assert "could not record" in capsys.readouterr().err
        assert meter._pending  # kept for the next flush
        meter._closed = True

    def test_stop_on_budget(self, launcher, sm, monkeypatch):
        lead = launcher.launch(name="lead", budget_usd=1.0)
        monkeypatch.setenv("OVERCODE_SESSION_ID", lead.id)
        with patch("overcode.spend_client.subprocess.run", side_effect=_cli_subprocess):
            meter = SpendMeter(flush_seconds=3600, stop_on_budget=True)
            meter.add(usd=2.0)
            with pytest.raises(OverBudget, match="over its budget"):
                meter.flush()
            meter._closed = True
