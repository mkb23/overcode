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
from overcode.doctor import VERDICT_MISSING_SETTINGS, VERDICT_OK
from overcode.exceptions import ClaudeNotFoundError
from overcode.hook_handler import CODEX_HOOK_EVENTS


OVERCODE_BIN = "/usr/local/bin/overcode"


def _codex_hook_flags(overcode_bin: str = OVERCODE_BIN) -> list:
    """Literal (not implementation-derived) reconstruction of the ``-c``
    hook overrides + ``--dangerously-bypass-hook-trust`` flag every launch
    now appends — golden tests freeze exact bytes, so this must not call
    ``backends.codex``'s own TOML-building helper to build its expectation.
    """
    array = f'[{{hooks=[{{type="command",command="{overcode_bin} hook-handler"}}]}}]'
    flags: list = []
    for event in CODEX_HOOK_EVENTS:
        flags.extend(["-c", f"hooks.{event}={array}"])
    flags.append("--dangerously-bypass-hook-trust")
    return flags


CODEX_HOOK_FLAGS = _codex_hook_flags()


@pytest.fixture
def backend():
    return get_backend("codex")


@pytest.fixture(autouse=True)
def pinned_overcode_bin():
    """Freeze the hook command path so golden argv doesn't depend on the
    machine's overcode install location (mirrors test_backend_claude.py's
    fixture of the same name for --settings)."""
    with patch(
        "overcode.backends.codex._resolve_overcode_bin", return_value=OVERCODE_BIN
    ), patch.dict(os.environ, {}, clear=False) as env:
        env.pop("CODEX_COMMAND", None)
        yield


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

    def test_hook_events_and_transcript_stats(self, backend):
        # Phase 2: hook injection + rollout-JSONL stats are wired.
        assert supports(backend, BackendCapability.HOOK_EVENTS)
        assert supports(backend, BackendCapability.TRANSCRIPT_STATS)

    @pytest.mark.parametrize("capability", [
        BackendCapability.SESSION_ID_PRESCRIPTION,
        BackendCapability.PERMISSION_INJECTION,
        BackendCapability.SKILLS,
        BackendCapability.SANDBOX_PROBE,
        BackendCapability.SUBSCRIPTION_USAGE,
        BackendCapability.AGENT_TEAMS,
    ])
    def test_unsupported(self, backend, capability):
        # codex has no analogue for any of these — see codex.py's class
        # docstring for why each one is absent.
        assert not supports(backend, capability)

    def test_stats_reader_is_the_real_reader(self, backend):
        from overcode.backends.codex_stats import CodexStatsReader

        reader = backend.make_stats_reader()
        assert isinstance(reader, CodexStatsReader)

    def test_stats_reader_for_session_resolves_to_codex_reader(self):
        # TRANSCRIPT_STATS is declared now, so the seam picks the real
        # reader for codex sessions instead of falling back to NullStatsReader.
        from overcode.backends.codex_stats import CodexStatsReader
        from overcode.stats_reader import clear_reader_cache, stats_reader_for_session
        from overcode.session_manager import Session

        clear_reader_cache()
        session = Session(
            id="s", name="n", tmux_session="agents", tmux_window="n",
            command=["codex"], start_directory=None, start_time="2026-08-01",
            backend="codex",
        )
        try:
            assert isinstance(stats_reader_for_session(session), CodexStatsReader)
        finally:
            clear_reader_cache()


class TestBuildCommand:
    def test_bare_launch(self, backend):
        assert backend.build_command(LaunchSpec()) == ["codex", *CODEX_HOOK_FLAGS]

    def test_command_override(self, backend):
        with patch.dict(os.environ, {"CODEX_COMMAND": "/tmp/mock_codex.py"}):
            cmd = backend.build_command(LaunchSpec())
        # Hook injection is unconditional — the mock harness gets it too,
        # same posture as Claude's --settings (mirrored, see codex.py's
        # build_command docstring); tests/mock_codex.py tolerates it.
        assert cmd == ["/tmp/mock_codex.py", *CODEX_HOOK_FLAGS]

    def test_model_is_passed_through_bare(self, backend):
        cmd = backend.build_command(LaunchSpec(model="gpt-5.6-sol"))
        assert cmd == ["codex", "-m", "gpt-5.6-sol", *CODEX_HOOK_FLAGS]

    def test_agent_persona_has_no_analogue(self, backend):
        # -p/--profile is a config-layer override, not a persona flag —
        # spec.agent is silently ignored.
        cmd = backend.build_command(LaunchSpec(agent="reviewer"))
        assert cmd == ["codex", *CODEX_HOOK_FLAGS]
        assert "-p" not in cmd
        assert "--profile" not in cmd

    @pytest.mark.parametrize("spec_kwargs", [
        {"permissiveness_mode": "bypass"},
        {"dangerously_skip_permissions": True},
    ])
    def test_bypass_modes(self, backend, spec_kwargs):
        cmd = backend.build_command(LaunchSpec(**spec_kwargs))
        assert cmd == [
            "codex", "--dangerously-bypass-approvals-and-sandbox", *CODEX_HOOK_FLAGS,
        ]

    @pytest.mark.parametrize("spec_kwargs", [
        {"permissiveness_mode": "permissive"},
        {"skip_permissions": True},
    ])
    def test_permissive_modes(self, backend, spec_kwargs):
        cmd = backend.build_command(LaunchSpec(**spec_kwargs))
        assert cmd == [
            "codex", "-a", "never", "--sandbox", "workspace-write", *CODEX_HOOK_FLAGS,
        ]

    def test_bypass_wins_over_permissive(self, backend):
        cmd = backend.build_command(LaunchSpec(
            dangerously_skip_permissions=True, skip_permissions=True,
        ))
        assert cmd == [
            "codex", "--dangerously-bypass-approvals-and-sandbox", *CODEX_HOOK_FLAGS,
        ]

    def test_normal_permissions_add_no_flag(self, backend):
        cmd = backend.build_command(LaunchSpec(permissiveness_mode="normal"))
        assert cmd == ["codex", *CODEX_HOOK_FLAGS]

    def test_allowed_tools_has_no_analogue(self, backend):
        cmd = backend.build_command(LaunchSpec(allowed_tools="bash,edit"))
        assert cmd == ["codex", *CODEX_HOOK_FLAGS]
        assert "--allowedTools" not in cmd

    def test_prescribed_session_id_is_ignored(self, backend):
        # codex has no --session-id-shaped flag for fresh launches.
        cmd = backend.build_command(LaunchSpec(prescribed_session_id="uuid-1234"))
        assert cmd == ["codex", *CODEX_HOOK_FLAGS]

    def test_resume(self, backend):
        cmd = backend.build_command(LaunchSpec(resume_session_id="01a0439d-abc"))
        assert cmd == ["codex", "resume", "01a0439d-abc", *CODEX_HOOK_FLAGS]

    def test_fork(self, backend):
        cmd = backend.build_command(
            LaunchSpec(resume_session_id="01a0439d-abc", fork=True)
        )
        assert cmd == ["codex", "fork", "01a0439d-abc", *CODEX_HOOK_FLAGS]

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
            *CODEX_HOOK_FLAGS,
        ]

    def test_extra_args_are_shell_split(self, backend):
        cmd = backend.build_command(
            LaunchSpec(extra_args=["--add-dir /tmp/x", "--pure"])
        )
        assert cmd == ["codex", *CODEX_HOOK_FLAGS, "--add-dir", "/tmp/x", "--pure"]

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
            *CODEX_HOOK_FLAGS,
            "--pure",
        ]

    def test_no_env_prefix(self, backend):
        assert backend.env_prefix(LaunchSpec()) == {}
        assert backend.env_prefix(LaunchSpec(agent_teams=True, provider="bedrock")) == {}


class TestHookInjection:
    """Golden coverage for the -c overrides + --dangerously-bypass-hook-trust
    Phase 2 adds on every launch (design doc §2.3, Route 1)."""

    def test_all_eight_events_registered_in_order(self, backend):
        cmd = backend.build_command(LaunchSpec())
        c_values = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "-c"]
        registered_events = [v.split("=", 1)[0].removeprefix("hooks.") for v in c_values]
        assert registered_events == list(CODEX_HOOK_EVENTS)

    def test_bypass_hook_trust_flag_present_and_last(self, backend):
        cmd = backend.build_command(LaunchSpec())
        assert cmd[-1] == "--dangerously-bypass-hook-trust"

    def test_command_value_is_a_bare_string_not_an_array(self, backend):
        # Appendix A's shape correction: HookHandlerConfig::Command is a
        # single shell command-line string, not Claude's `command: [str,...]`.
        cmd = backend.build_command(LaunchSpec())
        c_value = cmd[cmd.index("-c") + 1]
        assert f'command="{OVERCODE_BIN} hook-handler"' in c_value
        assert '"command": [' not in c_value

    def test_hook_injection_is_unconditional(self, backend):
        # Never gated on a launch flag — mirrors Claude's --settings posture.
        for spec in (
            LaunchSpec(),
            LaunchSpec(permissiveness_mode="bypass"),
            LaunchSpec(resume_session_id="abc"),
        ):
            cmd = backend.build_command(spec)
            assert "--dangerously-bypass-hook-trust" in cmd

    def test_toml_value_escapes_quotes_and_backslashes(self):
        from overcode.backends.codex import _codex_hook_toml_array

        value = _codex_hook_toml_array('C:\\path with "quotes"')
        assert value == '[{hooks=[{type="command",command="C:\\\\path with \\"quotes\\""}]}]'


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
    def test_hooks_injected_is_ok(self, backend):
        argv = (
            "/opt/homebrew/bin/codex -m gpt-5.6-sol -c hooks.Stop=[...] "
            "--dangerously-bypass-hook-trust"
        )
        verdict, details = backend.health_verdict(argv)
        assert verdict == VERDICT_OK
        assert "hooks injected" in details

    def test_missing_hook_overrides_is_missing_settings(self, backend):
        verdict, details = backend.health_verdict("/opt/homebrew/bin/codex -m gpt-5.6-sol")
        assert verdict == VERDICT_MISSING_SETTINGS
        assert "hook-injection overrides" in details

    def test_bare_binary_is_missing_settings(self, backend):
        assert backend.health_verdict("codex")[0] == VERDICT_MISSING_SETTINGS


class TestPrepareLaunch:
    def test_is_a_no_op(self, backend, tmp_path):
        # Route 1 (-c overrides in argv) stages nothing on disk — must not
        # raise, and must not stage any files.
        backend.prepare_launch(LaunchSpec(start_directory=str(tmp_path)))
        assert list(tmp_path.iterdir()) == []
