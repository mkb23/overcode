"""
Web server for Overcode dashboard.

Provides a mobile-optimized read-only dashboard for monitoring agents.
Uses Python stdlib http.server - no additional dependencies required.
"""

import json
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional
from urllib.parse import urlparse, parse_qs

from .web_templates import get_dashboard_html
from .web_api import get_status_data, get_timeline_data, get_health_data


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
