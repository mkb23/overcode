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
