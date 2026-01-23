"""
Web server for Overcode dashboard.

Provides a mobile-optimized read-only dashboard for monitoring agents.
Uses Python stdlib http.server - no additional dependencies required.
"""

import json
import os
import sys
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse, parse_qs

from .settings import (
    get_web_server_pid_path,
    get_web_server_port_path,
    ensure_session_dir,
)
from .pid_utils import is_process_running, stop_process
from .web_templates import get_dashboard_html, get_analytics_html
from .web_api import (
    get_status_data,
    get_timeline_data,
    get_health_data,
    # Analytics API functions
    get_analytics_sessions,
    get_analytics_timeline,
    get_analytics_stats,
    get_analytics_daily,
    get_time_presets,
)


class OvercodeHandler(BaseHTTPRequestHandler):
    """HTTP request handler for overcode dashboard."""

    # Set by run_server before starting
    tmux_session: str = "agents"

    def do_GET(self) -> None:
        """Handle GET requests."""
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            self._serve_dashboard()
        elif path == "/api/status":
            self._serve_json(get_status_data(self.tmux_session))
        elif path == "/api/timeline":
            hours = float(query.get("hours", [3.0])[0])
            slots = int(query.get("slots", [60])[0])
            self._serve_json(get_timeline_data(self.tmux_session, hours=hours, slots=slots))
        elif path == "/health":
            self._serve_json(get_health_data())
        else:
            self.send_error(404, "Not Found")

    def _serve_dashboard(self) -> None:
        """Serve the dashboard HTML page."""
        try:
            html = get_dashboard_html()
            html_bytes = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html_bytes)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(html_bytes)
        except Exception as e:
            self.send_error(500, f"Internal error: {e}")

    def _serve_json(self, data: dict) -> None:
        """Serve JSON data."""
        try:
            body = json.dumps(data, indent=2)
            body_bytes = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body_bytes)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body_bytes)
        except Exception as e:
            self.send_error(500, f"Internal error: {e}")

    def log_message(self, format: str, *args) -> None:
        """Custom log format - less verbose than default."""
        # Only log errors and important requests, not every poll
        if args and len(args) >= 2:
            status = str(args[1])
            path = str(args[0])
            # Don't log successful API polls
            if status.startswith("2") and "/api/" in path:
                return
        sys.stderr.write(f"[web] {args[0] if args else format}\n")


def run_server(
    host: str = "0.0.0.0",
    port: int = 8080,
    tmux_session: str = "agents"
) -> None:
    """Run the web dashboard server.

    Args:
        host: Host to bind to (default: 0.0.0.0 for all interfaces)
        port: Port to listen on (default: 8080)
        tmux_session: tmux session name to monitor
    """
    # Set the tmux session on the handler class
    OvercodeHandler.tmux_session = tmux_session

    server_address = (host, port)

    try:
        server = HTTPServer(server_address, OvercodeHandler)
    except OSError as e:
        if "Address already in use" in str(e):
            print(f"Error: Port {port} is already in use. Try a different port with --port")
            sys.exit(1)
        raise

    # Get actual bound address for display
    bound_host, bound_port = server.server_address

    print(f"Overcode Dashboard")
    print(f"====================")
    print(f"Monitoring tmux session: {tmux_session}")
    print(f"")
    print(f"Local:   http://localhost:{bound_port}")

    if host == "0.0.0.0":
        # Try to get the machine's IP for network access
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            print(f"Network: http://{ip}:{bound_port}")
        except Exception:
            print(f"Network: http://<your-ip>:{bound_port}")

    print(f"")
    print(f"Press Ctrl+C to stop")
    print(f"")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


# =============================================================================
# Web Server Management (for TUI toggle)
# =============================================================================


def _find_available_port(start_port: int = 8080, max_attempts: int = 10) -> int:
    """Find an available port starting from start_port."""
    import socket

    for i in range(max_attempts):
        port = start_port + i
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    raise RuntimeError(f"Could not find available port in range {start_port}-{start_port + max_attempts}")


def is_web_server_running(session: str) -> bool:
    """Check if the web server is running for the given session."""
    pid_path = get_web_server_pid_path(session)
    return is_process_running(pid_path)


def get_web_server_url(session: str) -> Optional[str]:
    """Get the URL of the running web server for the session."""
    if not is_web_server_running(session):
        return None

    port_path = get_web_server_port_path(session)
    if not port_path.exists():
        return None

    try:
        port = int(port_path.read_text().strip())
        return f"http://localhost:{port}"
    except (ValueError, OSError):
        return None


def _log_to_file(session: str, message: str) -> None:
    """Write a debug message to the web server log."""
    from datetime import datetime
    from .settings import get_session_dir
    try:
        log_path = get_session_dir(session) / "web_server.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{timestamp}] [start_web_server] {message}\n")
    except Exception:
        pass


def start_web_server(session: str, port: int = 8080) -> Tuple[bool, str]:
    """Start the analytics web server for a session.

    Args:
        session: tmux session name
        port: Preferred port (will try alternatives if busy)

    Returns:
        Tuple of (success, message)
    """
    _log_to_file(session, f"start_web_server called with port={port}")

    if is_web_server_running(session):
        url = get_web_server_url(session)
        _log_to_file(session, f"Already running at {url}")
        return False, f"Already running at {url}"

    ensure_session_dir(session)

    # Find an available port
    try:
        actual_port = _find_available_port(port)
        _log_to_file(session, f"Found available port: {actual_port}")
    except RuntimeError as e:
        _log_to_file(session, f"Failed to find port: {e}")
        return False, str(e)

    # Start the server as a subprocess (works better with Textual TUI)
    import subprocess
    from .settings import get_session_dir
    log_path = get_session_dir(session) / "web_server.log"

    try:
        # Open log file in append mode for stderr
        log_file = open(log_path, "a")
        cmd = [sys.executable, "-m", "overcode.web_server_runner",
               "--session", session, "--port", str(actual_port)]
        _log_to_file(session, f"Starting subprocess: {' '.join(cmd)}")
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=log_file,
            start_new_session=True,
        )
        _log_to_file(session, f"Subprocess started with PID: {proc.pid}")
    except (OSError, subprocess.SubprocessError) as e:
        _log_to_file(session, f"Subprocess failed: {e}")
        return False, f"Failed to start: {e}"

    # Wait briefly for the server to start
    import time
    for i in range(10):
        time.sleep(0.1)
        if is_web_server_running(session):
            url = get_web_server_url(session)
            _log_to_file(session, f"Server started successfully at {url}")
            return True, f"Started at {url}"
        _log_to_file(session, f"Waiting for server... attempt {i+1}/10")

    _log_to_file(session, "Server failed to start within timeout")
    return False, "Failed to start web server"


def stop_web_server(session: str) -> Tuple[bool, str]:
    """Stop the analytics web server for a session.

    Args:
        session: tmux session name

    Returns:
        Tuple of (success, message)
    """
    pid_path = get_web_server_pid_path(session)
    port_path = get_web_server_port_path(session)

    if not is_process_running(pid_path):
        # Clean up stale files
        try:
            pid_path.unlink(missing_ok=True)
            port_path.unlink(missing_ok=True)
        except Exception:
            pass
        return False, "Not running"

    stopped = stop_process(pid_path)

    # Clean up port file
    try:
        port_path.unlink(missing_ok=True)
    except Exception:
        pass

    if stopped:
        return True, "Stopped"
    else:
        return False, "Failed to stop"


def toggle_web_server(session: str, port: int = 8080) -> Tuple[bool, str]:
    """Toggle the web server on/off for a session.

    Args:
        session: tmux session name
        port: Preferred port (used when starting)

    Returns:
        Tuple of (is_now_running, message)
    """
    if is_web_server_running(session):
        success, msg = stop_web_server(session)
        return False, msg
    else:
        success, msg = start_web_server(session, port)
        return success, msg


# =============================================================================
# Analytics Web Server (for `overcode web` historical analytics dashboard)
# =============================================================================


class AnalyticsHandler(BaseHTTPRequestHandler):
    """HTTP request handler for analytics dashboard."""

    # Set by run_analytics_server before starting
    tmux_session: str = "agents"

    def do_GET(self) -> None:
        """Handle GET requests."""
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        # Parse time range from query params
        start = self._parse_datetime(query.get("start", [None])[0])
        end = self._parse_datetime(query.get("end", [None])[0])

        if path == "/" or path == "/index.html":
            self._serve_analytics_dashboard()
        elif path == "/static/chart.min.js":
            self._serve_chartjs()
        elif path == "/api/analytics/sessions":
            self._serve_json(get_analytics_sessions(start, end))
        elif path == "/api/analytics/timeline":
            self._serve_json(get_analytics_timeline(self.tmux_session, start, end))
        elif path == "/api/analytics/stats":
            self._serve_json(get_analytics_stats(self.tmux_session, start, end))
        elif path == "/api/analytics/daily":
            self._serve_json(get_analytics_daily(start, end))
        elif path == "/api/analytics/presets":
            self._serve_json(get_time_presets())
        elif path == "/health":
            self._serve_json(get_health_data())
        else:
            self.send_error(404, "Not Found")

    def _parse_datetime(self, value: Optional[str]) -> Optional[datetime]:
        """Parse ISO datetime string from query param."""
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return None

    def _serve_analytics_dashboard(self) -> None:
        """Serve the analytics dashboard HTML page."""
        try:
            html = get_analytics_html()
            html_bytes = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html_bytes)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(html_bytes)
        except Exception as e:
            self.send_error(500, f"Internal error: {e}")

    def _serve_chartjs(self) -> None:
        """Serve the embedded Chart.js library."""
        try:
            from .web_chartjs import CHARTJS_JS
            js_bytes = CHARTJS_JS.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Content-Length", str(len(js_bytes)))
            # Cache for 1 year - it's a versioned static asset
            self.send_header("Cache-Control", "public, max-age=31536000")
            self.end_headers()
            self.wfile.write(js_bytes)
        except Exception as e:
            self.send_error(500, f"Internal error: {e}")

    def _serve_json(self, data) -> None:
        """Serve JSON data."""
        try:
            body = json.dumps(data, indent=2, default=str)
            body_bytes = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body_bytes)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body_bytes)
        except Exception as e:
            self.send_error(500, f"Internal error: {e}")

    def log_message(self, format: str, *args) -> None:
        """Custom log format - less verbose than default."""
        if args and len(args) >= 2:
            status = str(args[1])
            path = str(args[0])
            # Don't log successful API polls
            if status.startswith("2") and "/api/" in path:
                return
        sys.stderr.write(f"[analytics] {args[0] if args else format}\n")


def run_analytics_server(
    host: str = "127.0.0.1",
    port: int = 8080,
    tmux_session: str = "agents",
) -> None:
    """Run the analytics web dashboard server.

    Args:
        host: Host to bind to (default: 127.0.0.1 for local only)
        port: Port to listen on (default: 8080)
        tmux_session: tmux session name for session-specific data
    """
    # Set the tmux session on the handler class
    AnalyticsHandler.tmux_session = tmux_session

    server_address = (host, port)

    try:
        server = HTTPServer(server_address, AnalyticsHandler)
    except OSError as e:
        if "Address already in use" in str(e):
            print(f"Error: Port {port} is already in use. Try a different port with --port")
            sys.exit(1)
        raise

    # Get actual bound address for display
    bound_host, bound_port = server.server_address

    print(f"Overcode Analytics Dashboard")
    print(f"=============================")
    print(f"")
    print(f"Local:   http://localhost:{bound_port}")

    if host == "0.0.0.0":
        # Try to get the machine's IP for network access
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            print(f"Network: http://{ip}:{bound_port}")
        except Exception:
            print(f"Network: http://<your-ip>:{bound_port}")

    print(f"")
    print(f"Press Ctrl+C to stop")
    print(f"")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()
