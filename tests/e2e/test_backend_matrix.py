"""
E2E test: the non-Claude backend matrix via each backend's mock TUI.

One parametrized suite, four backends (opencode, codex, grok, hermes).
opencode2 has its own file (tests/e2e/test_opencode2_backend.py) — this one
gives the other four the same coverage: `overcode launch -B <backend>`
must drive that backend's CLI grammar end to end — launch, pane chrome
detection through the backend's own status patterns, the permission
dialog surfacing as `waiting_user` with a `Permission:` activity, the
backend-resolved `approve` / `reject` gestures landing on the mock's
dialog with the right outcome, restart (graceful-exit keys + relaunch in
the same window) and kill.

The mocks (tests/mock_<backend>.py) are substituted via <BACKEND>_COMMAND
by the clean_test_env fixture, so no real CLI, API key or subscription is
needed. Their chrome is copied from the real-capture corpora under
tests/fixtures_<backend>_panes/, so a detector that passes here passes
against that captured build of the real TUI — and *only* that build; see
docs/backends.md for the live-verification story.
"""

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

from overcode.launcher import AgentLauncher
from overcode.status_detector_factory import StatusDetectorDispatcher

from conftest import get_tmux_pane_content


@dataclass(frozen=True)
class BackendSpec:
    """What one backend's mock looks like from the outside."""

    backend: str
    idle_scenario: str
    response_scenario: str
    permission_scenario: str
    # Substrings every fresh/idle pane of this mock shows (from the corpus).
    idle_markers: Tuple[str, ...]
    # Substrings of the pane once a turn has settled — opencode's footer
    # swaps the fresh "tab agents" hint for context+cost, so it differs.
    settled_markers: Tuple[str, ...]
    # Substrings that identify the permission dialog on screen.
    dialog_markers: Tuple[str, ...]
    # A substring of the dialog that must NOT be there (the other backends'
    # spelling of the same option) — guards against chrome leaking across
    # mocks and against a detector matching the wrong backend's dialog.
    dialog_absent: Tuple[str, ...]
    # What the mock prints once `approve` / `reject` lands on the dialog.
    approve_outcome: str
    reject_outcome: str
    # A substring of the finished scripted turn (response_scenario).
    turn_marker: str
    extra_env: Dict[str, str] = field(default_factory=dict)


SPECS: List[BackendSpec] = [
    BackendSpec(
        backend="opencode",
        idle_scenario="oc_launch_and_idle",
        response_scenario="oc_simple_response",
        permission_scenario="oc_permission_bash",
        idle_markers=("┃", "╹▀", "ctrl+p commands", "tab agents"),
        settled_markers=("┃", "╹▀", "ctrl+p commands", "$0.00"),
        dialog_markers=("Permission required", "Allow once", "Allow always"),
        # v2 renamed this option; the v1 mock must keep v1's spelling.
        dialog_absent=("Always allow",),
        approve_outcome="The command executed successfully, and the output is: hello.",
        reject_outcome="I'll find another approach that doesn't need the shell.",
        turn_marker="Build · GPT-4o mini",
    ),
    BackendSpec(
        backend="codex",
        idle_scenario="cx_launch_and_idle",
        response_scenario="cx_simple_response",
        permission_scenario="cx_permission_command",
        idle_markers=("› Ask Codex to do anything", "gpt-5.6-sol"),
        settled_markers=("› Ask Codex to do anything", "gpt-5.6-sol"),
        dialog_markers=(
            "Would you like to run the following command?",
            "Yes, proceed",
        ),
        dialog_absent=("Allow once", "Deny"),
        approve_outcome="• Ran touch ~/codex_probe_outside_test.txt",
        reject_outcome="Conversation interrupted",
        turn_marker="• Ran",
    ),
    BackendSpec(
        backend="grok",
        idle_scenario="gk_launch_and_idle",
        response_scenario="gk_simple_response",
        permission_scenario="gk_permission_command",
        # A fresh grok pane is the welcome banner + telemetry opt-in + the
        # empty box with its "[stable]" tail; the Shift+Tab/Ctrl+x hint bar
        # only appears once a turn has settled (corpus: idle_fresh.txt vs
        # idle_after_response.txt).
        idle_markers=("Grok Build", "[stable]", "❯"),
        settled_markers=("Shift+Tab:mode", "Ctrl+x:shortcuts"),
        dialog_markers=(
            "Yes, and don't ask again for anything (always-approve mode)",
            "Yes, proceed",
            "No, reject",
        ),
        dialog_absent=("Allow once", "Deny"),
        # The digit-2 gesture, NOT the preselected always-approve option.
        approve_outcome="◆ Ran echo hello",
        reject_outcome="Turn cancelled by user",
        turn_marker="◆",
    ),
    BackendSpec(
        backend="hermes",
        idle_scenario="hm_launch_and_idle",
        response_scenario="hm_simple_response",
        permission_scenario="hm_permission_command",
        idle_markers=("☤", "❯ Draft a reply to the last email in my inbox"),
        settled_markers=("☤", "❯ Draft a reply to the last email in my inbox"),
        dialog_markers=("Dangerous Command", "1. Allow once", "4. Deny"),
        dialog_absent=("Yes, proceed", "Allow always"),
        approve_outcome="finished",
        reject_outcome="You denied this command",
        turn_marker="☤ Hermes",
    ),
]


@pytest.fixture(params=SPECS, ids=[s.backend for s in SPECS])
def spec(request) -> BackendSpec:
    return request.param


class TestBackendMatrix:
    """Launch/status/permission/restart/kill flows, once per backend."""

    @pytest.fixture(autouse=True)
    def setup(self, clean_test_env, overcode_cli, tmp_path, spec):
        """Store fixtures; give the agent its own work directory.

        opencode's prepare_launch installs a telemetry plugin into
        <dir>/.opencode/plugins/, so launches target a per-test temp
        directory rather than the repo. grok's and hermes's global
        footprints are redirected by GROK_HOME / HERMES_HOME in the
        clean_test_env fixture.
        """
        self.env = clean_test_env
        self.cli = overcode_cli
        self.session = clean_test_env["session_name"]
        self.socket = clean_test_env["tmux_socket"]
        self.work_dir = tmp_path
        self.spec = spec

    # -- helpers ---------------------------------------------------------

    def _launch(self, name: str, scenario: str):
        env = self.env["env"].copy()
        env["MOCK_SCENARIO"] = scenario
        env.update(self.spec.extra_env)
        return subprocess.run(
            ["python", "-m", "overcode.cli", "launch",
             "--name", name,
             "--session", self.session,
             "--backend", self.spec.backend,
             "--directory", str(self.work_dir)],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
            cwd=Path(__file__).parent.parent.parent,
        )

    def _cli(self, *args, env_extra=None, timeout: int = 30):
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

    def _pane(self, name: str) -> str:
        return get_tmux_pane_content(self.socket, self.session, self._window(name))

    def _widen(self, name: str) -> None:
        """Give the window a real-terminal width.

        A detached tmux session defaults to 80 columns, and several mocks
        (grok's box footer, opencode's context+cost footer) reproduce
        120-column captures: at 80 columns tmux wraps them mid-marker and
        a substring like "ctrl+p commands" is split across two lines.
        """
        subprocess.run(
            ["tmux", "-L", self.socket, "resize-window",
             "-t", f"{self.session}:{self._window(name)}", "-x", "160", "-y", "50"],
            capture_output=True,
        )

    def _wait_for_pane(self, name: str, *markers: str, timeout: float = 15.0) -> str:
        """Poll the pane until every marker is on screen; return the content."""
        deadline = time.monotonic() + timeout
        content = ""
        widened = False
        while time.monotonic() < deadline:
            try:
                if not widened:
                    self._widen(name)
                    widened = True
                content = self._pane(name)
            except AssertionError:
                time.sleep(0.25)
                continue
            if all(m in content for m in markers):
                return content
            time.sleep(0.25)
        raise AssertionError(
            f"{name!r} never showed {markers!r}. Content:\n{content}"
        )

    def _wait_for_status(self, name: str, expected: str, timeout: float = 15.0):
        """Poll the agent's detected status through the backend dispatcher.

        StatusDetectorDispatcher scrapes each session with its own
        backend's patterns, so a grok pane is read with grok's grammar,
        not the claude-default detector's.
        """
        launcher = AgentLauncher(self.session)
        detector = StatusDetectorDispatcher(self.session)
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            sessions = launcher.list_sessions(
                detect_terminated=False, kill_untracked=False
            )
            session = next((s for s in sessions if s.name == name), None)
            if session is None:
                raise AssertionError(f"agent {name!r} not in state")
            status, activity, _content = detector.detect_status(session)
            last = f"{status} ({activity})"
            if status == expected:
                return status, activity
            time.sleep(0.5)
        raise AssertionError(
            f"{name!r} never reached {expected!r}; last: {last}; "
            f"pane:\n{self._pane(name)}"
        )

    # -- tests -----------------------------------------------------------

    def test_launch_records_backend_and_renders_chrome(self):
        name = f"{self.spec.backend}-idle"
        result = self._launch(name, self.spec.idle_scenario)
        assert result.returncode == 0, f"Launch failed: {result.stderr}"
        assert f"Backend: {self.spec.backend}" in result.stdout

        content = self._wait_for_pane(name, *self.spec.idle_markers)
        for absent in self.spec.dialog_absent:
            assert absent not in content

        # The session record carries the backend, so every later
        # per-session dispatch (detector, gestures, stats) resolves to it.
        sessions = AgentLauncher(self.session).list_sessions(
            detect_terminated=False, kill_untracked=False
        )
        session = next(s for s in sessions if s.name == name)
        assert session.backend == self.spec.backend

    def test_launched_agent_reports_waiting_user(self):
        name = f"{self.spec.backend}-status"
        result = self._launch(name, self.spec.idle_scenario)
        assert result.returncode == 0, f"Launch failed: {result.stderr}"
        status, _activity = self._wait_for_status(name, "waiting_user")
        assert status == "waiting_user"

    def test_scripted_turn_settles_to_idle(self):
        name = f"{self.spec.backend}-turn"
        result = self._launch(name, self.spec.response_scenario)
        assert result.returncode == 0, f"Launch failed: {result.stderr}"
        self._wait_for_pane(name, self.spec.turn_marker, *self.spec.settled_markers)
        status, _activity = self._wait_for_status(name, "waiting_user")
        assert status == "waiting_user"

    def test_permission_dialog_detected_and_approve_gesture_lands(self):
        name = f"{self.spec.backend}-perm"
        result = self._launch(name, self.spec.permission_scenario)
        assert result.returncode == 0, f"Launch failed: {result.stderr}"

        content = self._wait_for_pane(name, *self.spec.dialog_markers)
        for absent in self.spec.dialog_absent:
            assert absent not in content, absent

        status, activity = self._wait_for_status(name, "waiting_user")
        assert activity.startswith("Permission:"), activity

        # The gesture, not a raw key: overcode resolves it through the
        # backend (Enter for opencode/codex, bare digit 2 for grok,
        # 1 + Enter for hermes) — the mock's menu tells us which option
        # actually fired by what it prints next.
        result = self._cli("send", name, "approve", "--session", self.session)
        assert result.returncode == 0, f"send approve failed: {result.stderr}"
        content = self._wait_for_pane(name, self.spec.approve_outcome)
        assert self.spec.reject_outcome not in content

        status, _activity = self._wait_for_status(name, "waiting_user")
        assert status == "waiting_user"

    def test_reject_gesture_declines_the_tool_call(self):
        name = f"{self.spec.backend}-rej"
        result = self._launch(name, self.spec.permission_scenario)
        assert result.returncode == 0, f"Launch failed: {result.stderr}"
        self._wait_for_pane(name, *self.spec.dialog_markers)
        self._wait_for_status(name, "waiting_user")

        result = self._cli("send", name, "reject", "--session", self.session)
        assert result.returncode == 0, f"send reject failed: {result.stderr}"
        content = self._wait_for_pane(name, self.spec.reject_outcome)
        assert self.spec.approve_outcome not in content

        status, _activity = self._wait_for_status(name, "waiting_user")
        assert status == "waiting_user"

    def test_restart_graceful_exits_and_relaunches_in_place(self):
        name = f"{self.spec.backend}-rs"
        result = self._launch(name, self.spec.idle_scenario)
        assert result.returncode == 0, f"Launch failed: {result.stderr}"
        self._wait_for_status(name, "waiting_user")
        window = self._window(name)

        result = self._cli(
            "restart", name, "--session", self.session,
            env_extra={"MOCK_SCENARIO": self.spec.idle_scenario},
        )
        assert result.returncode == 0, f"Restart failed: {result.stderr}"

        # Same window, a freshly booted mock rendering its idle chrome.
        assert self._window(name) == window
        self._wait_for_pane(name, *self.spec.idle_markers)
        assert self._wait_for_status(name, "waiting_user")[0] == "waiting_user"

    def test_kill_removes_window_and_state(self):
        name = f"{self.spec.backend}-kill"
        result = self._launch(name, self.spec.idle_scenario)
        assert result.returncode == 0, f"Launch failed: {result.stderr}"
        assert self._window(name)

        result = self._cli("kill", name, "--session", self.session)
        assert result.returncode == 0, f"Kill failed: {result.stderr}"
        time.sleep(1)

        windows = subprocess.run(
            ["tmux", "-L", self.socket, "list-windows", "-t", self.session,
             "-F", "#{window_name}"],
            capture_output=True,
            text=True,
        ).stdout
        assert name not in windows, f"Window survived kill. Windows: {windows}"

        sessions = AgentLauncher(self.session).list_sessions(
            detect_terminated=False, kill_untracked=False
        )
        assert not any(s.name == name for s in sessions), \
            "Session survived kill in state"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
