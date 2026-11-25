"""The spend gateway: tokens in workers' env, allowances out, usage in.

``docs/design/experiment-billing.md``. The gateway is a fake (in memory,
or a localhost HTTP server); synthetic records only.
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock, patch

import pytest

from overcode import gateway
from overcode.external_spend import _load_state
from overcode.gateway import GatewayClient, GatewayError, agent_of, sync, token_for
from overcode.launcher import AgentLauncher
from overcode.session_manager import SessionManager
from overcode.tmux_manager import TmuxManager
from tests.tmux_doubles import MockTmux

CFG = {"url": "http://gw.invalid/overcode", "admin_key_env": "OVERCODE_GATEWAY_KEY",
       "env": {"OPENAI_BASE_URL": "https://gw.invalid/v1", "OPENAI_API_KEY": "{token}"},
       "sync_seconds": 30.0, "timeout_seconds": 5.0, "warnings": []}


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


class FakeGateway:
    """The gateway's overcode API in memory: a usage feed with integer cursors."""

    def __init__(self):
        self.events = []
        self.pushed = None
        self.page = 2

    def emit(self, token, model="claude-sonnet-4-5", **usage):
        self.events.append({"id": f"e{len(self.events) + 1}", "token": token,
                            "model": model, **usage})

    def put_allowances(self, allowances):
        self.pushed = allowances

    def usage(self, after=None, limit=1000):
        start = int(after or 0)
        chunk = self.events[start:start + self.page]
        end = start + len(chunk)
        return {"events": chunk, "next": end, "more": end < len(self.events)}


def _ext_cost(sm, agent_id):
    return sm.get_session(agent_id).stats.external_cost_usd


class TestTokens:
    def test_a_token_names_its_agent_and_cannot_be_forged(self, sm):
        token = token_for(sm.ledger.dir, "agent-1")
        assert token.startswith("ocg_agent-1.")
        assert agent_of(sm.ledger.dir, token) == "agent-1"
        assert token_for(sm.ledger.dir, "agent-1") == token  # stable: no storage
        forged = "ocg_agent-2." + token.rsplit(".", 1)[1]
        assert agent_of(sm.ledger.dir, forged) is None
        assert agent_of(sm.ledger.dir, "sk-whatever") is None
        assert agent_of(sm.ledger.dir, None) is None
        assert oct((sm.ledger.dir / gateway.SECRET_NAME).stat().st_mode & 0o777) == "0o600"

    def test_the_env_carries_the_agents_token(self, sm):
        env = gateway.worker_env(CFG, sm.ledger.dir, "agent-1")
        assert env == {"OPENAI_BASE_URL": "https://gw.invalid/v1",
                       "OPENAI_API_KEY": token_for(sm.ledger.dir, "agent-1")}

    def test_no_gateway_no_env(self, sm):
        assert gateway.worker_env(None, sm.ledger.dir, "a") == {}
        with patch("overcode.config.get_gateway_config", return_value=None):
            assert gateway.configured_worker_env(sm.ledger.dir, "a") == {}

    def test_a_job_exports_its_agents_gateway_env(self, tmp_path):
        from overcode.job_launcher import JobLauncher
        from overcode.job_manager import JobManager
        from overcode.paths import ledger_dir

        tmux = MagicMock()
        tmux.create_window.return_value = "w"
        jl = JobLauncher(tmux_manager=tmux, job_manager=JobManager(state_dir=tmp_path))
        with patch("overcode.config.get_gateway_config", return_value=CFG):
            jl.launch("python eval.py", directory=str(tmp_path), agent_session_id="abc123")
        sent = tmux.send_keys.call_args[0][1]
        assert f"export OPENAI_API_KEY={token_for(ledger_dir(), 'abc123')};" in sent


class TestLaunchLine:
    def test_env_is_in_the_launch_command(self, launcher, sm):
        with patch("overcode.config.get_gateway_config", return_value=CFG), \
             patch.object(launcher.tmux, "send_keys", wraps=launcher.tmux.send_keys) as send:
            agent = launcher.launch(name="lead")
        typed = " ".join(str(a) for call in send.call_args_list for a in call.args)
        assert f"OPENAI_API_KEY={token_for(sm.ledger.dir, agent.id)}" in typed


class TestSync:
    def test_allowances_are_the_budget_less_the_agents_own_spend(self, launcher, sm):
        lead = launcher.launch(name="lead", billing_code="exp1", budget_usd=10.0)
        free = launcher.launch(name="free")
        sm.update_stats(lead.id, estimated_cost_usd=3.0)
        gw = FakeGateway()
        sync(sm, gw)
        by_agent = {a["agent_id"]: a for a in gw.pushed}
        assert by_agent[lead.id]["limit_usd"] == pytest.approx(7.0)
        assert by_agent[lead.id]["code_name"] == "exp1"
        assert by_agent[lead.id]["token"] == token_for(sm.ledger.dir, lead.id)
        assert by_agent[free.id]["limit_usd"] is None

    def test_usage_is_recorded_once_per_event_across_pages_and_syncs(self, launcher, sm):
        lead = launcher.launch(name="lead", billing_code="exp1", budget_usd=10.0)
        token = token_for(sm.ledger.dir, lead.id)
        gw = FakeGateway()
        for _ in range(5):
            gw.emit(token, cost_usd=0.5, input_tokens=100, output_tokens=10)
        counts = sync(sm, gw)
        assert counts["events"] == 5 and counts["pages"] == 3
        assert _ext_cost(sm, lead.id) == pytest.approx(2.5)
        assert sync(sm, gw)["events"] == 0  # nothing new: nothing twice
        gw.emit(token, cost_usd=1.0)
        sync(sm, gw)
        assert _ext_cost(sm, lead.id) == pytest.approx(3.5)
        entry = sm.ledger.named(sm.ledger.totals())["exp1"]
        assert entry["ext_cost"] == pytest.approx(3.5) and entry["runs"] == 1
        assert _load_state(sm.ledger)["cursors"]["gateway"] == 6
        # One row per run per batch, not per event
        rows = [r for r in sm.ledger.iter_rows() if r.get("external")]
        assert len(rows) == 4 and {r["sid"] for r in rows} == {f"ext:gw-{lead.id}"}

    def test_the_gateway_counts_against_the_budget_and_the_next_push_says_so(self, launcher, sm):
        lead = launcher.launch(name="lead", budget_usd=5.0)
        sm.update_stats(lead.id, estimated_cost_usd=2.0)
        gw = FakeGateway()
        gw.emit(token_for(sm.ledger.dir, lead.id), cost_usd=3.0)
        sync(sm, gw)
        from overcode.budget_gate import is_budget_exceeded
        assert is_budget_exceeded(sm.get_session(lead.id))
        sync(sm, gw)
        [mine] = [a for a in gw.pushed if a["agent_id"] == lead.id]
        assert mine["limit_usd"] == pytest.approx(3.0) and mine["metered_usd"] == pytest.approx(3.0)

    def test_tokens_are_priced_when_the_gateway_gives_no_cost(self, launcher, sm):
        lead = launcher.launch(name="lead")
        gw = FakeGateway()
        gw.emit(token_for(sm.ledger.dir, lead.id), input_tokens=1_000_000)
        sync(sm, gw)
        assert _ext_cost(sm, lead.id) > 0

    def test_a_token_that_does_not_verify_bills_the_default_code(self, launcher, sm):
        launcher.launch(name="lead")
        gw = FakeGateway()
        gw.emit("ocg_someone.0000", cost_usd=2.0)
        sync(sm, gw)
        totals = sm.ledger.named(sm.ledger.totals())
        assert totals["general"]["ext_cost"] == pytest.approx(2.0)

    def test_a_child_bills_its_own_token_under_the_inherited_code(self, launcher, sm):
        launcher.launch(name="lead", billing_code="exp1", budget_usd=10.0)
        with patch.dict(os.environ, {"OVERCODE_SESSION_NAME": "lead"}):
            kid = launcher.launch(name="kid", budget_usd=2.0)
        gw = FakeGateway()
        gw.emit(token_for(sm.ledger.dir, kid.id), cost_usd=1.0)
        sync(sm, gw)
        assert _ext_cost(sm, kid.id) == pytest.approx(1.0)
        assert sm.ledger.named(sm.ledger.totals())["exp1"]["ext_cost"] == pytest.approx(1.0)

    def test_an_archived_agents_late_usage_still_bills_its_code(self, launcher, sm):
        lead = launcher.launch(name="lead", billing_code="exp1")
        token = token_for(sm.ledger.dir, lead.id)
        sm.delete_session(lead.id)
        gw = FakeGateway()
        gw.emit(token, cost_usd=1.5)
        sync(sm, gw)
        assert sm.ledger.named(sm.ledger.totals())["exp1"]["ext_cost"] == pytest.approx(1.5)

    def test_rows_a_crash_kept_from_the_ledger_are_written_next_time(self, launcher, sm):
        lead = launcher.launch(name="lead", billing_code="exp1")
        token = token_for(sm.ledger.dir, lead.id)
        gw = FakeGateway()
        gw.emit(token, cost_usd=1.0)
        with patch.object(type(sm.ledger), "append_locked", side_effect=OSError("disk")):
            with pytest.raises(OSError):
                sync(sm, gw)
        # The cursor moved with the run state: the event is not fetched again
        assert _load_state(sm.ledger)["cursors"]["gateway"] == 1
        gw.emit(token, cost_usd=2.0)
        sync(sm, gw)
        assert sm.ledger.named(sm.ledger.totals())["exp1"]["ext_cost"] == pytest.approx(3.0)
        # And a crash before the sessions write is healed from the runs
        assert _ext_cost(sm, lead.id) == pytest.approx(3.0)

    def test_one_sync_at_a_time(self, sm):
        with gateway._sync_lock(sm.ledger.dir) as first:
            assert first
            assert sync(sm, FakeGateway()) is None

    def test_a_gateway_failure_records_nothing(self, launcher, sm):
        launcher.launch(name="lead")
        gw = MagicMock()
        gw.usage.side_effect = GatewayError("down")
        with pytest.raises(GatewayError):
            sync(sm, gw)
        assert "gateway" not in _load_state(sm.ledger).get("cursors", {})


class _Handler(BaseHTTPRequestHandler):
    server_version = "fake-gw"

    def log_message(self, *args):
        pass

    def _reply(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.server.seen.append(("GET", self.path, self.headers.get("Authorization")))
        self._reply({"events": self.server.events, "next": len(self.server.events), "more": False})

    def do_PUT(self):
        length = int(self.headers.get("Content-Length", 0))
        self.server.seen.append(("PUT", self.path, json.loads(self.rfile.read(length))))
        self._reply({})


class TestHttpClient:
    def test_round_trip(self, launcher, sm):
        lead = launcher.launch(name="lead", billing_code="exp1")
        server = HTTPServer(("127.0.0.1", 0), _Handler)
        server.seen = []
        server.events = [{"id": "e1", "token": token_for(sm.ledger.dir, lead.id),
                          "model": "m", "cost_usd": 0.75}]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/overcode"
            client = GatewayClient.from_config({**CFG, "url": url},
                                               env={"OVERCODE_GATEWAY_KEY": "admin"})
            counts = sync(sm, client)
        finally:
            server.shutdown()
            server.server_close()
        assert counts["events"] == 1
        put = [s for s in server.seen if s[0] == "PUT"][0]
        assert put[1] == "/overcode/allowances" and put[2]["agents"][0]["agent_id"] == lead.id
        get = [s for s in server.seen if s[0] == "GET"][0]
        assert get[1].startswith("/overcode/usage?") and get[2] == "Bearer admin"
        assert _ext_cost(sm, lead.id) == pytest.approx(0.75)

    def test_unreachable(self):
        client = GatewayClient("http://127.0.0.1:9/overcode", timeout=0.5)
        with pytest.raises(GatewayError):
            client.usage()


class TestConfig:
    def test_parse(self):
        from overcode import config

        raw = {"url": "https://gw/oc/", "env": {"OPENAI_API_KEY": "{token}", "bad var": "x"},
               "nope": 1}
        with patch.object(config, "_get_config_value", return_value=raw):
            cfg = config.get_gateway_config()
        assert cfg["url"] == "https://gw/oc" and cfg["env"] == {"OPENAI_API_KEY": "{token}"}
        assert cfg["sync_seconds"] == 30.0 and cfg["admin_key_env"] == "OVERCODE_GATEWAY_KEY"
        assert len(cfg["warnings"]) == 2
        with patch.object(config, "_get_config_value", return_value=None):
            assert config.get_gateway_config() is None
        with patch.object(config, "_get_config_value", return_value="junk"):
            assert config.get_gateway_config() is None


class TestDaemon:
    def test_sync_runs_on_a_thread_when_configured_and_backs_off(self, sm):
        from overcode.daemon_housekeeping import Housekeeping

        hk = Housekeeping.__new__(Housekeeping)
        hk.gateway_thread = None
        hk.last_gateway_sync = None
        hk.gateway_failing = False
        hk.clock = MagicMock()
        hk.clock.monotonic.return_value = 1000.0
        hk.log = MagicMock()
        with patch("overcode.config.get_gateway_config", return_value=CFG), \
             patch("overcode.gateway.sync", side_effect=GatewayError("down")) as run:
            hk.maybe_sync_gateway(sm)
            hk.gateway_thread.join(5)
            assert run.call_count == 1 and hk.gateway_failing
            hk.clock.monotonic.return_value = 1000.0 + 31
            hk.maybe_sync_gateway(sm)  # backed off: ten intervals
            assert run.call_count == 1
            hk.clock.monotonic.return_value = 1000.0 + 301
            hk.maybe_sync_gateway(sm)
            hk.gateway_thread.join(5)
            assert run.call_count == 2
        assert hk.log.warn.call_count == 1  # warned once

    def test_no_gateway_no_thread(self, sm):
        from overcode.daemon_housekeeping import Housekeeping

        hk = Housekeeping.__new__(Housekeeping)
        hk.gateway_thread = None
        with patch("overcode.config.get_gateway_config", return_value=None):
            hk.maybe_sync_gateway(sm)
        assert hk.gateway_thread is None
