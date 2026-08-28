"""Argv, gestures and capability gating for the opencode backend.

Every flag asserted here was checked against a live `opencode --help` at
v1.18.19 and, where behavioural, driven in a real tmux session. Notably
absent: `--permissions`, which the design research expected and v1.18.19
does not have.
"""

import json
import os
import shlex
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
    list_backends,
    supports,
)
from overcode.backends.opencode import (
    OPENCODE_ALLOW_EVERYTHING_PERMISSION,
    PLUGIN_FILENAME,
    PLUGIN_MARKER,
    OpencodeBackend,
    OpencodeNotFoundError,
    bundled_plugin_path,
    ensure_plugin_installed,
    plugin_installed,
)
from overcode.doctor import VERDICT_OK
from overcode.exceptions import ClaudeNotFoundError


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

    def test_telemetry_and_stats(self, backend):
        # Phase 5: the bundled plugin writes hook-state files, and the SQLite
        # session store backs the token/cost columns.
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
        assert not supports(backend, capability)

    def test_stats_come_from_sqlite(self, backend):
        from overcode.backends.opencode_stats import OpencodeStatsReader

        assert isinstance(backend.make_stats_reader(), OpencodeStatsReader)


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

    def test_no_claude_env_prefix(self, backend):
        # opencode reads provider credentials from the ambient environment, and
        # has no analogue of the teams/bedrock switches.
        env = dict(os.environ)
        env.pop("OVERCODE_STATE_DIR", None)
        with patch.dict(os.environ, env, clear=True):
            assert backend.env_prefix(
                LaunchSpec(agent_teams=True, provider="bedrock")
            ) == {}


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
        # Telemetry is injected as a file in .opencode/plugins/, so argv can
        # never carry evidence of it the way Claude's --settings does.
        assert backend.health_verdict("opencode")[0] == VERDICT_OK


class TestPluginInstallation:
    """The launcher stages the telemetry plugin into the project directory.

    A project-local copy is used instead of a global config entry so overcode's
    telemetry never loads into opencode sessions the user runs themselves.
    """

    def test_prepare_launch_writes_the_plugin(self, backend, tmp_path):
        backend.prepare_launch(LaunchSpec(start_directory=str(tmp_path)))
        installed = tmp_path / ".opencode" / "plugins" / PLUGIN_FILENAME
        assert installed.is_file()
        assert PLUGIN_MARKER in installed.read_text()

    def test_installed_copy_matches_the_bundled_file(self, backend, tmp_path):
        backend.prepare_launch(LaunchSpec(start_directory=str(tmp_path)))
        installed = tmp_path / ".opencode" / "plugins" / PLUGIN_FILENAME
        assert installed.read_text() == bundled_plugin_path().read_text()

    def test_is_idempotent(self, backend, tmp_path):
        backend.prepare_launch(LaunchSpec(start_directory=str(tmp_path)))
        first = ensure_plugin_installed(str(tmp_path))
        second = ensure_plugin_installed(str(tmp_path))
        assert first == second
        assert plugin_installed(str(tmp_path))

    def test_stale_overcode_copy_is_refreshed(self, tmp_path):
        target = tmp_path / ".opencode" / "plugins" / PLUGIN_FILENAME
        target.parent.mkdir(parents=True)
        target.write_text(f"// old build\n// {PLUGIN_MARKER}\n")
        ensure_plugin_installed(str(tmp_path))
        assert target.read_text() == bundled_plugin_path().read_text()

    def test_user_owned_file_is_never_clobbered(self, tmp_path):
        target = tmp_path / ".opencode" / "plugins" / PLUGIN_FILENAME
        target.parent.mkdir(parents=True)
        target.write_text("export const Mine = async () => ({})\n")
        assert ensure_plugin_installed(str(tmp_path)) is None
        assert target.read_text() == "export const Mine = async () => ({})\n"

    def test_no_start_directory_is_a_no_op(self, backend):
        assert ensure_plugin_installed(None) is None
        backend.prepare_launch(LaunchSpec())  # must not raise

    def test_unwritable_directory_is_survivable(self, backend, tmp_path):
        blocked = tmp_path / "ro"
        blocked.mkdir(mode=0o500)
        try:
            assert ensure_plugin_installed(str(blocked)) is None
        finally:
            blocked.chmod(0o700)

    def test_leaves_no_temp_files(self, backend, tmp_path):
        backend.prepare_launch(LaunchSpec(start_directory=str(tmp_path)))
        assert list((tmp_path / ".opencode" / "plugins").glob("*.tmp")) == []

    def test_claude_backend_stages_nothing(self, tmp_path):
        get_backend("claude-code").prepare_launch(
            LaunchSpec(start_directory=str(tmp_path))
        )
        assert not (tmp_path / ".opencode").exists()


class TestEnvPrefix:
    def test_forwards_the_state_dir(self, backend):
        # The plugin runs inside the opencode process and must write hook state
        # where this overcode instance reads it.
        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": "/tmp/state dir"}):
            assert backend.env_prefix(LaunchSpec()) == {
                "OVERCODE_STATE_DIR": "'/tmp/state dir'"
            }

    def test_empty_without_a_state_dir_override(self, backend):
        env = dict(os.environ)
        env.pop("OVERCODE_STATE_DIR", None)
        with patch.dict(os.environ, env, clear=True):
            assert backend.env_prefix(LaunchSpec()) == {}

    # Ancillary — true bypass-permissions. bypass gets OPENCODE_PERMISSION;
    # permissive and normal must not, since --auto (which both still get on
    # the command line) already covers their honest, deny-rules-still-win
    # behaviour — only bypass claims to actually override deny rules.
    @pytest.mark.parametrize("spec_kwargs", [
        {"permissiveness_mode": "bypass"},
        {"dangerously_skip_permissions": True},
    ])
    def test_bypass_mode_sets_opencode_permission(self, backend, spec_kwargs):
        env = dict(os.environ)
        env.pop("OVERCODE_STATE_DIR", None)
        with patch.dict(os.environ, env, clear=True):
            result = backend.env_prefix(LaunchSpec(**spec_kwargs))
        assert "OPENCODE_PERMISSION" in result
        decoded = json.loads(shlex.split(result["OPENCODE_PERMISSION"])[0])
        assert decoded == OPENCODE_ALLOW_EVERYTHING_PERMISSION
        assert all(value == "allow" for value in decoded.values())

    @pytest.mark.parametrize("spec_kwargs", [
        {"permissiveness_mode": "permissive"},
        {"skip_permissions": True},
        {"permissiveness_mode": "normal"},
        {},
    ])
    def test_non_bypass_modes_do_not_set_opencode_permission(self, backend, spec_kwargs):
        env = dict(os.environ)
        env.pop("OVERCODE_STATE_DIR", None)
        with patch.dict(os.environ, env, clear=True):
            result = backend.env_prefix(LaunchSpec(**spec_kwargs))
        assert "OPENCODE_PERMISSION" not in result

    def test_bypass_mode_still_forwards_state_dir(self, backend):
        with patch.dict(os.environ, {"OVERCODE_STATE_DIR": "/tmp/state dir"}):
            result = backend.env_prefix(LaunchSpec(permissiveness_mode="bypass"))
        assert result["OVERCODE_STATE_DIR"] == "'/tmp/state dir'"
        assert "OPENCODE_PERMISSION" in result


class TestDetectionMode:
    def test_opencode_sessions_resolve_to_hooks(self):
        from overcode.status_detector_factory import resolve_session_detection_mode

        class S:
            backend = "opencode"

        # HOOK_EVENTS is what lets the dispatcher pick the hook detector; the
        # fleet default decides, exactly as for Claude Code.
        assert resolve_session_detection_mode(S(), "hooks") == "hooks"

    def test_per_agent_opt_out_still_wins(self):
        from overcode.status_detector_factory import resolve_session_detection_mode

        class S:
            backend = "opencode"
            hook_status_detection = False

        assert resolve_session_detection_mode(S(), "hooks") == "polling"

    def test_dispatcher_uses_the_hook_detector(self, tmp_path, monkeypatch):
        from overcode.hook_status_detector import HookStatusDetector
        from overcode.status_detector_factory import StatusDetectorDispatcher

        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))

        class S:
            id = "sid"
            name = "oc"
            backend = "opencode"
            tmux_window = "w"
            parent_session_id = None

        dispatcher = StatusDetectorDispatcher("agents", mode="hooks")
        polling, hooks = dispatcher._pair_for("opencode")
        assert isinstance(hooks, HookStatusDetector)
        assert dispatcher.resolve_mode(S()) == "hooks"


class TestPluginHealthVerdict:
    """opencode's stand-in for Claude Code's `--settings` check."""

    def test_plugin_present_is_ok(self, backend, tmp_path):
        from overcode.doctor import VERDICT_OK

        backend.prepare_launch(LaunchSpec(start_directory=str(tmp_path)))
        session = SimpleNamespace(start_directory=str(tmp_path))
        verdict, details = backend.refine_health_verdict(
            session, VERDICT_OK, "opencode process running"
        )
        assert verdict == VERDICT_OK
        assert "telemetry plugin installed" in details

    def test_plugin_absent_is_flagged(self, backend, tmp_path):
        from overcode.doctor import VERDICT_MISSING_SETTINGS, VERDICT_OK

        session = SimpleNamespace(start_directory=str(tmp_path))
        verdict, details = backend.refine_health_verdict(
            session, VERDICT_OK, "opencode process running"
        )
        assert verdict == VERDICT_MISSING_SETTINGS
        assert "pane polling" in details

    def test_a_worse_verdict_is_left_alone(self, backend, tmp_path):
        from overcode.doctor import VERDICT_NO_CLAUDE

        session = SimpleNamespace(start_directory=str(tmp_path))
        assert backend.refine_health_verdict(session, VERDICT_NO_CLAUDE, "gone") == (
            VERDICT_NO_CLAUDE,
            "gone",
        )

    def test_no_directory_leaves_the_verdict_unchanged(self, backend):
        from overcode.doctor import VERDICT_OK

        session = SimpleNamespace(start_directory=None)
        assert backend.refine_health_verdict(session, VERDICT_OK, "running") == (
            VERDICT_OK,
            "running",
        )

    def test_claude_backend_has_no_refinement(self):
        # The hook is optional; doctor uses getattr so Claude Code opts out by
        # simply not defining it.
        assert not hasattr(get_backend("claude-code"), "refine_health_verdict")
