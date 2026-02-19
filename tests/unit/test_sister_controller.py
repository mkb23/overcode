"""Tests for sister_controller module."""

import json
import pytest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError, URLError
from io import BytesIO

from overcode.sister_controller import SisterController, ControlResult


class TestControlResult:
    """Tests for ControlResult dataclass."""

    def test_defaults(self):
        r = ControlResult(ok=True)
        assert r.ok is True
        assert r.data == {}
        assert r.error == ""

    def test_data_none_becomes_empty_dict(self):
        r = ControlResult(ok=True, data=None)
        assert r.data == {}

    def test_explicit_data_preserved(self):
        r = ControlResult(ok=True, data={"key": "val"})
        assert r.data == {"key": "val"}

    def test_error_message(self):
        r = ControlResult(ok=False, error="something broke")
        assert r.error == "something broke"


class TestSisterControllerRequest:
    """Tests for the core _request method."""

    def setup_method(self):
        self.ctrl = SisterController(timeout=5)
        self.url = "http://localhost:8080"
        self.key = "test-api-key"

    def test_successful_request(self):
        resp_data = json.dumps({"ok": True, "result": "done"}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = resp_data
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("overcode.sister_controller.urlopen", return_value=mock_resp) as mock_open:
            result = self.ctrl._request("POST", self.url, self.key, "/api/test", {"foo": "bar"})

        assert result.ok is True
        assert result.data["result"] == "done"
        # Verify request was constructed correctly
        call_args = mock_open.call_args
        req = call_args[0][0]
        assert req.full_url == "http://localhost:8080/api/test"
        assert req.get_header("X-api-key") == "test-api-key"
        assert req.get_header("Content-type") == "application/json"

    def test_successful_request_without_ok_field(self):
        """Response without 'ok' field defaults to True."""
        resp_data = json.dumps({"result": "done"}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = resp_data
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("overcode.sister_controller.urlopen", return_value=mock_resp):
            result = self.ctrl._request("GET", self.url, self.key, "/api/test")

        assert result.ok is True

    def test_http_error_with_json_body(self):
        error_body = json.dumps({"error": "not found"}).encode()
        http_error = HTTPError(
            url="http://localhost:8080/api/test",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=BytesIO(error_body),
        )

        with patch("overcode.sister_controller.urlopen", side_effect=http_error):
            result = self.ctrl._request("POST", self.url, self.key, "/api/test")

        assert result.ok is False
        assert result.error == "not found"

    def test_http_error_with_non_json_body(self):
        http_error = HTTPError(
            url="http://localhost:8080/api/test",
            code=500,
            msg="Internal Server Error",
            hdrs={},
            fp=BytesIO(b"not json"),
        )

        with patch("overcode.sister_controller.urlopen", side_effect=http_error):
            result = self.ctrl._request("POST", self.url, self.key, "/api/test")

        assert result.ok is False
        assert "HTTP 500" in result.error

    def test_url_error(self):
        with patch("overcode.sister_controller.urlopen", side_effect=URLError("refused")):
            result = self.ctrl._request("POST", self.url, self.key, "/api/test")

        assert result.ok is False
        assert "Connection error" in result.error

    def test_os_error(self):
        with patch("overcode.sister_controller.urlopen", side_effect=OSError("network down")):
            result = self.ctrl._request("POST", self.url, self.key, "/api/test")

        assert result.ok is False
        assert "Connection error" in result.error

    def test_trailing_slash_stripped_from_url(self):
        resp_data = json.dumps({"ok": True}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = resp_data
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("overcode.sister_controller.urlopen", return_value=mock_resp) as mock_open:
            self.ctrl._request("POST", "http://localhost:8080/", self.key, "/api/test")

        req = mock_open.call_args[0][0]
        assert req.full_url == "http://localhost:8080/api/test"

    def test_no_api_key_header_when_empty(self):
        resp_data = json.dumps({"ok": True}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = resp_data
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("overcode.sister_controller.urlopen", return_value=mock_resp) as mock_open:
            self.ctrl._request("POST", self.url, "", "/api/test")

        req = mock_open.call_args[0][0]
        assert req.get_header("X-api-key") is None


class TestSisterControllerMethods:
    """Tests for all the convenience methods on SisterController."""

    def setup_method(self):
        self.ctrl = SisterController()
        self.url = "http://localhost:8080"
        self.key = "test-key"

    def _mock_request(self, expected_method, expected_path, expected_body=None):
        """Helper to mock _request and verify call args."""
        result = ControlResult(ok=True, data={"ok": True})

        def side_effect(method, url, key, path, body=None):
            assert method == expected_method
            assert path == expected_path
            if expected_body is not None:
                assert body == expected_body
            return result

        return patch.object(self.ctrl, "_request", side_effect=side_effect)

    def test_send_instruction(self):
        with self._mock_request("POST", "/api/agents/agent1/send", {"text": "hello", "enter": True}):
            r = self.ctrl.send_instruction(self.url, self.key, "agent1", "hello", enter=True)
        assert r.ok

    def test_send_key(self):
        with self._mock_request("POST", "/api/agents/agent1/keys", {"key": "Enter"}):
            r = self.ctrl.send_key(self.url, self.key, "agent1", "Enter")
        assert r.ok

    def test_kill_agent(self):
        with self._mock_request("POST", "/api/agents/agent1/kill", {"cascade": True}):
            r = self.ctrl.kill_agent(self.url, self.key, "agent1", cascade=True)
        assert r.ok

    def test_restart_agent(self):
        with self._mock_request("POST", "/api/agents/agent1/restart"):
            r = self.ctrl.restart_agent(self.url, self.key, "agent1")
        assert r.ok

    def test_launch_agent(self):
        with self._mock_request("POST", "/api/agents/launch",
                                {"directory": "/tmp", "name": "a1", "prompt": "do stuff", "permissions": "normal"}):
            r = self.ctrl.launch_agent(self.url, self.key, "/tmp", "a1", prompt="do stuff")
        assert r.ok

    def test_set_standing_orders(self):
        with self._mock_request("PUT", "/api/agents/agent1/standing-orders", {"text": "work hard"}):
            r = self.ctrl.set_standing_orders(self.url, self.key, "agent1", text="work hard")
        assert r.ok

    def test_set_standing_orders_with_preset(self):
        with self._mock_request("PUT", "/api/agents/agent1/standing-orders", {"preset": "careful"}):
            r = self.ctrl.set_standing_orders(self.url, self.key, "agent1", preset="careful")
        assert r.ok

    def test_clear_standing_orders(self):
        with self._mock_request("DELETE", "/api/agents/agent1/standing-orders"):
            r = self.ctrl.clear_standing_orders(self.url, self.key, "agent1")
        assert r.ok

    def test_set_budget(self):
        with self._mock_request("PUT", "/api/agents/agent1/budget", {"usd": 5.0}):
            r = self.ctrl.set_budget(self.url, self.key, "agent1", 5.0)
        assert r.ok

    def test_set_value(self):
        with self._mock_request("PUT", "/api/agents/agent1/value", {"value": 3}):
            r = self.ctrl.set_value(self.url, self.key, "agent1", 3)
        assert r.ok

    def test_set_annotation(self):
        with self._mock_request("PUT", "/api/agents/agent1/annotation", {"text": "note"}):
            r = self.ctrl.set_annotation(self.url, self.key, "agent1", "note")
        assert r.ok

    def test_set_sleep(self):
        with self._mock_request("POST", "/api/agents/agent1/sleep", {"asleep": True}):
            r = self.ctrl.set_sleep(self.url, self.key, "agent1", True)
        assert r.ok

    def test_configure_heartbeat(self):
        with self._mock_request("PUT", "/api/agents/agent1/heartbeat",
                                {"enabled": True, "frequency": "5m", "instruction": "check"}):
            r = self.ctrl.configure_heartbeat(self.url, self.key, "agent1",
                                              enabled=True, frequency="5m", instruction="check")
        assert r.ok

    def test_configure_heartbeat_minimal(self):
        with self._mock_request("PUT", "/api/agents/agent1/heartbeat", {"enabled": False}):
            r = self.ctrl.configure_heartbeat(self.url, self.key, "agent1", enabled=False)
        assert r.ok

    def test_pause_heartbeat(self):
        with self._mock_request("POST", "/api/agents/agent1/heartbeat/pause"):
            r = self.ctrl.pause_heartbeat(self.url, self.key, "agent1")
        assert r.ok

    def test_resume_heartbeat(self):
        with self._mock_request("POST", "/api/agents/agent1/heartbeat/resume"):
            r = self.ctrl.resume_heartbeat(self.url, self.key, "agent1")
        assert r.ok

    def test_set_time_context(self):
        with self._mock_request("PUT", "/api/agents/agent1/time-context", {"enabled": True}):
            r = self.ctrl.set_time_context(self.url, self.key, "agent1", True)
        assert r.ok

    def test_set_hook_detection(self):
        with self._mock_request("PUT", "/api/agents/agent1/hook-detection", {"enabled": True}):
            r = self.ctrl.set_hook_detection(self.url, self.key, "agent1", True)
        assert r.ok

    def test_transport_all(self):
        with self._mock_request("POST", "/api/agents/transport"):
            r = self.ctrl.transport_all(self.url, self.key)
        assert r.ok

    def test_cleanup_agents(self):
        with self._mock_request("POST", "/api/agents/cleanup", {"include_done": True}):
            r = self.ctrl.cleanup_agents(self.url, self.key, include_done=True)
        assert r.ok

    def test_restart_monitor(self):
        with self._mock_request("POST", "/api/daemon/monitor/restart"):
            r = self.ctrl.restart_monitor(self.url, self.key)
        assert r.ok

    def test_start_supervisor(self):
        with self._mock_request("POST", "/api/daemon/supervisor/start"):
            r = self.ctrl.start_supervisor(self.url, self.key)
        assert r.ok

    def test_stop_supervisor(self):
        with self._mock_request("POST", "/api/daemon/supervisor/stop"):
            r = self.ctrl.stop_supervisor(self.url, self.key)
        assert r.ok
