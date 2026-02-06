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

    def test_handler_has_tmux_session_attribute(self):
        """Handler should have tmux_session class attribute."""
        assert hasattr(OvercodeHandler, 'tmux_session')
        assert OvercodeHandler.tmux_session == "agents"

    def test_handler_inherits_from_base_handler(self):
        """Handler should inherit from BaseHTTPRequestHandler."""
        from http.server import BaseHTTPRequestHandler
        assert issubclass(OvercodeHandler, BaseHTTPRequestHandler)

    def test_handler_has_required_methods(self):
        """Handler should have do_GET and helper methods."""
        assert hasattr(OvercodeHandler, 'do_GET')
        assert hasattr(OvercodeHandler, '_serve_dashboard')
        assert hasattr(OvercodeHandler, '_serve_json')
        assert hasattr(OvercodeHandler, 'log_message')

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


class TestAnalyticsHandler:
    """Tests for AnalyticsHandler class."""

    def test_handler_has_tmux_session_attribute(self):
        """Handler should have tmux_session class attribute."""
        from overcode.web_server import AnalyticsHandler
        assert hasattr(AnalyticsHandler, 'tmux_session')
        assert AnalyticsHandler.tmux_session == "agents"

    def test_handler_has_required_methods(self):
        """Handler should have do_GET and helper methods."""
        from overcode.web_server import AnalyticsHandler
        assert hasattr(AnalyticsHandler, 'do_GET')
        assert hasattr(AnalyticsHandler, '_serve_analytics_dashboard')
        assert hasattr(AnalyticsHandler, '_serve_json')
        assert hasattr(AnalyticsHandler, '_parse_datetime')

    def test_parse_datetime_returns_none_for_none(self):
        """_parse_datetime should return None for None input."""
        from overcode.web_server import AnalyticsHandler

        handler = MagicMock(spec=AnalyticsHandler)
        result = AnalyticsHandler._parse_datetime(handler, None)

        assert result is None

    def test_parse_datetime_returns_none_for_invalid(self):
        """_parse_datetime should return None for invalid input."""
        from overcode.web_server import AnalyticsHandler

        handler = MagicMock(spec=AnalyticsHandler)
        result = AnalyticsHandler._parse_datetime(handler, "not-a-date")

        assert result is None

    def test_parse_datetime_parses_valid_iso(self):
        """_parse_datetime should parse valid ISO datetime."""
        from overcode.web_server import AnalyticsHandler
        from datetime import datetime

        handler = MagicMock(spec=AnalyticsHandler)
        result = AnalyticsHandler._parse_datetime(handler, "2024-01-15T10:30:00")

        assert result is not None
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15


class TestOvercodeHandlerRoutes:
    """Tests for OvercodeHandler route handling."""

    def test_do_GET_returns_404_for_unknown(self):
        """do_GET should return 404 for unknown routes."""
        handler = MagicMock(spec=OvercodeHandler)
        handler.path = "/unknown/route"

        OvercodeHandler.do_GET(handler)
        handler.send_error.assert_called_once_with(404, "Not Found")

    def test_root_route_matches_dashboard(self):
        """Root path should match dashboard route logic."""
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

    def test_root_calls_serve_dashboard(self):
        """GET / should call _serve_dashboard."""
        handler = MagicMock(spec=OvercodeHandler)
        handler.path = "/"

        OvercodeHandler.do_GET(handler)

        handler._serve_dashboard.assert_called_once()

    def test_index_html_calls_serve_dashboard(self):
        """GET /index.html should call _serve_dashboard."""
        handler = MagicMock(spec=OvercodeHandler)
        handler.path = "/index.html"

        OvercodeHandler.do_GET(handler)

        handler._serve_dashboard.assert_called_once()

    def test_api_status_calls_serve_json(self):
        """GET /api/status should call _serve_json with get_status_data result."""
        handler = MagicMock(spec=OvercodeHandler)
        handler.path = "/api/status"
        handler.tmux_session = "test-session"

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

        with patch('overcode.web_server.get_timeline_data') as mock_get_timeline:
            mock_get_timeline.return_value = {"timeline": "data"}

            OvercodeHandler.do_GET(handler)

            mock_get_timeline.assert_called_once_with("test-session", hours=6.0, slots=30)

    def test_health_calls_serve_json(self):
        """GET /health should call _serve_json with get_health_data result."""
        handler = MagicMock(spec=OvercodeHandler)
        handler.path = "/health"

        with patch('overcode.web_server.get_health_data') as mock_health:
            mock_health.return_value = {"status": "ok"}

            OvercodeHandler.do_GET(handler)

            mock_health.assert_called_once()
            handler._serve_json.assert_called_once_with({"status": "ok"})

    def test_unknown_route_returns_404(self):
        """GET /unknown should call send_error(404)."""
        handler = MagicMock(spec=OvercodeHandler)
        handler.path = "/nonexistent"

        OvercodeHandler.do_GET(handler)

        handler.send_error.assert_called_once_with(404, "Not Found")


class TestAnalyticsHandlerRouteDispatching:
    """Tests that AnalyticsHandler.do_GET dispatches to correct methods."""

    def test_root_calls_serve_analytics_dashboard(self):
        """GET / should call _serve_analytics_dashboard."""
        from overcode.web_server import AnalyticsHandler

        handler = MagicMock(spec=AnalyticsHandler)
        handler.path = "/"
        handler._parse_datetime = MagicMock(return_value=None)

        AnalyticsHandler.do_GET(handler)

        handler._serve_analytics_dashboard.assert_called_once()

    def test_sessions_route_calls_get_analytics_sessions(self):
        """GET /api/analytics/sessions should call get_analytics_sessions."""
        from overcode.web_server import AnalyticsHandler

        handler = MagicMock(spec=AnalyticsHandler)
        handler.path = "/api/analytics/sessions"
        handler._parse_datetime = MagicMock(return_value=None)

        with patch('overcode.web_server.get_analytics_sessions') as mock_fn:
            mock_fn.return_value = {"sessions": []}

            AnalyticsHandler.do_GET(handler)

            mock_fn.assert_called_once_with(None, None)
            handler._serve_json.assert_called_once_with({"sessions": []})

    def test_timeline_route_calls_get_analytics_timeline(self):
        """GET /api/analytics/timeline should call get_analytics_timeline."""
        from overcode.web_server import AnalyticsHandler

        handler = MagicMock(spec=AnalyticsHandler)
        handler.path = "/api/analytics/timeline"
        handler.tmux_session = "test-session"
        handler._parse_datetime = MagicMock(return_value=None)

        with patch('overcode.web_server.get_analytics_timeline') as mock_fn:
            mock_fn.return_value = {"agents": {}}

            AnalyticsHandler.do_GET(handler)

            mock_fn.assert_called_once_with("test-session", None, None)
            handler._serve_json.assert_called_once()

    def test_stats_route_calls_get_analytics_stats(self):
        """GET /api/analytics/stats should call get_analytics_stats."""
        from overcode.web_server import AnalyticsHandler

        handler = MagicMock(spec=AnalyticsHandler)
        handler.path = "/api/analytics/stats"
        handler.tmux_session = "test-session"
        handler._parse_datetime = MagicMock(return_value=None)

        with patch('overcode.web_server.get_analytics_stats') as mock_fn:
            mock_fn.return_value = {"stats": {}}

            AnalyticsHandler.do_GET(handler)

            mock_fn.assert_called_once_with("test-session", None, None)
            handler._serve_json.assert_called_once()

    def test_daily_route_calls_get_analytics_daily(self):
        """GET /api/analytics/daily should call get_analytics_daily."""
        from overcode.web_server import AnalyticsHandler

        handler = MagicMock(spec=AnalyticsHandler)
        handler.path = "/api/analytics/daily"
        handler._parse_datetime = MagicMock(return_value=None)

        with patch('overcode.web_server.get_analytics_daily') as mock_fn:
            mock_fn.return_value = {"days": []}

            AnalyticsHandler.do_GET(handler)

            mock_fn.assert_called_once_with(None, None)
            handler._serve_json.assert_called_once()

    def test_presets_route_calls_get_time_presets(self):
        """GET /api/analytics/presets should call get_time_presets."""
        from overcode.web_server import AnalyticsHandler

        handler = MagicMock(spec=AnalyticsHandler)
        handler.path = "/api/analytics/presets"
        handler._parse_datetime = MagicMock(return_value=None)

        with patch('overcode.web_server.get_time_presets') as mock_fn:
            mock_fn.return_value = [{"label": "24h"}]

            AnalyticsHandler.do_GET(handler)

            mock_fn.assert_called_once()
            handler._serve_json.assert_called_once_with([{"label": "24h"}])

    def test_chartjs_route_calls_serve_chartjs(self):
        """GET /static/chart.min.js should call _serve_chartjs."""
        from overcode.web_server import AnalyticsHandler

        handler = MagicMock(spec=AnalyticsHandler)
        handler.path = "/static/chart.min.js"
        handler._parse_datetime = MagicMock(return_value=None)

        AnalyticsHandler.do_GET(handler)

        handler._serve_chartjs.assert_called_once()

    def test_health_route_calls_get_health_data(self):
        """GET /health should call get_health_data."""
        from overcode.web_server import AnalyticsHandler

        handler = MagicMock(spec=AnalyticsHandler)
        handler.path = "/health"
        handler._parse_datetime = MagicMock(return_value=None)

        with patch('overcode.web_server.get_health_data') as mock_fn:
            mock_fn.return_value = {"status": "ok"}

            AnalyticsHandler.do_GET(handler)

            mock_fn.assert_called_once()
            handler._serve_json.assert_called_once_with({"status": "ok"})

    def test_unknown_route_returns_404(self):
        """GET /unknown should call send_error(404)."""
        from overcode.web_server import AnalyticsHandler

        handler = MagicMock(spec=AnalyticsHandler)
        handler.path = "/nonexistent"
        handler._parse_datetime = MagicMock(return_value=None)

        AnalyticsHandler.do_GET(handler)

        handler.send_error.assert_called_once_with(404, "Not Found")

    def test_sessions_route_with_time_params(self):
        """GET /api/analytics/sessions with start/end should parse datetime params."""
        from overcode.web_server import AnalyticsHandler
        from datetime import datetime

        handler = MagicMock(spec=AnalyticsHandler)
        handler.path = "/api/analytics/sessions?start=2024-01-01T00:00:00&end=2024-01-31T23:59:59"

        start_dt = datetime(2024, 1, 1)
        end_dt = datetime(2024, 1, 31, 23, 59, 59)
        handler._parse_datetime = MagicMock(side_effect=lambda v: {
            "2024-01-01T00:00:00": start_dt,
            "2024-01-31T23:59:59": end_dt,
        }.get(v))

        with patch('overcode.web_server.get_analytics_sessions') as mock_fn:
            mock_fn.return_value = {"sessions": []}

            AnalyticsHandler.do_GET(handler)

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
