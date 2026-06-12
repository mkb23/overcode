"""Per-test tmux server sandbox.

Every test gets a private tmux *server* (its own -L socket), so teardown is a
single kill-server that takes down every session, window, and pane process.
Sockets are uuid-suffixed, so pytest-xdist workers cannot collide.
"""

import subprocess
import uuid


class TmuxSandbox:
    def __init__(self, socket_name: str | None = None):
        self.socket = socket_name or f"oc-{uuid.uuid4().hex[:10]}"

    def cmd(
        self, *args: str, timeout: float = 10, env: dict | None = None
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["tmux", "-L", self.socket, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )

    def new_sized_session(
        self, session: str, command: str, env: dict, width: int = 120, height: int = 40
    ) -> None:
        """Detached session of an exact size running `command` (e.g. a TUI)."""
        self.cmd(
            "new-session", "-d", "-s", session,
            "-x", str(width), "-y", str(height),
            command,
            env=env,
        )

    def capture_pane_ansi(self, session: str, window: str = "", lines: int = 0) -> str:
        """Capture pane content including ANSI escapes (for PNG rendering)."""
        target = f"{session}:{window}" if window else session
        result = self.cmd("capture-pane", "-t", target, "-p", "-e")
        return result.stdout if result.returncode == 0 else ""

    def server_running(self) -> bool:
        return self.cmd("list-sessions").returncode == 0

    def has_session(self, session: str) -> bool:
        return self.cmd("has-session", "-t", session).returncode == 0

    def list_windows(self, session: str) -> list[str]:
        result = self.cmd("list-windows", "-t", session, "-F", "#{window_name}")
        if result.returncode != 0:
            return []
        return [line for line in result.stdout.splitlines() if line]

    def capture_pane(self, session: str, window: str = "", lines: int = 200) -> str:
        target = f"{session}:{window}" if window else session
        result = self.cmd(
            "capture-pane", "-t", target, "-p", "-S", f"-{lines}"
        )
        return result.stdout if result.returncode == 0 else ""

    def send_keys(self, session: str, window: str, *keys: str) -> None:
        target = f"{session}:{window}" if window else session
        self.cmd("send-keys", "-t", target, *keys)

    def set_global_env(self, key: str, value: str) -> None:
        """Set a global tmux env var (inherited by panes created afterwards).

        No-op if the server isn't up yet — in that case the variable must be in
        the environment of whichever overcode call first starts the server.
        """
        if self.server_running():
            self.cmd("set-environment", "-g", key, value)

    def kill_server(self) -> None:
        self.cmd("kill-server")
