"""
E2E Test: opencode2 backend via the mock TUI.

The opencode2 variant of the backend matrix: `overcode launch -B
opencode2` must drive the v2 CLI grammar end to end — launch, pane chrome
detection through OPENCODE2_PATTERNS, status flips, and the live-verified
permission dialog keys (bare Enter approves the preselected "Allow
once"). The fake TUI is tests/mock_opencode2.py, substituted via
OPENCODE2_COMMAND by the clean_test_env fixture, so no real opencode2
binary (or API key) is needed.
"""

import subprocess
import time
from pathlib import Path

import pytest

from overcode.launcher import AgentLauncher
from overcode.status_detector_factory import StatusDetectorDispatcher

from conftest import get_tmux_pane_content


class TestOpencode2Backend:
    """Launch/status/permission flows for the opencode2 backend."""

    @pytest.fixture(autouse=True)
    def setup(self, clean_test_env, overcode_cli, tmp_path):
        """Store fixtures; give the agent its own work directory.

        opencode2's prepare_launch installs the telemetry plugin into
        <dir>/.opencode/plugins/, so the launch must target a temp
        directory rather than the repo. ``tmp_path`` is per-test and
        pytest manages its retention, so no directory leaks.
        """
        self.env = clean_test_env
        self.cli = overcode_cli
        self.session = clean_test_env["session_name"]
        self.socket = clean_test_env["tmux_socket"]
        self.work_dir = tmp_path

    def _launch(self, name: str, scenario: str):
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = scenario
        return subprocess.run(
            ["python", "-m", "overcode.cli", "launch",
             "--name", name,
             "--session", self.session,
             "--backend", "opencode2",
             "--directory", str(self.work_dir)],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

    def _pane(self, name: str) -> str:
        # Resolve the full window name (windows are <name>-<session4>) so
        # the capture never depends on tmux prefix-match semantics.
        return get_tmux_pane_content(
            self.socket, self.session, self._window(name)
        )

    def _window(self, name: str) -> str:
        """Resolve the full tmux window name (``<name>-<session4>``)."""
        result = subprocess.run(
            ["tmux", "-L", self.socket, "list-windows", "-t", self.session,
             "-F", "#{window_name}"],
            capture_output=True,
            text=True,
        )
        for window in result.stdout.splitlines():
            if window.startswith(f"{name}-"):
                return window
        raise AssertionError(
            f"no window starting with {name!r}: {result.stdout!r}"
        )

    def _wait_for_status(self, name: str, expected: str,
                         timeout: float = 15.0):
        """Poll the agent's detected status in-process.

        Uses StatusDetectorDispatcher so the session is scraped with its
        own backend's patterns — an opencode2 agent against
        OPENCODE2_PATTERNS (the claude-default StatusDetector would read
        the v2 pane with the wrong grammar). The dispatcher honours
        OVERCODE_TMUX_SOCKET via the tmux layer.

        Returns the (status, activity) pair once status matches.
        """
        launcher = AgentLauncher(self.session)
        detector = StatusDetectorDispatcher(self.session)
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            sessions = launcher.list_sessions(
                detect_terminated=False, kill_untracked=False
            )
            session = next(
                (s for s in sessions if s.name == name), None
            )
            if session is None:
                raise AssertionError(f"agent {name!r} not in state")
            status, activity, _content = detector.detect_status(session)
            last = f"{status} ({activity})"
            if status == expected:
                return status, activity
            time.sleep(0.5)
        raise AssertionError(
            f"{name!r} never reached {expected!r}; last: {last}; "
            f"pane: {self._pane(name)}"
        )

    def _cli(self, *args, env_extra=None, timeout: int = 30):
        """Run an overcode CLI command against the test session."""
        env = self.env["env"].copy()
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            ["python", "-m", "overcode.cli", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

    def test_launch_creates_window_with_v2_chrome(self):
        result = self._launch("oc2-idle", "oc2_launch_and_idle")
        assert result.returncode == 0, f"Launch failed: {result.stderr}"

        # The mock types its frames with delays, but render time varies
        # under load — poll for the chrome instead of a fixed sleep.
        deadline = time.monotonic() + 10
        content = ""
        while time.monotonic() < deadline:
            try:
                content = self._pane("oc2-idle")
            except AssertionError:
                # Window not created yet — keep polling.
                time.sleep(0.25)
                continue
            if "┃" in content and "shift+tab agents" in content:
                break
            time.sleep(0.25)

        # v2 chrome, from the fixtures_opencode2_panes corpus: the ┃ box,
        # the ╹▀ bottom border, v2's shift+tab agents hint.
        for marker in ("┃", "╹▀", "ctrl+p commands", "shift+tab agents"):
            assert marker in content, \
                f"v2 chrome marker {marker!r} missing. Content: {content}"

    def test_launched_agent_reports_waiting_user(self):
        result = self._launch("oc2-status", "oc2_launch_and_idle")
        assert result.returncode == 0, f"Launch failed: {result.stderr}"

        status, _activity = self._wait_for_status(
            "oc2-status", "waiting_user"
        )
        assert status == "waiting_user"

    def test_scripted_turn_completes_to_idle(self):
        result = self._launch("oc2-turn", "oc2_simple_response")
        assert result.returncode == 0, f"Launch failed: {result.stderr}"

        # The busy bar (esc interrupt) renders mid-scenario; the pane
        # settles once the turn completes. Poll the pane for the finished
        # transcript AND the settled idle footer — the mock renders each
        # step with its own delay, so a read can land between the
        # assistant footer and the idle box.
        deadline = time.time() + 15
        content = ""
        while time.time() < deadline:
            content = self._pane("oc2-turn")
            if "93.0 tok/s" in content and "ctrl+p commands" in content:
                break
            time.sleep(0.5)
        assert "93.0 tok/s" in content, \
            f"Scripted turn never completed. Content: {content}"
        # The finished transcript carries v2's tool glyphs (no v1 ▣/✱)
        # and the idle footer with inline context+cost.
        assert "→" in content
        assert "ctrl+p commands" in content

        status, _activity = self._wait_for_status(
            "oc2-turn", "waiting_user"
        )
        assert status == "waiting_user"

    def test_permission_dialog_detected_and_enter_approves(self):
        result = self._launch("oc2-perm", "oc2_permission_bash")
        assert result.returncode == 0, f"Launch failed: {result.stderr}"

        # Dialog: "△ Permission required" (v2 says "Always allow").
        deadline = time.time() + 15
        content = ""
        while time.time() < deadline:
            content = self._pane("oc2-perm")
            if "Permission required" in content:
                break
            time.sleep(0.5)
        assert "Permission required" in content, \
            f"Permission dialog never appeared. Content: {content}"
        assert "Always allow" in content
        assert "Allow always" not in content

        # The detector reads the dialog as waiting_user with a
        # Permission activity (OPENCODE2_PATTERNS' permission branch).
        status, activity = self._wait_for_status("oc2-perm", "waiting_user")
        assert activity.startswith("Permission:"), activity

        # Bare Enter approves the preselected "Allow once" (verified live
        # against v0.0.0-dev-19272): the dialog clears, the command runs.
        subprocess.run(
            ["tmux", "-L", self.socket, "send-keys",
             "-t", f"{self.session}:{self._window('oc2-perm')}", "Enter"],
            capture_output=True,
        )

        deadline = time.time() + 15
        while time.time() < deadline:
            content = self._pane("oc2-perm")
            if "Command exited with code 0." in content:
                break
            time.sleep(0.5)
        assert "Command exited with code 0." in content, \
            f"Enter did not approve the dialog. Content: {content}"

        # Pane settles back at the idle box.
        status, _activity = self._wait_for_status(
            "oc2-perm", "waiting_user"
        )
        assert status == "waiting_user"

    def test_restart_graceful_exits_and_relaunches(self):
        """restart drives the verified /exit sequence and relaunches.

        The backend's graceful_exit_keys (Escape, Escape, /exit+Enter,
        bare Enter — the autocomplete consumes the first Enter in the
        real TUI) arrive at the mock as one readline line prefixed with
        the two ESC bytes; the mock's /exit handler matches through
        them and exits via its exit label (printing its exit message)
        rather than the engine's unmatched-input fallback. The relaunch
        command typed afterwards boots a fresh mock in the same window
        with the same scenario.
        """
        result = self._launch("oc2-rs", "oc2_launch_and_idle")
        assert result.returncode == 0, f"Launch failed: {result.stderr}"
        assert self._wait_for_status("oc2-rs", "waiting_user")[0] == \
            "waiting_user"

        window = self._window("oc2-rs")
        result = self._cli(
            "restart", "oc2-rs", "--session", self.session,
            env_extra={"MOCK_SCENARIO": "oc2_launch_and_idle"},
        )
        assert result.returncode == 0, f"Restart failed: {result.stderr}"

        # Same window, a freshly booted mock.
        assert self._window("oc2-rs") == window
        assert self._wait_for_status("oc2-rs", "waiting_user")[0] == \
            "waiting_user"
        content = self._pane("oc2-rs")
        assert "ctrl+p commands" in content, \
            f"Relaunched mock never rendered its chrome. Content: {content}"

    def test_kill_removes_window_and_state(self):
        result = self._launch("oc2-kill", "oc2_launch_and_idle")
        assert result.returncode == 0, f"Launch failed: {result.stderr}"
        assert self._window("oc2-kill")

        result = self._cli("kill", "oc2-kill", "--session", self.session)
        assert result.returncode == 0, f"Kill failed: {result.stderr}"
        time.sleep(1)

        tmux_result = subprocess.run(
            ["tmux", "-L", self.socket, "list-windows", "-t", self.session,
             "-F", "#{window_name}"],
            capture_output=True,
            text=True,
        )
        assert "oc2-kill" not in tmux_result.stdout, \
            f"Window survived kill. Windows: {tmux_result.stdout}"

        launcher = AgentLauncher(self.session)
        sessions = launcher.list_sessions(
            detect_terminated=False, kill_untracked=False
        )
        assert not any(s.name == "oc2-kill" for s in sessions), \
            "Session survived kill in state"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
