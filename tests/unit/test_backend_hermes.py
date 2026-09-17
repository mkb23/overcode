"""Argv, gestures, plugin install/enable and capability gating for the hermes backend.

Every flag and gesture asserted here was verified live against Hermes Agent
v0.21.3 on 2026-09-17 (``docs/design/agent-backend-hermes.md``,
``tests/fixtures_hermes_panes/README.md``). Notably absent: ``--allowedTools``/
``--agent``/``--session-id`` analogues, which Hermes has none of and are
silently ignored, same posture as opencode/codex.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode import config
from overcode.backends import (
    BackendCapability,
    LaunchSpec,
    get_backend,
    list_backends,
    supports,
)
from overcode.backends.hermes import (
    HermesBackend,
    HermesNotFoundError,
    PLUGIN_MARKER,
    TESTED_HERMES_MAX,
    TESTED_HERMES_MIN,
    bundled_plugin_dir,
    configured_context_length,
    ensure_plugin_enabled,
    ensure_plugin_installed,
    hermes_home,
    parse_version,
    plugin_dir,
    plugin_enabled,
    plugin_installed,
    provider_configured,
    remove_plugin,
    version_findings,
    version_in_tested_range,
)
from overcode.doctor import VERDICT_MISSING_SETTINGS, VERDICT_OK
from overcode.exceptions import ClaudeNotFoundError


OVERCODE_BIN = "/usr/local/bin/overcode"


@pytest.fixture
def backend():
    return get_backend("hermes")


@pytest.fixture(autouse=True)
def pinned_overcode_bin():
    with patch("overcode.backends.hermes._resolve_overcode_bin", return_value=OVERCODE_BIN):
        yield


@pytest.fixture(autouse=True)
def isolated_hermes_home(tmp_path, monkeypatch):
    """Never touch the developer's real ~/.hermes."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    path = tmp_path / "overcode-config.yaml"
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    config._clear_config_cache()
    yield path
    config._clear_config_cache()


def write_hermes_config(home: Path, text: str) -> Path:
    path = home / "config.yaml"
    path.write_text(text)
    return path


class TestRegistry:
    def test_listed(self):
        assert "hermes" in list_backends()

    def test_resolves_to_the_adapter(self, backend):
        assert isinstance(backend, HermesBackend)

    def test_display_name(self, backend):
        assert backend.display_name == "hermes"

    def test_not_found_error_is_catchable_as_the_legacy_one(self):
        assert issubclass(HermesNotFoundError, ClaudeNotFoundError)

    def test_process_basenames_and_argv_markers(self, backend):
        # The pane runs Hermes's venv python, not a `hermes` binary — the
        # argv marker is what doctor/monitor actually match on.
        assert backend.process_basenames == ("hermes",)
        assert backend.process_argv_markers == ("hermes-agent/hermes",)

    def test_install_hint_names_the_curl_installer(self, backend):
        assert "hermes-agent.nousresearch.com/install.sh" in backend.install_hint


class TestCapabilities:
    def test_resume_hooks_and_stats(self, backend):
        assert supports(backend, BackendCapability.RESUME)
        assert supports(backend, BackendCapability.HOOK_EVENTS)
        assert supports(backend, BackendCapability.TRANSCRIPT_STATS)

    @pytest.mark.parametrize("capability", [
        BackendCapability.FORK,
        BackendCapability.SESSION_ID_PRESCRIPTION,
        BackendCapability.PERMISSION_INJECTION,
        BackendCapability.SKILLS,
        BackendCapability.SANDBOX_PROBE,
        BackendCapability.SUBSCRIPTION_USAGE,
        BackendCapability.AGENT_TEAMS,
    ])
    def test_unsupported(self, backend, capability):
        assert not supports(backend, capability)

    def test_fork_does_not_prescribe(self, backend):
        assert backend.fork_prescribes_new_session_id is False

    def test_stats_reader_is_the_real_reader(self, backend):
        from overcode.backends.hermes_stats import HermesStatsReader
        assert isinstance(backend.make_stats_reader(), HermesStatsReader)

    def test_stats_reader_for_session_resolves_to_hermes_reader(self):
        from types import SimpleNamespace
        from overcode.backends.hermes_stats import HermesStatsReader
        from overcode.stats_reader import clear_reader_cache, stats_reader_for_session
        clear_reader_cache()
        try:
            reader = stats_reader_for_session(SimpleNamespace(backend="hermes"))
            assert isinstance(reader, HermesStatsReader)
        finally:
            clear_reader_cache()


class TestBuildCommand:
    def test_bare_launch_forces_the_classic_cli(self, backend):
        # --cli on every launch: the user's display.interface config may
        # default a bare `hermes` to the (unverified) Node TUI.
        assert backend.build_command(LaunchSpec()) == ["hermes", "--cli"]

    def test_command_override(self, backend):
        with patch.dict(os.environ, {"HERMES_COMMAND": "/tmp/mock_hermes.py"}):
            assert backend.build_command(LaunchSpec())[0] == "/tmp/mock_hermes.py"

    def test_model_is_passed_through_verbatim(self, backend):
        assert backend.build_command(LaunchSpec(model="gpt-5-mini")) == [
            "hermes", "--cli", "-m", "gpt-5-mini",
        ]
        assert "-m" in backend.build_command(LaunchSpec(model="anthropic/claude-opus-4.6"))

    def test_agent_persona_has_no_analogue(self, backend):
        assert backend.build_command(LaunchSpec(agent="reviewer")) == ["hermes", "--cli"]

    @pytest.mark.parametrize("spec_kwargs", [
        {"dangerously_skip_permissions": True},
        {"permissiveness_mode": "bypass"},
    ])
    def test_bypass_is_yolo(self, backend, spec_kwargs):
        assert backend.build_command(LaunchSpec(**spec_kwargs)) == ["hermes", "--cli", "--yolo"]

    @pytest.mark.parametrize("spec_kwargs", [
        {"skip_permissions": True},
        {"permissiveness_mode": "permissive"},
        {"permissiveness_mode": "normal"},
        {},
    ])
    def test_permissive_and_normal_add_no_flag(self, backend, spec_kwargs):
        # No per-launch approval mode below --yolo exists; Hermes's own
        # approvals.mode config (smart by default) is the user's knob.
        assert backend.build_command(LaunchSpec(**spec_kwargs)) == ["hermes", "--cli"]

    def test_allowed_tools_has_no_analogue(self, backend):
        assert backend.build_command(LaunchSpec(allowed_tools="Bash,Read")) == ["hermes", "--cli"]

    def test_prescribed_session_id_is_ignored(self, backend):
        assert backend.build_command(LaunchSpec(prescribed_session_id="abc")) == ["hermes", "--cli"]

    def test_resume(self, backend):
        assert backend.build_command(LaunchSpec(resume_session_id="20260917_131721_8f80ea")) == [
            "hermes", "--cli", "--resume", "20260917_131721_8f80ea",
        ]

    def test_resume_args_helper(self, backend):
        assert backend.resume_args("x", fork=False) == ["--resume", "x"]
        # No fork grammar exists; fork falls back to a plain resume.
        assert backend.resume_args("x", fork=True) == ["--resume", "x"]

    def test_extra_args_are_shell_split(self, backend):
        assert backend.build_command(LaunchSpec(extra_args=["--reasoning high", "-t web,terminal"])) == [
            "hermes", "--cli", "--reasoning", "high", "-t", "web,terminal",
        ]

    def test_full_combination_order(self, backend):
        spec = LaunchSpec(
            resume_session_id="20260917_131721_8f80ea",
            model="gpt-5-mini",
            permissiveness_mode="bypass",
            extra_args=["--reasoning low"],
        )
        assert backend.build_command(spec) == [
            "hermes", "--cli", "--resume", "20260917_131721_8f80ea",
            "-m", "gpt-5-mini", "--yolo", "--reasoning", "low",
        ]

    def test_no_accept_hooks_flag(self, backend):
        # The plugin route has no shell-hook consent step.
        assert "--accept-hooks" not in backend.build_command(LaunchSpec())


class TestEnvPrefix:
    def test_hook_command_is_forwarded(self, backend, monkeypatch):
        monkeypatch.delenv("OVERCODE_STATE_DIR", raising=False)
        assert backend.env_prefix(LaunchSpec()) == {"OVERCODE_HOOK_COMMAND": OVERCODE_BIN}

    def test_hook_command_is_shell_quoted(self, backend, monkeypatch):
        monkeypatch.delenv("OVERCODE_STATE_DIR", raising=False)
        with patch("overcode.backends.hermes._resolve_overcode_bin",
                   return_value="/usr/bin/python3 -m overcode.cli"):
            assert backend.env_prefix(LaunchSpec()) == {
                "OVERCODE_HOOK_COMMAND": "'/usr/bin/python3 -m overcode.cli'",
            }

    def test_state_dir_forwarded_when_set(self, backend, monkeypatch):
        monkeypatch.setenv("OVERCODE_STATE_DIR", "/tmp/oc state")
        env = backend.env_prefix(LaunchSpec())
        assert env["OVERCODE_STATE_DIR"] == "'/tmp/oc state'"


class TestGestures:
    def test_graceful_exit_is_single_c_c_then_quit(self, backend):
        presses = backend.graceful_exit_keys()
        assert [p.keys for p in presses] == ["C-c", "/quit"]
        assert presses[0].enter is False and presses[0].delay_after > 0
        assert presses[1].enter is True
        # A second C-c within 2s force-exits Hermes — never two.
        assert sum(p.keys == "C-c" for p in presses) == 1

    def test_clear_conversation_confirms_new(self, backend):
        presses = backend.clear_conversation_keys()
        assert [(p.keys, p.enter) for p in presses] == [("/new", True), ("1", True)]

    def test_approve_is_1_then_enter(self, backend):
        assert [(p.keys, p.enter) for p in backend.approve_keys()] == [("1", True)]

    def test_reject_is_4_then_enter(self, backend):
        assert [(p.keys, p.enter) for p in backend.reject_keys()] == [("4", True)]

    def test_no_startup_dialogs(self, backend):
        assert backend.startup_dialog_rules() == []

    def test_prompt_ready_line_accepts_placeholder_and_typed_text(self, backend):
        assert backend.prompt_ready_line("❯")
        assert backend.prompt_ready_line("❯ Draft a reply to the last email in my inbox")
        assert backend.prompt_ready_line("❯ hello there")

    @pytest.mark.parametrize("line", [
        "☤ ❯ msg=interrupt · /queue · /bg · /steer · Ctrl+C cancel",
        "⚠ ❯",
        "⚠ ❯ type 1/2/3, or use ↑/↓ then Enter",
        "│ ❯ 1. Allow once",
        "❯x",
        "",
    ])
    def test_prompt_ready_line_rejects_busy_dialog_and_menu_rows(self, backend, line):
        assert not backend.prompt_ready_line(line)

    def test_prompt_ready_chars_is_the_bare_glyph(self, backend):
        assert backend.prompt_ready_chars() == {"❯"}


class TestPluginInstall:
    def test_bundled_plugin_carries_the_marker(self):
        for name in ("plugin.yaml", "__init__.py"):
            assert PLUGIN_MARKER in (bundled_plugin_dir() / name).read_text()

    def test_installs_into_hermes_home(self, isolated_hermes_home):
        target = ensure_plugin_installed()
        assert target == plugin_dir()
        assert plugin_dir() == isolated_hermes_home / "plugins" / "overcode"
        assert plugin_installed()
        assert (target / "plugin.yaml").read_text() == (bundled_plugin_dir() / "plugin.yaml").read_text()
        assert (target / "__init__.py").read_text() == (bundled_plugin_dir() / "__init__.py").read_text()

    def test_reinstall_is_idempotent(self):
        ensure_plugin_installed()
        first = (plugin_dir() / "__init__.py").stat().st_mtime_ns
        ensure_plugin_installed()
        assert (plugin_dir() / "__init__.py").stat().st_mtime_ns == first

    def test_stale_copy_is_refreshed(self):
        ensure_plugin_installed()
        stale = plugin_dir() / "__init__.py"
        stale.write_text(f"# {PLUGIN_MARKER}\n# old version\n")
        ensure_plugin_installed()
        assert stale.read_text() == (bundled_plugin_dir() / "__init__.py").read_text()

    def test_user_plugin_of_same_name_is_left_alone(self):
        plugin_dir().mkdir(parents=True)
        (plugin_dir() / "plugin.yaml").write_text("name: overcode\ndescription: mine\n")
        assert ensure_plugin_installed() is None
        assert (plugin_dir() / "plugin.yaml").read_text() == "name: overcode\ndescription: mine\n"
        assert not plugin_installed()

    def test_hermes_home_honours_env(self, isolated_hermes_home):
        assert hermes_home() == isolated_hermes_home

    def test_hermes_home_defaults_to_dot_hermes(self, monkeypatch):
        monkeypatch.delenv("HERMES_HOME")
        assert hermes_home() == Path.home() / ".hermes"


class TestPluginEnabled:
    def test_missing_config_is_unknown(self):
        assert plugin_enabled() is None

    def test_absent_allowlist_is_disabled(self, isolated_hermes_home):
        write_hermes_config(isolated_hermes_home, "model:\n  default: gpt-5-mini\n")
        assert plugin_enabled() is False

    def test_listed_is_enabled(self, isolated_hermes_home):
        write_hermes_config(isolated_hermes_home, "plugins:\n  enabled:\n    - overcode\n")
        assert plugin_enabled() is True

    def test_disabled_list_wins(self, isolated_hermes_home):
        write_hermes_config(
            isolated_hermes_home,
            "plugins:\n  enabled:\n    - overcode\n  disabled:\n    - overcode\n",
        )
        assert plugin_enabled() is False

    def test_ensure_enabled_shells_out_only_when_needed(self, isolated_hermes_home):
        write_hermes_config(isolated_hermes_home, "plugins:\n  enabled:\n    - overcode\n")
        with patch("overcode.backends.hermes.subprocess.run") as run:
            assert ensure_plugin_enabled("hermes") is True
        run.assert_not_called()

    def test_ensure_enabled_uses_the_non_interactive_spelling(self, isolated_hermes_home):
        write_hermes_config(isolated_hermes_home, "plugins:\n  enabled: []\n")
        with patch("overcode.backends.hermes.subprocess.run") as run:
            run.return_value.returncode = 0
            assert ensure_plugin_enabled("/tmp/mock hermes") is True
        argv = run.call_args.args[0]
        assert argv == ["/tmp/mock", "hermes", "plugins", "enable", "overcode", "--no-allow-tool-override"]
        assert run.call_args.kwargs["stdin"] is not None

    def test_ensure_enabled_reports_failure(self, isolated_hermes_home):
        write_hermes_config(isolated_hermes_home, "plugins:\n  enabled: []\n")
        with patch("overcode.backends.hermes.subprocess.run", side_effect=OSError("no hermes")):
            assert ensure_plugin_enabled("hermes") is False


class TestPrepareLaunch:
    def test_installs_and_enables(self, backend, isolated_config, isolated_hermes_home):
        write_hermes_config(isolated_hermes_home, "plugins:\n  enabled: []\n")
        with patch("overcode.backends.hermes.subprocess.run") as run:
            run.return_value.returncode = 0
            backend.prepare_launch(LaunchSpec())
        assert plugin_installed()
        assert run.call_args.args[0][1:] == ["plugins", "enable", "overcode", "--no-allow-tool-override"]

    def test_uses_the_command_override_for_enable(self, backend, isolated_config, isolated_hermes_home):
        write_hermes_config(isolated_hermes_home, "plugins:\n  enabled: []\n")
        with patch.dict(os.environ, {"HERMES_COMMAND": "/tmp/mock_hermes.py"}), \
             patch("overcode.backends.hermes.subprocess.run") as run:
            run.return_value.returncode = 0
            backend.prepare_launch(LaunchSpec())
        assert run.call_args.args[0][0] == "/tmp/mock_hermes.py"

    def test_skipped_when_telemetry_disabled(self, backend, isolated_config):
        isolated_config.write_text("backend_telemetry:\n  hermes: off\n")
        config._clear_config_cache()
        with patch("overcode.backends.hermes.subprocess.run") as run:
            backend.prepare_launch(LaunchSpec())
        assert not plugin_installed()
        run.assert_not_called()

    def test_never_raises(self, backend, isolated_config):
        with patch("overcode.backends.hermes.ensure_plugin_installed", side_effect=OSError("disk")):
            with pytest.raises(OSError):
                # The launcher wraps prepare_launch in try/except; the
                # adapter itself only guarantees the helpers below are
                # failure-tolerant (covered above).
                backend.prepare_launch(LaunchSpec())


class TestHealthVerdict:
    def test_argv_alone_is_ok(self, backend):
        verdict, _ = backend.health_verdict("/x/venv/bin/python /x/hermes-agent/hermes --cli")
        assert verdict == VERDICT_OK

    def test_refine_flags_missing_plugin(self, backend, isolated_hermes_home):
        verdict, details = backend.refine_health_verdict(None, VERDICT_OK, "ok")
        assert verdict == VERDICT_MISSING_SETTINGS
        assert "telemetry plugin" in details

    def test_refine_flags_installed_but_not_enabled(self, backend, isolated_hermes_home):
        ensure_plugin_installed()
        write_hermes_config(isolated_hermes_home, "plugins:\n  enabled: []\n")
        verdict, details = backend.refine_health_verdict(None, VERDICT_OK, "ok")
        assert verdict == VERDICT_MISSING_SETTINGS
        assert "plugins.enabled" in details

    def test_refine_ok_when_installed_and_enabled(self, backend, isolated_hermes_home):
        ensure_plugin_installed()
        write_hermes_config(isolated_hermes_home, "plugins:\n  enabled:\n    - overcode\n")
        verdict, details = backend.refine_health_verdict(None, VERDICT_OK, "ok")
        assert verdict == VERDICT_OK
        assert "enabled" in details

    def test_refine_leaves_non_ok_verdicts_alone(self, backend):
        assert backend.refine_health_verdict(None, VERDICT_MISSING_SETTINGS, "x") == (
            VERDICT_MISSING_SETTINGS, "x",
        )


class TestUninstall:
    def test_uninstall_telemetry_removes_ours(self, backend):
        ensure_plugin_installed()
        ok, message = backend.uninstall_telemetry()
        assert ok and message.startswith("Removed")
        assert not plugin_dir().exists()

    def test_remove_plugin_leaves_foreign_dir(self):
        plugin_dir().mkdir(parents=True)
        (plugin_dir() / "plugin.yaml").write_text("name: overcode\n")
        ok, message = remove_plugin()
        assert not ok and "not overcode-managed" in message


class TestVersion:
    def test_parse_first_line_of_banner(self):
        assert parse_version("Hermes Agent v0.21.3 (2026.9.14) · upstream 98f758ae") == (0, 21, 3)

    def test_parse_none(self):
        assert parse_version("") is None

    @pytest.mark.parametrize("version,expected", [
        (TESTED_HERMES_MIN, True),
        ("0.21.9", True),
        (TESTED_HERMES_MAX, False),
        ("0.20.0", False),
        ("garbage", None),
    ])
    def test_in_range(self, version, expected):
        assert version_in_tested_range(version) is expected

    def test_findings_when_version_unknown(self):
        with patch("overcode.backends.hermes.installed_version", return_value=None):
            findings = version_findings()
        assert any("could not determine" in f for f in findings)

    def test_findings_out_of_range(self):
        findings = version_findings("Hermes Agent v0.22.0")
        assert any("outside the tested range" in f for f in findings)

    def test_findings_clean_for_a_configured_install(self, isolated_hermes_home):
        ensure_plugin_installed()
        write_hermes_config(
            isolated_hermes_home,
            "model:\n  default: gpt-5-mini\n  provider: openai-api\nplugins:\n  enabled:\n    - overcode\n",
        )
        assert version_findings("Hermes Agent v0.21.3") == []

    def test_findings_flag_plugin_not_enabled(self, isolated_hermes_home):
        ensure_plugin_installed()
        write_hermes_config(
            isolated_hermes_home,
            "model:\n  default: gpt-5-mini\n  provider: openai-api\nplugins:\n  enabled: []\n",
        )
        findings = version_findings("Hermes Agent v0.21.3")
        assert any("plugins.enabled" in f for f in findings)

    def test_findings_flag_missing_provider(self, isolated_hermes_home):
        write_hermes_config(isolated_hermes_home, "model:\n  default: ''\n")
        findings = version_findings("Hermes Agent v0.21.3")
        assert any("no model provider configured" in f for f in findings)

    def test_doctor_findings_hook_delegates(self, backend):
        with patch("overcode.backends.hermes.version_findings", return_value=["x"]):
            assert backend.doctor_findings() == ["x"]


class TestConfigHelpers:
    def test_provider_configured(self, isolated_hermes_home):
        assert provider_configured() is None
        write_hermes_config(isolated_hermes_home, "model:\n  default: gpt-5-mini\n  provider: openai-api\n")
        assert provider_configured() is True

    def test_context_length_from_config(self, isolated_hermes_home):
        assert configured_context_length() is None
        write_hermes_config(isolated_hermes_home, "model:\n  context_length: 256000\n")
        assert configured_context_length() == 256000
        write_hermes_config(isolated_hermes_home, "model:\n  context_length: '256K'\n")
        assert configured_context_length() is None
