"""
The sister API, end to end over real HTTP.

A sister overcode reaches this machine only through the API server
(web_server.OvercodeHandler). This module starts that server on an
ephemeral port and drives every route through the real sister client code
(SisterPoller, SisterController, ssh_provisioner), so a later cut to the
server cannot silently break sisters.

The data and control layers (web_api / web_control_api) are stubbed: what
is pinned here is the wire contract — path, method, auth header and body —
not what each action does (test_web_api / test_web_control_api cover that).
"""

import json
import re
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import MagicMock, patch
from urllib.request import Request, urlopen

import pytest

from overcode import web_control_api, web_server
from overcode.sister_controller import SisterController
from overcode.sister_poller import SisterPoller, SisterState
from overcode import ssh_provisioner

API_KEY = "sister-test-key"

STATUS = {
    "hostname": "remote-host",
    "version": "9.9.9",
    "daemon": {"running": True},
    "summary": {"green_agents": 1, "total_agents": 1},
    "agents": [{"name": "a1", "status": "running", "cost_usd": 0.5}],
}
AGENT = {"name": "a1", "status": "waiting_user"}
RAW_TIMELINE = {"hours": 2.0, "agents": {"a1": [{"t": "2026-10-07T10:00:00", "s": "running"}]}}
HEALTH = {"status": "ok", "timestamp": "2026-10-07T10:00:00", "version": "9.9.9"}

# Every control function a route can dispatch to.
CONTROL_FNS = [
    "send_to_agent", "send_key_to_agent", "kill_agent", "restart_agent",
    "launch_agent", "fork_agent", "resize_agent_window", "set_sleep",
    "pause_heartbeat", "resume_heartbeat", "set_standing_orders",
    "clear_standing_orders", "set_budget", "set_value", "set_annotation",
    "configure_heartbeat", "set_enhanced_context", "set_hook_detection",
    "cleanup_agents", "restart_monitor", "start_supervisor", "stop_supervisor",
    "shutdown_overcode", "toggle_summarizer",
]


@pytest.fixture
def server():
    """A live API server with auth on, control allowed, data/control stubbed."""
    control = {name: MagicMock(return_value={"ok": True, "fn": name}) for name in CONTROL_FNS}
    with (
        patch.object(web_server, "get_web_api_key", return_value=API_KEY),
        patch.object(web_server, "get_web_allow_control", return_value=True),
        patch.object(web_server, "touch_tui_attended"),
        patch.object(web_server, "get_status_data", return_value=STATUS),
        patch.object(web_server, "get_single_agent_status",
                     side_effect=lambda ts, name: AGENT if name == "a1" else None),
        patch.object(web_server, "get_raw_timeline_data", return_value=RAW_TIMELINE),
        patch.object(web_server, "get_health_data", return_value=HEALTH),
        patch.multiple(web_control_api, **control),
        patch.object(web_server.OvercodeHandler, "log_message"),
    ):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), web_server.OvercodeHandler)
        web_server.OvercodeHandler.tmux_session = "agents"
        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{httpd.server_address[1]}", control
        finally:
            httpd.shutdown()
            httpd.server_close()


def _sister(url: str) -> SisterState:
    return SisterState(name="remote", url=url, api_key=API_KEY)


def _poller(url: str) -> SisterPoller:
    with (
        patch("overcode.sister_poller.get_sisters_config",
              return_value=[{"name": "remote", "url": url, "api_key": API_KEY}]),
        patch("overcode.sister_poller.get_hostname", return_value="local"),
    ):
        return SisterPoller()


# ---------------------------------------------------------------------------
# GET routes
# ---------------------------------------------------------------------------


class TestSisterReadRoutes:

    def test_api_status_via_poller(self, server):
        url, _ = server
        poller = _poller(url)
        sessions = poller.poll_all()
        state = poller.get_sister_states()[0]
        assert state.reachable and state.daemon_running and state.version == "9.9.9"
        assert [s.name for s in sessions] == ["a1"]

    def test_api_status_via_provisioner_version_check(self, server):
        url, _ = server
        assert ssh_provisioner._check_version_local(url, API_KEY) == "9.9.9"

    def test_single_agent_status_via_poller(self, server):
        url, _ = server
        session = _poller(url).poll_single_agent(url, API_KEY, "a1")
        assert session is not None and session.name == "a1"
        assert _poller(url).poll_single_agent(url, API_KEY, "nope") is None  # 404

    def test_timeline_raw_via_poller(self, server):
        url, _ = server
        merged = _poller(url).poll_all_timelines(hours=2.0)
        assert list(merged) == ["remote/a1"]
        assert merged["remote/a1"][0][1] == "running"

    def test_health_via_provisioner(self, server):
        url, _ = server
        assert ssh_provisioner._check_health_local(url, API_KEY)["status"] == "ok"

    def test_reads_need_the_api_key(self, server):
        url, _ = server
        assert ssh_provisioner._check_health_local(url, "wrong") is None
        assert ssh_provisioner._check_version_local(url, "") == ""


# ---------------------------------------------------------------------------
# Control routes (SisterController)
# ---------------------------------------------------------------------------

# (controller method, args after (url, key), control fn, expected kwargs subset)
CONTROLLER_CALLS = [
    ("send_instruction", ("a1", "hello"), "send_to_agent", {"text": "hello", "enter": True}),
    ("send_key", ("a1", "Enter"), "send_key_to_agent", {"key": "Enter"}),
    ("kill_agent", ("a1",), "kill_agent", {"cascade": True}),
    ("restart_agent", ("a1",), "restart_agent", {}),
    ("launch_agent", ("/tmp/x", "newbie"), "launch_agent", {"directory": "/tmp/x", "name": "newbie"}),
    ("fork_agent", ("a1", "a1-fork"), "fork_agent", {"fork_name": "a1-fork"}),
    ("resize_agent", ("a1", 120, 40), "resize_agent_window", {"width": 120, "height": 40}),
    ("set_standing_orders", ("a1", "be terse"), "set_standing_orders", {"text": "be terse"}),
    ("clear_standing_orders", ("a1",), "clear_standing_orders", {}),
    ("set_budget", ("a1", 5.0), "set_budget", {"usd": 5.0}),
    ("set_value", ("a1", 2000), "set_value", {"value": 2000}),
    ("set_annotation", ("a1", "note"), "set_annotation", {"text": "note"}),
    ("set_sleep", ("a1", True), "set_sleep", {"asleep": True}),
    ("configure_heartbeat", ("a1",), "configure_heartbeat", {"enabled": True}),
    ("pause_heartbeat", ("a1",), "pause_heartbeat", {}),
    ("resume_heartbeat", ("a1",), "resume_heartbeat", {}),
    ("set_enhanced_context", ("a1", True), "set_enhanced_context", {"enabled": True}),
    ("set_hook_detection", ("a1", False), "set_hook_detection", {"enabled": False}),
    ("cleanup_agents", (), "cleanup_agents", {"include_done": False}),
    ("restart_monitor", (), "restart_monitor", {}),
    ("start_supervisor", (), "start_supervisor", {}),
    ("stop_supervisor", (), "stop_supervisor", {}),
]


class TestSisterControlRoutes:

    @pytest.mark.parametrize("method,args,fn,kwargs", CONTROLLER_CALLS,
                             ids=[c[0] for c in CONTROLLER_CALLS])
    def test_controller_reaches_its_handler(self, server, method, args, fn, kwargs):
        url, control = server
        result = getattr(SisterController(timeout=5), method)(url, API_KEY, *args)
        assert result.ok, result.error
        assert result.data.get("fn") == fn
        control[fn].assert_called_once()
        call = control[fn].call_args
        assert call.args[0] == "agents"
        if args and fn not in ("launch_agent", "cleanup_agents"):
            assert call.args[1] == "a1"
        for key, value in kwargs.items():
            assert call.kwargs[key] == value

    def test_controller_without_key_is_rejected(self, server):
        url, control = server
        result = SisterController(timeout=5).kill_agent(url, "", "a1")
        assert not result.ok and "Unauthorized" in result.error
        control["kill_agent"].assert_not_called()

    def test_shutdown_via_provisioner(self, server):
        """ssh_provisioner._stop_overcode curls POST /api/shutdown on the
        remote; replay the exact request it builds against the server."""
        url, control = server
        port = url.rsplit(":", 1)[1]
        commands = []
        with patch.object(ssh_provisioner, "_ssh_run", side_effect=lambda t, c, timeout: commands.append(c)):
            assert ssh_provisioner._stop_overcode("user@host", int(port), API_KEY)
        cmd = commands[0]
        assert "-X POST" in cmd and "X-API-Key: " + API_KEY in cmd
        target = re.search(r"(http://127\.0\.0\.1:\d+/\S+)", cmd).group(1)
        req = Request(target, data=b"", method="POST", headers={"X-API-Key": API_KEY})
        with urlopen(req, timeout=5) as resp:
            assert json.loads(resp.read())["fn"] == "shutdown_overcode"
        assert control["shutdown_overcode"].call_args.kwargs["scope"] == "services"


# ---------------------------------------------------------------------------
# The route set itself
# ---------------------------------------------------------------------------


class TestRouteSetIsTheSisterApi:
    """Adding or removing a server route must be a deliberate change here."""

    def test_get_routes(self):
        assert set(web_server._GET_ROUTES) == {"/api/status", "/api/timeline/raw", "/health"}

    def test_control_routes_are_all_exercised(self):
        exercised_fns = {c[2] for c in CONTROLLER_CALLS} | {"shutdown_overcode"}
        server_fns = set()
        fake = MagicMock()
        for handler in web_server._FIXED_CONTROL_ROUTES.values():
            fake.reset_mock()
            handler(fake, "agents", {})
            server_fns.add(fake.method_calls[0][0])
        for handler in web_server._AGENT_CONTROL_ROUTES.values():
            fake.reset_mock()
            handler(fake, "agents", "a1", {})
            server_fns.add(fake.method_calls[0][0])
        # toggle_summarizer is the one control route no sister calls.
        assert server_fns - exercised_fns == {"toggle_summarizer"}
        assert exercised_fns <= server_fns

    @pytest.mark.parametrize("path", ["/", "/index.html", "/dashboard", "/static/chart.min.js",
                                      "/api/timeline", "/api/analytics/stats"])
    def test_web_ui_paths_are_gone(self, server, path):
        url, _ = server
        req = Request(f"{url}{path}", headers={"X-API-Key": API_KEY})
        with pytest.raises(Exception) as exc:
            urlopen(req, timeout=5)
        assert getattr(exc.value, "code", None) == 404
