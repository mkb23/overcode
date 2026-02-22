"""
Unit tests for sister_poller module.

Tests agent-to-session conversion, unreachable handling, and ID generation.
"""

import json
import pytest
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from unittest.mock import patch, MagicMock

from overcode.sister_poller import SisterPoller, SisterState, _agent_to_session
from overcode.session_manager import Session


class TestAgentToSession:
    """Test conversion from API agent dict to Session."""

    def test_basic_conversion(self):
        agent = {
            "name": "agent1",
            "status": "running",
            "cost_usd": 1.23,
            "tokens_raw": 50000,
            "activity": "Writing tests...",
            "repo": "myrepo",
            "branch": "main",
            "green_time_raw": 600.0,
            "non_green_time_raw": 120.0,
            "robot_steers": 3,
            "human_interactions": 5,
            "permissiveness_mode": "normal",
        }

        session = _agent_to_session(agent, "macbook-pro")

        assert session.id == "remote:macbook-pro:agent1"
        assert session.name == "agent1"
        assert session.is_remote is True
        assert session.source_host == "macbook-pro"
        assert session.stats.estimated_cost_usd == 1.23
        assert session.stats.total_tokens == 50000
        assert session.stats.current_state == "running"
        assert session.stats.current_task == "Writing tests..."
        assert session.stats.green_time_seconds == 600.0
        assert session.stats.non_green_time_seconds == 120.0
        assert session.repo_name == "myrepo"
        assert session.branch == "main"

    def test_id_format_is_deterministic(self):
        agent = {"name": "worker-3", "status": "running"}
        s1 = _agent_to_session(agent, "desktop")
        s2 = _agent_to_session(agent, "desktop")
        assert s1.id == s2.id == "remote:desktop:worker-3"

    def test_different_hosts_produce_different_ids(self):
        agent = {"name": "agent1", "status": "running"}
        s1 = _agent_to_session(agent, "host-a")
        s2 = _agent_to_session(agent, "host-b")
        assert s1.id != s2.id

    def test_missing_fields_use_defaults(self):
        agent = {"name": "minimal", "status": "waiting_user"}
        session = _agent_to_session(agent, "host")

        assert session.is_remote is True
        assert session.stats.estimated_cost_usd == 0.0
        assert session.stats.total_tokens == 0
        assert session.repo_name is None
        assert session.branch is None

    def test_terminated_status_maps_to_session_status(self):
        agent = {"name": "dead", "status": "terminated"}
        session = _agent_to_session(agent, "host")
        assert session.status == "terminated"

    def test_running_status_maps_to_running(self):
        agent = {"name": "alive", "status": "running"}
        session = _agent_to_session(agent, "host")
        assert session.status == "running"

    def test_standing_orders_flag(self):
        agent = {"name": "a", "status": "running", "standing_orders": True}
        session = _agent_to_session(agent, "host")
        assert session.standing_instructions == "(remote)"

        agent2 = {"name": "b", "status": "running", "standing_orders": False}
        session2 = _agent_to_session(agent2, "host")
        assert session2.standing_instructions == ""

    def test_cost_budget_round_trip(self):
        agent = {"name": "a", "status": "running", "cost_budget_usd": 5.0}
        session = _agent_to_session(agent, "host")
        assert session.cost_budget_usd == 5.0

    def test_done_status_passes_through(self):
        """Done agents from sisters should be filterable by hide-done (#D key)."""
        agent = {"name": "finished", "status": "done"}
        session = _agent_to_session(agent, "host")
        assert session.status == "done"
        assert session.stats.current_state == "done"
        assert session.is_asleep is False

    def test_asleep_status_passes_through(self):
        """Asleep agents from sisters should be filterable by hide-asleep."""
        agent = {"name": "sleeping", "status": "asleep"}
        session = _agent_to_session(agent, "host")
        assert session.status == "asleep"
        assert session.is_asleep is True
        assert session.stats.current_state == "asleep"

    def test_waiting_user_status_passes_through(self):
        """Non-running statuses should pass through for correct display."""
        agent = {"name": "blocked", "status": "waiting_user"}
        session = _agent_to_session(agent, "host")
        assert session.status == "waiting_user"
        assert session.stats.current_state == "waiting_user"

    def test_error_status_passes_through(self):
        agent = {"name": "broken", "status": "error"}
        session = _agent_to_session(agent, "host")
        assert session.status == "error"
        assert session.stats.current_state == "error"

    def test_waiting_approval_status_passes_through(self):
        agent = {"name": "pending", "status": "waiting_approval"}
        session = _agent_to_session(agent, "host")
        assert session.status == "waiting_approval"
        assert session.stats.current_state == "waiting_approval"


class TestSisterPollerInit:
    """Test SisterPoller initialization."""

    @patch("overcode.sister_poller.get_sisters_config", return_value=[])
    @patch("overcode.sister_poller.get_hostname", return_value="local-host")
    def test_no_sisters(self, mock_hostname, mock_config):
        poller = SisterPoller()
        assert poller.has_sisters is False
        assert poller.local_hostname == "local-host"
        assert poller.poll_all() == []

    @patch("overcode.sister_poller.get_sisters_config", return_value=[
        {"name": "remote1", "url": "http://localhost:15337"},
        {"name": "remote2", "url": "http://localhost:25337", "api_key": "secret"},
    ])
    @patch("overcode.sister_poller.get_hostname", return_value="local")
    def test_with_sisters(self, mock_hostname, mock_config):
        poller = SisterPoller()
        assert poller.has_sisters is True
        states = poller.get_sister_states()
        assert len(states) == 2
        assert states[0].name == "remote1"
        assert states[1].api_key == "secret"


class TestSisterPollerUnreachable:
    """Test unreachable sister handling."""

    @patch("overcode.sister_poller.get_sisters_config", return_value=[
        {"name": "down", "url": "http://localhost:99999"},
    ])
    @patch("overcode.sister_poller.get_hostname", return_value="local")
    def test_unreachable_sister_returns_empty(self, mock_hostname, mock_config):
        poller = SisterPoller()
        sessions = poller.poll_all()
        assert sessions == []
        states = poller.get_sister_states()
        assert states[0].reachable is False
        assert states[0].total_agents == 0


class TestSisterPollerWithServer:
    """Integration-style test with a real HTTP server."""

    @pytest.fixture(autouse=True)
    def setup_server(self):
        """Start a tiny HTTP server returning mock status data."""
        self.response_data = {
            "timestamp": "2026-02-16T12:00:00",
            "hostname": "test-host",
            "summary": {"green_agents": 1, "total_agents": 2},
            "agents": [
                {
                    "name": "agent-a",
                    "status": "running",
                    "cost_usd": 0.5,
                    "tokens_raw": 10000,
                    "activity": "coding",
                    "repo": "repo1",
                    "branch": "feat",
                    "green_time_raw": 100.0,
                    "non_green_time_raw": 20.0,
                    "robot_steers": 1,
                    "human_interactions": 2,
                },
                {
                    "name": "agent-b",
                    "status": "waiting_user",
                    "cost_usd": 0.3,
                    "tokens_raw": 5000,
                    "activity": "waiting",
                    "repo": "repo2",
                    "branch": "main",
                    "green_time_raw": 50.0,
                    "non_green_time_raw": 80.0,
                    "robot_steers": 0,
                    "human_interactions": 1,
                },
            ],
        }
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(outer.response_data).encode())

            def log_message(self, *args):
                pass  # Suppress logs

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        yield
        self.server.shutdown()

    @patch("overcode.sister_poller.get_hostname", return_value="local")
    def test_poll_returns_sessions(self, mock_hostname):
        with patch("overcode.sister_poller.get_sisters_config", return_value=[
            {"name": "test-sister", "url": f"http://127.0.0.1:{self.port}"},
        ]):
            poller = SisterPoller()
            sessions = poller.poll_all()

        assert len(sessions) == 2
        assert sessions[0].name == "agent-a"
        assert sessions[0].is_remote is True
        assert sessions[0].source_host == "test-host"  # From API hostname
        assert sessions[1].name == "agent-b"
        assert sessions[1].stats.current_state == "waiting_user"

        states = poller.get_sister_states()
        assert states[0].reachable is True
        assert states[0].green_agents == 1
        assert states[0].total_agents == 2
        assert states[0].total_cost == pytest.approx(0.8)

    @patch("overcode.sister_poller.get_hostname", return_value="local")
    def test_api_key_header_sent(self, mock_hostname):
        """Verify that API key is sent in X-API-Key header."""
        received_headers = {}
        outer = self

        class KeyCheckHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                received_headers["X-API-Key"] = self.headers.get("X-API-Key", "")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(outer.response_data).encode())

            def log_message(self, *args):
                pass

        key_server = HTTPServer(("127.0.0.1", 0), KeyCheckHandler)
        key_port = key_server.server_address[1]
        key_thread = Thread(target=key_server.serve_forever, daemon=True)
        key_thread.start()

        try:
            with patch("overcode.sister_poller.get_sisters_config", return_value=[
                {"name": "keyed", "url": f"http://127.0.0.1:{key_port}", "api_key": "my-secret"},
            ]):
                poller = SisterPoller()
                poller.poll_all()

            assert received_headers["X-API-Key"] == "my-secret"
        finally:
            key_server.shutdown()


class TestPollAllTimelines:
    """Test timeline polling from sisters."""

    @patch("overcode.sister_poller.get_sisters_config", return_value=[])
    @patch("overcode.sister_poller.get_hostname", return_value="local")
    def test_no_sisters_returns_empty(self, mock_hostname, mock_config):
        poller = SisterPoller()
        result = poller.poll_all_timelines(hours=3.0)
        assert result == {}

    @patch("overcode.sister_poller.get_sisters_config", return_value=[
        {"name": "down", "url": "http://localhost:99999"},
    ])
    @patch("overcode.sister_poller.get_hostname", return_value="local")
    def test_unreachable_sister_returns_empty(self, mock_hostname, mock_config):
        poller = SisterPoller()
        result = poller.poll_all_timelines(hours=3.0)
        assert result == {}


class TestPollAllTimelinesWithServer:
    """Integration-style test with a real HTTP server for timeline data."""

    @pytest.fixture(autouse=True)
    def setup_server(self):
        """Start a server returning mock raw timeline data."""
        self.timeline_data = {
            "hours": 3.0,
            "agents": {
                "agent-a": [
                    {"t": "2026-02-22T10:00:00", "s": "running"},
                    {"t": "2026-02-22T10:05:00", "s": "waiting_user"},
                ],
                "agent-b": [
                    {"t": "2026-02-22T10:10:00", "s": "running"},
                ],
            },
        }
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if "/api/timeline/raw" in self.path:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(outer.timeline_data).encode())
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        yield
        self.server.shutdown()

    @patch("overcode.sister_poller.get_hostname", return_value="local")
    def test_poll_returns_parsed_histories(self, mock_hostname):
        with patch("overcode.sister_poller.get_sisters_config", return_value=[
            {"name": "test-sister", "url": f"http://127.0.0.1:{self.port}"},
        ]):
            poller = SisterPoller()
            result = poller.poll_all_timelines(hours=3.0)

        assert "agent-a" in result
        assert "agent-b" in result
        assert len(result["agent-a"]) == 2
        assert len(result["agent-b"]) == 1
        # Check parsed datetime and status
        ts, status = result["agent-a"][0]
        assert isinstance(ts, datetime)
        assert status == "running"

    @patch("overcode.sister_poller.get_hostname", return_value="local")
    def test_404_returns_empty(self, mock_hostname):
        """Server returning 404 (old version) should gracefully return {}."""
        class Handler404(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(404)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler404)
        port = server.server_address[1]
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            with patch("overcode.sister_poller.get_sisters_config", return_value=[
                {"name": "old-sister", "url": f"http://127.0.0.1:{port}"},
            ]):
                poller = SisterPoller()
                result = poller.poll_all_timelines(hours=3.0)

            assert result == {}
        finally:
            server.shutdown()
