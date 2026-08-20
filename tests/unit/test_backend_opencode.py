"""Argv, gestures and capability gating for the opencode backend.

Every flag asserted here was checked against a live `opencode --help` at
v1.18.19 and, where behavioural, driven in a real tmux session. Notably
absent: `--permissions`, which the design research expected and v1.18.19
does not have.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.backends import (
    BackendCapability,
    LaunchSpec,
    get_backend,
    list_backends,
    supports,
)
from overcode.backends.opencode import OpencodeBackend, OpencodeNotFoundError
from overcode.doctor import VERDICT_OK
from overcode.exceptions import ClaudeNotFoundError
from overcode.stats_reader import NullStatsReader


@pytest.fixture
def backend():
    return get_backend("opencode")


class TestRegistry:
    def test_listed(self):
        assert "opencode" in list_backends()

    def test_resolves_to_the_adapter(self, backend):
        assert isinstance(backend, OpencodeBackend)

    def test_display_name(self, backend):
        assert backend.display_name == "opencode"

    def test_not_found_error_is_catchable_as_the_legacy_one(self):
        # The launcher's existing "agent CLI missing" except clause names
        # ClaudeNotFoundError; opencode's must be caught by it.
        assert issubclass(OpencodeNotFoundError, ClaudeNotFoundError)

    def test_process_basenames_cover_the_bun_shim(self, backend):
        # Homebrew/npm install `opencode` as a symlink to `opencode.exe`;
        # argv[0] is whichever the user invoked.
        assert set(backend.process_basenames) == {"opencode", "opencode.exe"}


class TestCapabilities:
    def test_resume_and_fork(self, backend):
        # `--session <id> --fork` verified against v1.18.19: it replays the
        # source conversation and creates a new "(fork #1)" session.
        assert supports(backend, BackendCapability.RESUME)
        assert supports(backend, BackendCapability.FORK)

    @pytest.mark.parametrize("capability", [
        BackendCapability.SESSION_ID_PRESCRIPTION,
        BackendCapability.HOOK_EVENTS,
        BackendCapability.TRANSCRIPT_STATS,
        BackendCapability.PERMISSION_INJECTION,
        BackendCapability.SKILLS,
        BackendCapability.SANDBOX_PROBE,
        BackendCapability.SUBSCRIPTION_USAGE,
        BackendCapability.AGENT_TEAMS,
    ])
    def test_unsupported(self, backend, capability):
        assert not supports(backend, capability)

    def test_stats_degrade_to_dashes(self, backend):
        assert isinstance(backend.make_stats_reader(), NullStatsReader)


class TestBuildCommand:
    def test_bare_launch(self, backend):
        assert backend.build_command(LaunchSpec()) == ["opencode"]

    def test_command_override(self, backend):
        with patch.dict(os.environ, {"OPENCODE_COMMAND": "/tmp/mock_opencode.py"}):
            cmd = backend.build_command(LaunchSpec())
        assert cmd == ["/tmp/mock_opencode.py"]

    def test_model_is_passed_through_verbatim(self, backend):
        cmd = backend.build_command(LaunchSpec(model="openai/gpt-4o-mini"))
        assert cmd == ["opencode", "--model", "openai/gpt-4o-mini"]

    def test_agent_persona(self, backend):
        cmd = backend.build_command(LaunchSpec(agent="reviewer"))
        assert cmd == ["opencode", "--agent", "reviewer"]

    @pytest.mark.parametrize("spec_kwargs", [
        {"permissiveness_mode": "bypass"},
        {"permissiveness_mode": "permissive"},
        {"dangerously_skip_permissions": True},
        {"skip_permissions": True},
    ])
    def test_permissive_modes_all_collapse_onto_auto(self, backend, spec_kwargs):
        # opencode has one knob: --auto. Both of overcode's loosened modes
        # map to it; deny rules in opencode.json still win.
        cmd = backend.build_command(LaunchSpec(**spec_kwargs))
        assert cmd == ["opencode", "--auto"]

    def test_normal_permissions_add_no_flag(self, backend):
        cmd = backend.build_command(LaunchSpec(permissiveness_mode="normal"))
        assert cmd == ["opencode"]

    def test_allowed_tools_has_no_v1_18_analogue(self, backend):
        # The researched `--permissions a,b` flag does not exist in
        # v1.18.19; silently emitting it would fail the launch.
        cmd = backend.build_command(LaunchSpec(allowed_tools="bash,edit"))
        assert cmd == ["opencode"]
        assert "--permissions" not in cmd

    def test_prescribed_session_id_is_ignored(self, backend):
        # opencode mints its own ses_… ids.
        cmd = backend.build_command(LaunchSpec(prescribed_session_id="uuid-1234"))
        assert cmd == ["opencode"]

    def test_resume(self, backend):
        cmd = backend.build_command(LaunchSpec(resume_session_id="ses_abc"))
        assert cmd == ["opencode", "--session", "ses_abc"]

    def test_fork(self, backend):
        cmd = backend.build_command(
            LaunchSpec(resume_session_id="ses_abc", fork=True)
        )
        assert cmd == ["opencode", "--session", "ses_abc", "--fork"]

    def test_resume_args_helper(self, backend):
        assert backend.resume_args("ses_abc", False) == ["--session", "ses_abc"]
        assert backend.resume_args("ses_abc", True) == [
            "--session", "ses_abc", "--fork",
        ]

    def test_extra_args_are_shell_split(self, backend):
        cmd = backend.build_command(
            LaunchSpec(extra_args=["--log-level DEBUG", "--pure"])
        )
        assert cmd == ["opencode", "--log-level", "DEBUG", "--pure"]

    def test_full_combination_order(self, backend):
        cmd = backend.build_command(LaunchSpec(
            resume_session_id="ses_abc",
            fork=True,
            model="anthropic/claude-sonnet-4-5",
            agent="reviewer",
            permissiveness_mode="bypass",
            extra_args=["--pure"],
        ))
        assert cmd == [
            "opencode",
            "--session", "ses_abc", "--fork",
            "--model", "anthropic/claude-sonnet-4-5",
            "--agent", "reviewer",
            "--auto",
            "--pure",
        ]

    def test_no_env_prefix(self, backend):
        # opencode reads provider credentials from the ambient environment;
        # nothing extra is exported onto the launch line.
        assert backend.env_prefix(LaunchSpec(agent_teams=True, provider="bedrock")) == {}


class TestGestures:
    def test_graceful_exit_interrupts_then_exits(self, backend):
        presses = backend.graceful_exit_keys()
        # Ctrl-C kills opencode outright, so Escape (twice — the first press
        # only arms the interrupt) stands in for Claude's C-c.
        assert [p.keys for p in presses] == ["Escape", "Escape", "/exit"]
        assert presses[-1].enter is True
        assert not any(p.keys == "C-c" for p in presses)

    def test_clear_conversation(self, backend):
        presses = backend.clear_conversation_keys()
        assert [(p.keys, p.enter) for p in presses] == [("/new", True)]

    def test_approve_is_enter_on_allow_once(self, backend):
        presses = backend.approve_keys()
        assert [(p.keys, p.enter) for p in presses] == [("", True)]

    def test_reject_is_escape(self, backend):
        presses = backend.reject_keys()
        assert [(p.keys, p.enter) for p in presses] == [("Escape", False)]

    def test_no_startup_dialogs(self, backend):
        # With a provider credential in the environment opencode goes
        # straight to the input box — no trust prompt, no provider picker.
        assert backend.startup_dialog_rules() == []

    def test_prompt_ready_chars(self, backend):
        assert backend.prompt_ready_chars() == {"┃"}


class TestHealthVerdict:
    def test_live_process_is_ok(self, backend):
        verdict, details = backend.health_verdict("/opt/homebrew/bin/opencode --auto")
        assert verdict == VERDICT_OK
        assert "opencode process running" in details

    def test_verdict_does_not_depend_on_settings_flag(self, backend):
        # There is nothing to inject until Phase 5's plugin.
        assert backend.health_verdict("opencode")[0] == VERDICT_OK
