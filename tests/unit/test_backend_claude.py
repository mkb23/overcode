"""Golden-argv tests for the Claude Code backend.

The expected strings below were captured from the pre-refactor
`ClaudeLauncher._build_claude_command` / `_build_launch_cmd_str`. They are
frozen on purpose: any diff here is a change to what overcode actually
execs, not a cosmetic refactor.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.backends import (
    DEFAULT_BACKEND,
    BackendCapability,
    LaunchSpec,
    UnknownBackendError,
    get_backend,
    list_backends,
    register_backend,
    supports,
    unregister_backend,
)
from overcode.backends.claude_code import ClaudeCodeBackend
from overcode.launcher import ClaudeLauncher
from overcode.session_manager import Session, SessionManager


OVERCODE_BIN = "/usr/local/bin/overcode"

SAFE_SETTINGS = (
    '{"hooks": {"UserPromptSubmit": [{"matcher": "", "hooks": [{"type": "command", '
    '"command": "/usr/local/bin/overcode hook-handler"}]}], "PreToolUse": [{"matcher": "", '
    '"hooks": [{"type": "command", "command": "/usr/local/bin/overcode hook-handler"}]}], '
    '"PostToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": '
    '"/usr/local/bin/overcode hook-handler"}]}], "PostToolUseFailure": [{"matcher": "", '
    '"hooks": [{"type": "command", "command": "/usr/local/bin/overcode hook-handler"}]}], '
    '"Stop": [{"matcher": "", "hooks": [{"type": "command", "command": '
    '"/usr/local/bin/overcode hook-handler"}]}], "StopFailure": [{"matcher": "", "hooks": '
    '[{"type": "command", "command": "/usr/local/bin/overcode hook-handler"}]}], '
    '"PermissionRequest": [{"matcher": "", "hooks": [{"type": "command", "command": '
    '"/usr/local/bin/overcode hook-handler"}]}], "SessionEnd": [{"matcher": "", "hooks": '
    '[{"type": "command", "command": "/usr/local/bin/overcode hook-handler"}]}]}, '
    '"permissions": {"allow": ["Bash(overcode report *)", "Bash(overcode show *)", '
    '"Bash(overcode list *)", "Bash(overcode follow *)", "Bash(overcode kill *)", '
    '"Bash(overcode budget *)"]}}'
)

PUNCHY_SETTINGS = SAFE_SETTINGS.replace(
    '"Bash(overcode budget *)"]}}',
    '"Bash(overcode budget *)", "Bash(overcode launch *)", "Bash(overcode send *)", '
    '"Bash(overcode instruct *)"]}}',
)


def _make_session(**kwargs) -> Session:
    return Session(
        id="1", name="a", tmux_session="agents", tmux_window="w", command=[],
        start_directory=None, start_time="2026-08-20T00:00:00", **kwargs,
    )


@pytest.fixture
def backend():
    return ClaudeCodeBackend()


@pytest.fixture(autouse=True)
def pinned_overcode_bin():
    """Freeze the --settings hook command path and drop CLAUDE_COMMAND."""
    with patch(
        "overcode.backends.claude_code._resolve_overcode_bin", return_value=OVERCODE_BIN
    ), patch.dict(os.environ, {}, clear=False) as env:
        env.pop("CLAUDE_COMMAND", None)
        env.pop("MOCK_SCENARIO", None)
        yield


class TestGoldenArgv:
    """ClaudeCodeBackend.build_command output, frozen argv-by-argv."""

    def test_fresh_launch(self, backend):
        assert backend.build_command(LaunchSpec()) == [
            "claude", "--settings", SAFE_SETTINGS,
        ]

    def test_prescribed_session_id(self, backend):
        spec = LaunchSpec(prescribed_session_id="11111111-2222-3333-4444-555555555555")
        assert backend.build_command(spec) == [
            "claude",
            "--session-id", "11111111-2222-3333-4444-555555555555",
            "--settings", SAFE_SETTINGS,
        ]

    def test_resume(self, backend):
        spec = LaunchSpec(resume_session_id="abc-123")
        assert backend.build_command(spec) == [
            "claude", "--resume", "abc-123", "--settings", SAFE_SETTINGS,
        ]

    def test_fork(self, backend):
        spec = LaunchSpec(resume_session_id="abc-123", fork=True)
        assert backend.build_command(spec) == [
            "claude", "--resume", "abc-123", "--fork-session",
            "--settings", SAFE_SETTINGS,
        ]

    def test_resume_wins_over_prescribed_id(self, backend):
        spec = LaunchSpec(resume_session_id="abc-123", prescribed_session_id="ignored")
        assert "--session-id" not in backend.build_command(spec)

    def test_permissiveness_normal(self, backend):
        spec = LaunchSpec(permissiveness_mode="normal")
        assert backend.build_command(spec) == [
            "claude", "--settings", SAFE_SETTINGS,
        ]

    def test_permissiveness_permissive(self, backend):
        spec = LaunchSpec(permissiveness_mode="permissive")
        assert backend.build_command(spec) == [
            "claude", "--settings", SAFE_SETTINGS,
            "--permission-mode", "dontAsk",
        ]

    def test_permissiveness_bypass(self, backend):
        spec = LaunchSpec(permissiveness_mode="bypass")
        assert backend.build_command(spec) == [
            "claude", "--settings", SAFE_SETTINGS,
            "--dangerously-skip-permissions",
        ]

    def test_skip_permissions_flag(self, backend):
        spec = LaunchSpec(skip_permissions=True)
        assert backend.build_command(spec) == [
            "claude", "--settings", SAFE_SETTINGS,
            "--permission-mode", "dontAsk",
        ]

    def test_dangerously_skip_permissions_flag(self, backend):
        spec = LaunchSpec(dangerously_skip_permissions=True)
        assert backend.build_command(spec) == [
            "claude", "--settings", SAFE_SETTINGS,
            "--dangerously-skip-permissions",
        ]

    def test_model(self, backend):
        spec = LaunchSpec(model="opus")
        assert backend.build_command(spec) == [
            "claude", "--settings", SAFE_SETTINGS, "--model", "opus",
        ]

    def test_claude_agent(self, backend):
        spec = LaunchSpec(agent="reviewer")
        assert backend.build_command(spec) == [
            "claude", "--settings", SAFE_SETTINGS, "--agent", "reviewer",
        ]

    def test_allowed_tools(self, backend):
        spec = LaunchSpec(allowed_tools="Read,Grep")
        assert backend.build_command(spec) == [
            "claude", "--settings", SAFE_SETTINGS, "--allowedTools", "Read,Grep",
        ]

    def test_extra_args_are_shlex_split(self, backend):
        spec = LaunchSpec(extra_args=["--model haiku", "--effort low"])
        assert backend.build_command(spec) == [
            "claude", "--settings", SAFE_SETTINGS,
            "--model", "haiku", "--effort", "low",
        ]

    def test_punchy_perms(self, backend):
        spec = LaunchSpec(include_punchy_perms=True)
        assert backend.build_command(spec) == [
            "claude", "--settings", PUNCHY_SETTINGS,
        ]

    def test_full_matrix_ordering(self, backend):
        spec = LaunchSpec(
            prescribed_session_id="sid-1",
            permissiveness_mode="permissive",
            model="haiku",
            agent="rev",
            allowed_tools="Read,Write",
            extra_args=["--verbose"],
        )
        assert backend.build_command(spec) == [
            "claude",
            "--session-id", "sid-1",
            "--settings", SAFE_SETTINGS,
            "--permission-mode", "dontAsk",
            "--model", "haiku",
            "--agent", "rev",
            "--allowedTools", "Read,Write",
            "--verbose",
        ]


class TestClaudeCommandOverride:
    """CLAUDE_COMMAND swaps the binary — the e2e mock harness depends on it."""

    def test_override_used_for_fresh_launch(self, backend):
        with patch.dict(os.environ, {"CLAUDE_COMMAND": "/tmp/mock_claude.py"}):
            cmd = backend.build_command(LaunchSpec())
        assert cmd[0] == "/tmp/mock_claude.py"

    def test_override_used_for_resume(self, backend):
        with patch.dict(os.environ, {"CLAUDE_COMMAND": "/tmp/mock_claude.py"}):
            cmd = backend.build_command(LaunchSpec(resume_session_id="abc-123"))
        assert cmd[:3] == ["/tmp/mock_claude.py", "--resume", "abc-123"]

    def test_default_binary_is_claude(self, backend):
        assert backend.executable() == "claude"


class TestEnvPrefix:
    """Claude-specific env vars for the launch shell line."""

    def test_empty_by_default(self, backend):
        assert backend.env_prefix(LaunchSpec()) == {}

    def test_agent_teams(self, backend):
        assert backend.env_prefix(LaunchSpec(agent_teams=True)) == {
            "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
        }

    def test_bedrock_provider(self, backend):
        with patch(
            "overcode.config.get_bedrock_config", return_value={"region": "us-west-2"}
        ):
            env = backend.env_prefix(LaunchSpec(provider="bedrock"))
        assert env == {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": "us-west-2"}

    def test_web_provider_has_no_bedrock_vars(self, backend):
        assert backend.env_prefix(LaunchSpec(provider="web")) == {}


class TestLaunchCmdStr:
    """The rendered shell line: env prefix + wrapper/mock + argv."""

    def _launcher(self, tmp_path):
        from overcode.interfaces import MockTmux
        from overcode.tmux_manager import TmuxManager

        return ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=TmuxManager("agents", tmux=MockTmux()),
            session_manager=SessionManager(state_dir=tmp_path, skip_git_detection=True),
        )

    def test_plain(self, tmp_path, backend):
        launcher = self._launcher(tmp_path)
        spec = LaunchSpec(name="agent-1", session_id="sid", tmux_session="agents")
        cmd = launcher._build_launch_cmd_str(backend, spec, ["claude", "--foo"])
        assert cmd == (
            "OVERCODE_SESSION_NAME=agent-1 OVERCODE_SESSION_ID=sid "
            "OVERCODE_TMUX_SESSION=agents claude --foo"
        )

    def test_parent_linkage(self, tmp_path, backend):
        launcher = self._launcher(tmp_path)
        spec = LaunchSpec(
            name="child", session_id="sid", tmux_session="agents",
            parent_session_id="psid", parent_name="parent",
        )
        cmd = launcher._build_launch_cmd_str(backend, spec, ["claude"])
        assert cmd == (
            "OVERCODE_SESSION_NAME=child OVERCODE_SESSION_ID=sid "
            "OVERCODE_TMUX_SESSION=agents OVERCODE_PARENT_SESSION_ID=psid "
            "OVERCODE_PARENT_NAME=parent claude"
        )

    def test_agent_teams_and_bedrock_order(self, tmp_path, backend):
        launcher = self._launcher(tmp_path)
        spec = LaunchSpec(
            name="a", session_id="s", tmux_session="agents",
            agent_teams=True, provider="bedrock",
        )
        with patch(
            "overcode.config.get_bedrock_config", return_value={"region": "eu-west-1"}
        ):
            cmd = launcher._build_launch_cmd_str(backend, spec, ["claude"])
        assert cmd == (
            "OVERCODE_SESSION_NAME=a OVERCODE_SESSION_ID=s OVERCODE_TMUX_SESSION=agents "
            "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 CLAUDE_CODE_USE_BEDROCK=1 "
            "AWS_REGION=eu-west-1 claude"
        )

    def test_wrapper(self, tmp_path, backend):
        launcher = self._launcher(tmp_path)
        spec = LaunchSpec(
            name="a", session_id="s", tmux_session="agents",
            wrapper="/opt/w.sh", start_directory="/tmp/proj",
        )
        cmd = launcher._build_launch_cmd_str(backend, spec, ["claude"])
        assert cmd == (
            "OVERCODE_SESSION_NAME=a OVERCODE_SESSION_ID=s OVERCODE_TMUX_SESSION=agents "
            "OVERCODE_WRAPPER_DIR=/tmp/proj /opt/w.sh claude"
        )

    def test_wrapper_without_directory_quotes_empty(self, tmp_path, backend):
        launcher = self._launcher(tmp_path)
        spec = LaunchSpec(
            name="a", session_id="s", tmux_session="agents", wrapper="/opt/w.sh",
        )
        cmd = launcher._build_launch_cmd_str(backend, spec, ["claude"])
        assert "OVERCODE_WRAPPER_DIR=''" in cmd

    def test_mock_scenario_wins_over_wrapper(self, tmp_path, backend):
        launcher = self._launcher(tmp_path)
        spec = LaunchSpec(
            name="a", session_id="s", tmux_session="agents",
            wrapper="/opt/w.sh", start_directory="/tmp/proj",
            mock_scenario="idle",
        )
        cmd = launcher._build_launch_cmd_str(backend, spec, ["/tmp/mock_claude.py"])
        assert cmd == (
            "MOCK_SCENARIO=idle OVERCODE_SESSION_NAME=a OVERCODE_SESSION_ID=s "
            "OVERCODE_TMUX_SESSION=agents OVERCODE_WRAPPER_DIR=/tmp/proj "
            "python /tmp/mock_claude.py"
        )


class TestGestures:
    """Key gestures and startup-dialog handling."""

    def test_graceful_exit(self, backend):
        presses = backend.graceful_exit_keys()
        assert [(p.keys, p.enter, p.delay_after) for p in presses] == [
            ("C-c", False, 0.5),
            ("/exit", True, 0.0),
        ]

    def test_clear_conversation(self, backend):
        presses = backend.clear_conversation_keys()
        assert [(p.keys, p.enter) for p in presses] == [("/clear", True)]

    def test_approve_and_reject(self, backend):
        assert [(p.keys, p.enter) for p in backend.approve_keys()] == [("", True)]
        assert [(p.keys, p.enter) for p in backend.reject_keys()] == [("Escape", False)]

    def test_prompt_ready_chars(self, backend):
        assert backend.prompt_ready_chars() == {">", "›", "❯"}

    def test_startup_dialog_rules(self, backend):
        rules = backend.startup_dialog_rules()
        assert [r.marker for r in rules] == ["I trust this folder", "Yes, I accept"]

        trust = rules[0]
        assert [(p.keys, p.enter) for p in trust.presses] == [("", True)]
        assert trust.settle_seconds == 1.5

        perms = rules[1]
        assert [(p.keys, p.enter, p.delay_after) for p in perms.presses] == [
            ("Down", False, 0.3),
            ("", True, 0.0),
        ]
        assert perms.settle_seconds == 2.0


class TestCapabilities:
    """Claude Code has every capability overcode models."""

    def test_has_all_capabilities(self, backend):
        for cap in BackendCapability:
            if cap is BackendCapability.NONE:
                continue
            assert supports(backend, cap), cap

    def test_metadata(self, backend):
        assert backend.name == "claude-code"
        assert backend.binary == "claude"
        assert tuple(backend.version_args) == ("--version",)
        assert tuple(backend.process_basenames) == ("claude",)
        assert "claude.ai/claude-code" in backend.install_hint


class TestRegistry:

    def test_default_is_claude_code(self):
        assert get_backend().name == "claude-code"
        assert DEFAULT_BACKEND == "claude-code"

    def test_lookup_by_name(self):
        assert get_backend("claude-code").name == "claude-code"

    def test_empty_name_falls_back_to_default(self):
        assert get_backend("").name == "claude-code"
        assert get_backend(None).name == "claude-code"

    def test_unknown_backend_raises_with_known_names(self):
        with pytest.raises(UnknownBackendError) as exc:
            get_backend("nope")
        assert "nope" in str(exc.value)
        assert "claude-code" in str(exc.value)

    def test_list_backends(self):
        assert "claude-code" in list_backends()

    def test_register_and_unregister(self):
        class Double(ClaudeCodeBackend):
            name = "__test_double__"

        try:
            register_backend(Double())
            assert get_backend("__test_double__").name == "__test_double__"
            assert "__test_double__" in list_backends()
        finally:
            unregister_backend("__test_double__")
        with pytest.raises(UnknownBackendError):
            get_backend("__test_double__")

    def test_backends_are_singletons(self):
        assert get_backend("claude-code") is get_backend("claude-code")


class TestSessionBackendField:
    """Session.backend persists and round-trips."""

    def test_defaults_to_claude_code(self):
        session = _make_session()
        assert session.backend == "claude-code"

    def test_round_trip(self, tmp_path):
        sm = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        created = sm.create_session(
            name="a", tmux_session="agents", tmux_window="w", command=[],
            backend="claude-code",
        )
        reloaded = sm.get_session(created.id)
        assert reloaded.backend == "claude-code"

    def test_from_dict_tolerates_missing_backend(self):
        """Pre-backend sessions on disk deserialize to the default."""
        session = _make_session()
        data = session.to_dict()
        del data["backend"]
        assert Session.from_dict(data).backend == "claude-code"

    def test_from_dict_preserves_backend(self):
        session = _make_session()
        data = session.to_dict()
        data["backend"] = "opencode"
        assert Session.from_dict(data).backend == "opencode"

    def test_daemon_state_round_trip(self):
        from overcode.monitor_daemon_state import SessionDaemonState

        state = SessionDaemonState(name="a", backend="claude-code")
        assert SessionDaemonState.from_dict(state.to_dict()).backend == "claude-code"

    def test_daemon_state_tolerates_missing_backend(self):
        from overcode.monitor_daemon_state import SessionDaemonState

        data = SessionDaemonState(name="a").to_dict()
        del data["backend"]
        assert SessionDaemonState.from_dict(data).backend == "claude-code"


class TestLauncherDispatch:
    """The launcher resolves the backend from the Session."""

    def test_backend_for_defaults(self, tmp_path):
        from overcode.interfaces import MockTmux
        from overcode.tmux_manager import TmuxManager

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=TmuxManager("agents", tmux=MockTmux()),
            session_manager=SessionManager(state_dir=tmp_path, skip_git_detection=True),
        )
        session = _make_session()
        assert launcher.backend_for(session).name == "claude-code"

    def test_build_relaunch_command_matches_backend(self, tmp_path):
        from overcode.interfaces import MockTmux
        from overcode.tmux_manager import TmuxManager

        launcher = ClaudeLauncher(
            tmux_session="agents",
            tmux_manager=TmuxManager("agents", tmux=MockTmux()),
            session_manager=SessionManager(state_dir=tmp_path, skip_git_detection=True),
        )
        session = _make_session(permissiveness_mode="bypass")
        assert launcher.build_relaunch_command(session) == [
            "claude", "--settings", SAFE_SETTINGS, "--dangerously-skip-permissions",
        ]
