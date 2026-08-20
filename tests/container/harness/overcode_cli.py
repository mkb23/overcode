"""Driver for running overcode CLI commands inside the sandbox.

State layout (with OVERCODE_STATE_DIR=$state_dir):
  $state_dir/sessions/sessions.json      session registry (SessionManager)
  $state_dir/<tmux-session>/...          per-session daemon state/pids/logs
"""

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from .core import MOCK_CLAUDE, MOCK_OPENCODE, REPO_ROOT
from .tmux_sandbox import TmuxSandbox

# Env vars from a host Claude Code session that would confuse agents under test
_HOST_AGENT_VARS = (
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_SSE_PORT",
    "OVERCODE_SESSION_NAME",
    "OVERCODE_SESSION_ID",
    "OVERCODE_TMUX_SESSION",
)


class OvercodeCLI:
    """Runs overcode commands with fully test-scoped environment and state."""

    def __init__(self, base_dir: Path, sandbox: TmuxSandbox, mock_claude: bool = True):
        self.sandbox = sandbox
        self.session = f"e2e-{uuid.uuid4().hex[:8]}"
        self.home = base_dir / "home"
        self.overcode_dir = base_dir / "overcode"
        self.state_dir = self.overcode_dir / "state"
        self.log_dir = base_dir / "logs"
        for d in (self.home, self.overcode_dir, self.state_dir, self.log_dir):
            d.mkdir(parents=True, exist_ok=True)

        env = {k: v for k, v in os.environ.items() if k not in _HOST_AGENT_VARS}
        # Private HOME: ~/.claude (skills/hooks installs), ~/.overcode defaults
        env["HOME"] = str(self.home)
        env["OVERCODE_DIR"] = str(self.overcode_dir)
        env["OVERCODE_STATE_DIR"] = str(self.state_dir)
        env["OVERCODE_TMUX_SOCKET"] = sandbox.socket
        if mock_claude:
            env["CLAUDE_COMMAND"] = str(MOCK_CLAUDE)
            env["OPENCODE_COMMAND"] = str(MOCK_OPENCODE)
        self.env = env

        self._daemons: list[subprocess.Popen] = []

    # ------------------------------------------------------------------ CLI

    def run(
        self,
        *args,
        timeout: float = 30,
        session_arg: bool = True,
        extra_env: dict | None = None,
    ) -> subprocess.CompletedProcess:
        cmd = [sys.executable, "-m", "overcode.cli", *[str(a) for a in args]]
        if session_arg:
            cmd += ["--session", self.session]
        env = {**self.env, **(extra_env or {})}
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=REPO_ROOT,
        )

    def ok(self, *args, **kwargs) -> subprocess.CompletedProcess:
        """Run a command and assert it exited 0."""
        result = self.run(*args, **kwargs)
        assert result.returncode == 0, (
            f"`overcode {' '.join(str(a) for a in args)}` failed "
            f"(rc={result.returncode})\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        return result

    # ---------------------------------------------------------------- agents

    def launch(self, name: str, *args, scenario: str = "startup_idle", **kwargs):
        """Launch a mock agent running the given mock_claude scenario.

        The launcher has first-class mock support: when MOCK_SCENARIO is in the
        launch command's environment, it bakes `MOCK_SCENARIO=<x> python
        <CLAUDE_COMMAND> ...` into the pane command (launcher.py:258).
        """
        extra_env = {"MOCK_SCENARIO": scenario, **kwargs.pop("extra_env", {})}
        return self.ok("launch", "-n", name, *args, extra_env=extra_env, **kwargs)

    def pane(self, agent_name: str, lines: int = 200) -> str:
        """Capture the agent's tmux pane content."""
        for window in self.sandbox.list_windows(self.session):
            if window.startswith(agent_name):
                return self.sandbox.capture_pane(self.session, window, lines)
        return ""

    # ----------------------------------------------------------------- state

    @property
    def session_dir(self) -> Path:
        return self.state_dir / self.session

    def sessions(self) -> list[dict]:
        """Read the session registry (source of truth for agent metadata).

        sessions.json is a dict keyed by session id (SessionManager._save_state).
        """
        registry = self.state_dir / "sessions" / "sessions.json"
        if not registry.exists():
            return []
        try:
            data = json.loads(registry.read_text())
        except json.JSONDecodeError:  # may be mid-write
            return []
        return list(data.values())

    def agent(self, name: str) -> dict | None:
        for s in self.sessions():
            if s.get("name") == name and s.get("tmux_session") == self.session:
                return s
        return None

    def daemon_state(self) -> dict:
        state_file = self.session_dir / "monitor_daemon_state.json"
        if not state_file.exists():
            return {}
        try:
            return json.loads(state_file.read_text())
        except json.JSONDecodeError:  # daemon may be mid-write
            return {}

    def agent_daemon_state(self, name: str) -> dict:
        for s in self.daemon_state().get("sessions", []):
            if s.get("name") == name:
                return s
        return {}

    def agent_status(self, name: str) -> str:
        """Monitor-daemon-detected status (running, waiting_user, ...)."""
        return self.agent_daemon_state(name).get("current_status", "")

    # --------------------------------------------------------------- daemons

    def start_monitor_daemon(self, interval: int = 1) -> subprocess.Popen:
        return self._spawn_daemon("monitor-daemon", interval)

    def start_supervisor_daemon(self, interval: int = 1) -> subprocess.Popen:
        return self._spawn_daemon("supervisor-daemon", interval)

    def _spawn_daemon(self, kind: str, interval: int) -> subprocess.Popen:
        log = (self.log_dir / f"{kind}.log").open("a")
        proc = subprocess.Popen(
            [
                sys.executable, "-m", "overcode.cli", kind, "start",
                "--interval", str(interval), "--session", self.session,
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            env=self.env,
            cwd=REPO_ROOT,
            start_new_session=True,
        )
        # Reap on exit: a zombie daemon would still pass is_process_running()
        # (kill(pid, 0) succeeds on zombies), wedging `monitor-daemon status`.
        threading.Thread(target=proc.wait, daemon=True).start()
        self._daemons.append(proc)
        return proc

    # ------------------------------------------------------------- web server

    @property
    def config_file(self) -> Path:
        """User config lives under HOME (config.py), not OVERCODE_DIR."""
        path = self.home / ".overcode" / "config.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def start_web(self, extra_config: str = "") -> str:
        """Start the web server on a free port; returns its base URL.

        The server self-daemonizes (start_web_server uses start_new_session),
        so teardown goes through `web --stop` in stop_daemons().
        """
        self.config_file.write_text(extra_config)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        self.ok("web", "--port", str(port))
        self._web_started = True
        return f"http://127.0.0.1:{port}"

    def stop_daemons(self) -> None:
        if getattr(self, "_web_started", False):
            try:
                self.run("web", "--stop", timeout=10)
            except subprocess.TimeoutExpired:
                pass
        for kind in ("supervisor-daemon", "monitor-daemon"):
            try:
                self.run(kind, "stop", timeout=10)
            except subprocess.TimeoutExpired:
                pass
        for proc in self._daemons:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            proc.wait(timeout=5)
        self._daemons.clear()

    # ------------------------------------------------------------ diagnostics

    def diagnostics(self) -> str:
        """Everything useful when a wait_for times out."""
        parts = [f"tmux socket: {self.sandbox.socket}  session: {self.session}"]
        parts.append(f"windows: {self.sandbox.list_windows(self.session)}")
        for window in self.sandbox.list_windows(self.session):
            content = self.sandbox.capture_pane(self.session, window, 40).strip()
            parts.append(f"--- pane {window} (last 40 lines) ---\n{content}")
        parts.append(f"--- registry ---\n{json.dumps(self.sessions(), indent=2)[:4000]}")
        state = self.daemon_state()
        parts.append(f"--- daemon state ---\n{json.dumps(state, indent=2)[:4000]}")
        for log in self.log_dir.glob("*.log"):
            tail = "\n".join(log.read_text().splitlines()[-30:])
            parts.append(f"--- {log.name} (tail) ---\n{tail}")
        return "\n".join(parts)
