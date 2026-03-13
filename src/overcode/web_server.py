"""
Web server for Overcode dashboard and control API.

Provides a mobile-optimized dashboard for monitoring agents (GET)
and a control API for remote agent management (POST/PUT/DELETE).
Uses Python stdlib http.server - no additional dependencies required.
"""

import json
import sys
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional, Tuple
from urllib.parse import urlparse, parse_qs

from .settings import (
    get_web_server_pid_path,
    get_web_server_port_path,
    ensure_session_dir,
)
from .config import get_web_api_key, get_web_allow_control
from .pid_utils import is_process_running, stop_process
from .web_templates import get_dashboard_html, get_analytics_html
from .web_api import (
    get_status_data,
    get_single_agent_status,
    get_timeline_data,
    get_raw_timeline_data,
    get_health_data,
    # Analytics API functions
    get_analytics_sessions,
    get_analytics_timeline,
    get_analytics_stats,
    get_analytics_daily,
    get_time_presets,
)


# Route table: path -> method name on OvercodeHandler
_GET_ROUTES = {
    "/": "_serve_analytics_dashboard",
    "/index.html": "_serve_analytics_dashboard",
    "/static/chart.min.js": "_serve_chartjs",
    "/dashboard": "_serve_dashboard",
    "/api/status": "_serve_api_status",
    "/api/analytics/sessions": "_serve_analytics_sessions",
    "/api/analytics/timeline": "_serve_analytics_timeline",
    "/api/analytics/stats": "_serve_analytics_stats",
    "/api/analytics/daily": "_serve_analytics_daily",
    "/api/analytics/presets": "_serve_analytics_presets",
    "/api/timeline": "_serve_timeline",
    "/api/timeline/raw": "_serve_timeline_raw",
    "/health": "_serve_health",
}

# Control API route tables (POST/PUT/DELETE).
# Fixed routes: (method, path) -> handler(api, ts, body)
_FIXED_CONTROL_ROUTES = {
    ("POST", "/api/agents/launch"): lambda api, ts, body: api.launch_agent(
        ts, directory=body.get("directory", "."), name=body.get("name", ""),
        prompt=body.get("prompt"), permissions=body.get("permissions", "normal"),
    ),
    ("POST", "/api/agents/transport"): lambda api, ts, body: api.transport_all(ts),
    ("POST", "/api/agents/cleanup"): lambda api, ts, body: api.cleanup_agents(
        ts, include_done=body.get("include_done", False),
    ),
    ("POST", "/api/daemon/monitor/restart"): lambda api, ts, body: api.restart_monitor(ts),
    ("POST", "/api/daemon/supervisor/start"): lambda api, ts, body: api.start_supervisor(ts),
    ("POST", "/api/daemon/supervisor/stop"): lambda api, ts, body: api.stop_supervisor(ts),
    ("POST", "/api/daemon/summarizer/toggle"): lambda api, ts, body: api.toggle_summarizer(ts),
}

# Agent routes: (method, action_suffix) -> handler(api, ts, name, body)
_AGENT_CONTROL_ROUTES = {
    # POST agent routes
    ("POST", "send"): lambda api, ts, name, body: api.send_to_agent(
        ts, name, text=body.get("text", ""), enter=body.get("enter", True),
    ),
    ("POST", "keys"): lambda api, ts, name, body: api.send_key_to_agent(
        ts, name, key=body.get("key", ""),
    ),
    ("POST", "kill"): lambda api, ts, name, body: api.kill_agent(
        ts, name, cascade=body.get("cascade", True),
    ),
    ("POST", "restart"): lambda api, ts, name, body: api.restart_agent(ts, name),
    ("POST", "fork"): lambda api, ts, name, body: api.fork_agent(
        ts, name, fork_name=body.get("name", ""), prompt=body.get("prompt"),
    ),
    ("POST", "sleep"): lambda api, ts, name, body: api.set_sleep(
        ts, name, asleep=body.get("asleep", True),
    ),
    ("POST", "heartbeat/pause"): lambda api, ts, name, body: api.pause_heartbeat(ts, name),
    ("POST", "heartbeat/resume"): lambda api, ts, name, body: api.resume_heartbeat(ts, name),
    # PUT agent routes
    ("PUT", "standing-orders"): lambda api, ts, name, body: api.set_standing_orders(
        ts, name, text=body.get("text"), preset=body.get("preset"),
    ),
    ("PUT", "budget"): lambda api, ts, name, body: api.set_budget(
        ts, name, usd=float(body.get("usd", 0)),
    ),
    ("PUT", "value"): lambda api, ts, name, body: api.set_value(
        ts, name, value=int(body.get("value", 1000)),
    ),
    ("PUT", "annotation"): lambda api, ts, name, body: api.set_annotation(
        ts, name, text=body.get("text", ""),
    ),
    ("PUT", "heartbeat"): lambda api, ts, name, body: api.configure_heartbeat(
        ts, name, enabled=body.get("enabled", True),
        frequency=body.get("frequency"), instruction=body.get("instruction"),
    ),
    ("PUT", "time-context"): lambda api, ts, name, body: api.set_time_context(
        ts, name, enabled=body.get("enabled", True),
    ),
    ("PUT", "hook-detection"): lambda api, ts, name, body: api.set_hook_detection(
        ts, name, enabled=body.get("enabled", True),
    ),
    # DELETE agent routes
    ("DELETE", "standing-orders"): lambda api, ts, name, body: api.clear_standing_orders(ts, name),
}


class OvercodeHandler(BaseHTTPRequestHandler):
    """HTTP request handler for overcode dashboard.

    Serves both the live monitoring dashboard and historical analytics.
    """

    # Set by run_server before starting
    tmux_session: str = "agents"

    def do_GET(self) -> None:
        """Handle GET requests."""
        api_key = get_web_api_key()
        if api_key:
            request_key = self.headers.get("X-API-Key", "")
            if request_key != api_key:
                self.send_error(401, "Unauthorized: invalid or missing X-API-Key header")
                return

        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        handler_name = _GET_ROUTES.get(path)
        if handler_name:
            getattr(self, handler_name)(query)
            return

        # Dynamic route: /api/agents/{name}/status
        if path.startswith("/api/agents/") and path.endswith("/status"):
            name = path.split("/")[3]
            agent_data = get_single_agent_status(self.tmux_session, name)
            if agent_data is not None:
                self._serve_json(agent_data)
            else:
                self.send_error(404, f"Agent '{name}' not found")
            return

        self.send_error(404, "Not Found")

    def _parse_datetime(self, value: Optional[str]) -> Optional[datetime]:
        """Parse ISO datetime string from query param."""
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return None

    def _parse_time_range(self, query) -> Tuple[Optional[datetime], Optional[datetime]]:
        """Parse start/end datetime from query params."""
        return (
            self._parse_datetime(query.get("start", [None])[0]),
            self._parse_datetime(query.get("end", [None])[0]),
        )

    # -----------------------------------------------------------------
    # GET route handlers
    # -----------------------------------------------------------------

    def _serve_content(self, content: str, content_type: str = "text/html; charset=utf-8", cache_control: str = "no-cache") -> None:
        """Serve string content with given content type and cache headers."""
        try:
            content_bytes = content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content_bytes)))
            self.send_header("Cache-Control", cache_control)
            self.end_headers()
            self.wfile.write(content_bytes)
        except Exception as e:
            self.send_error(500, f"Internal error: {e}")

    def _serve_dashboard(self, query=None) -> None:
        """Serve the live monitoring dashboard HTML page."""
        self._serve_content(get_dashboard_html())

    def _serve_analytics_dashboard(self, query=None) -> None:
        """Serve the analytics dashboard HTML page."""
        self._serve_content(get_analytics_html())

    def _serve_chartjs(self, query=None) -> None:
        """Serve the embedded Chart.js library."""
        from .web_chartjs import CHARTJS_JS
        self._serve_content(CHARTJS_JS, "application/javascript", "public, max-age=31536000")

    def _serve_api_status(self, query) -> None:
        self._serve_json(get_status_data(self.tmux_session))

    def _serve_analytics_sessions(self, query) -> None:
        start, end = self._parse_time_range(query)
        self._serve_json(get_analytics_sessions(start, end))

    def _serve_analytics_timeline(self, query) -> None:
        start, end = self._parse_time_range(query)
        self._serve_json(get_analytics_timeline(self.tmux_session, start, end))

    def _serve_analytics_stats(self, query) -> None:
        start, end = self._parse_time_range(query)
        self._serve_json(get_analytics_stats(self.tmux_session, start, end))

    def _serve_analytics_daily(self, query) -> None:
        start, end = self._parse_time_range(query)
        self._serve_json(get_analytics_daily(start, end))

    def _serve_analytics_presets(self, query) -> None:
        self._serve_json(get_time_presets())

    def _serve_timeline(self, query) -> None:
        hours = float(query.get("hours", [3.0])[0])
        slots = int(query.get("slots", [60])[0])
        self._serve_json(get_timeline_data(self.tmux_session, hours=hours, slots=slots))

    def _serve_timeline_raw(self, query) -> None:
        hours = float(query.get("hours", [3.0])[0])
        self._serve_json(get_raw_timeline_data(self.tmux_session, hours=hours))

    def _serve_health(self, query) -> None:
        self._serve_json(get_health_data())

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

    # -----------------------------------------------------------------
    # Control API (POST / PUT / DELETE)
    # -----------------------------------------------------------------

    def _check_auth(self) -> bool:
        """Check API key authentication. Returns True if authorized."""
        api_key = get_web_api_key()
        if api_key:
            request_key = self.headers.get("X-API-Key", "")
            if request_key != api_key:
                self._send_json_error(401, "Unauthorized: invalid or missing X-API-Key header")
                return False
        return True

    def _check_control_allowed(self) -> bool:
        """Check if remote control is enabled. Returns True if allowed."""
        if not get_web_allow_control():
            self._send_json_error(403, "Remote control not enabled (set web.allow_control: true)")
            return False
        return True

    def _read_json_body(self) -> Optional[dict]:
        """Read and parse JSON body from request. Returns None on error."""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            return {}
        try:
            body = self.rfile.read(content_length)
            return json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self._send_json_error(400, f"Invalid JSON body: {e}")
            return None

    def _send_json_response(self, data: dict, status: int = 200) -> None:
        """Send a JSON response with the given status code."""
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_json_error(self, status: int, message: str) -> None:
        """Send a JSON error response."""
        self._send_json_response({"ok": False, "error": message}, status=status)

    def _route_control(self, method: str) -> None:
        """Route POST/PUT/DELETE requests to control API handlers."""
        from .web_control_api import ControlError

        if not self._check_auth():
            return
        if not self._check_control_allowed():
            return

        parsed = urlparse(self.path)
        path = parsed.path

        body = self._read_json_body()
        if body is None:
            return  # Error already sent

        try:
            result = self._dispatch_control(method, path, body)
            self._send_json_response(result)
        except ControlError as e:
            self._send_json_error(e.status, str(e))
        except Exception as e:
            self._send_json_error(500, f"Internal error: {e}")

    def _dispatch_control(self, method: str, path: str, body: dict) -> dict:
        """Dispatch a control request to the appropriate handler."""
        from . import web_control_api as api
        from .web_control_api import ControlError

        ts = self.tmux_session

        # Try fixed routes first
        fixed_handler = _FIXED_CONTROL_ROUTES.get((method, path))
        if fixed_handler:
            return fixed_handler(api, ts, body)

        # Try agent routes: /api/agents/{name}/{action...}
        if path.startswith("/api/agents/"):
            parts = path.split("/")
            if len(parts) >= 5:
                name = parts[3]
                action = "/".join(parts[4:])
                agent_handler = _AGENT_CONTROL_ROUTES.get((method, action))
                if agent_handler:
                    return agent_handler(api, ts, name, body)

        raise ControlError(f"Unknown {method} endpoint: {path}", status=404)

    def do_OPTIONS(self) -> None:
        """Handle CORS preflight requests."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_POST(self) -> None:
        """Handle POST requests (control API)."""
        self._route_control("POST")

    def do_PUT(self) -> None:
        """Handle PUT requests (control API)."""
        self._route_control("PUT")

    def do_DELETE(self) -> None:
        """Handle DELETE requests (control API)."""
        self._route_control("DELETE")

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
    host: str = "127.0.0.1",
    port: int = 8080,
    tmux_session: str = "agents"
) -> None:
    """Run the web dashboard server.

    Args:
        host: Host to bind to (default: 127.0.0.1 for localhost only)
        port: Port to listen on (default: 8080)
        tmux_session: tmux session name to monitor
    """
    # Security: require API key when binding to non-localhost
    if host != "127.0.0.1" and host != "localhost":
        if not get_web_api_key():
            print("Error: Binding to non-localhost requires web.api_key in config.")
            print("Set it in ~/.overcode/config.yaml:")
            print()
            print("  web:")
            print('    api_key: "your-secret-key"')
            print()
            sys.exit(1)

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

    print("Overcode Web Server")
    print("====================")
    print(f"Monitoring tmux session: {tmux_session}")
    print("")
    print(f"  Analytics:  http://localhost:{bound_port}/")
    print(f"  Dashboard:  http://localhost:{bound_port}/dashboard")
    print(f"  API Status: http://localhost:{bound_port}/api/status")
    print("")
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

    print("")
    print("Press Ctrl+C to stop")
    print("")

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
    except (OSError, TypeError, AttributeError):
        try:
            sys.stderr.write(f"[web] Failed to write log: {message}\n")
        except Exception:
            pass


def start_web_server(session: str, port: int | None = None, host: str | None = None) -> Tuple[bool, str]:
    """Start the analytics web server for a session.

    Args:
        session: tmux session name
        port: Preferred port (will try alternatives if busy)
        host: Host to bind to (default: 127.0.0.1)

    Returns:
        Tuple of (success, message)
    """
    from .config import get_web_port, get_web_host
    if port is None:
        port = get_web_port()
    if host is None:
        host = get_web_host()

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
               "--session", session, "--port", str(actual_port),
               "--host", host]
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
        except OSError:
            pass
        return False, "Not running"

    stopped = stop_process(pid_path)

    # Clean up port file
    try:
        port_path.unlink(missing_ok=True)
    except OSError:
        pass

    if stopped:
        return True, "Stopped"
    else:
        return False, "Failed to stop"


def toggle_web_server(session: str, port: int | None = None, host: str | None = None) -> Tuple[bool, str]:
    """Toggle the web server on/off for a session.

    Args:
        session: tmux session name
        port: Preferred port (used when starting)
        host: Host to bind to (default: 127.0.0.1)

    Returns:
        Tuple of (is_now_running, message)
    """
    if is_web_server_running(session):
        success, msg = stop_web_server(session)
        return False, msg
    else:
        success, msg = start_web_server(session, port, host)
        return success, msg
