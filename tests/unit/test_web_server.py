"""Tests for web_server module."""

import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from io import BytesIO

from overcode.web_server import (
    is_web_server_running,
    get_web_server_url,
    _find_available_port,
    stop_web_server,
    OvercodeHandler,
)


class TestIsWebServerRunning:
    """Tests for is_web_server_running function."""

    def test_returns_false_when_not_running(self, tmp_path, monkeypatch):
        """Should return False when server not running."""
        monkeypatch.setattr(
            'overcode.web_server.get_web_server_pid_path',
            lambda session: tmp_path / f"{session}_web.pid"
        )

        result = is_web_server_running("test_session")

        assert result is False

    def test_returns_true_when_running(self, tmp_path, monkeypatch):
        """Should return True when server is running."""
        pid_file = tmp_path / "test_session_web.pid"
        pid_file.write_text(str(os.getpid()))

        monkeypatch.setattr(
            'overcode.web_server.get_web_server_pid_path',
            lambda session: pid_file
        )

        result = is_web_server_running("test_session")

        assert result is True


class TestGetWebServerUrl:
    """Tests for get_web_server_url function."""

    def test_returns_none_when_not_running(self, tmp_path, monkeypatch):
        """Should return None when server not running."""
        monkeypatch.setattr(
            'overcode.web_server.get_web_server_pid_path',
            lambda session: tmp_path / f"{session}_web.pid"
        )

        result = get_web_server_url("test_session")

        assert result is None

    def test_returns_none_when_port_file_missing(self, tmp_path, monkeypatch):
        """Should return None when port file doesn't exist."""
        pid_file = tmp_path / "test_session_web.pid"
        pid_file.write_text(str(os.getpid()))

        monkeypatch.setattr(
            'overcode.web_server.get_web_server_pid_path',
            lambda session: pid_file
        )
        monkeypatch.setattr(
            'overcode.web_server.get_web_server_port_path',
            lambda session: tmp_path / f"{session}_web.port"
        )

        result = get_web_server_url("test_session")

        assert result is None

    def test_returns_url_when_running(self, tmp_path, monkeypatch):
        """Should return URL when server is running."""
        pid_file = tmp_path / "test_session_web.pid"
        pid_file.write_text(str(os.getpid()))

        port_file = tmp_path / "test_session_web.port"
        port_file.write_text("8080")

        monkeypatch.setattr(
            'overcode.web_server.get_web_server_pid_path',
            lambda session: pid_file
        )
        monkeypatch.setattr(
            'overcode.web_server.get_web_server_port_path',
            lambda session: port_file
        )

        result = get_web_server_url("test_session")

        assert result == "http://localhost:8080"

    def test_returns_none_for_invalid_port(self, tmp_path, monkeypatch):
        """Should return None when port file contains invalid data."""
        pid_file = tmp_path / "test_session_web.pid"
        pid_file.write_text(str(os.getpid()))

        port_file = tmp_path / "test_session_web.port"
        port_file.write_text("invalid")

        monkeypatch.setattr(
            'overcode.web_server.get_web_server_pid_path',
            lambda session: pid_file
        )
        monkeypatch.setattr(
            'overcode.web_server.get_web_server_port_path',
            lambda session: port_file
        )

        result = get_web_server_url("test_session")

        assert result is None


class TestFindAvailablePort:
    """Tests for _find_available_port function."""

    def test_finds_available_port(self):
        """Should find an available port."""
        # Use high port range unlikely to conflict
        port = _find_available_port(start_port=49152, max_attempts=10)

        assert port >= 49152
        assert port < 49162

    def test_raises_when_no_port_available(self):
        """Should raise RuntimeError when no port available."""
        with patch('socket.socket') as mock_socket:
            mock_socket_instance = MagicMock()
            mock_socket_instance.bind.side_effect = OSError("Address in use")
            mock_socket_instance.__enter__ = MagicMock(return_value=mock_socket_instance)
            mock_socket_instance.__exit__ = MagicMock(return_value=False)
            mock_socket.return_value = mock_socket_instance

            with pytest.raises(RuntimeError, match="Could not find available port"):
                _find_available_port(start_port=8080, max_attempts=3)


class TestStopWebServer:
    """Tests for stop_web_server function."""

    def test_returns_false_when_not_running(self, tmp_path, monkeypatch):
        """Should return False when server not running."""
        monkeypatch.setattr(
            'overcode.web_server.get_web_server_pid_path',
            lambda session: tmp_path / f"{session}_web.pid"
        )
        monkeypatch.setattr(
            'overcode.web_server.get_web_server_port_path',
            lambda session: tmp_path / f"{session}_web.port"
        )

        success, message = stop_web_server("test_session")

        assert success is False
        assert "not running" in message.lower()

    def test_stops_running_server(self, tmp_path, monkeypatch):
        """Should stop running server."""
        pid_file = tmp_path / "test_session_web.pid"
        pid_file.write_text("12345")

        port_file = tmp_path / "test_session_web.port"
        port_file.write_text("8080")

        monkeypatch.setattr(
            'overcode.web_server.get_web_server_pid_path',
            lambda session: pid_file
        )
        monkeypatch.setattr(
            'overcode.web_server.get_web_server_port_path',
            lambda session: port_file
        )

        with patch('overcode.web_server.is_process_running', return_value=True):
            with patch('overcode.web_server.stop_process') as mock_stop:
                mock_stop.return_value = True

                success, message = stop_web_server("test_session")

        assert success is True
        mock_stop.assert_called_once()


class TestOvercodeHandler:
    """Tests for OvercodeHandler class."""

    def test_parse_datetime_returns_none_for_none(self):
        """_parse_datetime should return None for None input."""
        handler = MagicMock(spec=OvercodeHandler)
        result = OvercodeHandler._parse_datetime(handler, None)
        assert result is None

    def test_parse_datetime_returns_none_for_invalid(self):
        """_parse_datetime should return None for invalid input."""
        handler = MagicMock(spec=OvercodeHandler)
        result = OvercodeHandler._parse_datetime(handler, "not-a-date")
        assert result is None

    def test_parse_datetime_parses_valid_iso(self):
        """_parse_datetime should parse valid ISO datetime."""
        from datetime import datetime
        handler = MagicMock(spec=OvercodeHandler)
        result = OvercodeHandler._parse_datetime(handler, "2024-01-15T10:30:00")
        assert result is not None
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15

    def test_log_message_suppresses_api_success(self):
        """Should suppress successful API poll logs."""
        handler = MagicMock(spec=OvercodeHandler)

        # Successful API poll should be suppressed
        with patch('sys.stderr') as mock_stderr:
            OvercodeHandler.log_message(handler, "%s %s", "GET /api/status", "200")
            mock_stderr.write.assert_not_called()

    def test_log_message_logs_errors(self):
        """Should log error responses."""
        handler = MagicMock(spec=OvercodeHandler)

        with patch('sys.stderr') as mock_stderr:
            OvercodeHandler.log_message(handler, "%s %s", "GET /api/status", "500")
            mock_stderr.write.assert_called()

    def test_log_message_logs_non_api_requests(self):
        """Should log non-API requests."""
        handler = MagicMock(spec=OvercodeHandler)

        with patch('sys.stderr') as mock_stderr:
            OvercodeHandler.log_message(handler, "%s %s", "GET /", "200")
            mock_stderr.write.assert_called()


class TestStartWebServer:
    """Tests for start_web_server function."""

    def test_returns_false_if_already_running(self, tmp_path, monkeypatch):
        """Should return False if server is already running."""
        from overcode.web_server import start_web_server

        pid_file = tmp_path / "test_session_web.pid"
        pid_file.write_text(str(os.getpid()))

        monkeypatch.setattr(
            'overcode.web_server.get_web_server_pid_path',
            lambda session: pid_file
        )
        monkeypatch.setattr(
            'overcode.web_server.get_web_server_port_path',
            lambda session: tmp_path / f"{session}_web.port"
        )

        success, message = start_web_server("test_session")

        assert success is False
        assert "Already running" in message

    def test_returns_false_when_no_port_available(self, tmp_path, monkeypatch):
        """Should return False when no port is available."""
        from overcode.web_server import start_web_server

        def raise_no_port(start_port=8080, max_attempts=10):
            raise RuntimeError("No port available")

        monkeypatch.setattr(
            'overcode.web_server.get_web_server_pid_path',
            lambda session: tmp_path / f"{session}_web.pid"
        )
        monkeypatch.setattr(
            'overcode.web_server.get_web_server_port_path',
            lambda session: tmp_path / f"{session}_web.port"
        )
        monkeypatch.setattr(
            'overcode.web_server._find_available_port',
            raise_no_port
        )
        monkeypatch.setattr(
            'overcode.web_server.ensure_session_dir',
            lambda session: None
        )

        success, message = start_web_server("test_session")

        assert success is False
        assert "No port" in message


class TestToggleWebServer:
    """Tests for toggle_web_server function."""

    def test_stops_running_server(self, tmp_path, monkeypatch):
        """Should stop server if running."""
        from overcode.web_server import toggle_web_server

        pid_file = tmp_path / "test_session_web.pid"
        pid_file.write_text("12345")

        port_file = tmp_path / "test_session_web.port"
        port_file.write_text("8080")

        monkeypatch.setattr(
            'overcode.web_server.get_web_server_pid_path',
            lambda session: pid_file
        )
        monkeypatch.setattr(
            'overcode.web_server.get_web_server_port_path',
            lambda session: port_file
        )

        with patch('overcode.web_server.is_process_running', return_value=True):
            with patch('overcode.web_server.stop_process') as mock_stop:
                mock_stop.return_value = True

                is_running, message = toggle_web_server("test_session")

        assert is_running is False
        assert message == "Stopped"

    def test_starts_server_if_not_running(self, tmp_path, monkeypatch):
        """Should attempt to start server if not running."""
        from overcode.web_server import toggle_web_server

        monkeypatch.setattr(
            'overcode.web_server.get_web_server_pid_path',
            lambda session: tmp_path / f"{session}_web.pid"
        )
        monkeypatch.setattr(
            'overcode.web_server.get_web_server_port_path',
            lambda session: tmp_path / f"{session}_web.port"
        )

        with patch('overcode.web_server.is_web_server_running', return_value=False):
            with patch('overcode.web_server.start_web_server') as mock_start:
                mock_start.return_value = (True, "Started at http://localhost:8080")

                is_running, message = toggle_web_server("test_session")

        assert is_running is True
        assert "Started" in message


class TestOvercodeHandlerRoutes:
    """Tests for OvercodeHandler route handling."""

    def test_do_GET_returns_404_for_unknown(self):
        """do_GET should return 404 for unknown routes."""
        handler = MagicMock(spec=OvercodeHandler)
        handler.path = "/unknown/route"
        handler._parse_datetime = MagicMock(return_value=None)

        OvercodeHandler.do_GET(handler)
        handler.send_error.assert_called_once_with(404, "Not Found")

    def test_root_route_matches_analytics(self):
        """Root path should match analytics route logic."""
        from urllib.parse import urlparse
        parsed = urlparse("/")
        assert parsed.path == "/" or parsed.path == "/index.html"

    def test_api_status_route_parsing(self):
        """API status path should be parsed correctly."""
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse("/api/status?foo=bar")
        assert parsed.path == "/api/status"
        assert parse_qs(parsed.query) == {"foo": ["bar"]}

    def test_api_timeline_route_parsing(self):
        """API timeline path with query params should be parsed correctly."""
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse("/api/timeline?hours=6&slots=30")
        assert parsed.path == "/api/timeline"
        query = parse_qs(parsed.query)
        assert float(query.get("hours", [3.0])[0]) == 6.0
        assert int(query.get("slots", [60])[0]) == 30


class TestOvercodeHandlerRouteDispatching:
    """Tests that OvercodeHandler.do_GET dispatches to correct methods."""

    def test_root_calls_serve_analytics_dashboard(self):
        """GET / should call _serve_analytics_dashboard."""
        handler = MagicMock(spec=OvercodeHandler)
        handler.path = "/"
        handler._parse_datetime = MagicMock(return_value=None)

        OvercodeHandler.do_GET(handler)

        handler._serve_analytics_dashboard.assert_called_once()

    def test_index_html_calls_serve_analytics_dashboard(self):
        """GET /index.html should call _serve_analytics_dashboard."""
        handler = MagicMock(spec=OvercodeHandler)
        handler.path = "/index.html"
        handler._parse_datetime = MagicMock(return_value=None)

        OvercodeHandler.do_GET(handler)

        handler._serve_analytics_dashboard.assert_called_once()

    def test_dashboard_calls_serve_dashboard(self):
        """GET /dashboard should call _serve_dashboard."""
        handler = MagicMock(spec=OvercodeHandler)
        handler.path = "/dashboard"
        handler._parse_datetime = MagicMock(return_value=None)

        OvercodeHandler.do_GET(handler)

        handler._serve_dashboard.assert_called_once()

    def test_api_status_calls_serve_json(self):
        """GET /api/status should call _serve_json with get_status_data result."""
        handler = MagicMock(spec=OvercodeHandler)
        handler.path = "/api/status"
        handler.tmux_session = "test-session"
        handler._parse_datetime = MagicMock(return_value=None)

        with patch('overcode.web_server.get_status_data') as mock_get_status:
            mock_get_status.return_value = {"status": "ok"}

            OvercodeHandler.do_GET(handler)

            mock_get_status.assert_called_once_with("test-session")
            handler._serve_json.assert_called_once_with({"status": "ok"})

    def test_api_timeline_calls_serve_json_with_defaults(self):
        """GET /api/timeline should call _serve_json with get_timeline_data."""
        handler = MagicMock(spec=OvercodeHandler)
        handler.path = "/api/timeline"
        handler.tmux_session = "test-session"
        handler._parse_datetime = MagicMock(return_value=None)

        with patch('overcode.web_server.get_timeline_data') as mock_get_timeline:
            mock_get_timeline.return_value = {"timeline": "data"}

            OvercodeHandler.do_GET(handler)

            mock_get_timeline.assert_called_once_with("test-session", hours=3.0, slots=60)
            handler._serve_json.assert_called_once_with({"timeline": "data"})

    def test_api_timeline_passes_query_params(self):
        """GET /api/timeline?hours=6&slots=30 should pass parsed params."""
        handler = MagicMock(spec=OvercodeHandler)
        handler.path = "/api/timeline?hours=6&slots=30"
        handler.tmux_session = "test-session"
        handler._parse_datetime = MagicMock(return_value=None)

        with patch('overcode.web_server.get_timeline_data') as mock_get_timeline:
            mock_get_timeline.return_value = {"timeline": "data"}

            OvercodeHandler.do_GET(handler)

            mock_get_timeline.assert_called_once_with("test-session", hours=6.0, slots=30)

    def test_health_calls_serve_json(self):
        """GET /health should call _serve_json with get_health_data result."""
        handler = MagicMock(spec=OvercodeHandler)
        handler.path = "/health"
        handler._parse_datetime = MagicMock(return_value=None)

        with patch('overcode.web_server.get_health_data') as mock_health:
            mock_health.return_value = {"status": "ok"}

            OvercodeHandler.do_GET(handler)

            mock_health.assert_called_once()
            handler._serve_json.assert_called_once_with({"status": "ok"})

    def test_unknown_route_returns_404(self):
        """GET /unknown should call send_error(404)."""
        handler = MagicMock(spec=OvercodeHandler)
        handler.path = "/nonexistent"
        handler._parse_datetime = MagicMock(return_value=None)

        OvercodeHandler.do_GET(handler)

        handler.send_error.assert_called_once_with(404, "Not Found")

    def test_chartjs_route_calls_serve_chartjs(self):
        """GET /static/chart.min.js should call _serve_chartjs."""
        handler = MagicMock(spec=OvercodeHandler)
        handler.path = "/static/chart.min.js"
        handler._parse_datetime = MagicMock(return_value=None)

        OvercodeHandler.do_GET(handler)

        handler._serve_chartjs.assert_called_once()

    def test_sessions_route_calls_get_analytics_sessions(self):
        """GET /api/analytics/sessions should call get_analytics_sessions."""
        handler = MagicMock(spec=OvercodeHandler)
        handler.path = "/api/analytics/sessions"
        handler._parse_datetime = MagicMock(return_value=None)

        with patch('overcode.web_server.get_analytics_sessions') as mock_fn:
            mock_fn.return_value = {"sessions": []}

            OvercodeHandler.do_GET(handler)

            mock_fn.assert_called_once_with(None, None)
            handler._serve_json.assert_called_once_with({"sessions": []})

    def test_analytics_timeline_route_calls_get_analytics_timeline(self):
        """GET /api/analytics/timeline should call get_analytics_timeline."""
        handler = MagicMock(spec=OvercodeHandler)
        handler.path = "/api/analytics/timeline"
        handler.tmux_session = "test-session"
        handler._parse_datetime = MagicMock(return_value=None)

        with patch('overcode.web_server.get_analytics_timeline') as mock_fn:
            mock_fn.return_value = {"agents": {}}

            OvercodeHandler.do_GET(handler)

            mock_fn.assert_called_once_with("test-session", None, None)
            handler._serve_json.assert_called_once()

    def test_stats_route_calls_get_analytics_stats(self):
        """GET /api/analytics/stats should call get_analytics_stats."""
        handler = MagicMock(spec=OvercodeHandler)
        handler.path = "/api/analytics/stats"
        handler.tmux_session = "test-session"
        handler._parse_datetime = MagicMock(return_value=None)

        with patch('overcode.web_server.get_analytics_stats') as mock_fn:
            mock_fn.return_value = {"stats": {}}

            OvercodeHandler.do_GET(handler)

            mock_fn.assert_called_once_with("test-session", None, None)
            handler._serve_json.assert_called_once()

    def test_daily_route_calls_get_analytics_daily(self):
        """GET /api/analytics/daily should call get_analytics_daily."""
        handler = MagicMock(spec=OvercodeHandler)
        handler.path = "/api/analytics/daily"
        handler._parse_datetime = MagicMock(return_value=None)

        with patch('overcode.web_server.get_analytics_daily') as mock_fn:
            mock_fn.return_value = {"days": []}

            OvercodeHandler.do_GET(handler)

            mock_fn.assert_called_once_with(None, None)
            handler._serve_json.assert_called_once()

    def test_presets_route_calls_get_time_presets(self):
        """GET /api/analytics/presets should call get_time_presets."""
        handler = MagicMock(spec=OvercodeHandler)
        handler.path = "/api/analytics/presets"
        handler._parse_datetime = MagicMock(return_value=None)

        with patch('overcode.web_server.get_time_presets') as mock_fn:
            mock_fn.return_value = [{"label": "24h"}]

            OvercodeHandler.do_GET(handler)

            mock_fn.assert_called_once()
            handler._serve_json.assert_called_once_with([{"label": "24h"}])

    def test_sessions_route_with_time_params(self):
        """GET /api/analytics/sessions with start/end should parse datetime params."""
        from datetime import datetime

        handler = MagicMock(spec=OvercodeHandler)
        handler.path = "/api/analytics/sessions?start=2024-01-01T00:00:00&end=2024-01-31T23:59:59"

        start_dt = datetime(2024, 1, 1)
        end_dt = datetime(2024, 1, 31, 23, 59, 59)
        handler._parse_datetime = MagicMock(side_effect=lambda v: {
            "2024-01-01T00:00:00": start_dt,
            "2024-01-31T23:59:59": end_dt,
        }.get(v))

        with patch('overcode.web_server.get_analytics_sessions') as mock_fn:
            mock_fn.return_value = {"sessions": []}

            OvercodeHandler.do_GET(handler)

            mock_fn.assert_called_once_with(start_dt, end_dt)


class TestLogToFile:
    """Tests for _log_to_file function."""

    def test_writes_message_to_log_file(self, tmp_path, monkeypatch):
        """Should write a timestamped message to web_server.log."""
        from overcode.web_server import _log_to_file

        monkeypatch.setattr(
            'overcode.settings.get_session_dir',
            lambda session: tmp_path / session
        )

        _log_to_file("test-session", "Hello from test")

        log_path = tmp_path / "test-session" / "web_server.log"
        assert log_path.exists()
        content = log_path.read_text()
        assert "Hello from test" in content
        assert "[start_web_server]" in content

    def test_appends_to_existing_log(self, tmp_path, monkeypatch):
        """Should append to existing log file, not overwrite."""
        from overcode.web_server import _log_to_file

        monkeypatch.setattr(
            'overcode.settings.get_session_dir',
            lambda session: tmp_path / session
        )

        _log_to_file("test-session", "First message")
        _log_to_file("test-session", "Second message")

        log_path = tmp_path / "test-session" / "web_server.log"
        content = log_path.read_text()
        assert "First message" in content
        assert "Second message" in content

    def test_creates_parent_directories(self, tmp_path, monkeypatch):
        """Should create parent dirs if they don't exist."""
        from overcode.web_server import _log_to_file

        deep_path = tmp_path / "deep" / "nested"
        monkeypatch.setattr(
            'overcode.settings.get_session_dir',
            lambda session: deep_path / session
        )

        _log_to_file("test-session", "Deep message")

        log_path = deep_path / "test-session" / "web_server.log"
        assert log_path.exists()

    def test_silently_handles_errors(self, tmp_path, monkeypatch):
        """Should not raise on write errors (passes silently)."""
        from overcode.web_server import _log_to_file

        # Point to a path that will cause an error (read-only dir)
        monkeypatch.setattr(
            'overcode.settings.get_session_dir',
            lambda session: None  # This will cause an AttributeError
        )

        # Should not raise
        _log_to_file("test-session", "Should not crash")


class TestToggleWebServerStopFailure:
    """Tests for toggle_web_server when stop fails."""

    def test_returns_false_when_stop_fails(self, tmp_path, monkeypatch):
        """Should return (False, 'Failed to stop') when stop_process fails."""
        from overcode.web_server import toggle_web_server

        pid_file = tmp_path / "test_session_web.pid"
        pid_file.write_text("12345")

        port_file = tmp_path / "test_session_web.port"
        port_file.write_text("8080")

        monkeypatch.setattr(
            'overcode.web_server.get_web_server_pid_path',
            lambda session: pid_file
        )
        monkeypatch.setattr(
            'overcode.web_server.get_web_server_port_path',
            lambda session: port_file
        )

        with patch('overcode.web_server.is_process_running', return_value=True):
            with patch('overcode.web_server.stop_process') as mock_stop:
                mock_stop.return_value = False  # stop fails

                is_running, message = toggle_web_server("test_session")

        # toggle_web_server returns (False, msg) regardless of stop success
        assert is_running is False
        assert "Failed to stop" in message


# =============================================================================
# New expanded tests for improved coverage
# =============================================================================


def _make_handler():
    """Create a mock OvercodeHandler with wfile and headers for method testing."""
    handler = MagicMock(spec=OvercodeHandler)
    handler.wfile = MagicMock()
    handler.tmux_session = "test-session"
    handler.headers = {}
    handler.rfile = BytesIO()
    return handler


class TestServeJson:
    """Tests for _serve_json — JSON serialization and response writing."""

    def test_writes_json_body(self):
        """Should serialize data as JSON and write to wfile."""
        handler = _make_handler()
        data = {"agents": [{"name": "a1", "status": "running"}]}

        OvercodeHandler._serve_json(handler, data)

        handler.send_response.assert_called_once_with(200)
        handler.send_header.assert_any_call("Content-Type", "application/json")
        handler.send_header.assert_any_call("Access-Control-Allow-Origin", "*")
        handler.send_header.assert_any_call("Cache-Control", "no-cache")
        handler.end_headers.assert_called_once()
        handler.wfile.write.assert_called_once()
        written = handler.wfile.write.call_args[0][0]
        import json
        parsed = json.loads(written.decode("utf-8"))
        assert parsed == data

    def test_handles_non_serializable_with_default_str(self):
        """Should use default=str for non-serializable types like datetime."""
        from datetime import datetime
        handler = _make_handler()
        data = {"timestamp": datetime(2024, 6, 15, 12, 0, 0)}

        OvercodeHandler._serve_json(handler, data)

        handler.send_response.assert_called_once_with(200)
        handler.wfile.write.assert_called_once()
        written = handler.wfile.write.call_args[0][0]
        assert b"2024-06-15" in written

    def test_handles_exception_with_500(self):
        """Should send 500 error when serialization fails."""
        handler = _make_handler()
        handler.send_response.side_effect = Exception("write failed")

        OvercodeHandler._serve_json(handler, {"ok": True})

        handler.send_error.assert_called_once()
        assert handler.send_error.call_args[0][0] == 500

    def test_content_length_header_matches_body(self):
        """Content-Length header should match actual body byte length."""
        handler = _make_handler()
        data = {"key": "value"}

        OvercodeHandler._serve_json(handler, data)

        written = handler.wfile.write.call_args[0][0]
        # Find the Content-Length header call
        content_length_calls = [
            call for call in handler.send_header.call_args_list
            if call[0][0] == "Content-Length"
        ]
        assert len(content_length_calls) == 1
        assert content_length_calls[0][0][1] == str(len(written))


class TestServeDashboard:
    """Tests for _serve_dashboard — live monitoring HTML page."""

    def test_serves_html_with_correct_headers(self):
        """Should serve HTML with correct content type."""
        handler = _make_handler()

        with patch('overcode.web_server.get_dashboard_html', return_value="<html>dashboard</html>"):
            OvercodeHandler._serve_dashboard(handler)

        handler.send_response.assert_called_once_with(200)
        handler.send_header.assert_any_call("Content-Type", "text/html; charset=utf-8")
        handler.send_header.assert_any_call("Cache-Control", "no-cache")
        handler.end_headers.assert_called_once()
        handler.wfile.write.assert_called_once()

    def test_handles_template_error_with_500(self):
        """Should send 500 if template generation fails."""
        handler = _make_handler()

        with patch('overcode.web_server.get_dashboard_html', side_effect=RuntimeError("template error")):
            OvercodeHandler._serve_dashboard(handler)

        handler.send_error.assert_called_once()
        assert handler.send_error.call_args[0][0] == 500


class TestServeAnalyticsDashboard:
    """Tests for _serve_analytics_dashboard."""

    def test_serves_analytics_html(self):
        """Should serve analytics HTML."""
        handler = _make_handler()

        with patch('overcode.web_server.get_analytics_html', return_value="<html>analytics</html>"):
            OvercodeHandler._serve_analytics_dashboard(handler)

        handler.send_response.assert_called_once_with(200)
        handler.send_header.assert_any_call("Content-Type", "text/html; charset=utf-8")
        handler.wfile.write.assert_called_once()

    def test_handles_error_with_500(self):
        """Should send 500 on error."""
        handler = _make_handler()

        with patch('overcode.web_server.get_analytics_html', side_effect=RuntimeError("fail")):
            OvercodeHandler._serve_analytics_dashboard(handler)

        handler.send_error.assert_called_once()
        assert handler.send_error.call_args[0][0] == 500


class TestServeChartjs:
    """Tests for _serve_chartjs — embedded Chart.js library."""

    def test_serves_javascript_with_cache_headers(self):
        """Should serve JS with long cache and correct content type."""
        handler = _make_handler()

        with patch.dict('sys.modules', {'overcode.web_chartjs': MagicMock(CHARTJS_JS="var Chart = {};")}):
            OvercodeHandler._serve_chartjs(handler)

        handler.send_response.assert_called_once_with(200)
        handler.send_header.assert_any_call("Content-Type", "application/javascript")
        handler.send_header.assert_any_call("Cache-Control", "public, max-age=31536000")
        handler.end_headers.assert_called_once()

    def test_handles_import_error_with_500(self):
        """Should send 500 if chart.js module not available."""
        handler = _make_handler()
        # Force the import to fail by patching the internal import
        with patch.object(OvercodeHandler, '_serve_chartjs', wraps=lambda self: None):
            pass  # Can't easily test internal import failures
        # Instead, test the exception path directly
        handler2 = _make_handler()
        handler2.send_response.side_effect = Exception("import failed")
        OvercodeHandler._serve_chartjs(handler2)
        handler2.send_error.assert_called_once()


class TestDoOptions:
    """Tests for do_OPTIONS — CORS preflight requests."""

    def test_returns_204_with_cors_headers(self):
        """Should return 204 No Content with CORS headers."""
        handler = _make_handler()

        OvercodeHandler.do_OPTIONS(handler)

        handler.send_response.assert_called_once_with(204)
        handler.send_header.assert_any_call("Access-Control-Allow-Origin", "*")
        handler.send_header.assert_any_call(
            "Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS"
        )
        handler.send_header.assert_any_call(
            "Access-Control-Allow-Headers", "Content-Type, X-API-Key"
        )
        handler.send_header.assert_any_call("Access-Control-Max-Age", "86400")
        handler.end_headers.assert_called_once()


class TestCheckAuth:
    """Tests for _check_auth — API key validation."""

    def test_returns_true_when_no_api_key_configured(self):
        """Should pass auth when no API key is set."""
        handler = _make_handler()

        with patch('overcode.web_server.get_web_api_key', return_value=None):
            result = OvercodeHandler._check_auth(handler)

        assert result is True

    def test_returns_true_when_key_matches(self):
        """Should pass auth when request key matches configured key."""
        handler = _make_handler()
        handler.headers = {"X-API-Key": "secret-key-123"}

        with patch('overcode.web_server.get_web_api_key', return_value="secret-key-123"):
            result = OvercodeHandler._check_auth(handler)

        assert result is True

    def test_returns_false_when_key_missing(self):
        """Should fail auth when request has no API key."""
        handler = _make_handler()
        handler.headers = {}

        with patch('overcode.web_server.get_web_api_key', return_value="secret-key-123"):
            result = OvercodeHandler._check_auth(handler)

        assert result is False
        handler._send_json_error.assert_called_once_with(401, "Unauthorized: invalid or missing X-API-Key header")

    def test_returns_false_when_key_wrong(self):
        """Should fail auth when request key does not match."""
        handler = _make_handler()
        handler.headers = {"X-API-Key": "wrong-key"}

        with patch('overcode.web_server.get_web_api_key', return_value="secret-key-123"):
            result = OvercodeHandler._check_auth(handler)

        assert result is False


class TestCheckControlAllowed:
    """Tests for _check_control_allowed — remote control gate."""

    def test_returns_true_when_control_enabled(self):
        """Should return True when control is allowed."""
        handler = _make_handler()

        with patch('overcode.web_server.get_web_allow_control', return_value=True):
            result = OvercodeHandler._check_control_allowed(handler)

        assert result is True

    def test_returns_false_when_control_disabled(self):
        """Should return False and send 403 when control is not allowed."""
        handler = _make_handler()

        with patch('overcode.web_server.get_web_allow_control', return_value=False):
            result = OvercodeHandler._check_control_allowed(handler)

        assert result is False
        handler._send_json_error.assert_called_once()
        assert handler._send_json_error.call_args[0][0] == 403


class TestReadJsonBody:
    """Tests for _read_json_body — request body parsing."""

    def test_returns_empty_dict_when_no_content(self):
        """Should return empty dict when Content-Length is 0."""
        handler = _make_handler()
        handler.headers = {"Content-Length": "0"}

        result = OvercodeHandler._read_json_body(handler)

        assert result == {}

    def test_returns_empty_dict_when_no_content_length_header(self):
        """Should return empty dict when Content-Length header is missing."""
        handler = _make_handler()
        handler.headers = {}

        result = OvercodeHandler._read_json_body(handler)

        assert result == {}

    def test_parses_valid_json_body(self):
        """Should parse valid JSON from request body."""
        handler = _make_handler()
        body = b'{"text": "hello", "enter": true}'
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = BytesIO(body)

        result = OvercodeHandler._read_json_body(handler)

        assert result == {"text": "hello", "enter": True}

    def test_returns_none_for_invalid_json(self):
        """Should return None and send 400 for malformed JSON."""
        handler = _make_handler()
        body = b'not valid json {{'
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = BytesIO(body)

        result = OvercodeHandler._read_json_body(handler)

        assert result is None
        handler._send_json_error.assert_called_once()
        assert handler._send_json_error.call_args[0][0] == 400


class TestSendJsonResponse:
    """Tests for _send_json_response and _send_json_error."""

    def test_send_json_response_default_200(self):
        """Should send JSON response with status 200 by default."""
        handler = _make_handler()

        OvercodeHandler._send_json_response(handler, {"ok": True})

        handler.send_response.assert_called_once_with(200)
        handler.send_header.assert_any_call("Content-Type", "application/json")
        handler.send_header.assert_any_call("Access-Control-Allow-Origin", "*")
        handler.wfile.write.assert_called_once()

    def test_send_json_response_custom_status(self):
        """Should send JSON response with custom status code."""
        handler = _make_handler()

        OvercodeHandler._send_json_response(handler, {"created": True}, status=201)

        handler.send_response.assert_called_once_with(201)

    def test_send_json_error_wraps_in_error_format(self):
        """Should wrap error message in {ok: false, error: msg} format."""
        handler = _make_handler()

        OvercodeHandler._send_json_error(handler, 404, "Agent not found")

        handler._send_json_response.assert_called_once_with(
            {"ok": False, "error": "Agent not found"}, status=404
        )


class TestDoGETWithApiKeyAuth:
    """Tests for API key authentication on GET requests."""

    def test_rejects_request_with_wrong_api_key(self):
        """GET with wrong API key should return 401."""
        handler = _make_handler()
        handler.path = "/api/status"
        handler.headers = {"X-API-Key": "wrong-key"}

        with patch('overcode.web_server.get_web_api_key', return_value="correct-key"):
            OvercodeHandler.do_GET(handler)

        handler.send_error.assert_called_once_with(
            401, "Unauthorized: invalid or missing X-API-Key header"
        )

    def test_rejects_request_with_missing_api_key(self):
        """GET with no API key should return 401 when key is configured."""
        handler = _make_handler()
        handler.path = "/api/status"
        handler.headers = {}

        with patch('overcode.web_server.get_web_api_key', return_value="my-secret"):
            OvercodeHandler.do_GET(handler)

        handler.send_error.assert_called_once()
        assert handler.send_error.call_args[0][0] == 401

    def test_allows_request_with_correct_api_key(self):
        """GET with correct API key should proceed to route handling."""
        handler = _make_handler()
        handler.path = "/health"
        handler.headers = {"X-API-Key": "my-secret"}
        handler._parse_datetime = MagicMock(return_value=None)

        with patch('overcode.web_server.get_web_api_key', return_value="my-secret"):
            with patch('overcode.web_server.get_health_data', return_value={"ok": True}):
                OvercodeHandler.do_GET(handler)

        handler.send_error.assert_not_called()
        handler._serve_json.assert_called_once()

    def test_allows_request_when_no_api_key_configured(self):
        """GET should proceed when no API key is configured."""
        handler = _make_handler()
        handler.path = "/health"
        handler.headers = {}
        handler._parse_datetime = MagicMock(return_value=None)

        with patch('overcode.web_server.get_web_api_key', return_value=None):
            with patch('overcode.web_server.get_health_data', return_value={"ok": True}):
                OvercodeHandler.do_GET(handler)

        handler.send_error.assert_not_called()
        handler._serve_json.assert_called_once()


class TestAgentStatusRoute:
    """Tests for /api/agents/<name>/status route."""

    def test_serves_agent_data_when_found(self):
        """Should serve agent data when agent exists."""
        handler = _make_handler()
        handler.path = "/api/agents/my-agent/status"
        handler._parse_datetime = MagicMock(return_value=None)

        with patch('overcode.web_server.get_web_api_key', return_value=None):
            with patch('overcode.web_server.get_single_agent_status') as mock_fn:
                mock_fn.return_value = {"name": "my-agent", "status": "running"}
                OvercodeHandler.do_GET(handler)

                mock_fn.assert_called_once_with("test-session", "my-agent")
                handler._serve_json.assert_called_once_with({"name": "my-agent", "status": "running"})

    def test_returns_404_when_agent_not_found(self):
        """Should return 404 when agent does not exist."""
        handler = _make_handler()
        handler.path = "/api/agents/nonexistent/status"
        handler._parse_datetime = MagicMock(return_value=None)

        with patch('overcode.web_server.get_web_api_key', return_value=None):
            with patch('overcode.web_server.get_single_agent_status', return_value=None):
                OvercodeHandler.do_GET(handler)

        handler.send_error.assert_called_once_with(404, "Agent 'nonexistent' not found")


class TestDoPostPutDelete:
    """Tests for do_POST, do_PUT, do_DELETE dispatching."""

    def test_do_post_calls_route_control(self):
        """do_POST should delegate to _route_control with 'POST'."""
        handler = _make_handler()

        OvercodeHandler.do_POST(handler)

        handler._route_control.assert_called_once_with("POST")

    def test_do_put_calls_route_control(self):
        """do_PUT should delegate to _route_control with 'PUT'."""
        handler = _make_handler()

        OvercodeHandler.do_PUT(handler)

        handler._route_control.assert_called_once_with("PUT")

    def test_do_delete_calls_route_control(self):
        """do_DELETE should delegate to _route_control with 'DELETE'."""
        handler = _make_handler()

        OvercodeHandler.do_DELETE(handler)

        handler._route_control.assert_called_once_with("DELETE")


class TestRouteControl:
    """Tests for _route_control — the main control API dispatcher."""

    def test_rejects_unauthorized_request(self):
        """Should return early if auth fails."""
        handler = _make_handler()
        handler._check_auth = MagicMock(return_value=False)

        OvercodeHandler._route_control(handler, "POST")

        handler._check_auth.assert_called_once()
        handler._check_control_allowed.assert_not_called()

    def test_rejects_when_control_not_allowed(self):
        """Should return early if control is not allowed."""
        handler = _make_handler()
        handler._check_auth = MagicMock(return_value=True)
        handler._check_control_allowed = MagicMock(return_value=False)

        OvercodeHandler._route_control(handler, "POST")

        handler._check_control_allowed.assert_called_once()
        handler._read_json_body.assert_not_called()

    def test_rejects_invalid_body(self):
        """Should return early if JSON body parsing fails."""
        handler = _make_handler()
        handler.path = "/api/agents/test/send"
        handler._check_auth = MagicMock(return_value=True)
        handler._check_control_allowed = MagicMock(return_value=True)
        handler._read_json_body = MagicMock(return_value=None)

        OvercodeHandler._route_control(handler, "POST")

        handler._dispatch_control.assert_not_called()

    def test_dispatches_on_success(self):
        """Should dispatch and send response on success."""
        handler = _make_handler()
        handler.path = "/api/agents/transport"
        handler._check_auth = MagicMock(return_value=True)
        handler._check_control_allowed = MagicMock(return_value=True)
        handler._read_json_body = MagicMock(return_value={})
        handler._dispatch_control = MagicMock(return_value={"ok": True})

        OvercodeHandler._route_control(handler, "POST")

        handler._dispatch_control.assert_called_once_with("POST", "/api/agents/transport", {})
        handler._send_json_response.assert_called_once_with({"ok": True})

    def test_handles_control_error(self):
        """Should send JSON error when ControlError is raised."""
        from overcode.web_control_api import ControlError

        handler = _make_handler()
        handler.path = "/api/agents/test/send"
        handler._check_auth = MagicMock(return_value=True)
        handler._check_control_allowed = MagicMock(return_value=True)
        handler._read_json_body = MagicMock(return_value={"text": "hello"})
        handler._dispatch_control = MagicMock(
            side_effect=ControlError("Agent not found", status=404)
        )

        OvercodeHandler._route_control(handler, "POST")

        handler._send_json_error.assert_called_once_with(404, "Agent not found")

    def test_handles_unexpected_exception(self):
        """Should send 500 for unexpected exceptions."""
        handler = _make_handler()
        handler.path = "/api/agents/test/send"
        handler._check_auth = MagicMock(return_value=True)
        handler._check_control_allowed = MagicMock(return_value=True)
        handler._read_json_body = MagicMock(return_value={})
        handler._dispatch_control = MagicMock(side_effect=RuntimeError("boom"))

        OvercodeHandler._route_control(handler, "POST")

        handler._send_json_error.assert_called_once()
        assert handler._send_json_error.call_args[0][0] == 500


class TestDispatchControl:
    """Tests for _dispatch_control — individual route dispatch."""

    def test_post_send_to_agent(self):
        """POST /api/agents/<name>/send dispatches correctly."""
        handler = _make_handler()

        with patch('overcode.web_control_api.send_to_agent') as mock_fn:
            mock_fn.return_value = {"ok": True}
            result = OvercodeHandler._dispatch_control(
                handler, "POST", "/api/agents/agent1/send",
                {"text": "do stuff", "enter": True}
            )

        mock_fn.assert_called_once_with("test-session", "agent1", text="do stuff", enter=True)
        assert result == {"ok": True}

    def test_post_send_keys_to_agent(self):
        """POST /api/agents/<name>/keys dispatches correctly."""
        handler = _make_handler()

        with patch('overcode.web_control_api.send_key_to_agent') as mock_fn:
            mock_fn.return_value = {"ok": True}
            result = OvercodeHandler._dispatch_control(
                handler, "POST", "/api/agents/agent1/keys",
                {"key": "Enter"}
            )

        mock_fn.assert_called_once_with("test-session", "agent1", key="Enter")

    def test_post_kill_agent(self):
        """POST /api/agents/<name>/kill dispatches correctly."""
        handler = _make_handler()

        with patch('overcode.web_control_api.kill_agent') as mock_fn:
            mock_fn.return_value = {"ok": True}
            result = OvercodeHandler._dispatch_control(
                handler, "POST", "/api/agents/agent1/kill",
                {"cascade": False}
            )

        mock_fn.assert_called_once_with("test-session", "agent1", cascade=False)

    def test_post_restart_agent(self):
        """POST /api/agents/<name>/restart dispatches correctly."""
        handler = _make_handler()

        with patch('overcode.web_control_api.restart_agent') as mock_fn:
            mock_fn.return_value = {"ok": True}
            result = OvercodeHandler._dispatch_control(
                handler, "POST", "/api/agents/agent1/restart", {}
            )

        mock_fn.assert_called_once_with("test-session", "agent1")

    def test_post_launch_agent(self):
        """POST /api/agents/launch dispatches correctly."""
        handler = _make_handler()

        with patch('overcode.web_control_api.launch_agent') as mock_fn:
            mock_fn.return_value = {"ok": True, "name": "new-agent"}
            result = OvercodeHandler._dispatch_control(
                handler, "POST", "/api/agents/launch",
                {"directory": "/tmp", "name": "new-agent", "prompt": "test"}
            )

        mock_fn.assert_called_once_with(
            "test-session", directory="/tmp", name="new-agent",
            prompt="test", permissions="normal"
        )

    def test_post_sleep_agent(self):
        """POST /api/agents/<name>/sleep dispatches correctly."""
        handler = _make_handler()

        with patch('overcode.web_control_api.set_sleep') as mock_fn:
            mock_fn.return_value = {"ok": True}
            result = OvercodeHandler._dispatch_control(
                handler, "POST", "/api/agents/agent1/sleep", {"asleep": True}
            )

        mock_fn.assert_called_once_with("test-session", "agent1", asleep=True)

    def test_post_heartbeat_pause(self):
        """POST /api/agents/<name>/heartbeat/pause dispatches correctly."""
        handler = _make_handler()

        with patch('overcode.web_control_api.pause_heartbeat') as mock_fn:
            mock_fn.return_value = {"ok": True}
            result = OvercodeHandler._dispatch_control(
                handler, "POST", "/api/agents/agent1/heartbeat/pause", {}
            )

        mock_fn.assert_called_once_with("test-session", "agent1")

    def test_post_heartbeat_resume(self):
        """POST /api/agents/<name>/heartbeat/resume dispatches correctly."""
        handler = _make_handler()

        with patch('overcode.web_control_api.resume_heartbeat') as mock_fn:
            mock_fn.return_value = {"ok": True}
            result = OvercodeHandler._dispatch_control(
                handler, "POST", "/api/agents/agent1/heartbeat/resume", {}
            )

        mock_fn.assert_called_once_with("test-session", "agent1")

    def test_post_transport_all(self):
        """POST /api/agents/transport dispatches correctly."""
        handler = _make_handler()

        with patch('overcode.web_control_api.transport_all') as mock_fn:
            mock_fn.return_value = {"ok": True}
            result = OvercodeHandler._dispatch_control(
                handler, "POST", "/api/agents/transport", {}
            )

        mock_fn.assert_called_once_with("test-session")

    def test_post_cleanup(self):
        """POST /api/agents/cleanup dispatches correctly."""
        handler = _make_handler()

        with patch('overcode.web_control_api.cleanup_agents') as mock_fn:
            mock_fn.return_value = {"ok": True}
            result = OvercodeHandler._dispatch_control(
                handler, "POST", "/api/agents/cleanup",
                {"include_done": True}
            )

        mock_fn.assert_called_once_with("test-session", include_done=True)

    def test_post_monitor_restart(self):
        """POST /api/daemon/monitor/restart dispatches correctly."""
        handler = _make_handler()

        with patch('overcode.web_control_api.restart_monitor') as mock_fn:
            mock_fn.return_value = {"ok": True}
            result = OvercodeHandler._dispatch_control(
                handler, "POST", "/api/daemon/monitor/restart", {}
            )

        mock_fn.assert_called_once_with("test-session")

    def test_post_supervisor_start(self):
        """POST /api/daemon/supervisor/start dispatches correctly."""
        handler = _make_handler()

        with patch('overcode.web_control_api.start_supervisor') as mock_fn:
            mock_fn.return_value = {"ok": True}
            result = OvercodeHandler._dispatch_control(
                handler, "POST", "/api/daemon/supervisor/start", {}
            )

        mock_fn.assert_called_once_with("test-session")

    def test_post_supervisor_stop(self):
        """POST /api/daemon/supervisor/stop dispatches correctly."""
        handler = _make_handler()

        with patch('overcode.web_control_api.stop_supervisor') as mock_fn:
            mock_fn.return_value = {"ok": True}
            result = OvercodeHandler._dispatch_control(
                handler, "POST", "/api/daemon/supervisor/stop", {}
            )

        mock_fn.assert_called_once_with("test-session")

    def test_post_summarizer_toggle(self):
        """POST /api/daemon/summarizer/toggle dispatches correctly."""
        handler = _make_handler()

        with patch('overcode.web_control_api.toggle_summarizer') as mock_fn:
            mock_fn.return_value = {"ok": True}
            result = OvercodeHandler._dispatch_control(
                handler, "POST", "/api/daemon/summarizer/toggle", {}
            )

        mock_fn.assert_called_once_with("test-session")

    def test_put_standing_orders(self):
        """PUT /api/agents/<name>/standing-orders dispatches correctly."""
        handler = _make_handler()

        with patch('overcode.web_control_api.set_standing_orders') as mock_fn:
            mock_fn.return_value = {"ok": True}
            result = OvercodeHandler._dispatch_control(
                handler, "PUT", "/api/agents/agent1/standing-orders",
                {"text": "Keep working"}
            )

        mock_fn.assert_called_once_with("test-session", "agent1", text="Keep working", preset=None)

    def test_put_budget(self):
        """PUT /api/agents/<name>/budget dispatches correctly."""
        handler = _make_handler()

        with patch('overcode.web_control_api.set_budget') as mock_fn:
            mock_fn.return_value = {"ok": True}
            result = OvercodeHandler._dispatch_control(
                handler, "PUT", "/api/agents/agent1/budget",
                {"usd": 5.0}
            )

        mock_fn.assert_called_once_with("test-session", "agent1", usd=5.0)

    def test_put_value(self):
        """PUT /api/agents/<name>/value dispatches correctly."""
        handler = _make_handler()

        with patch('overcode.web_control_api.set_value') as mock_fn:
            mock_fn.return_value = {"ok": True}
            result = OvercodeHandler._dispatch_control(
                handler, "PUT", "/api/agents/agent1/value",
                {"value": 2000}
            )

        mock_fn.assert_called_once_with("test-session", "agent1", value=2000)

    def test_put_annotation(self):
        """PUT /api/agents/<name>/annotation dispatches correctly."""
        handler = _make_handler()

        with patch('overcode.web_control_api.set_annotation') as mock_fn:
            mock_fn.return_value = {"ok": True}
            result = OvercodeHandler._dispatch_control(
                handler, "PUT", "/api/agents/agent1/annotation",
                {"text": "Working on feature X"}
            )

        mock_fn.assert_called_once_with("test-session", "agent1", text="Working on feature X")

    def test_put_heartbeat_config(self):
        """PUT /api/agents/<name>/heartbeat dispatches correctly."""
        handler = _make_handler()

        with patch('overcode.web_control_api.configure_heartbeat') as mock_fn:
            mock_fn.return_value = {"ok": True}
            result = OvercodeHandler._dispatch_control(
                handler, "PUT", "/api/agents/agent1/heartbeat",
                {"enabled": True, "frequency": 300, "instruction": "check status"}
            )

        mock_fn.assert_called_once_with(
            "test-session", "agent1",
            enabled=True, frequency=300, instruction="check status"
        )

    def test_put_time_context(self):
        """PUT /api/agents/<name>/time-context dispatches correctly."""
        handler = _make_handler()

        with patch('overcode.web_control_api.set_time_context') as mock_fn:
            mock_fn.return_value = {"ok": True}
            result = OvercodeHandler._dispatch_control(
                handler, "PUT", "/api/agents/agent1/time-context",
                {"enabled": True}
            )

        mock_fn.assert_called_once_with("test-session", "agent1", enabled=True)

    def test_put_hook_detection(self):
        """PUT /api/agents/<name>/hook-detection dispatches correctly."""
        handler = _make_handler()

        with patch('overcode.web_control_api.set_hook_detection') as mock_fn:
            mock_fn.return_value = {"ok": True}
            result = OvercodeHandler._dispatch_control(
                handler, "PUT", "/api/agents/agent1/hook-detection",
                {"enabled": False}
            )

        mock_fn.assert_called_once_with("test-session", "agent1", enabled=False)

    def test_delete_standing_orders(self):
        """DELETE /api/agents/<name>/standing-orders dispatches correctly."""
        handler = _make_handler()

        with patch('overcode.web_control_api.clear_standing_orders') as mock_fn:
            mock_fn.return_value = {"ok": True}
            result = OvercodeHandler._dispatch_control(
                handler, "DELETE", "/api/agents/agent1/standing-orders", {}
            )

        mock_fn.assert_called_once_with("test-session", "agent1")

    def test_unknown_post_raises_control_error(self):
        """Unknown POST endpoint should raise ControlError with 404."""
        from overcode.web_control_api import ControlError

        handler = _make_handler()

        with pytest.raises(ControlError) as exc_info:
            OvercodeHandler._dispatch_control(
                handler, "POST", "/api/nonexistent", {}
            )

        assert exc_info.value.status == 404

    def test_unknown_put_raises_control_error(self):
        """Unknown PUT endpoint should raise ControlError with 404."""
        from overcode.web_control_api import ControlError

        handler = _make_handler()

        with pytest.raises(ControlError) as exc_info:
            OvercodeHandler._dispatch_control(
                handler, "PUT", "/api/nonexistent", {}
            )

        assert exc_info.value.status == 404

    def test_unknown_delete_raises_control_error(self):
        """Unknown DELETE endpoint should raise ControlError with 404."""
        from overcode.web_control_api import ControlError

        handler = _make_handler()

        with pytest.raises(ControlError) as exc_info:
            OvercodeHandler._dispatch_control(
                handler, "DELETE", "/api/nonexistent", {}
            )

        assert exc_info.value.status == 404

    def test_unknown_method_raises_control_error(self):
        """Unknown HTTP method should raise ControlError with 404."""
        from overcode.web_control_api import ControlError

        handler = _make_handler()

        with pytest.raises(ControlError) as exc_info:
            OvercodeHandler._dispatch_control(
                handler, "PATCH", "/api/agents/test/send", {}
            )

        assert exc_info.value.status == 404


class TestLogMessageEdgeCases:
    """Additional tests for log_message filtering logic."""

    def test_single_arg_logged_as_format(self):
        """Log message with no extra args should log the format string."""
        handler = _make_handler()

        with patch('sys.stderr') as mock_stderr:
            OvercodeHandler.log_message(handler, "direct message")
            mock_stderr.write.assert_called_once()
            assert "direct message" in mock_stderr.write.call_args[0][0]

    def test_suppresses_api_200_poll(self):
        """Successful GET /api/analytics/sessions should be suppressed."""
        handler = _make_handler()

        with patch('sys.stderr') as mock_stderr:
            OvercodeHandler.log_message(handler, "%s %s", "GET /api/analytics/sessions", "200")
            mock_stderr.write.assert_not_called()

    def test_logs_api_404(self):
        """404 on API route should be logged."""
        handler = _make_handler()

        with patch('sys.stderr') as mock_stderr:
            OvercodeHandler.log_message(handler, "%s %s", "GET /api/agents/x/status", "404")
            mock_stderr.write.assert_called()


class TestParseDateTime:
    """Additional _parse_datetime tests."""

    def test_parse_datetime_empty_string(self):
        """Empty string should return None."""
        handler = _make_handler()
        result = OvercodeHandler._parse_datetime(handler, "")
        assert result is None

    def test_parse_datetime_with_timezone(self):
        """ISO datetime with timezone offset should parse."""
        handler = _make_handler()
        result = OvercodeHandler._parse_datetime(handler, "2024-06-15T12:00:00+00:00")
        assert result is not None
        assert result.year == 2024

    def test_parse_datetime_date_only(self):
        """Date-only string should parse via fromisoformat."""
        handler = _make_handler()
        result = OvercodeHandler._parse_datetime(handler, "2024-06-15")
        assert result is not None
        assert result.day == 15
