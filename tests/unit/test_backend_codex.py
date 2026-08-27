"""Argv, gestures and capability gating for the codex backend.

Every flag asserted here traces to Appendix A of
``docs/design/agent-backends-codex-grok.md`` (Phase 0, live-verified against
Codex CLI v0.150.1). Notably absent: ``--allowedTools``/``--agent``, which
have no codex analogue and are silently ignored, same posture as opencode.
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
from overcode.backends.codex import (
    CodexBackend,
    CodexNotFoundError,
)
from overcode.doctor import VERDICT_OK
from overcode.exceptions import ClaudeNotFoundError


@pytest.fixture
def backend():
    return get_backend("codex")


class TestRegistry:
    def test_listed(self):
        assert "codex" in list_backends()

    def test_resolves_to_the_adapter(self, backend):
        assert isinstance(backend, CodexBackend)

    def test_display_name(self, backend):
        assert backend.display_name == "codex"

    def test_not_found_error_is_catchable_as_the_legacy_one(self):
        # The launcher's existing "agent CLI missing" except clause names
        # ClaudeNotFoundError; codex's must be caught by it.
        assert issubclass(CodexNotFoundError, ClaudeNotFoundError)

    def test_process_basenames_are_the_vendored_child(self, backend):
        # The top-level process is `node .../codex` (the npm wrapper); the
        # vendored child actually running the TUI has basename "codex".
        assert backend.process_basenames == ("codex",)

    def test_install_hint_names_npm(self, backend):
        assert "npm install -g @openai/codex" in backend.install_hint


class TestCapabilities:
    def test_resume_and_fork(self, backend):
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
    def test_unsupported_this_phase(self, backend, capability):
        # Phase 1 is launch + polling status only — hooks/stats land in
        # Phase 2 per the design doc.
        assert not supports(backend, capability)

    def test_stats_reader_is_null_but_present(self, backend):
        from overcode.stats_reader import NullStatsReader

        reader = backend.make_stats_reader()
        assert isinstance(reader, NullStatsReader)
        assert reader.get_stats(None) is None

    def test_stats_reader_for_session_resolves_to_null(self):
        # No TRANSCRIPT_STATS declared, so the seam never calls
        # backend.make_stats_reader() for real sessions either — same path
        # an unknown backend name takes.
        from overcode.stats_reader import NullStatsReader, stats_reader_for_session
        from overcode.session_manager import Session

        session = Session(
            id="s", name="n", tmux_session="agents", tmux_window="n",
            command=["codex"], start_directory=None, start_time="2026-08-01",
            backend="codex",
        )
        assert isinstance(stats_reader_for_session(session), NullStatsReader)


class TestBuildCommand:
    def test_bare_launch(self, backend):
        assert backend.build_command(LaunchSpec()) == ["codex"]

    def test_command_override(self, backend):
        with patch.dict(os.environ, {"CODEX_COMMAND": "/tmp/mock_codex.py"}):
            cmd = backend.build_command(LaunchSpec())
        assert cmd == ["/tmp/mock_codex.py"]

    def test_model_is_passed_through_bare(self, backend):
        cmd = backend.build_command(LaunchSpec(model="gpt-5.6-sol"))
        assert cmd == ["codex", "-m", "gpt-5.6-sol"]

    def test_agent_persona_has_no_analogue(self, backend):
        # -p/--profile is a config-layer override, not a persona flag —
        # spec.agent is silently ignored.
        cmd = backend.build_command(LaunchSpec(agent="reviewer"))
        assert cmd == ["codex"]
        assert "-p" not in cmd
        assert "--profile" not in cmd

    @pytest.mark.parametrize("spec_kwargs", [
        {"permissiveness_mode": "bypass"},
        {"dangerously_skip_permissions": True},
    ])
    def test_bypass_modes(self, backend, spec_kwargs):
        cmd = backend.build_command(LaunchSpec(**spec_kwargs))
        assert cmd == ["codex", "--dangerously-bypass-approvals-and-sandbox"]

    @pytest.mark.parametrize("spec_kwargs", [
        {"permissiveness_mode": "permissive"},
        {"skip_permissions": True},
    ])
    def test_permissive_modes(self, backend, spec_kwargs):
        cmd = backend.build_command(LaunchSpec(**spec_kwargs))
        assert cmd == ["codex", "-a", "never", "--sandbox", "workspace-write"]

    def test_bypass_wins_over_permissive(self, backend):
        cmd = backend.build_command(LaunchSpec(
            dangerously_skip_permissions=True, skip_permissions=True,
        ))
        assert cmd == ["codex", "--dangerously-bypass-approvals-and-sandbox"]

    def test_normal_permissions_add_no_flag(self, backend):
        cmd = backend.build_command(LaunchSpec(permissiveness_mode="normal"))
        assert cmd == ["codex"]

    def test_allowed_tools_has_no_analogue(self, backend):
        cmd = backend.build_command(LaunchSpec(allowed_tools="bash,edit"))
        assert cmd == ["codex"]
        assert "--allowedTools" not in cmd

    def test_prescribed_session_id_is_ignored(self, backend):
        # codex has no --session-id-shaped flag for fresh launches.
        cmd = backend.build_command(LaunchSpec(prescribed_session_id="uuid-1234"))
        assert cmd == ["codex"]

    def test_resume(self, backend):
        cmd = backend.build_command(LaunchSpec(resume_session_id="01a0439d-abc"))
        assert cmd == ["codex", "resume", "01a0439d-abc"]

    def test_fork(self, backend):
        cmd = backend.build_command(
            LaunchSpec(resume_session_id="01a0439d-abc", fork=True)
        )
        assert cmd == ["codex", "fork", "01a0439d-abc"]

    def test_resume_args_helper(self, backend):
        assert backend.resume_args("abc", False) == ["resume", "abc"]
        assert backend.resume_args("abc", True) == ["fork", "abc"]

    def test_resume_subcommand_comes_before_shared_options(self, backend):
        # Verified live: `codex resume <id> -a never -m <model>` launches
        # cleanly — top-level options are accepted *after* the subcommand.
        cmd = backend.build_command(LaunchSpec(
            resume_session_id="01a0439d-abc",
            model="gpt-5.6-sol",
            permissiveness_mode="permissive",
        ))
        assert cmd == [
            "codex", "resume", "01a0439d-abc",
            "-m", "gpt-5.6-sol",
            "-a", "never", "--sandbox", "workspace-write",
        ]

    def test_extra_args_are_shell_split(self, backend):
        cmd = backend.build_command(
            LaunchSpec(extra_args=["--add-dir /tmp/x", "--pure"])
        )
        assert cmd == ["codex", "--add-dir", "/tmp/x", "--pure"]

    def test_full_combination_order(self, backend):
        cmd = backend.build_command(LaunchSpec(
            resume_session_id="sess-1",
            fork=True,
            model="gpt-5.6-sol",
            permissiveness_mode="bypass",
            extra_args=["--pure"],
        ))
        assert cmd == [
            "codex",
            "fork", "sess-1",
            "-m", "gpt-5.6-sol",
            "--dangerously-bypass-approvals-and-sandbox",
            "--pure",
        ]

    def test_no_env_prefix(self, backend):
        assert backend.env_prefix(LaunchSpec()) == {}
        assert backend.env_prefix(LaunchSpec(agent_teams=True, provider="bedrock")) == {}


class TestGestures:
    def test_graceful_exit_is_escape_then_quit_never_c_c(self, backend):
        presses = backend.graceful_exit_keys()
        assert [p.keys for p in presses] == ["Escape", "/quit"]
        assert presses[-1].enter is True
        assert not any(p.keys == "C-c" for p in presses)

    def test_clear_conversation(self, backend):
        presses = backend.clear_conversation_keys()
        assert [(p.keys, p.enter) for p in presses] == [("/new", True)]

    def test_approve_is_enter_on_yes_proceed(self, backend):
        presses = backend.approve_keys()
        assert [(p.keys, p.enter) for p in presses] == [("", True)]

    def test_reject_is_escape(self, backend):
        presses = backend.reject_keys()
        assert [(p.keys, p.enter) for p in presses] == [("Escape", False)]

    def test_trust_dialog_rule(self, backend):
        rules = backend.startup_dialog_rules()
        assert len(rules) == 1
        rule = rules[0]
        assert rule.marker == "Do you trust the contents of this directory?"
        assert [(p.keys, p.enter) for p in rule.presses] == [("", True)]

    def test_prompt_ready_chars(self, backend):
        # Not a bare glyph: codex never draws "›" alone (see codex.py's
        # docstring on this method), so the launcher's exact-line match
        # needs the literal idle placeholder line instead.
        assert backend.prompt_ready_chars() == {"› Ask Codex to do anything"}


class TestHealthVerdict:
    def test_live_process_is_ok(self, backend):
        verdict, details = backend.health_verdict("/opt/homebrew/bin/codex -m gpt-5.6-sol")
        assert verdict == VERDICT_OK
        assert "codex process running" in details

    def test_verdict_does_not_depend_on_argv_shape(self, backend):
        assert backend.health_verdict("codex")[0] == VERDICT_OK

    def test_no_refine_this_phase(self, backend):
        # No telemetry artifact exists yet to give refine_health_verdict
        # something to check — Phase 2 adds it.
        assert not hasattr(backend, "refine_health_verdict")


class TestPrepareLaunch:
    def test_is_a_no_op(self, backend, tmp_path):
        # Must not raise, and must not stage any files this phase.
        backend.prepare_launch(LaunchSpec(start_directory=str(tmp_path)))
        assert list(tmp_path.iterdir()) == []
