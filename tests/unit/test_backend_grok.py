"""Argv, gestures and capability gating for the grok backend.

Every flag asserted here traces to Appendix B of
``docs/design/agent-backends-codex-grok.md`` (Phase 0, live-verified against
Grok Build v1.0.5). grok is the first non-Claude backend to prescribe its
own session id and inject a permission allowlist — both capabilities are
launch-flag-shaped for grok, unlike codex (neither) or opencode (mints its
own ids, no allowlist flag).
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
from overcode.backends.grok import (
    GrokBackend,
    GrokNotFoundError,
)
from overcode.doctor import VERDICT_OK
from overcode.exceptions import ClaudeNotFoundError


@pytest.fixture
def backend():
    return get_backend("grok")


@pytest.fixture(autouse=True)
def clean_grok_command_env():
    """Keep GROK_COMMAND out of the ambient env unless a test opts in."""
    with patch.dict(os.environ, {}, clear=False) as env:
        env.pop("GROK_COMMAND", None)
        yield


class TestRegistry:
    def test_listed(self):
        assert "grok" in list_backends()

    def test_resolves_to_the_adapter(self, backend):
        assert isinstance(backend, GrokBackend)

    def test_display_name(self, backend):
        assert backend.display_name == "grok"

    def test_not_found_error_is_catchable_as_the_legacy_one(self):
        assert issubclass(GrokNotFoundError, ClaudeNotFoundError)

    def test_process_basenames_are_bare_grok(self, backend):
        # No wrapper/child split like codex's npm shim.
        assert backend.process_basenames == ("grok",)

    def test_install_hint_names_the_curl_installer_and_subscription(self, backend):
        assert "curl -fsSL https://x.ai/cli/install.sh | bash" in backend.install_hint
        assert "SuperGrok" in backend.install_hint or "X Premium" in backend.install_hint


class TestCapabilities:
    def test_resume_fork_prescription_and_permission_injection(self, backend):
        assert supports(backend, BackendCapability.RESUME)
        assert supports(backend, BackendCapability.FORK)
        assert supports(backend, BackendCapability.SESSION_ID_PRESCRIPTION)
        assert supports(backend, BackendCapability.PERMISSION_INJECTION)

    def test_hook_events_and_transcript_stats_since_phase_4(self, backend):
        assert supports(backend, BackendCapability.HOOK_EVENTS)
        assert supports(backend, BackendCapability.TRANSCRIPT_STATS)

    @pytest.mark.parametrize("capability", [
        BackendCapability.SKILLS,
        BackendCapability.SANDBOX_PROBE,
        BackendCapability.SUBSCRIPTION_USAGE,
        BackendCapability.AGENT_TEAMS,
    ])
    def test_unsupported(self, backend, capability):
        # No grok analogue at all — see GrokBackend's class docstring.
        assert not supports(backend, capability)

    def test_stats_reader_is_the_real_reader(self, backend):
        from overcode.backends.grok_stats import GrokStatsReader

        reader = backend.make_stats_reader()
        assert isinstance(reader, GrokStatsReader)

    def test_stats_reader_for_session_resolves_to_grok_reader(self):
        from overcode.backends.grok_stats import GrokStatsReader
        from overcode.stats_reader import clear_reader_cache, stats_reader_for_session
        from overcode.session_manager import Session

        clear_reader_cache()
        session = Session(
            id="s", name="n", tmux_session="agents", tmux_window="n",
            command=["grok"], start_directory=None, start_time="2026-08-01",
            backend="grok",
        )
        try:
            assert isinstance(stats_reader_for_session(session), GrokStatsReader)
        finally:
            clear_reader_cache()


class TestBuildCommand:
    def test_bare_launch(self, backend):
        cmd = backend.build_command(LaunchSpec())
        assert cmd == ["grok", "--fullscreen", "--permission-mode", "default"]

    def test_command_override(self, backend):
        with patch.dict(os.environ, {"GROK_COMMAND": "/tmp/mock_grok.py"}):
            cmd = backend.build_command(LaunchSpec())
        assert cmd == [
            "/tmp/mock_grok.py", "--fullscreen", "--permission-mode", "default",
        ]

    def test_model_is_passed_through_bare(self, backend):
        cmd = backend.build_command(LaunchSpec(model="grok-4.6"))
        assert cmd == [
            "grok", "--fullscreen", "-m", "grok-4.6", "--permission-mode", "default",
        ]

    def test_agent_persona(self, backend):
        cmd = backend.build_command(LaunchSpec(agent="reviewer"))
        assert cmd == [
            "grok", "--fullscreen", "--agent", "reviewer",
            "--permission-mode", "default",
        ]

    def test_fullscreen_is_always_present(self, backend):
        # Corpus README recommendation: pin the chrome regardless of the
        # user's own [ui] screen_mode config.
        for spec in (LaunchSpec(), LaunchSpec(resume_session_id="x"), LaunchSpec(fork=True)):
            assert "--fullscreen" in backend.build_command(spec)

    @pytest.mark.parametrize("spec_kwargs", [
        {"permissiveness_mode": "bypass"},
        {"dangerously_skip_permissions": True},
    ])
    def test_bypass_modes(self, backend, spec_kwargs):
        cmd = backend.build_command(LaunchSpec(**spec_kwargs))
        assert cmd == [
            "grok", "--fullscreen", "--permission-mode", "bypassPermissions",
        ]

    @pytest.mark.parametrize("spec_kwargs", [
        {"permissiveness_mode": "permissive"},
        {"skip_permissions": True},
    ])
    def test_permissive_modes_target_auto_not_dontask(self, backend, spec_kwargs):
        # Phase 0 correction: dontAsk shows the same dialog as default; only
        # auto actually skips it.
        cmd = backend.build_command(LaunchSpec(**spec_kwargs))
        assert cmd == ["grok", "--fullscreen", "--permission-mode", "auto"]
        assert "dontAsk" not in cmd

    def test_bypass_wins_over_permissive(self, backend):
        cmd = backend.build_command(LaunchSpec(
            dangerously_skip_permissions=True, skip_permissions=True,
        ))
        assert cmd == [
            "grok", "--fullscreen", "--permission-mode", "bypassPermissions",
        ]

    def test_normal_mode_is_explicit_default_not_omitted(self, backend):
        # Load-bearing: the user's own config can set always-approve, so the
        # flag must be passed on every launch, never relied on implicitly.
        cmd = backend.build_command(LaunchSpec(permissiveness_mode="normal"))
        assert cmd == ["grok", "--fullscreen", "--permission-mode", "default"]
        cmd_bare = backend.build_command(LaunchSpec())
        assert "--permission-mode" in cmd_bare and "default" in cmd_bare

    def test_allowed_tools_become_repeated_allow_flags(self, backend):
        cmd = backend.build_command(LaunchSpec(allowed_tools="Bash, Read ,Write"))
        assert cmd == [
            "grok", "--fullscreen", "--permission-mode", "default",
            "--allow", "Bash", "--allow", "Read", "--allow", "Write",
        ]

    def test_allowed_tools_skips_empty_entries(self, backend):
        cmd = backend.build_command(LaunchSpec(allowed_tools="Bash,,Read"))
        assert cmd == [
            "grok", "--fullscreen", "--permission-mode", "default",
            "--allow", "Bash", "--allow", "Read",
        ]

    def test_no_allowed_tools_means_no_allow_flags(self, backend):
        cmd = backend.build_command(LaunchSpec())
        assert "--allow" not in cmd

    def test_prescribed_session_id_on_fresh_launch(self, backend):
        cmd = backend.build_command(LaunchSpec(prescribed_session_id="uuid-1234"))
        assert cmd == [
            "grok", "--session-id", "uuid-1234", "--fullscreen",
            "--permission-mode", "default",
        ]

    def test_resume_does_not_prescribe_a_session_id(self, backend):
        # -s/--session-id is documented as "for a new conversation; must not
        # already exist" — a plain resume must never carry it, even if a
        # prescribed_session_id is somehow also set.
        cmd = backend.build_command(LaunchSpec(
            resume_session_id="01a0439d-abc", prescribed_session_id="stray-uuid",
        ))
        assert cmd == [
            "grok", "--resume", "01a0439d-abc", "--fullscreen",
            "--permission-mode", "default",
        ]
        assert "--session-id" not in cmd

    def test_fork_prescribes_the_new_session_id(self, backend):
        cmd = backend.build_command(LaunchSpec(
            resume_session_id="01a0439d-abc", fork=True,
            prescribed_session_id="new-uuid",
        ))
        assert cmd == [
            "grok", "--resume", "01a0439d-abc", "--fork-session",
            "--session-id", "new-uuid", "--fullscreen",
            "--permission-mode", "default",
        ]

    def test_fork_without_a_prescribed_id_omits_session_id(self, backend):
        cmd = backend.build_command(LaunchSpec(
            resume_session_id="01a0439d-abc", fork=True,
        ))
        assert cmd == [
            "grok", "--resume", "01a0439d-abc", "--fork-session",
            "--fullscreen", "--permission-mode", "default",
        ]

    def test_resume_args_helper(self, backend):
        assert backend.resume_args("abc", False) == ["--resume", "abc"]
        assert backend.resume_args("abc", True) == ["--resume", "abc", "--fork-session"]

    def test_extra_args_are_shell_split(self, backend):
        cmd = backend.build_command(
            LaunchSpec(extra_args=["--worktree feature-x", "--pure"])
        )
        assert cmd == [
            "grok", "--fullscreen", "--permission-mode", "default",
            "--worktree", "feature-x", "--pure",
        ]

    def test_full_combination_order(self, backend):
        cmd = backend.build_command(LaunchSpec(
            resume_session_id="src-id",
            fork=True,
            prescribed_session_id="new-uuid",
            model="grok-4.6",
            agent="reviewer",
            permissiveness_mode="bypass",
            allowed_tools="Bash,Read",
            extra_args=["--pure"],
        ))
        assert cmd == [
            "grok",
            "--resume", "src-id", "--fork-session", "--session-id", "new-uuid",
            "--fullscreen",
            "-m", "grok-4.6",
            "--agent", "reviewer",
            "--permission-mode", "bypassPermissions",
            "--allow", "Bash", "--allow", "Read",
            "--pure",
        ]

    def test_env_prefix_is_empty_without_state_dir_override(self, backend, monkeypatch):
        monkeypatch.delenv("OVERCODE_STATE_DIR", raising=False)
        assert backend.env_prefix(LaunchSpec()) == {}
        assert backend.env_prefix(LaunchSpec(agent_teams=True, provider="bedrock")) == {}

    def test_env_prefix_forwards_state_dir_for_hook_subprocesses(self, backend, monkeypatch):
        # The hooks file's command runs `overcode hook-handler` as a
        # subprocess of grok itself, which needs OVERCODE_STATE_DIR when
        # test-isolated — exact analogue of OpencodeBackend.env_prefix.
        monkeypatch.setenv("OVERCODE_STATE_DIR", "/tmp/oc-state")
        assert backend.env_prefix(LaunchSpec()) == {"OVERCODE_STATE_DIR": "/tmp/oc-state"}


class TestGestures:
    def test_graceful_exit_is_escape_then_quit_never_c_c(self, backend):
        presses = backend.graceful_exit_keys()
        assert [p.keys for p in presses] == ["Escape", "/quit"]
        assert presses[-1].enter is True
        assert not any(p.keys == "C-c" for p in presses)

    def test_clear_conversation(self, backend):
        presses = backend.clear_conversation_keys()
        assert [(p.keys, p.enter) for p in presses] == [("/new", True)]

    def test_approve_is_digit_two_no_enter(self, backend):
        # Option 1 is default-selected but means "always-approve mode" — a
        # one-time approve must target option 2 explicitly.
        presses = backend.approve_keys()
        assert [(p.keys, p.enter) for p in presses] == [("2", False)]

    def test_reject_is_digit_three_no_enter(self, backend):
        presses = backend.reject_keys()
        assert [(p.keys, p.enter) for p in presses] == [("3", False)]

    def test_no_startup_dialog_rules(self, backend):
        # Confirmed absent, even in a never-before-visited directory.
        assert backend.startup_dialog_rules() == []

    def test_prompt_ready_chars(self, backend):
        assert backend.prompt_ready_chars() == {"[stable]"}


class TestHealthVerdict:
    def test_any_live_process_is_ok_first_pass(self, backend):
        verdict, details = backend.health_verdict("/opt/homebrew/bin/grok -m grok-4.6")
        assert verdict == VERDICT_OK
        assert "grok process running" in details

    def test_bare_binary_is_also_ok(self, backend):
        assert backend.health_verdict("grok")[0] == VERDICT_OK


class TestRefineHealthVerdict:
    @pytest.fixture(autouse=True)
    def grok_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GROK_HOME", str(tmp_path / ".grok"))

    def test_hooks_installed_stays_ok(self, backend):
        from overcode.backends.grok import ensure_hooks_installed
        from overcode.doctor import VERDICT_OK

        ensure_hooks_installed()
        verdict, details = backend.refine_health_verdict(None, VERDICT_OK, "grok process running")
        assert verdict == VERDICT_OK
        assert "overcode hooks installed" in details

    def test_missing_hooks_file_degrades(self, backend):
        from overcode.doctor import VERDICT_MISSING_SETTINGS, VERDICT_OK

        verdict, details = backend.refine_health_verdict(None, VERDICT_OK, "grok process running")
        assert verdict == VERDICT_MISSING_SETTINGS
        assert "overcode restart" in details

    def test_non_ok_verdict_passes_through_unchanged(self, backend):
        verdict, details = backend.refine_health_verdict(None, "no_process", "gone")
        assert (verdict, details) == ("no_process", "gone")


class TestPrepareLaunch:
    @pytest.fixture(autouse=True)
    def grok_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GROK_HOME", str(tmp_path / "grok-home" / ".grok"))

    def test_installs_the_global_hooks_file(self, backend, tmp_path):
        from overcode.backends.grok import hooks_file_path, hooks_installed

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        # Phase 4: no longer a no-op — installs the global (not
        # start_directory-scoped) hooks file.
        backend.prepare_launch(LaunchSpec(start_directory=str(project_dir)))
        assert hooks_installed() is True
        assert hooks_file_path().exists()
        # Never writes into the launched agent's own project directory.
        assert list(project_dir.iterdir()) == []
