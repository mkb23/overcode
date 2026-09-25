"""The plain-shell backend (#496): argv, gestures, status and fleet gating."""

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.backends import (
    BackendCapability,
    LaunchSpec,
    get_backend,
    is_shell_session,
    list_backends,
)
from overcode.backends.shell import (
    KNOWN_SHELLS,
    SHELL_PATTERNS,
    ShellBackend,
    ShellStatusDetector,
    resolve_shell,
)
from overcode.doctor import VERDICT_OK, find_agent_process
from overcode.stats_reader import NullStatsReader, stats_reader_for_session
from overcode.status_constants import STATUS_RUNNING, STATUS_TERMINATED, STATUS_WAITING_USER
from overcode.status_detector_factory import StatusDetectorDispatcher, create_status_detector
from overcode.status_patterns import extract_from_pane
from overcode.supervisor_daemon_core import filter_non_green_sessions
from overcode.tmux_utils import PaneInfo


@pytest.fixture
def backend():
    return get_backend("shell")


@pytest.fixture(autouse=True)
def zsh_login_shell():
    with patch.dict(os.environ, {"SHELL": "/bin/zsh"}):
        yield


class TestRegistry:
    def test_listed(self):
        assert "shell" in list_backends()

    def test_resolves_to_the_adapter(self, backend):
        assert isinstance(backend, ShellBackend)
        assert backend.display_name == "Shell"

    def test_no_capabilities(self, backend):
        assert backend.capabilities == BackendCapability.NONE

    def test_is_shell_session(self):
        assert is_shell_session(SimpleNamespace(backend="shell"))
        assert not is_shell_session(SimpleNamespace(backend="claude-code"))
        assert not is_shell_session(SimpleNamespace())


class TestShellResolution:
    def test_uses_dollar_shell(self, backend):
        assert resolve_shell() == "/bin/zsh"
        assert backend.binary == "/bin/zsh"
        assert backend.executable() == "/bin/zsh"

    def test_falls_back_to_bash(self, backend):
        with patch.dict(os.environ, {"SHELL": ""}):
            assert backend.executable() == "bash"

    def test_process_basenames_lead_with_the_configured_shell(self, backend):
        names = backend.process_basenames
        assert names[0] == "zsh"
        assert set(names) == KNOWN_SHELLS


class TestBuildCommand:
    def test_execs_the_shell(self, backend):
        assert backend.build_command(LaunchSpec()) == ["exec", "/bin/zsh"]

    def test_extra_args_go_to_the_shell(self, backend):
        spec = LaunchSpec(extra_args=["-l", "-o vi"])
        assert backend.build_command(spec) == ["exec", "/bin/zsh", "-l", "-o", "vi"]

    def test_agent_knobs_are_ignored(self, backend):
        spec = LaunchSpec(
            model="sonnet", agent="reviewer", allowed_tools="Bash",
            permissiveness_mode="bypass", dangerously_skip_permissions=True,
            prescribed_session_id="abc", resume_session_id="def",
        )
        assert backend.build_command(spec) == ["exec", "/bin/zsh"]
        assert backend.env_prefix(spec) == {}


class TestGestures:
    def test_graceful_exit_interrupts_without_closing_the_window(self, backend):
        # `exit` would close the window restart/rename relaunch into.
        keys = [p.keys for p in backend.graceful_exit_keys()]
        assert keys == ["C-c"]

    def test_no_permission_dialogs_to_answer(self, backend):
        assert backend.approve_keys() == []
        assert backend.reject_keys() == []
        assert backend.startup_dialog_rules() == []

    def test_clear_clears_the_screen(self, backend):
        assert [p.keys for p in backend.clear_conversation_keys()] == ["clear"]

    def test_initial_prompt_is_typed_ahead(self, backend):
        assert backend.prompt_ready_line("$ OVERCODE_SESSION_NAME=x exec /bin/zsh")
        assert not backend.prompt_ready_line("")


class TestTelemetry:
    def test_null_stats_reader(self):
        reader = stats_reader_for_session(SimpleNamespace(backend="shell"))
        assert isinstance(reader, NullStatsReader)

    def test_health_verdict_is_ok(self, backend):
        verdict, _ = backend.health_verdict("/bin/zsh")
        assert verdict == VERDICT_OK

    def test_pane_root_is_found_as_the_shell_process(self, backend):
        # The shell is exec'd, so it is the pane's own process, not a child.
        pid, argv = find_agent_process(100, {}, {100: "/bin/zsh"}, backend.process_basenames)
        assert (pid, argv) == (100, "/bin/zsh")

    def test_agent_backends_still_skip_the_login_shell_root(self):
        children = {100: [101]}
        argv = {100: "-zsh", 101: "claude --settings {}"}
        assert find_agent_process(100, children, argv) == (101, "claude --settings {}")


class TestPatterns:
    def test_shell_output_lights_up_no_agent_columns(self):
        content = "⏺ 3 bashes · 2 local agents · 1 monitor\n⏵⏵ auto-accept on\n(running)"
        extracted = extract_from_pane(content, SHELL_PATTERNS)
        assert extracted.background_bash_count == 0
        assert extracted.live_subagent_count == 0
        assert extracted.active_monitor_count == 0


class FakeTmux:
    """Pane text plus a settable foreground command, as list-panes reports it."""

    def __init__(self, command="zsh", content="~/proj %"):
        self.command = command
        self.content = content
        self.listings = 0

    def capture_pane(self, session, window, lines=100):
        return self.content

    def list_panes(self, session):
        self.listings += 1
        if self.command is None:
            return None
        return {
            "sh-1": PaneInfo("sh-1", 1, 0, 0, 0, 0, 0, self.command, 0),
        }


def _session():
    return SimpleNamespace(id="sid", name="sh", tmux_window="sh-1", backend="shell")


def _detector(tmux):
    detector = ShellStatusDetector("agents", tmux=tmux, patterns=SHELL_PATTERNS)
    detector.LISTING_TTL_SECONDS = 0  # every call re-lists in tests
    return detector


class TestShellStatusDetector:
    def test_idle_at_the_prompt_is_waiting(self):
        status, activity, _ = _detector(FakeTmux("zsh")).detect_status(_session())
        assert status == STATUS_WAITING_USER
        assert activity == "Shell prompt"

    def test_login_shell_dash_is_idle(self):
        status, _, _ = _detector(FakeTmux("-zsh")).detect_status(_session())
        assert status == STATUS_WAITING_USER

    def test_foreground_command_is_running(self):
        status, activity, _ = _detector(FakeTmux("vim")).detect_status(_session())
        assert status == STATUS_RUNNING
        assert activity == "Running: vim"

    def test_shell_prompt_text_is_not_termination(self):
        # The generic detector reads a bare "$" prompt as "agent exited".
        tmux = FakeTmux("bash", content="user@host:~$")
        status, _, _ = _detector(tmux).detect_status(_session())
        assert status == STATUS_WAITING_USER

    def test_command_not_found_is_not_a_spawn_failure(self):
        tmux = FakeTmux("zsh", content="zsh: command not found: gti\n~ %")
        status, activity, _ = _detector(tmux).detect_status(_session())
        assert (status, activity) == (STATUS_WAITING_USER, "Shell prompt")

    def test_window_gone_is_terminated(self):
        tmux = FakeTmux("zsh", content=None)
        status, _, _ = _detector(tmux).detect_status(_session())
        assert status == STATUS_TERMINATED

    def test_no_listing_reads_as_waiting(self):
        status, _, _ = _detector(FakeTmux(None)).detect_status(_session())
        assert status == STATUS_WAITING_USER

    def test_listing_is_cached_across_rows(self):
        tmux = FakeTmux("zsh")
        detector = ShellStatusDetector("agents", tmux=tmux, patterns=SHELL_PATTERNS)
        for _ in range(5):
            detector.detect_status(_session())
        assert tmux.listings == 1


class TestDetectorWiring:
    def test_dispatcher_uses_the_shell_detector(self):
        tmux = FakeTmux("make")
        dispatcher = StatusDetectorDispatcher("agents", tmux=tmux)
        status, activity, _ = dispatcher.detect_status(_session())
        assert (status, activity) == (STATUS_RUNNING, "Running: make")
        assert dispatcher.resolve_mode(_session()) == "polling"

    def test_create_status_detector_by_backend(self):
        detector = create_status_detector("agents", tmux=FakeTmux(), backend_name="shell")
        assert isinstance(detector, ShellStatusDetector)

    def test_other_backends_keep_the_polling_detector(self):
        detector = create_status_detector("agents", tmux=FakeTmux(), backend_name="codex")
        assert not isinstance(detector, ShellStatusDetector)


class TestSupervisor:
    def test_idle_shell_is_never_supervised(self):
        sessions = [
            {"name": "sh", "current_status": STATUS_WAITING_USER, "backend": "shell"},
            {"name": "cc", "current_status": STATUS_WAITING_USER, "backend": "claude-code"},
        ]
        assert [s["name"] for s in filter_non_green_sessions(sessions)] == ["cc"]


class TestLaunch:
    def _launcher(self, tmp_path):
        from overcode.launcher import AgentLauncher
        from overcode.mocks import MockTmux
        from overcode.session_manager import SessionManager
        from overcode.tmux_manager import TmuxManager

        tmux = MockTmux()
        manager = TmuxManager("agents", tmux=tmux)
        sessions = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        return AgentLauncher("agents", tmux_manager=manager, session_manager=sessions), tmux

    def test_launch_line_execs_the_shell_unwrapped(self, tmp_path):
        launcher, tmux = self._launcher(tmp_path)
        with patch("overcode.launcher.require_tmux"), \
             patch("overcode.launcher.require_agent_cli"), \
             patch("overcode.launcher.get_default_standing_instructions", return_value="be good"), \
             patch("overcode.config.get_new_agent_defaults", return_value={"wrapper": "devcontainer"}), \
             patch.dict(os.environ, {"OVERCODE_SESSION_NAME": ""}):
            session = launcher.launch(name="term", start_directory=str(tmp_path), backend="shell")
        assert session is not None
        assert session.backend == "shell"
        assert session.wrapper is None
        assert session.standing_instructions == ""
        assert session.command == ["exec", "/bin/zsh"]
        line = tmux.sent_keys[-1][2]
        assert line.endswith(" exec /bin/zsh")
        assert "OVERCODE_SESSION_NAME=term" in line
        assert "OVERCODE_BACKEND=shell" in line
