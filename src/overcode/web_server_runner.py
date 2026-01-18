#!/usr/bin/env python3
"""
Web server runner - standalone script for running the analytics server.

This is invoked as a subprocess from the TUI to avoid multiprocessing issues
with Textual's file descriptor management.
"""

import argparse
import os
import signal
import sys
import traceback
from datetime import datetime
from http.server import HTTPServer
from pathlib import Path


def get_log_path(session: str) -> Path:
    """Get path for web server log file."""
    from .settings import get_session_dir
    return get_session_dir(session) / "web_server.log"


def log(session: str, message: str) -> None:
    """Write a log message to the web server log file."""
    try:
        log_path = get_log_path(session)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{timestamp}] {message}\n")
    except Exception:
        pass  # Can't log, silently fail


def main():
    parser = argparse.ArgumentParser(description="Run Overcode analytics web server")
    parser.add_argument("--session", "-s", required=True, help="Session name")
    parser.add_argument("--port", "-p", type=int, default=8080, help="Port to listen on")
    args = parser.parse_args()

    session = args.session
    port = args.port

    log(session, f"Starting web server on port {port}")

    try:
        from .settings import get_web_server_pid_path, get_web_server_port_path
        from .pid_utils import write_pid_file

        pid_path = get_web_server_pid_path(session)
        port_path = get_web_server_port_path(session)

        # Write PID file
        write_pid_file(pid_path)
        log(session, f"Wrote PID file: {pid_path}")

        # Write port file so TUI can find the URL
        port_path.write_text(str(port))
        log(session, f"Wrote port file: {port_path}")

        def cleanup(signum, frame):
            log(session, f"Received signal {signum}, shutting down")
            try:
                pid_path.unlink(missing_ok=True)
                port_path.unlink(missing_ok=True)
            except Exception:
                pass
            sys.exit(0)

        signal.signal(signal.SIGTERM, cleanup)
        signal.signal(signal.SIGINT, cleanup)

        # Import here to avoid circular imports
        from .web_server import AnalyticsHandler

        server_address = ("127.0.0.1", port)
        log(session, f"Creating HTTP server at {server_address}")
        server = HTTPServer(server_address, AnalyticsHandler)
        log(session, "Server created, starting serve_forever()")

        # Redirect stdout/stderr AFTER setup is complete
        sys.stdout = open(os.devnull, 'w')
        sys.stderr = open(os.devnull, 'w')

        server.serve_forever()

    except Exception as e:
        log(session, f"ERROR: {e}\n{traceback.format_exc()}")
        # Clean up on error
        try:
            from .settings import get_web_server_pid_path, get_web_server_port_path
            pid_path = get_web_server_pid_path(session)
            port_path = get_web_server_port_path(session)
            pid_path.unlink(missing_ok=True)
            port_path.unlink(missing_ok=True)
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
